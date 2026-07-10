"""Audio-energy lip-sync fallback for untranscripted speech.

When there is no transcript and no aligner output, you can still drive a
believable *flapping* mouth straight from the loudness of the audio. This is
the single most common non-ML lip-sync mechanism in the wild — SALSA remaps RMS
amplitude through low/high cutoffs, Moho drives openness from instantaneous
loudness, Live2D bakes RMS volume into a mouth-open parameter. This module is
OpenFaceFX's version of that idea.

    PCM samples -> per-frame RMS -> noise-gated, robustly-normalized envelope
                -> asymmetric attack/release smoothing -> mouth-open curves

**This is an energy fallback, not lip-sync.** It knows nothing about phonemes
or visemes: it cannot tell a /m/ from an /aa/, and it will happily open the
mouth on a cough. It drives one primary jaw-open channel plus a small, purely
*aesthetic* spread into two secondary mouth shapes so the result does not read
as a single channel flapping on and off. Use the transcript-based pipeline
(``generate_naive`` / ``generate_from_alignment``) whenever you have text; use
this only when you have nothing but a WAV.

Only numpy and the stdlib ``wave`` module are used. Input must be 16-bit PCM
WAV (mono or stereo — stereo is downmixed); convert other codecs first, e.g.
``ffmpeg -i in.mp3 -c:a pcm_s16le -ar 16000 out.wav``.
"""

from __future__ import annotations

import contextlib
import wave
from typing import Optional, Tuple

import numpy as np

from .curves import reduce_to_track, FaceTrack
from .mapping import Mapping
from .visemes import VISEMES

# Robust-normalization reference: scale the envelope against this percentile of
# the frame RMS rather than the max, so one plosive/clap does not flatten the
# rest of the take to near-zero.
_REF_PERCENTILE = 95.0

# Below this reference RMS (on a signal normalized to [-1, 1], ~ -60 dBFS) the
# whole clip is treated as silence and the envelope is all zeros.
_SILENCE_REF = 1e-3


def _read_wav_mono(path: str) -> Tuple[np.ndarray, int]:
    """Read a 16-bit PCM WAV as a float signal in [-1, 1] and its sample rate.

    Stereo (or more) is downmixed to mono by averaging channels. Any sample
    width other than 16-bit raises ``ValueError`` — the ``wave`` module gives
    us raw PCM only, and widening 8/24/32-bit correctly is out of scope for a
    stdlib-only reader.
    """
    with contextlib.closing(wave.open(path, "rb")) as w:
        n_channels = w.getnchannels()
        width = w.getsampwidth()
        rate = w.getframerate()
        raw = w.readframes(w.getnframes())
    if width != 2:
        raise ValueError(
            f"energy mode needs a 16-bit PCM WAV; got {width * 8}-bit "
            f"(sample width {width} bytes). Convert first, e.g. "
            f"`ffmpeg -i in.wav -c:a pcm_s16le out.wav`.")
    data = np.frombuffer(raw, dtype="<i2").astype(np.float64)
    if n_channels > 1:
        # Trailing partial frame (shouldn't happen for valid WAV) is trimmed.
        usable = (len(data) // n_channels) * n_channels
        data = data[:usable].reshape(-1, n_channels).mean(axis=1)
    return data / 32768.0, rate


def _frame_rms(signal: np.ndarray, rate: int, fps: float,
               window: float) -> np.ndarray:
    """RMS of ``signal`` in a window of ``window`` seconds centred on each
    output frame (one frame every ``1/fps`` seconds). Vectorised via a running
    sum of the squared signal."""
    duration = len(signal) / float(rate)
    n = max(int(round(duration * fps)) + 1, 1)
    if len(signal) == 0:
        return np.zeros(n)
    power = signal * signal
    csum = np.concatenate(([0.0], np.cumsum(power)))
    centres = np.round(np.arange(n) * rate / fps).astype(np.int64)
    half = max(int(round(window * rate)) // 2, 1)
    lo = np.clip(centres - half, 0, len(signal))
    hi = np.clip(centres + half, 0, len(signal))
    counts = np.maximum(hi - lo, 1)
    return np.sqrt((csum[hi] - csum[lo]) / counts)


def _attack_release(env: np.ndarray, fps: float,
                    attack: float, release: float) -> np.ndarray:
    """One-pole envelope follower with a fast attack and a slower release, so
    the mouth opens quickly and closes gradually (mouths snap open on an onset
    but relax shut). ``attack``/``release`` are time constants in seconds."""
    a_att = np.exp(-1.0 / (max(attack, 1e-6) * fps))
    a_rel = np.exp(-1.0 / (max(release, 1e-6) * fps))
    out = np.empty_like(env)
    prev = 0.0
    for i, x in enumerate(env):
        coef = a_att if x > prev else a_rel
        prev = coef * prev + (1.0 - coef) * x
        out[i] = prev
    return out


def energy_envelope(
    wav_path: str,
    fps: float = 60.0,
    window: Optional[float] = None,
    gate: float = 0.06,
    smoothing: Tuple[float, float] = (0.012, 0.09),
) -> Tuple[np.ndarray, np.ndarray]:
    """Loudness envelope of a WAV, sampled at ``fps``, as ``(times, envelope)``.

    ``envelope`` is in ``[0, 1]``: per-frame RMS, noise-gated and normalized
    against a high percentile (not the max — see ``_REF_PERCENTILE``), then run
    through an asymmetric attack/release follower.

    Parameters
    ----------
    window : RMS analysis window in seconds (default ``2 / fps`` — a mild
        overlap between frames). Larger = smoother, blurrier.
    gate : noise-floor gate as a fraction of the reference level in ``[0, 1)``.
        Frames quieter than ``gate * reference`` read as full silence; the
        remaining range is stretched back to ``[0, 1]`` (a SALSA-style
        low/high cutoff remap ``(v - lo) / (hi - lo)``).
    smoothing : ``(attack_seconds, release_seconds)`` for the envelope
        follower. The default opens fast and closes ~7x slower.

    A clip whose reference level is below ``_SILENCE_REF`` is all silence and
    returns an all-zero envelope.
    """
    if not (0.0 <= gate < 1.0):
        raise ValueError(f"gate must be in [0, 1), got {gate}")
    signal, rate = _read_wav_mono(wav_path)
    win = window if window is not None else 2.0 / fps
    rms = _frame_rms(signal, rate, fps, win)
    times = np.arange(len(rms)) / fps

    ref = float(np.percentile(rms, _REF_PERCENTILE)) if len(rms) else 0.0
    if ref < _SILENCE_REF:
        return times, np.zeros_like(rms)

    # Cutoff remap: floor (noise gate) -> 0, reference percentile -> 1.
    floor = gate * ref
    env = (rms - floor) / max(ref - floor, 1e-9)
    np.clip(env, 0.0, 1.0, out=env)

    attack, release = smoothing
    env = _attack_release(env, fps, attack, release)
    return times, env


def generate_from_energy(
    wav_path: str,
    fps: float = 60.0,
    epsilon: float = 0.015,
    mapping: Optional[Mapping] = None,
    intensity: float = 1.0,
    spread: float = 0.25,
    window: Optional[float] = None,
    gate: float = 0.06,
    smoothing: Tuple[float, float] = (0.012, 0.09),
    gestures=None,
) -> FaceTrack:
    """Build a ``FaceTrack`` from audio loudness alone — no transcript needed.

    **Energy fallback, not viseme detection.** The loudness envelope
    (:func:`energy_envelope`) drives one primary jaw-open channel (``aa``); a
    small ``spread`` fraction of that opening is bled into two secondary mouth
    shapes purely so the motion does not look like a single channel flapping.
    The spread rule is a deliberate *aesthetic* heuristic, **not** phoneme
    recognition: louder frames lean rounded/open (``O``), quieter frames lean
    mid-spread (``E``). Silent frames go to rest (``sil``). No claim is made
    that any channel matches the sound actually being spoken.

    Each frame partitions unit weight across ``sil`` and the mouth shapes
    (``sil + aa + E + O == 1``), so ``sil`` is a true "mouth closed" amount.
    Output is an ordinary Oculus-viseme track, so ``--retarget``, ``.anim`` and
    the cue exporters all compose downstream exactly as for the other modes.

    Parameters
    ----------
    intensity : gain on the mouth opening (``1.0`` as-is; ``>1`` opens wider on
        quiet speech, ``<1`` is subtler). Clamped into ``[0, 1]`` per frame.
    spread : fraction of the opening lent to the secondary ``E``/``O`` shapes
        (``0`` = pure jaw flap; default ``0.25``).
    mapping : optional target vocabulary supplying channel names and per-target
        clamps (like the other generators). Must contain ``aa`` and ``sil``;
        defaults to the built-in Oculus-15 set.
    epsilon, window, gate, smoothing : forwarded to keyframe reduction and
        :func:`energy_envelope`.
    gestures : opt-in non-verbal gesture layer (issue #5); a ``GestureParams``
        (or ``True`` for defaults) appends blink/brow/head/eye channels driven
        by this same envelope. ``None`` (default) leaves output byte-identical.

    Output is deterministic: identical audio and parameters give an identical
    track (the mouth path has no RNG; the gesture layer is seeded from
    ``GestureParams.seed``).
    """
    times, env = energy_envelope(wav_path, fps=fps, window=window, gate=gate,
                                 smoothing=smoothing)
    openness = np.clip(env * intensity, 0.0, 1.0)
    spread = float(np.clip(spread, 0.0, 1.0))

    names = mapping.target_names if mapping is not None else list(VISEMES)
    targets = mapping.targets if mapping is not None else None
    index = {n: i for i, n in enumerate(names)}
    if "aa" not in index or "sil" not in index:
        raise ValueError(
            "energy mode drives the built-in viseme roles (aa, E, O, sil); "
            "the given mapping has no 'aa'/'sil' target to drive")

    # Partition unit weight: jaw-open keeps (1 - spread) of the opening; the
    # spread budget splits between mid (E, quiet) and round (O, loud); the rest
    # is silence. aa + E + O == openness, so sil + aa + E + O == 1.
    channels = {
        "aa": openness * (1.0 - spread),
        "E": openness * spread * (1.0 - openness),
        "O": openness * spread * openness,
        "sil": 1.0 - openness,
    }
    matrix = np.zeros((len(times), len(names)))
    for name, col in channels.items():
        if name in index:
            matrix[:, index[name]] = col
    track = reduce_to_track(times, matrix, fps=fps, epsilon=epsilon,
                            targets=targets)
    if gestures:
        # No phonemes here, so stress/pauses/peaks come from the envelope itself.
        from .gestures import GestureParams, add_gestures_to_track
        gp = gestures if isinstance(gestures, GestureParams) else GestureParams()
        duration = float(times[-1]) if len(times) else 0.0
        add_gestures_to_track(track, duration, times, env, None, gp)
    return track
