"""Export a FaceTrack's event layer as an Unreal **AnimNotify sidecar** JSON.

Unlike Unity's ``.anim`` (hand-writable YAML), Unreal has no text anim asset a
tool can author directly -- ``UAnimSequence`` / ``UAnimMontage`` are binary
``.uasset``. The supported path is to emit a small JSON *sidecar* next to the
audio/animation and let a one-off **editor Python** utility stamp the notifies
onto the imported sequence via the official ``unreal`` API. That mirrors how the
FaceFX-UE plugin drives gameplay: notifies carry the payload, and a Blueprint
``OnAnimationEvent`` node (or named vars on a notify subclass) reads it.

Each event becomes one ``FAnimNotifyEvent``-shaped record::

    {"notify_name": "nod_small", "trigger_time": 1.234, "duration": 0.0,
     "notify_class": "gesture", "payload": {"intensity": 0.6},
     "blend_in": 0.08, "blend_out": 0.12}

``duration == 0`` is a point ``UAnimNotify`` (a single ``Received_Notify``);
``duration > 0`` is a ranged ``UAnimNotifyState`` (``Received_NotifyBegin`` /
``Tick`` / ``End``). The FaceFX UE4/UE5 runtime dropped FaceFX-style *string*
payloads (they were Legacy FxSDK), so on Unreal the ``payload`` is meant to be
surfaced through named variables on the notify subclass rather than a raw string
blob -- hence it is kept as a structured object here, not packed into a string
the way the single-parameter Unity ``AnimationEvent`` requires.

Ready-to-run editor snippet (paste into the UE Python console; needs the Editor
Scripting Utilities plugin)::

    import json, unreal
    data = json.load(open(r"C:/path/to/line.notifies.json"))
    seq  = unreal.load_asset(r"/Game/Anims/line")          # a UAnimSequence
    lib  = unreal.AnimationLibrary
    for ev in data["events"]:
        t = ev["trigger_time"]
        if ev["duration"] > 0.0:
            lib.add_animation_notify_state_event(
                seq, ev["notify_name"], t, ev["duration"])
        else:
            lib.add_animation_notify_event(seq, ev["notify_name"], t)
    # Read ev["payload"] to set named vars on your notify subclass as needed.

stdlib only (``json``); reuses :func:`openfacefx.events.resolve` so takes and
ranged events resolve identically to the other exporters.
"""

from __future__ import annotations

import json
from typing import Dict, List

from .curves import FaceTrack
from .events import resolve

#: JSON envelope version -- bumped only on a breaking change to the record shape,
#: independent of the track format's ``version``.
NOTIFY_FORMAT_VERSION = 1


def notifies_to_dict(track: FaceTrack) -> Dict:
    """The AnimNotify sidecar as a JSON-ready dict: an envelope plus one
    ``FAnimNotifyEvent``-shaped record per resolved event (ascending time)."""
    records: List[Dict] = []
    for e in resolve(track):
        records.append({
            "notify_name": e.name,
            "trigger_time": round(float(e.t), 4),
            "duration": round(float(e.dur), 4),
            "notify_class": e.type,
            "payload": e.payload,
            "blend_in": round(float(e.blend_in), 4),
            "blend_out": round(float(e.blend_out), 4),
        })
    return {
        "format": "openfacefx.unreal_notifies",
        "version": NOTIFY_FORMAT_VERSION,
        "events": records,
    }


def write_unreal_notifies(track: FaceTrack, path: str) -> None:
    """Write ``track``'s event layer as an Unreal AnimNotify sidecar JSON (see
    the module docstring for the editor Python that consumes it). Writes an
    envelope with an empty ``events`` list when the track has no events, so the
    file is always valid and the consumer needs no special-casing."""
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(notifies_to_dict(track), fh, indent=2)
