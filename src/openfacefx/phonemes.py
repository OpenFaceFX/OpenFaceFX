"""ARPAbet phoneme set.

We use the CMU/ARPAbet inventory because it is what the CMU Pronouncing
Dictionary, Montreal Forced Aligner, and most English G2P tools emit. Stress
digits (0/1/2) on vowels are stripped before mapping to visemes.
"""

from __future__ import annotations

# The 39-phoneme ARPAbet inventory (stress-less form).
ARPABET_VOWELS = {
    "AA", "AE", "AH", "AO", "AW", "AY", "EH", "ER",
    "EY", "IH", "IY", "OW", "OY", "UH", "UW",
}

ARPABET_CONSONANTS = {
    "B", "CH", "D", "DH", "F", "G", "HH", "JH", "K", "L", "M", "N",
    "NG", "P", "R", "S", "SH", "T", "TH", "V", "W", "Y", "Z", "ZH",
}

ARPABET = ARPABET_VOWELS | ARPABET_CONSONANTS

# A special phoneme representing closure / silence between words and at the
# start and end of an utterance. The mouth relaxes toward neutral here.
SILENCE = "sil"


def strip_stress(phoneme: str) -> str:
    """Remove the trailing stress digit ARPAbet attaches to vowels (e.g. AH0)."""
    if phoneme and phoneme[-1].isdigit():
        return phoneme[:-1]
    return phoneme


def is_vowel(phoneme: str) -> bool:
    return strip_stress(phoneme) in ARPABET_VOWELS


# --------------------------------------------------------------------------- #
# IPA / X-SAMPA aliases (issue #8): the internal ARPAbet inventory expressed in  #
# the two cross-language notations, for the multi-language dictionary loader and #
# for display. The maps are bijections, so internal -> alias -> internal round-  #
# trips exactly. Every one of the 39 ARPAbet phonemes has an entry.              #
# --------------------------------------------------------------------------- #

#: internal ARPAbet (stress-less) -> IPA.
IPA_ALIASES = {
    "AA": "ɑ", "AE": "æ", "AH": "ʌ", "AO": "ɔ", "AW": "aʊ", "AY": "aɪ",
    "EH": "ɛ", "ER": "ɝ", "EY": "eɪ", "IH": "ɪ", "IY": "i", "OW": "oʊ",
    "OY": "ɔɪ", "UH": "ʊ", "UW": "u",
    "B": "b", "CH": "tʃ", "D": "d", "DH": "ð", "F": "f", "G": "ɡ", "HH": "h",
    "JH": "dʒ", "K": "k", "L": "l", "M": "m", "N": "n", "NG": "ŋ", "P": "p",
    "R": "ɹ", "S": "s", "SH": "ʃ", "T": "t", "TH": "θ", "V": "v", "W": "w",
    "Y": "j", "Z": "z", "ZH": "ʒ",
}

#: internal ARPAbet (stress-less) -> X-SAMPA (case-significant).
SAMPA_ALIASES = {
    "AA": "A", "AE": "{", "AH": "V", "AO": "O", "AW": "aU", "AY": "aI",
    "EH": "E", "ER": "3`", "EY": "eI", "IH": "I", "IY": "i", "OW": "oU",
    "OY": "OI", "UH": "U", "UW": "u",
    "B": "b", "CH": "tS", "D": "d", "DH": "D", "F": "f", "G": "g", "HH": "h",
    "JH": "dZ", "K": "k", "L": "l", "M": "m", "N": "n", "NG": "N", "P": "p",
    "R": "r\\", "S": "s", "SH": "S", "T": "t", "TH": "T", "V": "v", "W": "w",
    "Y": "j", "Z": "z", "ZH": "Z",
}

_IPA_TO_INTERNAL = {v: k for k, v in IPA_ALIASES.items()}
_SAMPA_TO_INTERNAL = {v: k for k, v in SAMPA_ALIASES.items()}

#: The phoneme alphabets a dictionary may declare (issue #8).
ALPHABETS = ("arpabet", "ipa", "sampa")


def to_ipa(phoneme: str) -> str:
    """Internal phoneme -> IPA (stress digit dropped); unknown symbols pass
    through unchanged so a mixed transcript never crashes."""
    return IPA_ALIASES.get(strip_stress(phoneme), phoneme)


def to_sampa(phoneme: str) -> str:
    """Internal phoneme -> X-SAMPA (stress digit dropped); unknown pass through."""
    return SAMPA_ALIASES.get(strip_stress(phoneme), phoneme)


def from_ipa(symbol: str) -> str:
    """IPA -> internal ARPAbet; an unrecognised symbol passes through (and will
    fall to ``sil`` at the viseme stage, the documented behaviour)."""
    return _IPA_TO_INTERNAL.get(symbol, symbol)


def from_sampa(symbol: str) -> str:
    """X-SAMPA -> internal ARPAbet; an unrecognised symbol passes through."""
    return _SAMPA_TO_INTERNAL.get(symbol, symbol)


def from_alphabet(symbol: str, alphabet: str) -> str:
    """Map a phoneme ``symbol`` written in ``alphabet`` (``arpabet`` | ``ipa`` |
    ``sampa``) into the internal inventory. ``arpabet`` is the identity."""
    if alphabet == "ipa":
        return from_ipa(symbol)
    if alphabet == "sampa":
        return from_sampa(symbol)
    if alphabet == "arpabet":
        return symbol
    raise ValueError(f"unknown phoneme alphabet {alphabet!r} (use {ALPHABETS})")
