"""QA / embedding ergonomics (issue #23): the machine-readable ``--json`` /
``--report`` summary, transcript normalization, cue-duration flags, and the
public ``summarize`` / ``normalize_transcript`` / ``cue_flags`` API.

The load-bearing invariant is *additive*: without a flag the written track is
byte-identical to before and the console line is unchanged; the JSON is an
opt-in overlay a CI step or wrapping tool can parse instead of scraping text."""

import json
import os
import sys

import pytest

try:
    import openfacefx  # noqa: F401
except ImportError:
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from openfacefx import summarize, normalize_transcript, cue_flags
from openfacefx.cli import main as cli_main
from openfacefx.alignment import PhonemeSegment
from openfacefx.curves import FaceTrack

ROOT = os.path.join(os.path.dirname(__file__), "..")
VOICE = os.path.join(ROOT, "examples", "voice.wav")

# Every key the openfacefx.qa summary promises, always present so the schema is
# stable for a CI consumer regardless of input.
QA_KEYS = {"format", "version", "command", "output", "fps", "duration",
           "channels", "keyframes", "gestures", "events", "oov_words",
           "substitutions", "cue_warnings", "warnings"}

TEXTGRID = '''File type = "ooTextFile"
Object class = "TextGrid"
xmin = 0
xmax = 1.0
tiers? <exists>
size = 1
item []:
    item [1]:
        class = "IntervalTier"
        name = "phones"
        xmin = 0
        xmax = 1.0
        intervals: size = 3
        intervals [1]:
            xmin = 0.0
            xmax = 0.3
            text = "HH"
        intervals [2]:
            xmin = 0.3
            xmax = 0.7
            text = "OW1"
        intervals [3]:
            xmin = 0.7
            xmax = 1.0
            text = "P"
'''

CARTESIA = json.dumps({"phoneme_timestamps": {
    "phonemes": ["p", "a", "t"],
    "start": [0.0, 0.1, 0.2], "end": [0.1, 0.2, 0.3]}})


def _json_run(capsys, argv):
    """Run a CLI command with --json and return the parsed single-line summary,
    asserting stdout is exactly one JSON line (nothing leaked past the flag)."""
    assert cli_main(argv) == 0
    out = capsys.readouterr().out
    assert out.count("\n") == 1 and "wrote " not in out
    return json.loads(out)


# --------------------------------------------------------------------------- #
# --json summary: schema, parseability, determinism                           #
# --------------------------------------------------------------------------- #

def test_json_summary_schema_stable_and_parseable(capsys, tmp_path):
    naive = _json_run(capsys, ["naive", "--text", "hello world this is a test",
                               "--wav", VOICE, "-o", str(tmp_path / "n.json"),
                               "--json"])
    tg = tmp_path / "l.TextGrid"
    tg.write_text(TEXTGRID)
    mfa = _json_run(capsys, ["mfa", "--textgrid", str(tg),
                             "-o", str(tmp_path / "m.json"), "--json"])
    # identical key set across two very different inputs => stable schema
    for s in (naive, mfa):
        assert set(s) == QA_KEYS
        assert s["format"] == "openfacefx.qa" and s["version"] == 1
        assert isinstance(s["channels"], int) and isinstance(s["keyframes"], int)
        assert isinstance(s["oov_words"], list) and isinstance(s["warnings"], list)
    assert naive["command"] == "naive" and mfa["command"] == "mfa"
    assert naive["channels"] > 0 and naive["duration"] > 0


def test_json_summary_deterministic(capsys, tmp_path):
    out = str(tmp_path / "d.json")
    argv = ["naive", "--text", "hello world zorptrix it's here",
            "--duration", "1.5", "-o", out, "--json"]
    cli_main(argv)
    first = capsys.readouterr().out
    cli_main(argv)                      # same output path => identical summary
    assert capsys.readouterr().out == first
    # ensure_ascii: the JSON is pure ASCII even with non-ASCII substitutions
    assert first == first.encode("ascii", "replace").decode()


@pytest.mark.parametrize("cmd,extra", [
    ("naive", ["--text", "hello world this is a test", "--duration", "1.6"]),
    ("energy", ["--wav", VOICE]),
])
def test_json_leaves_output_byte_identical(capsys, tmp_path, cmd, extra):
    plain = str(tmp_path / "plain.json")
    withj = str(tmp_path / "withj.json")
    assert cli_main([cmd, *extra, "-o", plain]) == 0
    assert cli_main([cmd, *extra, "-o", withj, "--json"]) == 0
    assert open(plain, "rb").read() == open(withj, "rb").read()


def test_json_byte_identical_mfa_and_from_timing(capsys, tmp_path):
    tg = tmp_path / "l.TextGrid"
    tg.write_text(TEXTGRID)
    cart = tmp_path / "c.json"
    cart.write_text(CARTESIA)
    for argv in (["mfa", "--textgrid", str(tg)],
                 ["from-timing", "--file", str(cart), "--format", "cartesia"]):
        a, b = str(tmp_path / "a.json"), str(tmp_path / "b.json")
        assert cli_main([*argv, "-o", a]) == 0
        assert cli_main([*argv, "-o", b, "--json"]) == 0
        assert open(a, "rb").read() == open(b, "rb").read()


# --------------------------------------------------------------------------- #
# OOV words + warnings[] surfacing                                            #
# --------------------------------------------------------------------------- #

def test_oov_words_surface_in_summary_and_warnings(capsys, tmp_path):
    s = _json_run(capsys, ["naive", "--text", "zorptrix hello world",
                           "--duration", "1.0", "-o", str(tmp_path / "o.json"),
                           "--json"])
    assert "zorptrix" in s["oov_words"]
    assert any("zorptrix" in w and "G2P" in w for w in s["warnings"])


def test_unknown_vendor_symbol_populates_warnings(capsys, tmp_path):
    cart = tmp_path / "c.json"
    cart.write_text(json.dumps({"phoneme_timestamps": {
        "phonemes": ["p", "✳", "✳", "a"],
        "start": [0, 0.1, 0.2, 0.3], "end": [0.1, 0.2, 0.3, 0.4]}}))
    s = _json_run(capsys, ["from-timing", "--file", str(cart),
                           "--format", "cartesia",
                           "-o", str(tmp_path / "o.json"), "--json"])
    assert any("✳" in w and "silence" in w for w in s["warnings"])


# --------------------------------------------------------------------------- #
# Default path unchanged; --report is additive                                #
# --------------------------------------------------------------------------- #

def test_default_no_flag_prints_human_line_only(capsys, tmp_path):
    assert cli_main(["naive", "--text", "hello world", "--duration", "1.0",
                     "-o", str(tmp_path / "h.json")]) == 0
    out = capsys.readouterr().out
    assert out.startswith("wrote ") and "{" not in out


def test_report_writes_file_and_keeps_human_line(capsys, tmp_path):
    rep = str(tmp_path / "report.json")
    assert cli_main(["naive", "--text", "hello world", "--duration", "1.0",
                     "-o", str(tmp_path / "r.json"), "--report", rep]) == 0
    out = capsys.readouterr().out
    assert out.startswith("wrote ")             # human console output preserved
    doc = json.load(open(rep))
    assert set(doc) == QA_KEYS and doc["format"] == "openfacefx.qa"


# --------------------------------------------------------------------------- #
# Transcript normalization (issue #23 part 3)                                 #
# --------------------------------------------------------------------------- #

def test_normalize_transcript_folds_all_variants_and_reports():
    # escapes not glyphs: the NBSP (U+00A0) before "test" is invisible in source
    raw = "it\u2019s \u201cfun\u201d\u2014a\u2013b\u2026 caf\u00e9\u00a0test"
    out, subs = normalize_transcript(raw)
    assert out == "it's \"fun\"--a-b... caf\u00e9 test"  # e-acute is left alone
    got = {s["from"]: s["count"] for s in subs}
    assert got == {"\u2019": 1, "\u201c": 1, "\u201d": 1,
                   "\u2014": 1, "\u2013": 1, "\u2026": 1, "\u00a0": 1}

def test_normalize_transcript_ascii_is_noop():
    assert normalize_transcript("plain ascii, it's fine.") == \
        ("plain ascii, it's fine.", [])


def test_cli_normalization_default_on_and_opt_out(capsys, tmp_path):
    # a curly apostrophe is the case that actually changes phonemes: normalized
    # "it's" is one token; unnormalized it splits at U+2019 into two.
    on = _json_run(capsys, ["naive", "--text", "it’s a test",
                            "--duration", "1.0", "-o", str(tmp_path / "on.json"),
                            "--json"])
    assert any(s["from"] == "’" for s in on["substitutions"])
    off = _json_run(capsys, ["naive", "--text", "it’s a test",
                             "--duration", "1.0", "-o", str(tmp_path / "off.json"),
                             "--json", "--no-normalize"])
    assert off["substitutions"] == []
    # ASCII transcript: --no-normalize cannot change the written track
    a, b = str(tmp_path / "a.json"), str(tmp_path / "b.json")
    cli_main(["naive", "--text", "hello world", "--duration", "1.0", "-o", a])
    cli_main(["naive", "--text", "hello world", "--duration", "1.0", "-o", b,
              "--no-normalize"])
    assert open(a, "rb").read() == open(b, "rb").read()


# --------------------------------------------------------------------------- #
# Cue-duration flags (issue #23 part 4)                                       #
# --------------------------------------------------------------------------- #

def test_cue_flags_short_long_and_ignores_silence():
    segs = [PhonemeSegment("sil", 0.0, 0.9),       # long, but silence: ignored
            PhonemeSegment("AH0", 0.9, 0.91),       # 10 ms -> short
            PhonemeSegment("S", 0.91, 1.7),         # 790 ms -> long
            PhonemeSegment("T", 1.7, 1.9)]          # 200 ms -> ok
    flags = cue_flags(segs, min_dur=0.03, max_dur=0.5)
    assert [(f["phoneme"], f["kind"]) for f in flags] == \
        [("AH0", "short"), ("S", "long")]
    assert flags[0]["duration"] == 0.01 and flags[0]["start"] == 0.9
    assert [f["start"] for f in flags] == sorted(f["start"] for f in flags)


def test_cli_cue_warnings_have_clip_time_duration(capsys, tmp_path):
    s = _json_run(capsys, ["naive", "--text", "the quick brown fox",
                           "--duration", "2.0", "-o", str(tmp_path / "c.json"),
                           "--json", "--min-cue", "0.1"])
    assert s["cue_warnings"]
    for c in s["cue_warnings"]:
        assert set(c) == {"phoneme", "start", "duration", "kind"}
        assert c["kind"] in ("short", "long")


def test_min_cue_exceeding_max_cue_is_rejected(tmp_path):
    with pytest.raises(SystemExit):
        cli_main(["naive", "--text", "hi", "--duration", "1.0",
                  "-o", str(tmp_path / "x.json"), "--json",
                  "--min-cue", "0.5", "--max-cue", "0.1"])


# --------------------------------------------------------------------------- #
# Library embeddability: summarize() without the CLI                          #
# --------------------------------------------------------------------------- #

def test_summarize_is_callable_directly_and_deterministic():
    from openfacefx.pipeline import naive_segments, generate_naive
    track = generate_naive("hello zorptrix", 1.0)
    segs = naive_segments("hello zorptrix", 1.0)
    a = summarize(track, segments=segs, oov_words=["zorptrix"])
    b = summarize(track, segments=segs, oov_words=["zorptrix"])
    assert a == b                                   # deterministic
    assert set(a) == QA_KEYS
    assert a["channels"] == len(track.channels) and a["fps"] == 60.0
    assert a["oov_words"] == ["zorptrix"]
    assert any("G2P" in w for w in a["warnings"])   # OOV rollup synthesized
    assert json.loads(json.dumps(a)) == a           # JSON round-trips


def test_summarize_empty_track_warns_and_lip_path_has_no_channels():
    empty = summarize(FaceTrack(fps=60.0, channels=[]))
    assert empty["channels"] == 0 and empty["keyframes"] == 0
    assert any("no channels" in w for w in empty["warnings"])
    # track=None (the .lip case): duration comes from the segments, counts are 0
    segs = [PhonemeSegment("HH", 0.0, 0.4), PhonemeSegment("OW1", 0.4, 1.2)]
    lip = summarize(None, segments=segs, command="naive")
    assert lip["channels"] == 0 and lip["fps"] is None and lip["duration"] == 1.2


def test_summarize_counts_gestures_and_events(capsys, tmp_path):
    g = _json_run(capsys, ["naive", "--text", "hello world this is a test",
                           "--wav", VOICE, "-o", str(tmp_path / "g.json"),
                           "--json", "--gestures", "--events"])
    assert g["gestures"] >= 1 and g["events"] >= 1
