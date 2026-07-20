"""Vosk (offline Kaldi ASR) word-timestamp adapter (anchors.from_vosk, #70).

Fills the one gap in the offline lightweight-ASR adapters (Whisper/WhisperX/
Gentle/Allosaurus already ship). Parses Vosk's SetWords(True) output —
{"result": [{word,start,end,conf}], "text": ...}, or a list of streaming chunks —
into Anchors, with an optional per-word confidence gate. Vosk is Apache-2.0, so
parsing carries no GPL contamination; recognition runs externally.
"""

import json
import os
import sys

import pytest

try:
    import openfacefx  # noqa: F401
except ImportError:
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from openfacefx.anchors import from_vosk
from openfacefx.cli import main as cli_main
from openfacefx.io_export import from_dict

VOSK = json.dumps({"result": [
    {"conf": 1.0, "start": 0.36, "end": 0.66, "word": "hello"},
    {"conf": 0.4, "start": 0.72, "end": 1.02, "word": "world"},
], "text": "hello world"})


def test_parses_words():
    a = from_vosk(VOSK)
    assert [(x.text, x.start, x.end) for x in a] == \
        [("hello", 0.36, 0.66), ("world", 0.72, 1.02)]


def test_min_conf_gate_drops_low_confidence():
    assert [x.text for x in from_vosk(VOSK, min_conf=0.5)] == ["hello"]  # world 0.4 dropped


def test_streaming_chunks_concatenated_partials_skipped():
    chunks = json.dumps([
        {"result": [{"conf": 1.0, "start": 0.0, "end": 0.3, "word": "one"}], "text": "one"},
        {"partial": "tw"},                                  # partial -> skipped
        {"result": [{"conf": 1.0, "start": 0.4, "end": 0.7, "word": "two"}], "text": "two"},
    ])
    assert [x.text for x in from_vosk(chunks)] == ["one", "two"]


def test_conf_absent_word_kept_regardless():
    j = json.dumps({"result": [{"start": 0.0, "end": 0.3, "word": "a"}]})
    assert [x.text for x in from_vosk(j, min_conf=0.9)] == ["a"]


@pytest.mark.parametrize("doc,msg", [
    ({"result": [{"word": "x", "end": 1.0}]}, "'start'"),
    ({"result": [{"start": 0.0, "end": 1.0}]}, "'word'"),
    ({"result": "nope"}, "must be a list"),
    ({"result": []}, "no word results"),
])
def test_rejects_malformed(doc, msg):
    with pytest.raises(ValueError, match=msg):
        from_vosk(json.dumps(doc))


def test_all_dropped_by_conf_errors():
    with pytest.raises(ValueError, match="no word results"):
        from_vosk(VOSK, min_conf=2.0)


def test_cli_vosk_self_transcribing(tmp_path):
    f = str(tmp_path / "v.json")
    open(f, "w").write(VOSK)
    out = str(tmp_path / "t.json")
    # self-transcribing: no --text needed (words carry the transcript)
    assert cli_main(["naive", "--anchors", f, "--anchors-format", "vosk",
                     "--duration", "1.5", "-o", out]) == 0
    assert from_dict(json.load(open(out))).channels


def test_cli_vosk_min_conf_flag(tmp_path):
    f = str(tmp_path / "v.json")
    open(f, "w").write(VOSK)
    out = str(tmp_path / "t.json")
    assert cli_main(["naive", "--anchors", f, "--anchors-format", "vosk",
                     "--vosk-min-conf", "0.5", "--duration", "1.5", "-o", out]) == 0
    assert from_dict(json.load(open(out))).channels     # built from just "hello"
