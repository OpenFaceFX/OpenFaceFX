"""Built-in IPA -> Oculus-15 mapping preset (issue #32).

Piper and Cartesia timestamp their phonemes in IPA, and espeak-ng's MBROLA
``.pho`` dumps a SAMPA variant -- neither matches the ARPABET the default
mapping expects, so ``from-timing`` used to relax those sources to silence
unless the user hand-wrote a ``custom_symbols`` mapping. This module ships that
mapping as data: ``IPA_MAPPING`` keys the Oculus-15 targets by the IPA inventory
those engines actually emit, matched through ``_normalize_ipa`` so the
diacritics real dumps carry collapse onto the base symbol instead of
duplicating a table row per variant.

Normalization rules (``_normalize_ipa``), applied to the lookup key:
  * primary/secondary **stress** marks ``ˈ ˌ`` are dropped;
  * **length** marks ``ː ˑ`` are dropped (so ``ɑː`` matches ``ɑ``);
  * the MFA-style secondary-articulation modifier letters ``ʰ ʲ ʷ`` are dropped
    (so ``pʰ tʲ kʷ`` match ``p t k``);
  * every **combining** mark is dropped, which folds the affricate **tie bar**
    (``t͡ʃ`` -> ``tʃ``, matching the plain digraph too), the dental ``t̪`` -> ``t``,
    the syllabic ``n̩`` -> ``n`` and nasalization ``ẽ`` -> ``e``;
  * ASCII ``'`` (stress) and ``:`` (X-SAMPA length) are dropped.
It is idempotent and a no-op on ARPABET (which carries none of these), so the
default pipeline is untouched.

Symbol inventory is grounded in verifiable sources:
  * espeak-ng's phoneme guide -- affricates written as tie bars (t͡ʃ d͡ʒ), stress
    ``ˈ ˌ``, length ``ː ˑ`` (espeak-ng/espeak-ng docs/phonemes.md).
  * the Montreal Forced Aligner US-English phone set, which Cartesia's sonic
    models use verbatim -- the ``aj aw ej ow ɔj`` diphthong spellings and the
    ``pʰ pʲ pʷ tʰ tʲ tʷ kʰ kʷ`` secondary articulations (docs.cartesia.ai,
    "Specify Custom Pronunciations").
  * the English IPA key most G2P/TTS front-ends follow -- the ``aɪ aʊ eɪ oʊ ɔɪ``
    diphthongs and ``ɜ ɝ ɚ`` r-coloured vowels (Wikipedia Help:IPA/English).

The IPA-symbol -> viseme assignment itself is our articulatory synthesis -- the
same many-to-one judgement calls ``visemes.PHONEME_TO_VISEME`` documents for
ARPABET, not a figure lifted from any single source.
"""

from __future__ import annotations

import unicodedata
from typing import Dict, Iterable, List

from .mapping import Mapping, Target, _DEFAULT_CLASSES
from .visemes import VISEMES

# --------------------------------------------------------------------------- #
# Normalization                                                                #
# --------------------------------------------------------------------------- #

# Spacing modifier letters (Unicode category Lm; ``unicodedata.combining`` is 0
# for them) that we strip on lookup. Combining marks -- tie bars, dental,
# syllabic, nasalization -- are not listed: they are removed by the
# ``combining()`` test in ``_normalize_ipa``.
_IPA_STRIP = frozenset(
    "ˈ"  # ˈ  primary stress
    "ˌ"  # ˌ  secondary stress
    "ː"  # ː  long
    "ˑ"  # ˑ  half-long
    "ʰ"  # ʰ  aspirated
    "ʲ"  # ʲ  palatalized
    "ʷ"  # ʷ  labialized
    "'"       # ASCII apostrophe used as a stress mark by some espeak configs
    ":"       # ASCII colon used as a length mark in X-SAMPA
)


def _normalize_ipa(symbol: str) -> str:
    """Reduce a raw IPA/SAMPA token to the base symbol keyed in the table (see
    the module docstring for the rules). Idempotent; a no-op on ARPABET."""
    return "".join(
        ch for ch in symbol
        if ch not in _IPA_STRIP and not unicodedata.combining(ch)
    )


# --------------------------------------------------------------------------- #
# Symbol -> Oculus-15 viseme groupings (articulatory synthesis).               #
# --------------------------------------------------------------------------- #

# Consonants. Both the tie-bar (t͡ʃ) and plain-digraph (tʃ) affricate spellings
# resolve to the same row because _normalize_ipa strips the combining tie bar.
_CONSONANTS: Dict[str, str] = {
    # bilabial stop / nasal -> lips pressed
    "p": "PP", "b": "PP", "m": "PP",
    # labiodental fricative / nasal / approximant -> lower lip to upper teeth
    "f": "FF", "v": "FF", "ʋ": "FF", "ɱ": "FF",
    # dental fricatives -> tongue between teeth
    "θ": "TH", "ð": "TH",
    # alveolar/retroflex stop, lateral, tap -> tongue to alveolar ridge
    "t": "DD", "d": "DD", "l": "DD", "ɫ": "DD", "ɾ": "DD",
    "ʈ": "DD", "ɖ": "DD", "ɭ": "DD", "ʎ": "DD",
    # nasals (alveolar / palatal / velar / retroflex) -> nasal-liquid variant
    "n": "nn", "ɲ": "nn", "ŋ": "nn", "ɳ": "nn",
    # velar/uvular/palatal stop + dorsal & glottal fricative -> back of tongue
    "k": "kk", "ɡ": "kk", "g": "kk", "c": "kk", "ɟ": "kk", "q": "kk", "ɢ": "kk",
    "x": "kk", "ɣ": "kk", "χ": "kk", "ɰ": "kk", "h": "kk", "ɦ": "kk", "ʔ": "kk",
    # post-alveolar / (alveolo-)palatal fricatives + affricates -> rounded,
    # protruded
    "ʃ": "CH", "ʒ": "CH", "tʃ": "CH", "dʒ": "CH", "ʧ": "CH", "ʤ": "CH",
    "ɕ": "CH", "ʑ": "CH", "tɕ": "CH", "dʑ": "CH", "ʨ": "CH", "ʥ": "CH",
    "ç": "CH", "ʝ": "CH",
    # alveolar sibilants + affricates -> narrow, teeth close
    "s": "SS", "z": "SS", "ts": "SS", "dz": "SS", "ʦ": "SS", "ʣ": "SS",
    # rhotics -> retroflex / rounded
    "r": "RR", "ɹ": "RR", "ɻ": "RR", "ʀ": "RR", "ʁ": "RR", "ɽ": "RR",
    # glides -> borrow the nearest vowel mouth (as ARPABET W->U, Y->I)
    "j": "I", "w": "U", "ʍ": "U", "ɥ": "U",
}

# Vowels: monophthongs, diphthongs and r-coloured vowels. Grouped by the ARPABET
# analogue so the viseme choices line up with visemes.PHONEME_TO_VISEME. Every
# key here is also a vowel for coarticulation dominance (``IPA_VOWELS`` below),
# including the r-coloured set that lands on the RR (tongue) viseme -- exactly as
# ARPABET's ER is a vowel that maps to RR.
_VOWELS: Dict[str, str] = {
    # open / low + neutral schwa -> open jaw   (AA/AE/AH -> aa)
    "a": "aa", "ɑ": "aa", "æ": "aa", "ʌ": "aa", "ɐ": "aa", "ə": "aa",
    "ä": "aa", "ɶ": "aa",
    # mid-front spread -> E                    (EH/IH -> E)
    "ɛ": "E", "e": "E", "ɪ": "E", "ø": "E", "œ": "E", "ɘ": "E",
    # close-front wide -> I                    (IY -> I)
    "i": "I", "ɨ": "I",
    # rounded open / back -> O                 (AO/OW -> O)
    "ɔ": "O", "o": "O", "ɒ": "O", "ɤ": "O", "ɵ": "O",
    # close-back rounding -> U                 (UW/UH -> U)
    "u": "U", "ʊ": "U", "y": "U", "ʉ": "U", "ɯ": "U",
    # r-coloured / NURSE -> retroflex          (ER -> RR; still a vowel)
    "ɜ": "RR", "ɝ": "RR", "ɚ": "RR",
    # diphthongs -- both the ɪ/ʊ-offglide (espeak / Wikipedia) and the
    # j/w-offglide (MFA / Cartesia) spellings, grouped by ARPABET analogue
    "aɪ": "aa", "aj": "aa",                    # PRICE  (AY -> aa)
    "aʊ": "O", "aw": "O",                       # MOUTH  (AW -> O)
    "eɪ": "E", "ej": "E",                       # FACE   (EY -> E)
    "oʊ": "O", "ow": "O", "əʊ": "O",            # GOAT   (OW -> O)
    "ɔɪ": "O", "ɔj": "O",                       # CHOICE (OY -> O)
    "ɪə": "E", "eə": "E", "ɛə": "E", "ʊə": "U",  # centring diphthongs
}

# Non-colliding SAMPA fallbacks. MBROLA ``.pho`` voices emit SAMPA, which mostly
# overlaps ASCII/IPA; we add only the few high-frequency symbols that are
# unambiguous AND do not collide with an ARPABET symbol -- so ``is_ipa_vowel``
# stays a no-op on the default path. (SAMPA "V"=ʌ is deliberately omitted: it is
# ARPABET's /v/ consonant.) Per-voice SAMPA beyond this needs an explicit
# --mapping; see docs/timing.md.
_SAMPA_VOWELS: Dict[str, str] = {
    "@": "aa",   # ə  schwa
    "{": "aa",   # æ  TRAP
    "3": "RR",   # ɜ  NURSE (r-coloured; still a vowel)
}

# Structural / silence tokens that relax the mouth (espeak pause "_", MFA "sp"/
# "spn"/"sil", Piper utterance bounds "^"/"$").
_SILENCE_TOKENS = ("sil", "sp", "spn", "_", "^", "$")


# --------------------------------------------------------------------------- #
# Assembled preset                                                             #
# --------------------------------------------------------------------------- #

#: Every IPA/SAMPA token the coarticulation model should treat as a vowel (broad
#: dominance bump), matched after ``_normalize_ipa``. Contains no ARPABET symbol.
IPA_VOWELS = frozenset(_VOWELS) | frozenset(_SAMPA_VOWELS)

_IPA_TO_VISEME: Dict[str, str] = {**_CONSONANTS, **_VOWELS, **_SAMPA_VOWELS}

_IPA_ROWS: Dict[str, Dict[str, float]] = {
    sym: {vis: 1.0} for sym, vis in _IPA_TO_VISEME.items()
}
for _tok in _SILENCE_TOKENS:
    _IPA_ROWS[_tok] = {"sil": 1.0}

#: The built-in IPA -> Oculus-15 preset, auto-selected by ``from-timing`` for the
#: phoneme-unit formats (pho / piper / cartesia) when no --mapping is given.
IPA_MAPPING = Mapping(
    [Target(v, _DEFAULT_CLASSES.get(v, "basic")) for v in VISEMES],
    _IPA_ROWS,
    allow_custom_symbols=True,
    normalize=_normalize_ipa,
)


def is_ipa_vowel(symbol: str) -> bool:
    """True if a raw IPA/SAMPA token is a vowel (monophthong, diphthong or
    r-coloured), consulted by the coarticulation dominance model so vendor
    vowels get the broad vowel bump. Normalizes first (``ˈaɪ``, ``ɑː`` ->
    ``aɪ``, ``ɑ``). Returns False for every ARPABET symbol, so the ARPABET path
    is byte-for-byte unchanged."""
    return _normalize_ipa(symbol) in IPA_VOWELS


def ipa_unknown_symbols(symbols: Iterable[str]) -> List[str]:
    """QA warnings for phoneme symbols the preset can't place -- they route to
    silence -- one line per distinct symbol (sorted), mirroring the vendor
    viseme path. A lone suprasegmental (a bare stress/length mark that
    normalizes to empty) is a structural token, not an unknown, so it never
    warns."""
    counts: Dict[str, int] = {}
    for s in symbols:
        norm = _normalize_ipa(s)
        if norm and norm not in _IPA_ROWS:
            counts[s] = counts.get(s, 0) + 1
    return [f"unknown IPA symbol {s!r} ({n}x) routed to silence"
            for s, n in sorted(counts.items())]
