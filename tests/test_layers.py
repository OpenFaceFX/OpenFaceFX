"""Layered multi-track export (openfacefx.layers, issue #39).

The highest-risk feature: it extends `io_export.to_dict`/`from_dict`. The #1
invariant — the default (layers-off) path is BYTE-IDENTICAL — is proved by the
whole existing suite staying green plus the explicit checks here. Also pinned:
summing the layers at weight 1 reproduces the flat merged track within tolerance
(a faithful decomposition); layer names / weights / priorities survive
`write_json`→`read_json`; and empty/absent layers are omitted, not emitted as dead
channels.
"""

import copy
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
from openfacefx.emotion import bake_emotion
from openfacefx.gestures import GestureParams
from openfacefx.io_export import from_dict, read_json, to_dict, write_json
from openfacefx.layers import (Layer, build_layers, flatten_layers,
                               layers_from_dict, layers_to_dict)
from openfacefx.pipeline import generate_from_alignment, naive_segments

TEXT, DUR = "hello brave new world", 2.3
_ENV = {"format": "openfacefx.emotion", "version": 1, "mode": "valence_arousal",
        "va": {"valence": [[0, 1], [DUR, 1]], "arousal": [[0, 0.8], [DUR, 0.8]]}}


def _plain():
    return generate_from_alignment(naive_segments(TEXT, DUR), fps=60.0)


def _merged():
    """A track with all three layers: speech + gesture + emotion."""
    t = generate_from_alignment(naive_segments(TEXT, DUR), fps=60.0,
                                gestures=GestureParams(seed=1))
    return bake_emotion(t, _ENV, intensity=1.0)


def _max_diff_by_name(a, b):
    amap = {c.name: c for c in a.channels}
    bmap = {c.name: c for c in b.channels}
    assert set(amap) == set(bmap)
    T = np.linspace(0.0, max(a.duration, b.duration), 80)
    return max(float(np.max(np.abs(sample(amap[n], T) - sample(bmap[n], T))))
               for n in amap)


# --------------------------------------------------------------------------- #
# 1. byte-identity when layers off (the sacred invariant)                      #
# --------------------------------------------------------------------------- #

def test_to_dict_has_no_layers_key_by_default():
    for t in (_plain(), _merged()):
        d = to_dict(t)
        assert "layers" not in d                          # default schema unchanged


def test_to_dict_stable_and_from_dict_faithful_without_layers():
    t = _merged()
    d = to_dict(t)
    assert to_dict(from_dict(d)) == d                     # documented invariant holds


def test_empty_layers_list_omitted_not_emitted():
    t = _plain()
    assert "layers" not in to_dict(t, layers=[])          # empty => no key


def test_from_dict_ignores_absent_layers():
    t = read_json_roundtrip(_plain())
    assert getattr(t, "layers", None) is None


def read_json_roundtrip(track):
    return from_dict(to_dict(track))


# --------------------------------------------------------------------------- #
# 2. faithful decomposition: sum(layers) == flat track                        #
# --------------------------------------------------------------------------- #

def test_build_layers_is_a_disjoint_complete_split():
    t = _merged()
    layers = build_layers(t)
    assert [l.name for l in layers] == ["speech", "emotion", "gesture"]
    names = [c.name for l in layers for c in l.channels]
    assert sorted(names) == sorted(c.name for c in t.channels)   # complete
    assert len(names) == len(set(names))                          # disjoint


def test_layer_sum_reproduces_flat_merged_track():
    t = _merged()
    flat = flatten_layers(build_layers(t), fps=t.fps)
    assert _max_diff_by_name(t, flat) < 1e-9                      # exact, in fact


def test_classification_speech_emotion_gesture():
    t = _merged()
    layers = {l.name: {c.name for c in l.channels} for l in build_layers(t)}
    from openfacefx.gestures import GESTURE_CHANNELS
    from openfacefx.emotion import VA_EMOTION_CHANNELS
    assert layers["gesture"] <= set(GESTURE_CHANNELS)
    assert layers["emotion"] <= set(VA_EMOTION_CHANNELS)
    assert not (layers["speech"] & (set(GESTURE_CHANNELS) | set(VA_EMOTION_CHANNELS)))


def test_empty_layers_are_omitted_from_build():
    layers = build_layers(_plain())                              # visemes only
    assert [l.name for l in layers] == ["speech"]                # no emotion/gesture


# --------------------------------------------------------------------------- #
# 3. names / weights / priorities survive write_json -> read_json             #
# --------------------------------------------------------------------------- #

def test_layers_survive_write_read_json(tmp_path):
    t = _merged()
    layers = build_layers(t)
    p = str(tmp_path / "layered.json")
    write_json(t, p, layers=layers)
    back = read_json(p)
    assert back.layers is not None
    assert [l.name for l in back.layers] == [l.name for l in layers]
    assert [l.priority for l in back.layers] == [l.priority for l in layers]
    assert [l.weight for l in back.layers] == [l.weight for l in layers]
    # channels survive too
    assert [[c.name for c in l.channels] for l in back.layers] == \
           [[c.name for c in l.channels] for l in layers]


def test_custom_weight_curve_and_priority_round_trip():
    t = _merged()
    layers = build_layers(t)
    layers[0].weight = [[0.0, 0.0], [1.0, 1.0]]                  # a ramp
    layers[0].priority = 7
    d = to_dict(t, layers=layers)
    back = layers_from_dict(d["layers"])
    assert back[0].weight == [[0.0, 0.0], [1.0, 1.0]]
    assert back[0].priority == 7


def test_flatten_applies_the_blend_weight():
    t = FaceTrack(60.0, [Channel("aa", [Keyframe(0.0, 0.4), Keyframe(1.0, 0.8)])],
                  None)
    half = Layer("speech", t.channels, [[0.0, 0.5]], 0)          # constant 0.5
    flat = flatten_layers([half], fps=60.0)
    vals = [k.value for k in flat.channels[0].keys]
    assert vals == pytest.approx([0.2, 0.4])                     # halved


# --------------------------------------------------------------------------- #
# 4. validation + CLI                                                          #
# --------------------------------------------------------------------------- #

def test_layers_from_dict_rejects_malformed():
    with pytest.raises(ValueError):
        layers_from_dict({"not": "a list"})
    with pytest.raises(ValueError):
        layers_from_dict([{"no": "name"}])


def test_layered_track_still_validates_and_round_trips():
    from openfacefx.inspect import validate_asset
    t = _merged()
    d = to_dict(t, layers=build_layers(t))
    assert to_dict(from_dict(d)) == d                            # idempotent
    # the flat track still passes the #47 linter (layers are ignored there)
    assert not [p for p in validate_asset(d)[1] if p["severity"] == "error"]


def test_cli_export_layers(tmp_path):
    src = str(tmp_path / "m.json")
    write_json(_merged(), src)
    out = str(tmp_path / "layered.json")
    assert cli_main(["export-layers", src, "-o", out]) == 0
    d = json.load(open(out))
    assert d["format"] == "openfacefx.track"
    assert [l["name"] for l in d["layers"]] == ["speech", "emotion", "gesture"]
    # the flat channel list is byte-for-byte what a plain write would produce
    assert d["channels"] == to_dict(read_json(src))["channels"]


def test_cli_export_layers_flat_channels_unchanged(tmp_path):
    src = str(tmp_path / "m.json")
    write_json(_merged(), src)
    out = str(tmp_path / "layered.json")
    cli_main(["export-layers", src, "-o", out])
    # dropping the layers key reproduces the original flat track byte-for-byte
    d = json.load(open(out))
    d.pop("layers")
    assert d == json.load(open(src))
