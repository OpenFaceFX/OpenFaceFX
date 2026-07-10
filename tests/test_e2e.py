"""End-to-end tests: every shipped feature exercised through the CLI or the
public API, on real files, checking output invariants — not just unit math."""

import json
import os
import struct
import sys
import wave

import pytest

try:
    import openfacefx  # noqa: F401  (installed wheel wins; see test_core)
except ImportError:
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from openfacefx.cli import main as cli_main
from openfacefx.visemes import VISEMES

ROOT = os.path.join(os.path.dirname(__file__), "..")
VOICE = os.path.join(ROOT, "examples", "voice.wav")

TEXTGRID = '''File type = "ooTextFile"
Object class = "TextGrid"

xmin = 0
xmax = 1.6
tiers? <exists>
size = 1
item []:
    item [1]:
        class = "IntervalTier"
        name = "phones"
        xmin = 0
        xmax = 1.6
        intervals: size = 7
        intervals [1]:
            xmin = 0.0
            xmax = 0.15
            text = ""
        intervals [2]:
            xmin = 0.15
            xmax = 0.32
            text = "HH"
        intervals [3]:
            xmin = 0.32
            xmax = 0.55
            text = "AH0"
        intervals [4]:
            xmin = 0.55
            xmax = 0.78
            text = "L"
        intervals [5]:
            xmin = 0.78
            xmax = 1.1
            text = "OW1"
        intervals [6]:
            xmin = 1.1
            xmax = 1.35
            text = "P"
        intervals [7]:
            xmin = 1.35
            xmax = 1.6
            text = ""
'''


def _assert_track_invariants(path, viseme_names=True):
    with open(path, encoding="utf-8") as fh:
        d = json.load(fh)
    assert d["format"] == "openfacefx.track" and d["version"] == 1
    assert d["duration"] > 0 and d["channels"]
    for c in d["channels"]:
        times = [k[0] for k in c["keys"]]
        vals = [k[1] for k in c["keys"]]
        assert times == sorted(times), c["name"]
        assert all(0.0 <= v <= 1.0 for v in vals), c["name"]
        assert all(t <= d["duration"] + 1e-6 for t in times), c["name"]
        assert c["name"] in d["viseme_set"]
        if viseme_names:
            assert c["name"] in VISEMES
    return d


def test_e2e_cli_naive_json_csv_anim(tmp_path):
    for ext in ("json", "csv", "anim"):
        out = str(tmp_path / f"t.{ext}")
        rc = cli_main(["naive", "--text", "hello world this is a test",
                       "--wav", VOICE, "-o", out])
        assert rc == 0 and os.path.exists(out)
    _assert_track_invariants(str(tmp_path / "t.json"))
    csv_lines = open(tmp_path / "t.csv").read().splitlines()
    assert csv_lines[0] == "time,channel,value" and len(csv_lines) > 20
    anim = open(tmp_path / "t.anim").read()
    assert anim.startswith("%YAML 1.1") and "blendShape.viseme_" in anim


def test_e2e_cli_mfa_path(tmp_path):
    tg = tmp_path / "line.TextGrid"
    tg.write_text(TEXTGRID)
    out = str(tmp_path / "mfa.json")
    rc = cli_main(["mfa", "--textgrid", str(tg), "-o", out])
    assert rc == 0
    d = _assert_track_invariants(out)
    names = {c["name"] for c in d["channels"]}
    assert "PP" in names            # the final P must seal the lips
    pp = [c for c in d["channels"] if c["name"] == "PP"][0]
    assert max(k[1] for k in pp["keys"]) >= 0.89


def test_e2e_cli_mapping_and_retarget(tmp_path):
    mapping = os.path.join(ROOT, "examples", "mappings", "minimal9.json")
    out = str(tmp_path / "mapped.json")
    rc = cli_main(["naive", "--text", "hello world", "--duration", "1.2",
                   "-o", out, "--mapping", mapping])
    assert rc == 0
    d = _assert_track_invariants(out, viseme_names=False)
    assert set(d["viseme_set"]) >= {"MBP", "open", "rest"}

    out2 = str(tmp_path / "arkit.json")
    rc = cli_main(["naive", "--text", "hello world", "--duration", "1.2",
                   "-o", out2, "--retarget", "arkit"])
    assert rc == 0
    d2 = _assert_track_invariants(out2, viseme_names=False)
    assert any(c["name"] == "jawOpen" for c in d2["channels"])


def test_e2e_cli_rejects_retarget_with_anim(tmp_path):
    with pytest.raises(SystemExit):
        cli_main(["naive", "--text", "x", "--duration", "0.5",
                  "-o", str(tmp_path / "x.anim"), "--retarget", "arkit"])


def test_e2e_cli_bad_mapping_fails_clearly(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text('{"format": "openfacefx.mapping", "version": 1, '
                   '"targets": [{"name": "x"}], "phonemes": {"QQ": {"x": 1}}}')
    with pytest.raises(ValueError, match="unknown phoneme"):
        cli_main(["naive", "--text", "x", "--duration", "0.5",
                  "-o", str(tmp_path / "x.json"), "--mapping", str(bad)])


def test_e2e_preview_builder(tmp_path):
    track = str(tmp_path / "p.json")
    cli_main(["naive", "--text", "preview me", "--duration", "1.0",
              "-o", track])
    out = str(tmp_path / "preview.html")
    sys.path.insert(0, os.path.join(ROOT, "tools"))
    import build_preview
    build_preview.main(track, out)
    html = open(out, encoding="utf-8").read()
    assert "openfacefx.track" in html and "<canvas" in html.lower()
    assert "render(0);btn.click();" not in html
    # --autoplay (used by the hosted demo) starts playback on load
    build_preview.main(track, out, autoplay=True)
    assert "render(0);btn.click();" in open(out, encoding="utf-8").read()


def _build_preview():
    sys.path.insert(0, os.path.join(ROOT, "tools"))
    import build_preview
    return build_preview


def test_e2e_preview_builder_no_extras_byte_identical(tmp_path):
    """The upgrade must not touch the plain page: with no audio/segments the
    output is the template with only the track substituted — byte for byte."""
    bp = _build_preview()
    track = str(tmp_path / "p.json")
    cli_main(["naive", "--text", "preview me", "--duration", "1.0", "-o", track])
    out = str(tmp_path / "plain.html")
    bp.main(track, out)
    with open(track, encoding="utf-8") as fh:
        expected = bp.TEMPLATE.replace("/*__TRACK__*/null", json.dumps(json.load(fh)))
    html = open(out, encoding="utf-8").read()
    assert html == expected
    for marker in ("ofx-audio", "ofx-lane", "ofx-wave", "data:audio", "const OFX ="):
        assert marker not in html


def test_e2e_preview_builder_with_audio(tmp_path):
    """--audio embeds the WAV as a data URI + <audio> the transport syncs to,
    and a client-side waveform; the page stays a well-formed single file."""
    import base64
    bp = _build_preview()
    track = str(tmp_path / "p.json")
    cli_main(["naive", "--text", "preview me", "--wav", VOICE, "-o", track])
    out = str(tmp_path / "audio.html")
    bp.main(track, out, audio_path=VOICE)
    html = open(out, encoding="utf-8").read()
    assert "data:audio/wav;base64," in html and '<audio id="ofx-audio"' in html
    assert 'id="ofx-wave"' in html and "decodeAudioData" in html
    assert base64.b64encode(open(VOICE, "rb").read()).decode() in html  # real bytes
    assert html.startswith("<!DOCTYPE html>") and html.rstrip().endswith("</html>")
    # --autoplay transforms exactly the one template anchor, never the new JS
    out2 = str(tmp_path / "audio_auto.html")
    bp.main(track, out2, autoplay=True, audio_path=VOICE)
    assert open(out2, encoding="utf-8").read().count("render(0);btn.click();") == 1


def test_e2e_preview_builder_with_segments_and_emit(tmp_path):
    """naive --emit-segments dumps the JSON the previewer's --segments lane
    consumes; the lane renders with click-to-seek and confidence tinting."""
    bp = _build_preview()
    track = str(tmp_path / "p.json")
    segs = str(tmp_path / "segs.json")
    cli_main(["naive", "--text", "hello world", "--duration", "1.0",
              "-o", track, "--emit-segments", segs])
    seg_data = json.load(open(segs))
    assert seg_data and all({"phoneme", "start", "end"} <= set(s) for s in seg_data)
    assert all(s["start"] <= s["end"] for s in seg_data)

    conf = str(tmp_path / "conf.json")
    with open(conf, "w", encoding="utf-8") as fh:
        json.dump([{"phoneme": "HH", "start": 0.0, "end": 0.4, "confidence": 0.3},
                   {"phoneme": "OW", "start": 0.4, "end": 1.0, "confidence": 0.95}], fh)
    out = str(tmp_path / "seg.html")
    bp.main(track, out, segments_path=conf)
    html = open(out, encoding="utf-8").read()
    assert 'id="ofx-lane"' in html and '"HH"' in html
    assert "confidence" in html and "playSpan" in html and "hsl(" in html
    # segments-only page carries no audio element or waveform
    assert "data:audio" not in html and 'id="ofx-wave"' not in html
    assert html.rstrip().endswith("</html>")


def test_e2e_batch_dialogue_example(tmp_path):
    demo = os.path.join(ROOT, "examples", "dialogue")
    out = str(tmp_path / "tracks")
    rc = cli_main(["batch", "--dir", demo, "--out", out, "--recurse"])
    assert rc == 0
    summary = json.loads(open(os.path.join(out, "batch_summary.json")).read())
    assert summary["failed"] == 0 and summary["processed"] >= 3
    modes = {r["mode"] for r in summary["rows"]}
    assert modes == {"naive", "mfa"}   # demo covers both alignment paths
    for r in summary["rows"]:
        _assert_track_invariants(os.path.join(out, r["out"]))


def test_e2e_unity_anim_all_visemes_and_vrchat(tmp_path):
    out = str(tmp_path / "v.anim")
    rc = cli_main(["naive", "--text", "we watch福 pop", "--duration", "1.0",
                   "-o", out, "--anim-naming", "vrchat"])
    assert rc == 0
    text = open(out, encoding="utf-8").read()
    assert text.count("attribute: blendShape.vrc.v_") == 30  # 15 + editor dup
    for k in ("vrc.v_ih", "vrc.v_oh", "vrc.v_ou"):
        assert k in text


def test_e2e_long_utterance_and_odd_input(tmp_path):
    long_text = ("the quick brown fox jumps over the lazy dog " * 12).strip()
    out = str(tmp_path / "long.json")
    rc = cli_main(["naive", "--text", long_text, "--duration", "34.0",
                   "-o", out])
    assert rc == 0
    d = _assert_track_invariants(out)
    assert abs(d["duration"] - 34.0) < 0.1
    # punctuation, digits, unicode, apostrophes
    rc = cli_main(["naive", "--text", "it's 2026 — naïve café £5!?",
                   "--duration", "2.0", "-o", str(tmp_path / "odd.json")])
    assert rc == 0
