"""Post-solve curve conditioning: temporal smoothing and lag/lead time-shift.

FaceFX ships a Curve Smoothing dialog -- a temporal filter that softens jitter
plus Lag/Lead presets that slide a curve in time so the visemes can lead or
trail the audio. OpenFaceFX ran the dominance solver straight into RDP keyframe
reduction with no conditioning in between; these two functions add that stage.
Both are opt-in and default to an exact no-op.

  * :func:`smooth_matrix` runs a normalized Gaussian over the *dense* per-frame
    viseme matrix, BEFORE keyframe reduction, so the sparse output is clean.
    Because the kernel is a partition of unity applied uniformly to every
    channel with edge-hold padding, a frame that summed to ~1 still sums to ~1
    -- the coarticulation partition-energy invariant survives untouched. The
    caller re-enforces lip closures *after* smoothing, so a bilabial/labiodental
    seal that the filter would otherwise round off stays sharp (this is FaceFX's
    phoneme-influence toggle; see :mod:`openfacefx.coarticulation`).

  * :func:`time_shift` slides keyframe times by +/- seconds (lag/lead), clamped
    into the clip's ``[0, duration]`` envelope so the track never grows past it.

numpy + stdlib only; deterministic (no RNG): identical inputs give identical
output on any platform or Python version.
"""

from __future__ import annotations

import math
from typing import Iterable, Optional

import numpy as np

from .curves import FaceTrack, Keyframe


def _gaussian_kernel(sigma_frames: float) -> np.ndarray:
    """Unit-sum Gaussian sampled on the integer frame grid, truncated at 3 sigma
    (covers 99.7% of the weight). Symmetric, so it introduces no phase shift."""
    radius = max(int(math.ceil(sigma_frames * 3.0)), 1)
    x = np.arange(-radius, radius + 1, dtype=float)
    k = np.exp(-0.5 * (x / sigma_frames) ** 2)
    return k / k.sum()


def smooth_matrix(matrix: np.ndarray, sigma: float, fps: float) -> np.ndarray:
    """Temporally smooth a dense per-frame viseme ``matrix`` (shape
    ``(n_frames, n_targets)``) with a Gaussian of ``sigma`` seconds.

    Every channel is filtered with the same unit-sum kernel and edge-hold
    padding, so a constant signal is reproduced exactly: if each frame summed to
    ~1 before, it still does after (the partition-energy invariant is preserved),
    and values stay in ``[0, 1]`` (a weighted average of values in ``[0, 1]``).

    ``sigma <= 0`` (the default in :class:`~openfacefx.coarticulation.CoartParams`)
    or fewer than two frames returns the input array unchanged -- a byte-identical
    no-op -- so callers can wire this in without perturbing default output.
    """
    if sigma <= 0.0 or matrix.shape[0] < 2:
        return matrix
    sigma_frames = sigma * fps
    if sigma_frames <= 0.0:
        return matrix
    kernel = _gaussian_kernel(sigma_frames)
    radius = len(kernel) // 2
    out = np.empty_like(matrix)
    for v in range(matrix.shape[1]):
        padded = np.pad(matrix[:, v], radius, mode="edge")
        out[:, v] = np.convolve(padded, kernel, mode="valid")
    np.clip(out, 0.0, 1.0, out=out)
    return out


def time_shift(track: FaceTrack, seconds: float,
               channels: Optional[Iterable[str]] = None) -> FaceTrack:
    """Slide keyframe times by ``seconds`` in place, returning ``track``.

    ``seconds > 0`` delays the curves (visemes *lag* the audio); ``< 0`` advances
    them (visemes *lead*). Times are clamped into the clip's ``[0, duration]``
    envelope (measured before shifting), so a key never lands outside the clip
    and the track's duration is never stretched. Keys that pile onto a clamp
    boundary collapse to a single keyframe there.

    ``channels`` restricts the shift to the named channels (default: all). A
    per-channel shift leaves the other channels' keyframe objects untouched, and
    the track duration is preserved as long as some unshifted channel still holds
    a key at the end -- matching FaceFX's per-curve Lag/Lead. ``seconds == 0`` is
    a no-op (byte-identical).
    """
    if not seconds:
        return track
    dur = track.duration
    want = set(channels) if channels is not None else None
    for ch in track.channels:
        if want is not None and ch.name not in want:
            continue
        shifted: list = []
        for k in ch.keys:
            t = min(max(k.time + seconds, 0.0), dur)
            if shifted and abs(shifted[-1].time - t) < 1e-9:
                # Collapsed onto the previous key at a clamp boundary. Leading
                # keys pile at 0 (keep the later value); lagging keys pile at
                # duration (keep the earlier, already stored).
                if seconds < 0.0:
                    shifted[-1] = Keyframe(t, k.value)
                continue
            shifted.append(Keyframe(t, round(float(k.value), 4)))
        ch.keys = shifted
    return track
