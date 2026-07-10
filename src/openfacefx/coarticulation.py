"""Coarticulation via dominance functions (Cohen & Massaro, 1993).

Real speech is not a sequence of discrete mouth poses -- each phoneme's shape
is pulled toward its neighbours. A common, well-cited way to model this is to
give every phoneme segment a *dominance function*: a bump in time, peaked at the
segment centre, that decays outward. The activation of a viseme channel at any
instant is the dominance-weighted average of the targets of all nearby segments.

    F_v(t) = sum_i D_i(t) * target(i, v)  /  sum_i D_i(t)

where D_i(t) = alpha_i * exp( -theta_i * |t - c_i| )  (a Laplacian bump),
c_i is the segment centre, and target(i, v) is 1 if segment i maps to viseme v.

The result is smooth, overlapping viseme curves rather than hard switches.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import numpy as np

from .alignment import PhonemeSegment
from .mapping import Mapping, _DEFAULT_CLASSES
from .visemes import VISEMES, VISEME_INDEX, phoneme_to_viseme
from .phonemes import SILENCE, is_vowel, strip_stress
from .ipa import is_ipa_vowel
from .postprocess import smooth_matrix


def _seg_is_vowel(seg: PhonemeSegment) -> bool:
    """Vowel test for the dominance model. ARPABET (``is_vowel``) is checked
    first so the default path is unchanged; the IPA vowel set is consulted too
    so Piper/Cartesia/espeak vowels also get the broad vowel dominance (their
    symbols never satisfy ``is_vowel``, so ARPABET stays byte-identical)."""
    return is_vowel(seg.phoneme) or is_ipa_vowel(seg.phoneme)


# Vowels dominate (mouth opens broadly); consonants are sharper/briefer.
def _alpha(seg: PhonemeSegment) -> float:
    return 1.0 if _seg_is_vowel(seg) else 0.85


def _theta(seg: PhonemeSegment) -> float:
    """Decay rate (1/seconds). Shorter segments decay faster so a quick stop
    does not smear across the whole word."""
    dur = max(seg.dur, 1e-3)
    base = 6.0 if _seg_is_vowel(seg) else 11.0
    # Scale so very long segments stay broad and very short ones stay tight.
    return base * (0.09 / dur) ** 0.5


@dataclass
class CoartParams:
    """Component-model tunables (FaceFX-style ca_* knobs).

    ``lead`` gives per-articulator-class (lead_in, lead_out) extents in
    seconds — how far a segment's influence reaches before/after its centre.
    The "basic"/"jaw" defaults reproduce the classic symmetric model; lips
    and especially tongue targets are tighter, so a quick stop does not smear
    across neighbouring vowels.

    ``intensity`` (master) and ``gains`` (per-articulator-class) are JALI-style
    artistic dials: after the curves are normalized, every channel's opening is
    scaled by ``intensity * gains[class]`` and the freed weight flows into
    ``sil`` (see ``_apply_intensity``). All ``1.0`` is a byte-identical no-op;
    ``<1`` mumbles / softens a class, ``>1`` hyper-articulates, ``0`` mutes it.
    Enforced lip closures still win afterwards, so a whispered bilabial seals.

    ``smooth`` and ``lag`` are FaceFX-style post-solve curve conditioning
    (:mod:`openfacefx.postprocess`), both default off. ``smooth`` is the sigma
    (seconds) of a temporal Gaussian run over the dense matrix before keyframe
    reduction to soften jitter; closures are re-enforced *after* it, so lip
    seals stay sharp. ``lag`` slides the reduced keyframes in time (seconds;
    ``>0`` lags / ``<0`` leads the audio) and is applied by the pipeline once
    curves are reduced, not here. ``0.0`` for both is a byte-identical no-op.
    """
    lead: Dict[str, Tuple[float, float]] = field(default_factory=lambda: {
        "basic": (0.40, 0.45),
        "jaw": (0.40, 0.45),
        "lips": (0.30, 0.30),
        "tongue": (0.15, 0.15),
    })
    short_silence: float = 0.27   # absorb interior silences shorter than this
    closure_floor: float = 0.90   # enforced lip-target weight at closures
    split_diphthongs: bool = True
    preroll: float = 0.0          # anticipation seconds sampled before onset
    allow_negative_time: bool = False  # preroll below t=0 instead of clamping
    intensity: float = 1.0        # master articulation gain (all channels)
    gains: Dict[str, float] = field(default_factory=lambda: {
        "basic": 1.0, "jaw": 1.0, "lips": 1.0, "tongue": 1.0,
    })
    smooth: float = 0.0           # Gaussian smoothing sigma in seconds (0 = off)
    lag: float = 0.0              # keyframe time-shift seconds (>0 lag, <0 lead)

# Diphthong -> component vowels (split at 55% of the segment).
_DIPHTHONGS = {
    "AY": ("AA", "IY"), "AW": ("AA", "UW"), "EY": ("EH", "IY"),
    "OW": ("AO", "UW"), "OY": ("AO", "IY"),
}

# Articulator classes whose targets get closure enforcement.
_CLOSURE_CLASSES = ("lips",)


def _preprocess(segments: List[PhonemeSegment],
                params: CoartParams) -> List[PhonemeSegment]:
    # Absorb short interior silences into the preceding phoneme so the mouth
    # does not flap shut between words (FaceFX ca_shortsilenceduration).
    out: List[PhonemeSegment] = []
    last = len(segments) - 1
    for i, s in enumerate(segments):
        if (out and 0 < i < last and s.phoneme == SILENCE
                and s.dur < params.short_silence):
            prev = out[-1]
            out[-1] = PhonemeSegment(prev.phoneme, prev.start, s.end)
            continue
        out.append(s)
    if not params.split_diphthongs:
        return out
    split: List[PhonemeSegment] = []
    for s in out:
        parts = _DIPHTHONGS.get(strip_stress(s.phoneme).upper())
        if parts and s.dur > 1e-3:
            cut = s.start + s.dur * 0.55
            split.append(PhonemeSegment(parts[0], s.start, cut))
            split.append(PhonemeSegment(parts[1], cut, s.end))
        else:
            split.append(s)
    return split


def _segment_class(seg: PhonemeSegment, mapping: Optional[Mapping]) -> str:
    """Articulator class of a segment = class of its highest-weight target."""
    if mapping is None:
        return _DEFAULT_CLASSES.get(phoneme_to_viseme(seg.phoneme), "basic")
    row = mapping.row(seg.phoneme)
    if not row:
        return "basic"
    idx = max(row, key=row.get)
    return mapping.targets[idx].articulator


def build_viseme_curves(
    segments: List[PhonemeSegment],
    fps: float = 60.0,
    mapping: Optional[Mapping] = None,
    params: Optional[CoartParams] = None,
) -> tuple:
    """Return (times, matrix) where matrix[frame, target] in [0,1].

    ``times`` is a 1-D array of sample times. Without ``mapping``, columns
    follow ``visemes.VISEMES``; with a ``Mapping`` they follow
    ``mapping.target_names`` and any phoneme may drive several targets with
    fractional weights. ``params`` tunes the component coarticulation model
    (per-articulator lead in/out, silence absorption, closure enforcement,
    diphthong splitting, onset pre-roll).
    """
    params = params or CoartParams()
    n_targets = len(mapping.targets) if mapping is not None else len(VISEMES)
    if not segments:
        return np.zeros(0), np.zeros((0, n_targets))
    segments = _preprocess(segments, params)

    t0 = segments[0].start - params.preroll
    if not params.allow_negative_time:
        t0 = max(t0, 0.0) if segments[0].start >= 0.0 else segments[0].start
    t1 = segments[-1].end
    n = max(int(round((t1 - t0) * fps)) + 1, 1)
    times = t0 + np.arange(n) / fps

    centres = np.array([(s.start + s.end) / 2 for s in segments])
    alphas = np.array([_alpha(s) for s in segments])
    thetas = np.array([_theta(s) for s in segments])

    # Per-class asymmetric influence: scale the decay rate on each side by
    # (basic_lead / class_lead), so "basic"/"jaw" reproduce the classic
    # symmetric model and tighter articulators rise/decay faster.
    base_in, base_out = 0.40, 0.45
    lead = [params.lead.get(_segment_class(s, mapping), (base_in, base_out))
            for s in segments]
    scale_in = np.array([base_in / max(li, 1e-3) for li, _ in lead])
    scale_out = np.array([base_out / max(lo, 1e-3) for _, lo in lead])

    # Per-segment target weights: shape (n_seg, n_targets). The built-in
    # table is one-hot, so the weighted path reproduces it bit-for-bit.
    weights = np.zeros((len(segments), n_targets))
    if mapping is not None:
        for i, s in enumerate(segments):
            for idx, w in mapping.row(s.phoneme).items():
                weights[i, idx] = w
    else:
        idx = [VISEME_INDEX[phoneme_to_viseme(s.phoneme)] for s in segments]
        weights[np.arange(len(segments)), idx] = 1.0

    # Dominance of every segment at every sample time: shape (n, n_seg)
    dt = np.abs(times[:, None] - centres[None, :])
    before = times[:, None] < centres[None, :]
    theta_eff = np.where(before,
                         (thetas * scale_in)[None, :],
                         (thetas * scale_out)[None, :])
    dom = alphas[None, :] * np.exp(-theta_eff * dt)

    denom = dom.sum(axis=1, keepdims=True)
    denom[denom == 0] = 1.0

    matrix = np.zeros((n, n_targets))
    for v in range(n_targets):
        matrix[:, v] = (dom * weights[None, :, v]).sum(axis=1) / denom[:, 0]

    # Clean numerical dust and clamp, apply the artistic intensity/gain dials
    # (a no-op at defaults), optionally smooth the dense curves, then enforce
    # closures LAST so enforced frames end up summing to exactly 1 and the lip
    # seals stay sharp even when smoothing rounded everything else off (closures
    # win over the dials and the filter). Smoothing preserves the ~1 row sums, so
    # the partition-energy invariant holds throughout.
    matrix[matrix < 1e-4] = 0.0
    np.clip(matrix, 0.0, 1.0, out=matrix)
    _apply_intensity(matrix, mapping, params)
    if params.smooth > 0.0:
        matrix = smooth_matrix(matrix, params.smooth, fps)
    _enforce_closures(times, matrix, segments, mapping, weights, params)
    return times, matrix


def _apply_intensity(matrix, mapping, params) -> None:
    """Scale every channel's opening by ``intensity * gains[its class]`` and let
    ``sil`` reabsorb the freed weight, in place.

    Each column's articulator class is read the same way ``_segment_class`` reads
    a segment's — from the mapping target (or the built-in ``_DEFAULT_CLASSES``).
    The invariant is that a normalized frame sums to ~1, i.e. openness (all the
    non-``sil`` weight) plus ``sil`` equals 1. We scale only the openness and put
    whatever it gives up (or takes) back into ``sil``:

        open' = sum_{v != sil} scale_v * matrix[v]      (scale_v >= 0)
        matrix[v != sil] *= scale_v / max(open', 1)      (cap total open at 1)
        matrix[sil]       = max(1 - open', 0)

    so the row sums to exactly 1 again: for open' <= 1, open' + (1 - open') = 1;
    for open' > 1 (dialled past a full-open mouth) the non-``sil`` channels are
    renormalized to fill the frame and ``sil`` is 0. Because every scaled channel
    is then <= the frame total, all values stay within [0, 1] without clipping.

    Defaults (``intensity`` 1.0, all ``gains`` 1.0) make ``scale`` all ones, so
    this returns before touching ``matrix`` — a byte-identical no-op. ``sil`` is
    the designated absorber and is never itself scaled; a mapping with no ``sil``
    target has nowhere to bank the slack, so its channels are scaled and clipped
    in place and the sum-to-1 invariant is not maintained (documented)."""
    names = mapping.target_names if mapping is not None else list(VISEMES)
    scale = np.ones(len(names))
    for j, name in enumerate(names):
        cls = (mapping.targets[j].articulator if mapping is not None
               else _DEFAULT_CLASSES.get(name, "basic"))
        scale[j] = max(params.intensity * params.gains.get(cls, 1.0), 0.0)
    sil = names.index("sil") if "sil" in names else None
    if sil is not None:
        scale[sil] = 1.0                      # absorber is never scaled itself
    if np.all(scale == 1.0):
        return                                # neutral dials: exact no-op

    matrix *= scale[None, :]
    if sil is None:
        np.clip(matrix, 0.0, 1.0, out=matrix)
        return
    openness = matrix.sum(axis=1) - matrix[:, sil]
    matrix *= (1.0 / np.maximum(openness, 1.0))[:, None]
    matrix[:, sil] = np.maximum(1.0 - openness, 0.0)


def _enforce_closures(times, matrix, segments, mapping, weights, params):
    """Guarantee lip seals: at the midpoint of every lips-class segment,
    raise its main target to at least ``closure_floor`` and rescale the other
    channels so each frame still sums to ~1 (a bilabial between open vowels
    must fully close the mouth — FaceFX inserts an OPEN/closure key here)."""
    floor = params.closure_floor
    if floor <= 0.0 or len(times) == 0:
        return
    for i, s in enumerate(segments):
        if _segment_class(s, mapping) not in _CLOSURE_CLASSES:
            continue
        v = int(np.argmax(weights[i]))
        if weights[i, v] <= 0.0:
            continue
        centre = (s.start + s.end) / 2.0
        half = max(s.dur * 0.25, 1.0 / 120.0)
        sel = np.abs(times - centre) <= half
        if not np.any(sel):
            sel = np.array([int(np.argmin(np.abs(times - centre)))])
        for f in np.nonzero(sel)[0] if sel.dtype == bool else sel:
            cur = matrix[f, v]
            if cur >= floor:
                continue
            others = matrix[f].sum() - cur
            if others > 1e-9:
                matrix[f] *= (1.0 - floor) / others
            matrix[f, v] = floor
