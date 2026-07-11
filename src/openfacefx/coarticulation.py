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


def _stress_gains(segments: List[PhonemeSegment], amount: float) -> np.ndarray:
    """Per-segment dominance multipliers for the lexical-stress amplitude pass.

    Reuses the same ARPABET stress-digit cue the gesture layer reads (a vowel
    whose phoneme ends in ``1`` is primary-stressed): a primary-stress vowel is
    scaled to ``1 + amount``, a secondary (``2``) to ``1 + 0.5*amount``, and an
    explicitly unstressed vowel (``0``) down to ``1 - 0.35*amount``; consonants
    and digit-less vowels stay ``1.0``. Multiplied into ``alphas``, this makes a
    stressed syllable win more of the dominance blend (its viseme peaks higher
    and holds longer) while unstressed ones yield to their neighbours.

    Scaling the dominance *amplitude* — not the normalized weights — is what
    keeps the row-sum partition invariant intact: the factor multiplies segment
    ``i`` in both the blend numerator ``sum_i D_i * target(i,v)`` and the shared
    denominator ``sum_i D_i``, so every frame still sums to the same unit energy
    (only the balance between segments shifts). ``amount`` is caller-bounded to
    ``[0, 2]``; the ``0.35`` unstressed cut keeps the multiplier positive there.
    When no vowel carries a digit (vendor/IPA timing) every factor is ``1.0`` and
    the multiply is exact, so the pass is a graceful, byte-identical no-op."""
    gains = np.ones(len(segments))
    for i, s in enumerate(segments):
        ph = s.phoneme
        if not (ph and ph[-1].isdigit() and _seg_is_vowel(s)):
            continue
        digit = ph[-1]
        if digit == "1":
            gains[i] = 1.0 + amount
        elif digit == "2":
            gains[i] = 1.0 + 0.5 * amount
        elif digit == "0":
            gains[i] = 1.0 - 0.35 * amount
    return gains


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

    ``stress_emphasis`` (issue #18) is the lexical-stress amplitude pass: with
    it ``> 0`` a vowel segment carrying an ARPABET primary-stress digit (``1``)
    has its dominance amplitude raised, secondary (``2``) half as much, and an
    explicitly unstressed vowel (``0``) slightly lowered, so stressed syllables
    win more of the blend and articulate more strongly (see ``_stress_gains``).
    Because it scales the *dominance* — which appears in both the blend and its
    normalizing denominator — the partition invariant is untouched. It is a
    graceful no-op on inputs without stress digits (vendor/IPA timing) and, at
    the ``0.0`` default, byte-identical. Named delivery-style presets that bundle
    the ``intensity``/``gains`` dials live in ``STYLE_PRESETS`` / ``style_params``.
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
    stress_emphasis: float = 0.0  # lexical-stress articulation boost (0 = off)
    # Time-windowed local articulation boost (issue #7 [emphasis] text tags):
    # ``(t0, t1, gain)`` triples scale the dominance amplitude of every segment
    # whose centre falls in ``[t0, t1]`` by ``gain`` — the same alpha-multiply
    # mechanism ``stress_emphasis`` uses (:func:`_stress_gains`), so the partition
    # invariant is preserved, but localized to a span the tag layer resolves from
    # word timings instead of read from lexical-stress digits. Empty (the default)
    # is a byte-identical no-op.
    emphasis_windows: List[Tuple[float, float, float]] = field(default_factory=list)
    # JALI coarticulation (issue #19), all opt-in behind ``jali``. Off (the
    # default) leaves :func:`build_viseme_curves` byte-identical. When on, a
    # data-driven rule table (:mod:`openfacefx.coart_jali` / ``data/jali_rules
    # .json``) adds JALI's hard constraints + habits (bilabial/labiodental
    # closure, sibilant jaw narrowing, non-nasal lip opening, duplicated-viseme
    # merge, lip-heavy anticipation, tongue-never-touches-lips) and, with
    # ``jali_timing``, its empirical per-phoneme onset/decay lookup in place of
    # the per-class ``lead`` constants. ``jali_rules`` picks which rule ids are
    # active (``None`` = all).
    jali: bool = False
    jali_rules: Optional[Tuple[str, ...]] = None
    jali_timing: bool = True

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
    if params.jali:                          # JALI duplicated-viseme merge (#19)
        from . import coart_jali
        if coart_jali.rule_enabled(params, "duplicated_merge"):
            segments = coart_jali.merge_duplicates(segments, mapping)

    t0 = segments[0].start - params.preroll
    if not params.allow_negative_time:
        t0 = max(t0, 0.0) if segments[0].start >= 0.0 else segments[0].start
    t1 = segments[-1].end
    n = max(int(round((t1 - t0) * fps)) + 1, 1)
    times = t0 + np.arange(n) / fps

    matrix = _blend(segments, times, mapping, params, fps)
    return times, matrix


def _blend(segments: List[PhonemeSegment], times: np.ndarray,
           mapping: Optional[Mapping], params: CoartParams, fps: float):
    """The dominance blend + conditioning for ALREADY-preprocessed segments over
    an explicit ``times`` grid — the reusable core of :func:`build_viseme_curves`
    (which is ``_preprocess`` + grid + this). The streaming generator (issue #43,
    :mod:`openfacefx.streaming`) calls it on a bounded segment *window* and its own
    frame grid, so streaming and the offline solve share this exact math. Returns
    the ``(len(times), n_targets)`` matrix."""
    n_targets = len(mapping.targets) if mapping is not None else len(VISEMES)
    n = len(times)
    centres = np.array([(s.start + s.end) / 2 for s in segments])
    alphas = np.array([_alpha(s) for s in segments])
    thetas = np.array([_theta(s) for s in segments])

    # Lexical-stress amplitude pass (issue #18): bias each segment's dominance by
    # its ARPABET stress digit before the blend. Multiplying alphas by 1.0 (the
    # default, or any digit-less input) is exact, so this stays a byte-identical
    # no-op off the stressed path.
    if params.stress_emphasis > 0.0:
        alphas = alphas * _stress_gains(segments, params.stress_emphasis)

    # Local emphasis pass (issue #7 [emphasis] tags): scale the dominance of the
    # segments inside each tag-resolved time window. Like the stress pass above it
    # multiplies segment amplitudes (present in both blend numerator and shared
    # denominator), so the row-sum partition is untouched — only the balance
    # shifts toward the emphasized span, whose visemes then peak higher. Empty
    # ``emphasis_windows`` skips this entirely (byte-identical).
    if params.emphasis_windows:
        alphas = alphas.copy()
        for t0, t1, gain in params.emphasis_windows:
            alphas[(centres >= t0) & (centres <= t1)] *= gain

    # Per-class asymmetric influence: scale the decay rate on each side by
    # (basic_lead / class_lead), so "basic"/"jaw" reproduce the classic
    # symmetric model and tighter articulators rise/decay faster.
    base_in, base_out = 0.40, 0.45
    if params.jali and params.jali_timing:   # JALI empirical onset/decay (#19)
        from . import coart_jali
        lead_in, lead_out = coart_jali.timing_leads(segments, params)
        scale_in = base_in / np.maximum(lead_in, 1e-3)
        scale_out = base_out / np.maximum(lead_out, 1e-3)
    else:                                     # legacy per-class lead constants
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
    if params.jali:                          # tongue-only visemes never pull lips
        from . import coart_jali
        if coart_jali.rule_enabled(params, "tongue_no_lip"):
            names = mapping.target_names if mapping is not None else list(VISEMES)
            coart_jali.mask_tongue_lips(weights, segments, mapping, names)

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
    if params.jali:                          # JALI hard constraints (#19) replace
        from . import coart_jali               # the legacy lips-only closure pass
        names = mapping.target_names if mapping is not None else list(VISEMES)
        coart_jali.apply_constraints(times, matrix, segments, mapping, weights,
                                     names, params)
    else:
        _enforce_closures(times, matrix, segments, mapping, weights, params)
    return matrix


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


# Named delivery-style presets (issue #18): each is a small set of CoartParams
# dial overrides — a JALI-style master ``intensity`` with per-articulator-class
# ``gains`` — capturing a delivery style as *data*. "neutral" is empty, so
# ``style_params("neutral")`` is a default ``CoartParams()`` and selecting it is
# byte-identical to passing no style. A low intensity with tucked-in gains
# mumbles/softens; a high one with opened jaw/lip gains broadens/hyper-
# articulates. These only bias amplitude — enforced lip closures still seal
# afterwards, so a whispered bilabial fully closes — so they stay artistic, not
# destructive. Compose with the CLI: an explicit --intensity/--gain wins on top.
STYLE_PRESETS: Dict[str, Dict[str, object]] = {
    "neutral": {},
    "whisper": {"intensity": 0.5,
                "gains": {"jaw": 0.7, "lips": 0.95, "tongue": 0.85}},
    "mumble": {"intensity": 0.62,
               "gains": {"jaw": 0.75, "lips": 0.85, "tongue": 0.7}},
    "tense": {"intensity": 0.95,
              "gains": {"jaw": 0.8, "lips": 1.12, "tongue": 1.18}},
    "exaggerated": {"intensity": 1.35,
                    "gains": {"jaw": 1.3, "lips": 1.2, "tongue": 1.15}},
    "broad": {"intensity": 1.55,
              "gains": {"jaw": 1.5, "lips": 1.28, "tongue": 1.1}},
}


def style_params(name: str) -> CoartParams:
    """A fresh :class:`CoartParams` for the named style in ``STYLE_PRESETS``.

    The preset's dial overrides are laid over the defaults, so unset fields keep
    their default (byte-identical) values; ``gains``/``lead`` merge onto the
    all-1.0 defaults, scalar fields replace. ``style_params("neutral")`` is thus
    a plain ``CoartParams()``. A new instance is returned each call, so callers
    may mutate it (e.g. compose CLI dials on top) without disturbing the shared
    table. Unknown ``name`` raises ``KeyError`` (validated at the CLI boundary)."""
    if name not in STYLE_PRESETS:
        raise KeyError(name)
    p = CoartParams()
    for field_name, value in STYLE_PRESETS[name].items():
        if field_name in ("gains", "lead"):
            merged = dict(getattr(p, field_name))
            merged.update(value)
            setattr(p, field_name, merged)
        else:
            setattr(p, field_name, value)
    return p
