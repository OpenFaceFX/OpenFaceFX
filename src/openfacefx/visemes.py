"""Viseme inventory and phoneme -> viseme mapping.

A *viseme* is the visual mouth shape corresponding to one or more phonemes.
Many phonemes are visually indistinguishable (e.g. /p/, /b/, /m/ are all a
lip closure), so the mapping is many-to-one.

We ship the 15-target set popularised by the Oculus/Meta LipSync SDK because it
is a widely adopted, well-documented, IP-free convention that most character
rigs already provide blendshapes for. Each viseme name below is a blendshape
your rig is expected to expose.
"""

from __future__ import annotations

from .phonemes import strip_stress, SILENCE

# Ordered so indices are stable across exports.
VISEMES = [
    "sil",   # neutral / silence
    "PP",    # p, b, m  -> lips pressed
    "FF",    # f, v     -> lower lip to upper teeth
    "TH",    # th, dh   -> tongue between teeth
    "DD",    # t, d, n, l -> tongue to alveolar ridge
    "kk",    # k, g, ng, hh -> back of tongue raised
    "CH",    # ch, jh, sh, zh -> rounded, protruded
    "SS",    # s, z     -> narrow, teeth close
    "nn",    # (reserved nasal/liquid variant) n, ng
    "RR",    # r, er    -> retroflex / rounded
    "aa",    # AA, AE, AH, AY -> open jaw
    "E",     # EH, EY, IH -> mid-front spread
    "I",     # IY        -> wide spread
    "O",     # AO, OW, OY, AW -> rounded open
    "U",     # UW, UH, W -> tight rounding
]

VISEME_INDEX = {name: i for i, name in enumerate(VISEMES)}

# ARPAbet (stress-less) -> viseme. This is the heart of the mapping and is
# based on standard articulatory phonetics groupings.
PHONEME_TO_VISEME = {
    # bilabial stops / nasal -> lip press
    "P": "PP", "B": "PP", "M": "PP",
    # labiodental fricatives
    "F": "FF", "V": "FF",
    # dental fricatives
    "TH": "TH", "DH": "TH",
    # alveolar stops / nasal / lateral
    "T": "DD", "D": "DD", "L": "DD",
    "N": "nn", "NG": "nn",
    # velars + glottal
    "K": "kk", "G": "kk", "HH": "kk",
    # post-alveolar affricates / fricatives (rounded, protruded)
    "CH": "CH", "JH": "CH", "SH": "CH", "ZH": "CH",
    # alveolar sibilants
    "S": "SS", "Z": "SS",
    # rhotics
    "R": "RR", "ER": "RR",
    # glides
    "W": "U", "Y": "I",
    # vowels
    "AA": "aa", "AE": "aa", "AH": "aa", "AY": "aa",
    "EH": "E", "EY": "E", "IH": "E",
    "IY": "I",
    "AO": "O", "OW": "O", "OY": "O", "AW": "O",
    "UW": "U", "UH": "U",
    SILENCE: "sil",
}


def phoneme_to_viseme(phoneme: str) -> str:
    """Map a (possibly stressed) ARPAbet phoneme to a viseme name."""
    p = strip_stress(phoneme).upper() if phoneme != SILENCE else SILENCE
    return PHONEME_TO_VISEME.get(p, "sil")


def viseme_index(name: str) -> int:
    return VISEME_INDEX[name]
