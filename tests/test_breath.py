"""Opt-in procedural breathing channel (gestures, #69).

The gesture layer ships blink/brow/head/eye but no breath; this adds an idle
chest rise/fall as a [0,1] 'breath' channel (drives e.g. Live2D ParamBreath),
built like the proven _ambient() head-sway generator on its own rng sub-stream so
enabling it leaves the other gesture channels byte-identical. Tests: it appears
only when enabled, oscillates in [0,1] at ~the breath rate, is deterministic in
the seed, is in GESTURE_CHANNELS, flows through the pipeline (retarget/glTF), and
the CLI --breath flag toggles it.
"""

import json
import os
import sys

import numpy as np
import pytest

try:
    import openfacefx  # noqa: F401
except ImportError:
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from openfacefx.cli import main as cli_main
from openfacefx.edits import sample
from openfacefx.export_gltf import build_gltf
from openfacefx.gestures import (GESTURE_CHANNELS, GestureParams,
                                 add_gestures_to_track, generate_gestures)
from openfacefx.io_export import from_dict
from openfacefx.pipeline import generate_from_alignment, naive_segments


def _breath(dur=8.0, fps=60.0, **kw):
    p = GestureParams(seed=kw.pop("seed", 0), breath_enable=True, **kw)
    chans = generate_gestures(dur, fps, params=p)
    return next((c for c in chans if c.name == "breath"), None)


def test_breath_present_only_when_enabled():
    on = generate_gestures(4.0, 60.0, params=GestureParams(breath_enable=True))
    off = generate_gestures(4.0, 60.0, params=GestureParams())        # default
    assert any(c.name == "breath" for c in on)
    assert not any(c.name == "breath" for c in off)                  # off by default


def test_breath_in_gesture_channels():
    assert "breath" in GESTURE_CHANNELS


def test_breath_oscillates_in_unit_range():
    c = _breath(8.0)
    grid = np.arange(0.0, 8.0, 1 / 60.0)
    v = sample(c, grid)
    assert v.min() < 0.15 and v.max() > 0.85            # spans a full inhale/exhale
    assert np.all(v >= -1e-9) and np.all(v <= 1.0 + 1e-9)
    crossings = int(np.sum(np.diff(np.sign(v - 0.5)) != 0))
    assert crossings >= 2                                # ~2 cycles in 8s at 0.25Hz


def test_deterministic_and_seed_varies():
    a = [(k.time, k.value) for k in _breath(6.0, seed=1).keys]
    b = [(k.time, k.value) for k in _breath(6.0, seed=1).keys]
    c = [(k.time, k.value) for k in _breath(6.0, seed=2).keys]
    assert a == b                                        # deterministic in seed
    assert a != c                                        # a different seed differs


def test_amp_scales_range():
    c = _breath(8.0, breath_amp=0.5)
    v = sample(c, np.arange(0.0, 8.0, 1 / 60.0))
    assert v.max() <= 0.5 + 1e-6                         # capped at breath_amp


def test_flows_through_pipeline_to_gltf():
    src = generate_from_alignment(naive_segments("hello world", 2.0), fps=60.0)
    src = add_gestures_to_track(src, src.duration,
                                params=GestureParams(breath_enable=True))
    assert any(c.name == "breath" for c in src.channels)
    gltf, _ = build_gltf(src)                            # exported as a morph target
    assert "breath" in gltf["meshes"][0]["extras"]["targetNames"]


def test_cli_breath_toggles(tmp_path):
    on = str(tmp_path / "on.json")
    assert cli_main(["naive", "--text", "hello world", "--duration", "4.0",
                     "--gestures", "--breath", "-o", on]) == 0
    assert any(c.name == "breath" for c in from_dict(json.load(open(on))).channels)
    off = str(tmp_path / "off.json")
    assert cli_main(["naive", "--text", "hello world", "--duration", "4.0",
                     "--gestures", "-o", off]) == 0
    assert not any(c.name == "breath"
                   for c in from_dict(json.load(open(off))).channels)
