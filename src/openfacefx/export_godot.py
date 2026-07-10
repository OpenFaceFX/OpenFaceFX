"""Export a FaceTrack as a Godot 4 ``Animation`` text resource (``.tres``).

Godot has no first-party lip-sync; the community bakes mouth data into
``AnimationPlayer`` animations (rhubarb-lipsync integrations, godot-baked-
lipsync). This writes that artifact directly: a ``[gd_resource type="Animation"
format=3]`` resource (``format=3`` is Godot 4; ``2`` is Godot 3) with one
**value track** per active viseme, keyed with the existing RDP-reduced
keyframes and linear interpolation.

Each track drives a blend shape by node path, e.g.::

    tracks/0/path = NodePath("Head:blend_shapes/viseme_aa")

so it plays through any ``MeshInstance3D`` whose mesh exposes those shape keys.
Godot blend-shape weights are ``0..1`` (unlike Unity's ``0..100``), so channel
values are written straight through. Shape naming reuses the Unity exporter's
presets (``oculus`` ``viseme_*`` / ``vrchat`` ``vrc.v_*``); pass ``names`` for a
custom ``viseme -> shape`` map. The node name is configurable (default
``Head``).

A ``consumer`` loads the resource into an ``AnimationLibrary`` and adds it to an
``AnimationPlayer`` whose ``root_node`` makes the paths resolve; those runtime
nodes stay engine-side and out of scope here. Value tracks (not the importer-
only ``blend_shape`` track type) keep the resource hand-writable and stdlib-
serialisable. Text output, LF line endings.
"""

from __future__ import annotations

from typing import Dict, Optional

from .curves import FaceTrack
from .export_unity import NAMING_PRESETS
from .visemes import VISEMES


def _num(x: float) -> str:
    """Trimmed number for a ``PackedFloat32Array`` element (Godot prints these
    without a forced decimal: ``0``, ``1``, ``0.1``)."""
    s = f"{x:.6f}".rstrip("0").rstrip(".")
    return s or "0"


def _fnum(x: float) -> str:
    """Float with a forced decimal point, matching Godot's writer for scalar
    properties and generic ``Array`` values (``0.0``, ``1.0``, ``0.5``) so they
    never reparse as ints."""
    s = f"{x:.6f}".rstrip("0")
    return (s + "0") if s.endswith(".") else (s or "0.0")


def _track_block(index: int, path: str, times, values) -> str:
    t = ", ".join(_num(x) for x in times)
    trans = ", ".join("1" for _ in times)
    vals = ", ".join(_fnum(v) for v in values)
    # keys dict entry order matches Godot's own writer: times, transitions,
    # values, update -- with update last and no trailing comma. The packed
    # arrays print trimmed (0, 1, 0.1) while the generic values array forces a
    # decimal (0.0, 1.0) so its entries stay float-typed, not int.
    return (
        f'tracks/{index}/type = "value"\n'
        f"tracks/{index}/imported = false\n"
        f"tracks/{index}/enabled = true\n"
        f'tracks/{index}/path = NodePath("{path}")\n'
        f"tracks/{index}/interp = 1\n"
        f"tracks/{index}/loop_wrap = true\n"
        f"tracks/{index}/keys = {{\n"
        f'"times": PackedFloat32Array({t}),\n'
        f'"transitions": PackedFloat32Array({trans}),\n'
        f'"values": [{vals}],\n'
        f'"update": 0\n'
        f"}}\n"
    )


def write_godot_anim(
    track: FaceTrack,
    path: str,
    *,
    naming: str = "oculus",
    node: str = "Head",
    names: Optional[Dict[str, str]] = None,
    include_all_visemes: bool = True,
) -> None:
    """Write ``track`` as a Godot 4 ``Animation`` ``.tres``.

    ``node`` is the animated node's name relative to the AnimationPlayer's
    ``root_node`` (the blend shapes live at ``<node>:blend_shapes/<shape>``).
    ``naming`` picks a built-in shape-name preset; ``names`` overrides it with
    an explicit ``viseme -> shape`` map. ``include_all_visemes`` also writes a
    constant-0 track for every viseme the track never fires, clearing any weight
    a previous animation left on that shape.

    Track key times are absolute seconds, so the source fps does not appear in
    the resource. The ``[resource]`` header follows Godot's text saver, which
    omits any property left at its class default -- ``loop_mode`` (0),
    ``step`` (1/30) and ``resource_name`` (empty for animations) never appear,
    and ``length`` only when it is not the 1.0 default -- so the output matches
    what the editor would re-save.
    """
    if names is None:
        try:
            names = NAMING_PRESETS[naming]
        except KeyError:
            raise ValueError(
                f"unknown naming preset {naming!r}; use one of "
                f"{sorted(NAMING_PRESETS)} or pass names=") from None

    by_name = {c.name: c for c in track.channels}
    tracks = []
    for viseme in VISEMES:
        ch = by_name.get(viseme)
        if ch is not None and ch.keys:
            times = [k.time for k in ch.keys]
            values = [k.value for k in ch.keys]
        elif include_all_visemes and viseme in names:
            times, values = [0.0], [0.0]
        else:
            continue
        tracks.append((names[viseme], times, values))

    header = '[gd_resource type="Animation" format=3]\n\n[resource]\n'
    if track.duration != 1.0:                       # 1.0 is Animation's default
        header += f"length = {_fnum(track.duration)}\n"
    blocks = [
        _track_block(i, f"{node}:blend_shapes/{shape}", times, values)
        for i, (shape, times, values) in enumerate(tracks)
    ]
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(header + "".join(blocks))
