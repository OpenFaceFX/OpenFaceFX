"""Track transforms (openfacefx.transforms, issue #48): retime / mirror / trim.

Pins the acceptance: retime to 2x doubles every key time AND event time AND the
track duration with channel values unchanged; retime-to-`--wav` matches
`wav_duration` within one frame; **mirror ∘ mirror == identity, byte-identical**,
and one mirror swaps L/R channels + negates the signed lateral pose channels while
leaving centered channels untouched; trim keeps only in-window keys, rebases to 0,
drops/reclamps out-of-window events, and handles an empty window gracefully.
Everything deterministic.
"""

import copy
import json
import os
import sys
import wave

import pytest

try:
    import openfacefx  # noqa: F401  (installed wheel wins; see test_core)
except ImportError:
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from openfacefx.cli import main as cli_main
from openfacefx.curves import Channel, FaceTrack, Keyframe
from openfacefx.events import Event
from openfacefx.gestures import GestureParams
from openfacefx.io_export import from_dict, read_json, to_dict, write_json
from openfacefx.pipeline import generate_from_alignment, naive_segments
from openfacefx.transforms import (mirror, retime, retime_to_duration, trim,
                                   MIRROR_NEGATE, MIRROR_PAIRS)

TEXT, DUR = "hello brave new world", 2.3


def _track(fps=60.0, with_events=False, gestures=False):
    gp = GestureParams(seed=1) if gestures else None
    t = generate_from_alignment(naive_segments(TEXT, DUR), fps=fps, gestures=gp)
    if with_events:
        t.events = [Event(t=0.5, type="gesture", name="nod", dur=0.2, payload={}),
                    Event(t=1.5, type="emphasis", name="e", dur=0.0, payload={})]
    # round-trip through the JSON form (4-dp times), exactly as `transform` loads it
    return from_dict(to_dict(t))


# --------------------------------------------------------------------------- #
# 1. retime / stretch                                                          #
# --------------------------------------------------------------------------- #

def test_retime_2x_doubles_times_and_duration_values_unchanged():
    t = _track(with_events=True)
    r = retime(t, 2.0)
    orig = {c.name: c for c in t.channels}
    for c in r.channels:
        o = orig[c.name]
        assert len(c.keys) == len(o.keys)                     # keys preserved
        for k, ok in zip(c.keys, o.keys):
            assert k.time == 2.0 * ok.time                    # every time doubled
            assert k.value == ok.value                        # values unchanged
    assert r.duration == pytest.approx(2.0 * t.duration)
    assert [e.t for e in r.events] == [1.0, 3.0]              # event times doubled


def test_retime_anchor_pins_a_pivot():
    t = FaceTrack(60.0, [Channel("aa", [Keyframe(1.0, 0.2), Keyframe(2.0, 0.8)])],
                  None)
    r = retime(t, 2.0, anchor=1.0)                            # t=1 stays fixed
    times = [k.time for k in r.channels[0].keys]
    assert times == [1.0, 3.0]


def test_retime_to_duration_and_wav_within_one_frame(tmp_path):
    t = _track()
    r = retime_to_duration(t, 4.6)
    assert r.duration == pytest.approx(4.6, abs=1e-4)
    # a real WAV of a known length, via stdlib wave
    wav = str(tmp_path / "clip.wav")
    rate, seconds = 16000, 1.75
    with wave.open(wav, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"\x00\x00" * int(rate * seconds))
    tj = str(tmp_path / "t.json")
    write_json(t, tj)
    out = str(tmp_path / "retimed.json")
    assert cli_main(["transform", tj, "--wav", wav, "-o", out]) == 0
    assert abs(read_json(out).duration - seconds) <= 1.0 / t.fps   # within a frame


def test_retime_rejects_nonpositive_factor():
    t = _track()
    for bad in (0.0, -1.0, float("nan"), float("inf")):
        with pytest.raises(ValueError):
            retime(t, bad)


def test_retime_zero_duration_track_errors():
    with pytest.raises(ValueError):
        retime_to_duration(FaceTrack(60.0, [], None), 2.0)


# --------------------------------------------------------------------------- #
# 2. mirror -- the critical invariant                                          #
# --------------------------------------------------------------------------- #

def test_mirror_twice_is_byte_identical():
    for kw in ({}, {"gestures": True}, {"with_events": True}):
        t = _track(**kw)
        assert to_dict(mirror(mirror(t))) == to_dict(t)


def test_mirror_twice_byte_identical_with_lateral_pose_and_lr_pairs():
    t = FaceTrack(60.0, [
        Channel("mouthSmileLeft", [Keyframe(0.0, 0.3), Keyframe(1.0, 0.7)]),
        Channel("mouthSmileRight", [Keyframe(0.0, 0.1), Keyframe(1.0, 0.5)]),
        Channel("headYaw", [Keyframe(0.0, 0.0), Keyframe(1.0, 12.0)]),  # incl 0.0
        Channel("headPitch", [Keyframe(0.0, 3.0), Keyframe(1.0, -4.0)]),
        Channel("jawOpen", [Keyframe(0.0, 0.2), Keyframe(1.0, 0.9)]),
    ], ["mouthSmileLeft", "mouthSmileRight", "headYaw", "headPitch", "jawOpen"])
    assert to_dict(mirror(mirror(t))) == to_dict(t)


def test_one_mirror_swaps_lr_negates_lateral_leaves_centered():
    t = FaceTrack(60.0, [
        Channel("mouthSmileLeft", [Keyframe(0.0, 0.3), Keyframe(1.0, 0.7)]),
        Channel("headYaw", [Keyframe(0.0, 5.0), Keyframe(1.0, -8.0)]),
        Channel("headPitch", [Keyframe(0.0, 3.0), Keyframe(1.0, -4.0)]),
        Channel("jawOpen", [Keyframe(0.0, 0.2), Keyframe(1.0, 0.9)]),
    ], None)
    m = {c.name: c for c in mirror(t).channels}
    assert "mouthSmileRight" in m and "mouthSmileLeft" not in m     # L -> R
    assert [k.value for k in m["headYaw"].keys] == [-5.0, 8.0]      # lateral negated
    assert [k.value for k in m["headPitch"].keys] == [3.0, -4.0]    # pitch untouched
    assert [k.value for k in m["jawOpen"].keys] == [0.2, 0.9]       # centered untouched


def test_mirror_pairs_are_bidirectional_and_negate_is_lateral_only():
    for left, right in MIRROR_PAIRS:
        assert left != right
    assert MIRROR_NEGATE == {"headYaw", "headRoll", "eyeYaw"}       # not headPitch


def test_cli_mirror_twice_is_byte_identical(tmp_path):
    tj = str(tmp_path / "t.json")
    cli_main(["naive", "--text", TEXT, "--duration", str(DUR), "--gestures",
              "-o", tj])
    m1, m2 = str(tmp_path / "m1.json"), str(tmp_path / "m2.json")
    assert cli_main(["transform", tj, "--mirror", "-o", m1]) == 0
    assert cli_main(["transform", m1, "--mirror", "-o", m2]) == 0
    assert open(tj, "rb").read() == open(m2, "rb").read()


# --------------------------------------------------------------------------- #
# 3. trim / slice                                                              #
# --------------------------------------------------------------------------- #

def test_trim_keeps_window_rebases_and_clamps_events():
    t = _track(with_events=True)
    s = trim(t, 0.5, 1.5)
    times = [k.time for c in s.channels for k in c.keys]
    assert times and min(times) >= -1e-9 and max(times) <= 1.0 + 1e-9
    # the event at 0.5 survives rebased to 0.0; the one at 1.5 to 1.0
    ev = sorted(e.t for e in s.events)
    assert ev == [0.0, 1.0]


def test_trim_reclamps_event_duration_to_window():
    t = FaceTrack(60.0, [Channel("aa", [Keyframe(0.0, 0.1), Keyframe(2.0, 0.9)])],
                  None)
    t.events = [Event(t=0.4, type="gesture", name="g", dur=1.0, payload={})]
    s = trim(t, 0.0, 0.8)                       # event would run to 1.4, past 0.8
    assert s.events[0].t == pytest.approx(0.4)
    assert s.events[0].dur == pytest.approx(0.4)   # clamped to the window end


def test_trim_empty_window_is_graceful():
    t = _track(with_events=True)
    empty = trim(t, 5.0, 6.0)                    # entirely past the clip
    assert empty.channels == [] and empty.events == []
    assert to_dict(empty)                        # still serialises, no crash


def test_trim_drops_out_of_window_keys():
    t = FaceTrack(60.0, [Channel("aa", [Keyframe(0.0, 0.1), Keyframe(0.5, 0.5),
                                        Keyframe(1.0, 0.9)])], None)
    s = trim(t, 0.4, 0.6)
    assert [(round(k.time, 4), k.value) for k in s.channels[0].keys] == [(0.1, 0.5)]


# --------------------------------------------------------------------------- #
# 4. determinism + CLI surface                                                 #
# --------------------------------------------------------------------------- #

def test_transforms_are_deterministic():
    t = _track(with_events=True, gestures=True)
    assert to_dict(retime(t, 1.7)) == to_dict(retime(copy.deepcopy(t), 1.7))
    assert to_dict(mirror(t)) == to_dict(mirror(copy.deepcopy(t)))
    assert to_dict(trim(t, 0.3, 1.2)) == to_dict(trim(copy.deepcopy(t), 0.3, 1.2))


def test_cli_transform_compose_and_noop(tmp_path):
    tj = str(tmp_path / "t.json")
    cli_main(["naive", "--text", TEXT, "--duration", str(DUR), "-o", tj])
    out = str(tmp_path / "c.json")
    assert cli_main(["transform", tj, "--retime", "1.5", "--mirror",
                     "--trim", "0.2", "2.0", "-o", out]) == 0
    assert os.path.getsize(out) > 0
    with pytest.raises(SystemExit):             # no transform selected
        cli_main(["transform", tj, "-o", str(tmp_path / "x.json")])
