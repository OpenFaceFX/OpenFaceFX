"""Additive emotion/expression layer (`openfacefx.emotion`, issue #38).

The layer is additive, opt-in and deterministic. These tests pin each acceptance
criterion:

  * byte-identity when off -- ``intensity=0``, a neutral valence/arousal track, or
    an exactly-zero delta returns the input track untouched (library object- and
    dict-identity, plus a CLI ``cmp`` of a track baked at ``--intensity 0``);
  * the additive-delta round-trip -- ``base + (pose - reference)`` reconstructs the
    target pose within float tolerance (``eps=0`` so RDP is lossless);
  * per-channel clamps are honoured and ``intensity`` scales the delta linearly
    and deterministically;
  * the fixed valence/arousal table is documented and reproducible (hard-coded
    goldens, neutral maps to an all-zero pose);
  * a baked track validates through ``io_export.from_dict``/``to_dict`` and every
    exporter consumes it unchanged;
  * envelope-JSON schema validation raises clear, field-named ``ValueError``s;
  * a hard-coded golden bake MUST reproduce on Python 3.9 and 3.13 (pure numpy +
    `_rdp`, no RNG, stable 4-dp rounding).
"""

import json
import os
import sys

import numpy as np
import pytest

try:
    import openfacefx  # noqa: F401  (installed wheel wins; see test_core)
except ImportError:
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from openfacefx.cli import main as cli_main
from openfacefx.curves import Channel, FaceTrack, Keyframe
from openfacefx.edits import sample
from openfacefx.emotion import (
    FORMAT, VERSION, VA_AXIS, VA_EMOTION_CHANNELS, VA_TABLE, EmotionEnvelope,
    bake_emotion, load_envelope, save_envelope, va_to_pose,
)
from openfacefx.io_export import from_dict, read_json, to_dict, write_json


# --------------------------------------------------------------------------- #
# helpers                                                                      #
# --------------------------------------------------------------------------- #

def _track(channels=None, fps=60.0, target_set=None):
    if channels is None:
        channels = [Channel("aa", [Keyframe(0.0, 0.2), Keyframe(0.5, 0.6),
                                   Keyframe(1.0, 0.2)])]
    return FaceTrack(fps, channels, target_set)


def _channels_env(channels, reference=None, clamps=None):
    d = {"format": FORMAT, "version": VERSION, "mode": "channels",
         "channels": channels}
    if reference is not None:
        d["reference"] = reference
    if clamps is not None:
        d["clamps"] = clamps
    return d


def _va_env(valence=None, arousal=None, reference=None):
    va = {}
    if valence is not None:
        va["valence"] = valence
    if arousal is not None:
        va["arousal"] = arousal
    d = {"format": FORMAT, "version": VERSION, "mode": "valence_arousal", "va": va}
    if reference is not None:
        d["reference"] = reference
    return d


def _by_name(track):
    return {c.name: c for c in track.channels}


# --------------------------------------------------------------------------- #
# 1. additive / byte-identity when off                                        #
# --------------------------------------------------------------------------- #

def test_intensity_zero_returns_input_untouched():
    tr = _track()
    env = _va_env(valence=[[0.0, 1.0], [1.0, 1.0]])   # a real, non-neutral track
    baked = bake_emotion(tr, env, intensity=0.0)
    assert baked is tr                                # the very same object
    assert to_dict(baked) == to_dict(tr)


def test_neutral_va_at_full_intensity_is_byte_identical():
    # valence=arousal=0 is the neutral grid node -> all-zero pose -> zero delta.
    tr = _track()
    env = _va_env(valence=[[0.0, 0.0], [1.0, 0.0]], arousal=[[0.0, 0.0], [1.0, 0.0]])
    baked = bake_emotion(tr, env, intensity=1.0)
    assert to_dict(baked) == to_dict(tr)


def test_zero_delta_channels_pass_through_verbatim():
    # pose == reference everywhere -> delta 0 -> untouched, even with clamps set.
    tr = _track([Channel("smile", [Keyframe(0.0, 0.3), Keyframe(1.0, 0.3)])])
    env = _channels_env({"smile": [[0.0, 0.3], [1.0, 0.3]]},
                        reference={"smile": 0.3}, clamps={"smile": [0.2, 0.5]})
    baked = bake_emotion(tr, env, intensity=1.0)
    assert baked is tr
    assert to_dict(baked) == to_dict(tr)


def test_absent_target_set_sentinel_preserved_when_off():
    tr = _track(target_set=None)
    baked = bake_emotion(tr, _va_env(valence=[[0.0, 0.0]]), intensity=0.0)
    assert baked.target_set is None


def test_cli_intensity_zero_is_byte_identical(tmp_path):
    base = str(tmp_path / "base.json")
    env = str(tmp_path / "env.json")
    out = str(tmp_path / "baked.json")
    assert cli_main(["naive", "--text", "hello world", "--duration", "1.5",
                     "-o", base]) == 0
    with open(env, "w") as fh:
        json.dump(_va_env(valence=[[0.0, 1.0], [1.5, -1.0]],
                          arousal=[[0.0, 0.5], [1.5, 0.9]]), fh)
    assert cli_main(["emotion", base, env, "--intensity", "0", "-o", out]) == 0
    assert open(base).read() == open(out).read()      # cmp-equal


def test_cli_neutral_env_intensity_one_is_byte_identical(tmp_path):
    base = str(tmp_path / "base.json")
    env = str(tmp_path / "env.json")
    out = str(tmp_path / "baked.json")
    cli_main(["naive", "--text", "hello world", "--duration", "1.5", "-o", base])
    with open(env, "w") as fh:
        json.dump(_va_env(valence=[[0.0, 0.0], [1.5, 0.0]],
                          arousal=[[0.0, 0.0], [1.5, 0.0]]), fh)
    assert cli_main(["emotion", base, env, "--intensity", "1", "-o", out]) == 0
    assert open(base).read() == open(out).read()


# --------------------------------------------------------------------------- #
# 2. additive-delta round-trip                                                #
# --------------------------------------------------------------------------- #

def test_delta_round_trip_reconstructs_target_pose():
    # base == reference (constant) => base + (pose - reference) == pose exactly.
    ref = 0.4
    base = _track([Channel("smile", [Keyframe(0.0, ref), Keyframe(0.5, ref),
                                     Keyframe(1.0, ref)])])
    pose = [[0.0, 0.1], [0.5, 0.9], [1.0, 0.3]]
    env = _channels_env({"smile": pose}, reference={"smile": ref})
    baked = bake_emotion(base, env, intensity=1.0, eps=0.0)   # eps=0 -> lossless
    smile = _by_name(baked)["smile"]
    T = np.array([0.0, 0.25, 0.5, 0.75, 1.0])
    assert np.allclose(sample(smile, T), sample(pose, T), atol=1e-9)


def test_bake_equals_base_plus_delta_formula():
    # general additive identity where the clamp does not bind.
    base = _track([Channel("smile", [Keyframe(0.0, 0.2), Keyframe(1.0, 0.5)])])
    pose = [[0.0, 0.3], [1.0, 0.1]]
    ref = 0.25
    env = _channels_env({"smile": pose}, reference={"smile": ref})
    baked = bake_emotion(base, env, intensity=1.0, eps=0.0)
    smile = _by_name(baked)["smile"]
    T = np.linspace(0.0, 1.0, 11)
    expected = sample([[0.0, 0.2], [1.0, 0.5]], T) + (sample(pose, T) - ref)
    assert np.allclose(sample(smile, T), expected, atol=1e-9)


# --------------------------------------------------------------------------- #
# 3. clamps honoured, intensity linear & deterministic                        #
# --------------------------------------------------------------------------- #

def test_clamp_caps_baked_value():
    base = _track([Channel("smile", [Keyframe(0.0, 0.8), Keyframe(1.0, 0.8)])])
    env = _channels_env({"smile": [[0.0, 0.9], [1.0, 0.9]]})   # 0.8 + 0.9 -> 1.7
    default = _by_name(bake_emotion(base, env, intensity=1.0))["smile"]
    assert max(k.value for k in default.keys) == 1.0           # default [0,1]
    capped = _by_name(bake_emotion(base, env, intensity=1.0,
                                   clamps={"smile": (0.0, 0.5)}))["smile"]
    assert max(k.value for k in capped.keys) == 0.5


def test_envelope_clamp_used_and_param_overrides_it():
    base = _track([Channel("smile", [Keyframe(0.0, 0.0), Keyframe(1.0, 0.0)])])
    env = _channels_env({"smile": [[0.0, 0.9], [1.0, 0.9]]},
                        clamps={"smile": [0.0, 0.3]})
    assert max(k.value for k in _by_name(bake_emotion(base, env))["smile"].keys) == 0.3
    over = bake_emotion(base, env, clamps={"smile": (0.0, 0.6)})
    assert max(k.value for k in _by_name(over)["smile"].keys) == 0.6


def test_intensity_scales_delta_linearly():
    base = _track([Channel("smile", [Keyframe(0.0, 0.0), Keyframe(1.0, 0.0)])])
    env = _channels_env({"smile": [[0.0, 0.4], [1.0, 0.4]]})   # flat 0.4, no clamp bind
    half = _by_name(bake_emotion(base, env, intensity=0.5))["smile"]
    full = _by_name(bake_emotion(base, env, intensity=1.0))["smile"]
    assert np.isclose(max(k.value for k in half.keys), 0.2)
    assert np.isclose(max(k.value for k in full.keys), 0.4)


def test_bake_is_deterministic():
    base = _track()
    env = _va_env(valence=[[0.0, -0.5], [1.0, 0.8]], arousal=[[0.0, 0.9], [1.0, 0.1]])
    a = bake_emotion(base, env, intensity=0.7)
    b = bake_emotion(base, env, intensity=0.7)
    assert to_dict(a) == to_dict(b)


def test_negative_or_nonfinite_intensity_rejected():
    base = _track()
    env = _va_env(valence=[[0.0, 1.0]])
    for bad in (-0.5, float("nan"), float("inf")):
        with pytest.raises(ValueError):
            bake_emotion(base, env, intensity=bad)


# --------------------------------------------------------------------------- #
# 4. valence/arousal table -- documented & reproducible                       #
# --------------------------------------------------------------------------- #

def test_va_neutral_is_all_zero():
    assert va_to_pose(0.0, 0.0) == {ch: 0.0 for ch in VA_EMOTION_CHANNELS}


def test_va_corner_goldens():
    assert va_to_pose(1.0, 1.0)["smile"] == 0.90        # elated
    assert va_to_pose(1.0, 1.0)["cheek_raise"] == 0.80
    assert va_to_pose(-1.0, 1.0)["brow_lower"] == 0.85  # anger: low v + high a
    assert va_to_pose(-1.0, -1.0)["frown"] == 0.70      # sad
    assert va_to_pose(0.0, 1.0)["brow_raise"] == 0.70   # surprise


def test_va_bilinear_interpolation_goldens():
    # midpoints of the 3x3 grid are exact linear blends of the nodes.
    assert np.isclose(va_to_pose(0.5, 1.0)["smile"], 0.45)   # (0->0, 1->0.9) @0.5
    assert np.isclose(va_to_pose(1.0, 0.5)["smile"], 0.80)   # (0.70->0.90) @0.5
    assert np.isclose(va_to_pose(-0.5, 1.0)["brow_lower"], 0.425)


def test_va_out_of_range_clamps_to_edge():
    assert va_to_pose(5.0, 5.0) == va_to_pose(1.0, 1.0)
    assert va_to_pose(-9.0, -9.0) == va_to_pose(-1.0, -1.0)


def test_va_table_shape_and_neutral_node():
    assert VA_AXIS == (-1.0, 0.0, 1.0)
    for ch in VA_EMOTION_CHANNELS:
        grid = VA_TABLE[ch]
        assert len(grid) == 3 and all(len(row) == 3 for row in grid)
        assert grid[1][1] == 0.0                        # centre node == neutral


# --------------------------------------------------------------------------- #
# 5. exporters consume the baked track; io_export round-trip                  #
# --------------------------------------------------------------------------- #

def test_baked_track_round_trips_through_io_export():
    base = _track()
    env = _va_env(valence=[[0.0, 0.9], [1.0, 0.9]], arousal=[[0.0, 0.8], [1.0, 0.8]])
    baked = bake_emotion(base, env, intensity=1.0)
    d = to_dict(baked)
    assert to_dict(from_dict(d)) == d                   # faithful inverse
    assert "smile" in {c.name for c in baked.channels}


def test_exporters_consume_baked_track(tmp_path):
    from openfacefx.export_unity import write_unity_anim
    from openfacefx.export_cues import write_rhubarb_tsv
    from openfacefx.io_export import write_csv
    base = _track()
    env = _va_env(valence=[[0.0, 0.9], [1.0, 0.9]], arousal=[[0.0, 0.9], [1.0, 0.9]])
    baked = bake_emotion(base, env, intensity=1.0)
    # a curve exporter carries every channel (incl. the emotion channels)...
    csv_path = str(tmp_path / "b.csv")
    write_csv(baked, csv_path)
    assert "smile" in open(csv_path).read()
    anim_path = str(tmp_path / "b.anim")
    write_unity_anim(baked, anim_path)                  # must not raise
    assert os.path.getsize(anim_path) > 0
    # ...and a mouth-only cue exporter simply ignores the non-viseme channels.
    tsv_path = str(tmp_path / "b.tsv")
    write_rhubarb_tsv(baked, tsv_path)
    assert "smile" not in open(tsv_path).read()


def test_new_emotion_channels_extend_target_set():
    base = _track(target_set=None)
    env = _va_env(valence=[[0.0, 1.0], [1.0, 1.0]])     # pleasant -> smile etc.
    baked = bake_emotion(base, env, intensity=1.0)
    assert baked.target_set is not None
    assert "smile" in baked.target_set
    assert "aa" in baked.target_set                     # base vocab preserved


# --------------------------------------------------------------------------- #
# 6. envelope schema + validation                                             #
# --------------------------------------------------------------------------- #

def test_envelope_save_load_round_trip(tmp_path):
    env = EmotionEnvelope.from_dict(
        _channels_env({"smile": [[0.0, 0.0], [1.0, 0.8]]},
                      reference={"smile": 0.1}, clamps={"smile": [0.0, 1.0]}))
    p = str(tmp_path / "e.emotion.json")
    save_envelope(env, p)
    again = load_envelope(p)
    assert again.to_dict() == env.to_dict()


def test_va_envelope_round_trip(tmp_path):
    env = EmotionEnvelope.from_dict(_va_env(valence=[[0.0, 0.0], [1.0, 0.9]]))
    p = str(tmp_path / "va.emotion.json")
    save_envelope(env, p)
    assert load_envelope(p).to_dict() == env.to_dict()


@pytest.mark.parametrize("bad, needle", [
    ({"format": "x", "version": 1, "mode": "channels", "channels": {}}, "format"),
    ({"format": FORMAT, "version": 1, "mode": "bogus"}, "mode"),
    ({"format": FORMAT, "version": 1, "mode": "channels", "channels": {}},
     "non-empty"),
    ({"format": FORMAT, "version": 1, "mode": "channels",
      "channels": {"s": [[1.0, 0.1], [0.5, 0.2]]}}, "ascending"),
    ({"format": FORMAT, "version": 1, "mode": "valence_arousal", "va": {}},
     "at least one"),
    ({"format": FORMAT, "version": 1, "mode": "valence_arousal",
      "va": {"mood": [[0.0, 0.0]]}}, "unknown"),
    ({"format": FORMAT, "version": 1, "mode": "channels",
      "channels": {"s": [[0.0, 0.1]]}, "clamps": {"s": [0.9, 0.1]}}, "clamp"),
    ({"format": FORMAT, "version": 1, "mode": "channels",
      "channels": {"s": [[0.0, 0.1]]}, "reference": {"s": "x"}}, "reference"),
    ({"format": FORMAT, "version": 1, "mode": "valence_arousal",
      "va": {"valence": [[0.0, 0.0]]}, "fps": 0}, "fps"),
])
def test_envelope_validation_errors(bad, needle):
    with pytest.raises(ValueError) as ei:
        EmotionEnvelope.from_dict(bad)
    assert needle in str(ei.value)


def test_load_envelope_bad_json(tmp_path):
    p = str(tmp_path / "broken.json")
    with open(p, "w") as fh:
        fh.write("{not json")
    with pytest.raises(ValueError):
        load_envelope(p)


# --------------------------------------------------------------------------- #
# 7. golden bake -- MUST reproduce on Python 3.9 and 3.13                      #
# --------------------------------------------------------------------------- #

def test_golden_bake_reproducible():
    base = FaceTrack(60.0, [
        Channel("aa", [Keyframe(0.0, 0.2), Keyframe(0.5, 0.6), Keyframe(1.0, 0.2)]),
        Channel("smile", [Keyframe(0.0, 0.1), Keyframe(1.0, 0.1)]),
    ], None)
    env = _channels_env(
        {"smile": [[0.0, 0.0], [0.5, 0.8], [1.0, 0.0]],
         "brow_raise": [[0.0, 0.0], [0.5, 0.5], [1.0, 0.0]]},
        reference={"smile": 0.1})
    baked = bake_emotion(base, env, intensity=0.5, eps=0.015)
    got = {c.name: [(k.time, k.value) for k in c.keys] for c in baked.channels}
    assert got == {
        "aa": [(0.0, 0.2), (0.5, 0.6), (1.0, 0.2)],       # untouched
        "smile": [(0.0, 0.05), (0.5, 0.45), (1.0, 0.05)],  # 0.1 + 0.5*(pose-0.1)
        "brow_raise": [(0.0, 0.0), (0.5, 0.25), (1.0, 0.0)],  # new, 0.5*pose
    }
    assert baked.target_set[-1] == "brow_raise"


def test_direct_channels_mode_adds_channel():
    base = _track()
    env = _channels_env({"brow_raise": [[0.0, 0.0], [0.5, 0.6], [1.0, 0.0]]})
    baked = bake_emotion(base, env, intensity=1.0)
    brow = _by_name(baked)["brow_raise"]
    assert np.isclose(max(k.value for k in brow.keys), 0.6)
