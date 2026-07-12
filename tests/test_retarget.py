"""Retarget: preset integrity, optional-shape fallbacks, rename mapping,
per-target gain/offset trim and the CLI --adjust/--retarget-shapes flags (#9, #22).

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


def test_arkit_dd_gains_tongue_out_kk_stays_velar():
    # issue #53 (A'): the alveolar DD viseme (t/d/l) now fires ARKit's tongueOut
    # at 0.2, matching nn — a deliberate, versioned change to the shipped preset's
    # output for t/d/l tracks. Velar kk (k/g) stays tongue-free on purpose.
    from openfacefx.curves import Channel, FaceTrack, Keyframe
    from openfacefx.retarget import retarget, PRESETS, PRESET_FALLBACKS
    dd = FaceTrack(fps=60, channels=[Channel("DD", [Keyframe(0.0, 1.0)])])
    full = {c.name: c.keys[0].value for c in retarget(dd, PRESETS["arkit"]).channels}
    assert full == {"mouthPressLeft": 0.8, "mouthPressRight": 0.8,
                    "mouthFunnel": 0.5, "jawOpen": 0.2, "tongueOut": 0.2}
    # a tongue-less rig reroutes DD's tongueOut to jawOpen, exactly like nn/TH
    avail = {"mouthPressLeft", "mouthPressRight", "mouthFunnel", "jawOpen"}
    out = {c.name: c.keys[0].value for c in
           retarget(dd, PRESETS["arkit"], available=avail,
                    fallbacks=PRESET_FALLBACKS["arkit"]).channels}
    assert out == {"mouthPressLeft": 0.8, "mouthPressRight": 0.8,
                   "mouthFunnel": 0.5, "jawOpen": 0.24}   # 0.2 direct + 0.2*0.2 rerouted
    assert "tongueOut" not in out                          # advertised as rerouted
    # velar kk (k/g) is deliberately tongue-free: back of tongue, no protrusion
    kk = FaceTrack(fps=60, channels=[Channel("kk", [Keyframe(0.0, 1.0)])])
    assert "tongueOut" not in {c.name for c in retarget(kk, PRESETS["arkit"]).channels}


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


# ---------------------------------------------------------------------------
# Per-target gain/offset (#22): retarget(adjust=) and the apply_adjust helper.
# ---------------------------------------------------------------------------

def test_adjust_gain_offset_and_clamp():
    # gain multiplies, offset adds, result clamps to [0, 1] per target; a target
    # with no adjust entry is passed through untouched.
    from openfacefx.curves import Channel, FaceTrack, Keyframe
    from openfacefx.retarget import apply_adjust
    tk = FaceTrack(fps=60, channels=[
        Channel("jawOpen", [Keyframe(0.0, 0.5), Keyframe(1.0, 1.0)]),
        Channel("mouthPucker", [Keyframe(0.0, 0.4)]),
    ])
    gain = apply_adjust(tk, {"jawOpen": (0.8, 0.0)})
    assert [(k.time, k.value) for k in gain.channels[0].keys] == [(0.0, 0.4), (1.0, 0.8)]
    off = apply_adjust(tk, {"jawOpen": (1.0, 0.1)})
    assert [k.value for k in off.channels[0].keys] == [0.6, 1.0]       # 1.1 -> clamp 1
    clamp = apply_adjust(tk, {"jawOpen": (3.0, -0.2)})
    assert [k.value for k in clamp.channels[0].keys] == [1.0, 1.0]     # 1.3/2.8 -> 1
    assert [(k.time, k.value) for k in gain.channels[1].keys] == [(0.0, 0.4)]  # untouched


def test_adjust_noop_and_retarget_equivalence():
    # None/empty adjust is a byte-identical no-op, and retarget(adjust=A) equals
    # apply_adjust(retarget(), A) exactly -- adjust never perturbs the preset table.
    from openfacefx.retarget import retarget, apply_adjust, PRESETS
    track = generate_naive("hello world", duration=1.3)
    base = retarget(track, PRESETS["arkit"])
    assert to_dict(retarget(track, PRESETS["arkit"], adjust=None)) == to_dict(base)
    assert to_dict(retarget(track, PRESETS["arkit"], adjust={})) == to_dict(base)
    assert apply_adjust(base, {}) is base                 # no-op returns the same object
    A = {"jawOpen": (0.7, 0.05), "mouthPucker": (1.1, 0.0), "mouthFunnel": (0.5, 0.1)}
    assert (to_dict(retarget(track, PRESETS["arkit"], adjust=A))
            == to_dict(apply_adjust(base, A)))


def test_adjust_creates_always_on_shape():
    # A positive-offset target with no curve yet becomes a constant channel over
    # the clip and joins target_set ("mouthSmile always slightly on"), without
    # editing the mapping; a declared-but-unfired target is not duplicated in
    # target_set; zero/negative/gain-only absent targets create nothing.
    from openfacefx.curves import Channel, FaceTrack, Keyframe
    from openfacefx.retarget import apply_adjust
    tk = FaceTrack(fps=60,
                   channels=[Channel("jawOpen", [Keyframe(0.0, 0.5), Keyframe(2.0, 0.2)])],
                   target_set=["jawOpen", "mouthFunnel"])   # mouthFunnel declared, unfired
    out = apply_adjust(tk, {"mouthSmileLeft": (1.0, 0.2),    # absent -> create constant
                            "mouthFunnel": (1.0, 0.3),        # declared, unfired -> create
                            "jawOpen": (0.5, 0.0)})           # existing -> scale
    chans = {c.name: [(k.time, k.value) for k in c.keys] for c in out.channels}
    assert chans["jawOpen"] == [(0.0, 0.25), (2.0, 0.1)]
    assert chans["mouthSmileLeft"] == [(0.0, 0.2), (2.0, 0.2)]   # constant over clip span
    assert chans["mouthFunnel"] == [(0.0, 0.3), (2.0, 0.3)]
    assert out.target_set == ["jawOpen", "mouthFunnel", "mouthSmileLeft"]  # deduped
    none_made = apply_adjust(tk, {"a": (1.0, 0.0), "b": (1.0, -0.3), "c": (5.0, 0.0)})
    assert {c.name for c in none_made.channels} == {"jawOpen"}


def test_adjust_preserves_events_and_is_deterministic():
    from openfacefx.curves import Channel, FaceTrack, Keyframe
    from openfacefx.retarget import retarget, apply_adjust, PRESETS
    from openfacefx.events import Event
    tk = FaceTrack(fps=60, channels=[Channel("jawOpen", [Keyframe(0.0, 0.5)])])
    tk.events = [Event(0.2, "emphasis", "beat")]
    out = apply_adjust(tk, {"jawOpen": (0.5, 0.0)})
    assert out.events == tk.events and out.variants is tk.variants
    track = generate_naive("determinism check please", duration=1.1)
    A = {"jawOpen": (0.83, 0.017), "mouthFunnel": (1.0, 0.2)}
    assert (to_dict(retarget(track, PRESETS["arkit"], adjust=A))
            == to_dict(retarget(track, PRESETS["arkit"], adjust=A)))


# --- CLI: --adjust and --retarget-shapes end-to-end -----------------------

def _cli_naive(tmp, out_name, *extra):
    from openfacefx.cli import main as cli_main
    out = os.path.join(tmp, out_name)
    rc = cli_main(["naive", "--text", "thin south path is here", "--duration",
                   "1.4", "-o", out, *extra])
    return rc, out


def test_cli_retarget_adjust_end_to_end():
    import json, tempfile
    with tempfile.TemporaryDirectory() as tmp:
        adj = os.path.join(tmp, "adjust.json")
        with open(adj, "w") as fh:
            json.dump({"jawOpen": {"gain": 0.5},
                       "mouthSmileLeft": {"offset": 0.2}}, fh)
        rc, out = _cli_naive(tmp, "r.json", "--retarget", "arkit", "--adjust", adj)
        assert rc == 0
        with open(out) as fh:
            d = json.load(fh)
        chans = {c["name"]: c["keys"] for c in d["channels"]}
        # mouthSmileLeft is not in the arkit table; the offset lifts it "always on"
        assert "mouthSmileLeft" in chans and "mouthSmileLeft" in d["viseme_set"]
        assert all(v == 0.2 for _, v in chans["mouthSmileLeft"])
        assert all(0.0 <= v <= 1.0 for c in d["channels"] for _, v in c["keys"])


def test_cli_adjust_empty_is_byte_identical():
    import json, tempfile
    with tempfile.TemporaryDirectory() as tmp:
        empty = os.path.join(tmp, "e.json")
        with open(empty, "w") as fh:
            fh.write("{}")
        rc1, out1 = _cli_naive(tmp, "a.json", "--retarget", "arkit")
        rc2, out2 = _cli_naive(tmp, "b.json", "--retarget", "arkit", "--adjust", empty)
        assert rc1 == 0 and rc2 == 0
        with open(out1, "rb") as f1, open(out2, "rb") as f2:
            assert f1.read() == f2.read()


def test_cli_adjust_schema_errors():
    import tempfile
    import pytest
    from openfacefx.cli import main as cli_main
    with tempfile.TemporaryDirectory() as tmp:
        out = os.path.join(tmp, "x.json")

        def argv(doc):
            p = os.path.join(tmp, "adj.json")
            with open(p, "w") as fh:
                fh.write(doc)
            return ["naive", "--text", "hi", "--duration", "1", "--retarget",
                    "arkit", "--adjust", p, "-o", out]

        with pytest.raises(SystemExit, match="finite number"):
            cli_main(argv('{"jawOpen": {"gain": "x"}}'))
        with pytest.raises(SystemExit, match="unknown key"):
            cli_main(argv('{"jawOpen": {"scale": 0.5}}'))
        with pytest.raises(SystemExit, match="finite number"):     # JSON bool != number
            cli_main(argv('{"jawOpen": {"gain": true}}'))
        with pytest.raises(SystemExit, match="must be an object"):
            cli_main(argv('{"jawOpen": 0.5}'))
        with pytest.raises(SystemExit, match="JSON object"):
            cli_main(argv('["jawOpen"]'))
        with pytest.raises(SystemExit, match="cannot read"):
            cli_main(["naive", "--text", "hi", "--duration", "1", "--retarget",
                      "arkit", "--adjust", os.path.join(tmp, "nope.json"), "-o", out])


def test_cli_retarget_shapes_filters_and_requires_retarget():
    import json, tempfile
    import pytest
    from openfacefx.cli import main as cli_main
    with tempfile.TemporaryDirectory() as tmp:
        shapes = os.path.join(tmp, "shapes.json")
        with open(shapes, "w") as fh:                    # tongue-less ARKit rig
            json.dump(["jawOpen", "jawForward", "mouthPucker", "mouthFunnel",
                       "mouthRollUpper", "mouthRollLower", "mouthPressLeft",
                       "mouthPressRight", "mouthDimpleLeft", "mouthDimpleRight",
                       "mouthLowerDownLeft", "mouthLowerDownRight",
                       "mouthUpperUpLeft", "mouthUpperUpRight", "mouthShrugUpper"], fh)
        rc, out = _cli_naive(tmp, "s.json", "--retarget", "arkit",
                             "--retarget-shapes", shapes)
        assert rc == 0
        with open(out) as fh:
            d = json.load(fh)
        names = {c["name"] for c in d["channels"]}
        assert "tongueOut" not in names and "tongueOut" not in d["viseme_set"]
        assert "jawOpen" in names                        # TH/nn tongue weight rerouted here
        with pytest.raises(SystemExit, match="needs --retarget"):
            cli_main(["naive", "--text", "hi", "--duration", "1",
                      "--retarget-shapes", shapes, "-o", os.path.join(tmp, "y.json")])
        empty = os.path.join(tmp, "empty.json")
        with open(empty, "w") as fh:
            fh.write("[]")
        with pytest.raises(SystemExit, match="empty"):
            cli_main(["naive", "--text", "hi", "--duration", "1", "--retarget",
                      "arkit", "--retarget-shapes", empty, "-o", os.path.join(tmp, "z.json")])


def test_cli_adjust_rejected_on_noncurve_formats():
    import json, tempfile
    import pytest
    from openfacefx.cli import main as cli_main
    with tempfile.TemporaryDirectory() as tmp:
        adj = os.path.join(tmp, "a.json")
        with open(adj, "w") as fh:
            json.dump({"jawOpen": {"gain": 0.5}}, fh)
        for out_name, pat in (("c.tsv", "cue formats"),
                              ("m.motion3.json", "Live2D"),
                              ("g.tres", "Godot")):
            with pytest.raises(SystemExit, match=pat):
                cli_main(["naive", "--text", "hi", "--duration", "1",
                          "--adjust", adj, "-o", os.path.join(tmp, out_name)])


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print("PASS", fn.__name__)
    print(f"\n{len(fns)} tests passed")


def test_retarget_preserves_events(tmp_path):
    """Retargeting renames mouth channels but must not drop the event/take
    layer (issue #34) — events are timeline metadata, not visemes."""
    from openfacefx import generate_naive, retarget, PRESETS
    from openfacefx.events import Event
    track = generate_naive("hello world", duration=1.2)
    track.events = [Event(0.5, "emphasis", name="beat")]
    out = retarget(track, PRESETS["arkit"])
    assert [e.name for e in out.events] == ["beat"]
    assert any(c.name == "jawOpen" for c in out.channels)
