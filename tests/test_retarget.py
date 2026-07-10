"""Retarget: preset integrity, optional-shape fallbacks, rename mapping (#9, #22).

Run with:  python -m pytest  (or)  python tests/test_retarget.py
"""

import os
import sys

try:
    import openfacefx  # noqa: F401
except ImportError:
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from openfacefx import generate_naive, to_dict
from openfacefx.visemes import VISEMES


def test_retarget_combines_scales_and_clamps():
    from openfacefx.curves import Channel, FaceTrack, Keyframe
    from openfacefx.retarget import retarget
    track = FaceTrack(fps=60, channels=[
        Channel("aa", [Keyframe(0.0, 0.8), Keyframe(1.0, 0.0)]),
        Channel("O",  [Keyframe(0.5, 1.0)]),
    ])
    out = retarget(track, {"aa": [("jawOpen", 1.0)], "O": [("jawOpen", 0.5)]})
    (jaw,) = out.channels
    assert jaw.name == "jawOpen"
    # union of key times; a single-key channel holds its value everywhere
    assert [k.time for k in jaw.keys] == [0.0, 0.5, 1.0]
    assert jaw.keys[0].value == 1.0          # 0.8 + 1.0*0.5 = 1.3, clamped
    assert jaw.keys[1].value == 0.9          # aa lerped to 0.4, + 0.5
    assert jaw.keys[2].value == 0.5          # aa 0.0, + 0.5


def test_retarget_presets_integrity():
    from openfacefx.retarget import PRESETS, retarget
    # ARKit names the preset may use (mouth/jaw/tongue subset of Apple's 52)
    arkit_ok = {
        "jawForward", "jawLeft", "jawRight", "jawOpen", "mouthClose",
        "mouthFunnel", "mouthPucker", "mouthLeft", "mouthRight",
        "mouthSmileLeft", "mouthSmileRight", "mouthFrownLeft",
        "mouthFrownRight", "mouthDimpleLeft", "mouthDimpleRight",
        "mouthStretchLeft", "mouthStretchRight", "mouthRollLower",
        "mouthRollUpper", "mouthShrugLower", "mouthShrugUpper",
        "mouthPressLeft", "mouthPressRight", "mouthLowerDownLeft",
        "mouthLowerDownRight", "mouthUpperUpLeft", "mouthUpperUpRight",
        "tongueOut",
    }
    # VRM 0.x names its five lip-sync BlendShapePresets with uppercase single
    # letters (spec 0.0: "A (aa)" "I (ih)" "U (ou)" "E (E)" "O (oh)").
    vrm0_ok = {"A", "I", "U", "E", "O"}
    for name, mapping in PRESETS.items():
        assert mapping, name
        for viseme, targets in mapping.items():
            assert viseme in VISEMES, (name, viseme)
            for target, scale in targets:
                assert 0.0 < scale <= 1.0, (name, viseme, target, scale)
                if name == "arkit":
                    assert target in arkit_ok, (viseme, target)
                if name == "vrm0":
                    assert target in vrm0_ok, (viseme, target)
                # Ready Player Me is the Oculus 15 as viseme_<name> morphs,
                # verbatim casing (Oculus OVR LipSync convention).
                if name == "readyplayerme":
                    assert target == "viseme_" + viseme, (viseme, target)
        # every vowel must land somewhere in every preset
        for vowel in ("aa", "E", "I", "O", "U"):
            assert vowel in mapping, (name, vowel)

    # the new presets cover exactly their ecosystem's shape vocabulary
    assert {t for ts in PRESETS["vrm0"].values() for t, _ in ts} == vrm0_ok
    assert ({t for ts in PRESETS["readyplayerme"].values() for t, _ in ts}
            == {"viseme_" + v for v in VISEMES})

    track = generate_naive("hello world", duration=1.2)
    out = retarget(track, PRESETS["arkit"])
    assert any(c.name == "jawOpen" for c in out.channels)
    assert all(0.0 <= k.value <= 1.0 for c in out.channels for k in c.keys)


def test_retarget_available_and_fallbacks():
    # A rig missing a shape reroutes its weight through the preset's fallback
    # table instead of dropping it silently (issue #22 optional shapes).
    from openfacefx.curves import Channel, FaceTrack, Keyframe
    from openfacefx.retarget import retarget, PRESETS, PRESET_FALLBACKS

    # One TH source at full weight -> arkit: mouthRollUpper 0.6, jawOpen 0.2,
    # tongueOut 0.4. A tongue-less rig sends tongueOut to jawOpen at 0.2 scale.
    track = FaceTrack(fps=60, channels=[Channel("TH", [Keyframe(0.0, 1.0)])])
    full = {c.name: c.keys[0].value for c in retarget(track, PRESETS["arkit"]).channels}
    assert full == {"mouthRollUpper": 0.6, "jawOpen": 0.2, "tongueOut": 0.4}

    avail = {"mouthRollUpper", "jawOpen"}          # rig lacks tongueOut
    out = retarget(track, PRESETS["arkit"], available=avail,
                   fallbacks=PRESET_FALLBACKS["arkit"])
    assert {c.name: c.keys[0].value for c in out.channels} == {
        "mouthRollUpper": 0.6, "jawOpen": 0.28}    # 0.2 direct + 0.4 * 0.2 rerouted
    assert "tongueOut" not in out.target_set       # advertises only real shapes

    # An explicit empty rule drops the weight rather than rerouting it.
    dropped = retarget(track, PRESETS["arkit"], available=avail,
                       fallbacks={"tongueOut": ()})
    assert {c.name: c.keys[0].value for c in dropped.channels} == {
        "mouthRollUpper": 0.6, "jawOpen": 0.2}

    # Fallbacks chain (weights multiply); a cycle is broken, not fatal.
    tk = FaceTrack(fps=60, channels=[Channel("aa", [Keyframe(0.0, 1.0)])])
    chain = retarget(tk, {"aa": [("a", 1.0)]}, available={"c"},
                     fallbacks={"a": (("b", 0.5),), "b": (("c", 0.5),)})
    assert {c.name: c.keys[0].value for c in chain.channels} == {"c": 0.25}
    cyc = retarget(tk, {"aa": [("a", 1.0)]}, available={"z"},
                   fallbacks={"a": (("b", 1.0),), "b": (("a", 1.0),)})
    assert cyc.channels == [] and cyc.target_set == []

    # available=None is a no-op: identical to a plain rename/combine.
    assert to_dict(retarget(tk, {"aa": [("jawOpen", 0.6)]})) == to_dict(
        retarget(tk, {"aa": [("jawOpen", 0.6)]}, available=None))


def test_rhubarb_fallback_single_source():
    # The Rhubarb basic-set collapse lives once, in retarget.PRESET_FALLBACKS;
    # export_cues derives its single-shape cue-label view from that table.
    from openfacefx.retarget import PRESET_FALLBACKS
    from openfacefx.export_cues import RHUBARB_EXTENDED_FALLBACK
    assert RHUBARB_EXTENDED_FALLBACK == {
        k: v[0][0] for k, v in PRESET_FALLBACKS["rhubarb"].items()}
    assert RHUBARB_EXTENDED_FALLBACK == {"G": "A", "H": "C", "X": "A"}


def test_retarget_rename_only():
    from openfacefx.retarget import rename_only, retarget
    track = generate_naive("hello", duration=0.8)
    out = retarget(track, rename_only(prefix="viseme_"))
    assert {c.name for c in out.channels} == {"viseme_" + c.name for c in track.channels}
    src = {c.name: [(k.time, k.value) for k in c.keys] for c in track.channels}
    dst = {c.name: [(k.time, k.value) for k in c.keys] for c in out.channels}
    for name, keys in src.items():
        assert dst["viseme_" + name] == keys


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print("PASS", fn.__name__)
    print(f"\n{len(fns)} tests passed")
