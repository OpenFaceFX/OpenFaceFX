"""Prosody extraction (`openfacefx.prosody`, issue #4): the numpy autocorrelation
pitch tracker and the typed events it derives.

Synthetic signals built in-test with the stdlib `wave` module exercise the tracker
and the event heuristics against ground truth we control: a pure tone recovers its
frequency, digital silence reads unvoiced, a loud+high burst fires an emphasis, a
silent gap fires a phrase boundary, and a rising terminal F0 fires a question
rise. It is DSP, not an ML prosody model, so the asserts pin *relative* structure
(a peak lands in the right window, a boundary exists) with wide margins, plus the
determinism and byte-identity contracts the rest of the pipeline holds to.
"""

import json
import os
import sys
import wave

import numpy as np
import pytest

try:
    import openfacefx  # noqa: F401  (installed wheel wins; see test_core)
except ImportError:
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from openfacefx.cli import main as cli_main
from openfacefx.events import Event, EVENT_TYPES, event_to_dict, event_from_dict
from openfacefx.io_export import to_dict
from openfacefx.prosody import (
    ProsodyParams, ProsodyTrack, pitch_track, prosody_features, prosody_events,
    detect_events,
)

ROOT = os.path.join(os.path.dirname(__file__), "..")
VOICE = os.path.join(ROOT, "examples", "voice.wav")
RATE = 16000


def _write_pcm(path, samples, rate=RATE, channels=1):
    """Write a float signal in [-1, 1] as 16-bit PCM (mirrors test_energy)."""
    data = np.asarray(samples, dtype=np.float64)
    ints = np.clip(np.round(data * 32767.0), -32768, 32767).astype("<i2")
    with wave.open(str(path), "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(ints.reshape(-1).tobytes())


def _tone(freq, dur, amp=0.6, rate=RATE):
    """A sine of ``freq`` Hz (scalar, or a per-sample array for a glide) held for
    ``dur`` seconds. Continuous-phase so a glide has no clicks."""
    t = np.arange(int(rate * dur)) / rate
    f = np.full_like(t, float(freq)) if np.isscalar(freq) else np.asarray(freq)
    phase = 2.0 * np.pi * np.cumsum(f) / rate
    return amp * np.sin(phase)


def _read(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def _named(events, name):
    return [e for e in events if e.name == name]


# --- pitch tracker ----------------------------------------------------------

def test_pitch_tracker_recovers_pure_tone():
    # A 150 Hz sine should read fully voiced and recover ~150 Hz within a few %.
    times, f0, voiced = pitch_track(_tone(150.0, 1.0), rate=RATE, fps=100.0)
    assert len(times) == len(f0) == len(voiced)
    assert voiced.mean() > 0.9                          # a clean tone is all voiced
    est = np.nanmedian(f0[voiced])
    assert abs(est - 150.0) / 150.0 < 0.03             # within 3 %
    # unvoiced frames (none expected here) would be nan, never a bogus Hz
    assert np.all(np.isnan(f0[~voiced]))


def test_pitch_tracker_other_frequency():
    # Not hard-coded to 150: a 220 Hz tone recovers 220 Hz too.
    _, f0, voiced = pitch_track(_tone(220.0, 0.8), rate=RATE, fps=100.0)
    assert abs(np.nanmedian(f0[voiced]) - 220.0) / 220.0 < 0.03


def test_pitch_tracker_silence_is_unvoiced():
    times, f0, voiced = pitch_track(np.zeros(RATE), rate=RATE, fps=100.0)
    assert not voiced.any()                             # nothing periodic to find
    assert np.all(np.isnan(f0))                         # no frequency reported


def test_pitch_track_accepts_wav_path(tmp_path):
    p = tmp_path / "tone.wav"
    _write_pcm(p, _tone(180.0, 0.8))
    _, f0, voiced = pitch_track(str(p), fps=100.0)      # path, rate read from file
    assert abs(np.nanmedian(f0[voiced]) - 180.0) / 180.0 < 0.03


def test_pitch_track_samples_need_rate():
    with pytest.raises(ValueError, match="sample rate"):
        pitch_track(_tone(150.0, 0.3))                  # ndarray, no rate


# --- prosody_features bundle ------------------------------------------------

def test_features_bundle_shapes_and_rate(tmp_path):
    p = tmp_path / "v.wav"
    # Four evenly spaced syllable-like bursts of voiced tone over 2 s.
    t = np.arange(int(RATE * 2.0)) / RATE
    amp = np.zeros_like(t)
    for c in (0.3, 0.8, 1.3, 1.8):
        amp += np.exp(-((t - c) ** 2) / (2 * 0.05 ** 2))
    _write_pcm(p, np.clip(amp, 0, 1) * _tone(160.0, 2.0))
    tr = prosody_features(str(p), fps=100.0)
    assert isinstance(tr, ProsodyTrack)
    n = len(tr.times)
    assert len(tr.f0) == len(tr.voiced) == len(tr.rms) == len(tr.clarity) == n
    assert tr.speaking_rate > 0.0                       # syllable proxy fired
    assert tr.voiced.any()


# --- event derivation: emphasis --------------------------------------------

def _emphasis_wav(path):
    """Quiet 130 Hz carrier with one loud, higher-pitched (200 Hz) burst in the
    middle — coincident pitch+energy prominence, the emphasis cue."""
    dur = 2.0
    t = np.arange(int(RATE * dur)) / RATE
    f = np.where((t >= 0.8) & (t <= 1.2), 200.0, 130.0)
    amp = np.where((t >= 0.8) & (t <= 1.2), 0.9, 0.2)
    _write_pcm(path, amp * _tone(f, dur))


def test_emphasis_on_pitch_and_energy_peak(tmp_path):
    p = tmp_path / "emph.wav"
    _emphasis_wav(p)
    evs = prosody_events(str(p), fps=100.0)
    emph = _named(evs, "emphasis")
    assert emph, "a coincident pitch+energy burst must fire an emphasis"
    assert emph[0].type == "emphasis"
    assert any(0.75 <= e.t <= 1.25 for e in emph)       # lands on the burst
    assert 0.0 < emph[0].payload["strength"] <= 1.0


def test_quiet_flat_tone_has_no_emphasis(tmp_path):
    # A steady, unremarkable tone has no prominence peak -> no emphasis events.
    p = tmp_path / "flat.wav"
    _write_pcm(p, _tone(150.0, 1.5, amp=0.4))
    assert not _named(prosody_events(str(p), fps=100.0), "emphasis")


# --- event derivation: phrase boundary -------------------------------------

def test_long_silence_fires_phrase_boundary(tmp_path):
    # Two voiced blobs split by ~0.5 s of silence: the gap is a phrase boundary.
    p = tmp_path / "pause.wav"
    dur = 2.4
    t = np.arange(int(RATE * dur)) / RATE
    sig = np.zeros_like(t)
    a = (t >= 0.2) & (t <= 0.9)
    b = (t >= 1.5) & (t <= 2.2)
    sig[a] = _tone(150.0, dur)[a]
    sig[b] = _tone(150.0, dur)[b]
    _write_pcm(p, 0.5 * sig)
    bounds = _named(prosody_events(str(p), fps=100.0), "phrase_boundary")
    assert bounds, "a long internal silence must fire a phrase boundary"
    # one boundary sits inside the mid gap (roughly 0.9..1.5)
    assert any(0.9 <= e.t <= 1.5 for e in bounds)
    assert all(e.type == "marker" for e in bounds)
    assert {e.payload["level"] for e in bounds} <= {"clause", "sentence"}


def test_boundary_at_utterance_end(tmp_path):
    p = tmp_path / "end.wav"
    _write_pcm(p, _tone(150.0, 1.0, amp=0.5))
    bounds = _named(prosody_events(str(p), fps=100.0), "phrase_boundary")
    assert bounds and bounds[-1].t == pytest.approx(1.0, abs=0.05)  # ends the line


# --- event derivation: question rise ---------------------------------------

def test_terminal_rise_fires_question(tmp_path):
    # Steady 160 Hz then a steep terminal glide up to 260 Hz, then silence.
    p = tmp_path / "q.wav"
    dur = 1.6
    t = np.arange(int(RATE * dur)) / RATE
    f = np.full_like(t, 160.0)
    rise = (t >= 1.0) & (t <= 1.35)
    f[rise] = 160.0 + (t[rise] - 1.0) / 0.35 * 100.0
    sig = _tone(f, dur)
    sig[t > 1.35] = 0.0
    _write_pcm(p, sig)
    q = _named(prosody_events(str(p), fps=100.0), "question_rise")
    assert q, "a rising terminal F0 must fire a question_rise"
    assert q[0].payload["net_rise"] >= 2.0             # semitones, above threshold
    assert q[0].payload["slope"] > 0.0


def test_falling_tone_has_no_question(tmp_path):
    # A falling terminal F0 (statement intonation) must NOT fire question_rise.
    p = tmp_path / "stmt.wav"
    dur = 1.4
    t = np.arange(int(RATE * dur)) / RATE
    f = np.linspace(240.0, 150.0, len(t))              # steadily falling
    sig = _tone(f, dur)
    sig[t > 1.2] = 0.0
    _write_pcm(p, sig)
    assert not _named(prosody_events(str(p), fps=100.0), "question_rise")


# --- events are valid openfacefx.events.Event -------------------------------

def test_events_are_valid_and_serialize(tmp_path):
    p = tmp_path / "emph.wav"
    _emphasis_wav(p)
    evs = prosody_events(str(p), fps=100.0)
    assert evs and all(isinstance(e, Event) for e in evs)
    assert all(e.type in EVENT_TYPES for e in evs)     # controlled vocabulary
    assert [e.t for e in evs] == sorted(e.t for e in evs)   # time-sorted
    # each serialises to a stable dict (the format rounds to 4 dp; the dict form
    # is idempotent under a read/write round-trip)
    for e in evs:
        assert event_to_dict(event_from_dict(event_to_dict(e))) == event_to_dict(e)
    # and they serialise inside a FaceTrack's JSON like an authored layer
    from openfacefx.curves import Channel, FaceTrack, Keyframe
    tr = FaceTrack(fps=60, channels=[Channel("aa", [Keyframe(0.0, 0.5),
                                                    Keyframe(2.0, 0.0)])])
    tr.events = evs
    d = json.loads(json.dumps(to_dict(tr)))
    assert len(d["events"]) == len(evs)
    assert d["events"][0]["name"] in {"emphasis", "phrase_boundary", "question_rise"}


def test_detect_events_seam_on_track(tmp_path):
    # detect_events works directly on a ProsodyTrack (the reusable seam).
    p = tmp_path / "emph.wav"
    _emphasis_wav(p)
    track = prosody_features(str(p), fps=100.0)
    evs = detect_events(track, ProsodyParams())
    assert evs == prosody_events(str(p), fps=100.0)    # same result either entry


# --- determinism ------------------------------------------------------------

def test_events_deterministic_same_input(tmp_path):
    p = tmp_path / "emph.wav"
    _emphasis_wav(p)
    a = [event_to_dict(e) for e in prosody_events(str(p), fps=100.0)]
    b = [event_to_dict(e) for e in prosody_events(str(p), fps=100.0)]
    assert a == b                                       # no RNG, no jitter


def test_pitch_track_deterministic(tmp_path):
    sig = _tone(170.0, 1.0)
    t1, f1, v1 = pitch_track(sig, rate=RATE, fps=100.0)
    t2, f2, v2 = pitch_track(sig, rate=RATE, fps=100.0)
    assert np.array_equal(t1, t2)
    assert np.array_equal(np.nan_to_num(f1), np.nan_to_num(f2))
    assert np.array_equal(v1, v2)


# --- CLI integration --------------------------------------------------------

def test_cli_prosody_off_is_byte_identical(tmp_path):
    # No --prosody -> no event layer, byte-identical to a plain run (and stable).
    base = str(tmp_path / "a.json")
    again = str(tmp_path / "b.json")
    argv = ["naive", "--text", "hello world this is a test", "--wav", VOICE]
    assert cli_main(argv + ["-o", base]) == 0
    assert cli_main(argv + ["-o", again]) == 0
    assert _read(base) == _read(again)
    assert "events" not in json.loads(_read(base))


def test_cli_prosody_adds_events(tmp_path):
    out = str(tmp_path / "p.json")
    rc = cli_main(["naive", "--text", "are you going to the store",
                   "--wav", VOICE, "--prosody", "-o", out])
    assert rc == 0
    d = json.loads(_read(out))
    assert d["channels"]                                # mouth track still there
    evs = d.get("events", [])
    assert evs and all(e["type"] in EVENT_TYPES for e in evs)
    assert {e["name"] for e in evs} <= {"emphasis", "phrase_boundary", "question_rise"}


def test_cli_prosody_needs_audio(tmp_path):
    # naive with --duration (no wav) + --prosody errors clearly, never a crash.
    out = str(tmp_path / "x.json")
    with pytest.raises(SystemExit, match="prosody needs audio"):
        cli_main(["naive", "--text", "hello world", "--duration", "2.0",
                  "--prosody", "-o", out])


def test_cli_prosody_composes_with_events(tmp_path):
    # --prosody (audio) and --events (timing/energy) attach independently; the
    # audio 'phrase_boundary' and the timing 'phrase'/'beat' names coexist.
    out = str(tmp_path / "c.json")
    rc = cli_main(["naive", "--text", "are you going to the store",
                   "--wav", VOICE, "--prosody", "--events", "-o", out])
    assert rc == 0
    names = {e["name"] for e in json.loads(_read(out)).get("events", [])}
    assert "phrase_boundary" in names                   # from --prosody (audio)
    assert names & {"phrase", "beat"}                   # from --events (timing)


def test_cli_prosody_mfa_needs_wav(tmp_path):
    # mfa can take --wav for prosody; without it, --prosody errors clearly.
    tg = tmp_path / "x.TextGrid"
    tg.write_text(
        'File type = "ooTextFile"\nObject class = "TextGrid"\n'
        "xmin = 0\nxmax = 1\ntiers? <exists>\nsize = 1\nitem []:\n"
        "    item [1]:\n        class = \"IntervalTier\"\n        name = \"phones\"\n"
        "        xmin = 0\n        xmax = 1\n        intervals: size = 1\n"
        "        intervals [1]:\n            xmin = 0\n            xmax = 1\n"
        '            text = "AA1"\n')
    with pytest.raises(SystemExit, match="prosody needs audio"):
        cli_main(["mfa", "--textgrid", str(tg), "--prosody",
                  "-o", str(tmp_path / "m.json")])


def test_cli_energy_prosody_e2e(tmp_path):
    # Prosody events on the real example voice, into a .anim: they must fill the
    # Unity m_Events slot exactly like any other event layer.
    out = str(tmp_path / "e.anim")
    rc = cli_main(["energy", "--wav", VOICE, "--prosody", "-o", out])
    assert rc == 0
    text = _read(out)
    assert "m_Events: []" not in text                   # events made it in
    assert "functionName: OnFaceEvent" in text
