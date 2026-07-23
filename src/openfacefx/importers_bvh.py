"""Read a Biovision Hierarchy (``.bvh``) motion-capture file's **head/eye
rotation** into a :class:`~openfacefx.curves.FaceTrack` as signed pose channels.

BVH is the lingua franca of skeletal mocap (every mocap suit, Blender, MotionBuilder,
Rokoko, Perception Neuron export it). It carries no blendshapes — but it *does*
carry the head and (sometimes) eye joints as Euler-degree rotation channels, which
is exactly OpenFaceFX's signed ``headPitch/headYaw/headRoll`` (and ``eyePitch/
eyeYaw``) pose model. So a captured head performance — nods, turns, tilts, gaze —
can be brought in to drive the same pose channels the VMD importer harvests from
頭/首 bones, then layered onto generated lip motion.

The head axes map straight through (BVH is already intrinsic Euler degrees, no
quaternion step): ``Xrotation → …Pitch``, ``Yrotation → …Yaw``, ``Zrotation →
…Roll``. Left/right eye joints, when present, are **averaged** into a single
``eyePitch/eyeYaw`` gaze. Columns are RDP-thinned to a track via
:func:`~openfacefx.curves.reduce_to_track` (the pose values pass through
unclamped). Dead all-zero axes are dropped.

BVH axis conventions vary by exporter, so the **sign** of a given nod/turn may
need flipping for a particular rig — the values are faithful to the file. Pure
stdlib + numpy, deterministic.
"""

from __future__ import annotations

from typing import Dict, List, Optional, Tuple

import numpy as np

from .curves import Channel, FaceTrack, Keyframe, _rdp

_ROT = ("Xrotation", "Yrotation", "Zrotation")
# BVH rotation axis -> signed pose-channel suffix (straight-through Euler degrees).
_AXIS_TO_POSE = {"Xrotation": "Pitch", "Yrotation": "Yaw", "Zrotation": "Roll"}

_DEFAULT_FPS = 30.0


def _parse_hierarchy(tokens: List[str]) -> Tuple[List[Tuple[str, List[str]]], int]:
    """Walk the HIERARCHY tokens into an ordered ``[(joint_name, [channels])]``
    layout (file/DFS order = the MOTION column order) and return the index of the
    ``MOTION`` token. Only ROOT/JOINT names and their CHANNELS matter for the flat
    column layout; braces, OFFSET and ``End Site`` blocks (no CHANNELS) are skipped.
    """
    if not tokens or tokens[0].upper() != "HIERARCHY":
        raise ValueError("bvh: file does not start with HIERARCHY")
    layout: List[Tuple[str, List[str]]] = []
    pending: Optional[str] = None
    i = 1
    n = len(tokens)
    while i < n:
        tu = tokens[i].upper()
        if tu == "MOTION":
            return layout, i
        if tu in ("ROOT", "JOINT"):
            if i + 1 >= n:
                raise ValueError("bvh: joint declaration without a name")
            pending = tokens[i + 1]
            i += 2
            continue
        if tu == "CHANNELS":
            if i + 1 >= n:
                raise ValueError("bvh: CHANNELS without a count")
            cnt = int(tokens[i + 1])
            chans = tokens[i + 2:i + 2 + cnt]
            if len(chans) != cnt:
                raise ValueError("bvh: CHANNELS count exceeds available tokens")
            layout.append((pending or f"joint{len(layout)}", chans))
            i += 2 + cnt
            continue
        i += 1
    raise ValueError("bvh: no MOTION section")


def _read_motion(tokens: List[str], start: int
                 ) -> Tuple[int, float, List[str], List[str]]:
    """From the ``MOTION`` token, read ``Frames:`` / ``Frame Time:`` and return
    ``(frames, frame_time, motion_value_tokens, warnings)``."""
    warnings: List[str] = []
    frames: Optional[int] = None
    dt: Optional[float] = None
    j = start + 1
    n = len(tokens)
    while j < n:
        key = tokens[j].rstrip(":").lower()
        if key == "frames" and j + 1 < n:
            frames = int(float(tokens[j + 1]))
            j += 2
            continue
        if key == "time" and j + 1 < n:
            dt = float(tokens[j + 1])
            j += 2
            continue
        if key == "frame":            # first half of "Frame Time:"
            j += 1
            continue
        break                          # first data token
    if frames is None:
        raise ValueError("bvh: MOTION section missing 'Frames:'")
    if dt is None or dt <= 0.0:
        warnings.append("bvh: missing/invalid 'Frame Time:'; assuming 30 fps")
        dt = 1.0 / _DEFAULT_FPS
    return frames, dt, tokens[j:], warnings


def _joint_columns(layout: List[Tuple[str, List[str]]], keyword: str
                   ) -> Optional[Dict[str, int]]:
    """Global column index of each channel of the first joint whose (lower-cased)
    name contains ``keyword`` — ``{channel_name: column}`` — or ``None``."""
    col = 0
    for name, chans in layout:
        if keyword in name.lower():
            return {c: col + k for k, c in enumerate(chans)}
        col += len(chans)
    return None


def _all_joint_columns(layout: List[Tuple[str, List[str]]], keyword: str
                       ) -> List[Dict[str, int]]:
    """Column maps for *every* joint whose name contains ``keyword`` (both eyes)."""
    out: List[Dict[str, int]] = []
    col = 0
    for name, chans in layout:
        if keyword in name.lower():
            out.append({c: col + k for k, c in enumerate(chans)})
        col += len(chans)
    return out


def parse_bvh(text: str, *, fps: Optional[float] = None, epsilon: float = 0.1,
              head: bool = True, eyes: bool = True) -> Tuple[FaceTrack, List[str]]:
    """Parse BVH text into ``(track, warnings)``.

    Extracts the head joint's rotation into ``headPitch/headYaw/headRoll`` (neck as
    a fallback), and averages any eye joints into ``eyePitch/eyeYaw``. ``fps``
    overrides the file's Frame Time; ``epsilon`` is the RDP tolerance in degrees.
    """
    warnings: List[str] = []
    tokens = text.split()
    layout, motion_i = _parse_hierarchy(tokens)
    total = sum(len(chs) for _, chs in layout)
    if total == 0:
        raise ValueError("bvh: hierarchy declares no motion channels")

    frames, dt, values, mwarn = _read_motion(tokens, motion_i)
    warnings += mwarn
    if fps is not None and fps > 0:
        dt = 1.0 / float(fps)
    have = len(values) // total
    if have < frames:
        warnings.append(f"bvh: motion block has {have} of {frames} frames; using {have}")
        frames = have
    if frames <= 0:
        return FaceTrack(fps=fps or _DEFAULT_FPS, channels=[]), warnings + ["bvh: no frames"]

    data = np.asarray(values[:frames * total], dtype=np.float64).reshape(frames, total)

    # collect (channel_name, per-frame series) for head + eyes
    cols: List[Tuple[str, np.ndarray]] = []
    if head:
        hit = _joint_columns(layout, "head") or _joint_columns(layout, "neck")
        if hit is None:
            warnings.append("bvh: no head/neck joint found")
        else:
            for axis in _ROT:
                if axis in hit:
                    cols.append(("head" + _AXIS_TO_POSE[axis], data[:, hit[axis]]))
    if eyes:
        eye_maps = _all_joint_columns(layout, "eye")
        for axis in ("Xrotation", "Yrotation"):     # gaze: pitch + yaw, no roll
            series = [data[:, m[axis]] for m in eye_maps if axis in m]
            if series:
                cols.append(("eye" + _AXIS_TO_POSE[axis],
                             np.mean(series, axis=0)))

    # drop dead all-zero axes (a held non-zero tilt is kept)
    cols = [(nm, s) for nm, s in cols if float(np.max(np.abs(s))) > 1e-6]
    if not cols:
        return (FaceTrack(fps=fps or round(1.0 / dt, 3), channels=[]),
                warnings + ["bvh: no non-zero head/eye rotation to import"])

    # Build channels directly with RDP — NOT reduce_to_track, whose positive-only
    # "never fires" filter (col > 1e-3) and [0,1] clamp are for weight channels and
    # would drop an all-negative pose axis (e.g. a head turned only one way).
    times = np.arange(frames, dtype=np.float64) * dt
    fps_out = float(fps) if (fps and fps > 0) else round(1.0 / dt, 3)
    channels: List[Channel] = []
    for nm, series in cols:
        col = np.asarray(series, dtype=np.float64)
        idx = _rdp(times, col, epsilon)
        keys = [Keyframe(float(times[i]), round(float(col[i]), 4)) for i in idx]
        channels.append(Channel(nm, keys))
    return FaceTrack(fps=fps_out, channels=channels), warnings


def read_bvh(path: str, *, fps: Optional[float] = None, epsilon: float = 0.1,
             head: bool = True, eyes: bool = True) -> Tuple[FaceTrack, List[str]]:
    """Read a ``.bvh`` file's head/eye rotation into a track (see :func:`parse_bvh`)."""
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        return parse_bvh(fh.read(), fps=fps, epsilon=epsilon, head=head, eyes=eyes)
