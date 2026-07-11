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


# --------------------------------------------------------------------------- #
# concat / sequence -- splice finished tracks end to end (the inverse of trim) #
# --------------------------------------------------------------------------- #

def concat(tracks: List[FaceTrack], *, gaps=None, crossfade: float = 0.0
           ) -> FaceTrack:
    """Splice ``tracks`` into one timeline, in order.

    Every keyframe and event/variant time of segment *k* is offset by its
    cumulative start (``Σ`` of the earlier durations and gaps); the result's
    ``duration`` is ``Σ durations + Σ gaps``. Channels are **unioned** across
    segments -- a channel absent from a segment reads as rest (``0``) across that
    segment's span (a ``0`` key at each of its boundaries stops the previous
    segment's last value bleeding over the seam). ``gaps`` is a per-seam second
    list (or one float applied between all); a single-track ``concat([a])`` with
    no gap or crossfade returns ``a`` unchanged (byte-identical).

    ``crossfade`` (default ``0`` -- a hard cut) linearly blends the shared
    channels over ``±crossfade`` seconds at each abutting seam, RDP-thinning only
    that window; at ``0`` the splice is a pure relabel/offset with no re-thin."""
    if not tracks:
        raise ValueError("concat needs at least one track")
    if not (math.isfinite(crossfade) and crossfade >= 0.0):
        raise ValueError(f"crossfade must be a finite number >= 0, got {crossfade!r}")
    n = len(tracks)
    gap_list = _concat_gaps(gaps, n)
    if n == 1 and not crossfade:
        return tracks[0]                             # a single splice is a no-op

    offsets = [0.0]
    for k in range(1, n):
        offsets.append(round(offsets[-1] + tracks[k - 1].duration
                             + gap_list[k - 1], 6))

    order: List[str] = []
    seen = set()
    for t in tracks:
        for c in t.channels:
            if c.name not in seen:
                seen.add(c.name)
                order.append(c.name)

    channels: List[Channel] = []
    for name in order:
        keys: List[Keyframe] = []
        for k, t in enumerate(tracks):
            ch = next((c for c in t.channels if c.name == name), None)
            off = offsets[k]
            if ch is not None:
                keys.extend(Keyframe(round(kf.time + off, 6), kf.value)
                            for kf in ch.keys)
            else:                                    # rest across this span, no bleed
                keys.append(Keyframe(round(off, 6), 0.0))
                keys.append(Keyframe(round(off + t.duration, 6), 0.0))
        channels.append(Channel(name, _dedup_keys(keys)))

    out = FaceTrack(tracks[0].fps, channels, _concat_target_set(tracks))
    out = _carry(tracks[0], out, _concat_events(tracks, offsets),
                 _concat_variants(tracks, offsets))
    if crossfade > 0.0:
        _apply_crossfades(out, tracks, offsets, gap_list, crossfade)
    return out


def _concat_gaps(gaps, n: int) -> List[float]:
    if gaps is None:
        return [0.0] * (n - 1)
    if isinstance(gaps, (int, float)) and not isinstance(gaps, bool):
        gaps = [float(gaps)] * (n - 1)
    gaps = [float(g) for g in gaps]
    if len(gaps) != n - 1:
        raise ValueError(f"need {n - 1} gap(s) for {n} track(s), got {len(gaps)}")
    if any(g < 0 for g in gaps):
        raise ValueError("gaps must be >= 0")
    return gaps


def _dedup_keys(keys: List[Keyframe]) -> List[Keyframe]:
    out: List[Keyframe] = []
    for k in keys:
        if out and out[-1].time == k.time and out[-1].value == k.value:
            continue                                 # collapse an exact repeat
        out.append(k)
    return out


def _concat_target_set(tracks: List[FaceTrack]):
    if all(t.target_set is None for t in tracks):
        return None
    from .visemes import VISEMES
    out: List[str] = []
    for t in tracks:
        for name in (t.target_set if t.target_set is not None else VISEMES):
            if name not in out:
                out.append(name)
    return out


def _offset_events(events, offset: float):
    return [replace(e, t=round(e.t + offset, 4)) for e in events]


def _concat_events(tracks: List[FaceTrack], offsets: List[float]):
    out = []
    for k, t in enumerate(tracks):
        out.extend(_offset_events(getattr(t, "events", None) or [], offsets[k]))
    return out


def _concat_variants(tracks: List[FaceTrack], offsets: List[float]):
    groups = []
    line_id = None
    for k, t in enumerate(tracks):
        v = getattr(t, "variants", None)
        if v is None:
            continue
        line_id = line_id if line_id is not None else v.line_id
        for g in v.groups:
            groups.append(replace(g, alternatives=[
                replace(a, events=_offset_events(a.events, offsets[k]))
                for a in g.alternatives]))
    if not groups:
        return None
    from .events import Variants
    return Variants(line_id, groups)


def _apply_crossfades(out: FaceTrack, tracks, offsets, gap_list, span: float):
    """Linearly blend the shared channels across each abutting (gap-0) seam over
    ``±span`` seconds, RDP-thinning only the blended window."""
    import numpy as np
    from .curves import _rdp
    from .edits import sample
    chan = {c.name: c for c in out.channels}
    for k in range(len(tracks) - 1):
        if gap_list[k] > 0:                          # a gap already separates them
            continue
        seam = offsets[k] + tracks[k].duration
        lo, hi = seam - span, seam + span
        a = {c.name: c for c in tracks[k].channels}
        b = {c.name: c for c in tracks[k + 1].channels}
        for name in sorted(set(a) & set(b)):
            c = chan[name]
            m = max(2, int(round((hi - lo) * out.fps)) + 1)
            grid = np.linspace(lo, hi, m)
            left = sample(a[name], grid - offsets[k])        # held past its end
            right = sample(b[name], grid - offsets[k + 1])   # held before its start
            alpha = np.clip((grid - lo) / (2.0 * span), 0.0, 1.0)
            blend = (1.0 - alpha) * left + alpha * right
            idx = _rdp(grid, blend, 0.01)
            inner = [Keyframe(round(float(grid[i]), 4), round(float(blend[i]), 4))
                     for i in idx]
            outside = [kf for kf in c.keys
                       if kf.time < lo - _EPS or kf.time > hi + _EPS]
            c.keys = sorted(outside + inner, key=lambda kf: kf.time)
