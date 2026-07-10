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

from typing import List, Optional

import numpy as np

from .alignment import PhonemeSegment
from .mapping import Mapping
from .visemes import VISEMES, VISEME_INDEX, phoneme_to_viseme
from .phonemes import is_vowel


# Vowels dominate (mouth opens broadly); consonants are sharper/briefer.
def _alpha(seg: PhonemeSegment) -> float:
    return 1.0 if is_vowel(seg.phoneme) else 0.85


def _theta(seg: PhonemeSegment) -> float:
    """Decay rate (1/seconds). Shorter segments decay faster so a quick stop
    does not smear across the whole word."""
    dur = max(seg.dur, 1e-3)
    base = 6.0 if is_vowel(seg.phoneme) else 11.0
    # Scale so very long segments stay broad and very short ones stay tight.
    return base * (0.09 / dur) ** 0.5


def build_viseme_curves(
    segments: List[PhonemeSegment],
    fps: float = 60.0,
    mapping: Optional[Mapping] = None,
) -> tuple:
    """Return (times, matrix) where matrix[frame, target] in [0,1].

    ``times`` is a 1-D array of sample times. Without ``mapping``, columns
    follow ``visemes.VISEMES`` and each phoneme drives exactly one viseme —
    identical to previous releases. With a ``Mapping``, columns follow
    ``mapping.target_names`` and any phoneme may drive several targets with
    fractional weights.
    """
    n_targets = len(mapping.targets) if mapping is not None else len(VISEMES)
    if not segments:
        return np.zeros(0), np.zeros((0, n_targets))

    t0 = segments[0].start
    t1 = segments[-1].end
    n = max(int(round((t1 - t0) * fps)) + 1, 1)
    times = t0 + np.arange(n) / fps

    centres = np.array([(s.start + s.end) / 2 for s in segments])
    alphas = np.array([_alpha(s) for s in segments])
    thetas = np.array([_theta(s) for s in segments])

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
    dom = alphas[None, :] * np.exp(-thetas[None, :] * dt)

    denom = dom.sum(axis=1, keepdims=True)
    denom[denom == 0] = 1.0

    matrix = np.zeros((n, n_targets))
    for v in range(n_targets):
        matrix[:, v] = (dom * weights[None, :, v]).sum(axis=1) / denom[:, 0]

    # Clean numerical dust and clamp.
    matrix[matrix < 1e-4] = 0.0
    np.clip(matrix, 0.0, 1.0, out=matrix)
    return times, matrix
