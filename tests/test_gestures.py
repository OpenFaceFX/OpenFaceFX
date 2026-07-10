"""Procedural gesture layer (`openfacefx.gestures`, issue #5).

The layer is deterministic and OPT-IN: these tests pin the physiological
behaviour (blink rate, fast-close/slow-open lid, brows on energy accents, blinks
snapping to pauses/stress), the value ranges, the byte-identity of a gesture-less
track, and cross-version reproducibility (the golden test below must pass under
both Python 3.9 and 3.12 unchanged).
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
from openfacefx.gestures import (generate_gestures, add_gestures_to_track,
                                  GestureParams, GESTURE_CHANNELS)
from openfacefx.alignment import PhonemeSegment
from openfacefx.pipeline import generate_from_alignment, naive_segments
from openfacefx.curves import FaceTrack, Channel, Keyframe
from openfacefx.io_export import to_dict
from openfacefx.visemes import VISEMES

ROOT = os.path.join(os.path.dirname(__file__), "..")
VOICE = os.path.join(ROOT, "examples", "voice.wav")


def _chan(channels, name):
    hits = [c for c in channels if c.name == name]
    return hits[0] if hits else None


def _apexes(channels, name="blink_L"):
    """Blink apex times = keyframes at full closure."""
    ch = _chan(channels, name)
    return [round(k.time, 4) for k in ch.keys if k.value > 0.999] if ch else []


def _dump(channels):
    return [(c.name, [(round(k.time, 4), k.value) for k in c.keys]) for c in channels]


def _pulse_env(duration=6.0, fps=60.0, centre=3.0, width=0.15, amp=0.9):
    """A flat-silence envelope with a single Gaussian energy accent."""
    t = np.arange(0.0, duration, 1.0 / fps)
    return t, amp * np.exp(-((t - centre) ** 2) / (2 * width ** 2))


# --- determinism ------------------------------------------------------------

def test_same_seed_is_identical():
    a = generate_gestures(120.0, 60.0, params=GestureParams(seed=0))
    b = generate_gestures(120.0, 60.0, params=GestureParams(seed=0))
    assert _dump(a) == _dump(b)


def test_different_seed_changes_blink_times():
    a = _apexes(generate_gestures(120.0, 60.0, params=GestureParams(seed=0)))
    b = _apexes(generate_gestures(120.0, 60.0, params=GestureParams(seed=7)))
    assert a and b and a != b


def test_cross_version_golden():
    """Hard-coded expected keyframes; PCG64 is platform-independent, so this must
    reproduce bit-for-bit on Python 3.9 and 3.12 (the interpreter-parity check)."""
    ch = generate_gestures(60.0, 60.0, params=GestureParams(seed=0))
    assert [c.name for c in ch] == ["blink_L", "blink_R", "headPitch",
                                    "headYaw", "headRoll", "eyeYaw", "eyePitch"]
    assert _apexes(ch)[:3] == [10.0, 13.5305, 16.2642]
    assert _chan(ch, "headYaw").keys[0].value == 1.6209
    # eyeYaw is a step curve: hold at 0, then snap to the first fixation.
    ey = _chan(ch, "eyeYaw").keys
    assert (round(ey[2].time, 4), ey[2].value) == (0.74, -0.6465)


# --- blink physiology -------------------------------------------------------

def test_blink_rate_in_human_band():
    # ~15 blinks/min relaxed baseline; assert a generous human band over 5 min.
    ch = generate_gestures(300.0, 60.0, params=GestureParams(seed=0))
    per_min = len(_apexes(ch)) / 5.0
    assert 10.0 <= per_min <= 24.0, per_min


def test_blink_curve_is_fast_close_slow_open():
    ch = generate_gestures(60.0, 60.0, params=GestureParams(seed=0))
    keys = _chan(ch, "blink_L").keys
    apex_t = next(k.time for k in keys if k.value > 0.999)
    onset = max(k.time for k in keys if k.time < apex_t and k.value < 1e-6)
    offset = min(k.time for k in keys if k.time > apex_t and k.value < 1e-6)
    close, open_ = apex_t - onset, offset - apex_t
    assert close < open_                       # down-phase shorter than up-phase
    assert abs(close - 0.05) < 0.02 and abs(open_ - 0.10) < 0.02


def test_second_eye_trails_the_first():
    ch = generate_gestures(60.0, 60.0, params=GestureParams(seed=0))
    left, right = _apexes(ch, "blink_L"), _apexes(ch, "blink_R")
    assert left and right
    assert all(abs((r - l) - 0.008) < 1e-3 for l, r in zip(left, right))


# --- eyebrow accents --------------------------------------------------------

def test_brows_fire_on_energy_peak():
    t, env = _pulse_env()
    ch = generate_gestures(6.0, 60.0, t, env, params=GestureParams(seed=0))
    brow = _chan(ch, "browUp")
    assert brow is not None
    peak_key = max(brow.keys, key=lambda k: k.value)
    assert peak_key.value > 0.3                 # a real flash
    assert abs(peak_key.time - 3.0) < 0.15      # aligned to the accent


def test_no_brows_in_silence():
    t = np.arange(0.0, 6.0, 1.0 / 60.0)
    ch = generate_gestures(6.0, 60.0, t, np.zeros_like(t),
                           params=GestureParams(seed=0))
    assert _chan(ch, "browUp") is None          # nothing to accent


# --- speech coupling: blinks snap to pauses / stress ------------------------

def test_blinks_snap_to_pauses():
    # Dense sil segments: several blink candidates land within the snap window
    # and are pulled exactly onto a pause centre.
    segs = [PhonemeSegment("sil", x - 0.05, x + 0.05)
            for x in np.arange(1.0, 60.0, 1.0)]
    ch = generate_gestures(60.0, 60.0, segments=segs, params=GestureParams(seed=0))
    pauses = {round(float(x), 4) for x in np.arange(1.0, 60.0, 1.0)}
    snapped = [a for a in _apexes(ch) if a in pauses]
    assert len(snapped) >= 1


def test_blinks_snap_to_stress_when_no_pause():
    # Only stress snapping active (no pauses): blinks land on a stressed-vowel
    # centre. Stressed vowels every ~1s at x.x5 centres.
    segs = [PhonemeSegment("AA1", x - 0.15, x + 0.15)
            for x in np.arange(1.0, 60.0, 1.0)]
    p = GestureParams(seed=0, blink_snap_pause=False)
    ch = generate_gestures(60.0, 60.0, segments=segs, params=p)
    centres = {round(float(x), 4) for x in np.arange(1.0, 60.0, 1.0)}
    assert any(a in centres for a in _apexes(ch))


def test_stress_from_energy_when_no_digits():
    # IPA vowels carry no ARPABET stress digit, so stress falls back to the
    # energy+duration z-score. The long, loud vowel should win and nod the head.
    segs = [PhonemeSegment("sil", 0.0, 0.4),
            PhonemeSegment("i", 0.4, 0.5),     # short, quiet
            PhonemeSegment("a", 0.5, 1.1),     # long, loud -> stressed
            PhonemeSegment("i", 1.1, 1.2),     # short, quiet
            PhonemeSegment("sil", 1.2, 1.6)]
    t = np.arange(0.0, 1.6, 1.0 / 60.0)
    env = 0.9 * np.exp(-((t - 0.8) ** 2) / (2 * 0.15 ** 2))   # accent on the "a"
    p = GestureParams(seed=0, head_ambient=False, gaze_enable=False,
                      blink_enable=False, brow_enable=False)
    ch = generate_gestures(1.6, 120.0, t, env, segs, p)
    pitch = _chan(ch, "headPitch")
    assert pitch is not None and max(k.value for k in pitch.keys) > 1.0
    apex_t = max(pitch.keys, key=lambda k: k.value).time
    assert 0.7 < apex_t < 1.3                   # nod centred on the loud vowel


def test_head_nod_fires_on_stress():
    # A single stressed vowel produces a downward (positive) pitch excursion.
    segs = [PhonemeSegment("sil", 0.0, 0.5), PhonemeSegment("AA1", 0.5, 1.0),
            PhonemeSegment("sil", 1.0, 2.0)]
    p = GestureParams(seed=0, head_ambient=False, gaze_enable=False,
                      blink_enable=False)
    ch = generate_gestures(2.0, 120.0, segments=segs, params=p)
    pitch = _chan(ch, "headPitch")
    assert pitch is not None
    assert max(k.value for k in pitch.keys) > 1.0   # nods down a few degrees


# --- value ranges -----------------------------------------------------------

def test_blink_brow_values_in_unit_range():
    t, env = _pulse_env(duration=120.0, centre=60.0)
    segs = naive_segments("the quick brown fox jumps over the lazy dog", 120.0)
    ch = generate_gestures(120.0, 60.0, t, env, segs, GestureParams(seed=1))
    for name in ("blink_L", "blink_R", "browUp"):
        c = _chan(ch, name)
        if c:
            assert all(0.0 <= k.value <= 1.0 for k in c.keys), name


def test_head_eye_within_degree_bounds():
    segs = naive_segments("the quick brown fox jumps over the lazy dog", 60.0)
    p = GestureParams(seed=2)
    ch = generate_gestures(60.0, 60.0, segments=segs, params=p)
    s = sum(1.0 / (k + 1) for k in range(len(p.head_ambient_freqs)))
    bounds = {
        "headPitch": p.head_ambient_deg * s + p.head_pitch_deg,
        "headYaw": p.head_ambient_deg * s,
        "headRoll": p.head_ambient_deg * s,
        "eyeYaw": p.gaze_yaw_deg,
        "eyePitch": p.gaze_pitch_deg,
    }
    for name, bound in bounds.items():
        c = _chan(ch, name)
        if c:
            assert max(abs(k.value) for k in c.keys) <= bound + 1e-6, name


def test_normalized_mode_stays_in_unit_range():
    segs = naive_segments("the quick brown fox jumps over the lazy dog", 60.0)
    ch = generate_gestures(60.0, 60.0, segments=segs,
                           params=GestureParams(seed=0, head_eye_in_degrees=False))
    for name in ("headPitch", "headYaw", "headRoll", "eyeYaw", "eyePitch"):
        c = _chan(ch, name)
        if c:
            assert all(-1.0 <= k.value <= 1.0 for k in c.keys), name


# --- opt-in / byte-identity -------------------------------------------------

def test_gestures_off_is_byte_identical():
    segs = naive_segments("the quick brown fox jumps over the lazy dog", 3.0)
    baseline = to_dict(generate_from_alignment(segs))
    # Default (no gestures) and explicit gestures=None are the same track.
    assert to_dict(generate_from_alignment(segs, gestures=None)) == baseline
    # Turning gestures on only APPENDS: the mouth channels are bit-for-bit equal.
    withg = to_dict(generate_from_alignment(segs, gestures=GestureParams(seed=0)))
    n = len(baseline["channels"])
    assert withg["channels"][:n] == baseline["channels"]
    assert len(withg["channels"]) > n
    assert baseline["viseme_set"] == withg["viseme_set"][:len(baseline["viseme_set"])]


def test_never_firing_channels_are_dropped():
    # A sub-second clip is too short for a blink (~15/min) -> no blink channel.
    ch = generate_gestures(0.5, 60.0, params=GestureParams(seed=0))
    assert _chan(ch, "blink_L") is None
    # Disabling every layer yields nothing at all.
    off = GestureParams(seed=0, blink_enable=False, brow_enable=False,
                        head_ambient=False, head_nod_on_stress=False,
                        gaze_enable=False)
    assert generate_gestures(60.0, 60.0, params=off) == []


# --- track integration ------------------------------------------------------

def test_add_gestures_to_track_extends_target_set():
    segs = naive_segments("hello world", 30.0)
    track = generate_from_alignment(segs)
    mouth_before = list(track.channels)
    add_gestures_to_track(track, 30.0, None, None, segs, GestureParams(seed=0))
    added = [c.name for c in track.channels if c.name in GESTURE_CHANNELS]
    assert added                                   # gestures were appended
    assert track.channels[:len(mouth_before)] == mouth_before   # mouth untouched
    for name in added:
        assert name in track.target_set            # first-class vocabulary


def test_gesture_names_disjoint_from_visemes():
    assert GESTURE_CHANNELS.isdisjoint(set(VISEMES))


# --- exporter / retarget safety ---------------------------------------------

def test_retarget_preserves_gesture_channels(tmp_path):
    out = str(tmp_path / "arkit.json")
    rc = cli_main(["naive", "--text", "hello world this is a test",
                   "--duration", "30", "--gestures", "-o", out,
                   "--retarget", "arkit"])
    assert rc == 0
    d = json.load(open(out))
    names = {c["name"] for c in d["channels"]}
    assert "jawOpen" in names                      # visemes were retargeted...
    assert "headPitch" in names                    # ...gestures passed through
    assert "headPitch" in d["viseme_set"]


def test_cue_export_ignores_gestures(tmp_path):
    # A gesture-augmented track must still cue-export (mouth-only): the gesture
    # channels are dropped, never treated as a winning "viseme" shape.
    out = str(tmp_path / "cues.tsv")
    rc = cli_main(["naive", "--text", "hello world this is a test",
                   "--duration", "30", "--gestures", "-o", out])
    assert rc == 0
    lines = open(out, encoding="utf-8").read().splitlines()
    shapes = {ln.split("\t")[1] for ln in lines if "\t" in ln}
    assert shapes and shapes <= set("ABCDEFGHX")   # only Rhubarb mouth shapes
    assert not (shapes & GESTURE_CHANNELS)


# --- CLI end-to-end ---------------------------------------------------------

def test_cli_naive_gestures_json(tmp_path):
    out = str(tmp_path / "g.json")
    rc = cli_main(["naive", "--text", "hello world this is a longer test line",
                   "--duration", "30", "--gestures", "--gesture-seed", "1",
                   "-o", out])
    assert rc == 0
    d = json.load(open(out))
    names = {c["name"] for c in d["channels"]}
    assert "aa" in names                           # mouth still present
    assert "blink_L" in names and "headYaw" in names
    # head/eye channels carry signed degrees, so they are NOT in [0, 1].
    head = _chan([Channel(c["name"], [Keyframe(*k) for k in c["keys"]])
                  for c in d["channels"]], "headYaw")
    assert any(k.value < 0 for k in head.keys)


def test_cli_blink_rate_and_no_brows(tmp_path):
    # --blink-rate sets the mean interval; --no-brows drops the brow channel.
    out = str(tmp_path / "fast.json")
    rc = cli_main(["naive", "--text", "hello world", "--wav", VOICE,
                   "--gestures", "--blink-rate", "40", "--no-brows", "-o", out])
    assert rc == 0
    d = json.load(open(out))
    assert not any(c["name"] == "browUp" for c in d["channels"])


def test_cli_blink_rate_rejects_nonpositive(tmp_path):
    with pytest.raises(SystemExit):
        cli_main(["naive", "--text", "x", "--duration", "5", "--gestures",
                  "--blink-rate", "0", "-o", str(tmp_path / "x.json")])


def test_energy_gestures_are_envelope_driven(tmp_path):
    out = str(tmp_path / "e.json")
    rc = cli_main(["energy", "--wav", VOICE, "--gestures", "-o", out])
    assert rc == 0
    d = json.load(open(out))
    names = {c["name"] for c in d["channels"]}
    assert "aa" in names and "headPitch" in names   # mouth + gesture layers
