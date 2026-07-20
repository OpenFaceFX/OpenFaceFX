"""Esoteric Spine slot-attachment lip-sync exporter -- the 2D game-animation seam.

Spine (Esoteric Software) is the de-facto 2D skeletal-animation runtime for games
(Unity / Unreal / Godot / web / C++). Its lip-sync idiom is a **slot attachment
timeline**: a mouth ``slot`` swaps which image ``attachment`` it shows over time --
exactly the *stepped, one-shape-per-interval* representation
:mod:`openfacefx.export_cues` already reduces a smooth :class:`FaceTrack` to for
Rhubarb / Moho. Rhubarb ships a dedicated "Rhubarb Lip Sync for Spine" bridge that
does just this, which is the demand signal; this is its native, offline equivalent.

A Spine attachment timeline is::

    animations.<anim>.slots.<slot>.attachment = [{"time": <seconds>, "name": <att>}, ...]

with times in **seconds** (float) -- our native time base, so no fps quantisation
(cleaner than Moho ``.dat``'s frame integers). We flatten the track to
``(start, shape)`` cues via ``export_cues`` (dominant channel per frame, coerced to
Rhubarb A-H/X shapes), then map each shape to an attachment name through a small
default table (``mouth_a`` .. ``mouth_x``, overridable).

Two modes, matching Rhubarb's proven workflow:

  * **Splice** (``write_spine(track, out, base=<existing.json>)``) -- load the
    artist's Spine JSON, insert one ``animations[anim].slots[slot].attachment``
    list, leave bones / skins / other slots / other animations untouched.
  * **Standalone** (no ``base``) -- emit a minimal skeleton: one ``root`` bone, one
    mouth ``slot``, a ``default`` skin listing the referenced attachments (region
    stubs; bring your own art), and the animation.

Spec: http://esotericsoftware.com/spine-json-format

**Verification.** The Spine editor is the external gate and can't run here; the
in-repo proof is a true **round-trip** (:func:`read_spine_cues` recovers the cue
list) plus a splice-preservation check (every non-mouth section is byte-for-byte
untouched). Pure stdlib ``json``, deterministic on py3.9/3.13, additive / opt-in.
"""

from __future__ import annotations

import copy
import json
from typing import Dict, List, Optional, Set, Tuple

from .curves import FaceTrack
from .export_cues import _rhubarb_cues

# Rhubarb's nine mouth shapes -> Spine attachment names. Same spirit as the
# Preston-Blair drawing-name table in ``retarget.py``: a plain, overridable map.
DEFAULT_ATTACHMENT_MAP: Dict[str, str] = {s: "mouth_" + s.lower() for s in "ABCDEFGHX"}

_TIME_DP = 4                     # seconds rounded for byte-stable, clean output


def _keyframes(track: FaceTrack, attachment_map: Dict[str, str],
               retarget_preset: Optional[str],
               available_shapes: Optional[Set[str]]) -> List[Dict]:
    """The ``[{"time", "name"}]`` attachment timeline for ``track``.

    One keyframe per dominant-shape run start; consecutive keyframes that resolve
    to the same attachment are merged (a custom map may collapse two shapes)."""
    cues = _rhubarb_cues(track, retarget_preset, available_shapes)
    frames: List[Dict] = []
    prev_name = None
    for start, _end, shape in cues:
        try:
            name = attachment_map[shape]
        except KeyError:
            raise ValueError(
                f"no Spine attachment mapped for mouth shape {shape!r}; "
                f"attachment_map must cover every shape the track uses "
                f"(needs at least {shape!r})") from None
        if name == prev_name:
            continue
        frames.append({"time": round(float(start), _TIME_DP), "name": name})
        prev_name = name
    return frames


def build_spine(track: FaceTrack, *, anim_name: str = "lipsync",
                slot: str = "mouth", bone: str = "root",
                attachment_map: Optional[Dict[str, str]] = None,
                spine_version: str = "4.2.00",
                retarget_preset: Optional[str] = None,
                available_shapes: Optional[Set[str]] = None) -> Dict:
    """Build a standalone minimal Spine skeleton dict driving ``slot``'s
    attachment from ``track``. The skin lists the referenced attachments as region
    stubs -- replace them with real art, or use splice mode against your own rig."""
    amap = dict(DEFAULT_ATTACHMENT_MAP if attachment_map is None else attachment_map)
    if attachment_map is not None and available_shapes is None:
        available_shapes = set(attachment_map)          # collapse to the mapped shapes
    frames = _keyframes(track, amap, retarget_preset, available_shapes)

    used = sorted({f["name"] for f in frames})
    rest = amap.get("X", frames[0]["name"] if frames else "mouth_x")
    if rest not in used:
        used.append(rest)
        used.sort()
    return {
        "skeleton": {"spine": spine_version, "images": "", "audio": ""},
        "bones": [{"name": bone}],
        "slots": [{"name": slot, "bone": bone, "attachment": rest}],
        "skins": [{"name": "default",
                   "attachments": {slot: {name: {} for name in used}}}],
        "animations": {anim_name: {"slots": {slot: {"attachment": frames}}}},
    }


def splice_spine(base: Dict, track: FaceTrack, *, anim_name: str = "lipsync",
                 slot: str = "mouth",
                 attachment_map: Optional[Dict[str, str]] = None,
                 retarget_preset: Optional[str] = None,
                 available_shapes: Optional[Set[str]] = None) -> Dict:
    """Return a copy of the ``base`` Spine JSON with only
    ``animations[anim_name].slots[slot].attachment`` inserted/replaced; every other
    section (bones, skins, other slots, other animations) is left untouched.

    ``slot`` must already be declared in ``base["slots"]`` -- writing a timeline for
    a slot the skeleton lacks is a silent no-op in Spine, so it is a clear error
    here instead."""
    slot_names = {s.get("name") for s in base.get("slots", [])}
    if slot not in slot_names:
        raise ValueError(
            f"slot {slot!r} is not declared in the base skeleton's slots "
            f"{sorted(n for n in slot_names if n)}; add the mouth slot to the "
            f"Spine project first, or pass slot= one that exists")
    amap = dict(DEFAULT_ATTACHMENT_MAP if attachment_map is None else attachment_map)
    if attachment_map is not None and available_shapes is None:
        available_shapes = set(attachment_map)
    frames = _keyframes(track, amap, retarget_preset, available_shapes)

    out = copy.deepcopy(base)
    anims = out.setdefault("animations", {})
    anim = anims.setdefault(anim_name, {})
    slots = anim.setdefault("slots", {})
    slot_tl = slots.setdefault(slot, {})
    slot_tl["attachment"] = frames                      # replace ONLY this timeline
    return out


def write_spine(track: FaceTrack, path: str, *, base: Optional[str] = None,
                anim_name: str = "lipsync", slot: str = "mouth",
                bone: str = "root",
                attachment_map: Optional[Dict[str, str]] = None,
                retarget_preset: Optional[str] = None,
                available_shapes: Optional[Set[str]] = None) -> None:
    """Write ``track`` as a Spine skeleton JSON. With ``base`` (a path to an
    existing Spine ``.json``) it splices the mouth timeline into that project;
    without it, a standalone minimal skeleton."""
    if base is not None:
        with open(base, encoding="utf-8") as fh:
            base_doc = json.load(fh)
        doc = splice_spine(base_doc, track, anim_name=anim_name, slot=slot,
                           attachment_map=attachment_map,
                           retarget_preset=retarget_preset,
                           available_shapes=available_shapes)
    else:
        doc = build_spine(track, anim_name=anim_name, slot=slot, bone=bone,
                          attachment_map=attachment_map,
                          retarget_preset=retarget_preset,
                          available_shapes=available_shapes)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(doc, fh, indent=2)
        fh.write("\n")


def read_spine_cues(path: str, *, anim_name: Optional[str] = None,
                    slot: Optional[str] = None) -> List[Tuple[float, str]]:
    """Recover ``[(time_seconds, attachment_name)]`` from a Spine JSON's slot
    attachment timeline -- the inverse used to prove the writer round-trips. With
    ``anim_name``/``slot`` omitted, the single animation / single attachment-driven
    slot is chosen (ambiguity is a clear error)."""
    with open(path, encoding="utf-8") as fh:
        doc = json.load(fh)
    anims = doc.get("animations", {})
    if anim_name is None:
        if len(anims) != 1:
            raise ValueError(
                f"pass anim_name=: the file has {len(anims)} animations {sorted(anims)}")
        anim_name = next(iter(anims))
    slots = anims.get(anim_name, {}).get("slots", {})
    driven = {s for s, tl in slots.items() if "attachment" in tl}
    if slot is None:
        if len(driven) != 1:
            raise ValueError(
                f"pass slot=: animation {anim_name!r} has {len(driven)} slots with "
                f"an attachment timeline {sorted(driven)}")
        slot = next(iter(driven))
    keyframes = slots.get(slot, {}).get("attachment", [])
    return [(float(k["time"]), k["name"]) for k in keyframes]
