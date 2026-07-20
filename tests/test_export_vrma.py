"""VRM Animation (.vrma) expression-clip exporter (openfacefx.export_vrma, #62).

A .vrma is a glTF asset carrying VRMC_vrm_animation: each expression is a node
whose translation X component is the [0,1] weight over time. The VRM validators
are the external gate (they can't run here); the in-repo proof mirrors test_gltf's
accessor round-trip -- decode the sampler input/output accessors (LE float32) for
both the .gltf and .vrma (GLB) forms and check every expression weight
reconstructs within 1e-6 of retarget(src, PRESETS['vrm']), plus the structural
invariants the spec requires: specVersion=='1.0', expressions.preset keys are VRM
presets whose .node indexes a real node carrying a 'translation' channel, the
weight rides only in X, --vrma-head-node adds exactly one humanoid head rotation
channel, and the bytes are deterministic.
"""

import base64
import json
import os
import struct
import sys

import numpy as np
import pytest

try:
    import openfacefx  # noqa: F401  (installed wheel wins; see test_core)
except ImportError:
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from openfacefx.cli import main as cli_main
from openfacefx.edits import sample
from openfacefx.export_vrma import (build_vrma, write_vrma,
                                    VRM_EXPRESSION_PRESETS)
from openfacefx.gestures import GestureParams
from openfacefx.io_export import from_dict, to_dict
from openfacefx.pipeline import generate_from_alignment, naive_segments
from openfacefx.retarget import PRESETS, retarget

TEXT, DUR, FPS = "hello brave new world", 2.3, 60.0
_NCOMP = {"SCALAR": 1, "VEC3": 3, "VEC4": 4}


def _source():
    # gestures add pose channels (headYaw etc.) exercised by --vrma-head-node
    t = generate_from_alignment(naive_segments(TEXT, DUR), fps=FPS,
                                gestures=GestureParams(seed=1))
    return from_dict(to_dict(t))


def _decode(path):
    """Return ``(gltf_dict, bin_bytes)`` for a .gltf (base64 data URI) or GLB."""
    if path.endswith(".gltf"):
        gltf = json.load(open(path))
        uri = gltf["buffers"][0]["uri"]
        return gltf, base64.b64decode(uri.split(",", 1)[1])
    raw = open(path, "rb").read()
    magic, ver, total = struct.unpack("<III", raw[:12])
    assert magic == 0x46546C67 and ver == 2 and total == len(raw)
    assert len(raw) % 4 == 0
    off = 12
    jlen, jtype = struct.unpack("<II", raw[off:off + 8])
    off += 8
    assert jtype == 0x4E4F534A and jlen % 4 == 0
    gltf = json.loads(raw[off:off + jlen])
    off += jlen
    blen, btype = struct.unpack("<II", raw[off:off + 8])
    off += 8
    assert btype == 0x004E4942 and blen % 4 == 0
    return gltf, raw[off:off + blen]


def _accessor(gltf, blob, index):
    a = gltf["accessors"][index]
    bv = gltf["bufferViews"][a["bufferView"]]
    assert a["componentType"] == 5126                     # FLOAT
    assert bv["byteOffset"] % 4 == 0                      # 4-byte aligned
    ncomp = _NCOMP[a["type"]]
    assert bv["byteLength"] == a["count"] * ncomp * 4
    arr = np.frombuffer(blob[bv["byteOffset"]:bv["byteOffset"] + bv["byteLength"]],
                        dtype="<f4").reshape(a["count"], ncomp)
    assert np.allclose(a["min"], arr.min(axis=0))
    assert np.allclose(a["max"], arr.max(axis=0))
    return arr


def _expr_nodes(gltf):
    """Map expression name -> (node index, translation sampler index)."""
    anim = gltf["animations"][0]
    node_sampler = {c["target"]["node"]: c["sampler"]
                    for c in anim["channels"]
                    if c["target"]["path"] == "translation"}
    preset = gltf["extensions"]["VRMC_vrm_animation"]["expressions"]["preset"]
    return {name: (spec["node"], node_sampler[spec["node"]])
            for name, spec in preset.items()}


# --------------------------------------------------------------------------- #
# accessor round-trip (the in-repo proof), both .gltf and .vrma                #
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("ext", [".gltf", ".vrma"])
def test_expression_weight_round_trip(tmp_path, ext):
    src = _source()
    path = str(tmp_path / ("clip" + ext))
    write_vrma(src, path)
    gltf, blob = _decode(path)
    anim = gltf["animations"][0]

    nodes = _expr_nodes(gltf)
    assert nodes                                          # at least one expression
    # shared, strictly-increasing time grid
    times = _accessor(gltf, blob, anim["samplers"][0]["input"])[:, 0]
    assert np.all(np.diff(times) > 0)
    grid = np.array([i / FPS for i in range(len(times))])
    assert np.allclose(times, grid.astype(np.float32))

    expr = retarget(src, PRESETS["vrm"])
    cmap = {c.name: c for c in expr.channels}
    for name, (node, samp) in nodes.items():
        out = _accessor(gltf, blob, anim["samplers"][samp]["output"])
        assert out.shape == (len(times), 3)               # VEC3 per frame
        assert np.allclose(out[:, 1], 0.0) and np.allclose(out[:, 2], 0.0)  # X only
        want = np.clip(sample(cmap[name], grid), 0.0, 1.0)
        assert float(np.max(np.abs(want - out[:, 0]))) < 1e-6


# --------------------------------------------------------------------------- #
# structural validity against the VRMC_vrm_animation spec                       #
# --------------------------------------------------------------------------- #

def test_extension_and_preset_structure():
    src = _source()
    gltf, _ = build_vrma(src)
    assert gltf["extensionsUsed"] == ["VRMC_vrm_animation"]
    ext = gltf["extensions"]["VRMC_vrm_animation"]
    assert ext["specVersion"] == "1.0"
    preset = ext["expressions"]["preset"]
    assert preset                                          # non-empty
    for name, spec in preset.items():
        assert name in VRM_EXPRESSION_PRESETS             # a real VRM preset id
        node = spec["node"]
        assert 0 <= node < len(gltf["nodes"])             # indexes a real node
        assert gltf["nodes"][node]["name"] == name        # node is named for it
        assert "translation" in gltf["nodes"][node]
    # every preset node is driven by exactly one translation channel
    tpaths = [c for c in gltf["animations"][0]["channels"]
              if c["target"]["path"] == "translation"]
    assert sorted(c["target"]["node"] for c in tpaths) == \
           sorted(s["node"] for s in preset.values())


def test_lipsync_track_maps_to_vowel_presets():
    # a plain viseme track -> only the VRM vowel expressions, no consonants/pose
    src = from_dict(to_dict(generate_from_alignment(naive_segments(TEXT, DUR),
                                                    fps=FPS)))
    gltf, _ = build_vrma(src)
    preset = gltf["extensions"]["VRMC_vrm_animation"]["expressions"]["preset"]
    assert set(preset).issubset({"aa", "ih", "ou", "ee", "oh"})
    assert preset                                          # and it produced some


def test_no_expression_channels_raises():
    # a pose-only track has nothing the VRM preset maps -> a clear error
    src = _source()
    posey = from_dict(to_dict(src))
    posey.channels = [c for c in posey.channels if c.name.startswith("head")]
    with pytest.raises(ValueError, match="no VRM expression channels"):
        build_vrma(posey)


# --------------------------------------------------------------------------- #
# head node (humanoid) opt-in                                                   #
# --------------------------------------------------------------------------- #

def test_head_node_adds_humanoid_rotation(tmp_path):
    src = _source()
    g0, _ = build_vrma(src)
    g1, _ = build_vrma(src, head_node=True)
    # expressions untouched
    assert g1["extensions"]["VRMC_vrm_animation"]["expressions"] == \
           g0["extensions"]["VRMC_vrm_animation"]["expressions"]
    # exactly one head bone + one rotation channel pointing at it
    head = g1["extensions"]["VRMC_vrm_animation"]["humanoid"]["humanBones"]["head"]
    rots = [c for c in g1["animations"][0]["channels"]
            if c["target"]["path"] == "rotation"]
    assert len(rots) == 1 and rots[0]["target"]["node"] == head["node"]

    path = str(tmp_path / "h.vrma")
    write_vrma(src, path, head_node=True)
    gltf, blob = _decode(path)
    samp = gltf["animations"][0]["channels"]
    rot_samp = next(c["sampler"] for c in samp if c["target"]["path"] == "rotation")
    rot = _accessor(gltf, blob, gltf["animations"][0]["samplers"][rot_samp]["output"])
    assert rot.shape[1] == 4                               # VEC4 quaternions
    assert np.allclose(np.linalg.norm(rot, axis=1), 1.0, atol=1e-5)  # unit


# --------------------------------------------------------------------------- #
# determinism + CLI                                                            #
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("ext", [".gltf", ".vrma"])
def test_deterministic_bytes(tmp_path, ext):
    src = _source()
    a, b = str(tmp_path / ("a" + ext)), str(tmp_path / ("b" + ext))
    write_vrma(src, a)
    write_vrma(src, b)
    assert open(a, "rb").read() == open(b, "rb").read()


def test_cli_generate_and_convert_write_vrma(tmp_path):
    tj = str(tmp_path / "t.json")
    assert cli_main(["naive", "--text", TEXT, "--duration", str(DUR),
                     "--gestures", "-o", tj]) == 0
    out = str(tmp_path / "gen.vrma")
    assert cli_main(["naive", "--text", TEXT, "--duration", str(DUR),
                     "--gestures", "-o", out]) == 0
    assert os.path.getsize(out) > 0
    gltf, _ = _decode(out)
    assert gltf["extensions"]["VRMC_vrm_animation"]["specVersion"] == "1.0"
    conv = str(tmp_path / "conv.vrma")
    assert cli_main(["convert", tj, "-o", conv]) == 0
    gltf2, _ = _decode(conv)
    assert "VRMC_vrm_animation" in gltf2["extensionsUsed"]


def test_cli_head_node_flag(tmp_path):
    out = str(tmp_path / "h.vrma")
    assert cli_main(["naive", "--text", TEXT, "--duration", str(DUR),
                     "--gestures", "--vrma-head-node", "-o", out]) == 0
    gltf, _ = _decode(out)
    assert "head" in \
        gltf["extensions"]["VRMC_vrm_animation"]["humanoid"]["humanBones"]


def test_cli_rejects_retarget_flag(tmp_path):
    # --retarget is rejected: .vrma maps to VRM vowels internally
    with pytest.raises(SystemExit, match="retarget"):
        cli_main(["naive", "--text", TEXT, "--duration", str(DUR),
                  "--retarget", "vrm", "-o", str(tmp_path / "x.vrma")])
