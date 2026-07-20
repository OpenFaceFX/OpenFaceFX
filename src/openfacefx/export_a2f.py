"""NVIDIA Audio2Face blendshape-weights JSON interop (#64) -- the non-USD path.

NVIDIA Audio2Face / Audio2Face-3D is the dominant audio-driven facial-animation
tool, and its dense per-frame **ARKit-FACS JSON** is the public interchange path
for pipelines that can't consume USD (confirmed by NVIDIA's samples repo, the
`audio-2-face-weights-import` Blender addon, and NVIDIA forum threads). The layout::

    {"exportFps": 30.0, "numFrames": N, "numPoses": P,
     "facsNames": ["jawOpen", "mouthSmileLeft", ...],   # P names
     "weightMat": [[w0, w1, ... wP-1], ...]}            # N frames x P weights

This adds both halves, twinning shipped code:

  * **export** (`write_a2f`) mirrors :mod:`openfacefx.export_livelink` -- sample the
    track onto a fixed-fps grid and emit one row per frame. ``facsNames`` are the
    track's own ``[0, 1]`` channel names **verbatim** (A2F's pose set is a
    configurable ~46/52 ARKit-derived list, so we do not force a fixed header), so
    a ``--retarget arkit`` track lands as ARKit names; a still-viseme track is
    reported (retarget first). Head/eye **pose** channels are excluded (A2F carries
    blendshape weights, not rotation angles).
  * **import** (`read_a2f`) mirrors :mod:`openfacefx.importers_csv`'s wide branch --
    read ``facsNames`` verbatim as channel names, ``weightMat`` rows as frames,
    times from ``numFrames``/``exportFps`` (a ``fps`` override wins; a file lacking
    ``exportFps`` falls back to it), clip to ``[0, 1]`` and RDP-thin via
    :func:`openfacefx.curves.reduce_to_track`.

``write_a2f`` then ``read_a2f`` reconstructs every channel (the round-trip proof).
stdlib ``json`` + numpy, deterministic on py3.9/3.13 (fixed 6-dp weights), additive.
"""

from __future__ import annotations

import json
from collections import namedtuple
from typing import Dict, List, Optional, Tuple

import numpy as np

from .curves import FaceTrack, reduce_to_track
from .edits import sample as _sample
from .export_livelink import _ARKIT_52
from .inspect import POSE_CHANNELS

_DEFAULT_FPS = 30.0                       # A2F's typical export rate
_WEIGHT_DP = 6                            # fixed precision -> deterministic bytes
_ARKIT_LC = {n.lower() for n in _ARKIT_52}

# reduce_to_track only reads .name/.lo/.hi off each target (see importers_csv).
_Col = namedtuple("_Col", "name lo hi")


def _blendshape_channels(track: FaceTrack):
    """The ``[0, 1]`` weight channels (pose/rotation channels excluded)."""
    return [c for c in track.channels if c.name not in POSE_CHANNELS]


def _grid_fps(track: FaceTrack, fps: Optional[float]) -> float:
    if fps is None:
        fps = track.fps or _DEFAULT_FPS
    fps = float(fps)
    if not (0.0 < fps < float("inf")):
        raise ValueError(f"a2f: fps must be a finite value > 0, got {fps!r}")
    return fps


def a2f_dict(track: FaceTrack, *, fps: Optional[float] = None
             ) -> Tuple[Dict, int]:
    """Return ``(a2f_json_dict, matched)`` -- the Audio2Face blendshape document and
    the number of ``facsNames`` that are ARKit blendshapes (0 usually means the
    track is still in viseme space; retarget to ``arkit`` first)."""
    fps = _grid_fps(track, fps)
    chans = _blendshape_channels(track)
    names = [c.name for c in chans]
    n = int(round(track.duration * fps)) + 1              # rows, incl. frame 0
    grid = np.arange(n, dtype=float) / fps

    weight_mat: List[List[float]] = []
    sampled = [np.clip(_sample(c, grid), 0.0, 1.0) for c in chans]
    for i in range(n):
        weight_mat.append([round(float(s[i]), _WEIGHT_DP) for s in sampled])

    matched = sum(1 for name in names if name.lower() in _ARKIT_LC)
    doc = {
        "exportFps": round(float(fps), 6),
        "numFrames": n,
        "numPoses": len(names),
        "facsNames": names,
        "weightMat": weight_mat,
    }
    return doc, matched


def write_a2f(track: FaceTrack, path: str, *, fps: Optional[float] = None) -> int:
    """Write ``track`` as an Audio2Face blendshape JSON (see :func:`a2f_dict`).
    Returns the count of ARKit-named channels, so a caller can warn on a
    still-viseme-space track. Compact, deterministic bytes."""
    doc, matched = a2f_dict(track, fps=fps)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, separators=(",", ":"))
        fh.write("\n")
    return matched


def parse_a2f(doc: Dict, *, fps: Optional[float] = None,
              epsilon: float = 0.015) -> Tuple[FaceTrack, List[str]]:
    """Parse a loaded Audio2Face blendshape dict into ``(FaceTrack, warnings)``.

    ``fps`` overrides the file's ``exportFps`` (and is the fallback when the file
    omits it). Channel names come from ``facsNames`` verbatim; weights clip to
    ``[0, 1]`` (out-of-range columns are reported) and RDP-thin into sparse keys."""
    if not isinstance(doc, dict):
        raise ValueError(f"a2f: expected a JSON object, got {type(doc).__name__}")
    names = doc.get("facsNames")
    mat = doc.get("weightMat")
    if not isinstance(names, list) or not all(isinstance(x, str) for x in names):
        raise ValueError("a2f: 'facsNames' must be a list of blendshape-name strings")
    if not isinstance(mat, list):
        raise ValueError("a2f: 'weightMat' must be a list of per-frame weight rows")
    if len(set(names)) != len(names):
        raise ValueError(f"a2f: duplicate names in 'facsNames': {names}")

    eff_fps = fps if fps is not None else doc.get("exportFps")
    eff_fps = _grid_fps_value(eff_fps)

    if not names or not mat:
        return FaceTrack(fps=eff_fps, channels=[], target_set=None), []

    P = len(names)
    for i, row in enumerate(mat):
        if not isinstance(row, list) or len(row) != P:
            raise ValueError(
                f"a2f: weightMat row {i} has {len(row) if isinstance(row, list) else '?'} "
                f"values but facsNames declares {P} poses")
    try:
        raw = np.asarray(mat, dtype=float)
    except (TypeError, ValueError):
        raise ValueError("a2f: weightMat contains a non-numeric weight") from None
    if not np.all(np.isfinite(raw)):
        raise ValueError("a2f: weightMat contains a non-finite weight (NaN/inf)")

    warnings: List[str] = []
    for k, name in enumerate(names):
        if np.any(raw[:, k] < 0.0) or np.any(raw[:, k] > 1.0):
            warnings.append(f"column {name!r} had values outside [0, 1]; clamped")
    matrix = np.clip(raw, 0.0, 1.0)
    times = np.arange(matrix.shape[0], dtype=float) / eff_fps
    targets = [_Col(name, 0.0, 1.0) for name in names]
    track = reduce_to_track(times, matrix, fps=eff_fps, epsilon=epsilon,
                            targets=targets)
    return track, warnings


def read_a2f(path: str, *, fps: Optional[float] = None,
             epsilon: float = 0.015) -> Tuple[FaceTrack, List[str]]:
    """Read an Audio2Face blendshape JSON file into ``(FaceTrack, warnings)``
    (see :func:`parse_a2f`)."""
    with open(path, encoding="utf-8") as fh:
        doc = json.load(fh)
    return parse_a2f(doc, fps=fps, epsilon=epsilon)


def _grid_fps_value(fps) -> float:
    """Validate a bare fps value (import side; no track to fall back to)."""
    if fps is None:
        fps = _DEFAULT_FPS
    try:
        fps = float(fps)
    except (TypeError, ValueError):
        raise ValueError(f"a2f: fps/exportFps must be a number, got {fps!r}") from None
    if not (0.0 < fps < float("inf")):
        raise ValueError(f"a2f: fps must be a finite value > 0, got {fps!r}")
    return fps
