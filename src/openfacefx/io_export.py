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
    d = {
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
    # Additive event/take layer (issue #6): appended ONLY when present, and after
    # the base keys, so `version` stays 1 and an ordinary track serialises
    # byte-identically to previous releases. Readers ignore unknown top-level
    # keys, so this is forward-compatible in both directions.
    if getattr(track, "events", None):
        from .events import event_to_dict
        d["events"] = [event_to_dict(e) for e in track.events]
    if getattr(track, "variants", None) is not None:
        from .events import variants_to_dict
        d["variants"] = variants_to_dict(track.variants)
    return d


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
