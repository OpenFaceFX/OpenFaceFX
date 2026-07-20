"""Reviewable OOV pronunciation-dictionary emit (g2p.emit_oov_dict, #66).

We already detect OOV words and QA says "add to a pronunciation dict"; this turns
that list into an editable CMUdict of rule-G2P guesses. Proof: the emitted dict is
a well-formed CMUdict (header + WORD  P1 P2 lines) that, loaded back with
load_cmudict, resolves exactly the words that were OOV — the validate->g2p loop,
closed. Plus known-word exclusion, determinism, and the CLI verb.
"""

import os
import sys

import pytest

try:
    import openfacefx  # noqa: F401
except ImportError:
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from openfacefx.cli import main as cli_main
from openfacefx.g2p import G2P

OOV_TEXT = "the zorptlan kwyjibo grommish"          # 3 nonsense OOV words, "the" known


def _entries(dict_text):
    return [ln for ln in dict_text.splitlines() if ln and not ln.startswith(";")]


def test_round_trip_resolves_every_oov(tmp_path):
    g = G2P()
    assert set(g.oov_words(OOV_TEXT)) == {"zorptlan", "kwyjibo", "grommish"}
    path = tmp_path / "oov.dict"
    path.write_text(g.emit_oov_dict(OOV_TEXT), encoding="utf-8")

    g2 = G2P()
    added = g2.load_cmudict(str(path))
    assert added == 3
    assert g2.oov_words(OOV_TEXT) == []                 # all now resolve


def test_dict_line_format():
    g = G2P()
    out = g.emit_oov_dict("xylophrium")
    assert out.splitlines()[0].startswith(";;;")        # review header
    entries = _entries(out)
    assert entries == ["XYLOPHRIUM  " + " ".join(g.word("xylophrium"))]
    word, sep, phones = entries[0].partition("  ")      # two-space CMUdict sep
    assert word.isupper() and phones and all(p.strip() for p in phones.split())


def test_known_words_are_not_emitted():
    # every word is in the built-in dict -> no OOV entries, header only
    out = G2P().emit_oov_dict("hello world the dog")
    assert _entries(out) == []


def test_preloaded_dict_excludes_defined_words(tmp_path):
    pre = tmp_path / "pre.dict"
    pre.write_text("ZORPTLAN  Z AO1 R P T L AH0 N\n", encoding="utf-8")
    g = G2P()
    g.load_cmudict(str(pre))
    entries = _entries(g.emit_oov_dict("zorptlan kwyjibo"))
    assert entries == ["KWYJIBO  " + " ".join(g.word("kwyjibo"))]


def test_deterministic_and_sorted():
    a = G2P().emit_oov_dict("gamma alpha beta")
    b = G2P().emit_oov_dict("alpha beta gamma")
    assert a == b                                       # order-independent, sorted
    assert _entries(a) == ["ALPHA  " + " ".join(G2P().word("alpha")),
                           "BETA  " + " ".join(G2P().word("beta")),
                           "GAMMA  " + " ".join(G2P().word("gamma"))]


def test_cli_emit_then_resolves(tmp_path):
    out = tmp_path / "o.dict"
    assert cli_main(["emit-oov-dict", "--text", "zorptlan kwyjibo",
                     "-o", str(out)]) == 0
    g = G2P()
    assert g.load_cmudict(str(out)) == 2
    # the emitted dict resolves those words in a real run (no OOV warning path)
    track = tmp_path / "t.json"
    assert cli_main(["naive", "--text", "zorptlan kwyjibo", "--duration", "1.0",
                     "--cmudict", str(out), "-o", str(track)]) == 0


def test_cli_transcript_file(tmp_path):
    script = tmp_path / "script.txt"
    script.write_text("the flibbertigibbet capered", encoding="utf-8")
    out = tmp_path / "o.dict"
    assert cli_main(["emit-oov-dict", "--transcript", str(script),
                     "-o", str(out)]) == 0
    assert "FLIBBERTIGIBBET" in out.read_text(encoding="utf-8")


def test_cli_requires_exactly_one_text_source(tmp_path):
    with pytest.raises(SystemExit, match="exactly one"):
        cli_main(["emit-oov-dict", "-o", str(tmp_path / "x.dict")])
    with pytest.raises(SystemExit, match="exactly one"):
        cli_main(["emit-oov-dict", "--text", "a", "--transcript", "f.txt",
                  "-o", str(tmp_path / "x.dict")])
