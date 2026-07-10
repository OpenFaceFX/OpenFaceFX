"""Word/segment-anchored alignment tests (issue #15).

Every parser has an inline fixture (exact word text and converted times) and a
malformed-input case asserting a clear ValueError; the anchored distribution is
checked for the two guarantees that matter (phonemes never leave their span;
wordless gaps relax to silence); the no-anchor path is asserted byte-identical
to ``naive_segments``; the issue's accuracy criterion is a synthetic reference
whose derived anchors beat the unanchored spread; and the CLI is run end to end
with an SRT fixture under the same track invariants the other paths hold."""

import json
import os
import re
import sys

import pytest

try:
    import openfacefx  # noqa: F401  (installed wheel wins; see test_core)
except ImportError:
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from openfacefx import anchors as A
from openfacefx.anchors import (
    Anchor, anchored_segments, anchors_transcript, parse_srt,
    parse_word_anchors, from_azure_word_boundaries, from_elevenlabs_alignment,
    from_kokoro_tokens, google_ssml_with_marks, from_google_timepoints,
)
from openfacefx.alignment import NaiveAligner, PhonemeSegment
from openfacefx.g2p import G2P
from openfacefx.phonemes import SILENCE
from openfacefx.pipeline import naive_segments
from openfacefx.cli import main as cli_main
from openfacefx.visemes import VISEMES


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


# --------------------------------------------------------------------------- #
# parse_srt                                                                     #
# --------------------------------------------------------------------------- #

SRT = """1
00:00:00,200 --> 00:00:01,000
Hello world

2
00:00:01.500 --> 00:00:02,800
<i>this is</i> a test
{\\an8}second line
"""


def test_parse_srt_multiline_tags_and_decimal_mark():
    cues = parse_srt(SRT)
    assert [c.text for c in cues] == ["Hello world", "this is a test second line"]
    # HH:MM:SS with both ',' and '.' as the millisecond separator
    assert [(round(c.start, 3), round(c.end, 3)) for c in cues] == [
        (0.2, 1.0), (1.5, 2.8)]
    # cue text concatenates into a transcript when --text is omitted
    assert anchors_transcript(cues) == "Hello world this is a test second line"


def test_parse_srt_rejects_malformed():
    with pytest.raises(ValueError, match="no cues"):
        parse_srt("not a subtitle file\n")
    with pytest.raises(ValueError, match="timecode"):
        parse_srt("1\n00:00:01 -->\nbroken\n")


# --------------------------------------------------------------------------- #
# parse_word_anchors                                                            #
# --------------------------------------------------------------------------- #

def test_parse_word_anchors_array_and_wrapper_and_optional_end():
    a = parse_word_anchors(json.dumps(
        [{"text": "hello", "start": 0.1, "end": 0.4}, {"text": "world", "start": 0.5}]))
    assert [(x.text, x.start, x.end) for x in a] == [
        ("hello", 0.1, 0.4), ("world", 0.5, None)]
    # object wrapper + explicit null end
    w = parse_word_anchors(json.dumps({"anchors": [{"text": "hi", "start": 0, "end": None}]}))
    assert w[0].end is None


def test_parse_word_anchors_rejects_malformed():
    with pytest.raises(ValueError, match="not valid JSON"):
        parse_word_anchors("{oops")
    with pytest.raises(ValueError, match="string 'text'"):
        parse_word_anchors(json.dumps([{"start": 0.0}]))
    with pytest.raises(ValueError, match="must be a number"):
        parse_word_anchors(json.dumps([{"text": "x", "start": "soon"}]))
    with pytest.raises(ValueError, match="no anchors"):
        parse_word_anchors("[]")


# --------------------------------------------------------------------------- #
# from_azure_word_boundaries                                                    #
# --------------------------------------------------------------------------- #

AZURE = json.dumps([
    {"Text": "hello", "AudioOffset": 1000000, "Duration": 3000000, "BoundaryType": "Word"},
    {"Text": ",", "AudioOffset": 4000000, "Duration": 0, "BoundaryType": "Punctuation"},
    {"Text": "world", "AudioOffset": 4500000, "Duration": 3500000, "BoundaryType": "Word"},
])


def test_from_azure_ticks_to_seconds_and_skips_punctuation():
    a = from_azure_word_boundaries(AZURE)
    # 100-ns ticks / 1e7 = seconds; punctuation boundary dropped
    assert [(x.text, round(x.start, 3), round(x.end, 3)) for x in a] == [
        ("hello", 0.1, 0.4), ("world", 0.45, 0.8)]


def test_from_azure_snake_case_aliases_wrapper_and_missing_duration():
    a = from_azure_word_boundaries(json.dumps({"words": [
        {"text": "hi", "audio_offset": 2000000}]}))          # no duration -> open end
    assert a[0].text == "hi" and round(a[0].start, 3) == 0.2 and a[0].end is None


def test_from_azure_rejects_malformed():
    with pytest.raises(ValueError, match="not valid JSON"):
        from_azure_word_boundaries("nope")
    with pytest.raises(ValueError, match="'Text' and 'AudioOffset'"):
        from_azure_word_boundaries(json.dumps([{"AudioOffset": 0}]))
    with pytest.raises(ValueError, match="no word-boundary"):
        from_azure_word_boundaries(json.dumps([{"Text": "...", "AudioOffset": 0}]))


# --------------------------------------------------------------------------- #
# from_elevenlabs_alignment                                                     #
# --------------------------------------------------------------------------- #

def _el_align(chars, step=0.1):
    starts = [round(i * step, 4) for i in range(len(chars))]
    ends = [round((i + 1) * step, 4) for i in range(len(chars))]
    return {"characters": list(chars),
            "character_start_times_seconds": starts,
            "character_end_times_seconds": ends}


def test_from_elevenlabs_groups_words_and_prefers_normalized():
    payload = json.dumps({
        "audio_base64": "x",
        "alignment": _el_align("hi"),                        # would give one word
        "normalized_alignment": _el_align("Hi there")})      # preferred
    a = from_elevenlabs_alignment(payload)
    # grouped at the space: "Hi" [0,0.2], "there" [0.3,0.8]
    assert [x.text for x in a] == ["Hi", "there"]
    assert (round(a[0].start, 3), round(a[0].end, 3)) == (0.0, 0.2)
    assert (round(a[1].start, 3), round(a[1].end, 3)) == (0.3, 0.8)
    # a bare alignment object (three arrays at top level) also works
    bare = from_elevenlabs_alignment(json.dumps(_el_align("ab")))
    assert bare[0].text == "ab"


def test_from_elevenlabs_rejects_malformed():
    with pytest.raises(ValueError, match="differ in length"):
        from_elevenlabs_alignment(json.dumps(
            {"alignment": {"characters": ["a"], "character_start_times_seconds": [],
                           "character_end_times_seconds": [0.1]}}))
    with pytest.raises(ValueError, match="no 'alignment'"):
        from_elevenlabs_alignment(json.dumps({"audio_base64": "x"}))


# --------------------------------------------------------------------------- #
# from_kokoro_tokens                                                            #
# --------------------------------------------------------------------------- #

def test_from_kokoro_none_tolerant():
    a = from_kokoro_tokens(json.dumps({"tokens": [
        {"text": "hello", "start_ts": 0.0, "end_ts": 0.3, "whitespace": True},
        {"text": "lost", "start_ts": None, "end_ts": None},   # no start -> dropped
        {"text": " ", "start_ts": 0.31, "end_ts": 0.32},      # whitespace -> dropped
        {"text": "world", "start_ts": 0.5, "end_ts": None}]})) # open end kept
    assert [(x.text, x.start, x.end) for x in a] == [
        ("hello", 0.0, 0.3), ("world", 0.5, None)]


def test_from_kokoro_rejects_malformed():
    with pytest.raises(ValueError, match="not valid JSON"):
        from_kokoro_tokens("{")
    with pytest.raises(ValueError, match="no timestamped"):
        from_kokoro_tokens(json.dumps({"tokens": [{"text": "x", "start_ts": None}]}))


# --------------------------------------------------------------------------- #
# google_ssml_with_marks / from_google_timepoints                               #
# --------------------------------------------------------------------------- #

def test_google_ssml_marks_are_pure_transform_and_escape_xml():
    ssml = google_ssml_with_marks("hello world friends")
    assert ssml == ('<speak><mark name="w0"/>hello <mark name="w1"/>world '
                    '<mark name="w2"/>friends</speak>')
    # original punctuation is preserved and XML metacharacters escaped in place
    esc = google_ssml_with_marks("a & b")
    assert esc == '<speak><mark name="w0"/>a &amp; <mark name="w1"/>b</speak>'


def test_from_google_timepoints_maps_marks_to_words():
    tp = json.dumps({"timepoints": [{"markName": "w0", "timeSeconds": 0.1},
                                    {"markName": "w1", "timeSeconds": 0.6}]})
    a = from_google_timepoints(tp, "hello world")
    assert [(x.text, x.start, x.end) for x in a] == [
        ("hello", 0.1, None), ("world", 0.6, None)]
    with pytest.raises(ValueError, match="beyond the transcript"):
        from_google_timepoints(json.dumps([{"markName": "w9", "timeSeconds": 0.1}]),
                               "hello world")
    with pytest.raises(ValueError, match="not 'w<index>'"):
        from_google_timepoints(json.dumps([{"markName": "start", "timeSeconds": 0}]),
                               "hi")


# --------------------------------------------------------------------------- #
# anchored_segments distribution                                                #
# --------------------------------------------------------------------------- #

def test_no_anchors_is_byte_identical_to_naive_segments():
    g = G2P()
    for text, dur in [("the quick brown fox", 2.0), ("", 1.0), ("hello", 0.8)]:
        a = anchored_segments(text, dur, None, g2p=g)
        b = naive_segments(text, dur, g2p=g)
        key = lambda segs: [(s.phoneme, round(s.start, 9), round(s.end, 9)) for s in segs]
        assert key(a) == key(b), text


def test_anchored_keeps_phonemes_strictly_inside_span():
    g = G2P()
    segs = anchored_segments("the fox", 1.0, [Anchor("fox", 0.6, 0.9)], g2p=g)
    fox = [s for s in segs if s.phoneme in ("F", "AA1", "K", "S")]     # fox phones
    the = [s for s in segs if s.phoneme in ("DH", "AH0")]              # the phones
    assert fox and all(0.6 - 1e-9 <= s.start and s.end <= 0.9 + 1e-9 for s in fox)
    assert the and all(s.end <= 0.6 + 1e-9 for s in the)              # 'the' leads in


def test_anchored_long_gap_relaxes_short_gap_does_not():
    g = G2P()
    # 0.4 s gap between cue ends -> a sil segment spanning it
    segs = anchored_segments("hello world", 1.2,
                             [Anchor("hello", 0.1, 0.4), Anchor("world", 0.8, 1.0)], g2p=g)
    assert any(s.phoneme == SILENCE and abs(s.start - 0.4) < 1e-6
               and abs(s.end - 0.8) < 1e-6 for s in segs)
    # 0.1 s gap: no relaxation sil sits inside it
    tight = anchored_segments("hello world", 1.0,
                              [Anchor("hello", 0.1, 0.4), Anchor("world", 0.5, 0.9)], g2p=g)
    assert not any(s.phoneme == SILENCE and s.start >= 0.4 - 1e-9
                   and s.end <= 0.5 + 1e-9 and s.end - s.start > 1e-6 for s in tight)


def test_anchored_end_none_extends_to_next_start_and_shares_gap_words():
    g = G2P()
    # two open-ended word anchors, no words between: first runs to the second's start
    segs = anchored_segments("hello world", 1.0,
                             [Anchor("hello", 0.1), Anchor("world", 0.5)], g2p=g)
    hello = [s for s in segs if s.phoneme in ("HH", "OW1")]   # phones unique to hello
    assert hello and all(s.end <= 0.5 + 1e-9 for s in hello)
    # one open anchor with uncovered trailing words: they get a share of the tail,
    # so the anchor cannot swallow the whole remainder
    segs2 = anchored_segments("the quick brown fox", 2.0, [Anchor("quick", 0.5)], g2p=g)
    quick = [s for s in segs2 if s.phoneme in ("W", "IH1")]   # phones unique to quick
    fox_f = [s for s in segs2 if s.phoneme == "F"]            # 'fox' starts after
    assert quick and all(s.start >= 0.5 - 1e-9 for s in quick)
    assert max(s.end for s in quick) < 1.5                    # only a share of [0.5, 2.0]
    assert fox_f and min(s.start for s in fox_f) >= max(s.end for s in quick) - 1e-9


def test_anchored_validation_errors():
    g = G2P()
    with pytest.raises(ValueError, match="not found in the transcript"):
        anchored_segments("hello world", 1.0, [Anchor("banana", 0.1, 0.4)], g2p=g)
    with pytest.raises(ValueError, match="time-ordered|overlap"):
        anchored_segments("hello world", 1.0,
                          [Anchor("hello", 0.5, 0.9), Anchor("world", 0.2, 0.4)], g2p=g)
    with pytest.raises(ValueError, match="end .* before start"):
        anchored_segments("hello", 1.0, [Anchor("hello", 0.8, 0.2)], g2p=g)
    with pytest.raises(ValueError, match="after the audio"):
        anchored_segments("hello", 1.0, [Anchor("hello", 3.0)], g2p=g)


def test_anchored_beats_unanchored_against_synthetic_reference():
    """Issue #15 accuracy criterion. Build a deliberately uneven ground-truth
    alignment (word spans NOT proportional to phoneme weight, so a uniform spread
    is provably wrong), derive one anchor per word from it, and assert the
    anchored aligner's mean segment-midpoint error against the reference is
    strictly smaller than the unanchored aligner's."""
    g = G2P()
    text = "the quick brown fox jumps over the lazy dog"
    words = re.findall(r"[A-Za-z']+", text)
    duration = 6.0
    lead, tail = 0.4, 0.5
    # first word hogs the timeline; the rest are short and equal -> very non-uniform
    widths = [6.0] + [1.0] * (len(words) - 1)
    span = duration - lead - tail
    scale = span / sum(widths)
    spans, t = [], lead
    for w in widths:
        spans.append((t, t + w * scale)); t += w * scale

    ref = [PhonemeSegment(SILENCE, 0.0, lead)]
    for (s, e), w in zip(spans, words):
        ref += NaiveAligner().align(g.word(w), e - s, start=s)
    ref.append(PhonemeSegment(SILENCE, spans[-1][1], duration))

    anchors = [Anchor(w, s, e) for (s, e), w in zip(spans, words)]
    anchored = anchored_segments(text, duration, anchors, g2p=g)
    unanchored = anchored_segments(text, duration, [], g2p=g)   # == naive_segments

    seq = [s.phoneme for s in ref]
    assert [s.phoneme for s in anchored] == seq == [s.phoneme for s in unanchored]

    def mae(segs):
        mid = lambda s: 0.5 * (s.start + s.end)
        return sum(abs(mid(x) - mid(r)) for x, r in zip(segs, ref)) / len(ref)

    err_anchored, err_unanchored = mae(anchored), mae(unanchored)
    assert err_anchored < err_unanchored          # the criterion
    assert err_anchored < 1e-6                     # anchors recover the reference
    assert err_unanchored > 0.1                    # uneven spans really do hurt


# --------------------------------------------------------------------------- #
# CLI: openfacefx naive --anchors ... (MFA-grade invariants)                    #
# --------------------------------------------------------------------------- #

def test_cli_naive_srt_supplies_transcript(tmp_path):
    src = tmp_path / "cues.srt"
    src.write_text(SRT, encoding="utf-8")
    out = str(tmp_path / "srt.json")
    rc = cli_main(["naive", "--anchors", str(src), "--anchors-format", "srt",
                   "--duration", "3.0", "-o", out])
    assert rc == 0
    d = _assert_track_invariants(out)
    assert abs(d["duration"] - 3.0) < 0.05
    names = {c["name"] for c in d["channels"]}
    assert names & {"aa", "E", "I", "O", "U"}    # vowels from the cue words open the mouth


def test_cli_naive_azure_anchors_with_text(tmp_path):
    src = tmp_path / "az.json"
    src.write_text(AZURE, encoding="utf-8")
    out = str(tmp_path / "az.json.out")
    rc = cli_main(["naive", "--anchors", str(src), "--anchors-format", "azure",
                   "--text", "hello world", "--duration", "1.0", "-o", out])
    assert rc == 0
    _assert_track_invariants(out)


def test_cli_naive_google_marks_roundtrip(tmp_path):
    # marks named as google_ssml_with_marks would emit them
    tp = tmp_path / "tp.json"
    tp.write_text(json.dumps({"timepoints": [{"markName": "w0", "timeSeconds": 0.2},
                                             {"markName": "w1", "timeSeconds": 0.7}]}),
                  encoding="utf-8")
    out = str(tmp_path / "g.json")
    rc = cli_main(["naive", "--anchors", str(tp), "--anchors-format", "google",
                   "--text", "hello world", "--duration", "1.2", "-o", out])
    assert rc == 0
    _assert_track_invariants(out)


def test_cli_naive_anchor_flags_require_each_other(tmp_path):
    src = tmp_path / "wa.json"
    src.write_text(json.dumps([{"text": "hi", "start": 0.1}]), encoding="utf-8")
    with pytest.raises(SystemExit, match="only valid together"):
        cli_main(["naive", "--anchors", str(src), "--text", "hi",
                  "--duration", "1.0", "-o", str(tmp_path / "o.json")])
    with pytest.raises(SystemExit, match="required with --anchors-format"):
        cli_main(["naive", "--anchors", str(src), "--anchors-format", "words",
                  "--duration", "1.0", "-o", str(tmp_path / "o.json")])


def test_cli_naive_srt_text_mismatch_is_reported(tmp_path):
    src = tmp_path / "cues.srt"
    src.write_text(SRT, encoding="utf-8")
    # transcript that does not contain the cue words -> ValueError names the anchor
    with pytest.raises(ValueError, match="not found in the transcript"):
        cli_main(["naive", "--anchors", str(src), "--anchors-format", "srt",
                  "--text", "completely different narration", "--duration", "3.0",
                  "-o", str(tmp_path / "o.json")])
