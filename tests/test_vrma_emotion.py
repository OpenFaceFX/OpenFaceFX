"""VRM emotion-expression preset (export_vrma.VRM_EMOTION_MAP, #67).

emotion.py produces FACS-style AU channels (smile/frown/brow_*) and export_vrma
has happy/angry/sad/relaxed/surprised expression slots, but nothing connected them
(PRESETS["vrm"] maps only the five vowels, so retargeting dropped emotion). This
overlays an emotion-AU -> VRM-emotion map inside the .vrma exporter. Proof: an
emotion-baked track's AUs land in the .vrma emotion slots with the right weights,
a viseme-only track is untouched (no emotion nodes), and the shared vrm preset is
NOT mutated (no side effect on other exporters).
"""

import os
import sys

import numpy as np
import pytest

try:
    import openfacefx  # noqa: F401
except ImportError:
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from openfacefx.cli import main as cli_main
from openfacefx.curves import Channel, FaceTrack, Keyframe
from openfacefx.edits import sample
from openfacefx.export_gltf import _grid
from openfacefx.export_vrma import VRM_EMOTION_MAP, build_vrma
from openfacefx.io_export import write_json
from openfacefx.pipeline import generate_from_alignment, naive_segments
from openfacefx.retarget import PRESETS


def _emotion_src():
    return FaceTrack(fps=60.0, channels=[
        Channel("aa", [Keyframe(0.0, 0.0), Keyframe(0.5, 1.0), Keyframe(1.0, 0.0)]),
        Channel("smile", [Keyframe(0.0, 0.0), Keyframe(0.5, 0.8), Keyframe(1.0, 0.1)]),
        Channel("frown", [Keyframe(0.0, 0.3), Keyframe(1.0, 0.3)]),
        Channel("brow_lower", [Keyframe(0.0, 0.0), Keyframe(1.0, 0.6)]),
        Channel("brow_raise", [Keyframe(0.0, 0.5), Keyframe(1.0, 0.0)]),
        Channel("cheek_raise", [Keyframe(0.0, 0.4), Keyframe(1.0, 0.4)]),
    ])


def _preset(gltf):
    return gltf["extensions"]["VRMC_vrm_animation"]["expressions"]["preset"]


def _node_x(gltf, blob, name):
    """The translation.X (weight) column for expression ``name``."""
    node = _preset(gltf)[name]["node"]
    anim = gltf["animations"][0]
    samp = next(c["sampler"] for c in anim["channels"]
                if c["target"]["node"] == node
                and c["target"]["path"] == "translation")
    a = gltf["accessors"][anim["samplers"][samp]["output"]]
    bv = gltf["bufferViews"][a["bufferView"]]
    arr = np.frombuffer(blob[bv["byteOffset"]:bv["byteOffset"] + bv["byteLength"]],
                        dtype="<f4").reshape(a["count"], 3)
    return arr[:, 0]


def test_emotion_aus_become_vrm_expressions():
    src = _emotion_src()
    gltf, blob = build_vrma(src)
    preset = _preset(gltf)
    # the four mapped emotions appear, alongside the vowel
    assert {"happy", "sad", "angry", "surprised"} <= set(preset)
    assert "aa" in preset
    # and each emotion node reconstructs its source AU weight (mapped 1:1)
    grid = _grid(src)
    cmap = {c.name: c for c in src.channels}
    for expr, au in [("happy", "smile"), ("sad", "frown"),
                     ("angry", "brow_lower"), ("surprised", "brow_raise")]:
        want = np.clip(sample(cmap[au], grid), 0.0, 1.0)
        assert float(np.max(np.abs(_node_x(gltf, blob, expr) - want))) < 1e-6


def test_relaxed_and_cheek_raise_unmapped():
    # cheek_raise has no VRM target, and relaxed is intentionally never populated
    src = FaceTrack(fps=60.0, channels=[
        Channel("cheek_raise", [Keyframe(0.0, 0.5), Keyframe(1.0, 0.5)]),
        Channel("aa", [Keyframe(0.0, 0.0), Keyframe(1.0, 1.0)]),
    ])
    preset = _preset(build_vrma(src)[0])
    assert "relaxed" not in preset
    assert set(preset) == {"aa"}                       # cheek_raise dropped


def test_viseme_only_track_has_no_emotion_expressions():
    src = generate_from_alignment(naive_segments("hello brave new world", 2.3),
                                  fps=60.0)
    preset = _preset(build_vrma(src)[0])
    assert set(preset) <= {"aa", "ih", "ou", "ee", "oh"}   # vowels only
    assert not ({"happy", "sad", "angry", "surprised", "relaxed"} & set(preset))


def test_shared_vrm_preset_not_mutated():
    # the emotion overlay lives in the exporter, not in PRESETS["vrm"]
    assert "smile" not in PRESETS["vrm"]
    assert set(VRM_EMOTION_MAP) == {"smile", "frown", "brow_lower", "brow_raise"}


def test_cli_convert_emotion_track_to_vrma(tmp_path):
    tj = str(tmp_path / "emo.json")
    write_json(_emotion_src(), tj)
    out = str(tmp_path / "emo.vrma")
    assert cli_main(["convert", tj, "-o", out]) == 0
    raw = open(out, "rb").read()
    import json
    import struct
    jlen = struct.unpack("<I", raw[12:16])[0]
    gltf = json.loads(raw[20:20 + jlen])
    preset = gltf["extensions"]["VRMC_vrm_animation"]["expressions"]["preset"]
    assert {"happy", "sad", "angry", "surprised"} <= set(preset)
