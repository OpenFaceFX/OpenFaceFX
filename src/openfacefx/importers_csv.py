"""Import blendshape-weight CSV into a FaceTrack (issue #45).

The CSV half of :mod:`openfacefx.importers`. A lot of face animation lives as
per-frame blendshape weights — Apple ARKit's 52 coefficients recorded by Epic's
Live Link Face app, or exported by capture tools and DCCs — and OpenFaceFX could
``write_csv`` but never read one back. Two layouts, auto-detected from the header:

  * **OpenFaceFX long CSV** ``time,channel,value`` — the exact inverse of
    :func:`openfacefx.io_export.write_csv`; a byte-clean round-trip.
  * **Wide per-frame CSV** — one row per frame, one column per blendshape name,
    with an optional leading ``Timecode`` / ``BlendShapeCount`` header as Live
    Link Face emits. The timecode (or the row index) converts to seconds via
    ``fps``/``timecode_col`` and each column is RDP-thinned into sparse keys with
    :func:`openfacefx.curves.reduce_to_track`.

Channel names land in **rig space** verbatim (``jawOpen``, ``mouthSmileLeft`` …)
and values are clamped to ``[0, 1]``. The forward viseme→ARKit map is
many-to-one, so this deliberately does **not** recover visemes — it brings the raw
channels in so they can be conditioned (``--smooth``/``--lag``), layered and
re-exported. numpy + stdlib (``csv``) only, deterministic (fixed RDP thinner,
stable 4-dp rounding — identical on Python 3.9/3.13), and additive.
"""

from __future__ import annotations

import csv as _csv
from collections import OrderedDict, namedtuple
from typing import List, Optional, Tuple

import numpy as np

from .curves import Channel, FaceTrack, Keyframe, reduce_to_track

#: The OpenFaceFX long-format header (inverse of ``io_export.write_csv``).
_LONG_HEADER = ["time", "channel", "value"]
#: Wide-CSV columns that are metadata, not a blendshape weight (Live Link Face).
_META_COLUMNS = {"blendshapecount"}
#: Live Link Face's default capture rate; the wide layout's frame→seconds default.
_DEFAULT_FPS = 60.0

# reduce_to_track only reads ``.name``/``.lo``/``.hi`` off each target, so a
# minimal record keeps the blendshape names verbatim without the articulator
# machinery of ``mapping.Target``.
_Col = namedtuple("_Col", "name lo hi")


def _clamp01(v: float) -> float:
    return 0.0 if v < 0.0 else 1.0 if v > 1.0 else v


def read_csv(path: str, *, fps: float = _DEFAULT_FPS,
             timecode_col: Optional[str] = None,
             epsilon: float = 0.015) -> Tuple[FaceTrack, List[str]]:
    """Read a blendshape-weight CSV into ``(FaceTrack, warnings)``.

    Auto-detects the OpenFaceFX long ``time,channel,value`` layout (the exact
    inverse of :func:`~openfacefx.io_export.write_csv`) versus a wide per-frame
    layout. ``fps`` times the wide rows (frame→seconds); ``timecode_col`` names
    the SMPTE-timecode column (else a literal ``Timecode`` column, else the row
    index drives the timeline). ``warnings`` reports any column whose values were
    clamped into ``[0, 1]`` (e.g. a non-blendshape angle column)."""
    with open(path, newline="", encoding="utf-8") as fh:
        rows = list(_csv.reader(fh))
    if not rows:
        return FaceTrack(fps=fps, channels=[], target_set=None), []
    header = [h.strip() for h in rows[0]]
    body = rows[1:]
    if [h.lower() for h in header] == _LONG_HEADER:
        return _read_long(body, fps), []
    return _read_wide(header, body, fps, timecode_col, epsilon)


# --------------------------------------------------------------------------- #
# (a) OpenFaceFX long CSV -- inverse of write_csv                             #
# --------------------------------------------------------------------------- #

def _read_long(body: List[List[str]], fps: float) -> FaceTrack:
    channels: "OrderedDict[str, List[Keyframe]]" = OrderedDict()
    for i, row in enumerate(body):
        if not row or all(not c.strip() for c in row):
            continue
        if len(row) != 3:
            raise ValueError(f"long CSV line {i + 2}: expected 3 fields "
                             f"'time,channel,value', got {len(row)}: {row!r}")
        t_s, name, v_s = row
        try:
            t, v = float(t_s), float(v_s)
        except ValueError:
            raise ValueError(f"long CSV line {i + 2}: non-numeric time/value "
                             f"{row!r}") from None
        channels.setdefault(name.strip(), []).append(
            Keyframe(t, round(_clamp01(v), 4)))
    chans = [Channel(name, keys) for name, keys in channels.items()]
    return FaceTrack(fps=fps, channels=chans,
                     target_set=[c.name for c in chans] or None)


# --------------------------------------------------------------------------- #
# (b) Wide per-frame CSV -- ARKit / Live Link Face                            #
# --------------------------------------------------------------------------- #

def _timecode_index(header: List[str], timecode_col: Optional[str]) -> Optional[int]:
    if timecode_col is not None:
        for j, name in enumerate(header):
            if name.lower() == timecode_col.lower():
                return j
        raise ValueError(f"--timecode-col {timecode_col!r} not found in header "
                         f"{header!r}")
    for j, name in enumerate(header):
        if name.lower() == "timecode":
            return j
    return None


def _parse_timecode(tc: str, fps: float) -> float:
    """SMPTE ``HH:MM:SS:FF`` (the frame field may carry a fractional subframe) to
    seconds, or a bare number as seconds. ``fps`` sizes the frame field."""
    tc = tc.strip()
    if ":" not in tc:
        try:
            return float(tc)
        except ValueError:
            raise ValueError(f"unparseable timecode {tc!r}") from None
    parts = tc.split(":")
    if len(parts) != 4:
        raise ValueError(
            f"timecode {tc!r} must be SMPTE 'HH:MM:SS:FF' or plain seconds")
    try:
        h, m, s = int(parts[0]), int(parts[1]), int(parts[2])
        frame = float(parts[3])
    except ValueError:
        raise ValueError(f"unparseable SMPTE timecode {tc!r}") from None
    return h * 3600.0 + m * 60.0 + s + frame / fps


def _read_wide(header: List[str], body: List[List[str]], fps: float,
               timecode_col: Optional[str], epsilon: float
               ) -> Tuple[FaceTrack, List[str]]:
    if not header:
        raise ValueError("wide CSV: missing header row of column names")
    tc_idx = _timecode_index(header, timecode_col)
    data_idx = [j for j, name in enumerate(header)
                if j != tc_idx and name.lower() not in _META_COLUMNS]
    names = [header[j] for j in data_idx]
    if not names:
        raise ValueError(f"wide CSV: no blendshape weight columns in header {header!r}")
    if len(set(names)) != len(names):
        raise ValueError(f"wide CSV: duplicate column names in header {header!r}")

    times: List[float] = []
    cols: List[List[float]] = [[] for _ in data_idx]
    for i, row in enumerate(body):
        if not row or all(not c.strip() for c in row):
            continue
        if len(row) != len(header):
            raise ValueError(f"wide CSV line {i + 2}: {len(row)} fields but the "
                             f"header declares {len(header)}")
        times.append(i / fps if tc_idx is None
                     else _parse_timecode(row[tc_idx], fps))
        for k, j in enumerate(data_idx):
            try:
                cols[k].append(float(row[j]))
            except ValueError:
                raise ValueError(f"wide CSV line {i + 2}, column {header[j]!r}: "
                                 f"non-numeric weight {row[j]!r}") from None
    if not times:
        return FaceTrack(fps=fps, channels=[], target_set=None), []

    T = np.asarray(times, dtype=float)
    if np.any(np.diff(T) < 0):
        raise ValueError("wide CSV: timecodes are not non-decreasing")
    raw = np.asarray(cols, dtype=float).T                 # (frames, channels)
    warnings: List[str] = []
    for k, name in enumerate(names):
        col = raw[:, k]
        if np.any(col < 0.0) or np.any(col > 1.0):
            warnings.append(f"column {name!r} had values outside [0, 1]; clamped "
                            f"(is it a non-blendshape angle column?)")
    matrix = np.clip(raw, 0.0, 1.0)
    targets = [_Col(name, 0.0, 1.0) for name in names]
    track = reduce_to_track(T, matrix, fps=fps, epsilon=epsilon, targets=targets)
    return track, warnings
