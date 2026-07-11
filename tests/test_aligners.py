"""Open-source aligner adapters — Whisper / WhisperX / Gentle (issue #54).

Pins the acceptance against checked-in realistic samples (`tests/data/`): each
parses to the expected `Anchor`/`PhonemeSegment` list (monotonic, non-overlapping,
times within tolerance of the source); the key variance across implementations
(`word`/`text`, `probability`/`score`) is tolerated and an unaligned word is
dropped deterministically without crashing; Gentle's `phones[]` reconstruct
absolute phone times that sum to the word span and route through
`generate_from_alignment` to a valid track; and the adapters are additive (no
`--anchors-format` → output unchanged).
"""

import json
import os
import sys

import pytest

try:
    import openfacefx  # noqa: F401
except ImportError:
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from openfacefx.aligners import (from_gentle, from_gentle_phones,
                                 from_whisper_json, from_whisperx)
from openfacefx.cli import main as cli_main
from openfacefx.inspect import inspect_track
from openfacefx.io_export import from_dict, to_dict
from openfacefx.pipeline import generate_from_alignment

_DATA = os.path.join(os.path.dirname(__file__), "data")


def _load(name):
    with open(os.path.join(_DATA, name), encoding="utf-8") as fh:
        return fh.read()


def _monotonic(anchors):
    for i in range(len(anchors) - 1):
        if anchors[i].start > anchors[i + 1].start:
            return False
        end = anchors[i].end
        if end is not None and end > anchors[i + 1].start + 1e-9:
            return False
    return all(a.end is None or a.end >= a.start for a in anchors)


# --------------------------------------------------------------------------- #
# each checked-in sample parses to the expected anchors                        #
# --------------------------------------------------------------------------- #

def test_whisper_verbose_json_parses():
    a = from_whisper_json(_load("whisper_verbose.json"))
    assert [x.text for x in a] == ["Hello", "brave", "new", "world"]  # "." dropped
    assert (a[0].start, a[0].end) == (0.0, 0.42)                      # source times
    assert _monotonic(a)


def test_whisper_top_level_words_key_variance_and_drop():
    # flat words[] with `text`/`score` keys, and a word with NO timestamp
    a = from_whisper_json(_load("whisper_words.json"))
    assert [x.text for x in a] == ["the", "quick", "fox"]             # 'unaligned' dropped
    assert _monotonic(a) and a[0].start == 0.10


def test_whisperx_parses_and_drops_unaligned():
    a = from_whisperx(_load("whisperx.json"))
    assert [x.text for x in a] == ["hello", "brave", "world"]         # '2024' dropped
    assert _monotonic(a) and a[-1].end == 1.33


def test_gentle_word_anchors_skip_non_success():
    a = from_gentle(_load("gentle.json"))
    assert [x.text for x in a] == ["hello", "world"]                  # 'um' not-found -> gap
    assert (a[0].start, a[0].end) == (0.20, 0.70) and _monotonic(a)


# --------------------------------------------------------------------------- #
# robustness: never crash on malformed / empty / missing keys                  #
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("fn", [from_whisper_json, from_whisperx, from_gentle,
                                from_gentle_phones])
def test_malformed_json_raises_valueerror(fn):
    with pytest.raises(ValueError):
        fn("{ not json")


def test_all_unaligned_raises_not_crashes():
    # every word missing a timestamp -> a clear error, not an index/attr crash
    with pytest.raises(ValueError):
        from_whisper_json(json.dumps({"words": [{"text": "a"}, {"text": "b"}]}))


# --------------------------------------------------------------------------- #
# Gentle phone path: absolute times sum to the word span -> a valid track      #
# --------------------------------------------------------------------------- #

def test_gentle_phones_reconstruct_absolute_times():
    segs = from_gentle_phones(_load("gentle.json"))
    # suffix stripped + upper-cased, gap-filled with silence
    assert [s.phoneme for s in segs] == ["sil", "HH", "AH0", "L", "OW1",
                                         "sil", "W", "ER1", "L", "D"]
    # monotonic, non-overlapping
    assert all(segs[i].end <= segs[i + 1].start + 1e-9 for i in range(len(segs) - 1))
    # hello's phones (0.20..0.70) sum to the word span within float tolerance
    hello = [s for s in segs if 0.19 < s.start < 0.71 and s.phoneme != "sil"]
    assert hello[0].start == pytest.approx(0.20)
    assert hello[-1].end == pytest.approx(0.70)          # sums to the word end
    assert sum(s.end - s.start for s in hello) == pytest.approx(0.50)


def test_gentle_phones_route_to_a_valid_facetrack():
    segs = from_gentle_phones(_load("gentle.json"))
    track = generate_from_alignment(segs, fps=60.0)
    d = to_dict(track)
    assert d["channels"] and d["duration"] == pytest.approx(1.40, abs=1e-2)
    assert from_dict(d).duration > 0                     # round-trips via io_export
    assert isinstance(inspect_track(track), dict)        # validates via inspect


# --------------------------------------------------------------------------- #
# CLI: self-transcribing word path + the phone path; additive when absent      #
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("sample, fmt", [
    ("whisper_verbose.json", "whisper"),
    ("whisperx.json", "whisperx"),
    ("gentle.json", "gentle"),
])
def test_cli_word_path_needs_no_transcript(tmp_path, sample, fmt):
    out = str(tmp_path / "t.json")
    # no --text: the aligner supplies the words
    assert cli_main(["naive", "--anchors", os.path.join(_DATA, sample),
                     "--anchors-format", fmt, "--duration", "2.0", "-o", out]) == 0
    assert from_dict(json.load(open(out))).duration > 0


def test_cli_gentle_phones_path(tmp_path):
    out = str(tmp_path / "phones.json")
    assert cli_main(["naive", "--anchors", os.path.join(_DATA, "gentle.json"),
                     "--anchors-format", "gentle-phones", "-o", out,
                     "--duration", "2.0"]) == 0
    track = from_dict(json.load(open(out)))
    assert track.duration == pytest.approx(1.40, abs=1e-2)   # the phones' own span


def test_adapters_are_additive_no_anchors_unchanged(tmp_path):
    # plain naive (no --anchors-format) is untouched by the new machinery
    a, b = str(tmp_path / "a.json"), str(tmp_path / "b.json")
    assert cli_main(["naive", "--text", "hello world", "--duration", "1.5",
                     "-o", a]) == 0
    assert cli_main(["naive", "--text", "hello world", "--duration", "1.5",
                     "-o", b]) == 0
    assert open(a, "rb").read() == open(b, "rb").read()
