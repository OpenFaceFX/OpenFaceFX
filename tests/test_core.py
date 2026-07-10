"""Core tests. Run with:  python -m pytest  (or)  python tests/test_core.py"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import numpy as np

from openfacefx import (
    G2P, NaiveAligner, PhonemeSegment, phoneme_to_viseme,
    build_viseme_curves, generate_naive, to_dict,
)
from openfacefx.visemes import VISEMES


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
    # dominance-weighted average => each frame's channels sum to ~1
    row_sums = m.sum(axis=1)
    assert np.allclose(row_sums, 1.0, atol=1e-6)


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


def test_retarget_combines_scales_and_clamps():
    from openfacefx.curves import Channel, FaceTrack, Keyframe
    from openfacefx.retarget import retarget
    track = FaceTrack(fps=60, channels=[
        Channel("aa", [Keyframe(0.0, 0.8), Keyframe(1.0, 0.0)]),
        Channel("O",  [Keyframe(0.5, 1.0)]),
    ])
    out = retarget(track, {"aa": [("jawOpen", 1.0)], "O": [("jawOpen", 0.5)]})
    (jaw,) = out.channels
    assert jaw.name == "jawOpen"
    # union of key times; a single-key channel holds its value everywhere
    assert [k.time for k in jaw.keys] == [0.0, 0.5, 1.0]
    assert jaw.keys[0].value == 1.0          # 0.8 + 1.0*0.5 = 1.3, clamped
    assert jaw.keys[1].value == 0.9          # aa lerped to 0.4, + 0.5
    assert jaw.keys[2].value == 0.5          # aa 0.0, + 0.5


def test_retarget_presets_integrity():
    from openfacefx.retarget import PRESETS, retarget
    # ARKit names the preset may use (mouth/jaw/tongue subset of Apple's 52)
    arkit_ok = {
        "jawForward", "jawLeft", "jawRight", "jawOpen", "mouthClose",
        "mouthFunnel", "mouthPucker", "mouthLeft", "mouthRight",
        "mouthSmileLeft", "mouthSmileRight", "mouthFrownLeft",
        "mouthFrownRight", "mouthDimpleLeft", "mouthDimpleRight",
        "mouthStretchLeft", "mouthStretchRight", "mouthRollLower",
        "mouthRollUpper", "mouthShrugLower", "mouthShrugUpper",
        "mouthPressLeft", "mouthPressRight", "mouthLowerDownLeft",
        "mouthLowerDownRight", "mouthUpperUpLeft", "mouthUpperUpRight",
        "tongueOut",
    }
    for name, mapping in PRESETS.items():
        assert mapping, name
        for viseme, targets in mapping.items():
            assert viseme in VISEMES, (name, viseme)
            for target, scale in targets:
                assert 0.0 < scale <= 1.0, (name, viseme, target, scale)
                if name == "arkit":
                    assert target in arkit_ok, (viseme, target)
        # every vowel must land somewhere in every preset
        for vowel in ("aa", "E", "I", "O", "U"):
            assert vowel in mapping, (name, vowel)

    track = generate_naive("hello world", duration=1.2)
    out = retarget(track, PRESETS["arkit"])
    assert any(c.name == "jawOpen" for c in out.channels)
    assert all(0.0 <= k.value <= 1.0 for c in out.channels for k in c.keys)


def test_retarget_rename_only():
    from openfacefx.retarget import rename_only, retarget
    track = generate_naive("hello", duration=0.8)
    out = retarget(track, rename_only(prefix="viseme_"))
    assert {c.name for c in out.channels} == {"viseme_" + c.name for c in track.channels}
    src = {c.name: [(k.time, k.value) for k in c.keys] for c in track.channels}
    dst = {c.name: [(k.time, k.value) for k in c.keys] for c in out.channels}
    for name, keys in src.items():
        assert dst["viseme_" + name] == keys


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


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print("PASS", fn.__name__)
    print(f"\n{len(fns)} tests passed")
