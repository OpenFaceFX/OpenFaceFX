"""glTF 2.0 morph-target exporter (openfacefx.export_gltf, issue #49).

The Khronos glTF Validator is the external gate (it can't run here); the in-repo
proof is a full accessor **round-trip** with pure stdlib `json`/`base64`/`struct`.
These tests decode the sampler `input`/`output` accessors (LE float32) for BOTH
`.gltf` and `.glb` and check: every weight channel reconstructs within `1e-6` of
the source; `accessor.min`/`max`/`count`/`byteLength`, bufferView 4-byte
alignment and `componentType 5126` are correct; sampler times strictly
increasing; `mesh.extras.targetNames` equals the exported weight channels in
order; pose channels are excluded by default and `--gltf-head-node` adds exactly
one `rotation` channel; and the bytes are deterministic.
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
from openfacefx.export_gltf import build_gltf, write_gltf
from openfacefx.gestures import GestureParams
from openfacefx.inspect import POSE_CHANNELS
from openfacefx.io_export import from_dict, to_dict
from openfacefx.pipeline import generate_from_alignment, naive_segments

TEXT, DUR, FPS = "hello brave new world", 2.3, 60.0
_NCOMP = {"SCALAR": 1, "VEC3": 3, "VEC4": 4}


def _source():
    # gestures give pose channels (headYaw etc.) that must be excluded from morphs
    t = generate_from_alignment(naive_segments(TEXT, DUR), fps=FPS,
                                gestures=GestureParams(seed=1))
    return from_dict(to_dict(t))


def _decode(path):
    """Return ``(gltf_dict, bin_bytes)`` for a .gltf (base64 data URI) or .glb."""
    if path.endswith(".glb"):
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
    gltf = json.load(open(path))
    uri = gltf["buffers"][0]["uri"]
    return gltf, base64.b64decode(uri.split(",", 1)[1])


def _accessor(gltf, blob, index):
    a = gltf["accessors"][index]
    bv = gltf["bufferViews"][a["bufferView"]]
    assert a["componentType"] == 5126                     # FLOAT
    assert bv["byteOffset"] % 4 == 0                      # 4-byte aligned
    ncomp = _NCOMP[a["type"]]
    assert bv["byteLength"] == a["count"] * ncomp * 4     # consistent byteLength
    arr = np.frombuffer(blob[bv["byteOffset"]:bv["byteOffset"] + bv["byteLength"]],
                        dtype="<f4").reshape(a["count"], ncomp)
    assert np.allclose(a["min"], arr.min(axis=0))         # correct min/max
    assert np.allclose(a["max"], arr.max(axis=0))
    return arr


# --------------------------------------------------------------------------- #
# accessor round-trip (the in-repo proof), both .gltf and .glb                #
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("ext", [".gltf", ".glb"])
def test_accessor_round_trip_reconstructs_source(tmp_path, ext):
    src = _source()
    path = str(tmp_path / ("f" + ext))
    write_gltf(src, path)
    gltf, blob = _decode(path)
    names = gltf["meshes"][0]["extras"]["targetNames"]
    samp = gltf["animations"][0]["samplers"][0]
    times = _accessor(gltf, blob, samp["input"])[:, 0]
    weights = _accessor(gltf, blob, samp["output"])[:, 0]
    assert np.all(np.diff(times) > 0)                     # strictly increasing
    nt, n = len(times), len(names)
    assert weights.size == nt * n                         # frame-major
    W = weights.reshape(nt, n)
    grid = np.array([i / FPS for i in range(nt)])         # the exporter's grid
    assert np.allclose(times, grid.astype(np.float32))    # times are that grid
    cmap = {c.name: c for c in src.channels}
    for j, name in enumerate(names):
        want = np.clip(sample(cmap[name], grid), 0.0, 1.0)
        assert float(np.max(np.abs(want - W[:, j]))) < 1e-6


def test_glb_chunk_layout_exact():
    src = _source()
    import tempfile
    path = os.path.join(tempfile.mkdtemp(), "f.glb")
    write_gltf(src, path)
    raw = open(path, "rb").read()
    magic, ver, total = struct.unpack("<III", raw[:12])
    jlen = struct.unpack("<II", raw[12:20])[0]
    blen = struct.unpack("<II", raw[20 + jlen:28 + jlen])[0]
    assert total == 12 + (8 + jlen) + (8 + blen)          # header total is exact
    json_chunk = raw[20:20 + jlen]
    assert json_chunk[-1:] in (b" ", b"}")                # JSON padded with SPACES


# --------------------------------------------------------------------------- #
# targetNames, pose exclusion, head node                                       #
# --------------------------------------------------------------------------- #

def test_target_names_are_weight_channels_in_order():
    src = _source()
    gltf, _ = build_gltf(src)
    weight_names = [c.name for c in src.channels if c.name not in POSE_CHANNELS]
    assert gltf["meshes"][0]["extras"]["targetNames"] == weight_names
    # and the node/mesh weights arrays match the target count
    n = len(weight_names)
    assert len(gltf["nodes"][0]["weights"]) == n
    assert len(gltf["meshes"][0]["primitives"][0]["targets"]) == n


def test_pose_channels_absent_by_default():
    src = _source()
    gltf, _ = build_gltf(src)
    names = gltf["meshes"][0]["extras"]["targetNames"]
    assert not any(nm in POSE_CHANNELS for nm in names)
    paths = [c["target"]["path"] for c in gltf["animations"][0]["channels"]]
    assert paths == ["weights"]                            # no rotation channel


def test_head_node_adds_one_rotation_channel_no_morph_change():
    src = _source()
    g0, _ = build_gltf(src)
    g1, _ = build_gltf(src, head_node=True)
    p1 = [c["target"]["path"] for c in g1["animations"][0]["channels"]]
    assert p1.count("rotation") == 1 and p1.count("weights") == 1
    # the morph sampler, target names and node weights are untouched
    assert g1["animations"][0]["samplers"][0] == g0["animations"][0]["samplers"][0]
    assert g1["meshes"][0]["extras"]["targetNames"] == \
           g0["meshes"][0]["extras"]["targetNames"]
    rot = g1["animations"][0]["samplers"][1]["output"]
    assert g1["accessors"][rot]["type"] == "VEC4"          # quaternions
    assert g1["accessors"][rot]["count"] == g1["accessors"][
        g1["animations"][0]["samplers"][0]["input"]]["count"]


def test_head_node_rotation_quaternions_are_unit(tmp_path):
    src = _source()
    path = str(tmp_path / "h.glb")
    write_gltf(src, path, head_node=True)
    gltf, blob = _decode(path)
    rot = _accessor(gltf, blob, gltf["animations"][0]["samplers"][1]["output"])
    assert np.allclose(np.linalg.norm(rot, axis=1), 1.0, atol=1e-5)


# --------------------------------------------------------------------------- #
# determinism, additive, CLI                                                   #
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("ext", [".gltf", ".glb"])
def test_deterministic_bytes(tmp_path, ext):
    src = _source()
    a, b = str(tmp_path / ("a" + ext)), str(tmp_path / ("b" + ext))
    write_gltf(src, a)
    write_gltf(src, b)
    assert open(a, "rb").read() == open(b, "rb").read()


def test_additive_viseme_only_track_has_all_channels_as_targets(tmp_path):
    # a pure viseme track (no pose) puts every channel in targetNames
    src = from_dict(to_dict(generate_from_alignment(naive_segments(TEXT, DUR),
                                                    fps=FPS)))
    gltf, _ = build_gltf(src)
    assert gltf["meshes"][0]["extras"]["targetNames"] == \
           [c.name for c in src.channels]


@pytest.mark.parametrize("ext", [".gltf", ".glb"])
def test_cli_generate_and_convert_write_gltf(tmp_path, ext):
    tj = str(tmp_path / "t.json")
    cli_main(["naive", "--text", TEXT, "--duration", str(DUR), "--gestures",
              "-o", tj])
    out = str(tmp_path / ("gen" + ext))
    assert cli_main(["naive", "--text", TEXT, "--duration", str(DUR),
                     "--gestures", "-o", out]) == 0
    assert os.path.getsize(out) > 0
    conv = str(tmp_path / ("conv" + ext))
    assert cli_main(["convert", tj, "-o", conv]) == 0
    gltf, _ = _decode(conv)
    assert gltf["asset"]["version"] == "2.0"


def test_cli_gltf_head_node_flag(tmp_path):
    out = str(tmp_path / "h.glb")
    assert cli_main(["naive", "--text", TEXT, "--duration", str(DUR),
                     "--gestures", "--gltf-head-node", "-o", out]) == 0
    gltf, _ = _decode(out)
    paths = [c["target"]["path"] for c in gltf["animations"][0]["channels"]]
    assert "rotation" in paths
