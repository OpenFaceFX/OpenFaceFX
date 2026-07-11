"""Multi-language pronunciation framework (issue #8).

Pins the acceptance: the G2P seam is a protocol with the current class as the
English implementation and its default path **byte-identical**; every one of the
39 phonemes has IPA + SAMPA aliases that round-trip; an IPA-alphabet toy-language
dictionary drives a valid track end-to-end; a pronouncer hook overrides the
dictionary with correct prev/next context; and a custom tokenizer keeps non-Latin
tokens while the English tokenizer is unchanged.
"""

import os
import sys

import pytest

try:
    import openfacefx  # noqa: F401
except ImportError:
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from openfacefx.g2p import G2P
from openfacefx.phonemes import (ARPABET, IPA_ALIASES, SAMPA_ALIASES, from_ipa,
                                 from_sampa, to_ipa, to_sampa)
from openfacefx.pipeline import naive_segments
from openfacefx.pronounce import Pronouncer, read_dictionary
from openfacefx.visemes import phoneme_to_viseme


def _dict_file(tmp_path, text, name="toy.dict"):
    p = tmp_path / name
    p.write_text(text, encoding="utf-8")
    return str(p)


# --------------------------------------------------------------------------- #
# the overriding invariant: the default English path is byte-identical         #
# --------------------------------------------------------------------------- #

def test_default_english_path_is_byte_identical():
    g = G2P()
    # the seam is unchanged for the default (no dict, no pronouncer, no tokenizer)
    assert g.phrase("hello world") == ["HH", "AH0", "L", "OW1", "W", "ER1", "L", "D"]
    assert g.tokenize("hello, world's end!") == ["hello", "world's", "end"]
    assert g.oov_words("hello xyzzy world") == ["xyzzy"]
    # a fresh default and an explicit-null-hooks instance resolve identically
    plain = [(s.phoneme, s.start) for s in naive_segments("the quick brown fox", 2.0)]
    guard = [(s.phoneme, s.start) for s in
             naive_segments("the quick brown fox", 2.0,
                            g2p=G2P(pronouncer=None, tokenizer=None))]
    assert plain == guard


def test_g2p_conforms_to_the_pronouncer_protocol():
    assert isinstance(G2P(), Pronouncer)                  # tokenize + word + phrase


# --------------------------------------------------------------------------- #
# IPA + SAMPA aliases for all 39, round-trippable                              #
# --------------------------------------------------------------------------- #

def test_every_phoneme_has_ipa_and_sampa_aliases():
    assert set(IPA_ALIASES) == ARPABET == set(SAMPA_ALIASES)   # all 39, exactly
    assert len(ARPABET) == 39


@pytest.mark.parametrize("ph", sorted(ARPABET))
def test_ipa_and_sampa_round_trip(ph):
    assert from_ipa(to_ipa(ph)) == ph                     # internal -> IPA -> internal
    assert from_sampa(to_sampa(ph)) == ph                 # internal -> SAMPA -> internal


def test_aliases_are_bijections():
    # distinct aliases (so the reverse maps are unambiguous)
    assert len(set(IPA_ALIASES.values())) == len(IPA_ALIASES)
    assert len(set(SAMPA_ALIASES.values())) == len(SAMPA_ALIASES)


# --------------------------------------------------------------------------- #
# toy IPA-alphabet dictionary -> a valid track end-to-end                      #
# --------------------------------------------------------------------------- #

def test_toy_ipa_dictionary_drives_a_valid_track(tmp_path):
    path = _dict_file(tmp_path,
                      ";;; locale = xx-Toy\n;;; alphabet = ipa\n"
                      "ka  k ɑ\nki  k i\nku  k u\nmo  m oʊ\n")
    d = read_dictionary(path)
    assert d.locale == "xx-Toy" and d.alphabet == "ipa"
    assert d.entries["ka"] == ["K", "AA"] and d.entries["mo"] == ["M", "OW"]

    g = G2P()
    assert g.load_dictionary(path) == 4
    assert g.phrase("ka ki mo") == ["K", "AA", "K", "IY", "M", "OW"]
    # end-to-end via the naive path: a real, non-silent track
    from openfacefx.coarticulation import build_viseme_curves
    segs = naive_segments("ka ki ku mo", 2.0, g2p=g)
    _, matrix = build_viseme_curves(segs, fps=60.0)
    assert matrix.shape[0] > 0 and matrix[:, 1:].max() > 0.1   # visemes fire


def test_read_dictionary_maps_each_alphabet(tmp_path):
    ipa = read_dictionary(_dict_file(tmp_path, ";;; alphabet = ipa\nx  ʃ ɑ\n", "a"))
    sampa = read_dictionary(_dict_file(tmp_path, ";;; alphabet = sampa\nx  S A\n", "b"))
    arpa = read_dictionary(_dict_file(tmp_path, ";;; alphabet = arpabet\nx  SH AA\n", "c"))
    assert ipa.entries["x"] == sampa.entries["x"] == arpa.entries["x"] == ["SH", "AA"]
    with pytest.raises(ValueError):
        read_dictionary(_dict_file(tmp_path, ";;; alphabet = klingon\nx  q\n", "d"))


def test_out_of_inventory_phoneme_falls_to_sil(tmp_path):
    # ɴ (uvular nasal) is outside the 39; it passes through and is silent
    path = _dict_file(tmp_path, ";;; alphabet = ipa\nfoo  ɴ ɑ\n")
    entries = read_dictionary(path).entries
    assert entries["foo"] == ["ɴ", "AA"]                  # unmapped symbol kept
    assert phoneme_to_viseme("ɴ") == "sil"                # -> silent at viseme stage


# --------------------------------------------------------------------------- #
# pronouncer hook: overrides the dictionary, receives prev/next context        #
# --------------------------------------------------------------------------- #

def test_pronouncer_overrides_with_correct_context():
    seen = []

    def hook(word, prev, nxt):
        seen.append((word, prev, nxt))
        if word == "read":                                # past vs present by context
            return ["R", "EH", "D"] if prev == "i" else ["R", "IY", "D"]
        return None                                       # defer to the rules

    g = G2P(pronouncer=hook)
    out = g.phrase("i read a book")
    assert ["R", "EH", "D"] == out[out.index("R"):out.index("R") + 3]   # override won
    assert ("read", "i", "a") in seen                     # correct prev/next context
    # a word the hook defers on still falls through to the rule fallback
    assert "book" not in [w for w, _, _ in seen] or out[-3:] == g.word("book")


def test_pronouncer_consulted_after_dictionary_not_before():
    # a dictionary word is resolved by the dict; the hook is only asked for misses
    def hook(word, prev, nxt):
        return ["Z", "Z", "Z"]                            # would clobber everything

    g = G2P(pronouncer=hook)
    assert g.phrase("hello") == ["HH", "AH0", "L", "OW1"]   # dict wins over the hook


# --------------------------------------------------------------------------- #
# pluggable tokenizer: non-Latin survives; default English unchanged           #
# --------------------------------------------------------------------------- #

def test_custom_tokenizer_keeps_non_latin_tokens():
    g = G2P(tokenizer=lambda text: text.split())
    tokens = g.tokenize("こんにちは 世界 test")
    assert tokens == ["こんにちは", "世界", "test"]        # nothing dropped
    # the default English tokenizer is unchanged (drops non-Latin, keeps [A-Za-z'])
    assert G2P().tokenize("こんにちは hello world's") == ["hello", "world's"]
