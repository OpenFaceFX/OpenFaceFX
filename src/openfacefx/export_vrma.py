"""VRM Animation (``.vrma``) expression-clip exporter -- lip-sync for VRM 1.0.

glTF 2.0 is the base of **VRM**, and a ``.vrma`` file is a glTF asset carrying the
``VRMC_vrm_animation`` extension. The generic morph-target animation that
:mod:`openfacefx.export_gltf` writes (``animation.channel.target.path="weights"``)
is **ignored by VRM runtimes** for expression playback -- they read
``VRMC_vrm_animation.expressions``, where each expression is a plain glTF node
whose **translation X component** is the ``[0, 1]`` expression weight over time
(the spec: "the X component of the translation is treated as the animation data
for the weight", clamped to ``[0, 1]``). This writes an **expression-only**,
skeleton-free clip -- the root extension requires only ``specVersion``, so no rig
is needed -- that UniVRM (Unity), ``@pixiv/three-vrm-animation``, the Blender VRM
add-on, VRoid Hub and VMagicMirror will actually lip-sync to.

The five VRM 1.0 vowel presets ``aa/ih/ou/ee/oh`` are exactly what
``retarget(track, PRESETS["vrm"])`` produces, so -- like every other engine
exporter here -- this maps the Oculus-viseme track onto its target rig
**internally** (``--retarget`` is rejected at the CLI; override in the API via
``preset=``). Each mapped expression becomes a node ``{"name": <expr>,
"translation": [0,0,0]}`` + a LINEAR sampler whose VEC3 output carries the sampled
weight in X (Y = Z = 0), wired by a ``target.path="translation"`` channel, and
registered under ``extensions.VRMC_vrm_animation.expressions.preset.<expr>.node``.
``--vrma-head-node`` additionally maps the signed head pose to
``humanoid.humanBones.head`` with a quaternion ``rotation`` sampler.

This reuses :mod:`openfacefx.export_gltf` wholesale -- its float32 accessor packer
(:func:`~openfacefx.export_gltf._add_accessor`), GLB ``struct`` writer
(:func:`~openfacefx.export_gltf._write_glb`), the base64 ``data:`` URI path and the
Euler->quaternion helper -- so the two exporters emit identical accessor blocks.

Spec: https://github.com/vrm-c/vrm-specification/blob/master/specification/VRMC_vrm_animation-1.0/README.md
Format: https://vrm.dev/en/vrma/

**Verification.** The Khronos glTF Validator and the VRM validators are the
documented external gates and cannot run here, so the asset is built strictly to
the spec (so it would pass) and the in-repo proof is a full accessor **round-trip**
(decode the LE float32 buffers, read each expression node's ``translation.X`` per
frame, reconstruct every weight channel within 1e-6). numpy + stdlib only,
deterministic bytes on Python 3.9/3.13; additive and opt-in.
"""

from __future__ import annotations

import base64
import json
from typing import Dict, List, Optional, Tuple

import numpy as np

from .curves import FaceTrack
from .edits import sample
from .export_gltf import _add_accessor, _euler_quaternions, _grid, _write_glb
from .retarget import PRESETS, retarget

# The VRM 1.0 expression preset ids (VRMC_vrm ``expression.preset``), in a fixed
# canonical order so node indices -- and thus the bytes -- are deterministic
# regardless of track channel order. Lip-sync vowels first (our primary output),
# then blink, the emotion presets, look, and neutral. A track channel is emitted
# as an expression node iff its name is one of these; ``custom`` expressions are
# intentionally not synthesised (a preset-only clip is the portable subset).
VRM_EXPRESSION_PRESETS: Tuple[str, ...] = (
    "aa", "ih", "ou", "ee", "oh",
    "blink", "blinkLeft", "blinkRight",
    "happy", "angry", "sad", "relaxed", "surprised",
    "lookUp", "lookDown", "lookLeft", "lookRight",
    "neutral",
)
_PRESET_SET = frozenset(VRM_EXPRESSION_PRESETS)

# Emotion AU channels (:data:`openfacefx.emotion.VA_EMOTION_CHANNELS`) -> VRM 1.0
# emotion expression presets (#67). This connects the emotion layer to the .vrma
# emotion slots that nothing else fills (``PRESETS["vrm"]`` maps only the five
# vowels). Applied *inside* this exporter as an additive overlay on the vowel
# preset, NOT merged into the shared ``vrm`` preset -- so ``--retarget vrm`` output
# for every other exporter is unchanged.
#
# Only the four AUs with a distinct expression map: smile->happy, frown->sad,
# brow_lower (corrugator)->angry, brow_raise->surprised. ``cheek_raise`` and VRM
# ``relaxed`` are intentionally left unmapped -- there is no clean low-arousal
# "relaxed/content" AU (the smile channel *grows* with arousal, so it reads as
# happy->elated, not calm), and cheek_raise shadows an aroused smile, so mapping it
# to ``relaxed`` would fire relaxed hardest when the face is most excited. Better a
# missing slot than a wrong one; author ``relaxed`` by hand if a rig needs it.
VRM_EMOTION_MAP = {
    "smile": (("happy", 1.0),),
    "frown": (("sad", 1.0),),
    "brow_lower": (("angry", 1.0),),
    "brow_raise": (("surprised", 1.0),),
}


def _expression_channels(track: FaceTrack):
    """Channels whose names are VRM expression preset ids, in canonical order."""
    by_name = {c.name: c for c in track.channels}
    return [(name, by_name[name]) for name in VRM_EXPRESSION_PRESETS
            if name in by_name]


def build_vrma(track: FaceTrack, *, head_node: bool = False,
               preset: Optional[str] = "vrm") -> Tuple[Dict, bytes]:
    """Build the ``VRMC_vrm_animation`` glTF JSON dict (buffer without a URI) and
    the packed BIN bytes; :func:`write_vrma` embeds the bytes as a ``data:`` URI
    (``.gltf``) or a GLB BIN chunk (``.vrma``/``.glb``).

    ``preset`` names a :data:`openfacefx.retarget.PRESETS` entry to map the track
    onto the VRM vowel expressions first (default ``"vrm"``); pass ``preset=None``
    to treat ``track`` as already being in VRM expression space (its channels
    already named ``aa``/``ih``/... ). ``head_node`` additionally encodes the
    signed head pose channels as a ``humanoid.humanBones.head`` rotation sampler
    (taken from the *original* track, since retargeting drops pose channels)."""
    grid = _grid(track)
    times = grid.astype(np.float32)

    if preset is not None:
        # vowel preset + the emotion-AU overlay, so one retarget yields both the
        # lip-sync vowels and the emotion expressions (#67).
        mapping = dict(PRESETS[preset])
        mapping.update(VRM_EMOTION_MAP)
        expr_track = retarget(track, mapping)
    else:
        expr_track = track                       # already in VRM expression space
    exprs = _expression_channels(expr_track)
    if not exprs:
        raise ValueError(
            "no VRM expression channels to export: expected channels named "
            f"{'/'.join(VRM_EXPRESSION_PRESETS[:5])}... -- the track maps to none "
            "of the VRM 1.0 expression presets. Retarget with PRESETS['vrm'] "
            "first (this exporter does so by default; pass preset=None only for a "
            "track already in VRM expression space).")

    parts: List[bytes] = []
    bufferviews: List[Dict] = []
    accessors: List[Dict] = []

    def add(arr: np.ndarray, ncomp: int, gltf_type: str) -> int:
        return _add_accessor(parts, bufferviews, accessors, arr, ncomp, gltf_type)

    a_time = add(times, 1, "SCALAR")                       # shared sampler input

    nodes: List[Dict] = []
    scene_nodes: List[int] = []
    samplers: List[Dict] = []
    channels: List[Dict] = []
    preset_map: Dict[str, Dict] = {}

    for name, ch in exprs:
        weight = np.clip(sample(ch, grid), 0.0, 1.0)
        vec3 = np.zeros((len(grid), 3), dtype=np.float32)
        vec3[:, 0] = weight                                # weight rides in X
        a_out = add(vec3, 3, "VEC3")
        node = len(nodes)
        nodes.append({"name": name, "translation": [0.0, 0.0, 0.0]})
        scene_nodes.append(node)
        samplers.append({"input": a_time, "output": a_out,
                         "interpolation": "LINEAR"})
        channels.append({"sampler": len(samplers) - 1,
                         "target": {"node": node, "path": "translation"}})
        preset_map[name] = {"node": node}

    vrm_anim: Dict = {"specVersion": "1.0",
                      "expressions": {"preset": preset_map}}

    gltf: Dict = {
        "asset": {"version": "2.0", "generator": "openfacefx"},
        "extensionsUsed": ["VRMC_vrm_animation"],
        "extensions": {"VRMC_vrm_animation": vrm_anim},
        "scene": 0,
        "scenes": [{"nodes": scene_nodes}],
        "nodes": nodes,
        "animations": [{"samplers": samplers, "channels": channels}],
        "accessors": accessors,
        "bufferViews": bufferviews,
        "buffers": [],
    }

    if head_node:
        a_rot = add(_euler_quaternions(track, grid), 4, "VEC4")
        node = len(nodes)
        nodes.append({"name": "head", "rotation": [0.0, 0.0, 0.0, 1.0]})
        scene_nodes.append(node)
        samplers.append({"input": a_time, "output": a_rot,
                         "interpolation": "LINEAR"})
        channels.append({"sampler": len(samplers) - 1,
                         "target": {"node": node, "path": "rotation"}})
        vrm_anim["humanoid"] = {"humanBones": {"head": {"node": node}}}

    return gltf, b"".join(parts)


def write_vrma(track: FaceTrack, path: str, *, head_node: bool = False,
               preset: Optional[str] = "vrm") -> None:
    """Write ``track`` as a VRM Animation clip. ``.gltf`` picks the JSON form with
    a base64 ``data:`` buffer; ``.vrma`` (and ``.glb``) the GLB binary container --
    ``.vrma`` is simply the GLB with the VRM-specific suffix."""
    gltf, blob = build_vrma(track, head_node=head_node, preset=preset)
    if path.endswith(".gltf"):
        gltf["buffers"] = [{
            "byteLength": len(blob),
            "uri": "data:application/octet-stream;base64," +
                   base64.b64encode(blob).decode("ascii"),
        }]
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(gltf, fh, indent=2)
        return
    _write_glb(gltf, blob, path)
