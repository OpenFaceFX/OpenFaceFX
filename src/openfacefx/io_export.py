"""Exporters. Keep formats simple and engine-agnostic.

  * ``to_dict`` / ``write_json`` -- canonical interchange format.
  * ``from_dict`` / ``read_json`` -- the inverse loaders (read a ``.track.json``
    back into a :class:`FaceTrack`, e.g. to diff a hand-edited track, issue #9).
  * ``write_csv``  -- one row per keyframe (time, channel, value), easy to load
    into a spreadsheet or a DAW-style curve editor.

Engine-specific exporters (Unreal AnimCurve, glTF morph-target animation,
Blender F-curves) can be layered on top of ``FaceTrack`` without touching the
solver.
"""

from __future__ import annotations

import json
from typing import Dict, Optional

from .curves import Channel, FaceTrack, Keyframe
from .visemes import VISEMES


def to_dict(track: FaceTrack, source_id: Optional[str] = None) -> Dict:
    """Serialise a track to the canonical dict. ``source_id`` (issue #9) is an
    optional stable id for the source audio/alignment; when given it is embedded
    so an ``openfacefx.edits`` sidecar can be keyed to it. Omitted by default, so
    an ordinary track is byte-identical to previous releases."""
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
    # Optional edit-preservation source id (issue #9) and the additive event/take
    # layer (issue #6): appended ONLY when present, and after the base keys, so
    # `version` stays 1 and an ordinary track serialises byte-identically to
    # previous releases. Readers ignore unknown top-level keys, so this is
    # forward-compatible in both directions.
    if source_id is not None:
        d["source_id"] = source_id
    if getattr(track, "events", None):
        from .events import event_to_dict
        d["events"] = [event_to_dict(e) for e in track.events]
    if getattr(track, "variants", None) is not None:
        from .events import variants_to_dict
        d["variants"] = variants_to_dict(track.variants)
    return d


def from_dict(d: Dict) -> FaceTrack:
    """Inverse of :func:`to_dict`: parse a track dict back into a
    :class:`FaceTrack`, including its optional event/take layer. A ``viseme_set``
    equal to the built-in Oculus set restores the ``target_set=None`` sentinel, so
    ``to_dict(from_dict(d)) == d`` byte-for-byte. Unknown top-level keys (e.g.
    ``source_id``) are ignored, per the additive forward-compat rule."""
    if d.get("format") != "openfacefx.track" or d.get("version") != 1:
        raise ValueError(
            f"expected format 'openfacefx.track' version 1, got "
            f"{d.get('format')!r} version {d.get('version')!r}")
    channels = [
        Channel(str(c["name"]),
                [Keyframe(float(t), float(v)) for t, v in c["keys"]])
        for c in d.get("channels", [])
    ]
    vs = d.get("viseme_set")
    target_set = None if (vs is None or list(vs) == VISEMES) else list(vs)
    track = FaceTrack(fps=float(d["fps"]), channels=channels, target_set=target_set)
    from .events import read_events
    track.events, track.variants = read_events(d)
    return track


def read_json(path: str) -> FaceTrack:
    """Load a ``.track.json`` file into a :class:`FaceTrack` (see :func:`from_dict`)."""
    with open(path, encoding="utf-8") as fh:
        return from_dict(json.load(fh))


def write_json(track: FaceTrack, path: str, source_id: Optional[str] = None) -> None:
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(to_dict(track, source_id=source_id), fh, indent=2)


def write_csv(track: FaceTrack, path: str) -> None:
    rows = ["time,channel,value"]
    for ch in track.channels:
        for k in ch.keys:
            rows.append(f"{k.time:.4f},{ch.name},{k.value:.4f}")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(rows) + "\n")
