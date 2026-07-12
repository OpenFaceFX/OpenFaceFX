"""Export a FaceTrack as an Apple ARKit / Epic **Live Link Face** wide CSV (#61).

The write side of :mod:`openfacefx.importers_csv`'s wide branch. We can already
*read* the per-frame CSV Epic's Live Link Face app records, but never *wrote* one,
so a synthetic OpenFaceFX performance couldn't be replayed as a Live Link Face
take. This is that missing inverse: one row per frame, one column per ARKit
blendshape, in the layout MetaHuman Animator / Unreal Live Link and the DCC
retarget tools (MotionBuilder, Blender, Maya) consume.

The header is the canonical 61-column Live Link Face order (verified against the
``FaceBlendShape`` enum in JimWest/PyLiveLinkFace):
``Timecode,BlendShapeCount,`` then the **52 ARKit** ``ARFaceAnchor.BlendShapeLocation``
coefficients (PascalCase — Epic capitalises Apple's camelCase identifiers) and
**9 head/eye rotation** columns. ``BlendShapeCount`` is 61.

Channels are matched into their columns **case-insensitively**, so a rig-space
track (``jawOpen``, ``mouthSmileLeft`` …) lands in ``JawOpen``/``MouthSmileLeft``
directly; the many→one viseme→ARKit map lives in the ``arkit`` retarget preset, so
a viseme track should be retargeted first (CLI ``--retarget arkit``). Columns with
no matching channel are written ``0.000000``. The 9 head/eye rotation columns are
emitted **zero-filled** for now — they carry rotation *angles*, not ``[0,1]``
weights, and Live Link's exact rotation-unit convention is not headlessly
verifiable; head-pose export is a follow-up.

``Timecode`` is SMPTE ``HH:MM:SS:FF`` at ``fps`` (default 60, Live Link's rate),
the exact form :func:`importers_csv._parse_timecode` inverts. numpy + stdlib only,
deterministic (fixed ``%.6f`` and integer-frame timecodes — identical on py3.9 and
py3.13), and additive.
"""

from __future__ import annotations

from typing import List, Optional, Tuple

import numpy as np

from .curves import FaceTrack
from .edits import sample as _sample

# The 52 ARKit ARFaceAnchor.BlendShapeLocation coefficients in Live Link Face's
# canonical order (indices 0-51 of the FaceBlendShape enum), PascalCase as the CSV
# header spells them.
_ARKIT_52: List[str] = [
    "EyeBlinkLeft", "EyeLookDownLeft", "EyeLookInLeft", "EyeLookOutLeft",
    "EyeLookUpLeft", "EyeSquintLeft", "EyeWideLeft",
    "EyeBlinkRight", "EyeLookDownRight", "EyeLookInRight", "EyeLookOutRight",
    "EyeLookUpRight", "EyeSquintRight", "EyeWideRight",
    "JawForward", "JawLeft", "JawRight", "JawOpen",
    "MouthClose", "MouthFunnel", "MouthPucker", "MouthLeft", "MouthRight",
    "MouthSmileLeft", "MouthSmileRight", "MouthFrownLeft", "MouthFrownRight",
    "MouthDimpleLeft", "MouthDimpleRight", "MouthStretchLeft", "MouthStretchRight",
    "MouthRollLower", "MouthRollUpper", "MouthShrugLower", "MouthShrugUpper",
    "MouthPressLeft", "MouthPressRight", "MouthLowerDownLeft", "MouthLowerDownRight",
    "MouthUpperUpLeft", "MouthUpperUpRight",
    "BrowDownLeft", "BrowDownRight", "BrowInnerUp", "BrowOuterUpLeft",
    "BrowOuterUpRight",
    "CheekPuff", "CheekSquintLeft", "CheekSquintRight",
    "NoseSneerLeft", "NoseSneerRight",
    "TongueOut",
]
# The 9 trailing head/eye rotation columns (indices 52-60).
_HEAD_EYE_9: List[str] = [
    "HeadYaw", "HeadPitch", "HeadRoll",
    "LeftEyeYaw", "LeftEyePitch", "LeftEyeRoll",
    "RightEyeYaw", "RightEyePitch", "RightEyeRoll",
]
#: All 61 Live Link Face data columns, in order (52 blendshapes + 9 head/eye).
LIVELINK_COLUMNS: List[str] = _ARKIT_52 + _HEAD_EYE_9
_BLENDSHAPE_COUNT = len(LIVELINK_COLUMNS)          # 61
_DEFAULT_FPS = 60.0


def _validate_fps(fps) -> float:
    if not (isinstance(fps, (int, float)) and not isinstance(fps, bool)
            and 0.0 < float(fps) < float("inf")):
        raise ValueError(f"livelink: fps must be a finite value > 0, got {fps!r}")
    return float(fps)


def _timecode(frame: int, nf: int) -> str:
    """SMPTE ``HH:MM:SS:FF`` for a frame index, ``nf`` integer frames per second."""
    s_total, ff = divmod(frame, nf)
    h, rem = divmod(s_total, 3600)
    m, s = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{s:02d}:{ff:02d}"


def livelink_csv_string(track: FaceTrack, *, fps: Optional[float] = None
                        ) -> Tuple[str, int]:
    """Return ``(csv_text, matched)`` — the Live Link Face CSV for ``track`` and the
    number of the 52 ARKit columns that a track channel populated (0 usually means
    the track is still in viseme space; retarget to ``arkit`` first)."""
    fps = _DEFAULT_FPS if fps is None else _validate_fps(fps)
    nf = max(1, int(round(fps)))
    n = int(round(track.duration * fps)) + 1           # rows, inclusive of frame 0
    grid = np.arange(n, dtype=float) / fps

    lut = {c.name.lower(): c for c in track.channels}
    columns: List[np.ndarray] = []
    matched = 0
    for name in _ARKIT_52:
        ch = lut.get(name.lower())
        if ch is not None:
            matched += 1
            columns.append(np.clip(_sample(ch, grid), 0.0, 1.0))
        else:
            columns.append(np.zeros(n))
    columns.extend(np.zeros(n) for _ in _HEAD_EYE_9)   # head/eye: zero-filled (v1)

    lines = ["Timecode,BlendShapeCount," + ",".join(LIVELINK_COLUMNS)]
    count = str(_BLENDSHAPE_COUNT)
    for i in range(n):
        row = [_timecode(i, nf), count]
        row.extend(f"{col[i]:.6f}" for col in columns)
        lines.append(",".join(row))
    return "\n".join(lines) + "\n", matched


def write_livelink_csv(track: FaceTrack, path: str, *,
                       fps: Optional[float] = None) -> int:
    """Write ``track`` as an ARKit / Live Link Face wide CSV (see
    :func:`livelink_csv_string`). Returns the number of ARKit columns a channel
    populated, so a caller can warn on a still-viseme-space track."""
    text, matched = livelink_csv_string(track, fps=fps)
    with open(path, "w", newline="", encoding="utf-8") as fh:
        fh.write(text)
    return matched
