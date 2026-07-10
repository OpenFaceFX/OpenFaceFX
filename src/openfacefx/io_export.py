"""Exporters. Keep formats simple and engine-agnostic.

  * ``to_dict`` / ``write_json`` -- canonical interchange format.
  * ``write_csv``  -- one row per keyframe (time, channel, value), easy to load
    into a spreadsheet or a DAW-style curve editor.

Engine-specific exporters (Unreal AnimCurve, glTF morph-target animation,
Blender F-curves) can be layered on top of ``FaceTrack`` without touching the
solver.
"""

from __future__ import annotations

import json
from typing import Dict

from .curves import FaceTrack
from .visemes import VISEMES


def to_dict(track: FaceTrack) -> Dict:
    return {
        "format": "openfacefx.track",
        "version": 1,
        "fps": track.fps,
        "duration": round(track.duration, 4),
        "viseme_set": track.target_set if track.target_set is not None else VISEMES,
        "channels": [
            {
                "name": ch.name,
                "keys": [[round(k.time, 4), k.value] for k in ch.keys],
            }
            for ch in track.channels
        ],
    }


def write_json(track: FaceTrack, path: str) -> None:
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(to_dict(track), fh, indent=2)


def write_csv(track: FaceTrack, path: str) -> None:
    rows = ["time,channel,value"]
    for ch in track.channels:
        for k in ch.keys:
            rows.append(f"{k.time:.4f},{ch.name},{k.value:.4f}")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(rows) + "\n")
