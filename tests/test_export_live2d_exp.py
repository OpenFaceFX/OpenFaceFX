"""Live2D Cubism .exp3.json expression-pose exporter (export_live2d, #65).

An exp3.json freezes ONE pose as absolute parameter values, hotkey-bindable in
VTube Studio. The Cubism editor is the external gate; the in-repo proof is
structural conformance to the CubismSpecs schema (Type == "Live2D Expression",
only Type/FadeInTime/FadeOutTime/Parameters keys — additionalProperties is false —
each Parameter only Id/Value/Blend, Blend in the enum, no Version), that Value is
the track's pose sampled at the chosen instant, the peak-activity default, plus
determinism and the CLI path.
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
from openfacefx.export_live2d import (DEFAULT_MOUTH_PARAM, build_expression,
                                      write_live2d_expression)
from openfacefx.retarget import _sampler, retarget
from openfacefx.io_export import from_dict, to_dict
from openfacefx.pipeline import generate_from_alignment, naive_segments

TEXT, DUR, FPS = "hello brave new world", 2.3, 60.0
_BLENDS = {"Add", "Multiply", "Overwrite"}


def _source():
    return from_dict(to_dict(generate_from_alignment(naive_segments(TEXT, DUR),
                                                     fps=FPS)))


# --------------------------------------------------------------------------- #
# schema conformance (additionalProperties: false)                             #
# --------------------------------------------------------------------------- #

def test_exp3_schema_shape():
    doc = build_expression(_source())
    assert set(doc) == {"Type", "FadeInTime", "FadeOutTime", "Parameters"}
    assert doc["Type"] == "Live2D Expression"
    assert "Version" not in doc
    assert isinstance(doc["FadeInTime"], (int, float))
    assert isinstance(doc["FadeOutTime"], (int, float))
    assert doc["Parameters"]                                # non-empty
    for p in doc["Parameters"]:
        assert set(p) == {"Id", "Value", "Blend"}          # no extra keys
        assert p["Blend"] in _BLENDS
        assert 0.0 <= p["Value"] <= 1.0


def test_default_mode_single_mouth_param():
    doc = build_expression(_source())
    ids = [p["Id"] for p in doc["Parameters"]]
    assert ids == [DEFAULT_MOUTH_PARAM]                     # collapsed to one
    assert doc["Parameters"][0]["Blend"] == "Overwrite"     # absolute pose


def test_per_parameter_mode():
    params = {"aa": "ParamA", "E": "ParamE", "I": "ParamI",
              "O": "ParamO", "U": "ParamU"}
    doc = build_expression(_source(), params=params)
    ids = {p["Id"] for p in doc["Parameters"]}
    assert ids <= set(params.values()) and ids                # only mapped Ids


# --------------------------------------------------------------------------- #
# the pose is the track sampled at the chosen instant                           #
# --------------------------------------------------------------------------- #

def test_value_matches_sampled_pose_at_time():
    src = _source()
    at = 1.0
    doc = build_expression(src, at=at)
    # reconstruct the collapsed mouth curve and sample it at `at`
    baked = retarget(src, {v: [(DEFAULT_MOUTH_PARAM, 1.0)]
                           for v in ["PP", "FF", "TH", "DD", "kk", "CH", "SS",
                                     "nn", "RR", "aa", "E", "I", "O", "U"]})
    want = round(float(_sampler(baked.channels[0])(at)), 4)
    assert doc["Parameters"][0]["Value"] == want


def test_default_at_is_peak_activity():
    src = _source()
    peak = build_expression(src)["Parameters"][0]["Value"]
    rest = build_expression(src, at=0.0)["Parameters"][0]["Value"]
    assert peak >= rest                                     # peak is the max frame
    assert peak > 0.0


def test_fade_times_configurable():
    doc = build_expression(_source(), fade_in=0.25, fade_out=0.5)
    assert doc["FadeInTime"] == 0.25 and doc["FadeOutTime"] == 0.5


# --------------------------------------------------------------------------- #
# determinism + CLI                                                            #
# --------------------------------------------------------------------------- #

def test_deterministic_bytes(tmp_path):
    src = _source()
    a, b = str(tmp_path / "a.exp3.json"), str(tmp_path / "b.exp3.json")
    write_live2d_expression(src, a)
    write_live2d_expression(src, b)
    assert open(a, "rb").read() == open(b, "rb").read()


def test_cli_writes_exp3(tmp_path):
    out = str(tmp_path / "face.exp3.json")
    assert cli_main(["naive", "--text", TEXT, "--duration", str(DUR),
                     "-o", out]) == 0
    doc = json.load(open(out))
    assert doc["Type"] == "Live2D Expression"
    assert doc["Parameters"][0]["Id"] == DEFAULT_MOUTH_PARAM


def test_cli_exp3_at_and_params(tmp_path):
    pmap = str(tmp_path / "p.json")
    json.dump({"aa": "ParamA", "E": "ParamE", "I": "ParamI", "O": "ParamO",
               "U": "ParamU"}, open(pmap, "w"))
    out = str(tmp_path / "vowels.exp3.json")
    assert cli_main(["naive", "--text", TEXT, "--duration", str(DUR),
                     "--exp3-at", "0.8", "--live2d-params", pmap, "-o", out]) == 0
    doc = json.load(open(out))
    assert {p["Id"] for p in doc["Parameters"]} <= {"ParamA", "ParamE", "ParamI",
                                                    "ParamO", "ParamU"}


def test_cli_rejects_retarget(tmp_path):
    with pytest.raises(SystemExit, match="retarget"):
        cli_main(["naive", "--text", TEXT, "--duration", str(DUR),
                  "--retarget", "vrm", "-o", str(tmp_path / "x.exp3.json")])
