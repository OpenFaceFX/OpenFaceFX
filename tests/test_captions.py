"""Subtitle / caption exporter (issue #41) — SRT + WebVTT from the alignment.

Pins the acceptance: SRT/WebVTT validate against their timestamp grammars
(`HH:MM:SS,mmm` / `HH:MM:SS.mmm`) and are monotonic + non-overlapping; cue
durations respect a configurable reading-speed (CPS) and no cue exceeds the
max-line/max-lines wrap limit; karaoke emits per-word timestamps inside their cue
span; output is deterministic and derived from the SAME alignment the lip curves
use; and `anchors.parse_srt(srt_text(cues))` round-trips the cue spans.
"""

import os
import re
import struct
import sys
import wave

import pytest

try:
    import openfacefx  # noqa: F401
except ImportError:
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from openfacefx.anchors import parse_srt
from openfacefx.cli import main as cli_main
from openfacefx.export_captions import (build_cues, format_timestamp, srt_text,
                                        vtt_text, word_timings, write_captions)
from openfacefx.g2p import G2P
from openfacefx.pipeline import naive_segments
from openfacefx.texttags import naive_word_segments

_SRT_CUE = re.compile(
    r"^\d{2}:\d{2}:\d{2},\d{3} --> \d{2}:\d{2}:\d{2},\d{3}$", re.M)
_VTT_CUE = re.compile(
    r"^\d{2}:\d{2}:\d{2}\.\d{3} --> \d{2}:\d{2}:\d{2}\.\d{3}$", re.M)
_TS = "Hello there, traveler. How are you on this fine morning, my friend?"


def _cues(text=_TS, duration=4.0, **kw):
    return build_cues(word_timings(text, duration, G2P()), **kw)


def _write_wav(path, seconds=2.0, rate=16000):
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(struct.pack("<h", 0) * int(seconds * rate))


# --------------------------------------------------------------------------- #
# grammar + monotonic / non-overlapping                                        #
# --------------------------------------------------------------------------- #

def test_srt_grammar_and_monotonic_non_overlapping():
    cues = _cues()
    srt = srt_text(cues)
    assert len(_SRT_CUE.findall(srt)) == len(cues)          # every cue valid
    for i in range(len(cues) - 1):
        assert cues[i].start <= cues[i].end <= cues[i + 1].start
    assert cues[-1].start < cues[-1].end


def test_vtt_grammar_and_header():
    vtt = vtt_text(_cues())
    assert vtt.startswith("WEBVTT\n")
    assert len(_VTT_CUE.findall(vtt)) == len(_cues())


def test_format_timestamp_srt_and_vtt():
    assert format_timestamp(3661.5, ",") == "01:01:01,500"
    assert format_timestamp(3661.5, ".") == "01:01:01.500"
    assert format_timestamp(0.0015, ",") == "00:00:00,002"     # rounds to ms
    assert format_timestamp(-1.0, ",") == "00:00:00,000"       # floored at 0


# --------------------------------------------------------------------------- #
# parse_srt round-trip (the inverse, anchors.py)                               #
# --------------------------------------------------------------------------- #

def test_parse_srt_round_trips_cue_spans():
    cues = _cues()
    back = parse_srt(srt_text(cues))
    assert len(back) == len(cues)
    for anchor, cue in zip(back, cues):
        assert abs(anchor.start - cue.start) <= 1e-3           # within mmm
        assert abs(anchor.end - cue.end) <= 1e-3
        assert anchor.text == cue.text                         # text preserved


# --------------------------------------------------------------------------- #
# CPS reading speed + wrap limit                                               #
# --------------------------------------------------------------------------- #

def test_cps_min_duration_respected_when_timeline_allows():
    # two far-apart words -> two cues with room to extend to the CPS minimum
    words = [("hello", 0.0, 0.1), ("world", 3.0, 3.1)]
    for cue in build_cues(words, cps=5.0):                     # 5 chars / 5 cps = 1 s
        assert (cue.end - cue.start) >= len(cue.text) / 5.0 - 1e-9


def test_no_cue_exceeds_the_wrap_limit():
    cues = _cues(max_line=20, max_lines=2)
    for cue in cues:
        assert len(cue.lines) <= 2
        for line in cue.lines:
            assert len(line) <= 20
    # a single tight budget still never overflows the line count
    for cue in _cues(max_line=12, max_lines=1):
        assert len(cue.lines) == 1 and len(cue.lines[0]) <= 12 or \
            len(cue.words) == 1                                # lone long word


def test_sentence_and_gap_breaks_split_cues():
    # punctuation ends a sentence; a long silence also breaks a cue
    close = word_timings("Hello there. General Kenobi.", 3.0, G2P())
    assert len(build_cues(close)) >= 2                         # the '.' splits
    gapped = [("hello", 0.0, 0.2), ("world", 2.5, 2.7)]        # 2.3 s gap
    assert len(build_cues(gapped, gap=0.5)) == 2


# --------------------------------------------------------------------------- #
# karaoke                                                                      #
# --------------------------------------------------------------------------- #

def test_karaoke_word_timestamps_fall_inside_cue_span():
    cues = _cues()
    vtt = vtt_text(cues, karaoke=True)
    assert "<c>" in vtt
    # every inline <HH:MM:SS.mmm> timestamp lies within some cue's [start, end]
    def _sec(ts):
        h, m, rest = ts.split(":")
        s, ms = rest.split(".")
        return int(h) * 3600 + int(m) * 60 + int(s) + int(ms) / 1000.0
    from openfacefx.export_captions import _karaoke_payload
    for cue in cues:
        stamps = [_sec(m) for m in
                  re.findall(r"<(\d{2}:\d{2}:\d{2}\.\d{3})>", _karaoke_payload(cue))]
        assert stamps == sorted(stamps)                        # monotonic
        for t in stamps:
            assert cue.start - 1e-6 <= t <= cue.end + 1e-6     # inside the cue


# --------------------------------------------------------------------------- #
# determinism + shared source with the lip curves                             #
# --------------------------------------------------------------------------- #

def test_output_is_deterministic():
    assert srt_text(_cues()) == srt_text(_cues())
    assert vtt_text(_cues(), karaoke=True) == vtt_text(_cues(), karaoke=True)


def test_captions_share_the_alignment_the_curves_use():
    """The caption word timings come from `naive_word_segments`, whose phoneme
    segments are byte-identical to the `naive_segments` that
    `generate_from_alignment` reduces into the viseme curves — one source of
    truth for captions and lip motion."""
    text, dur, g2p = "hello world how are you", 3.0, G2P()
    curve_segs = naive_segments(text, dur, g2p)                # what the curves use
    caption_segs, _ = naive_word_segments(text, dur, g2p)      # what captions use
    assert [(s.phoneme, s.start, s.end) for s in curve_segs] == \
           [(s.phoneme, s.start, s.end) for s in caption_segs]
    # and the cue span tracks those same segment times
    cues = _cues(text, dur)
    non_sil = [s for s in curve_segs if s.phoneme != "sil"]
    assert cues[0].start == pytest.approx(non_sil[0].start)
    assert cues[-1].words[-1][2] == pytest.approx(non_sil[-1].end)


# --------------------------------------------------------------------------- #
# CLI: captions command + naive --emit-captions co-generation                  #
# --------------------------------------------------------------------------- #

def test_cli_captions_writes_valid_srt(tmp_path):
    out = str(tmp_path / "out.srt")
    assert cli_main(["captions", "--text", _TS, "--duration", "4.0",
                     "-o", out]) == 0
    text = open(out, encoding="utf-8").read()
    assert _SRT_CUE.search(text) and parse_srt(text)           # readable back


def test_cli_captions_webvtt_karaoke(tmp_path):
    out = str(tmp_path / "out.vtt")
    assert cli_main(["captions", "--text", "Hello world", "--duration", "2.0",
                     "-o", out, "--karaoke"]) == 0
    text = open(out, encoding="utf-8").read()
    assert text.startswith("WEBVTT\n") and "<c>" in text and _VTT_CUE.search(text)


def test_cli_naive_emit_captions_cogenerates_from_one_run(tmp_path):
    track = str(tmp_path / "track.json")
    srt = str(tmp_path / "side.srt")
    assert cli_main(["naive", "--text", "farewell my friend", "--duration", "2.0",
                     "-o", track, "--emit-captions", srt]) == 0
    assert os.path.exists(track) and os.path.exists(srt)
    # the side captions equal captions built directly from the same text+duration
    from openfacefx.io_export import read_json
    assert read_json(track).duration > 0
    expected = srt_text(_cues("farewell my friend", 2.0))
    assert open(srt, encoding="utf-8").read() == expected


def test_write_captions_picks_format_by_extension(tmp_path):
    srt, vtt = str(tmp_path / "a.srt"), str(tmp_path / "a.vtt")
    write_captions("hello world", 2.0, srt)
    write_captions("hello world", 2.0, vtt)
    assert not open(srt).read().startswith("WEBVTT")
    assert open(vtt).read().startswith("WEBVTT")


def test_cli_batch_captions_sidecar_is_opt_in(tmp_path):
    src, out = tmp_path / "src" / "q", tmp_path / "out"
    src.mkdir(parents=True)
    _write_wav(str(src / "line.wav"))
    (src / "line.txt").write_text("hello world")
    # opt-in: without --captions no sidecar is written
    assert cli_main(["batch", "--dir", str(tmp_path / "src"), "--out", str(out),
                     "--recurse", "--quiet"]) == 0
    assert not (out / "q" / "line.srt").exists()
    # with --captions a .srt lands next to the track, mirroring the tree
    assert cli_main(["batch", "--dir", str(tmp_path / "src"), "--out", str(out),
                     "--recurse", "--captions", "srt", "--quiet"]) == 0
    side = out / "q" / "line.srt"
    assert side.exists() and parse_srt(side.read_text())
