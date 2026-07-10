"""Animation curves: keyframe reduction and track containers.

The dominance model produces one dense sample per frame. Rigs and engines
prefer sparse keyframes, so we thin each channel with the Ramer-Douglas-Peucker
algorithm: drop samples that lie within ``epsilon`` of the straight line between
their neighbours. This is lossy but perceptually safe and shrinks output a lot.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List

import numpy as np

from .visemes import VISEMES


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
                    epsilon: float = 0.015) -> FaceTrack:
    channels: List[Channel] = []
    for v, name in enumerate(VISEMES):
        col = matrix[:, v]
        if not np.any(col > 1e-3):
            continue  # channel never fires; skip entirely
        idx = _rdp(times, col, epsilon)
        keys = [Keyframe(float(times[i]), round(float(col[i]), 4)) for i in idx]
        channels.append(Channel(name, keys))
    return FaceTrack(fps=fps, channels=channels)
