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
