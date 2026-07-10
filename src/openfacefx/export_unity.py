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

from typing import Dict, Optional

from .curves import FaceTrack
from .visemes import VISEMES

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
  m_Events: []
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
) -> None:
    """Write ``track`` as a Unity AnimationClip.

    ``mesh_path`` is the transform path from the Animator's GameObject to the
    SkinnedMeshRenderer's (empty string if they are the same object).
    ``include_all_visemes`` also emits a constant-0 curve for every viseme the
    track never fires, so stale weights from a previous clip are cleared.
    ``names`` overrides the preset with an explicit viseme -> blendshape map.
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
    body = _HEADER.format(
        name=clip_name or "OpenFaceFX_Lipsync",
        float_curves=float_curves,
        editor_curves=float_curves,   # duplicated so the clip is editable
        sample_rate=sample_rate,
        stop_time=_num(track.duration),
        loop=1 if loop else 0,
    )
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(body)
