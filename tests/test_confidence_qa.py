"""Low-confidence phoneme QA flagging (qa.confidence_flags, #72).

FaceFX surfaces low-confidence phonemes for hand-fixing; we already carry
PhonemeSegment.confidence but never flagged it. This adds confidence_flags (a
sibling of cue_flags) and a confidence_warnings key to summarize(), plus a
--min-confidence CLI flag. Inert unless segments carry confidence (no built-in
aligner populates it), so default output is unchanged.
"""

import json
import os
import sys

import pytest

try:
    import openfacefx  # noqa: F401
except ImportError:
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from openfacefx.alignment import PhonemeSegment
from openfacefx.cli import main as cli_main
from openfacefx.qa import confidence_flags, summarize


def _segs():
    return [
        PhonemeSegment("P", 0.0, 0.1, 0.9),    # high confidence -> not flagged
        PhonemeSegment("AE", 0.1, 0.3, 0.3),   # low -> flagged
        PhonemeSegment("sil", 0.3, 0.5, 0.1),  # silence -> skipped though low
        PhonemeSegment("T", 0.5, 0.6, None),   # no confidence -> skipped
        PhonemeSegment("K", 0.6, 0.7, 0.4),    # low -> flagged
    ]


def test_flags_below_threshold_only():
    assert confidence_flags(_segs(), min_confidence=0.5) == [
        {"phoneme": "AE", "start": 0.1, "confidence": 0.3},
        {"phoneme": "K", "start": 0.6, "confidence": 0.4},
    ]


def test_threshold_respected():
    assert confidence_flags(_segs(), min_confidence=0.35) == [
        {"phoneme": "AE", "start": 0.1, "confidence": 0.3},   # K (0.4) now above
    ]


def test_inert_without_confidence():
    segs = [PhonemeSegment("P", 0.0, 0.1), PhonemeSegment("AE", 0.1, 0.3)]
    assert confidence_flags(segs) == []                       # all None -> empty


def test_time_sorted():
    segs = [PhonemeSegment("Z", 0.9, 1.0, 0.1), PhonemeSegment("A", 0.1, 0.2, 0.1)]
    assert [c["start"] for c in confidence_flags(segs)] == [0.1, 0.9]


def test_summarize_includes_confidence_warnings():
    d = summarize(None, segments=_segs(), min_confidence=0.5)
    assert d["confidence_warnings"] == [
        {"phoneme": "AE", "start": 0.1, "confidence": 0.3},
        {"phoneme": "K", "start": 0.6, "confidence": 0.4},
    ]
    # always present (schema stable), empty when there is nothing to flag
    assert summarize(None)["confidence_warnings"] == []


def test_cli_report_has_key_and_is_inert(tmp_path):
    out, rep = str(tmp_path / "t.json"), str(tmp_path / "qa.json")
    # naive segments carry no confidence -> key present but empty
    assert cli_main(["naive", "--text", "hello world", "--duration", "1.5",
                     "--report", rep, "-o", out]) == 0
    doc = json.load(open(rep))
    assert doc["confidence_warnings"] == []


def test_cli_min_confidence_validation(tmp_path):
    with pytest.raises(SystemExit, match="min-confidence"):
        cli_main(["naive", "--text", "hi", "--duration", "1.0",
                  "--report", str(tmp_path / "q.json"), "--min-confidence", "1.5",
                  "-o", str(tmp_path / "t.json")])
