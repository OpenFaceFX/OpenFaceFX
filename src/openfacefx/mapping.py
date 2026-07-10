"""Data-driven phoneme -> target mapping (FaceFX-style "mapping spreadsheet").

The built-in behavior maps each phoneme to exactly one Oculus-15 viseme at
weight 1.0 (``visemes.PHONEME_TO_VISEME``). A ``Mapping`` generalizes that:
any phoneme may drive any set of named targets with fractional weights, each
target may declare an articulator class (used by the coarticulation model)
and min/max clamps applied before keyframe reduction.

JSON file format (validated on load)::

    {
      "format": "openfacefx.mapping",
      "version": 1,
      "targets": [
        {"name": "PP", "class": "lips", "min": 0.0, "max": 1.0},
        ...
      ],
      "phonemes": { "P": {"PP": 1.0}, "AY": {"aa": 0.7, "E": 0.3}, ... }
    }

``Mapping.default()`` reproduces the built-in table exactly — running without
``--mapping`` is bit-for-bit identical to previous releases.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from .phonemes import ARPABET, SILENCE, strip_stress
from .visemes import PHONEME_TO_VISEME, VISEMES

ARTICULATOR_CLASSES = ("basic", "jaw", "lips", "tongue")

# Default articulator classes for the Oculus-15 targets (used by the
# component coarticulation model; "basic" timing when unspecified).
_DEFAULT_CLASSES = {
    "PP": "lips", "FF": "lips",
    "TH": "tongue", "DD": "tongue", "nn": "tongue", "SS": "tongue",
    "CH": "tongue", "kk": "tongue", "RR": "tongue",
    "aa": "jaw", "E": "jaw", "I": "jaw", "O": "jaw", "U": "jaw",
    "sil": "basic",
}


@dataclass
class Target:
    name: str
    articulator: str = "basic"
    lo: float = 0.0
    hi: float = 1.0


@dataclass
class Mapping:
    targets: List[Target]
    rows: Dict[str, Dict[str, float]] = field(default_factory=dict)

    def __post_init__(self):
        names = [t.name for t in self.targets]
        if len(set(names)) != len(names):
            raise ValueError("duplicate target names in mapping")
        self.index = {n: i for i, n in enumerate(names)}
        for t in self.targets:
            if t.articulator not in ARTICULATOR_CLASSES:
                raise ValueError(
                    f"target {t.name!r}: unknown articulator class "
                    f"{t.articulator!r} (use one of {ARTICULATOR_CLASSES})")
            if not (0.0 <= t.lo <= t.hi <= 1.0):
                raise ValueError(
                    f"target {t.name!r}: invalid clamp range [{t.lo}, {t.hi}]")
        for ph, row in self.rows.items():
            key = strip_stress(ph).upper() if ph != SILENCE else SILENCE
            if key != SILENCE and key not in ARPABET:
                raise ValueError(f"unknown phoneme {ph!r} in mapping")
            for tname, w in row.items():
                if tname not in self.index:
                    raise ValueError(
                        f"phoneme {ph!r} maps to undeclared target {tname!r}")
                if not (isinstance(w, (int, float)) and math.isfinite(w) and w >= 0.0):
                    raise ValueError(
                        f"phoneme {ph!r} -> {tname!r}: weight must be a "
                        f"finite number >= 0, got {w!r}")

    @property
    def target_names(self) -> List[str]:
        return [t.name for t in self.targets]

    def row(self, phoneme: str) -> Dict[int, float]:
        """Target-index -> weight for a (possibly stressed) phoneme.
        Unknown phonemes fall back to the silence row, like the built-in map."""
        key = strip_stress(phoneme).upper() if phoneme != SILENCE else SILENCE
        row = self.rows.get(key) or self.rows.get(SILENCE) or {}
        return {self.index[n]: w for n, w in row.items()}

    @classmethod
    def default(cls) -> "Mapping":
        targets = [Target(v, _DEFAULT_CLASSES.get(v, "basic")) for v in VISEMES]
        rows = {ph: {vis: 1.0} for ph, vis in PHONEME_TO_VISEME.items()}
        return cls(targets, rows)

    @classmethod
    def from_json(cls, path: str) -> "Mapping":
        with open(path, encoding="utf-8") as fh:
            try:
                d = json.load(fh)
            except json.JSONDecodeError as e:
                raise ValueError(f"{path}: not valid JSON ({e})") from None
        if d.get("format") != "openfacefx.mapping" or d.get("version") != 1:
            raise ValueError(
                f"{path}: expected format 'openfacefx.mapping' version 1")
        try:
            targets = [Target(t["name"], t.get("class", "basic"),
                              float(t.get("min", 0.0)), float(t.get("max", 1.0)))
                       for t in d["targets"]]
        except (KeyError, TypeError) as e:
            raise ValueError(f"{path}: malformed targets entry ({e})") from None
        phonemes = d.get("phonemes")
        if not isinstance(phonemes, dict) or not phonemes:
            raise ValueError(f"{path}: 'phonemes' must be a non-empty object")
        return cls(targets, phonemes)

    def to_json(self, path: str) -> None:
        d = {
            "format": "openfacefx.mapping",
            "version": 1,
            "targets": [
                {"name": t.name, "class": t.articulator,
                 "min": t.lo, "max": t.hi}
                for t in self.targets
            ],
            "phonemes": self.rows,
        }
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(d, fh, indent=2)
            fh.write("\n")
