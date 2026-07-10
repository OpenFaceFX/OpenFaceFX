"""Implementation of the gesture layers for :mod:`openfacefx.gestures`.

This is the private engine behind the public ``gestures`` module: the shared
curve/RNG math, the speech-event extraction (stress, pauses, energy peaks), and
the four channel builders (blinks, eyebrow flashes, head motion, gaze saccades).
:mod:`gestures` owns the ``GestureParams`` dataclass and the public entry points
and simply orchestrates the ``*_layer`` / ``*_events`` functions here, so the
tunable-dial contract lives in one place and this file stays pure implementation.

``params`` is duck-typed (every function just reads ``GestureParams`` attributes)
so this module imports no dataclass and there is no import cycle with
``gestures``. All randomness flows through :func:`rng` sub-streams, keyed by a
fixed stream id per layer (blinks 1, gaze 2, head 3), so the output is
deterministic and each layer is independent of the others' RNG. numpy + stdlib.
"""

from __future__ import annotations

from typing import List, Optional, Tuple

import numpy as np

from .curves import Channel, Keyframe, _rdp
from .phonemes import is_vowel, SILENCE
from .ipa import is_ipa_vowel

# Envelope-only pause detection (used only when there are no phoneme segments):
# a run below _PAUSE_LEVEL lasting at least _PAUSE_MIN_DUR is a pause. _STRESS_Z:
# the z-score sum above which a digit-less vowel counts as "stressed". _FIRE: a
# channel whose peak |value| is below this never fires and is dropped.
_PAUSE_LEVEL = 0.15
_PAUSE_MIN_DUR = 0.15
_STRESS_Z = 1.0
_FIRE = 1e-3


# --------------------------------------------------------------------------- #
# Shared math / RNG / channel helpers                                          #
# --------------------------------------------------------------------------- #

def rng(seed: int, stream: int) -> np.random.Generator:
    """An independent, reproducible sub-stream. ``[seed, stream]`` keeps each
    component's draws separate (so toggling one layer never shifts another's
    timing), and PCG64 gives identical output across Python/numpy versions."""
    return np.random.default_rng([int(seed), int(stream)])


def _smooth(x):
    """Smoothstep ``3x^2 - 2x^3`` on x in [0, 1] (scalar or array)."""
    return x * x * (3.0 - 2.0 * x)


def _zscore(x: np.ndarray) -> np.ndarray:
    sd = float(x.std())
    return (x - float(x.mean())) / sd if sd >= 1e-9 else np.zeros_like(x)


def _mean_env(times: np.ndarray, env: np.ndarray, a: float, b: float) -> float:
    """Mean envelope over the segment [a, b], sampled by interpolation."""
    if b <= a:
        return float(np.interp((a + b) / 2.0, times, env))
    return float(np.interp(np.linspace(a, b, 5), times, env).mean())


def nearest(t: float, xs, window: float) -> Optional[float]:
    """The element of ``xs`` closest to ``t`` within ``window``, else None."""
    best, bd = None, window
    for x in xs:
        d = abs(x - t)
        if d <= bd:
            best, bd = x, d
    return best


def _channel_or_none(name: str, keys, lo: float, hi: float) -> Optional[Channel]:
    """Channel from raw (time, value) pairs, clamped to [lo, hi] and rounded like
    the rest of the pipeline; None if it never fires (parity with
    ``reduce_to_track``'s silent-channel drop)."""
    ks = [Keyframe(round(float(t), 4), round(float(min(max(v, lo), hi)), 4))
          for t, v in keys]
    if not ks or max(abs(k.value) for k in ks) <= _FIRE:
        return None
    return Channel(name, ks)


def _rdp_channel(name, times, values, eps, lo, hi) -> Optional[Channel]:
    """RDP-thinned channel from a dense per-frame signal (the continuous brow and
    head channels only), clamped to [lo, hi]; None if it never fires."""
    v = np.clip(values, lo, hi)
    if float(np.max(np.abs(v))) <= _FIRE:
        return None
    idx = _rdp(times, v, eps)
    return Channel(name, [Keyframe(round(float(times[i]), 4), round(float(v[i]), 4))
                          for i in idx])


# --------------------------------------------------------------------------- #
# Step 0: stress / pause / energy-peak extraction (audio + timing, no ML)      #
# --------------------------------------------------------------------------- #

def stress_events(segments, env_times, env) -> List[Tuple[float, float]]:
    """``[(centre_time, strength)]`` for stressed-syllable centres.

    Prefers ARPABET stress digits: a vowel whose phoneme ends in ``1`` is a
    primary stress (strength 1.0). When the source carries no stress digits
    (IPA/vendor input), stress is derived from the audio -- each vowel's mean
    energy and duration are z-scored across all vowels and summed, and vowels
    above ``_STRESS_Z`` are stressed with strength rising with the score. That
    ties stress directly to ``energy.py``; with no envelope it degrades to a
    duration-only score. A vowel is anything ``is_vowel`` or ``is_ipa_vowel``
    accepts, so ARPABET and vendor IPA both work."""
    if not segments:
        return []
    vowels = [s for s in segments if is_vowel(s.phoneme) or is_ipa_vowel(s.phoneme)]
    if not vowels:
        return []
    if any(s.phoneme and s.phoneme[-1].isdigit() for s in vowels):
        return [((s.start + s.end) / 2.0, 1.0)
                for s in vowels if s.phoneme and s.phoneme[-1] == "1"]
    durs = np.array([s.dur for s in vowels], dtype=float)
    if env is not None and len(env):
        means = np.array([_mean_env(env_times, env, s.start, s.end) for s in vowels])
    else:
        means = np.zeros(len(vowels))
    score = _zscore(means) + _zscore(durs)
    return [((s.start + s.end) / 2.0, float(np.clip(sc / 3.0, 0.3, 1.0)))
            for s, sc in zip(vowels, score) if sc > _STRESS_Z]


def pause_times(segments, env_times, env) -> List[float]:
    """Pause centres: ``sil`` segment midpoints, or (envelope only) the middle of
    each quiet run at least ``_PAUSE_MIN_DUR`` long."""
    if segments:
        return [(s.start + s.end) / 2.0 for s in segments if s.phoneme == SILENCE]
    if env is None or not len(env):
        return []
    quiet = env < _PAUSE_LEVEL
    out: List[float] = []
    i, n = 0, len(env)
    while i < n:
        if not quiet[i]:
            i += 1
            continue
        j = i
        while j < n and quiet[j]:
            j += 1
        if env_times[j - 1] - env_times[i] >= _PAUSE_MIN_DUR:
            out.append((env_times[i] + env_times[j - 1]) / 2.0)
        i = j
    return out


def _side_base(env: np.ndarray, i: int, step: int) -> float:
    """Lowest envelope value walking from peak ``i`` in ``step`` direction until
    terrain rises to the peak or the clip ends -- one side of a topographic
    prominence."""
    peak, m, j = env[i], env[i], i + step
    while 0 <= j < len(env) and env[j] < peak:
        if env[j] < m:
            m = env[j]
        j += step
    return m


def energy_peaks(times, env, thresh, min_prom, min_spacing) -> List[Tuple[float, float]]:
    """``[(time, prominence)]`` for accent-carrying loudness peaks: local maxima
    above ``thresh`` with topographic prominence ``>= min_prom``, thinned greedily
    by prominence so accepted peaks are at least ``min_spacing`` apart."""
    if env is None or len(env) < 3:
        return []
    peaks: List[Tuple[float, float]] = []
    for i in range(1, len(env) - 1):
        if env[i] > env[i - 1] and env[i] >= env[i + 1] and env[i] > thresh:
            prom = env[i] - max(_side_base(env, i, -1), _side_base(env, i, 1))
            if prom >= min_prom:
                peaks.append((float(times[i]), float(prom)))
    peaks.sort(key=lambda p: p[1], reverse=True)
    kept: List[Tuple[float, float]] = []
    for t, prom in peaks:
        if all(abs(t - kt) >= min_spacing for kt, _ in kept):
            kept.append((t, prom))
    kept.sort()
    return kept


# --------------------------------------------------------------------------- #
# Step 1: blinks (Poisson process + biphasic physiological curve)              #
# --------------------------------------------------------------------------- #

def _poisson_times(rng_, duration, mean, min_gap, max_gap, start) -> List[float]:
    """Blink/saccade onset times: exponential inter-arrivals (a Poisson process)
    clamped to ``[min_gap, max_gap]`` so events never cluster tighter than the
    refractory floor. Starts accumulating from ``start``."""
    t, out = start, []
    while True:
        t += float(np.clip(rng_.exponential(mean), min_gap, max_gap))
        if t >= duration:
            break
        out.append(t)
    return out


def _snap_blinks(cands, pauses, stresses, params) -> List[float]:
    """Move each candidate onto a nearby pause (preferred) or stressed syllable
    within ``blink_snap_window``, then drop any left closer than ``blink_min_gap``
    (FaceFX: a blink lands on a pause / stressed syllable)."""
    w = params.blink_snap_window
    stress_t = [s for s, _ in stresses]
    snapped = []
    for t in cands:
        near = nearest(t, pauses, w) if params.blink_snap_pause else None
        if near is None and params.blink_snap_stress:
            near = nearest(t, stress_t, w)
        snapped.append(near if near is not None else t)
    snapped.sort()
    out: List[float] = []
    for t in snapped:
        if not out or t - out[-1] >= params.blink_min_gap:
            out.append(t)
    return out


def _blink_keys(t0, params, fps) -> List[Tuple[float, float]]:
    """Keyframes for one blink apex ``t0``: a fast smoothstep close then a slower
    smoothstep open, sampled per frame. Injected directly (never RDP-thinned) so
    the sharp fast edge survives."""
    amp = params.blink_amp
    keys: List[Tuple[float, float]] = []
    n = max(int(round(params.blink_close_dur * fps)), 1)
    for k in range(n + 1):
        x = k / n
        keys.append((t0 - params.blink_close_dur + x * params.blink_close_dur,
                     amp * _smooth(x)))
    n = max(int(round(params.blink_open_dur * fps)), 1)
    for k in range(1, n + 1):
        x = k / n
        keys.append((t0 + x * params.blink_open_dur, amp * (1.0 - _smooth(x))))
    return keys


def blink_layer(duration, fps, pauses, stresses, params
                ) -> Tuple[List[Channel], List[float]]:
    """The (blink_L, blink_R) channels plus the apex times (gaze locks onto them).
    Poisson candidates are snapped to pauses/stress; blink_R trails blink_L by
    ``blink_inter_eye_delay``."""
    cands = _poisson_times(rng(params.seed, 1), duration,
                           params.blink_mean_interval, params.blink_min_gap,
                           params.blink_max_gap,
                           start=params.blink_mean_interval * 0.5)
    apexes = _snap_blinks(cands, pauses, stresses, params)
    channels: List[Channel] = []
    for name, shift in (("blink_L", 0.0), ("blink_R", params.blink_inter_eye_delay)):
        keys: List[Tuple[float, float]] = []
        for t0 in apexes:
            keys.extend(_blink_keys(t0 + shift, params, fps))
        ch = _channel_or_none(name, keys, 0.0, 1.0)
        if ch is not None:
            channels.append(ch)
    return channels, apexes


# --------------------------------------------------------------------------- #
# Step 2: eyebrow raises (energy peaks, optionally stress-gated)               #
# --------------------------------------------------------------------------- #

def _add_flash(g, t, tp, a, p) -> None:
    """Accumulate one eyebrow flash onto grid ``g`` (max, so overlaps take the
    louder): smoothstep attack, flat sustain, smoothstep release."""
    m = (t >= tp - p.brow_attack) & (t <= tp)
    if m.any():
        g[m] = np.maximum(g[m], a * _smooth((t[m] - (tp - p.brow_attack)) / p.brow_attack))
    m = (t > tp) & (t <= tp + p.brow_sustain)
    g[m] = np.maximum(g[m], a)
    s0 = tp + p.brow_sustain
    m = (t > s0) & (t <= s0 + p.brow_release)
    if m.any():
        g[m] = np.maximum(g[m], a * (1.0 - _smooth((t[m] - s0) / p.brow_release)))


def brow_layer(peaks, duration, fps, stresses, params) -> List[Channel]:
    """browUp (or browInnerUp/browOuterUp) from the accepted energy peaks; if
    ``brow_require_stress`` only peaks near a stressed vowel survive. Amplitude
    scales with peak prominence; the dense grid is RDP-thinned."""
    if params.brow_require_stress and stresses:
        st = [s for s, _ in stresses]
        peaks = [(t, pr) for t, pr in peaks if nearest(t, st, 0.15) is not None]
    if not peaks:
        return []
    t = np.arange(int(round(duration * fps)) + 1) / fps
    g = np.zeros(len(t))
    span = params.brow_amp_max - params.brow_amp_min
    denom = max(1.0 - params.brow_min_prominence, 1e-9)
    for tp, prom in peaks:
        a = params.brow_amp_min + span * float(np.clip(
            (prom - params.brow_min_prominence) / denom, 0.0, 1.0))
        _add_flash(g, t, tp, a, params)
    if params.brow_split_inner_outer:
        chans = [_rdp_channel("browInnerUp", t, g, 0.01, 0.0, 1.0),
                 _rdp_channel("browOuterUp", t, 0.7 * g, 0.01, 0.0, 1.0)]
    else:
        chans = [_rdp_channel("browUp", t, g, 0.01, 0.0, 1.0)]
    return [c for c in chans if c is not None]


# --------------------------------------------------------------------------- #
# Step 3: head nod (on stress) + ambient sway                                  #
# --------------------------------------------------------------------------- #

def _sine_sum(p) -> float:
    return sum(1.0 / (k + 1) for k in range(len(p.head_ambient_freqs)))


def _ambient(t, p, rng_) -> np.ndarray:
    """Idle head drift: a sum of slow sines with random per-channel phases, so the
    head never freezes. Amplitude scaled by ``head_ambient_deg``."""
    s = np.zeros(len(t))
    for k, f in enumerate(p.head_ambient_freqs):
        s += (1.0 / (k + 1)) * np.sin(2.0 * np.pi * f * t + rng_.uniform(0.0, 2.0 * np.pi))
    return p.head_ambient_deg * s


def _nod_grid(t, stresses, p) -> np.ndarray:
    """Downward pitch dips on stressed syllables (positive = down), accumulated by
    max so overlapping nods stay bounded by ``head_pitch_deg``."""
    g = np.zeros(len(t))
    for ts, strength in stresses:
        a = p.head_pitch_deg * float(np.clip(strength, 0.3, 1.0))
        lo, peak = ts - 0.03, ts + p.head_nod_attack
        m = (t >= lo) & (t <= peak)
        if m.any():
            g[m] = np.maximum(g[m], a * (t[m] - lo) / (peak - lo))
        end = peak + p.head_nod_release
        m = (t > peak) & (t <= end)
        if m.any():
            g[m] = np.maximum(g[m], a * (1.0 - (t[m] - peak) / p.head_nod_release))
    return g


def _signed_channel(name, t, sig, ref, p) -> Optional[Channel]:
    """A signed head channel: degrees, or normalized to [-1, 1] by ``ref`` when
    ``head_eye_in_degrees`` is off. RDP-thinned (a smooth continuous curve)."""
    if p.head_eye_in_degrees:
        return _rdp_channel(name, t, sig, 0.02, -1e9, 1e9)
    return _rdp_channel(name, t, sig / max(ref, 1e-9), 0.01, -1.0, 1.0)


def head_layer(stresses, duration, fps, params) -> List[Channel]:
    """headPitch / headYaw / headRoll: ambient sway (independent per-channel sine
    phases from the head stream) with stress nods added onto pitch."""
    t = np.arange(int(round(duration * fps)) + 1) / fps
    r = rng(params.seed, 3)
    zero = np.zeros(len(t))
    # Ambient phases drawn per channel in a fixed order from the head stream.
    pitch = _ambient(t, params, r) if params.head_ambient else zero.copy()
    yaw = _ambient(t, params, r) if params.head_ambient else zero.copy()
    roll = _ambient(t, params, r) if params.head_ambient else zero.copy()
    if params.head_nod_on_stress and stresses:
        pitch = pitch + _nod_grid(t, stresses, params)
    s = _sine_sum(params)
    ref_pitch = params.head_pitch_deg + params.head_ambient_deg * s
    ref_sway = params.head_ambient_deg * s
    chans = [_signed_channel("headPitch", t, pitch, ref_pitch, params),
             _signed_channel("headYaw", t, yaw, ref_sway, params),
             _signed_channel("headRoll", t, roll, ref_sway, params)]
    return [c for c in chans if c is not None]


# --------------------------------------------------------------------------- #
# Step 4: gaze saccades (step fixations, occasionally blink-locked)            #
# --------------------------------------------------------------------------- #

def _gaze_channel(name, keys, deg, p) -> Optional[Channel]:
    """A step (hold-then-snap) eye channel -- never RDP-thinned. Degrees, or
    normalized to [-1, 1] by the max amplitude ``deg``."""
    if p.head_eye_in_degrees:
        return _channel_or_none(name, keys, -deg, deg)
    return _channel_or_none(name, [(t, v / deg) for t, v in keys], -1.0, 1.0)


def gaze_layer(duration, fps, blink_apexes, params) -> List[Channel]:
    """eyeYaw / eyePitch step fixations: Poisson saccades to Gaussian targets
    clipped to the yaw/pitch bounds; every ``gaze_align_blink_every``-th saccade
    snaps onto a blink apex (gaze-evoked blink co-occurrence)."""
    r = rng(params.seed, 2)
    times = _poisson_times(r, duration, params.gaze_mean_interval,
                           params.gaze_min_gap, np.inf, start=0.0)
    every = params.gaze_align_blink_every
    if blink_apexes and every > 0:
        for i in range(every - 1, len(times), every):
            near = nearest(times[i], blink_apexes, float("inf"))
            if near is not None:
                times[i] = near
    times = sorted(set(round(x, 6) for x in times))  # snapping may collide/reorder
    yaw_keys: List[Tuple[float, float]] = [(0.0, 0.0)]
    pitch_keys: List[Tuple[float, float]] = [(0.0, 0.0)]
    cy = cp = 0.0
    for t in times:
        ty = float(np.clip(r.normal(0.0, params.gaze_yaw_deg / 2.0),
                           -params.gaze_yaw_deg, params.gaze_yaw_deg))
        tp = float(np.clip(r.normal(0.0, params.gaze_pitch_deg / 2.0),
                           -params.gaze_pitch_deg, params.gaze_pitch_deg))
        yaw_keys += [(t, cy), (t + params.gaze_saccade_dur, ty)]
        pitch_keys += [(t, cp), (t + params.gaze_saccade_dur, tp)]
        cy, cp = ty, tp
    chans = [_gaze_channel("eyeYaw", yaw_keys, params.gaze_yaw_deg, params),
             _gaze_channel("eyePitch", pitch_keys, params.gaze_pitch_deg, params)]
    return [c for c in chans if c is not None]
