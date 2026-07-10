"""Procedural non-verbal gestures: blinks, brow flashes, head and eye motion.

A face driven by visemes alone reads as a talking mask -- the eyes stare, the
brow is frozen, the head is bolted in place. This module layers the *other*
channels a believable performance needs (eye blinks, eyebrow raises, head nods
and idle sway, gaze saccades) onto a finished lip-sync track, the way
JALI/JAmbient, SmartBody's NVBG and FaceFX's analysis actor do: couple
non-verbal timing to the speech itself. Two deterministic layers combine -- a
*stochastic* timing layer (Poisson eye blinks and gaze saccades, quasi-periodic
ambient head drift) drawn from ``np.random.default_rng`` seeded by
``params.seed`` (default 0), and an *audio-driven* event layer (eyebrow flashes
and head nods on energy peaks / stressed syllables, reusing
``energy.energy_envelope`` and the phoneme-segment stress the pipeline computes).

Nothing is learned; blink rate, curve shape and amplitudes come from published
human baselines (~15 blinks/min, a biphasic fast-close/slow-open lid, 30-80ms
saccades, brow raises and nods co-occurring with pitch/energy accents; issue #5).
Each stochastic component draws from its OWN sub-stream (``default_rng([seed,
k])``), so toggling one feature never shifts another's timing, and PCG64
reproduces bit-for-bit across Python/numpy versions.

Gestures are OPT-IN and appended *after* viseme reduction, so an ordinary track
is byte-identical unless asked for and the mouth channels are never touched.
Blink/brow channels carry a [0,1] weight; head/eye channels are signed pose
channels in degrees by default (``head_eye_in_degrees``; +headPitch = down,
+eyeYaw = subject's left) or signed [-1,1] when off. They are NOT visemes: the
mouth-only exporters (cues, .lip) ignore them and ``retarget`` passes them
through by name (GESTURE_CHANNELS).

This module holds the ``GestureParams`` dial-set and the public entry points;
the per-layer math lives in :mod:`openfacefx.gestures_layers`. numpy + stdlib.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Tuple

import numpy as np

from .curves import Channel, FaceTrack
from .alignment import PhonemeSegment  # noqa: F401  (documents the segment type)
from .visemes import VISEMES
from . import gestures_layers as _gl


@dataclass
class GestureParams:
    """Artistic dials for the gesture layer (mirrors ``CoartParams`` ergonomics).

    Conservative defaults deliberately under-animate -- a relaxed ~15 blinks/min,
    2-6deg head nods, 1-2deg idle sway -- so the result reads calm, not twitchy.
    Every timing draw derives from ``seed``.
    """
    seed: int = 0
    # blinks
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
    # brows
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
    # head
    head_nod_on_stress: bool = True
    head_pitch_deg: float = 4.0           # max downward nod amplitude
    head_nod_attack: float = 0.15
    head_nod_release: float = 0.25
    head_ambient: bool = True
    head_ambient_deg: float = 1.5         # idle sway amplitude scale
    head_ambient_freqs: Tuple[float, ...] = (0.13, 0.27, 0.41)  # slow drift (Hz)
    # gaze
    gaze_enable: bool = True
    gaze_mean_interval: float = 2.0
    gaze_min_gap: float = 0.7
    gaze_saccade_dur: float = 0.04        # near-instant step (30-80ms saccade)
    gaze_yaw_deg: float = 8.0
    gaze_pitch_deg: float = 5.0
    gaze_align_blink_every: int = 3       # every Nth saccade snaps to a blink
    # units
    head_eye_in_degrees: bool = True      # False => signed [-1, 1] pose channels


#: Every channel name this module can emit -- pose/expression channels, disjoint
#: from any viseme vocabulary, so mouth-only exporters filter them out and
#: ``retarget`` passes them through by name.
GESTURE_CHANNELS = frozenset({
    "blink_L", "blink_R", "browUp", "browInnerUp", "browOuterUp",
    "headPitch", "headYaw", "headRoll", "eyePitch", "eyeYaw",
})


def generate_gestures(duration: float, fps: float = 60.0,
                      env_times: Optional[np.ndarray] = None,
                      env: Optional[np.ndarray] = None,
                      segments: Optional[List[PhonemeSegment]] = None,
                      params: Optional[GestureParams] = None) -> List[Channel]:
    """Build the non-verbal gesture channels for a clip of ``duration`` seconds.

    ``env_times``/``env`` are an ``energy.energy_envelope`` result (drives brow
    flashes and, absent stress digits, stress); pass None to omit the audio
    layer. ``segments`` supply stress digits and pause boundaries. Returns
    :class:`curves.Channel`\\ s in a stable order (blink_L, blink_R, brow*,
    headPitch/Yaw/Roll, eyeYaw, eyePitch), dropping any channel that never fires.
    Fully deterministic in ``params.seed``."""
    params = params or GestureParams()
    if duration <= 0:
        return []
    env_times = np.asarray(env_times, dtype=float) if env_times is not None else np.zeros(0)
    env = np.asarray(env, dtype=float) if env is not None else np.zeros(0)

    stresses = _gl.stress_events(segments, env_times, env)
    pauses = _gl.pause_times(segments, env_times, env)
    peaks = (_gl.energy_peaks(env_times, env, params.brow_energy_thresh,
                              params.brow_min_prominence, params.brow_min_spacing)
             if len(env) else [])

    channels: List[Channel] = []
    apexes: List[float] = []
    if params.blink_enable:
        blinks, apexes = _gl.blink_layer(duration, fps, pauses, stresses, params)
        channels.extend(blinks)
    if params.brow_enable:
        channels.extend(_gl.brow_layer(peaks, duration, fps, stresses, params))
    channels.extend(_gl.head_layer(stresses, duration, fps, params))
    if params.gaze_enable:
        channels.extend(_gl.gaze_layer(duration, fps, apexes, params))
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
    """Append gesture channels to ``track`` (at the track's own fps) and extend
    ``target_set`` with their names so downstream consumers see a complete
    vocabulary. Mouth channels are untouched. Returns ``track``."""
    chans = generate_gestures(duration, track.fps, env_times, env, segments, params)
    if not chans:
        return track
    base = list(track.target_set) if track.target_set is not None else list(VISEMES)
    track.channels.extend(chans)
    track.target_set = base + [c.name for c in chans]
    return track
