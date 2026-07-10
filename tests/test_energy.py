"""Audio-energy fallback (`openfacefx energy`): synthetic WAVs built in-test
with the stdlib `wave` module exercise the envelope math and the track
synthesis, and the CLI is run end-to-end on the real examples/voice.wav.

The mode is an amplitude fallback, not viseme detection — the tests assert
envelope *shape* (rises/falls, gated, attack<release) and that jaw-open drives
the track, never that a channel matches any spoken sound.
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
from openfacefx.energy import energy_envelope, generate_from_energy
from openfacefx.io_export import to_dict
from openfacefx.visemes import VISEMES

ROOT = os.path.join(os.path.dirname(__file__), "..")
VOICE = os.path.join(ROOT, "examples", "voice.wav")


def _write_pcm(path, samples, rate=16000, channels=1):
    """Write a float signal in [-1, 1] as 16-bit PCM. ``samples`` is 1-D for
    mono, or shape (n, channels) for interleaved multi-channel."""
    data = np.asarray(samples, dtype=np.float64)
    ints = np.clip(np.round(data * 32767.0), -32768, 32767).astype("<i2")
    with wave.open(str(path), "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(ints.reshape(-1).tobytes())


def _assert_track_invariants(path, viseme_names=True):
    """Same invariants test_e2e checks: sorted key times in-bounds, values in
    [0, 1], names in the declared viseme set."""
    with open(path, encoding="utf-8") as fh:
        d = json.load(fh)
    assert d["format"] == "openfacefx.track" and d["version"] == 1
    assert d["duration"] > 0 and d["channels"]
    for c in d["channels"]:
        times = [k[0] for k in c["keys"]]
        vals = [k[1] for k in c["keys"]]
        assert times == sorted(times), c["name"]
        assert all(0.0 <= v <= 1.0 for v in vals), c["name"]
        assert all(t <= d["duration"] + 1e-6 for t in times), c["name"]
        assert c["name"] in d["viseme_set"]
        if viseme_names:
            assert c["name"] in VISEMES
    return d


def _amp_bump(t, lo, hi):
    """Raised-cosine 0->1->0 over [lo, hi], zero elsewhere (a smooth burst
    amplitude envelope)."""
    amp = np.zeros_like(t)
    inside = (t >= lo) & (t <= hi)
    amp[inside] = 0.5 * (1.0 - np.cos(2 * np.pi * (t[inside] - lo) / (hi - lo)))
    return amp


def _sample_channel(track, name, t):
    ch = [c for c in track.channels if c.name == name]
    if not ch:
        return 0.0
    keys = ch[0].keys
    return float(np.interp(t, [k.time for k in keys], [k.value for k in keys]))


def test_energy_silence_gives_sil_track(tmp_path):
    p = tmp_path / "silence.wav"
    _write_pcm(p, np.zeros(16000))          # 1s of digital silence
    times, env = energy_envelope(str(p))
    assert env.max() == 0.0                 # gated as all-silence
    assert len(times) == len(env)

    d = to_dict(generate_from_energy(str(p)))
    names = {c["name"] for c in d["channels"]}
    assert "aa" not in names                # no mouth-open at all
    assert names <= {"sil"}                 # only rest fires
    sil = [c for c in d["channels"] if c["name"] == "sil"]
    if sil:                                 # mouth held fully closed
        assert all(abs(k[1] - 1.0) < 1e-6 for k in sil[0]["keys"])


def test_energy_burst_drives_aa_rise_and_fall(tmp_path):
    rng = np.random.default_rng(0)
    rate, dur = 16000, 2.0
    t = np.arange(int(rate * dur)) / rate
    sig = 0.8 * _amp_bump(t, 0.4, 1.6) * rng.standard_normal(len(t))
    p = tmp_path / "burst.wav"
    _write_pcm(p, sig)

    fps = 60.0
    tt, env = energy_envelope(str(p), fps=fps)
    assert env.min() >= 0.0 and env.max() <= 1.0          # bounded
    mid = env[(tt > 0.8) & (tt < 1.2)].mean()
    head = env[tt < 0.3].mean()
    tail = env[tt > 1.9].mean()
    assert mid > 0.5                                       # loud in the middle
    assert mid > head and mid > tail                       # rises then falls
    assert head < 0.2                                      # quiet before onset

    track = generate_from_energy(str(p), fps=fps)
    assert any(c.name == "aa" for c in track.channels)     # jaw-open drives it
    assert _sample_channel(track, "aa", 1.0) > _sample_channel(track, "aa", 0.1)


def test_energy_gate_kills_noise_floor(tmp_path):
    rng = np.random.default_rng(1)
    rate = 16000
    t = np.arange(2 * rate) / rate
    sig = 0.02 * rng.standard_normal(len(t))               # quiet floor
    loud = (t > 0.8) & (t < 1.2)
    sig[loud] += 0.7 * rng.standard_normal(int(loud.sum()))  # loud burst
    p = tmp_path / "floor.wav"
    _write_pcm(p, sig)

    _, gated = energy_envelope(str(p), gate=0.15)
    _, ungated = energy_envelope(str(p), gate=0.0)
    tt = np.arange(len(gated)) / 60.0
    pre = tt < 0.5                                          # floor-only region
    assert gated[pre].max() < 0.05                         # gate forces silence
    assert ungated[pre].max() > gated[pre].max()           # gate is what did it


def test_energy_attack_faster_than_release(tmp_path):
    rng = np.random.default_rng(2)
    rate = 16000
    t = np.arange(int(1.5 * rate)) / rate
    on = (t >= 0.5) & (t < 1.0)                            # hard step on/off
    sig = np.zeros(len(t))
    sig[on] = 0.8 * rng.standard_normal(int(on.sum()))
    p = tmp_path / "step.wav"
    _write_pcm(p, sig)

    fps = 60.0
    tt, env = energy_envelope(str(p), fps=fps)

    def _first(mask):
        idx = np.flatnonzero(mask)
        assert len(idx), "crossing never happened"
        return int(idx[0])

    rise = _first((tt >= 0.5) & (env >= 0.5)) - int(round(0.5 * fps))
    fall = _first((tt >= 1.0) & (env < 0.5)) - int(round(1.0 * fps))
    assert rise >= 0 and fall >= 0
    assert rise < fall                                     # opens fast, closes slow


def test_energy_stereo_downmix_matches_average(tmp_path):
    rng = np.random.default_rng(3)
    rate = 16000
    t = np.arange(int(1.5 * rate)) / rate
    # Low amplitude so neither channel clips at ±1 — otherwise the stereo
    # channels clip independently while the pre-averaged mono barely does,
    # which would shift the normalization reference and diverge the envelopes.
    amp = 0.25 * _amp_bump(t, 0.3, 1.2)
    left = amp * rng.standard_normal(len(t))
    right = amp * rng.standard_normal(len(t))              # independent channel

    mono = tmp_path / "mono.wav"
    stereo = tmp_path / "stereo.wav"
    _write_pcm(mono, (left + right) / 2.0)
    _write_pcm(stereo, np.stack([left, right], axis=1), channels=2)

    _, e_mono = energy_envelope(str(mono))
    _, e_stereo = energy_envelope(str(stereo))
    assert e_stereo.shape == e_mono.shape                  # timing unaffected
    assert np.allclose(e_stereo, e_mono, atol=5e-3)        # L/R averaged


def test_energy_rejects_non_16bit(tmp_path):
    p = tmp_path / "8bit.wav"
    with wave.open(str(p), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(1)                                  # 8-bit PCM
        w.setframerate(16000)
        w.writeframes(bytes(16000))
    with pytest.raises(ValueError, match="16-bit"):
        energy_envelope(str(p))


def test_energy_intensity_scales_opening(tmp_path):
    rng = np.random.default_rng(4)
    rate = 16000
    t = np.arange(int(1.5 * rate)) / rate
    sig = 0.3 * _amp_bump(t, 0.3, 1.2) * rng.standard_normal(len(t))
    p = tmp_path / "quiet.wav"
    _write_pcm(p, sig)

    soft = generate_from_energy(str(p), intensity=0.5)
    loud = generate_from_energy(str(p), intensity=2.0)
    assert (max(k.value for c in loud.channels if c.name == "aa"
                for k in c.keys)
            > max(k.value for c in soft.channels if c.name == "aa"
                  for k in c.keys))


def test_energy_output_is_deterministic(tmp_path):
    rng = np.random.default_rng(5)
    rate = 16000
    t = np.arange(int(1.5 * rate)) / rate
    sig = 0.6 * _amp_bump(t, 0.2, 1.3) * rng.standard_normal(len(t))
    p = tmp_path / "det.wav"
    _write_pcm(p, sig)
    a = to_dict(generate_from_energy(str(p)))
    b = to_dict(generate_from_energy(str(p)))
    assert a == b                                          # no jitter/RNG


def test_energy_mapping_without_roles_raises(tmp_path):
    from openfacefx.mapping import Mapping, Target
    p = tmp_path / "x.wav"
    _write_pcm(p, np.zeros(1600))
    m = Mapping([Target("MBP"), Target("open")], {"AA": {"open": 1.0}})
    with pytest.raises(ValueError, match="aa'/'sil'"):
        generate_from_energy(str(p), mapping=m)


def test_energy_cli_e2e_voice(tmp_path):
    out = str(tmp_path / "energy.json")
    rc = cli_main(["energy", "--wav", VOICE, "-o", out])
    assert rc == 0 and os.path.exists(out)
    d = _assert_track_invariants(out)
    names = {c["name"] for c in d["channels"]}
    assert "aa" in names                                   # jaw-open present
    assert abs(d["duration"] - 1.6) < 0.1                  # matches the WAV


def test_energy_cli_composes_with_retarget_and_cues(tmp_path):
    # Output is a normal FaceTrack, so the standard output options apply.
    arkit = str(tmp_path / "arkit.json")
    rc = cli_main(["energy", "--wav", VOICE, "-o", arkit, "--retarget", "arkit"])
    assert rc == 0
    d = _assert_track_invariants(arkit, viseme_names=False)
    assert any(c["name"] == "jawOpen" for c in d["channels"])

    cues = str(tmp_path / "cues.tsv")
    rc = cli_main(["energy", "--wav", VOICE, "-o", cues])
    assert rc == 0
    lines = open(cues, encoding="utf-8").read().splitlines()
    assert lines and lines[-1].split("\t")[1] == "X"       # Rhubarb terminal row
