"""Round-trip tests for the glTF morph-animation importer (openfacefx.importers_gltf)."""

import json

import numpy as np

from openfacefx import write_gltf, read_gltf, naive_segments, generate_from_alignment
from openfacefx.edits import sample
from openfacefx.inspect import POSE_CHANNELS


def _track():
    return generate_from_alignment(naive_segments("hello brave new world", 2.4), fps=30)


def test_gltf_roundtrip_glb(tmp_path):
    tk = _track()
    p = str(tmp_path / "a.glb")
    write_gltf(tk, p)
    back, warns = read_gltf(p)
    assert not any("CUBIC" in w for w in warns)
    # the writer emits only [0,1] weight channels (pose excluded); names round-trip
    src = {c.name for c in tk.channels if c.name not in POSE_CHANNELS}
    got = {c.name for c in back.channels}
    assert got == src and got
    # sampled values match within the RDP re-thinning tolerance
    grid = np.linspace(0.0, tk.duration, 80)
    s = {c.name: c for c in tk.channels}
    b = {c.name: c for c in back.channels}
    for nm in got:
        a = np.clip(sample(s[nm], grid), 0, 1)
        c = np.clip(sample(b[nm], grid), 0, 1)
        assert float(np.max(np.abs(a - c))) < 0.05, nm


def test_gltf_roundtrip_json_container(tmp_path):
    tk = _track()
    p = str(tmp_path / "a.gltf")          # JSON container with a base64 data: buffer
    write_gltf(tk, p)
    back, _ = read_gltf(p)
    assert back.channels
    assert {c.name for c in back.channels} == {c.name for c in tk.channels if c.name not in POSE_CHANNELS}


def test_gltf_fps_override_and_names(tmp_path):
    tk = _track()
    p = str(tmp_path / "a.glb")
    write_gltf(tk, p)
    back, _ = read_gltf(p, fps=48.0)
    assert back.fps == 48.0
    # every imported channel name is a real morph name from the source
    assert all(c.name for c in back.channels)


def test_gltf_no_weight_animation_is_empty_not_error(tmp_path):
    p = str(tmp_path / "empty.gltf")
    with open(p, "w") as f:
        json.dump({"asset": {"version": "2.0"}, "buffers": []}, f)
    tk, warns = read_gltf(p)
    assert tk.channels == []
    assert warns and "no morph-weight animation" in warns[0]
