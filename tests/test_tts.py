"""Pure-numpy formant TTS (:mod:`openfacefx.tts`) — the Studio's 'generate voice'.

Not asserting intelligibility (it's a robotic formant synth), but the properties
that make it a usable audio buffer: right length/range, silence is silent, speech
carries energy, vowels have distinct formant structure, deterministic, and it
round-trips through the energy engine as a valid WAV.
"""

import io
import wave

import numpy as np

from openfacefx import synthesize, synth_wav_bytes, to_wav_bytes


def _f2(word, lo=900, hi=2700):
    s, sr = synthesize(word, 0.6)
    seg = s[int(0.2 * sr):int(0.42 * sr)]
    sp = np.abs(np.fft.rfft(seg * np.hanning(len(seg))))
    f = np.fft.rfftfreq(len(seg), 1.0 / sr)
    band = (f > lo) & (f < hi)
    return f[band][np.argmax(sp[band])]


def test_length_and_range():
    s, sr = synthesize("hello brave new world", 2.4)
    assert sr == 16000 and s.dtype == np.float32
    assert abs(len(s) / sr - 2.4) < 0.05
    assert np.abs(s).max() <= 1.0 and np.abs(s).max() > 0.5   # normalised, non-trivial


def test_silence_is_silent_speech_has_energy():
    s, sr = synthesize("hello brave new world", 2.4)
    lead = np.sqrt(np.mean(s[:int(0.08 * sr)] ** 2))          # padded leading silence
    body = np.sqrt(np.mean(s[int(0.3 * sr):int(1.8 * sr)] ** 2))
    assert lead < 0.02 and body > lead * 5


def test_vowels_have_distinct_formants():
    assert _f2("ee") > _f2("aw") + 200          # IY (high F2) vs AO (low F2)


def test_deterministic():
    a, _ = synthesize("the quick brown fox", 2.0)
    b, _ = synthesize("the quick brown fox", 2.0)
    assert np.array_equal(a, b)


def test_wav_bytes_valid_and_readable():
    b = synth_wav_bytes("test one two three", 1.6)
    assert b[:4] == b"RIFF" and b[8:12] == b"WAVE"
    with wave.open(io.BytesIO(b), "rb") as w:
        assert w.getnchannels() == 1 and w.getsampwidth() == 2 and w.getframerate() == 16000
        assert w.getnframes() > 0


def test_drives_energy_engine(tmp_path):
    from openfacefx.energy import generate_from_energy
    p = tmp_path / "voice.wav"
    p.write_bytes(synth_wav_bytes("hello brave new world", 2.4))
    track = generate_from_energy(str(p))                      # synthesized audio → lip-sync
    assert track.channels and track.duration > 0


def test_to_wav_bytes_clips_out_of_range():
    b = to_wav_bytes(np.array([2.0, -2.0, 0.0], dtype=np.float32), 16000)
    with wave.open(io.BytesIO(b), "rb") as w:
        pcm = np.frombuffer(w.readframes(w.getnframes()), dtype="<i2")
    assert pcm[0] == 32767 and pcm[1] == -32767     # clipped, not wrapped
