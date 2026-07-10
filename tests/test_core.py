"""Core tests. Run with:  python -m pytest  (or)  python tests/test_core.py"""

import os
import sys

# Prefer an installed openfacefx (so CI can test the built wheel); fall back
# to the repo source for contributors running pytest without installing.
try:
    import openfacefx  # noqa: F401
except ImportError:
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import numpy as np

from openfacefx import (
    G2P, NaiveAligner, PhonemeSegment, phoneme_to_viseme,
    build_viseme_curves, generate_naive, to_dict,
)
from openfacefx.visemes import VISEMES

# silence-tolerance: several coart tests build segment lists by hand


def test_phoneme_to_viseme_groups_bilabials():
    assert phoneme_to_viseme("P") == "PP"
    assert phoneme_to_viseme("B") == "PP"
    assert phoneme_to_viseme("M") == "PP"


def test_stress_is_stripped():
    assert phoneme_to_viseme("AA1") == phoneme_to_viseme("AA0") == "aa"


def test_g2p_known_and_oov():
    g = G2P()
    assert g.word("hello") == ["HH", "AH0", "L", "OW1"]
    # OOV word still returns *some* phonemes, never empty
    assert len(g.word("zqxblorp")) > 0


def test_naive_aligner_covers_full_span_in_order():
    segs = NaiveAligner().align(["HH", "AH0", "L", "OW1"], total_duration=1.0)
    assert abs(segs[0].start - 0.0) < 1e-9
    assert abs(segs[-1].end - 1.0) < 1e-6
    # monotonic, non-overlapping
    for a, b in zip(segs, segs[1:]):
        assert abs(a.end - b.start) < 1e-9


def test_curves_are_bounded_and_partition_energy():
    segs = NaiveAligner().align(["P", "AA1", "T"], total_duration=0.6)
    times, m = build_viseme_curves(segs, fps=60)
    assert m.min() >= 0.0 and m.max() <= 1.0
    # dominance-weighted average => each frame's channels sum to ~1, up to
    # the 1e-4 dust-zeroing threshold applied per channel after normalizing
    row_sums = m.sum(axis=1)
    assert np.allclose(row_sums, 1.0, atol=2e-3)


def test_pipeline_produces_valid_track():
    track = generate_naive("the quick brown fox", duration=2.0, fps=60)
    d = to_dict(track)
    assert d["format"] == "openfacefx.track"
    assert d["channels"], "expected at least one active channel"
    # PP (from 'brown' b) and aa vowels should appear
    names = {c["name"] for c in d["channels"]}
    assert "PP" in names or "aa" in names


def test_naive_segments_layer():
    from openfacefx.pipeline import naive_segments
    segs = naive_segments("hello world", duration=1.5)
    assert segs[0].phoneme == "sil" and segs[-1].phoneme == "sil"
    assert abs(segs[0].start - 0.0) < 1e-9
    assert abs(segs[-1].end - 1.5) < 1e-6
    # identical timing feeds generate_naive, so both paths must agree
    from openfacefx import generate_from_alignment
    assert to_dict(generate_from_alignment(segs)) == to_dict(
        generate_naive("hello world", duration=1.5))


def test_unity_anim_export():
    import tempfile
    from openfacefx.curves import Channel, FaceTrack, Keyframe
    from openfacefx.export_unity import write_unity_anim
    track = FaceTrack(fps=60, channels=[
        Channel("aa", [Keyframe(0.0, 0.5), Keyframe(1.25, 0.0)]),
    ])
    out = tempfile.NamedTemporaryFile(suffix=".anim", delete=False).name
    write_unity_anim(track, out)
    text = open(out, encoding="utf-8").read()
    assert text.startswith("%YAML 1.1\n%TAG !u! tag:unity3d.com,2011:\n--- !u!74 &7400000\n")
    # 15 curves in m_FloatCurves + duplicated in m_EditorCurves
    assert text.count("attribute: blendShape.viseme_") == 30
    assert "attribute: blendShape.viseme_aa" in text
    assert "value: 50" in text                      # 0.5 -> percent
    assert "m_StopTime: 1.25" in text
    assert "classID: 137" in text and "path: Body" in text
    os.unlink(out)


def test_unity_anim_vrchat_naming():
    import tempfile
    from openfacefx.export_unity import write_unity_anim
    track = generate_naive("hello world", duration=1.0)
    out = tempfile.NamedTemporaryFile(suffix=".anim", delete=False).name
    write_unity_anim(track, out, naming="vrchat", mesh_path="Armature/Head")
    text = open(out, encoding="utf-8").read()
    # lowercase vrc names with the enum's ih/oh/ou spellings
    for name in ("vrc.v_sil", "vrc.v_pp", "vrc.v_kk", "vrc.v_ih",
                 "vrc.v_oh", "vrc.v_ou"):
        assert "attribute: blendShape." + name in text, name
    assert "viseme_" not in text
    assert "path: Armature/Head" in text
    # all emitted percent values stay in [0, 100]
    vals = [float(l.split(":")[1]) for l in text.splitlines()
            if l.strip().startswith("value:")]
    assert vals and all(0.0 <= v <= 100.0 for v in vals)
    os.unlink(out)


def _channel(track_or_matrix_names, name):
    times, matrix, names = track_or_matrix_names
    return times, matrix[:, names.index(name)]


def test_coart_bilabial_closure_between_vowels():
    from openfacefx.coarticulation import build_viseme_curves
    segs = [PhonemeSegment("AA", 0.0, 0.25), PhonemeSegment("P", 0.25, 0.33),
            PhonemeSegment("AA", 0.33, 0.6)]
    times, m = build_viseme_curves(segs, fps=120)
    pp = m[:, VISEMES.index("PP")]
    mid = int(np.argmin(np.abs(times - 0.29)))
    assert pp[mid] >= 0.89, pp[mid]
    # rows still partition energy
    assert np.allclose(m.sum(axis=1), 1.0, atol=2e-3)


def test_coart_short_silence_absorbed_long_kept():
    from openfacefx.coarticulation import build_viseme_curves
    sil_i = VISEMES.index("sil")

    def sil_at_gap(gap):
        segs = [PhonemeSegment("sil", 0.0, 0.1),
                PhonemeSegment("AA", 0.1, 0.5),
                PhonemeSegment("sil", 0.5, 0.5 + gap),
                PhonemeSegment("AA", 0.5 + gap, 0.9 + gap),
                PhonemeSegment("sil", 0.9 + gap, 1.0 + gap)]
        times, m = build_viseme_curves(segs, fps=60)
        mid = int(np.argmin(np.abs(times - (0.5 + gap / 2))))
        return m[mid, sil_i]

    assert sil_at_gap(0.1) < 0.15          # short pause: mouth stays open
    assert sil_at_gap(0.6) > 0.5           # long pause: mouth relaxes


def test_coart_tongue_lead_is_tunable_and_local():
    from openfacefx.coarticulation import CoartParams, build_viseme_curves
    segs = [PhonemeSegment("AA", 0.0, 0.3), PhonemeSegment("D", 0.3, 0.38),
            PhonemeSegment("AA", 0.38, 0.7)]
    loose = CoartParams()
    loose.lead = dict(loose.lead); loose.lead["tongue"] = (0.40, 0.45)
    _, m_tight = build_viseme_curves(segs, fps=60)
    _, m_loose = build_viseme_curves(segs, fps=60, params=loose)
    dd = VISEMES.index("DD"); aa = VISEMES.index("aa")
    # tongue channel responds strongly to its own lead parameter...
    dd_delta = np.abs(m_tight[:, dd] - m_loose[:, dd]).max()
    assert dd_delta > 0.05, dd_delta
    # ...while the jaw channel's peak barely moves (normalized model, so
    # strict independence is impossible; near-independence is the contract)
    aa_peak_delta = abs(m_tight[:, aa].max() - m_loose[:, aa].max())
    assert aa_peak_delta < 0.05, aa_peak_delta


def test_coart_diphthong_splits_into_two_peaks():
    from openfacefx.coarticulation import build_viseme_curves
    segs = [PhonemeSegment("sil", 0.0, 0.1), PhonemeSegment("AY1", 0.1, 0.6),
            PhonemeSegment("sil", 0.6, 0.7)]
    times, m = build_viseme_curves(segs, fps=120)
    aa = m[:, VISEMES.index("aa")]; ii = m[:, VISEMES.index("I")]
    assert aa.max() > 0.3 and ii.max() > 0.3
    # open component peaks before the spread component
    assert times[int(np.argmax(aa))] < times[int(np.argmax(ii))]


def test_coart_preroll_onset_policy():
    from openfacefx.coarticulation import CoartParams, build_viseme_curves
    segs = [PhonemeSegment("AA", 0.0, 0.5)]
    t_default, _ = build_viseme_curves(segs, fps=60)
    assert t_default[0] == 0.0
    p = CoartParams(); p.preroll = 0.2
    t_clamped, _ = build_viseme_curves(segs, fps=60, params=p)
    assert t_clamped[0] == 0.0              # clamped at zero by default
    p2 = CoartParams(); p2.preroll = 0.2; p2.allow_negative_time = True
    t_neg, _ = build_viseme_curves(segs, fps=60, params=p2)
    assert t_neg[0] < -0.19                  # true anticipation keys


def test_intensity_defaults_are_byte_identical():
    # gains all 1.0 + intensity 1.0 must be a behavioural no-op: an explicit
    # default CoartParams produces the same track as passing none at all. This
    # pins the byte-identity guarantee the dials are built to preserve.
    from openfacefx.coarticulation import CoartParams
    from openfacefx import generate_from_alignment
    from openfacefx.pipeline import naive_segments
    segs = naive_segments("the quick brown fox jumps", duration=2.5)
    assert to_dict(generate_from_alignment(segs)) == to_dict(
        generate_from_alignment(segs, params=CoartParams()))


def test_gain_zero_mutes_its_class_only():
    # --gain tongue=0 zeroes the tongue-class channels while the jaw channel is
    # left bit-for-bit intact — dialling one class down never touches another.
    from openfacefx.coarticulation import CoartParams, build_viseme_curves
    segs = [PhonemeSegment("sil", 0.0, 0.1), PhonemeSegment("T", 0.1, 0.25),
            PhonemeSegment("AA", 0.25, 0.6), PhonemeSegment("sil", 0.6, 0.7)]
    dd, aa = VISEMES.index("DD"), VISEMES.index("aa")
    _, base = build_viseme_curves(segs, fps=120)
    p = CoartParams(); p.gains = dict(p.gains, tongue=0.0)
    _, muted = build_viseme_curves(segs, fps=120, params=p)
    assert base[:, dd].max() > 0.2           # tongue (DD) fires by default...
    assert muted[:, dd].max() < 1e-9         # ...and is silenced at gain 0
    assert np.array_equal(muted[:, aa], base[:, aa])   # jaw (aa) untouched


def test_intensity_scales_openness_with_sil_absorbing_slack():
    # intensity 0.5 halves every frame's openness (all non-sil weight); sil
    # takes up the freed weight so each frame still partitions unit energy.
    from openfacefx.coarticulation import CoartParams, build_viseme_curves
    segs = [PhonemeSegment("sil", 0.0, 0.1), PhonemeSegment("AA", 0.1, 0.5),
            PhonemeSegment("sil", 0.5, 0.6)]
    sil = VISEMES.index("sil")
    _, base = build_viseme_curves(segs, fps=120)
    p = CoartParams(); p.intensity = 0.5
    _, half = build_viseme_curves(segs, fps=120, params=p)
    open_base = base.sum(axis=1) - base[:, sil]
    open_half = half.sum(axis=1) - half[:, sil]
    assert np.allclose(open_half, 0.5 * open_base, atol=1e-9)   # openness halved
    assert np.allclose(half.sum(axis=1), 1.0, atol=2e-3)        # invariant holds
    assert np.all(half[:, sil] >= base[:, sil] - 1e-9)          # mouth closes more


def test_closure_enforced_under_low_intensity():
    # A whispered bilabial still fully seals: closure enforcement runs after the
    # dials and wins, so PP reaches the 0.9 floor even at intensity 0.5.
    from openfacefx.coarticulation import CoartParams, build_viseme_curves
    segs = [PhonemeSegment("AA", 0.0, 0.25), PhonemeSegment("P", 0.25, 0.33),
            PhonemeSegment("AA", 0.33, 0.6)]
    p = CoartParams(); p.intensity = 0.5
    times, m = build_viseme_curves(segs, fps=120, params=p)
    mid = int(np.argmin(np.abs(times - 0.29)))
    assert m[mid, VISEMES.index("PP")] >= 0.89
    assert np.allclose(m.sum(axis=1), 1.0, atol=2e-3)


def test_cli_intensity_and_gain_parse_errors(tmp_path):
    import pytest
    from openfacefx.cli import main as cli_main
    out = str(tmp_path / "o.json")
    base = ["naive", "--text", "hi", "--duration", "0.5", "-o", out]
    for bad in (["--gain", "nose=0.5"],     # unknown articulator class
                ["--gain", "jaw=abc"],      # value is not a number
                ["--gain", "jaw=-1"],       # negative gain
                ["--gain", "jawopen"],      # missing '='
                ["--intensity", "-2"]):     # negative master intensity
        with pytest.raises(SystemExit):
            cli_main(base + bad)
    # a valid dial run still succeeds and writes a track
    assert cli_main(base + ["--gain", "tongue=0.6", "--intensity", "0.8"]) == 0
    assert os.path.exists(out)


def test_mapping_default_matches_builtin():
    from openfacefx.mapping import Mapping
    m = Mapping.default()
    track_m = generate_naive("the quick brown fox", duration=2.0)
    from openfacefx import generate_from_alignment
    from openfacefx.pipeline import naive_segments
    segs = naive_segments("the quick brown fox", duration=2.0)
    track_d = generate_from_alignment(segs, mapping=m)
    assert to_dict(track_m) == to_dict(track_d)


def test_mapping_weighted_many_to_many():
    from openfacefx.mapping import Mapping, Target
    from openfacefx import generate_from_alignment
    from openfacefx.alignment import PhonemeSegment
    m = Mapping(
        [Target("jaw"), Target("lips")],
        {"AA": {"jaw": 0.7, "lips": 0.3}, "sil": {}},
    )
    segs = [PhonemeSegment("sil", 0.0, 0.2), PhonemeSegment("AA1", 0.2, 0.8),
            PhonemeSegment("sil", 0.8, 1.0)]
    track = generate_from_alignment(segs, mapping=m)
    by = {c.name: max(k.value for k in c.keys) for c in track.channels}
    assert set(by) == {"jaw", "lips"}
    # both peaks driven by the same phoneme, scaled 0.7 : 0.3
    assert abs(by["jaw"] / by["lips"] - 0.7 / 0.3) < 0.05
    # stress digit was stripped to find the AA row
    assert by["jaw"] > 0.5


def test_mapping_clamps_applied():
    from openfacefx.mapping import Mapping, Target
    from openfacefx import generate_from_alignment
    from openfacefx.alignment import PhonemeSegment
    m = Mapping([Target("jaw", hi=0.4)], {"AA": {"jaw": 1.0}, "sil": {}})
    segs = [PhonemeSegment("AA", 0.0, 1.0)]
    track = generate_from_alignment(segs, mapping=m)
    assert all(k.value <= 0.4 + 1e-9 for c in track.channels for k in c.keys)


def test_mapping_validation_errors():
    import pytest
    from openfacefx.mapping import Mapping, Target
    with pytest.raises(ValueError, match="unknown phoneme"):
        Mapping([Target("x")], {"QQ": {"x": 1.0}})
    with pytest.raises(ValueError, match="undeclared target"):
        Mapping([Target("x")], {"AA": {"y": 1.0}})
    with pytest.raises(ValueError, match="weight"):
        Mapping([Target("x")], {"AA": {"x": -1.0}})
    with pytest.raises(ValueError, match="articulator"):
        Mapping([Target("x", articulator="nose")], {"AA": {"x": 1.0}})
    with pytest.raises(ValueError, match="clamp"):
        Mapping([Target("x", lo=0.9, hi=0.1)], {"AA": {"x": 1.0}})


def test_mapping_files_load():
    from openfacefx.mapping import Mapping
    root = os.path.join(os.path.dirname(__file__), "..", "examples", "mappings")
    m = Mapping.from_json(os.path.join(root, "oculus15.json"))
    assert m.target_names == list(VISEMES)
    m2 = Mapping.from_json(os.path.join(root, "minimal9.json"))
    assert "MBP" in m2.target_names and len(m2.rows) >= 39


def test_fuz_container_roundtrip():
    import tempfile
    from openfacefx.bethesda import read_fuz, write_fuz
    lip, audio = b"\x01\x00\x00\x00LIPPAYLOAD", b"RIFFxwma-audio-bytes"
    out = tempfile.NamedTemporaryFile(suffix=".fuz", delete=False).name
    write_fuz(out, audio, lip=lip)
    rl, ra = read_fuz(out)
    assert (rl, ra) == (lip, audio)
    # lip-less container: audio starts right after the 12-byte header
    write_fuz(out, audio)
    rl, ra = read_fuz(out)
    assert rl == b"" and ra == audio
    raw = open(out, "rb").read()
    assert raw[:4] == b"FUZE" and len(raw) == 12 + len(audio)
    os.unlink(out)


def test_lip_header_parse_and_info():
    import struct
    import zlib
    from openfacefx.bethesda import (parse_lip_header, lip_info,
                                     LIP_FLAG_COMPRESSED, SKYRIM_TARGETS)
    payload = zlib.compress(b"fake-facefx-anim-payload")
    blob = struct.pack("<iii", 4, 24, LIP_FLAG_COMPRESSED) + payload
    hdr = parse_lip_header(blob)
    assert (hdr.version, hdr.size) == (4, 24)
    assert hdr.compressed and not hdr.big_endian and not hdr.has_gestures
    info = lip_info(blob)
    assert info["zlib_inflates"] and info["inflated_bytes"] == 24
    assert len(SKYRIM_TARGETS) == 16


def _write_wav(path, seconds=0.4, rate=16000):
    import struct
    import wave
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(struct.pack("<h", 0) * int(seconds * rate))


def test_batch_tree_incremental_and_failure(tmp_path):
    import json
    import time
    from openfacefx.cli import main as cli_main

    src, out = tmp_path / "src", tmp_path / "out"
    (src / "quests" / "mq01").mkdir(parents=True)
    lines = {
        "hello.wav": "hello world",
        "quests/greet.wav": "this is a test",
        "quests/mq01/zorblat.wav": "the zorblat awakens",   # OOV word
    }
    for rel, text in lines.items():
        p = src / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        _write_wav(str(p))
        p.with_suffix(".txt").write_text(text)
    (src / "broken.wav",)  # no transcript for this one
    _write_wav(str(src / "broken.wav"))

    rc = cli_main(["batch", "--dir", str(src), "--out", str(out), "--recurse"])
    assert rc == 1  # broken.wav has no transcript
    summary = json.loads((out / "batch_summary.json").read_text())
    assert summary["processed"] == 4 and summary["failed"] == 1
    # mirrored tree
    assert (out / "hello.json").exists()
    assert (out / "quests" / "greet.json").exists()
    assert (out / "quests" / "mq01" / "zorblat.json").exists()
    # failures sort first; OOV word surfaced
    assert summary["rows"][0]["file"].startswith("broken")
    zrow = [r for r in summary["rows"] if "zorblat" in r["file"]][0]
    assert "zorblat" in zrow["oov"]

    # incremental: nothing to redo except the broken file
    rc = cli_main(["batch", "--dir", str(src), "--out", str(out),
                   "--recurse", "--modified-only"])
    summary = json.loads((out / "batch_summary.json").read_text())
    assert summary["skipped_unchanged"] == 3 and summary["processed"] == 1

    # touching one source reprocesses exactly that file (plus broken)
    time.sleep(0.02)
    (src / "hello.txt").write_text("hello again world")
    os.utime(src / "hello.txt")
    rc = cli_main(["batch", "--dir", str(src), "--out", str(out),
                   "--recurse", "--modified-only"])
    summary = json.loads((out / "batch_summary.json").read_text())
    processed_files = {r["file"] for r in summary["rows"]}
    assert any("hello" in f for f in processed_files)
    assert not any("greet" in f for f in processed_files)


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print("PASS", fn.__name__)
    print(f"\n{len(fns)} tests passed")
