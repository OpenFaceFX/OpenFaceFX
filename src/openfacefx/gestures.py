"""Procedural non-verbal gestures: blinks, brow flashes, head and eye motion.

A face driven by visemes alone reads as a talking mask: the eyes stare, the brow
is frozen, the head is bolted in place. This module layers the *other* channels
a believable performance needs -- eye blinks, eyebrow raises, head nods and idle
sway, gaze saccades -- onto a finished lip-sync track, the way JALI/JAmbient,
SmartBody's NVBG and FaceFX's analysis actor do: couple non-verbal timing to the
speech itself. Two layers combine, both fully deterministic:

  * a *stochastic* timing layer -- Poisson eye blinks and gaze saccades, plus a
    quasi-periodic ambient head drift -- drawn from ``np.random.default_rng``
    seeded by ``params.seed`` (default 0); and
  * an *audio-driven* event layer -- eyebrow flashes and head nods fire on energy
    peaks and stressed syllables, reusing ``energy.energy_envelope`` and the
    phoneme-segment stress the rest of the pipeline already computes.

Nothing is learned; blink rate, curve shape and amplitudes come from published
human baselines (~15 blinks/min, a biphasic fast-close/slow-open lid, 30-80ms
saccades, brow raises and nods co-occurring with pitch/energy accents; issue #5).
Each stochastic component draws from its OWN sub-stream (``default_rng([seed,
k])``), so toggling one feature never shifts another's timing, and PCG64
reproduces bit-for-bit across Python/numpy versions.

Gestures are OPT-IN and appended *after* viseme reduction, so an ordinary track
is byte-identical unless asked for and the mouth channels are never touched.
Blink/brow channels carry a [0,1] blendshape weight; head/eye channels are signed
pose channels in degrees by default (``head_eye_in_degrees``; +headPitch = down,
+eyeYaw = subject's left) or a signed [-1,1] range when that flag is off. They
are NOT visemes: the mouth-only exporters (Rhubarb cues, Bethesda .lip) ignore
them and ``retarget`` passes them through (see GESTURE_CHANNELS). numpy + stdlib.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Tuple

import numpy as np

from .curves import Channel, FaceTrack, Keyframe, _rdp
from .alignment import PhonemeSegment  # noqa: F401  (documents the segment type)
from .phonemes import is_vowel, SILENCE
from .ipa import is_ipa_vowel
from .visemes import VISEMES


@dataclass
class GestureParams:
    """Artistic dials for the gesture layer (mirrors ``CoartParams`` ergonomics).

    Conservative defaults deliberately under-animate: a relaxed ~15 blinks/min,
    2-6deg head nods and 1-2deg idle sway, so the result reads calm rather than
    twitchy. Every timing draw derives from ``seed``.
    """
    seed: int = 0
    # blinks -------------------------------------------------------------
    blink_enable: bool = True
    blink_mean_interval: float = 4.0      # mean inter-blink gap (s) => ~15/min
    blink_min_gap: float = 1.5            # refractory floor between blinks
    blink_max_gap: float = 8.0
    blink_close_dur: float = 0.05         # fast down-phase (lid closing)
    blink_open_dur: float = 0.10          # slower up-phase (lid re-opening)
    blink_amp: float = 1.0
    blink_inter_eye_delay: float = 0.008  # R eye trails L, so it isn't robotic
    blink_snap_pause: bool = True
    blink_snap_stress: bool = True
    blink_snap_window: float = 0.25       # a blink may move this far onto speech
    # brows --------------------------------------------------------------
    brow_enable: bool = True
    brow_energy_thresh: float = 0.55      # envelope level a peak must exceed
    brow_min_prominence: float = 0.15
    brow_min_spacing: float = 0.40        # min gap between accepted brow flashes
    brow_attack: float = 0.12
    brow_sustain: float = 0.08
    brow_release: float = 0.22
    brow_amp_min: float = 0.3
    brow_amp_max: float = 1.0
    brow_require_stress: bool = False     # keep only peaks near a stressed vowel
    brow_split_inner_outer: bool = False  # browInnerUp + 0.7*browOuterUp
    # head ---------------------------------------------------------------
    head_nod_on_stress: bool = True
    head_pitch_deg: float = 4.0           # max downward nod amplitude
    head_nod_attack: float = 0.15
    head_nod_release: float = 0.25
    head_ambient: bool = True
    head_ambient_deg: float = 1.5         # idle sway amplitude scale
    head_ambient_freqs: Tuple[float, ...] = (0.13, 0.27, 0.41)  # slow drift (Hz)
    # gaze ---------------------------------------------------------------
    gaze_enable: bool = True
    gaze_mean_interval: float = 2.0
    gaze_min_gap: float = 0.7
    gaze_saccade_dur: float = 0.04        # near-instant step (30-80ms saccade)
    gaze_yaw_deg: float = 8.0
    gaze_pitch_deg: float = 5.0
    gaze_align_blink_every: int = 3       # every Nth saccade snaps to a blink
    # units --------------------------------------------------------------
    head_eye_in_degrees: bool = True      # False => signed [-1, 1] pose channels


#: Every channel name this module can emit. These are pose/expression channels,
#: disjoint from any viseme/mouth-shape vocabulary, so mouth-only exporters can
#: filter them out and ``retarget`` can pass them through by name.
GESTURE_CHANNELS = frozenset({
    "blink_L", "blink_R", "browUp", "browInnerUp", "browOuterUp",
    "headPitch", "headYaw", "headRoll", "eyePitch", "eyeYaw",
})

# Envelope-only pause detection: a run below this level lasting at least this
# long is treated as a pause (used only when there are no phoneme segments).
_PAUSE_LEVEL = 0.15
_PAUSE_MIN_DUR = 0.15
# Energy+duration stress: a vowel scoring above this (sum of z-scores) is
# "stressed" when the source carries no ARPABET stress digits.
_STRESS_Z = 1.0
_FIRE = 1e-3  # a channel whose peak |value| is below this never fires (dropped)


# --- Small shared helpers ---------------------------------------------------

def _rng(seed: int, stream: int) -> np.random.Generator:
    """An independent, reproducible sub-stream. ``[seed, stream]`` keeps each
    component's draws separate so enabling/disabling one never perturbs another,
    and PCG64 gives identical output across Python/numpy versions."""
    return np.random.default_rng([int(seed), int(stream)])


def _smooth(x):
    """Smoothstep ``3x^2 - 2x^3`` on x in [0, 1] (scalar or array)."""
    return x * x * (3.0 - 2.0 * x)


def _zscore(x: np.ndarray) -> np.ndarray:
    sd = float(x.std())
    if sd < 1e-9:
        return np.zeros_like(x)
    return (x - float(x.mean())) / sd


def _mean_env(times: np.ndarray, env: np.ndarray, a: float, b: float) -> float:
    """Mean envelope over the segment [a, b], sampled by interpolation."""
    if b <= a:
        return float(np.interp((a + b) / 2.0, times, env))
    return float(np.interp(np.linspace(a, b, 5), times, env).mean())


def _nearest(t: float, xs, window: float) -> Optional[float]:
    """The element of ``xs`` closest to ``t`` within ``window``, else None."""
    best, bd = None, window
    for x in xs:
        d = abs(x - t)
        if d <= bd:
            best, bd = x, d
    return best


def _channel_or_none(name: str, keys, lo: float, hi: float) -> Optional[Channel]:
    """Build a Channel from raw (time, value) pairs, clamped to [lo, hi] and
    rounded like the rest of the pipeline; None if it never fires."""
    ks = [Keyframe(round(float(t), 4), round(float(min(max(v, lo), hi)), 4))
          for t, v in keys]
    if not ks or max(abs(k.value) for k in ks) <= _FIRE:
        return None
    return Channel(name, ks)


def _rdp_channel(name: str, times: np.ndarray, values: np.ndarray, eps: float,
                 lo: float, hi: float) -> Optional[Channel]:
    """Thin a dense per-frame signal with RDP (the continuous brow/head channels
    only), clamped to [lo, hi]; None if it never fires."""
    v = np.clip(values, lo, hi)
    if float(np.max(np.abs(v))) <= _FIRE:
        return None
    idx = _rdp(times, v, eps)
    return Channel(name, [Keyframe(round(float(times[i]), 4), round(float(v[i]), 4))
                          for i in idx])


# --- Step 0: stress / pause / energy-peak extraction (audio + timing, no ML) -

def _stress_events(segments, env_times, env) -> List[Tuple[float, float]]:
    """``[(centre_time, strength)]`` for stressed-syllable centres.

    Prefers ARPABET stress digits: a vowel whose phoneme ends in ``1`` is a
    primary stress (strength 1.0). When the source carries no stress digits
    (IPA/vendor input), stress is derived from the audio -- each vowel's mean
    energy and duration are z-scored across all vowels and summed, and vowels
    above ``_STRESS_Z`` are stressed with strength rising with the score. That
    ties stress directly to ``energy.py``; with no envelope it degrades to a
    duration-only score."""
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


def _pause_times(segments, env_times, env) -> List[float]:
    """Pause centres: ``sil`` segment midpoints, or (envelope only) the middle
    of each quiet run at least ``_PAUSE_MIN_DUR`` long."""
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
    peak = env[i]
    m = peak
    j = i + step
    while 0 <= j < len(env) and env[j] < peak:
        if env[j] < m:
            m = env[j]
        j += step
    return m


def _energy_peaks(times, env, thresh, min_prom, min_spacing
                  ) -> List[Tuple[float, float]]:
    """``[(time, prominence)]`` for accent-carrying loudness peaks: local maxima
    above ``thresh`` with topographic prominence ``>= min_prom``, thinned
    greedily by prominence so accepted peaks are at least ``min_spacing`` apart."""
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

def _poisson_times(rng, duration, mean, min_gap, max_gap, start) -> List[float]:
    """Blink/saccade onset times: exponential inter-arrivals (a Poisson process)
    clamped to ``[min_gap, max_gap]`` so events never cluster tighter than the
    refractory floor. Starts accumulating from ``start``."""
    t = start
    out: List[float] = []
    while True:
        t += float(np.clip(rng.exponential(mean), min_gap, max_gap))
        if t >= duration:
            break
        out.append(t)
    return out


def _snap_blinks(cands, pauses, stresses, params) -> List[float]:
    """Move each candidate onto a nearby pause (preferred) or stressed syllable
    within ``blink_snap_window``, then drop any left closer than
    ``blink_min_gap`` (FaceFX: a blink lands on a pause / stressed syllable)."""
    w = params.blink_snap_window
    stress_t = [s for s, _ in stresses]
    snapped: List[float] = []
    for t in cands:
        near = _nearest(t, pauses, w) if params.blink_snap_pause else None
        if near is None and params.blink_snap_stress:
            near = _nearest(t, stress_t, w)
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


def _blink_channel(name, apexes, params, fps, shift) -> Optional[Channel]:
    keys: List[Tuple[float, float]] = []
    for t0 in apexes:
        keys.extend(_blink_keys(t0 + shift, params, fps))
    return _channel_or_none(name, keys, 0.0, 1.0)


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


def _brow_channels(peaks, duration, fps, params) -> List[Channel]:
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


def _ambient(t, p, rng) -> np.ndarray:
    """Idle head drift: a sum of slow sines with random per-channel phases, so
    the head never freezes. Amplitude scaled by ``head_ambient_deg``."""
    s = np.zeros(len(t))
    for k, f in enumerate(p.head_ambient_freqs):
        s += (1.0 / (k + 1)) * np.sin(2.0 * np.pi * f * t + rng.uniform(0.0, 2.0 * np.pi))
    return p.head_ambient_deg * s


def _nod_grid(t, stresses, p) -> np.ndarray:
    """Downward pitch dips on stressed syllables (positive = down), accumulated
    by max so overlapping nods stay bounded by ``head_pitch_deg``."""
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
    """Emit a signed head channel in degrees, or normalized to [-1, 1] by
    dividing by its reference amplitude ``ref`` when ``head_eye_in_degrees`` is
    off. RDP-thinned (it's a smooth continuous curve)."""
    if p.head_eye_in_degrees:
        return _rdp_channel(name, t, sig, 0.02, -1e9, 1e9)
    return _rdp_channel(name, t, sig / max(ref, 1e-9), 0.01, -1.0, 1.0)


def _head_channels(stresses, duration, fps, params) -> List[Channel]:
    t = np.arange(int(round(duration * fps)) + 1) / fps
    rng = _rng(params.seed, 3)
    zero = np.zeros(len(t))
    # Draw ambient phases per channel, in a fixed order, from the head stream.
    pitch = _ambient(t, params, rng) if params.head_ambient else zero.copy()
    yaw = _ambient(t, params, rng) if params.head_ambient else zero.copy()
    roll = _ambient(t, params, rng) if params.head_ambient else zero.copy()
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
    normalized to [-1, 1] by dividing by the max amplitude ``deg``."""
    if p.head_eye_in_degrees:
        return _channel_or_none(name, keys, -deg, deg)
    return _channel_or_none(name, [(t, v / deg) for t, v in keys], -1.0, 1.0)


def _gaze_channels(duration, fps, blink_apexes, params) -> List[Channel]:
    rng = _rng(params.seed, 2)
    times = _poisson_times(rng, duration, params.gaze_mean_interval,
                           params.gaze_min_gap, np.inf, start=0.0)
    every = params.gaze_align_blink_every
    if blink_apexes and every > 0:
        for i in range(every - 1, len(times), every):
            near = _nearest(times[i], blink_apexes, float("inf"))
            if near is not None:
                times[i] = near
    times = sorted(set(round(x, 6) for x in times))  # snapping may collide/reorder
    yaw_keys: List[Tuple[float, float]] = [(0.0, 0.0)]
    pitch_keys: List[Tuple[float, float]] = [(0.0, 0.0)]
    cy = cp = 0.0
    for t in times:
        ty = float(np.clip(rng.normal(0.0, params.gaze_yaw_deg / 2.0),
                           -params.gaze_yaw_deg, params.gaze_yaw_deg))
        tp = float(np.clip(rng.normal(0.0, params.gaze_pitch_deg / 2.0),
                           -params.gaze_pitch_deg, params.gaze_pitch_deg))
        yaw_keys += [(t, cy), (t + params.gaze_saccade_dur, ty)]
        pitch_keys += [(t, cp), (t + params.gaze_saccade_dur, tp)]
        cy, cp = ty, tp
    chans = [_gaze_channel("eyeYaw", yaw_keys, params.gaze_yaw_deg, params),
             _gaze_channel("eyePitch", pitch_keys, params.gaze_pitch_deg, params)]
    return [c for c in chans if c is not None]


# --------------------------------------------------------------------------- #
# Assembly / public API                                                        #
# --------------------------------------------------------------------------- #

def generate_gestures(duration: float, fps: float = 60.0,
                      env_times: Optional[np.ndarray] = None,
                      env: Optional[np.ndarray] = None,
                      segments: Optional[List[PhonemeSegment]] = None,
                      params: Optional[GestureParams] = None) -> List[Channel]:
    """Build the non-verbal gesture channels for a clip of ``duration`` seconds.

    ``env_times``/``env`` are an ``energy.energy_envelope`` result (drives brow
    flashes, energy peaks and -- absent stress digits -- stress); pass None to
    omit the audio layer. ``segments`` are ``PhonemeSegment``s (supply stress
    digits and pause boundaries). Returns a list of :class:`curves.Channel` in a
    stable order (blink_L, blink_R, brow*, headPitch/Yaw/Roll, eyeYaw,
    eyePitch), with any channel that never fires dropped -- exactly as
    ``reduce_to_track`` drops silent viseme channels. Fully deterministic in
    ``params.seed``."""
    params = params or GestureParams()
    if duration <= 0:
        return []
    env_times = np.asarray(env_times, dtype=float) if env_times is not None else np.zeros(0)
    env = np.asarray(env, dtype=float) if env is not None else np.zeros(0)

    stresses = _stress_events(segments, env_times, env)
    pauses = _pause_times(segments, env_times, env)
    peaks = (_energy_peaks(env_times, env, params.brow_energy_thresh,
                           params.brow_min_prominence, params.brow_min_spacing)
             if len(env) else [])

    channels: List[Channel] = []
    apexes: List[float] = []
    if params.blink_enable:
        cands = _poisson_times(_rng(params.seed, 1), duration,
                               params.blink_mean_interval, params.blink_min_gap,
                               params.blink_max_gap,
                               start=params.blink_mean_interval * 0.5)
        apexes = _snap_blinks(cands, pauses, stresses, params)
        for name, shift in (("blink_L", 0.0),
                            ("blink_R", params.blink_inter_eye_delay)):
            ch = _blink_channel(name, apexes, params, fps, shift)
            if ch is not None:
                channels.append(ch)

    if params.brow_enable:
        pk = peaks
        if params.brow_require_stress and stresses:
            st = [s for s, _ in stresses]
            pk = [(t, pr) for t, pr in peaks if _nearest(t, st, 0.15) is not None]
        channels.extend(_brow_channels(pk, duration, fps, params))

    channels.extend(_head_channels(stresses, duration, fps, params))

    if params.gaze_enable:
        channels.extend(_gaze_channels(duration, fps, apexes, params))

    return channels


def gestures_from_wav(wav_path: str, duration: float, fps: float = 60.0,
                      segments: Optional[List[PhonemeSegment]] = None,
                      params: Optional[GestureParams] = None) -> List[Channel]:
    """Convenience wrapper: compute the loudness envelope from ``wav_path`` (via
    :func:`energy.energy_envelope`) and generate gestures from it."""
    from .energy import energy_envelope
    times, env = energy_envelope(wav_path, fps=fps)
    return generate_gestures(duration, fps, times, env, segments, params)


def add_gestures_to_track(track: FaceTrack, duration: float,
                          env_times: Optional[np.ndarray] = None,
                          env: Optional[np.ndarray] = None,
                          segments: Optional[List[PhonemeSegment]] = None,
                          params: Optional[GestureParams] = None) -> FaceTrack:
    """Append gesture channels to ``track`` (sampled at the track's own fps) and
    extend ``target_set`` with their names so downstream consumers see a
    complete vocabulary. Mouth channels are untouched. Returns ``track``."""
    chans = generate_gestures(duration, track.fps, env_times, env, segments, params)
    if not chans:
        return track
    base = list(track.target_set) if track.target_set is not None else list(VISEMES)
    track.channels.extend(chans)
    track.target_set = base + [c.name for c in chans]
    return track


def split_gesture_channels(track: FaceTrack) -> Tuple[List[Channel], List[Channel]]:
    """Partition ``track.channels`` into (mouth channels, gesture channels).
    Used by retargeting to route the viseme channels through the rig map while
    passing pose channels through unchanged."""
    mouth = [c for c in track.channels if c.name not in GESTURE_CHANNELS]
    gest = [c for c in track.channels if c.name in GESTURE_CHANNELS]
    return mouth, gest
