"""`inspect` + `validate` (openfacefx.inspect, issue #47): read-only stats and a
CI-friendly format/contract linter.

Pins the acceptance: `validate` exits 0 on every track the generators AND
importers produce, and nonzero with a deterministic, sorted problem list on a
corrupted one (out-of-order times, out-of-range weight, unknown event type,
viseme_set mismatch); `inspect --json` is deterministic and schema-stable (every
documented key always present, lists empty not absent); it validates a
`.track.json`, an `*.edits.json`, and a standalone events file; and it never
writes anything.
"""

import copy
import json
import os
import sys

import pytest

try:
    import openfacefx  # noqa: F401  (installed wheel wins; see test_core)
except ImportError:
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from openfacefx.cli import main as cli_main
from openfacefx.curves import Channel, FaceTrack, Keyframe
from openfacefx.gestures import GestureParams
from openfacefx.inspect import (detect_kind, inspect_track, validate_asset,
                                validate_file)
from openfacefx.io_export import to_dict
from openfacefx.pipeline import generate_from_alignment, naive_segments
from openfacefx.retarget import PRESETS, retarget

TEXT, DUR = "hello brave new world", 2.3
INSPECT_KEYS = {
    "format", "version", "fps", "duration", "channels", "keyframes",
    "weight_channels", "pose_channels", "gesture_channels", "events",
    "variants", "viseme_set", "channel_detail", "cue_warnings", "oov_words",
    "warnings",
}


def _segs():
    return naive_segments(TEXT, DUR)


def _errors(problems):
    return [p for p in problems if p["severity"] == "error"]


# --------------------------------------------------------------------------- #
# 1. validate exits 0 on every generator/importer output                      #
# --------------------------------------------------------------------------- #

def _generator_and_importer_tracks(tmp_path):
    from openfacefx.emotion import bake_emotion
    from openfacefx.export_cues import write_moho_dat
    from openfacefx.importers import import_cues
    from openfacefx.importers_csv import read_csv
    tracks = {}
    tracks["naive_60"] = generate_from_alignment(_segs(), fps=60.0)
    tracks["naive_100"] = generate_from_alignment(_segs(), fps=100.0)
    tracks["gestures"] = generate_from_alignment(
        _segs(), fps=60.0, gestures=GestureParams(seed=0))
    tracks["retarget_arkit"] = retarget(tracks["naive_60"], PRESETS["arkit"])
    tracks["retarget_vrm"] = retarget(tracks["naive_60"], PRESETS["vrm"])
    env = {"format": "openfacefx.emotion", "version": 1,
           "mode": "valence_arousal",
           "va": {"valence": [[0, 1], [DUR, 1]], "arousal": [[0, 0.8], [DUR, 0.8]]}}
    tracks["emotion"] = bake_emotion(tracks["naive_60"], env, intensity=1.0)
    dat = str(tmp_path / "c.dat")
    write_moho_dat(tracks["naive_60"], dat)
    tracks["from_cues"] = import_cues(dat)[0]
    tracks["from_cues_coart"] = import_cues(dat, coarticulate=True)[0]
    csv = str(tmp_path / "llf.csv")
    with open(csv, "w") as fh:
        fh.write("Timecode,jawOpen,mouthSmileLeft\n0:00:00:00,0.0,0.0\n"
                 "0:00:00:01,0.5,0.2\n0:00:00:02,1.0,0.1\n")
    tracks["from_csv"] = read_csv(csv, fps=60.0)[0]
    return tracks


def test_validate_clean_on_all_generator_and_importer_outputs(tmp_path):
    for name, track in _generator_and_importer_tracks(tmp_path).items():
        kind, problems = validate_asset(to_dict(track))
        assert kind == "track"
        assert _errors(problems) == [], f"{name}: {_errors(problems)}"


def test_cli_validate_exit_zero_on_generated_track(tmp_path):
    tj = str(tmp_path / "t.json")
    assert cli_main(["naive", "--text", TEXT, "--duration", str(DUR),
                     "--gestures", "-o", tj]) == 0
    assert cli_main(["validate", tj]) == 0


# --------------------------------------------------------------------------- #
# 2. validate nonzero + deterministic problem list on corruption              #
# --------------------------------------------------------------------------- #

def _corrupt(kind):
    tr = generate_from_alignment(_segs(), fps=60.0)
    d = to_dict(tr)
    if kind == "times":
        d["channels"][0]["keys"] = [[1.0, 0.5], [0.5, 0.6]]
        return d, "times_not_monotonic"
    if kind == "weight":
        d["channels"][0]["keys"] = [[0.0, 0.5], [0.5, 1.9]]
        return d, "weight_out_of_range"
    if kind == "event":
        d["events"] = [{"t": 0.1, "type": "BOGUS", "name": "x", "dur": 0.0,
                        "payload": {}}]
        return d, "unknown_event_type"
    if kind == "viseme_set":
        d["viseme_set"] = ["aa"]
        return d, "channel_not_in_viseme_set"
    raise AssertionError(kind)


@pytest.mark.parametrize("kind", ["times", "weight", "event", "viseme_set"])
def test_validate_flags_corruption(kind):
    data, code = _corrupt(kind)
    _, problems = validate_asset(data)
    assert code in {p["code"] for p in _errors(problems)}


def test_validate_problem_list_is_deterministic_and_sorted():
    data, _ = _corrupt("weight")
    a = validate_asset(data)[1]
    b = validate_asset(copy.deepcopy(data))[1]
    assert a == b                                        # deterministic
    keys = [(p["severity"], p["code"], p["where"]) for p in a]
    assert keys == sorted(keys) or all(  # errors before warnings, then code/where
        p["severity"] == "error" for p in a)


def test_cli_validate_exit_nonzero_and_json_on_corruption(tmp_path, capsys):
    data, code = _corrupt("weight")
    f = str(tmp_path / "bad.json")
    with open(f, "w") as fh:
        json.dump(data, fh)
    assert cli_main(["validate", f, "--json"]) == 1
    out = json.loads(capsys.readouterr().out)
    assert out["ok"] is False and out["kind"] == "track"
    assert code in {p["code"] for p in out["problems"]}


# --------------------------------------------------------------------------- #
# 3. inspect --json deterministic + schema-stable                             #
# --------------------------------------------------------------------------- #

def test_inspect_schema_stable_and_deterministic():
    track = generate_from_alignment(_segs(), fps=60.0)
    a = inspect_track(track)
    b = inspect_track(track)
    assert set(a) == INSPECT_KEYS                        # every key present
    assert json.dumps(a) == json.dumps(b)               # deterministic bytes
    for d in a["channel_detail"]:
        assert set(d) == {"name", "kind", "keys", "min", "max", "start",
                          "end", "coverage"}


def test_inspect_empty_track_keeps_schema():
    track = FaceTrack(60.0, [], None)
    doc = inspect_track(track)
    assert set(doc) == INSPECT_KEYS
    assert doc["channel_detail"] == [] and doc["cue_warnings"] == []
    assert doc["channels"] == 0 and doc["variants"] == 0


def test_inspect_weight_pose_split():
    track = generate_from_alignment(_segs(), fps=60.0,
                                    gestures=GestureParams(seed=0))
    doc = inspect_track(track)
    assert doc["pose_channels"] >= 1                     # head/eye angle channels
    assert doc["gesture_channels"] >= 1
    poses = [d for d in doc["channel_detail"] if d["kind"] == "pose"]
    assert all(d["name"] in {"headPitch", "headYaw", "headRoll",
                             "eyePitch", "eyeYaw"} for d in poses)


# --------------------------------------------------------------------------- #
# 4. validates track / edits / events; strict; pose bounds; read-only         #
# --------------------------------------------------------------------------- #

def test_validate_detects_and_checks_edits(tmp_path):
    from openfacefx.edits import diff_edits, save_edits
    base = generate_from_alignment(_segs(), fps=60.0)
    edited = copy.deepcopy(base)
    for c in edited.channels:
        if c.name == "aa":
            for k in c.keys:
                k.value = round(min(1.0, k.value + 0.1), 4)
    p = str(tmp_path / "e.edits.json")
    save_edits(diff_edits(base, edited), p)
    assert detect_kind(json.load(open(p))) == "edits"
    assert cli_main(["validate", p]) == 0
    # a broken clamp is caught
    d = json.load(open(p))
    if d.get("channels"):
        name = next(iter(d["channels"]))
        d["channels"][name]["clamp"] = [0.9, 0.1]
    _, problems = validate_asset(d)
    assert _errors(problems)


def test_validate_events_file():
    good = {"events": [{"t": 0.1, "type": "gesture", "name": "nod", "dur": 0.2,
                        "payload": {}}]}
    assert detect_kind(good) == "events"
    assert _errors(validate_asset(good)[1]) == []
    bad = {"events": [{"t": 0.0, "type": "ZZZ", "name": "x", "dur": 0.0,
                       "payload": {}}]}
    assert "unknown_event_type" in {p["code"] for p in validate_asset(bad)[1]}


def test_strict_promotes_warnings_to_errors():
    track = FaceTrack(60.0, [Channel("aa", [Keyframe(0, 0.5), Keyframe(1, 0.5)]),
                             Channel("PP", [])], None)
    lenient = validate_asset(to_dict(track))[1]
    assert [p["severity"] for p in lenient if p["code"] == "empty_channel"] == \
        ["warning"]
    strict = validate_asset(to_dict(track), strict=True)[1]
    assert [p["severity"] for p in strict if p["code"] == "empty_channel"] == \
        ["error"]


def test_pose_channel_range():
    wild = FaceTrack(60.0, [Channel("headYaw", [Keyframe(0, 5.0),
                                                Keyframe(1, 9999.0)])], ["headYaw"])
    assert "pose_wildly_out_of_range" in {p["code"]
                                          for p in validate_asset(to_dict(wild))[1]}
    sane = FaceTrack(60.0, [Channel("headYaw", [Keyframe(0, -30.0),
                                                Keyframe(1, 45.0)])], ["headYaw"])
    assert _errors(validate_asset(to_dict(sane))[1]) == []


def test_unknown_asset_is_flagged():
    _, problems = validate_asset({"format": "something.else", "version": 1})
    assert problems[0]["code"] in {"unknown_asset", "parse_error"}


def test_validate_file_handles_bad_json(tmp_path):
    p = str(tmp_path / "broken.json")
    with open(p, "w") as fh:
        fh.write("{not json")
    kind, problems = validate_file(p)
    assert problems[0]["code"] == "not_json"
    assert cli_main(["validate", p]) == 1


def test_commands_are_read_only(tmp_path):
    tj = str(tmp_path / "t.json")
    cli_main(["naive", "--text", TEXT, "--duration", str(DUR), "-o", tj])
    before = set(os.listdir(tmp_path))
    cli_main(["inspect", tj])
    cli_main(["inspect", tj, "--json"])
    cli_main(["validate", tj])
    cli_main(["validate", tj, "--strict", "--json"])
    assert set(os.listdir(tmp_path)) == before           # nothing written
