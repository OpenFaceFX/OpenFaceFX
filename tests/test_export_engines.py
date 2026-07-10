"""Engine exporters: #20 Live2D ``motion3.json`` and #21 Godot ``.tres``.

Golden-file fixtures are built from hand-authored tracks already in the target
parameter/shape space, so the expected bytes are fully determined by the
writers (no dependence on the coarticulation solver or retarget preset tables).

The Live2D ``Meta`` counts are re-derived from the emitted ``Curves`` with an
independent stride walk: a ``Meta`` that disagrees with the segment data is the
format's #1 gotcha (Cubism loaders trust the counts and read past the array),
so every multi-curve fixture asserts they agree.
"""

import json
import os
import re
import sys

import pytest

try:
    import openfacefx  # noqa: F401
except ImportError:
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from openfacefx.cli import main as cli_main
from openfacefx.curves import Channel, FaceTrack, Keyframe
from openfacefx.export_godot import write_godot_anim
from openfacefx.export_live2d import lipsync_param_ids, write_live2d_motion


def _track(fps, series):
    """A FaceTrack with one key per frame, so channel ``name``'s value at frame
    ``i`` is exactly ``series[name][i]``."""
    channels = [Channel(name, [Keyframe(round(i / fps, 4), v)
                               for i, v in enumerate(vals)])
                for name, vals in series.items()]
    return FaceTrack(fps=fps, channels=channels, target_set=None)


def _read(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


# sil + two vowels, one key per frame at fps=10. The default mouth-open collapse
# is the summed non-sil weight clamped to 0..1: [0, .5, 1, 1, .5].
TRACK = _track(10, {
    "sil": [1.0, 0.5, 0.0, 0.0, 0.5],
    "aa":  [0.0, 0.5, 1.0, 0.0, 0.0],
    "O":   [0.0, 0.0, 0.0, 1.0, 0.5],
})


# --- Live2D motion3.json ----------------------------------------------------

def _recount(curves):
    """(CurveCount, TotalSegmentCount, TotalPointCount) re-derived from Curves
    by the Cubism stride -- leading point, then each segment is an id plus its
    points (bezier id 1 = 3 points, else 1) -- independent of the writer."""
    total_seg = total_pt = 0
    for c in curves:
        seg = c["Segments"]
        total_pt += 1                       # the leading point
        i = 2
        while i < len(seg):
            pts = 3 if seg[i] == 1 else 1
            total_seg += 1
            total_pt += pts
            i += 1 + 2 * pts
    return len(curves), total_seg, total_pt


def test_live2d_default_mouth_open_collapse_exact(tmp_path):
    path = str(tmp_path / "m.motion3.json")
    write_live2d_motion(TRACK, path)
    assert _read(path) == (
        "{\n"
        '  "Version": 3,\n'
        '  "Meta": {\n'
        '    "Duration": 0.4,\n'
        '    "Fps": 10.0,\n'
        '    "Loop": false,\n'
        '    "AreBeziersRestricted": true,\n'
        '    "CurveCount": 1,\n'
        '    "TotalSegmentCount": 4,\n'
        '    "TotalPointCount": 5,\n'
        '    "UserDataCount": 0,\n'
        '    "TotalUserDataSize": 0\n'
        "  },\n"
        '  "Curves": [\n'
        "    {\n"
        '      "Target": "Parameter",\n'
        '      "Id": "ParamMouthOpenY",\n'
        '      "Segments": [0.0, 0.0, 0, 0.1, 0.5, 0, 0.2, 1.0, 0, 0.3, 1.0, 0, 0.4, 0.5]\n'
        "    }\n"
        "  ]\n"
        "}\n"
    )
    # decode the segment values: openness is the clamped sum of non-sil weights
    seg = json.loads(_read(path))["Curves"][0]["Segments"]
    assert seg[1::3] == [0.0, 0.5, 1.0, 1.0, 0.5]


def test_live2d_meta_counts_match_curves(tmp_path):
    # per-parameter mode with two visemes summed onto one Id (aa+sil -> ParamA)
    # gives a multi-curve file whose Meta must equal an independent recount.
    path = str(tmp_path / "p.motion3.json")
    write_live2d_motion(TRACK, path,
                        params={"aa": "ParamA", "sil": "ParamA", "O": "ParamO"})
    doc = json.loads(_read(path))
    meta = doc["Meta"]
    assert (meta["CurveCount"], meta["TotalSegmentCount"],
            meta["TotalPointCount"]) == _recount(doc["Curves"])
    # linear-only identity: points == curves (leading) + segments
    assert meta["TotalPointCount"] == meta["CurveCount"] + meta["TotalSegmentCount"]


def test_live2d_valid_json_linear_and_monotonic(tmp_path):
    path = str(tmp_path / "p.motion3.json")
    write_live2d_motion(TRACK, path, params={"aa": "ParamA", "O": "ParamO"})
    doc = json.loads(_read(path))            # valid JSON
    assert doc["Version"] == 3 and doc["Meta"]["Duration"] == 0.4
    for curve in doc["Curves"]:
        seg = curve["Segments"]
        assert all(sid == 0 for sid in seg[2::3])          # linear segments only
        times = [seg[0]] + seg[3::3]
        assert times == sorted(times) and len(set(times)) == len(times)


def test_live2d_params_select_present_targets(tmp_path):
    # PP is not in the track -> no curve; aa/O are -> one curve each, sil dropped.
    path = str(tmp_path / "p.motion3.json")
    write_live2d_motion(TRACK, path,
                        params={"aa": "ParamA", "O": "ParamO", "PP": "ParamPP"})
    doc = json.loads(_read(path))
    assert sorted(c["Id"] for c in doc["Curves"]) == ["ParamA", "ParamO"]
    assert all(c["Target"] == "Parameter" for c in doc["Curves"])


def test_live2d_custom_mouth_param(tmp_path):
    path = str(tmp_path / "m.motion3.json")
    write_live2d_motion(TRACK, path, mouth_param="ParamMouthOpen")
    assert [c["Id"] for c in json.loads(_read(path))["Curves"]] == ["ParamMouthOpen"]


def test_lipsync_param_ids_from_model3(tmp_path):
    model3 = tmp_path / "hiyori.model3.json"
    model3.write_text(json.dumps({"Version": 3, "Groups": [
        {"Target": "Parameter", "Name": "EyeBlink",
         "Ids": ["ParamEyeLOpen", "ParamEyeROpen"]},
        {"Target": "Parameter", "Name": "LipSync", "Ids": ["ParamMouthOpenY"]},
    ]}))
    assert lipsync_param_ids(str(model3)) == ["ParamMouthOpenY"]
    empty = tmp_path / "empty.model3.json"
    empty.write_text(json.dumps({"Groups": []}))
    assert lipsync_param_ids(str(empty)) == []


# --- Godot .tres ------------------------------------------------------------

def test_godot_value_track_exact(tmp_path):
    track = FaceTrack(fps=10, channels=[
        Channel("aa", [Keyframe(0.0, 0.0), Keyframe(0.2, 0.75)])])
    path = str(tmp_path / "a.tres")
    write_godot_anim(track, path, include_all_visemes=False)
    assert _read(path) == (
        '[gd_resource type="Animation" format=3]\n'
        "\n"
        "[resource]\n"
        'resource_name = "lipsync"\n'
        "length = 0.2\n"
        "loop_mode = 0\n"
        "step = 0.1\n"
        'tracks/0/type = "value"\n'
        "tracks/0/imported = false\n"
        "tracks/0/enabled = true\n"
        'tracks/0/path = NodePath("Head:blend_shapes/viseme_aa")\n'
        "tracks/0/interp = 1\n"
        "tracks/0/loop_wrap = true\n"
        "tracks/0/keys = {\n"
        '"times": PackedFloat32Array(0, 0.2),\n'
        '"transitions": PackedFloat32Array(1, 1),\n'
        '"update": 0,\n'
        '"values": [0.0, 0.75]\n'
        "}\n"
    )


def test_godot_equal_length_key_arrays(tmp_path):
    path = str(tmp_path / "a.tres")
    write_godot_anim(TRACK, path, include_all_visemes=False)
    text = _read(path)
    times = re.findall(r'"times": PackedFloat32Array\(([^)]*)\)', text)
    trans = re.findall(r'"transitions": PackedFloat32Array\(([^)]*)\)', text)
    values = re.findall(r'"values": \[([^\]]*)\]', text)
    assert len(times) == len(trans) == len(values) == 3     # sil, aa, O
    for t, tr, v in zip(times, trans, values):
        assert len(t.split(",")) == len(tr.split(",")) == len(v.split(","))


def test_godot_naming_presets(tmp_path):
    track = FaceTrack(fps=10, channels=[
        Channel("U", [Keyframe(0.0, 0.0), Keyframe(0.2, 1.0)])])
    o, v = str(tmp_path / "o.tres"), str(tmp_path / "v.tres")
    write_godot_anim(track, o, naming="oculus", include_all_visemes=False)
    write_godot_anim(track, v, naming="vrchat", include_all_visemes=False)
    assert 'NodePath("Head:blend_shapes/viseme_U")' in _read(o)
    assert 'NodePath("Head:blend_shapes/vrc.v_ou")' in _read(v)   # U -> ou


def test_godot_node_and_custom_names(tmp_path):
    track = FaceTrack(fps=10, channels=[
        Channel("aa", [Keyframe(0.0, 0.0), Keyframe(0.2, 1.0)])])
    path = str(tmp_path / "c.tres")
    write_godot_anim(track, path, node="Face", names={"aa": "jawOpen"},
                     include_all_visemes=False)
    assert 'NodePath("Face:blend_shapes/jawOpen")' in _read(path)


def test_godot_unknown_naming_preset_errors(tmp_path):
    track = FaceTrack(fps=10, channels=[Channel("aa", [Keyframe(0.0, 0.0)])])
    with pytest.raises(ValueError, match="unknown naming preset"):
        write_godot_anim(track, str(tmp_path / "x.tres"), naming="arkit")


def test_godot_include_all_visemes_clears_stale(tmp_path):
    track = FaceTrack(fps=10, channels=[
        Channel("aa", [Keyframe(0.0, 0.0), Keyframe(0.2, 1.0)])])
    path = str(tmp_path / "all.tres")
    write_godot_anim(track, path)            # include_all_visemes=True (default)
    text = _read(path)
    assert text.count('/type = "value"') == 15         # one per Oculus viseme
    assert 'NodePath("Head:blend_shapes/viseme_PP")' in text   # unfired -> const 0


# --- CLI end-to-end ---------------------------------------------------------

_TEXTGRID = '''File type = "ooTextFile"
Object class = "TextGrid"

xmin = 0
xmax = 0.6
tiers? <exists>
size = 1
item []:
    item [1]:
        class = "IntervalTier"
        name = "phones"
        xmin = 0
        xmax = 0.6
        intervals: size = 2
        intervals [1]:
            xmin = 0.0
            xmax = 0.3
            text = "HH"
        intervals [2]:
            xmin = 0.3
            xmax = 0.6
            text = "AH0"
'''


def test_cli_engine_formats_from_naive(tmp_path):
    l2 = str(tmp_path / "o.motion3.json")
    assert cli_main(["naive", "--text", "hello world this is a test",
                     "--duration", "1.6", "--fps", "24", "-o", l2]) == 0
    doc = json.loads(_read(l2))              # dispatched to Live2D, not native json
    assert doc["Version"] == 3
    assert (doc["Meta"]["CurveCount"], doc["Meta"]["TotalSegmentCount"],
            doc["Meta"]["TotalPointCount"]) == _recount(doc["Curves"])

    gd = str(tmp_path / "o.tres")
    assert cli_main(["naive", "--text", "hello world this is a test",
                     "--duration", "1.6", "--fps", "24", "-o", gd]) == 0
    assert _read(gd).startswith('[gd_resource type="Animation" format=3]\n')


def test_cli_live2d_params_file(tmp_path):
    mapping = tmp_path / "params.json"
    mapping.write_text(json.dumps({"aa": "ParamA", "E": "ParamE", "O": "ParamO"}))
    out = str(tmp_path / "p.motion3.json")
    assert cli_main(["naive", "--text", "hello world", "--duration", "1.2",
                     "-o", out, "--live2d-params", str(mapping)]) == 0
    ids = {c["Id"] for c in json.loads(_read(out))["Curves"]}
    assert ids and ids <= {"ParamA", "ParamE", "ParamO"}


def test_cli_live2d_model3_single_and_multi(tmp_path):
    single = tmp_path / "m.model3.json"
    single.write_text(json.dumps({"Groups": [
        {"Target": "Parameter", "Name": "LipSync", "Ids": ["ParamMouthOpen"]}]}))
    out = str(tmp_path / "m.motion3.json")
    assert cli_main(["naive", "--text", "hi there", "--duration", "1.0",
                     "-o", out, "--live2d-model3", str(single)]) == 0
    assert [c["Id"] for c in json.loads(_read(out))["Curves"]] == ["ParamMouthOpen"]

    multi = tmp_path / "multi.model3.json"
    multi.write_text(json.dumps({"Groups": [
        {"Name": "LipSync", "Ids": ["ParamA", "ParamI"]}]}))
    with pytest.raises(SystemExit, match="supply --live2d-params"):
        cli_main(["naive", "--text", "hi", "--duration", "0.5",
                  "-o", str(tmp_path / "x.motion3.json"),
                  "--live2d-model3", str(multi)])


def test_cli_godot_naming_and_node(tmp_path):
    out = str(tmp_path / "v.tres")
    assert cli_main(["naive", "--text", "hello world", "--duration", "1.2",
                     "-o", out, "--godot-naming", "vrchat",
                     "--godot-node", "Avatar"]) == 0
    assert 'NodePath("Avatar:blend_shapes/vrc.v_' in _read(out)


def test_cli_godot_bad_naming_rejected(tmp_path):
    with pytest.raises(SystemExit):                    # argparse choices
        cli_main(["naive", "--text", "hi", "--duration", "0.5",
                  "-o", str(tmp_path / "x.tres"), "--godot-naming", "arkit"])


def test_cli_retarget_rejected_for_engine_formats(tmp_path):
    for ext in (".motion3.json", ".tres"):
        with pytest.raises(SystemExit, match="does not apply"):
            cli_main(["naive", "--text", "hi", "--duration", "0.5",
                      "--retarget", "arkit", "-o", str(tmp_path / ("x" + ext))])


def test_cli_engine_formats_from_mfa(tmp_path):
    tg = tmp_path / "line.TextGrid"
    tg.write_text(_TEXTGRID)
    l2 = str(tmp_path / "m.motion3.json")
    assert cli_main(["mfa", "--textgrid", str(tg), "-o", l2]) == 0
    assert json.loads(_read(l2))["Version"] == 3
    gd = str(tmp_path / "m.tres")
    assert cli_main(["mfa", "--textgrid", str(tg), "-o", gd]) == 0
    assert _read(gd).startswith('[gd_resource type="Animation" format=3]\n')
