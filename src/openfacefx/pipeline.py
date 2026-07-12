"""End-to-end pipeline: (audio, text) -> FaceTrack.

Two entry points:

  * ``generate_from_alignment`` -- you already have time-stamped phonemes (from
    MFA, or Gentle/Whisper/WhisperX via the built-in :mod:`openfacefx.aligners`
    adapters, issue #54). This is the accurate path.

  * ``generate_naive`` -- you only have text and an audio duration. Uses G2P +
    NaiveAligner. Fast, dependency-free, approximate lip-sync for prototyping.
"""

from __future__ import annotations

import contextlib
import wave
from typing import Callable, List, Optional

from .g2p import G2P
from .alignment import NaiveAligner, PhonemeSegment
from .coarticulation import build_viseme_curves
from .curves import reduce_to_track, FaceTrack
from .phonemes import SILENCE


def wav_duration(path: str) -> float:
    """Duration of a PCM WAV in seconds, using only the stdlib."""
    with contextlib.closing(wave.open(path, "rb")) as w:
        return w.getnframes() / float(w.getframerate())


def _require_positive(value, name: str) -> None:
    """Validate a positive, finite fps/duration at a pipeline boundary. ``fps=0``
    would divide-by-zero in the frame grid (empty/NaN track written silently);
    a non-positive duration yields a degenerate track — both raise a clear
    ValueError here instead (mirrors ``anchors.anchored_segments``)."""
    try:
        v = float(value)
    except (TypeError, ValueError):
        raise ValueError(f"{name} must be a number, got {value!r}") from None
    if not (0.0 < v < float("inf")):
        raise ValueError(f"{name} must be a finite value > 0, got {value!r}")


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
    _require_positive(fps, "fps")
    times, matrix = build_viseme_curves(segments, fps=fps, mapping=mapping,
                                        params=params)
    targets = mapping.targets if mapping is not None else None
    track = reduce_to_track(times, matrix, fps=fps, epsilon=epsilon,
                            targets=targets)
    # Lag/lead post-process (issue #10): slide the reduced viseme keyframes in
    # time before the gesture layer is attached, so only the mouth leads/trails
    # the audio (blinks/brows keep their own timing). Off (lag=0) => untouched.
    if params is not None and params.lag:
        from .postprocess import time_shift
        time_shift(track, params.lag)
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


def derive_events(
    segments: Optional[List[PhonemeSegment]] = None,
    env_times=None,
    env=None,
    emphasis: bool = True,
    phrase: bool = True,
    energy_thresh: float = 0.55,
    min_prominence: float = 0.15,
    min_spacing: float = 0.40,
) -> list:
    """Auto-author a typed event layer from the speech itself (issue #6) --
    ``emphasis`` events on stressed syllables / loudness peaks and ``phrase``
    boundary markers at pauses -- WITHOUT touching the gesture *channels*.

    It reuses the exact stress/pause/peak detectors behind
    :mod:`openfacefx.gestures` (``gestures_layers``), so an emphasis event and a
    head-nod gesture agree on where the accents are, yet the two layers stay
    independent and separately opt-in. Determinism is inherited from those
    numpy detectors (no RNG here): identical inputs give identical events.

    ``segments`` supply ARPABET stress digits and ``sil`` pauses; ``env_times`` /
    ``env`` are an :func:`openfacefx.energy.energy_envelope` result. With
    segments, stress drives emphasis and ``sil`` spans drive phrase markers; with
    only an envelope (energy mode), loudness peaks drive emphasis and quiet runs
    drive phrase markers. Returns a time-sorted ``list[Event]``."""
    import numpy as np
    from . import gestures_layers as _gl
    from .events import Event

    et = np.asarray(env_times, dtype=float) if env_times is not None else np.zeros(0)
    ev = np.asarray(env, dtype=float) if env is not None else np.zeros(0)
    out: list = []
    if emphasis:
        stresses = _gl.stress_events(segments, et, ev) if segments else []
        if stresses:
            out += [Event(t, "emphasis", "beat",
                          payload={"strength": round(float(s), 4)})
                    for t, s in stresses]
        elif len(ev):
            peaks = _gl.energy_peaks(et, ev, energy_thresh, min_prominence,
                                     min_spacing)
            out += [Event(t, "emphasis", "beat",
                          payload={"level": round(float(p), 4)})
                    for t, p in peaks]
    if phrase:
        out += [Event(t, "marker", "phrase") for t in _gl.pause_times(segments, et, ev)]
    out.sort(key=lambda e: e.t)
    return out


def naive_segments(
    text: str,
    duration: float,
    g2p: Optional[G2P] = None,
) -> List[PhonemeSegment]:
    """Time-stamped phonemes for ``text`` spread over ``duration`` seconds.

    This is the phoneme-timing layer the curve solver consumes; exporters that
    need phonemes rather than visemes (e.g. Bethesda .LIP) start here.
    """
    _require_positive(duration, "duration")
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
    preprocess: Optional[Callable[[str], str]] = None,
    parse_tags: bool = False,
) -> FaceTrack:
    """``preprocess`` (issue #7) is an optional ``callable(text) -> text`` run on
    the transcript before anything else, so a registered auto-tagger can insert
    tags programmatically; injecting a tag this way is identical to hand-writing
    it. ``parse_tags`` enables the transcript text-tag stage (see
    :mod:`openfacefx.texttags`): curve tags become extra channels, event tags the
    event layer, ``[emphasis]`` a local articulation boost and ``<T>``/``[pause]``
    chunk/silence the timeline. Both default off, so the plain naive path is
    byte-identical; a ``parse_tags`` run on a transcript that carries no tags is
    byte-identical too."""
    _require_positive(fps, "fps")
    _require_positive(duration, "duration")
    if preprocess is not None:
        text = preprocess(text)
    if parse_tags:
        from .texttags import (parse_tagged_transcript, resolve_tagged_segments,
                               curve_channels, tag_events, emphasis_params)
        clean, tags = parse_tagged_transcript(text)
        if not tags:
            segs = naive_segments(clean, duration, g2p=g2p)
            return generate_from_alignment(segs, fps=fps, epsilon=epsilon,
                                           mapping=mapping, params=params,
                                           gestures=gestures, wav=wav)
        segs, spans, windows = resolve_tagged_segments(clean, duration, tags,
                                                       g2p or G2P())
        track = generate_from_alignment(
            segs, fps=fps, epsilon=epsilon, mapping=mapping,
            params=emphasis_params(params, windows), gestures=gestures, wav=wav)
        track.channels.extend(curve_channels(tags, spans, duration))
        track.events.extend(tag_events(tags, spans, duration))
        return track
    segs = naive_segments(text, duration, g2p=g2p)
    return generate_from_alignment(segs, fps=fps, epsilon=epsilon,
                                   mapping=mapping, params=params,
                                   gestures=gestures, wav=wav)
