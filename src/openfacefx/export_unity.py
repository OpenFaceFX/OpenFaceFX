"""Export a FaceTrack as a Unity .anim AnimationClip (text YAML).

The clip animates ``blendShape.<name>`` float curves on a SkinnedMeshRenderer
(classID 137), so it plays through any Animator on any blendshape rig — no
extra packages needed. Two naming presets cover the common conventions:

  * ``oculus``  -- ``viseme_sil .. viseme_U``  (Meta reference rigs,
                   Ready Player Me; mixed case, I/O/U spelling)
  * ``vrchat``  -- ``vrc.v_sil .. vrc.v_ou``   (VRChat auto-detect names;
                   lowercase, ih/oh/ou spelling)

Unity blendshape weights are 0-100 (percent), so track weights (0-1) are
scaled by 100. Keyframe times are absolute seconds; the sparse RDP-reduced
keys map 1:1 onto clip keys. Format derived from Unity's serialized
AnimationClip (serializedVersion 6) as emitted by working generators — see
docs/COMPATIBILITY.md for provenance.
"""

from __future__ import annotations

import json
import re
from typing import Dict, Optional

from .curves import FaceTrack
from .events import resolve
from .visemes import VISEMES

# Function name Unity SendMessage-invokes for each event, when no override is
# given. One handler with a switch on the packed string keeps the runtime
# contract to a single method; override per type via ``event_func_map``.
DEFAULT_EVENT_FUNC = "OnFaceEvent"

# A YAML plain scalar is safe only if it avoids the indicators that would start a
# flow collection / tag / anchor / quote / directive, and carries no ": " or
# " #" or leading/trailing space. Anything else (notably a packed JSON payload)
# is emitted double-quoted.
_YAML_PLAIN_SAFE = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_./+\-]*\Z")

# OpenFaceFX viseme -> blendshape name, per convention. VRChat lowercases the
# SDK enum (whose last three are spelled ih/oh/ou) behind a "vrc.v_" prefix.
_VRCHAT_SUFFIX = {"I": "ih", "O": "oh", "U": "ou"}

NAMING_PRESETS: Dict[str, Dict[str, str]] = {
    "oculus": {v: "viseme_" + v for v in VISEMES},
    "vrchat": {v: "vrc.v_" + _VRCHAT_SUFFIX.get(v, v).lower() for v in VISEMES},
}

_HEADER = """\
%YAML 1.1
%TAG !u! tag:unity3d.com,2011:
--- !u!74 &7400000
AnimationClip:
  m_ObjectHideFlags: 0
  m_CorrespondingSourceObject: {{fileID: 0}}
  m_PrefabInstance: {{fileID: 0}}
  m_PrefabAsset: {{fileID: 0}}
  m_Name: {name}
  serializedVersion: 6
  m_Legacy: 0
  m_Compressed: 0
  m_UseHighQualityCurve: 1
  m_RotationCurves: []
  m_CompressedRotationCurves: []
  m_EulerCurves: []
  m_PositionCurves: []
  m_ScaleCurves: []
  m_FloatCurves:
{float_curves}
  m_PPtrCurves: []
  m_SampleRate: {sample_rate}
  m_WrapMode: 0
  m_Bounds:
    m_Center: {{x: 0, y: 0, z: 0}}
    m_Extent: {{x: 0, y: 0, z: 0}}
  m_ClipBindingConstant:
    genericBindings: []
    pptrCurveMapping: []
  m_AnimationClipSettings:
    serializedVersion: 2
    m_AdditiveReferencePoseClip: {{fileID: 0}}
    m_AdditiveReferencePoseTime: 0
    m_StartTime: 0
    m_StopTime: {stop_time}
    m_OrientationOffsetY: 0
    m_Level: 0
    m_CycleOffset: 0
    m_HasAdditiveReferencePose: 0
    m_LoopTime: {loop}
    m_LoopBlend: 0
    m_LoopBlendOrientation: 0
    m_LoopBlendPositionY: 0
    m_LoopBlendPositionXZ: 0
    m_KeepOriginalOrientation: 0
    m_KeepOriginalPositionY: 1
    m_KeepOriginalPositionXZ: 0
    m_HeightFromFeet: 0
    m_Mirror: 0
  m_EditorCurves:
{editor_curves}
  m_EulerEditorCurves: []
  m_HasGenericRootTransform: 0
  m_HasMotionFloatCurves: 0
  m_Events:{m_events}
"""


def _num(x: float) -> str:
    """Plain decimal without exponent or trailing zeros (Unity style)."""
    s = f"{x:.6f}".rstrip("0").rstrip(".")
    return s or "0"


def _key_block(time: float, value: float) -> str:
    return f"""\
      - serializedVersion: 3
        time: {_num(time)}
        value: {_num(value)}
        inSlope: 0
        outSlope: 0
        tangentMode: 136
        weightedMode: 0
        inWeight: 0.33333334
        outWeight: 0.33333334
"""


def _curve_block(attribute: str, mesh_path: str, keys) -> str:
    key_blocks = "".join(_key_block(t, v) for t, v in keys)
    return f"""\
  - curve:
      serializedVersion: 2
      m_Curve:
{key_blocks}\
      m_PreInfinity: 2
      m_PostInfinity: 2
      m_RotationOrder: 4
    attribute: blendShape.{attribute}
    path: {mesh_path}
    classID: 137
    script: {{fileID: 0}}
"""


def _yaml_str(s: str) -> str:
    """Serialise ``s`` as a Unity-YAML scalar: plain when unambiguous, else a
    double-quoted scalar with backslash escapes (Unity's reader is YAML 1.1, so
    ``\\"`` / ``\\\\`` / ``\\n`` / ``\\t`` are understood). The empty string
    renders as nothing after the key, matching how Unity writes an unset
    ``stringParameter``."""
    if s == "":
        return ""
    if _YAML_PLAIN_SAFE.match(s):
        return " " + s
    esc = (s.replace("\\", "\\\\").replace('"', '\\"')
            .replace("\n", "\\n").replace("\t", "\\t"))
    return ' "' + esc + '"'


def _event_string(name: str, payload: Dict) -> str:
    """Pack an event into Unity's single ``stringParameter`` slot (AnimationEvent
    carries one argument): the event ``name``, and -- when there is a payload --
    a compact, key-sorted JSON blob after a ``|`` separator, e.g.
    ``nod_small|{"intensity":0.6}``. ``sort_keys``/``ensure_ascii`` keep it
    deterministic and ASCII-safe inside the YAML."""
    if not payload:
        return name
    blob = json.dumps(payload, separators=(",", ":"), sort_keys=True,
                      ensure_ascii=True)
    return f"{name}|{blob}"


def _event_block(time: float, func: str, sval: str, message_options: int) -> str:
    """One ``m_Events`` entry, field order matching Unity's own serialised
    output (time, functionName, floatParameter, intParameter, stringParameter,
    objectReferenceParameter, messageOptions). No trailing newline."""
    return (
        f"  - time: {_num(time)}\n"
        f"    functionName: {func}\n"
        f"    floatParameter: 0\n"
        f"    intParameter: 0\n"
        f"    stringParameter:{_yaml_str(sval)}\n"
        f"    objectReferenceParameter: {{fileID: 0}}\n"
        f"    messageOptions: {message_options}"
    )


def _events_yaml(track: FaceTrack, func_map: Optional[Dict[str, str]],
                 message_options: int) -> str:
    """Render the resolved event layer as the value of ``m_Events:``. Empty ->
    `` []`` (byte-identical to an event-free clip); otherwise a newline plus one
    ascending-time block per event. Ranged events (``dur > 0``) become a
    ``<name>_Begin`` at ``t`` and a ``<name>_End`` at ``t + dur``, since a Unity
    AnimationEvent has no native begin/end -- the runtime pairs them by name."""
    func_map = func_map or {}
    blocks = []
    for e in resolve(track):
        func = func_map.get(e.type, DEFAULT_EVENT_FUNC)
        if e.dur > 0.0:
            blocks.append((e.t, _event_block(
                e.t, func, _event_string(e.name + "_Begin", e.payload),
                message_options)))
            blocks.append((e.t + e.dur, _event_block(
                e.t + e.dur, func, _event_string(e.name + "_End", e.payload),
                message_options)))
        else:
            blocks.append((e.t, _event_block(
                e.t, func, _event_string(e.name, e.payload), message_options)))
    if not blocks:
        return " []"
    # The _End of a ranged event can fall after a later event's start, so sort
    # the fully-expanded list to keep Unity's required ascending time.
    blocks.sort(key=lambda b: b[0])
    return "\n" + "\n".join(b for _, b in blocks)


def write_unity_anim(
    track: FaceTrack,
    path: str,
    naming: str = "oculus",
    mesh_path: str = "Body",
    clip_name: Optional[str] = None,
    sample_rate: int = 60,
    loop: bool = False,
    include_all_visemes: bool = True,
    names: Optional[Dict[str, str]] = None,
    events: bool = True,
    event_func_map: Optional[Dict[str, str]] = None,
    event_message_options: int = 1,
) -> None:
    """Write ``track`` as a Unity AnimationClip.

    ``mesh_path`` is the transform path from the Animator's GameObject to the
    SkinnedMeshRenderer's (empty string if they are the same object).
    ``include_all_visemes`` also emits a constant-0 curve for every viseme the
    track never fires, so stale weights from a previous clip are cleared.
    ``names`` overrides the preset with an explicit viseme -> blendshape map.

    The optional event layer (issue #6) fills the clip's ``m_Events`` array: each
    resolved :class:`~openfacefx.events.Event` becomes an ``AnimationEvent`` that
    Unity SendMessage-invokes on the Animator's GameObject. ``event_func_map``
    maps event ``type`` -> handler method name (default ``OnFaceEvent`` for all);
    the event ``name`` and its JSON payload ride in the single ``stringParameter``
    as ``name|{json}``. ``event_message_options`` defaults to ``1``
    (``DontRequireReceiver``) so a clip with no handler wired up does not error at
    runtime. Set ``events=False`` to omit them. A track with no events writes a
    byte-identical ``m_Events: []`` -- exactly as before this feature.
    """
    if names is None:
        try:
            names = NAMING_PRESETS[naming]
        except KeyError:
            raise ValueError(
                f"unknown naming preset {naming!r}; use one of "
                f"{sorted(NAMING_PRESETS)} or pass names=") from None

    by_name = {c.name: c for c in track.channels}
    blocks = []
    for viseme in VISEMES:
        ch = by_name.get(viseme)
        if ch is not None and ch.keys:
            keys = [(k.time, k.value * 100.0) for k in ch.keys]
        elif include_all_visemes and viseme in names:
            keys = [(0.0, 0.0)]
        else:
            continue
        blocks.append(_curve_block(names[viseme], mesh_path, keys))

    float_curves = "".join(blocks).rstrip("\n") if blocks else "  []"
    m_events = _events_yaml(track, event_func_map, event_message_options) \
        if events else " []"
    body = _HEADER.format(
        name=clip_name or "OpenFaceFX_Lipsync",
        float_curves=float_curves,
        editor_curves=float_curves,   # duplicated so the clip is editable
        sample_rate=sample_rate,
        stop_time=_num(track.duration),
        loop=1 if loop else 0,
        m_events=m_events,
    )
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(body)
