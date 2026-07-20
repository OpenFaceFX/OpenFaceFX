"""FaceFX-style nonlinear link functions (openfacefx.links, #68).

Response curves beyond linear gain+offset, applied at the two sites that already
compute clamp(gain*v+offset): retarget.apply_adjust (the --adjust knob) and
curves.reduce_to_track (mapping.Target, schema v3). Tests cover each closed-form,
the validator, both application sites, the v3 mapping round-trip, and — crucially —
that the linear/no-link paths stay byte-identical.
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
from openfacefx.curves import Channel, FaceTrack, Keyframe, reduce_to_track
from openfacefx.links import apply_link, normalize_link, LINK_FUNCTIONS
from openfacefx.mapping import Mapping, Target
from openfacefx.retarget import apply_adjust


# --------------------------------------------------------------------------- #
# closed-form correctness (scalar + array)                                     #
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("name,params,x,want", [
    ("linear", {"m": 2.0, "b": 0.1}, 0.5, 1.1),
    ("quadratic", {"m": 1.0}, 0.5, 0.25),
    ("cubic", {"m": 1.0}, 0.5, 0.125),
    ("sqrt", {"m": 1.0}, 0.25, 0.5),
    ("negate", {}, 0.3, -0.3),
    ("constant", {"c": 0.2}, 0.7, 0.2),
    ("clamped_linear", {"m": 1.0, "clampx": 0.5, "clampy": 1.0, "clampdir": "right"}, 0.3, 0.8),
    ("clamped_linear", {"m": 1.0, "clampx": 0.5, "clampy": 1.0, "clampdir": "right"}, 0.7, 1.0),
])
def test_scalar_formulas(name, params, x, want):
    _, p = normalize_link({"function": name, **params})
    assert apply_link(x, name, p) == pytest.approx(want)


def test_vectorized_matches_scalar():
    x = np.array([0.0, 0.25, 0.5, 1.0])
    _, p = normalize_link({"function": "quadratic", "m": 1.0})
    got = apply_link(x, "quadratic", p)
    assert np.allclose(got, [0.0, 0.0625, 0.25, 1.0])
    assert np.allclose(apply_link(x, "constant", {"c": 0.3}), 0.3)   # array-filled


def test_registry_covers_scoped_functions():
    assert set(LINK_FUNCTIONS) == {"linear", "quadratic", "cubic", "sqrt",
                                   "negate", "constant", "clamped_linear"}
    assert "inverse" not in LINK_FUNCTIONS                # scoped out (see module doc)


# --------------------------------------------------------------------------- #
# validation                                                                   #
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("spec,msg", [
    ({"function": "nope"}, "unknown link function"),
    ({"function": "quadratic", "z": 1}, "unknown parameter"),
    ({"function": "linear", "m": float("inf")}, "finite"),
    ({"function": "linear", "m": True}, "finite"),
    ({"function": "clamped_linear", "clampdir": "up"}, "clampdir"),
    ({"function": "negate", "m": 1}, "unknown parameter"),
    ({"m": 1}, "function"),
])
def test_normalize_rejects_bad_specs(spec, msg):
    with pytest.raises(ValueError, match=msg):
        normalize_link(spec)


def test_normalize_fills_defaults():
    assert normalize_link({"function": "quadratic", "m": 2}) == \
        ("quadratic", {"m": 2.0, "b": 0.0})


# --------------------------------------------------------------------------- #
# application site 1: apply_adjust (--adjust)                                   #
# --------------------------------------------------------------------------- #

def _one(name, keys):
    return FaceTrack(fps=60.0, channels=[Channel(name, keys)])


def test_apply_adjust_link_reshapes():
    src = _one("jaw", [Keyframe(0, 0.0), Keyframe(1, 0.5), Keyframe(2, 1.0)])
    out = apply_adjust(src, {"jaw": {"function": "quadratic", "m": 1.0}})
    assert [k.value for k in out.channels[0].keys] == [0.0, 0.25, 1.0]


def test_apply_adjust_tuple_path_byte_identical():
    src = _one("jaw", [Keyframe(0, 0.0), Keyframe(1, 0.5), Keyframe(2, 1.0)])
    out = apply_adjust(src, {"jaw": (0.5, 0.1)})          # linear gain/offset
    assert [k.value for k in out.channels[0].keys] == [0.1, 0.35, 0.6]


def test_apply_adjust_constant_link_materializes_missing_channel():
    src = _one("jaw", [Keyframe(0, 0.0), Keyframe(2, 1.0)])
    out = apply_adjust(src, {"brow": {"function": "constant", "c": 0.3}})
    brow = [c for c in out.channels if c.name == "brow"]
    assert brow and all(k.value == 0.3 for k in brow[0].keys)


# --------------------------------------------------------------------------- #
# application site 2: reduce_to_track (mapping.Target, schema v3)               #
# --------------------------------------------------------------------------- #

def test_reduce_to_track_applies_target_link():
    times = np.array([0.0, 1.0, 2.0])
    matrix = np.array([[0.0], [0.5], [1.0]])
    t = Target("jaw", link={"function": "quadratic", "m": 1.0})
    track = reduce_to_track(times, matrix, fps=60.0, epsilon=0.001, targets=[t])
    vals = {k.time: k.value for k in track.channels[0].keys}
    assert vals[1.0] == pytest.approx(0.25, abs=1e-4)     # 0.5^2, clamped [0,1]


def test_mapping_v3_link_round_trip(tmp_path):
    m = Mapping([Target("jaw", link={"function": "cubic", "m": 1.0}), Target("PP")],
                {"P": {"PP": 1.0}})
    p = str(tmp_path / "m.json")
    m.to_json(p)
    assert json.load(open(p))["version"] == 3
    m2 = Mapping.from_json(p)
    assert m2.targets[0].link == {"function": "cubic", "m": 1.0, "b": 0.0}


def test_mapping_without_link_stays_v2(tmp_path):
    m = Mapping([Target("PP")], {"P": {"PP": 1.0}})
    p = str(tmp_path / "m2.json")
    m.to_json(p)
    assert json.load(open(p))["version"] == 2               # byte-shape unchanged


def test_mapping_rejects_bad_link():
    with pytest.raises(ValueError, match="unknown link function"):
        Mapping([Target("jaw", link={"function": "bogus"})], {"P": {"jaw": 1.0}})


# --------------------------------------------------------------------------- #
# CLI                                                                          #
# --------------------------------------------------------------------------- #

def test_cli_adjust_link(tmp_path):
    adj = str(tmp_path / "adj.json")
    json.dump({"aa": {"function": "sqrt"}}, open(adj, "w"))
    out = str(tmp_path / "t.json")
    assert cli_main(["naive", "--text", "hello brave world", "--duration", "2.0",
                     "--retarget", "vrm", "--adjust", adj, "-o", out]) == 0


def test_cli_adjust_link_rejects_bad(tmp_path):
    bad = str(tmp_path / "bad.json")
    json.dump({"aa": {"function": "nope"}}, open(bad, "w"))
    with pytest.raises(SystemExit, match="unknown link function"):
        cli_main(["naive", "--text", "hello", "--duration", "1.0",
                  "--retarget", "vrm", "--adjust", bad, "-o", str(tmp_path / "x.json")])
