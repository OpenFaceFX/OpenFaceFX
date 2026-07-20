"""Animation curves: keyframe reduction and track containers.

The dominance model produces one dense sample per frame. Rigs and engines
prefer sparse keyframes, so we thin each channel with the Ramer-Douglas-Peucker
algorithm: drop samples that lie within ``epsilon`` of the straight line between
their neighbours. This is lossy but perceptually safe and shrinks output a lot.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional, TYPE_CHECKING

import numpy as np

from .visemes import VISEMES

if TYPE_CHECKING:                       # avoid a runtime import cycle; the event
    from .events import Event, Variants  # layer (events.py) is optional & additive


@dataclass
class Keyframe:
    time: float
    value: float


@dataclass
class Channel:
    name: str                       # viseme / blendshape name
    keys: List[Keyframe] = field(default_factory=list)


@dataclass
class FaceTrack:
    fps: float
    channels: List[Channel]
    # Full target vocabulary the channels are drawn from; None means the
    # built-in Oculus viseme set (visemes.VISEMES).
    target_set: List[str] = None
    # Optional, additive event/take layer (issue #6). Both default empty, so an
    # ordinary track is byte-identical: `duration` is deliberately unchanged
    # (still key-based), so events never stretch the clip. See events.py.
    events: "List[Event]" = field(default_factory=list)
    variants: "Optional[Variants]" = None

    @property
    def duration(self) -> float:
        return max((k.time for c in self.channels for k in c.keys), default=0.0)


def _rdp(times: np.ndarray, values: np.ndarray, eps: float) -> List[int]:
    """Ramer-Douglas-Peucker; returns indices of kept points."""
    keep = np.zeros(len(times), dtype=bool)
    keep[0] = keep[-1] = True
    stack = [(0, len(times) - 1)]
    while stack:
        lo, hi = stack.pop()
        if hi <= lo + 1:
            continue
        x0, y0 = times[lo], values[lo]
        x1, y1 = times[hi], values[hi]
        dx, dy = x1 - x0, y1 - y0
        norm = (dx * dx + dy * dy) ** 0.5 or 1e-9
        # perpendicular distance of each interior point to the chord
        seg = slice(lo + 1, hi)
        dist = np.abs(dy * (times[seg] - x0) - dx * (values[seg] - y0)) / norm
        k = int(np.argmax(dist))
        if dist[k] > eps:
            idx = lo + 1 + k
            keep[idx] = True
            stack.append((lo, idx))
            stack.append((idx, hi))
    return list(np.nonzero(keep)[0])


def reduce_to_track(times: np.ndarray, matrix: np.ndarray, fps: float,
                    epsilon: float = 0.015, targets=None) -> FaceTrack:
    """``targets``: optional list of ``mapping.Target`` — supplies channel
    names, per-target min/max clamps and (schema v2, issue #53) optional
    ``gain``/``offset`` applied as ``clamp(gain*value + offset, min, max)`` before
    reduction. Defaults to the Oculus viseme set with no clamping and no
    gain/offset (identical to previous releases)."""
    if targets is None:
        names, clamps = VISEMES, [None] * len(VISEMES)
    else:
        names = [t.name for t in targets]
        clamps = [(t.lo, t.hi) if (t.lo, t.hi) != (0.0, 1.0) else None
                  for t in targets]
    channels: List[Channel] = []
    for v, name in enumerate(names):
        col = matrix[:, v]
        # A2F-style per-target gain/offset (issue #53): scale/bias a channel's
        # output, then clamp. getattr keeps non-Target callers (e.g. the CSV
        # importer's _Col) working; gain=1/offset=0 skips this branch entirely,
        # so default mappings and the built-in viseme set stay byte-identical.
        g = getattr(targets[v], "gain", 1.0) if targets is not None else 1.0
        o = getattr(targets[v], "offset", 0.0) if targets is not None else 0.0
        link = getattr(targets[v], "link", None) if targets is not None else None
        if link is not None:                       # nonlinear response (issue #68)
            from .links import apply_link
            lo, hi = clamps[v] if clamps[v] is not None else (0.0, 1.0)
            params = {k: val for k, val in link.items() if k != "function"}
            col = np.clip(apply_link(col, link["function"], params), lo, hi)
        elif g != 1.0 or o != 0.0:
            lo, hi = clamps[v] if clamps[v] is not None else (0.0, 1.0)
            col = np.clip(col * g + o, lo, hi)
        elif clamps[v] is not None:
            col = np.clip(col, clamps[v][0], clamps[v][1])
        if not np.any(col > 1e-3):
            continue  # channel never fires; skip entirely
        idx = _rdp(times, col, epsilon)
        keys = [Keyframe(float(times[i]), round(float(col[i]), 4)) for i in idx]
        channels.append(Channel(name, keys))
    return FaceTrack(fps=fps, channels=channels,
                     target_set=None if targets is None else list(names))
