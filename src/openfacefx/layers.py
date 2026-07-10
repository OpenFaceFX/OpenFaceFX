"""Layered multi-track export (issue #39).

Engines want to re-blend or toggle facial layers at runtime rather than receive
one flattened curve set -- Unreal Sequencer/Control Rig keep lip-sync and
expression on separate additive tracks, and SALSA blends layers by priority. The
pipeline already produces speech / emotion / gesture on **disjoint** channels
before they are concatenated into one :class:`~openfacefx.curves.FaceTrack`, so
this is a data-reshuffle of arrays it already has plus a little metadata; the
runtime mix stays the engine's job.

:func:`build_layers` decomposes a merged track into named layers by channel
classification (``gesture`` = :data:`openfacefx.gestures.GESTURE_CHANNELS`,
``emotion`` = :data:`openfacefx.emotion.VA_EMOTION_CHANNELS`, ``speech`` =
everything else -- visemes and any rig blendshapes), each carrying its channel
list, a per-layer **blend-weight curve** (default constant ``1.0``) and an integer
**priority** (engine layering order). Because every channel lands in exactly one
layer, :func:`flatten_layers` summing them at weight 1 reproduces the merged
channels exactly -- a faithful decomposition. Empty layers are omitted.

The layers ride as an optional ``layers`` block on the track JSON
(:func:`openfacefx.io_export.to_dict` with ``layers=``); the flat channel list
stays the default, so a track without layers is byte-identical to before and a
reader that ignores the block still sees the merged track. Events (including
prosody, issue #4) remain the track's own event layer, unchanged -- prosody drives
notifies, not curves, so it is not a channel layer. numpy + stdlib, deterministic.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List

from .curves import Channel, FaceTrack, Keyframe
from .edits import sample

#: A constant full-blend weight curve.
FULL_WEIGHT: List[List[float]] = [[0.0, 1.0]]

#: The named layers a merged track decomposes into, with their default engine
#: priority (higher = layered on top). Speech is the base; expression and the
#: non-verbal gestures ride above it.
_LAYER_PRIORITY = (("speech", 0), ("emotion", 10), ("gesture", 20))


@dataclass
class Layer:
    """One named sub-track: a normal channel list plus a blend-weight curve
    (``[[t, w], ...]``, default constant ``1.0``) and an integer ``priority``
    (engine layering order). The weight and priority are metadata for the runtime
    mix; OpenFaceFX only uses ``weight`` when it flattens for verification."""
    name: str
    channels: List[Channel] = field(default_factory=list)
    weight: List[List[float]] = field(default_factory=lambda: [[0.0, 1.0]])
    priority: int = 0


def build_layers(track: FaceTrack) -> List[Layer]:
    """Decompose a merged ``track`` into named layers by channel classification.

    Every channel lands in exactly one layer (``gesture`` / ``emotion`` / the
    ``speech`` base), so the split is lossless; empty layers are omitted. Each
    layer gets a constant full-blend weight and its default priority."""
    from .gestures import GESTURE_CHANNELS
    from .emotion import VA_EMOTION_CHANNELS
    groups = {"speech": [], "emotion": [], "gesture": []}
    for c in track.channels:
        if c.name in GESTURE_CHANNELS:
            groups["gesture"].append(c)
        elif c.name in VA_EMOTION_CHANNELS:
            groups["emotion"].append(c)
        else:
            groups["speech"].append(c)
    return [Layer(name, groups[name], [[0.0, 1.0]], priority)
            for name, priority in _LAYER_PRIORITY if groups[name]]


def flatten_layers(layers: List[Layer], fps: float = 60.0) -> FaceTrack:
    """Sum ``layers`` back into one :class:`FaceTrack`, each channel scaled by its
    layer's blend-weight curve and channels of the same name added on a shared
    grid. At weight 1 (the default) this reproduces the merged channels -- the
    faithful-decomposition check. ``priority`` is engine metadata, ignored here."""
    from collections import OrderedDict
    merged: "OrderedDict[str, Channel]" = OrderedDict()
    for layer in layers:
        for ch in layer.channels:
            scaled = _scale(ch, layer.weight)
            merged[ch.name] = (_sum(merged[ch.name], scaled)
                               if ch.name in merged else scaled)
    return FaceTrack(fps=fps, channels=list(merged.values()), target_set=None)


def _is_full(weight: List[List[float]]) -> bool:
    return all(w == 1.0 for _t, w in weight)


def _scale(ch: Channel, weight: List[List[float]]) -> Channel:
    if _is_full(weight):
        return Channel(ch.name, [Keyframe(k.time, k.value) for k in ch.keys])
    return Channel(ch.name, [Keyframe(k.time, k.value * float(sample(weight, [k.time])[0]))
                             for k in ch.keys])


def _sum(a: Channel, b: Channel) -> Channel:
    times = sorted({k.time for k in a.keys} | {k.time for k in b.keys})
    return Channel(a.name, [Keyframe(t, float(sample(a, [t])[0] + sample(b, [t])[0]))
                            for t in times])


def layers_to_dict(layers: List[Layer]) -> List[dict]:
    """Serialise layers to the JSON block: a list of ``{name, priority, weight,
    channels}`` (channels/weights rounded to 4 dp, exactly like track keys)."""
    return [{
        "name": layer.name,
        "priority": int(layer.priority),
        "weight": [[round(float(t), 4), float(w)] for t, w in layer.weight],
        "channels": [
            {"name": c.name,
             "keys": [[round(k.time, 4), k.value] for k in c.keys]}
            for c in layer.channels
        ],
    } for layer in layers]


def layers_from_dict(raw) -> List[Layer]:
    """Inverse of :func:`layers_to_dict`; raises ``ValueError`` on a malformed
    block so a corrupt ``layers`` key fails loudly rather than silently."""
    if not isinstance(raw, list):
        raise ValueError("'layers' must be a JSON array of layer objects")
    out: List[Layer] = []
    for i, layer in enumerate(raw):
        if not isinstance(layer, dict) or "name" not in layer:
            raise ValueError(f"layer[{i}] must be an object with a 'name'")
        channels = [
            Channel(str(c["name"]),
                    [Keyframe(float(t), float(v)) for t, v in c["keys"]])
            for c in layer.get("channels", [])
        ]
        weight = [[float(t), float(w)] for t, w in layer.get("weight", [[0.0, 1.0]])]
        out.append(Layer(str(layer["name"]), channels, weight,
                         int(layer.get("priority", 0))))
    return out
