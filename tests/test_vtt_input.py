"""WebVTT input parser — the read-side inverse of the #41 caption exporter (#55).

The hard anchor (same strength as the #44 cue-importer round-trips): the exact
`write_vtt`/`vtt_text` output — in BOTH plain-cue and karaoke/word modes — parses
back through `parse_vtt` to anchors whose start/end match the source `CaptionCue`
timings within millisecond rounding. Plus: `HH:MM:SS.mmm` and `MM:SS.mmm` parse;
cues are monotonic + non-overlapping; `NOTE`/`STYLE`/`REGION`, cue identifiers and
cue settings are ignored; karaoke recovers word anchors inside their cue span;
`--anchors-format vtt` drives generation identically to `srt`; and it is additive.
"""

import os
import sys

import pytest

try:
    import openfacefx  # noqa: F401
except ImportError:
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from openfacefx.cli import main as cli_main
from openfacefx.export_captions import (build_cues, parse_vtt, vtt_text,
                                        word_timings)
from openfacefx.g2p import G2P

_G2P = G2P()


def _cues(text="hello there general kenobi how are you today", duration=4.0):
    return build_cues(word_timings(text, duration, _G2P))


# --------------------------------------------------------------------------- #
# THE round-trip against the #41 writer (plain + karaoke)                       #
# --------------------------------------------------------------------------- #

def test_plain_round_trip_against_writer():
    cues = _cues()
    back = parse_vtt(vtt_text(cues))                      # write -> read
    assert len(back) == len(cues)
    for a, c in zip(back, cues):
        assert abs(a.start - c.start) <= 1e-3            # within ms rounding
        assert abs(a.end - c.end) <= 1e-3
        assert a.text == c.text


def test_karaoke_round_trip_recovers_word_timings():
    cues = _cues()
    words = parse_vtt(vtt_text(cues, karaoke=True))       # word-level
    src = [(t, ws) for c in cues for (t, ws, _we) in c.words]
    assert len(words) == len(src)
    for a, (token, ws) in zip(words, src):
        assert a.text == token
        assert abs(a.start - ws) <= 1e-3                 # each word start recovered


def test_karaoke_words_lie_inside_their_cue_span():
    cues = _cues()
    cue_spans = parse_vtt(vtt_text(cues))                 # cue-level, ms-rounded
    words = parse_vtt(vtt_text(cues, karaoke=True))
    for w in words:
        assert any(c.start - 1e-9 <= w.start and w.end <= c.end + 1e-9
                   for c in cue_spans)                    # inside some cue
        assert w.start <= w.end                           # non-degenerate


# --------------------------------------------------------------------------- #
# timestamp grammars, monotonicity, and blocks that must be ignored            #
# --------------------------------------------------------------------------- #

_EDGE = """WEBVTT
Kind: captions

NOTE this is a multi-line
comment block

STYLE
::cue { color: white }

intro
00:01.000 --> 00:03.500 position:50% align:center
Hello world

REGION
id:r1

2
01:00:04.000 --> 01:00:05.250
<v Speaker>Bye</v> now
"""


def test_edge_cases_grammar_and_ignored_blocks():
    anchors = parse_vtt(_EDGE)
    assert [a.text for a in anchors] == ["Hello world", "Bye now"]
    assert (anchors[0].start, anchors[0].end) == (1.0, 3.5)          # MM:SS.mmm
    assert (anchors[1].start, anchors[1].end) == (3604.0, 3605.25)   # HH:MM:SS.mmm
    # monotonic, non-overlapping
    assert all(anchors[i].end <= anchors[i + 1].start + 1e-9
               for i in range(len(anchors) - 1))


@pytest.mark.parametrize("bad", ["no header at all\n00:00.000 --> 00:01.000\nx",
                                 "WEBVTT\n\nonly an identifier\nno timing here",
                                 "WEBVTT\n\n1\n00:00.000 --> junk\ntext"])
def test_malformed_vtt(bad):
    with pytest.raises(ValueError):
        parse_vtt(bad)


def test_non_karaoke_cue_strips_inline_tags():
    vtt = "WEBVTT\n\n00:00.000 --> 00:02.000\n<c.loud>Hello</c> <b>there</b>\n"
    a = parse_vtt(vtt)
    assert len(a) == 1 and a[0].text == "Hello there"    # <c>/<b> stripped, no TS


# --------------------------------------------------------------------------- #
# CLI: vtt drives generation like srt; additive when absent                     #
# --------------------------------------------------------------------------- #

_SRT = ("1\n00:00:00,500 --> 00:00:01,500\nhello world\n\n"
        "2\n00:00:01,500 --> 00:00:02,500\nbrave new day\n")
_VTT = ("WEBVTT\n\n1\n00:00:00.500 --> 00:00:01.500\nhello world\n\n"
        "2\n00:00:01.500 --> 00:00:02.500\nbrave new day\n")


def test_cli_vtt_matches_srt_on_equivalent_input(tmp_path):
    srt, vtt = tmp_path / "c.srt", tmp_path / "c.vtt"
    srt.write_text(_SRT)
    vtt.write_text(_VTT)
    a, b = str(tmp_path / "a.json"), str(tmp_path / "b.json")
    assert cli_main(["naive", "--anchors", str(srt), "--anchors-format", "srt",
                     "--duration", "3.0", "-o", a]) == 0       # self-transcribing
    assert cli_main(["naive", "--anchors", str(vtt), "--anchors-format", "vtt",
                     "--duration", "3.0", "-o", b]) == 0       # no --text needed
    assert open(a, "rb").read() == open(b, "rb").read()        # identical track


def test_adapters_are_additive_no_anchors_unchanged(tmp_path):
    a, b = str(tmp_path / "a.json"), str(tmp_path / "b.json")
    assert cli_main(["naive", "--text", "hello world", "--duration", "1.5",
                     "-o", a]) == 0
    assert cli_main(["naive", "--text", "hello world", "--duration", "1.5",
                     "-o", b]) == 0
    assert open(a, "rb").read() == open(b, "rb").read()
