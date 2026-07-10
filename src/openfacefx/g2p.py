"""Grapheme-to-phoneme (word -> ARPAbet phonemes).

Priority order:
  1. A pronunciation dictionary (CMUdict format) if one is loaded.
  2. A small built-in dictionary so the demo runs with no downloads.
  3. A crude rule-based fallback for out-of-vocabulary words.

For production accuracy, load the full CMU Pronouncing Dictionary via
``G2P.load_cmudict(path)`` or plug in a neural G2P model. The fallback exists
only so nothing crashes on unknown words.
"""

from __future__ import annotations

import re
from typing import Dict, List, Optional

# Tiny seed dictionary. Enough to demo the pipeline offline; replace/extend
# with the full CMUdict (~134k entries) in real use.
_BUILTIN: Dict[str, List[str]] = {
    "hello": ["HH", "AH0", "L", "OW1"],
    "world": ["W", "ER1", "L", "D"],
    "the": ["DH", "AH0"],
    "quick": ["K", "W", "IH1", "K"],
    "brown": ["B", "R", "AW1", "N"],
    "fox": ["F", "AA1", "K", "S"],
    "jumps": ["JH", "AH1", "M", "P", "S"],
    "over": ["OW1", "V", "ER0"],
    "lazy": ["L", "EY1", "Z", "IY0"],
    "dog": ["D", "AO1", "G"],
    "this": ["DH", "IH1", "S"],
    "is": ["IH1", "Z"],
    "a": ["AH0"],
    "test": ["T", "EH1", "S", "T"],
    "of": ["AH1", "V"],
    "speech": ["S", "P", "IY1", "CH"],
    "animation": ["AE2", "N", "AH0", "M", "EY1", "SH", "AH0", "N"],
}

# Very rough letter-cluster -> phoneme rules for OOV fallback.
_RULES = [
    ("tch", ["CH"]), ("sh", ["SH"]), ("ch", ["CH"]), ("th", ["TH"]),
    ("ph", ["F"]), ("ck", ["K"]), ("ng", ["NG"]), ("qu", ["K", "W"]),
    ("oo", ["UW1"]), ("ee", ["IY1"]), ("ea", ["IY1"]), ("ou", ["AW1"]),
    ("ai", ["EY1"]), ("ay", ["EY1"]), ("oa", ["OW1"]), ("igh", ["AY1"]),
    ("a", ["AE1"]), ("e", ["EH1"]), ("i", ["IH1"]), ("o", ["AA1"]),
    ("u", ["AH1"]), ("y", ["IY1"]),
    ("b", ["B"]), ("c", ["K"]), ("d", ["D"]), ("f", ["F"]), ("g", ["G"]),
    ("h", ["HH"]), ("j", ["JH"]), ("k", ["K"]), ("l", ["L"]), ("m", ["M"]),
    ("n", ["N"]), ("p", ["P"]), ("r", ["R"]), ("s", ["S"]), ("t", ["T"]),
    ("v", ["V"]), ("w", ["W"]), ("x", ["K", "S"]), ("z", ["Z"]),
]


class G2P:
    def __init__(self) -> None:
        self._dict: Dict[str, List[str]] = dict(_BUILTIN)

    def load_cmudict(self, path: str) -> int:
        """Load a CMUdict-format file. Returns the number of entries added.

        Format per line:  WORD  P1 P2 P3 ...   (alt pronunciations as WORD(2))
        """
        added = 0
        with open(path, "r", encoding="latin-1") as fh:
            for line in fh:
                if line.startswith(";;;") or not line.strip():
                    continue
                parts = line.split()
                word = re.sub(r"\(\d+\)$", "", parts[0]).lower()
                if word not in self._dict:  # keep first (primary) pronunciation
                    self._dict[word] = parts[1:]
                    added += 1
        return added

    def word(self, w: str) -> List[str]:
        key = re.sub(r"[^a-z']", "", w.lower())
        if not key:
            return []
        if key in self._dict:
            return list(self._dict[key])
        return self._fallback(key)

    def phrase(self, text: str) -> List[str]:
        out: List[str] = []
        for w in re.findall(r"[A-Za-z']+", text):
            out.extend(self.word(w))
        return out

    def _fallback(self, key: str) -> List[str]:
        phones: List[str] = []
        i = 0
        while i < len(key):
            for cluster, ph in _RULES:
                if key.startswith(cluster, i):
                    phones.extend(ph)
                    i += len(cluster)
                    break
            else:
                i += 1
        return phones or ["AH1"]
