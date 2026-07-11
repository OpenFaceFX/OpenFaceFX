"""Multi-language pronunciation framework (issue #8).

FaceFX ships an *Additional Language Framework*: drop-in dictionaries that declare
a phonetic alphabet (arpabet / ipa / sampa) and a locale, rule-based *pronouncers*
that receive previous/current/next word context, and per-language tokenizers so
non-Latin scripts survive. OpenFaceFX was hard-wired to English. This module
formalizes the grapheme-to-phoneme **seam** into a small protocol and adds the
additive machinery around it — every hook is opt-in, so the default English path
(:class:`openfacefx.g2p.G2P` with no dictionary, pronouncer or custom tokenizer)
is **byte-identical** to before.

  * :class:`Pronouncer` — the protocol: a **tokenizer** (``text -> words``) plus a
    word/phrase **grapheme-to-phoneme** map. ``G2P`` is the English implementation.
  * :func:`read_dictionary` — load a ``.dict`` file declaring ``locale`` and
    ``alphabet``, mapping each entry into the internal ARPAbet inventory via the
    IPA/SAMPA alias tables in :mod:`openfacefx.phonemes`.
  * :data:`PronouncerHook` — the ``callable(word, prev, next) -> phonemes | None``
    consulted **between** dictionary lookup and the rule fallback, mirroring
    FaceFX's lookup → pronouncer → rules order.

A phoneme that maps to nothing in the internal inventory passes through and falls
to ``sil`` at the viseme stage (the documented ``PHONEME_TO_VISEME`` behaviour), so
an unmapped symbol is silent, never a crash.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Protocol, runtime_checkable

from .phonemes import ALPHABETS, from_alphabet

#: A pronouncer hook: ``(word, previous_word, next_word) -> internal phonemes``,
#: or ``None`` to defer to the rule fallback. ``previous``/``next`` are ``None`` at
#: the utterance edges.
PronouncerHook = Callable[[str, Optional[str], Optional[str]], Optional[List[str]]]

#: A tokenizer: ``text -> list of word tokens``. The English default is
#: ``re.findall(r"[A-Za-z']+", text)``; a per-language one keeps non-Latin scripts.
Tokenizer = Callable[[str], List[str]]


@runtime_checkable
class Pronouncer(Protocol):
    """The grapheme-to-phoneme seam: a tokenizer plus word/phrase resolution.
    :class:`openfacefx.g2p.G2P` is the English implementation; a locale can supply
    its own conformer."""

    def tokenize(self, text: str) -> List[str]: ...

    def word(self, w: str) -> List[str]: ...

    def phrase(self, text: str) -> List[str]: ...


@dataclass
class Dictionary:
    """A parsed pronunciation dictionary: a ``locale`` tag, the source phoneme
    ``alphabet`` and ``entries`` already mapped into the internal inventory."""
    locale: str = ""
    alphabet: str = "arpabet"
    entries: Dict[str, List[str]] = field(default_factory=dict)


_HEADER = re.compile(r"[;#]+\s*(\w+)\s*[=:]\s*(.+)")


def read_dictionary(path: str) -> Dictionary:
    """Parse a pronunciation ``.dict`` file into a :class:`Dictionary`.

    Header lines (``;;; locale = ja-JP`` / ``;;; alphabet = ipa``) declare the
    locale and phoneme alphabet; each remaining line is ``word  p1 p2 ...`` with
    the phonemes written in that alphabet. Every phoneme is mapped into the
    internal ARPAbet inventory via the alias tables, so a loaded IPA or SAMPA
    dictionary drives the same downstream pipeline. The first spelling of a
    repeated word wins (as CMUdict primary pronunciations do)."""
    locale, alphabet = "", "arpabet"
    entries: Dict[str, List[str]] = {}
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            s = line.strip()
            if not s:
                continue
            if s[0] in ";#":
                m = _HEADER.match(s)
                if m:
                    key, val = m.group(1).lower(), m.group(2).strip()
                    if key == "locale":
                        locale = val
                    elif key in ("alphabet", "phoneset", "phonemes"):
                        alphabet = val.lower()
                continue
            parts = s.split()
            word = parts[0].lower()
            if word not in entries:
                entries[word] = [from_alphabet(p, alphabet) for p in parts[1:]]
    if alphabet not in ALPHABETS:
        raise ValueError(f"dictionary {path}: unknown alphabet {alphabet!r} "
                         f"(declare one of {ALPHABETS})")
    return Dictionary(locale, alphabet, entries)
