"""End-to-end pipeline: (audio, text) -> FaceTrack.

Two entry points:

  * ``generate_from_alignment`` -- you already have time-stamped phonemes (from
    MFA, Gentle, wav2vec2, Whisper...). This is the accurate path.

  * ``generate_naive`` -- you only have text and an audio duration. Uses G2P +
    NaiveAligner. Fast, dependency-free, approximate lip-sync for prototyping.
"""

from __future__ import annotations

import contextlib
import wave
from typing import List, Optional

from .g2p import G2P
from .alignment import NaiveAligner, PhonemeSegment
from .coarticulation import build_viseme_curves
from .curves import reduce_to_track, FaceTrack
from .phonemes import SILENCE


def wav_duration(path: str) -> float:
    """Duration of a PCM WAV in seconds, using only the stdlib."""
    with contextlib.closing(wave.open(path, "rb")) as w:
        return w.getnframes() / float(w.getframerate())


def generate_from_alignment(
    segments: List[PhonemeSegment],
    fps: float = 60.0,
    epsilon: float = 0.015,
    mapping=None,
    params=None,
    gestures=None,
    wav: Optional[str] = None,
) -> FaceTrack:
    """``gestures`` opts into the non-verbal gesture layer (issue #5): pass a
    ``GestureParams`` (or ``True`` for defaults) to append blink/brow/head/eye
    channels after viseme reduction. Off (``None``) leaves output byte-identical.
    ``wav`` supplies the audio those energy-driven brows/nods read; without it
    they degrade gracefully (stress still comes from the segments)."""
    times, matrix = build_viseme_curves(segments, fps=fps, mapping=mapping,
                                        params=params)
    targets = mapping.targets if mapping is not None else None
    track = reduce_to_track(times, matrix, fps=fps, epsilon=epsilon,
                            targets=targets)
    if gestures:
        _attach_gestures(track, segments, wav, gestures)
    return track


def _attach_gestures(track: FaceTrack, segments, wav, gestures) -> None:
    from .gestures import GestureParams, add_gestures_to_track
    gp = gestures if isinstance(gestures, GestureParams) else GestureParams()
    duration = segments[-1].end if segments else track.duration
    env_times = env = None
    if wav:
        from .energy import energy_envelope
        env_times, env = energy_envelope(wav, fps=track.fps)
    add_gestures_to_track(track, duration, env_times, env, segments, gp)


def naive_segments(
    text: str,
    duration: float,
    g2p: Optional[G2P] = None,
) -> List[PhonemeSegment]:
    """Time-stamped phonemes for ``text`` spread over ``duration`` seconds.

    This is the phoneme-timing layer the curve solver consumes; exporters that
    need phonemes rather than visemes (e.g. Bethesda .LIP) start here.
    """
    g2p = g2p or G2P()
    # Pad with silence at both ends so the mouth starts and ends relaxed.
    phones = [SILENCE] + g2p.phrase(text) + [SILENCE]
    return NaiveAligner().align(phones, total_duration=duration)


def generate_naive(
    text: str,
    duration: float,
    fps: float = 60.0,
    epsilon: float = 0.015,
    g2p: Optional[G2P] = None,
    mapping=None,
    params=None,
    gestures=None,
    wav: Optional[str] = None,
) -> FaceTrack:
    segs = naive_segments(text, duration, g2p=g2p)
    return generate_from_alignment(segs, fps=fps, epsilon=epsilon,
                                   mapping=mapping, params=params,
                                   gestures=gestures, wav=wav)
