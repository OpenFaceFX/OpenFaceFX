"""Data-driven phoneme -> target mapping (FaceFX-style "mapping spreadsheet").

The built-in behavior maps each phoneme to exactly one Oculus-15 viseme at
weight 1.0 (``visemes.PHONEME_TO_VISEME``). A ``Mapping`` generalizes that:
any phoneme may drive any set of named targets with fractional weights, each
target may declare an articulator class (used by the coarticulation model)
and min/max clamps applied before keyframe reduction.

JSON file format (validated on load)::

    {
      "format": "openfacefx.mapping",
      "version": 2,
      "targets": [
        {"name": "PP", "class": "lips", "min": 0.0, "max": 1.0},
        {"name": "tng", "class": "tongue", "gain": 1.5, "offset": 0.05},
        ...
      ],
      "phonemes": { "P": {"PP": 1.0}, "AY": {"aa": 0.7, "E": 0.3}, ... }
    }

Optional per-target ``gain``/``offset`` (schema **version 2**, issue #53) tune a
channel's output NVIDIA-Audio2Face-style — at keyframe reduction the channel
becomes ``clamp(gain*value + offset, min, max)`` — chiefly to scale/bias the
independent tongue channel. They default to ``gain=1.0`` / ``offset=0.0`` (an
exact no-op) and are distinct from :attr:`coarticulation.CoartParams.gains`,
which scales per-class dominance mid-blend. Version-1 files (no gain/offset) still
load — the absent fields read as the no-op defaults — so old mappings stay
byte-identical.

``Mapping.default()`` reproduces the built-in table exactly — running without
``--mapping`` is bit-for-bit identical to previous releases.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional

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
    # NVIDIA-A2F-style per-target output tuning (schema v2, issue #53): at
    # keyframe reduction the channel becomes clamp(gain*value + offset, lo, hi).
    # Defaults are an exact no-op, so a v1 mapping (no fields) is byte-identical.
    gain: float = 1.0
    offset: float = 0.0


@dataclass
class Mapping:
    targets: List[Target]
    rows: Dict[str, Dict[str, float]] = field(default_factory=dict)
    # When set, ``rows`` keys are opaque vendor symbols (Azure viseme IDs as
    # strings, Polly viseme letters, SAMPA/IPA) matched verbatim: no ARPABET
    # validation and no stress-strip/upper-case normalization (which would
    # corrupt numeric IDs and collapse case-significant symbols like s vs S).
    # Set by the timing.py viseme-unit adapters, or by a mapping file with
    # top-level "custom_symbols": true.
    allow_custom_symbols: bool = False
    # Optional symbol normalizer applied to the lookup key (only with
    # ``allow_custom_symbols``). It lets a preset key rows by a base alphabet and
    # match the diacritic-carrying tokens real dumps emit -- the built-in IPA
    # preset (ipa.py) sets it to fold stress/length/tie-bar/etc. onto the base
    # symbol on lookup, rather than duplicating a row per diacritic variant.
    # ``None`` (the default, and every JSON-loaded mapping) matches verbatim.
    normalize: Optional[Callable[[str], str]] = None

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
            if not (math.isfinite(t.gain) and math.isfinite(t.offset)):
                raise ValueError(
                    f"target {t.name!r}: gain/offset must be finite numbers "
                    f"(got gain={t.gain!r}, offset={t.offset!r})")
        for ph, row in self.rows.items():
            if not self.allow_custom_symbols:
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
        Unknown phonemes fall back to the silence row, like the built-in map.
        With ``allow_custom_symbols`` the key is matched verbatim (vendor
        symbols carry no stress digit and are case-significant), unless a
        ``normalize`` hook is set, which is applied to the key first."""
        if self.allow_custom_symbols:
            key = self.normalize(phoneme) if self.normalize is not None else phoneme
            row = self.rows.get(key)
            if row is None:
                row = self.rows.get(SILENCE) or {}
        else:
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
        if d.get("format") != "openfacefx.mapping" or d.get("version") not in (1, 2):
            raise ValueError(
                f"{path}: expected format 'openfacefx.mapping' version 1 or 2")
        try:
            # gain/offset are v2 (issue #53); a v1 file omits them and reads the
            # no-op defaults, so old mappings load unchanged.
            targets = [Target(t["name"], t.get("class", "basic"),
                              float(t.get("min", 0.0)), float(t.get("max", 1.0)),
                              float(t.get("gain", 1.0)), float(t.get("offset", 0.0)))
                       for t in d["targets"]]
        except (KeyError, TypeError) as e:
            raise ValueError(f"{path}: malformed targets entry ({e})") from None
        phonemes = d.get("phonemes")
        if not isinstance(phonemes, dict) or not phonemes:
            raise ValueError(f"{path}: 'phonemes' must be a non-empty object")
        # "custom_symbols": true lets a mapping file key rows by a non-ARPABET
        # alphabet (SAMPA/IPA from TTS timing sources) — matched verbatim.
        return cls(targets, phonemes,
                   allow_custom_symbols=bool(d.get("custom_symbols", False)))

    def to_json(self, path: str) -> None:
        # Emit schema v2, but omit gain/offset when they are the no-op defaults so
        # a mapping that uses no A2F tuning round-trips to the same minimal target
        # entries it always had (only the version tag advances 1 -> 2).
        targets = []
        for t in self.targets:
            entry = {"name": t.name, "class": t.articulator,
                     "min": t.lo, "max": t.hi}
            if t.gain != 1.0:
                entry["gain"] = t.gain
            if t.offset != 0.0:
                entry["offset"] = t.offset
            targets.append(entry)
        d = {
            "format": "openfacefx.mapping",
            "version": 2,
            "targets": targets,
            "phonemes": self.rows,
        }
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(d, fh, indent=2)
            fh.write("\n")
