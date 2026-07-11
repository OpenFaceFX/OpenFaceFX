"""JALI coarticulation rules over the component stage (issue #19).

JALI (Edwards et al., SIGGRAPH 2016, https://www.dgp.toronto.edu/~elf/JALISIG16.pdf)
publishes a concrete rule set and measured onset/decay constants that extend the
machinery OpenFaceFX already ships (Cohen–Massaro blending, per-class timing,
bilabial closure). This module encodes those rules as a **data-driven table**
(``data/jali_rules.json`` — plain data so new measurements drop in) evaluated over
articulator classes, plus the empirical timing lookup.

Everything here is **opt-in**: it runs only when :attr:`CoartParams.jali` is set,
so with the default (JALI off) :func:`openfacefx.coarticulation.build_viseme_curves`
is byte-identical to before. Each constraint/habit is individually toggleable via
:attr:`CoartParams.jali_rules` (``None`` = all). Implemented (the issue's
prioritised set):

  * **hard constraints** — bilabial lip closure, labiodental bottom-lip-to-teeth,
    sibilant jaw narrowing, non-nasal lip opening;
  * **habits** — duplicated-viseme merge across word boundaries ("po_p m_an"),
    lip-heavy anticipation/hysteresis (UW/OW/OY/w/S/Z/J/C start early & end late),
    tongue-only visemes never influence lip channels;
  * **empirical timing** — per-phoneme, context-dependent onset/decay (post-pause
    vs post-vowel; a 150 ms lip-protrusion extension) replacing the per-class
    ``lead`` constants when :attr:`CoartParams.jali_timing` is on.

Deferred (flagged, not yet implemented): the short-obstruent / nasal
"leave-the-jaw-untouched" and word-final anticipatory-lip habits, and the
tongue-channel gain/offset mapping fields (NVIDIA A2F style) with the ARKit tongue
targets — those touch the mapping schema / shipped presets and are left for a
follow-up.
"""

from __future__ import annotations

import json
from functools import lru_cache
from typing import Dict, List, Optional, Tuple

import numpy as np

from .alignment import PhonemeSegment
from .mapping import _DEFAULT_CLASSES
from .phonemes import SILENCE, is_vowel, strip_stress
from .visemes import phoneme_to_viseme

#: Individually toggleable constraint / habit ids (via ``CoartParams.jali_rules``).
RULE_IDS: Tuple[str, ...] = (
    "bilabial_close", "labiodental_teeth", "sibilant_jaw", "nonnasal_lip_open",
    "duplicated_merge", "lip_heavy", "tongue_no_lip",
)


@lru_cache(maxsize=1)
def load_rules() -> dict:
    """The JALI rule table (cached). Plain JSON data — categories, constraints and
    the empirical timing constants."""
    from importlib import resources
    with resources.files("openfacefx").joinpath(
            "data/jali_rules.json").open(encoding="utf-8") as fh:
        return json.load(fh)


def rule_enabled(params, rule_id: str) -> bool:
    """True if ``rule_id`` is active: JALI on and either all rules (``jali_rules``
    is ``None``) or this id is in the selected set."""
    return bool(getattr(params, "jali", False)) and (
        params.jali_rules is None or rule_id in params.jali_rules)


def _phon(seg: PhonemeSegment) -> str:
    return strip_stress(seg.phoneme).upper()


def _seg_class(seg: PhonemeSegment, mapping) -> str:
    """Articulator class of a segment = class of its highest-weight target (the
    same rule :func:`coarticulation._segment_class` uses)."""
    if mapping is None:
        return _DEFAULT_CLASSES.get(phoneme_to_viseme(seg.phoneme), "basic")
    row = mapping.row(seg.phoneme)
    if not row:
        return "basic"
    return mapping.targets[max(row, key=row.get)].articulator


def _target_classes(names: List[str], mapping) -> List[str]:
    if mapping is not None:
        return [t.articulator for t in mapping.targets]
    return [_DEFAULT_CLASSES.get(n, "basic") for n in names]


def _viseme(seg: PhonemeSegment, mapping) -> str:
    if mapping is None:
        return phoneme_to_viseme(seg.phoneme)
    row = mapping.row(seg.phoneme)
    return mapping.targets[max(row, key=row.get)].name if row else "sil"


# --------------------------------------------------------------------------- #
# habit: duplicated-viseme merge ("pop man" -> "po_p m_an")                     #
# --------------------------------------------------------------------------- #

def merge_duplicates(segments: List[PhonemeSegment], mapping
                     ) -> List[PhonemeSegment]:
    """Merge adjacent segments that map to the **same** viseme into one longer
    segment (JALI's duplicated-viseme merge) — the closing /p/ of "pop" and the
    /m/ of "man" are one bilabial hold, not two. Silence is never merged."""
    out: List[PhonemeSegment] = []
    for s in segments:
        if (out and _viseme(out[-1], mapping) == _viseme(s, mapping)
                and _viseme(s, mapping) != "sil"):
            p = out[-1]
            out[-1] = PhonemeSegment(p.phoneme, p.start, s.end)
        else:
            out.append(s)
    return out


# --------------------------------------------------------------------------- #
# empirical timing: per-phoneme, context-dependent onset / decay extents        #
# --------------------------------------------------------------------------- #

def timing_leads(segments: List[PhonemeSegment], params
                 ) -> Tuple[np.ndarray, np.ndarray]:
    """Per-segment ``(lead_in, lead_out)`` influence extents (seconds) from the
    empirical table, replacing the per-class ``lead`` constants. Onset is
    context-dependent — longer after a pause, tighter after a vowel — and the
    lip-heavy habit extends both sides by the lip-protrusion constant."""
    t = load_rules()["timing"]
    cats = load_rules()["categories"]
    n = len(segments)
    lead_in = np.full(n, float(t["onset"]))
    lead_out = np.full(n, float(t["decay"]))
    lip_heavy = rule_enabled(params, "lip_heavy")
    for i, s in enumerate(segments):
        prev = segments[i - 1] if i > 0 else None
        if prev is None or prev.phoneme == SILENCE:
            lead_in[i] = t["post_pause_onset"]
        elif is_vowel(prev.phoneme):
            lead_in[i] = t["post_vowel_onset"]
        if lip_heavy and _phon(s) in cats["lip_heavy"]:
            lead_in[i] += t["lip_protrusion_ext"]
            lead_out[i] += t["lip_protrusion_ext"]
    return lead_in, lead_out


# --------------------------------------------------------------------------- #
# habit: tongue-only visemes never influence lip channels                       #
# --------------------------------------------------------------------------- #

def mask_tongue_lips(weights: np.ndarray, segments: List[PhonemeSegment],
                     mapping, names: List[str]) -> None:
    """Zero every tongue-class segment's weight on lip-class targets, in place —
    a tongue articulation (l/n/t/d/g/k/ng) must not pull the lips."""
    classes = _target_classes(names, mapping)
    lip_cols = [j for j, c in enumerate(classes) if c == "lips"]
    if not lip_cols:
        return
    for i, s in enumerate(segments):
        if _seg_class(s, mapping) == "tongue":
            for j in lip_cols:
                weights[i, j] = 0.0


# --------------------------------------------------------------------------- #
# hard constraints (post-blend forcings over the dense matrix)                  #
# --------------------------------------------------------------------------- #

def _selection(times: np.ndarray, centre: float, half: float) -> np.ndarray:
    sel = np.abs(times - centre) <= half
    if not np.any(sel):
        sel = np.zeros(len(times), bool)
        sel[int(np.argmin(np.abs(times - centre)))] = True
    return sel


def _force_closure(matrix: np.ndarray, weights: np.ndarray, i: int,
                   sel: np.ndarray, floor: float) -> None:
    """Raise segment ``i``'s main target to ``floor`` over ``sel`` and rescale the
    rest so each frame still sums to ~1 (the closure-enforcement math)."""
    v = int(np.argmax(weights[i]))
    if weights[i, v] <= 0.0:
        return
    for f in np.nonzero(sel)[0]:
        cur = matrix[f, v]
        if cur >= floor:
            continue
        others = matrix[f].sum() - cur
        if others > 1e-9:
            matrix[f] *= (1.0 - floor) / others
        matrix[f, v] = floor


def apply_constraints(times: np.ndarray, matrix: np.ndarray,
                      segments: List[PhonemeSegment], mapping,
                      weights: np.ndarray, names: List[str], params) -> None:
    """Apply the enabled hard constraints to the dense matrix, in place. Called
    from :func:`coarticulation._blend` in place of the legacy
    ``_enforce_closures`` when JALI is on."""
    rules = load_rules()
    cats, cons = rules["categories"], rules["constraints"]
    classes = _target_classes(names, mapping)
    jaw_cols = [j for j, c in enumerate(classes) if c == "jaw"]
    lip_cols = [j for j, c in enumerate(classes) if c == "lips"]
    for i, s in enumerate(segments):
        ph = _phon(s)
        centre = (s.start + s.end) / 2.0
        sel = _selection(times, centre, max(s.dur * 0.25, 1.0 / 120.0))
        # constraints 1 & 2: bilabial / labiodental lip closure
        for cid, cat in (("bilabial_close", "bilabial"),
                         ("labiodental_teeth", "labiodental")):
            if rule_enabled(params, cid) and ph in cats[cat]:
                _force_closure(matrix, weights, i, sel, cons[cid]["floor"])
        # constraint 3: sibilants narrow the jaw across the segment
        if rule_enabled(params, "sibilant_jaw") and ph in cats["sibilant"] and jaw_cols:
            cap = cons["sibilant_jaw"]["jaw_cap"]
            span = (times >= s.start) & (times <= s.end)
            for j in jaw_cols:
                matrix[span, j] = np.minimum(matrix[span, j], cap)
        # constraint 4: non-nasal open segments open the lips (cap closed-lip
        # targets so a neighbour's closure does not bleed across)
        if (rule_enabled(params, "nonnasal_lip_open") and lip_cols
                and ph not in cats["nasal"] and ph not in cats["bilabial"]
                and ph not in cats["labiodental"]):
            cap = cons["nonnasal_lip_open"]["lip_cap"]
            for j in lip_cols:
                matrix[sel, j] = np.minimum(matrix[sel, j], cap)
