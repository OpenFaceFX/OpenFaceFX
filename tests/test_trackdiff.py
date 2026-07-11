"""A/B track drift report (openfacefx.trackdiff, issue #50).

Pins the acceptance: identical tracks give an all-zero report and exit 0; a track
perturbed by a known constant `d` on one channel reports that channel's
`max_abs == d` and `rms == d` (others zero); the delta magnitudes are symmetric
and added/removed channels swap; `--tolerance` gates the exit code exactly; and
the report is schema-stable, deterministic, and read-only (never writes; always
two tracks — distinct from `validate` and `diff-edits`).
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
from openfacefx.io_export import from_dict, to_dict, write_json
from openfacefx.pipeline import generate_from_alignment, naive_segments
from openfacefx.trackdiff import diff_tracks

TEXT, DUR = "hello brave new world", 2.3


def _track():
    return from_dict(to_dict(generate_from_alignment(naive_segments(TEXT, DUR),
                                                     fps=60.0)))


def _perturbed(track, channel, d):
    b = copy.deepcopy(track)
    for c in b.channels:
        if c.name == channel:
            for k in c.keys:
                k.value = k.value + d                 # constant offset, no clamp
    return b


# --------------------------------------------------------------------------- #
# 1. identical -> all-zero, ok                                                #
# --------------------------------------------------------------------------- #

def test_identical_tracks_report_zero_and_ok():
    a = _track()
    r = diff_tracks(a, copy.deepcopy(a))
    assert r["ok"] and r["problems"] == []
    assert all(d["max_abs"] == 0 and d["rms"] == 0 and d["mean_abs"] == 0
               for d in r["deltas"])
    assert r["channels"]["added"] == [] and r["channels"]["removed"] == []
    assert r["duration"]["delta"] == 0 and r["fps"]["delta"] == 0


# --------------------------------------------------------------------------- #
# 2. constant perturbation -> max_abs == rms == d                             #
# --------------------------------------------------------------------------- #

def test_constant_perturbation_reports_d_on_that_channel_only():
    a = _track()
    d, name = 0.1, a.channels[0].name
    r = diff_tracks(a, _perturbed(a, name, d))
    hit = next(x for x in r["deltas"] if x["channel"] == name)
    assert hit["max_abs"] == pytest.approx(d, abs=1e-6)
    assert hit["rms"] == pytest.approx(d, abs=1e-6)
    assert hit["mean_abs"] == pytest.approx(d, abs=1e-6)
    for x in r["deltas"]:
        if x["channel"] != name:
            assert x["max_abs"] == 0 and x["rms"] == 0


# --------------------------------------------------------------------------- #
# 3. symmetry                                                                  #
# --------------------------------------------------------------------------- #

def test_delta_magnitudes_symmetric_and_added_removed_swap():
    a = _track()
    b = copy.deepcopy(a)
    b.channels = b.channels[:-1]                       # b is missing one channel
    b = _perturbed(b, b.channels[0].name, 0.2)
    ab, ba = diff_tracks(a, b), diff_tracks(b, a)
    assert ab["channels"]["added"] == ba["channels"]["removed"]
    assert ab["channels"]["removed"] == ba["channels"]["added"]
    mag = lambda r: sorted((x["channel"], x["max_abs"], x["rms"], x["mean_abs"])
                           for x in r["deltas"])
    assert mag(ab) == mag(ba)                          # magnitudes agree
    # coverage/first/last-key deltas flip sign but agree in magnitude
    da = {x["channel"]: x for x in ab["deltas"]}
    db = {x["channel"]: x for x in ba["deltas"]}
    for n in da:
        assert da[n]["first_key_delta"] == pytest.approx(-db[n]["first_key_delta"])


# --------------------------------------------------------------------------- #
# 4. tolerance gates the verdict                                              #
# --------------------------------------------------------------------------- #

def test_tolerance_gates_ok_exactly():
    a = _track()
    b = _perturbed(a, a.channels[0].name, 0.1)
    assert diff_tracks(a, b, tolerance=0.0)["ok"] is False
    assert diff_tracks(a, b, tolerance=0.09)["ok"] is False
    assert diff_tracks(a, b, tolerance=0.1)["ok"] is True    # <= passes
    assert diff_tracks(a, b, tolerance=0.2)["ok"] is True


def test_added_removed_channels_fail_regardless_of_value_tolerance():
    a = _track()
    b = copy.deepcopy(a)
    b.channels = b.channels[:-1]
    assert diff_tracks(a, b, tolerance=1000.0)["ok"] is False  # structural drift


def test_duration_and_fps_deltas_reported():
    a = FaceTrack(60.0, [Channel("aa", [Keyframe(0.0, 0.0), Keyframe(1.0, 0.5)])],
                  None)
    b = FaceTrack(30.0, [Channel("aa", [Keyframe(0.0, 0.0), Keyframe(2.0, 0.5)])],
                  None)
    r = diff_tracks(a, b)
    assert r["fps"]["delta"] == 30.0 and not r["ok"]
    assert r["duration"]["delta"] == pytest.approx(-1.0)
    assert {"fps", "duration"} <= {p["metric"] for p in r["problems"]}


# --------------------------------------------------------------------------- #
# 5. events                                                                    #
# --------------------------------------------------------------------------- #

def test_event_add_remove_changed():
    from openfacefx.events import Event
    a = _track()
    a.events = [Event(t=0.5, type="gesture", name="nod", dur=0.2, payload={"i": 1})]
    b = copy.deepcopy(a)
    b.events = [Event(t=0.5, type="gesture", name="nod", dur=0.2, payload={"i": 2}),
                Event(t=1.5, type="emphasis", name="e", dur=0.0, payload={})]
    r = diff_tracks(a, b)
    assert r["events"]["changed"] == 1                # same (t,type,name), diff payload
    assert r["events"]["added"] == 1                  # the emphasis event
    assert not r["ok"]


# --------------------------------------------------------------------------- #
# 6. schema-stable, deterministic, read-only                                   #
# --------------------------------------------------------------------------- #

def test_report_schema_stable_and_deterministic():
    a = _track()
    b = _perturbed(a, a.channels[0].name, 0.1)
    r1 = diff_tracks(a, b)
    r2 = diff_tracks(copy.deepcopy(a), copy.deepcopy(b))
    assert set(r1) == {"format", "version", "tolerance", "ok", "duration", "fps",
                       "channels", "events", "deltas", "problems"}
    assert json.dumps(r1) == json.dumps(r2)           # byte-identical report
    keys = [(p["channel"], p["metric"]) for p in r1["problems"]]
    assert keys == sorted(keys)                        # sorted problem list
    assert all(set(p) == {"channel", "metric", "value"} for p in r1["problems"])


def test_cli_diff_exit_codes_and_read_only(tmp_path):
    a = str(tmp_path / "a.json")
    b = str(tmp_path / "b.json")
    write_json(_track(), a)
    write_json(_perturbed(_track(), _track().channels[0].name, 0.1), b)
    before = set(os.listdir(tmp_path))
    assert cli_main(["diff", a, a]) == 0               # identical -> 0
    assert cli_main(["diff", a, b]) == 1               # drift -> nonzero
    assert cli_main(["diff", a, b, "--tolerance", "0.2"]) == 0
    assert cli_main(["diff", a, b, "--json"]) == 1
    assert set(os.listdir(tmp_path)) == before          # never writes


def test_cli_diff_json_report(tmp_path, capsys):
    a = str(tmp_path / "a.json")
    write_json(_track(), a)
    assert cli_main(["diff", a, a, "--json"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["format"] == "openfacefx.diff" and out["ok"] is True
