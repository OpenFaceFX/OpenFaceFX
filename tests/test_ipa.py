"""Built-in IPA -> Oculus-15 preset (issue #32).

Covers the normalization rules (stress / length / affricate tie bar / MFA
secondary-articulation marks), that a realistic espeak / Piper / Cartesia symbol
inventory produces non-silence visemes for every phone, that ``from-timing``
auto-selects the preset for the phoneme-unit formats while an explicit --mapping
still wins, that IPA vowels get the broad coarticulation dominance, and that the
ARPABET default path is byte-for-byte unchanged.
"""

import json
import os
import sys

import pytest

try:
    import openfacefx  # noqa: F401
except ImportError:
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from openfacefx.cli import main as cli_main
from openfacefx.visemes import VISEMES
from openfacefx.mapping import Mapping
from openfacefx.phonemes import ARPABET, SILENCE
from openfacefx.alignment import PhonemeSegment
from openfacefx.coarticulation import _alpha, _seg_is_vowel
from openfacefx.ipa import (
    IPA_MAPPING, IPA_VOWELS, _IPA_ROWS, _normalize_ipa, is_ipa_vowel,
    ipa_unknown_symbols,
)


def _viseme(sym):
    """Highest-weight target name the preset routes ``sym`` to."""
    row = IPA_MAPPING.row(sym)
    if not row:
        return None
    return IPA_MAPPING.targets[max(row, key=row.get)].name


# --------------------------------------------------------------------------- #
# Normalization rules                                                          #
# --------------------------------------------------------------------------- #

def test_normalize_strips_stress_length_tiebar_and_modifiers():
    # stress ˈ ˌ (and ASCII '), length ː ˑ (and ASCII :)
    assert _normalize_ipa("ˈɑ") == "ɑ"
    assert _normalize_ipa("ˌe") == "e"
    assert _normalize_ipa("'a") == "a"
    assert _normalize_ipa("ɑː") == "ɑ"          # long
    assert _normalize_ipa("eˑ") == "e"          # half-long
    assert _normalize_ipa("i:") == "i"          # X-SAMPA length
    # affricate tie bar (both U+0361 over and U+035C under) -> plain digraph
    assert _normalize_ipa("t͡ʃ") == "tʃ"
    assert _normalize_ipa("d͡ʒ") == "dʒ"
    assert _normalize_ipa("t͜s") == "ts"
    # MFA secondary-articulation modifier letters
    assert _normalize_ipa("pʰ") == "p" and _normalize_ipa("tʲ") == "t"
    assert _normalize_ipa("kʷ") == "k"
    # combining diacritics: dental, syllabic, nasalization (as emitted: a base
    # letter plus a combining mark, not a precomposed codepoint)
    assert _normalize_ipa("t̪") == "t" and _normalize_ipa("n̩") == "n"
    assert _normalize_ipa("ẽ") == "e"        # e + combining tilde
    # a lone suprasegmental normalizes to empty
    assert _normalize_ipa("ˈ") == "" and _normalize_ipa("ː") == ""


def test_normalize_is_idempotent_and_noop_on_arpabet():
    for raw in ("ˈɑː", "t͡ʃ", "pʰ", "n̩", "ɜː", "aɪ"):
        assert _normalize_ipa(_normalize_ipa(raw)) == _normalize_ipa(raw)
    # ARPABET carries none of the stripped marks, so it passes through verbatim
    for sym in list(ARPABET) + [s + d for s in ARPABET for d in "012"] + [SILENCE]:
        assert _normalize_ipa(sym) == sym


def test_tiebar_and_digraph_affricates_hit_the_same_row():
    assert _viseme("t͡ʃ") == _viseme("tʃ") == "CH"
    assert _viseme("d͡ʒ") == _viseme("dʒ") == "CH"
    # length/stress variants resolve to the base row
    assert IPA_MAPPING.row("ˈɜː") == IPA_MAPPING.row("ɜ")
    assert IPA_MAPPING.row("ɑː") == IPA_MAPPING.row("ɑ")


# --------------------------------------------------------------------------- #
# Coverage: a realistic vendor inventory produces non-silence visemes          #
# --------------------------------------------------------------------------- #

# English IPA as Piper / espeak-ng / Cartesia(MFA) emit it -- diacritics and
# all. Every token here must resolve to a real (non-silence) viseme. Sources are
# documented in openfacefx/ipa.py.
REALISTIC_IPA = [
    # consonants (espeak / Wikipedia spelling)
    "p", "b", "t", "d", "k", "ɡ", "m", "n", "ŋ", "f", "v", "θ", "ð",
    "s", "z", "ʃ", "ʒ", "h", "tʃ", "dʒ", "l", "ɹ", "j", "w",
    # monophthongs + r-coloured vowels, with length marks
    "iː", "ɪ", "ɛ", "æ", "ɑː", "ɒ", "ɔː", "ʊ", "uː", "ʌ", "ə", "ɜː", "ɝ", "ɚ",
    # diphthongs -- ɪ/ʊ-offglide spelling
    "eɪ", "aɪ", "ɔɪ", "oʊ", "əʊ", "aʊ", "ɪə", "eə", "ʊə",
    # stressed / tie-bar forms
    "ˈɑː", "ˌɛ", "t͡ʃ", "d͡ʒ",
    # MFA / Cartesia spelling: j/w offglides + aspiration/palatalization/labial.
    "aj", "aw", "ej", "ow", "ɔj",
    "pʰ", "tʰ", "kʰ", "pʲ", "tʲ", "kʷ", "tʷ", "bʲ", "dʲ", "fʲ", "vʲ", "mʲ",
    "t̪", "d̪", "n̩", "m̩", "ɫ", "ɫ̩", "ɾ", "c", "ɟ", "ç", "ɱ", "ɲ", "ɐ",
    # non-colliding SAMPA fallbacks
    "@", "{", "3",
]


def test_realistic_inventory_all_map_to_non_silence():
    silent = [s for s in REALISTIC_IPA if _viseme(s) in (None, "sil")]
    assert silent == [], f"routed to silence: {silent}"


def test_preset_targets_are_the_oculus_visemes():
    assert IPA_MAPPING.allow_custom_symbols
    assert IPA_MAPPING.target_names == list(VISEMES)
    # every row lands on a declared target
    for sym, row in _IPA_ROWS.items():
        assert set(row) <= set(VISEMES), sym


# --------------------------------------------------------------------------- #
# CLI: auto-select for phoneme-unit formats; explicit --mapping wins            #
# --------------------------------------------------------------------------- #

def _assert_track(path):
    with open(path, encoding="utf-8") as fh:
        d = json.load(fh)
    assert d["format"] == "openfacefx.track" and d["duration"] > 0 and d["channels"]
    for c in d["channels"]:
        assert all(0.0 <= v <= 1.0 for _, v in c["keys"]), c["name"]
        assert c["name"] in VISEMES
    return d


PIPER = json.dumps({"phonemes": ["h", "ə", "l", "oʊ"],
                    "phoneme_id_samples": [2205, 4410, 2205, 6615]})
CARTESIA = json.dumps({"phoneme_timestamps": {
    "phonemes": ["h", "ə", "l", "oʊ"],
    "start": [0.093, 0.174, 0.255, 0.337],
    "end": [0.174, 0.255, 0.337, 0.418]}})


def _run(tmp_path, name, text, fmt, extra=()):
    src = tmp_path / name
    src.write_text(text, encoding="utf-8")
    out = str(tmp_path / (fmt + ".json"))
    assert cli_main(["from-timing", "--file", str(src), "--format", fmt,
                     "-o", out, *extra]) == 0
    return out


def test_cartesia_autoselects_ipa_without_mapping(tmp_path):
    # h->kk, ə->aa, l->DD, oʊ->O -- rich visemes, no --mapping flag (issue #32)
    d = _assert_track(_run(tmp_path, "c.json", CARTESIA, "cartesia"))
    assert {"kk", "aa", "DD", "O"} <= {c["name"] for c in d["channels"]}


def test_piper_autoselects_ipa_without_mapping(tmp_path):
    d = _assert_track(_run(tmp_path, "p.json", PIPER, "piper",
                           ("--sample-rate", "22050")))
    assert {"kk", "aa", "DD", "O"} <= {c["name"] for c in d["channels"]}


def test_explicit_mapping_overrides_autoselected_ipa(tmp_path):
    # A custom_symbols mapping that sends every fixture phone to E only: if the
    # explicit --mapping wins, we get E and none of the IPA preset's kk/DD/O.
    mp = tmp_path / "force.json"
    mp.write_text(json.dumps({
        "format": "openfacefx.mapping", "version": 1, "custom_symbols": True,
        "targets": [{"name": "E", "class": "jaw"}, {"name": "sil"}],
        "phonemes": {"h": {"E": 1.0}, "ə": {"E": 1.0}, "l": {"E": 1.0},
                     "oʊ": {"E": 1.0}, "sil": {"sil": 1.0}},
    }), encoding="utf-8")
    d = _assert_track(_run(tmp_path, "c.json", CARTESIA, "cartesia",
                           ("--mapping", str(mp))))
    names = {c["name"] for c in d["channels"]}
    assert "E" in names
    assert not ({"kk", "DD", "O"} & names)      # IPA preset was not used


def test_unknown_symbol_warns_once_per_distinct_symbol(tmp_path, capsys):
    # ✳ is not a phoneme; a lone stress mark ˈ is structural (must not warn)
    cart = json.dumps({"phoneme_timestamps": {
        "phonemes": ["p", "✳", "ˈ", "✳", "a"],
        "start": [0.0, 0.1, 0.2, 0.3, 0.4], "end": [0.1, 0.2, 0.3, 0.4, 0.5]}})
    _run(tmp_path, "c.json", cart, "cartesia")
    warns = [l for l in capsys.readouterr().out.splitlines() if "warning:" in l]
    assert len(warns) == 1 and "✳" in warns[0] and "2x" in warns[0]


def test_ipa_unknown_symbols_helper():
    out = ipa_unknown_symbols(["p", "a", "✳", "✳", "ˈ", "ː", "oʊ"])
    assert out == ["unknown IPA symbol '✳' (2x) routed to silence"]
    assert ipa_unknown_symbols(["ˈ", "ː", "_", "sil"]) == []   # structural, known


# --------------------------------------------------------------------------- #
# Vowel/consonant classification drives coarticulation dominance               #
# --------------------------------------------------------------------------- #

def test_ipa_vowel_gets_vowel_dominance():
    # IPA vowels take the broad vowel alpha (1.0); consonants stay sharp (0.85)
    for v in ("ɑ", "ə", "aɪ", "oʊ", "@"):
        assert _alpha(PhonemeSegment(v, 0.0, 0.2)) == 1.0, v
    # an r-coloured vowel is a vowel for dominance even though it maps to RR
    assert _seg_is_vowel(PhonemeSegment("ɜː", 0.0, 0.2))
    assert _alpha(PhonemeSegment("ɜː", 0.0, 0.2)) == 1.0
    for c in ("s", "p", "tʃ", "l"):
        assert _alpha(PhonemeSegment(c, 0.0, 0.2)) == 0.85, c


def test_arpabet_classification_is_unchanged():
    # byte-identity guard: no ARPABET symbol (stress digits included) is ever an
    # IPA vowel, so the `is_vowel or is_ipa_vowel` seam can't shift ARPABET.
    for sym in list(ARPABET) + [s + d for s in ARPABET for d in "012"]:
        assert not is_ipa_vowel(sym), sym
    # ARPABET's /v/ consonant stays a consonant (SAMPA "V"=ʌ was left out for it)
    assert _alpha(PhonemeSegment("V", 0.0, 0.2)) == 0.85
    assert IPA_VOWELS.isdisjoint(ARPABET)


def test_default_mapping_row_still_verbatim_without_normalize():
    # the normalize hook is opt-in: a plain custom_symbols Mapping matches
    # verbatim, so case-significant vendor symbols are untouched (issue #14)
    from openfacefx.mapping import Target
    plain = Mapping([Target("SS"), Target("CH")],
                    {"s": {"SS": 1.0}, "S": {"CH": 1.0}}, allow_custom_symbols=True)
    assert plain.normalize is None
    assert plain.row("s") == {0: 1.0} and plain.row("S") == {1: 1.0}
