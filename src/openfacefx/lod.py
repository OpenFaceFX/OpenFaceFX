"""Offline LOD (level-of-detail) variant export (issue #36).

Game runtimes carry facial animation at several detail levels and thin it with
distance -- Unity's "Optimal" compression is keyframe reduction under an error
tolerance, and MetaHuman drops curve detail and updates at ~30 fps above LOD0.
OpenFaceFX already owns the exact machinery (`curves._rdp` / `edits.sample`), so
this is a pure re-run at a tiered tolerance table -- no ML, no engine, no camera.

From ONE solved track it produces K variants, finest first, along two tiers:

  * **RDP tier** -- re-run `_rdp` per channel at a tolerance table
    (e.g. ``eps = [0.002, 0.01, 0.04]``): LOD0 keeps the dense curves, higher
    tiers keep only the major inflections. A pure RDP tier only ever *selects* a
    subset of the source keyframes -- it never invents a key.
  * **fps tier** -- before thinning, step/linear-resample each channel onto a
    coarser time grid via `edits.sample` (e.g. 60/30/15 fps), so a distant LOD
    updates less often; the kept keys land only on that coarse grid.

A tier that keeps the source fps is pure-RDP (so LOD0 at the source epsilon is
**byte-identical** to the input); a coarser fps resamples first. Each variant is a
normal `FaceTrack` written to its own file; a ``*_lod.json`` metadata sidecar
names every variant's epsilon + fps and ships an advisory screen-coverage ->
LOD-index switching table (the engine owns the actual switch). The event/take
layer -- including ``FaceTrack.variants`` (issue #6), which is NOT overloaded for
LOD -- is carried through each variant unchanged. numpy + stdlib, deterministic.
"""

from __future__ import annotations

from typing import List, Optional, Tuple

import numpy as np

from .curves import Channel, FaceTrack, Keyframe, _rdp
from .edits import sample

#: Default RDP tolerance per LOD tier (finest first).
LOD_DEFAULT_RDP: List[float] = [0.002, 0.01, 0.04]
#: Default update rate per LOD tier (finest first); capped at the source fps.
LOD_DEFAULT_FPS: List[float] = [60.0, 30.0, 15.0]
#: Thinning tolerance for an fps-only tier list (no ``--rdp`` given).
_DEFAULT_EPS = 0.015


def _resolve_levels(rdp: Optional[List[float]], fps: Optional[List[float]],
                    source_fps: float) -> List[Tuple[float, float]]:
    """Align the ``--rdp`` and ``--fps`` tier lists into ``[(eps, fps), ...]``.

    Both default to :data:`LOD_DEFAULT_RDP` / :data:`LOD_DEFAULT_FPS`; giving only
    one keeps the other constant (the source fps for an RDP-only run, a default
    epsilon for an fps-only run). The two lists must end up the same length."""
    if rdp is None and fps is None:
        rdp, fps = list(LOD_DEFAULT_RDP), list(LOD_DEFAULT_FPS)
    elif fps is None:
        rdp = list(rdp)
        fps = [source_fps] * len(rdp)
    elif rdp is None:
        fps = list(fps)
        rdp = [_DEFAULT_EPS] * len(fps)
    else:
        rdp, fps = list(rdp), list(fps)
    if not rdp:
        raise ValueError("LOD needs at least one tier")
    if len(rdp) != len(fps):
        raise ValueError(f"--rdp and --fps must list the same number of tiers, "
                         f"got {len(rdp)} and {len(fps)}")
    for e in rdp:
        if not (isinstance(e, (int, float)) and not isinstance(e, bool)
                and e >= 0.0):
            raise ValueError(f"RDP epsilon must be a non-negative number, got {e!r}")
    for f in fps:
        if not (isinstance(f, (int, float)) and not isinstance(f, bool)
                and f > 0.0):
            raise ValueError(f"fps must be a positive number, got {f!r}")
    return [(float(e), float(f)) for e, f in zip(rdp, fps)]


def make_lod(track: FaceTrack, eps: float, fps: float) -> FaceTrack:
    """One LOD variant of ``track``. When ``fps >= track.fps`` it is a *pure RDP*
    tier -- each channel's existing keyframes re-thinned at ``eps`` (a subset,
    never invented), so ``eps <= the source epsilon`` reproduces the source
    exactly. A coarser ``fps`` resamples each channel onto that grid first, then
    thins, so the keys land only on the coarse grid."""
    if fps >= track.fps:
        channels = [_rdp_thin(c, eps) for c in track.channels]
        out_fps = track.fps
    else:
        grid = _grid(track.duration, fps)
        channels = _resample_thin(track.channels, grid, eps)
        out_fps = fps
    target_set = list(track.target_set) if track.target_set is not None else None
    out = FaceTrack(out_fps, channels, target_set)
    # Carry the event/take layer (issue #6) through unchanged -- LOD thins curves,
    # not notifies; FaceTrack.variants stays event-take alternatives, not LOD.
    out.events = list(getattr(track, "events", None) or [])
    out.variants = getattr(track, "variants", None)
    return out


def _rdp_thin(channel: Channel, eps: float) -> Channel:
    if len(channel.keys) <= 2:
        return Channel(channel.name, list(channel.keys))
    t = np.array([k.time for k in channel.keys], dtype=float)
    v = np.array([k.value for k in channel.keys], dtype=float)
    idx = _rdp(t, v, eps)
    return Channel(channel.name, [channel.keys[i] for i in idx])


def _grid(duration: float, fps: float) -> np.ndarray:
    n = int(round(duration * fps))
    if n < 1:
        return np.array([0.0], dtype=float)
    return np.array([i / fps for i in range(n + 1)], dtype=float)


def _resample_thin(channels, grid: np.ndarray, eps: float) -> List[Channel]:
    out: List[Channel] = []
    for c in channels:
        vals = np.round(sample(c, grid), 4)
        if not np.any(np.abs(vals) > 1e-3):           # magnitude, not sign:
            continue  # silent at this LOD; drop. |v| so an all-negative signed
            #           pose channel (e.g. eyeYaw in [-4, 0]) is not misread as
            #           "never fires" and dropped (issue #36 follow-up).
        idx = _rdp(grid, vals, eps)
        # Times stay full-precision grid points (matching reduce_to_track, which
        # rounds only values); to_dict rounds times to 4 dp on write like any
        # track, so the keys still land exactly on the coarse grid.
        out.append(Channel(c.name, [Keyframe(float(grid[i]), float(vals[i]))
                                    for i in idx]))
    return out


def generate_lods(track: FaceTrack, *, rdp: Optional[List[float]] = None,
                  fps: Optional[List[float]] = None
                  ) -> Tuple[List[FaceTrack], List[Tuple[float, float]]]:
    """Return ``(variants, levels)`` -- K deterministic LOD variants (finest
    first) and the resolved ``(eps, fps)`` per level."""
    levels = _resolve_levels(rdp, fps, track.fps)
    return [make_lod(track, eps, f) for eps, f in levels], levels


def switching_table(k: int) -> List[dict]:
    """Advisory screen-coverage -> LOD-index thresholds for ``k`` levels.

    ``lod`` i is the engine's pick while the face covers at least
    ``min_screen_height`` of the view height (Unity ``LODGroup``-style
    screen-relative size); the last level is the ``0.0`` fallback. OpenFaceFX has
    no camera at export, so the switch stays the engine's job -- this is advice."""
    return [{"lod": i,
             "min_screen_height": 0.0 if i == k - 1 else round(0.5 * 0.4 ** i, 4)}
            for i in range(k)]


def lod_metadata(track: FaceTrack, levels: List[Tuple[float, float]],
                 variants: List[FaceTrack], files: List[str]) -> dict:
    """The ``*_lod.json`` sidecar: source stats, one entry per variant naming its
    epsilon + fps + counts, and the advisory switching table. Plain JSON-ready."""
    entries = []
    for i, ((eps, _req_fps), variant, path) in enumerate(
            zip(levels, variants, files)):
        entries.append({
            "index": i,
            "file": path,
            "epsilon": round(eps, 6),
            "fps": variant.fps,
            "channels": len(variant.channels),
            "keyframes": sum(len(c.keys) for c in variant.channels),
        })
    return {
        "format": "openfacefx.lod",
        "version": 1,
        "source_fps": track.fps,
        "duration": round(track.duration, 4),
        "levels": entries,
        "switching": switching_table(len(levels)),
    }
