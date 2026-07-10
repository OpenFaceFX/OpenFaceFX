"""Deterministic track transforms: retime / mirror / trim (issue #48).

Common post-production edits that had no home. Unlike
:func:`openfacefx.postprocess.time_shift` (which only *slides* curves and never
stretches the clip), these reshape the timeline and the channel layout:

  * :func:`retime` -- scale every keyframe *time* (and event time) by a factor, or
    :func:`retime_to_duration` to a target length, pinning an ``anchor``. Channel
    *values* are untouched; the track ``duration`` follows the scaled keys.
  * :func:`mirror` -- swap ``*Left`` <-> ``*Right`` channel pairs (an extensible
    :data:`MIRROR_PAIRS` table, plain data like the retarget presets) and negate
    the signed lateral pose channels (:data:`MIRROR_NEGATE`: ``headYaw`` /
    ``headRoll`` / ``eyeYaw``); centered channels (visemes, ``jawOpen``,
    ``headPitch``) pass through untouched. A **pure relabel + sign-flip** -- no
    time change, no re-thin, channel order preserved -- so ``mirror(mirror(t))``
    is *byte-identical* to ``t``.
  * :func:`trim` -- keep ``[t0, t1]``, rebase to zero, and drop/reclamp events to
    the window; an empty window yields an empty track, not a crash.

They compose with ``convert`` and the importers (bring a capture in, retime to the
new VO, re-export). numpy is not needed -- pure stdlib arithmetic, no clock, no
RNG, identical on Python 3.9/3.13.

Note on re-thinning: a uniform time scale preserves collinearity and (when
stretching) only widens key spacing, so it introduces no redundant keys; retime
therefore keeps every key (deduping only an exact time collision under heavy
compression) rather than RDP-resampling, which would move keys and defeat the
"every key time scales, values unchanged" contract.
"""

from __future__ import annotations

import math
from dataclasses import replace
from typing import List, Optional, Tuple

from .curves import Channel, FaceTrack, Keyframe

#: Left/right channel pairs swapped by :func:`mirror`. Extensible plain data (the
#: same style as ``retarget.PRESETS``) -- ARKit ``*Left``/``*Right`` blendshapes
#: plus the openfacefx gesture-layer ``blink_L``/``blink_R``. Copy and extend for
#: a rig with other lateral names.
MIRROR_PAIRS: List[Tuple[str, str]] = [
    ("browDownLeft", "browDownRight"),
    ("browOuterUpLeft", "browOuterUpRight"),
    ("cheekSquintLeft", "cheekSquintRight"),
    ("eyeBlinkLeft", "eyeBlinkRight"),
    ("eyeLookDownLeft", "eyeLookDownRight"),
    ("eyeLookInLeft", "eyeLookInRight"),
    ("eyeLookOutLeft", "eyeLookOutRight"),
    ("eyeLookUpLeft", "eyeLookUpRight"),
    ("eyeSquintLeft", "eyeSquintRight"),
    ("eyeWideLeft", "eyeWideRight"),
    ("mouthDimpleLeft", "mouthDimpleRight"),
    ("mouthFrownLeft", "mouthFrownRight"),
    ("mouthLowerDownLeft", "mouthLowerDownRight"),
    ("mouthPressLeft", "mouthPressRight"),
    ("mouthSmileLeft", "mouthSmileRight"),
    ("mouthStretchLeft", "mouthStretchRight"),
    ("mouthUpperUpLeft", "mouthUpperUpRight"),
    ("noseSneerLeft", "noseSneerRight"),
    ("blink_L", "blink_R"),
]

#: Signed *lateral* pose channels negated by :func:`mirror` (a left turn becomes a
#: right turn). ``headPitch`` / ``eyePitch`` are up/down, NOT lateral, so they are
#: left untouched -- as are all ``[0, 1]`` weight channels.
MIRROR_NEGATE = frozenset({"headYaw", "headRoll", "eyeYaw"})

#: Bidirectional name -> mirrored name.
_MIRROR_MAP = {}
for _left, _right in MIRROR_PAIRS:
    _MIRROR_MAP[_left] = _right
    _MIRROR_MAP[_right] = _left

_EPS = 1e-9


def _copy_target_set(track: FaceTrack):
    return list(track.target_set) if track.target_set is not None else None


def _carry(track: FaceTrack, out: FaceTrack, events, variants) -> FaceTrack:
    out.events = events
    out.variants = variants
    return out


# --------------------------------------------------------------------------- #
# retime / stretch                                                             #
# --------------------------------------------------------------------------- #

def retime(track: FaceTrack, factor: float, *, anchor: float = 0.0) -> FaceTrack:
    """Scale every keyframe and event time about ``anchor`` by ``factor`` --
    ``t' = anchor + (t - anchor) * factor`` -- leaving channel *values* unchanged
    and letting the track ``duration`` follow. ``factor`` must be finite and
    positive. Keys are preserved (a uniform scale adds no redundancy); only an
    exact 4-dp time collision under heavy compression is de-duplicated."""
    if not math.isfinite(factor) or factor <= 0.0:
        raise ValueError(f"retime factor must be a finite positive number, "
                         f"got {factor!r}")
    channels: List[Channel] = []
    for c in track.channels:
        keys: List[Keyframe] = []
        last: Optional[float] = None
        for k in c.keys:
            t = round(anchor + (k.time - anchor) * factor, 4)
            if last is not None and t <= last:      # collided under compression
                continue
            keys.append(Keyframe(t, k.value))
            last = t
        channels.append(Channel(c.name, keys))
    out = FaceTrack(track.fps, channels, _copy_target_set(track))
    return _carry(track, out, _retime_events(track.events, factor, anchor),
                  _retime_variants(track.variants, factor, anchor))


def retime_to_duration(track: FaceTrack, target: float, *,
                       anchor: float = 0.0) -> FaceTrack:
    """Retime so the clip lasts ``target`` seconds (``factor = target /
    duration``). A zero-duration track cannot be scaled to a length."""
    current = track.duration
    if current <= 0.0:
        raise ValueError("cannot retime a zero-duration track to a new length")
    if not math.isfinite(target) or target <= 0.0:
        raise ValueError(f"target duration must be finite and positive, "
                         f"got {target!r}")
    return retime(track, target / current, anchor=anchor)


def _retime_events(events, factor: float, anchor: float):
    return [replace(e, t=round(anchor + (e.t - anchor) * factor, 4),
                    dur=round(e.dur * factor, 4)) for e in events]


def _retime_variants(variants, factor: float, anchor: float):
    if variants is None:
        return None
    groups = [replace(g, alternatives=[
        replace(a, events=_retime_events(a.events, factor, anchor))
        for a in g.alternatives]) for g in variants.groups]
    return replace(variants, groups=groups)


# --------------------------------------------------------------------------- #
# mirror -- pure relabel + sign flip (mirror . mirror == identity)            #
# --------------------------------------------------------------------------- #

def mirror(track: FaceTrack) -> FaceTrack:
    """Swap ``*Left``/``*Right`` channel pairs and negate the signed lateral pose
    channels, in place (channel order preserved, no time change, no re-thin).

    Centered channels pass through untouched. Because it is a pure relabel plus
    sign flip, ``mirror(mirror(track))`` is **byte-identical** to ``track``."""
    channels: List[Channel] = []
    for c in track.channels:
        name = _MIRROR_MAP.get(c.name, c.name)
        if c.name in MIRROR_NEGATE:
            keys = [Keyframe(k.time, -k.value) for k in c.keys]
        else:
            keys = [Keyframe(k.time, k.value) for k in c.keys]
        channels.append(Channel(name, keys))
    ts = track.target_set
    if ts is not None:
        ts = [_MIRROR_MAP.get(n, n) for n in ts]
    out = FaceTrack(track.fps, channels, ts)
    # The event/variant layer is timeline metadata, unaffected by a spatial
    # mirror -- carried through unchanged so the double-mirror stays identical.
    return _carry(track, out, list(track.events), track.variants)


# --------------------------------------------------------------------------- #
# trim / slice                                                                 #
# --------------------------------------------------------------------------- #

def trim(track: FaceTrack, t0: float, t1: float) -> FaceTrack:
    """Keep ``[t0, t1]``, rebased so ``t0`` becomes ``0``. Only in-window keys are
    kept (a channel left empty is dropped); events whose start is in-window are
    rebased and their duration reclamped to the window, the rest dropped. An empty
    or out-of-range window yields an empty track (no crash)."""
    if not (math.isfinite(t0) and math.isfinite(t1)):
        raise ValueError(f"trim window must be finite, got [{t0}, {t1}]")
    if t1 < t0:
        raise ValueError(f"trim window end {t1} precedes start {t0}")
    channels: List[Channel] = []
    for c in track.channels:
        keys = [Keyframe(round(k.time - t0, 4), k.value)
                for k in c.keys if t0 - _EPS <= k.time <= t1 + _EPS]
        if keys:
            channels.append(Channel(c.name, keys))
    out = FaceTrack(track.fps, channels, _copy_target_set(track))
    return _carry(track, out, _trim_events(track.events, t0, t1),
                  _trim_variants(track.variants, t0, t1))


def _trim_events(events, t0: float, t1: float):
    window = t1 - t0
    out = []
    for e in events:
        if e.t < t0 - _EPS or e.t > t1 + _EPS:
            continue
        offset = e.t - t0
        dur = round(min(e.dur, window - offset), 4) if e.dur else e.dur
        out.append(replace(e, t=round(offset, 4), dur=max(0.0, dur)))
    return out


def _trim_variants(variants, t0: float, t1: float):
    if variants is None:
        return None
    groups = [replace(g, alternatives=[
        replace(a, events=_trim_events(a.events, t0, t1))
        for a in g.alternatives]) for g in variants.groups]
    return replace(variants, groups=groups)
