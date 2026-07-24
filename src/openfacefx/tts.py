"""Pure-numpy formant speech synthesis — turn a transcript into a speech-like
waveform, so the Studio can *generate* voice audio instead of only loading it.

This is a source-filter / additive-formant synthesizer: voiced phonemes are a
glottal buzz whose harmonics are shaped by the vowel/consonant formants;
fricatives are spectrally-shaped noise; stops are a closure + burst. It won't
pass for a human — it's intelligible-ish *robotic* speech, in the eSpeak/Klatt
tradition — but it is a real audio buffer that:

  * drives the energy engine (:mod:`openfacefx.energy`) for audio-based lip-sync,
  * renders a proper spectrogram in the Studio, and
  * can be played back.

Dependency-free (numpy + the stdlib ``wave`` module, which works under Pyodide),
deterministic (fixed RNG seed), and reuses the existing phoneme timing
(:func:`openfacefx.pipeline.naive_segments`), so the same words line up with the
same mouth shapes.
"""

from __future__ import annotations

import io
import re
import wave
from typing import List, Optional, Tuple

import numpy as np

from .g2p import G2P
from .pipeline import naive_segments

__all__ = ["synthesize", "to_wav_bytes", "synth_wav_bytes"]

# --- phoneme acoustics (ARPABET base, stress stripped) --------------------- #
# Vowels: the first three formants (Hz). Monophthongs.
_VOWEL = {
    "AA": (700, 1220, 2600), "AE": (660, 1720, 2410), "AH": (620, 1220, 2550),
    "AO": (600, 900, 2600), "EH": (550, 1770, 2490), "ER": (490, 1350, 1690),
    "IH": (400, 1900, 2570), "IY": (300, 2300, 3000), "UH": (450, 1100, 2350),
    "UW": (320, 900, 2200), "AX": (620, 1220, 2550),
}
# Diphthongs: glide from a start to an end formant target across the segment.
_DIPH = {
    "AY": ((660, 1080, 2410), (300, 2300, 3000)),
    "EY": ((480, 1720, 2520), (300, 2300, 3000)),
    "OY": ((550, 960, 2400), (300, 2300, 3000)),
    "AW": ((640, 1230, 2550), (450, 1100, 2350)),
    "OW": ((500, 900, 2200), (320, 900, 2200)),
}
# Voiced sonorants (nasal murmur / approximant): formants + a quieter amplitude.
_NASAL = {"M": (250, 1100, 2300), "N": (250, 1700, 2600), "NG": (250, 2300, 2750)}
_APPROX = {"L": (360, 1300, 2900), "R": (490, 1350, 1690),
           "W": (300, 610, 2200), "Y": (260, 2070, 3020)}
# Fricatives: (noise centre Hz, bandwidth Hz). Voiced ones add a low buzz.
_FRIC_U = {"F": (4200, 3200), "TH": (5800, 2600), "S": (6500, 1400),
           "SH": (3000, 1400), "HH": (1600, 2600)}
_FRIC_V = {"V": (4200, 3200), "DH": (5800, 2600), "Z": (6500, 1400), "ZH": (3000, 1400)}
_STOP_U = {"P", "T", "K"}
_STOP_V = {"B", "D", "G"}
_AFFR = {"CH": "SH", "JH": "ZH"}       # stop burst + fricative
_SILENCE = {"SIL", "SP", "_", ""}      # compared against the stress-stripped, upper-cased base

_BW = np.array([90.0, 110.0, 170.0])   # formant bandwidths (F1,F2,F3)


def _base(phoneme: str) -> str:
    """ARPABET base symbol, stress digits stripped (AH0 -> AH)."""
    return re.sub(r"\d+$", "", (phoneme or "").strip()).upper()


def _voiced(n: int, sr: int, f0: float, formants: np.ndarray, amp: float) -> np.ndarray:
    """Additive glottal buzz at ``f0`` shaped by (possibly time-varying) formants.

    ``formants`` is (n, 3) Hz. Each harmonic's amplitude is the sum of Gaussian
    formant gains at that harmonic's frequency, plus a small spectral floor and a
    gentle -6 dB/oct source tilt."""
    if n <= 0:
        return np.zeros(0)
    t = np.arange(n) / sr
    phase = 2.0 * np.pi * f0 * t
    out = np.zeros(n)
    hmax = max(1, int((sr / 2) / max(60.0, f0)))
    for h in range(1, hmax + 1):
        fh = h * f0
        g = np.zeros(n)
        for i in range(3):
            g += np.exp(-0.5 * ((fh - formants[:, i]) / _BW[i]) ** 2)
        g = (g + 0.006) / h ** 1.05                  # small spectral floor + source tilt (cleaner formants)
        out += g * np.sin(h * phase)
    mx = np.abs(out).max()
    if mx > 1e-9:
        out /= mx
    return out * amp


def _noise(n: int, sr: int, centre: float, bw: float, amp: float,
           rng: np.random.Generator) -> np.ndarray:
    """White noise shaped by a Gaussian band around ``centre`` (FFT-domain)."""
    if n <= 0:
        return np.zeros(0)
    x = rng.standard_normal(n)
    spec = np.fft.rfft(x)
    freqs = np.fft.rfftfreq(n, 1.0 / sr)
    spec *= np.exp(-0.5 * ((freqs - centre) / bw) ** 2)
    y = np.fft.irfft(spec, n)
    mx = np.abs(y).max()
    if mx > 1e-9:
        y /= mx
    return y * amp


def _fade(sig: np.ndarray, sr: int, ms: float = 6.0) -> np.ndarray:
    """Raised-cosine fade in/out to avoid clicks at segment joins."""
    k = min(len(sig) // 2, int(sr * ms / 1000.0))
    if k <= 0:
        return sig
    ramp = 0.5 - 0.5 * np.cos(np.linspace(0, np.pi, k))
    sig = sig.copy()
    sig[:k] *= ramp
    sig[-k:] *= ramp[::-1]
    return sig


def _segment(base: str, n: int, sr: int, f0: float,
             rng: np.random.Generator) -> np.ndarray:
    """Synthesize one phoneme of ``n`` samples."""
    if n <= 0 or base in _SILENCE:
        return np.zeros(max(0, n))

    if base in _VOWEL or base in _DIPH:
        if base in _DIPH:
            a, b = _DIPH[base]
            ramp = np.linspace(0, 1, n)[:, None]
            formants = (1 - ramp) * np.array(a) + ramp * np.array(b)
        else:
            formants = np.tile(_VOWEL[base], (n, 1)).astype(float)
        return _fade(_voiced(n, sr, f0, formants, 1.0), sr)

    if base in _NASAL:
        formants = np.tile(_NASAL[base], (n, 1)).astype(float)
        return _fade(_voiced(n, sr, f0, formants, 0.55), sr)
    if base in _APPROX:
        formants = np.tile(_APPROX[base], (n, 1)).astype(float)
        return _fade(_voiced(n, sr, f0, formants, 0.7), sr)

    if base in _FRIC_U:
        c, bw = _FRIC_U[base]
        return _fade(_noise(n, sr, c, bw, 0.32, rng), sr)
    if base in _FRIC_V:
        c, bw = _FRIC_V[base]
        buzz = _voiced(n, sr, f0, np.tile((300, 1000, 2200), (n, 1)).astype(float), 0.22)
        return _fade(_noise(n, sr, c, bw, 0.28, rng) + buzz, sr)

    if base in _STOP_U or base in _STOP_V or base in _AFFR:
        out = np.zeros(n)
        closed = int(n * 0.55)                      # silent closure, then a burst
        burst = n - closed
        if burst > 0:
            if base in _AFFR:                        # affricate: burst + fricative tail
                c, bw = _FRIC_U[_AFFR[base]]
                out[closed:] = _noise(burst, sr, c, bw, 0.4, rng)
            else:
                centre = 3500.0 if base in ("T", "D") else 1800.0 if base in ("K", "G") else 900.0
                out[closed:] = _noise(burst, sr, centre, 1800.0, 0.45, rng)
                if base in _STOP_V:                  # voiced stops get a little buzz
                    out[closed:] += _voiced(burst, sr, f0,
                                            np.tile((300, 1000, 2200), (burst, 1)).astype(float), 0.18)
        return _fade(out, sr, 3.0)

    # unknown symbol → a soft neutral vowel so timing is still audible
    return _fade(_voiced(n, sr, f0, np.tile((500, 1500, 2500), (n, 1)).astype(float), 0.5), sr)


def synthesize(text: str, duration: float, *, sr: int = 16000,
               g2p: Optional[G2P] = None, f0: float = 118.0,
               seed: int = 7) -> Tuple[np.ndarray, int]:
    """Synthesize ``text`` into a mono float32 waveform in ``[-1, 1]``.

    Timing comes from :func:`naive_segments` so the audio lines up with the
    phoneme/viseme track. ``f0`` is the base pitch (Hz); a gentle declination is
    applied across the utterance. Returns ``(samples, sr)``."""
    if duration <= 0:
        raise ValueError("duration must be > 0")
    segs = naive_segments(text, duration, g2p=g2p)
    rng = np.random.default_rng(seed)
    total = int(round(duration * sr))
    out = np.zeros(total + sr // 10, dtype=np.float64)   # a little tail headroom
    for s in segs:
        a = int(round(s.start * sr))
        n = int(round((s.end - s.start) * sr))
        if n <= 0:
            continue
        # pitch declination: ~+4 % at the start down to ~-8 % at the end
        frac = 0.0 if duration <= 0 else min(1.0, max(0.0, s.start / duration))
        f = f0 * (1.04 - 0.12 * frac)
        seg = _segment(_base(s.phoneme), n, sr, f, rng)
        if seg.size:
            end = min(len(out), a + seg.size)
            out[a:end] += seg[:end - a]
    out = out[:total]
    mx = np.abs(out).max()
    if mx > 1e-9:
        out = out / mx * 0.92                          # normalize, leave headroom
    return out.astype(np.float32), sr


def to_wav_bytes(samples: np.ndarray, sr: int) -> bytes:
    """Encode float ``samples`` in ``[-1, 1]`` as 16-bit PCM mono WAV bytes."""
    pcm = (np.clip(np.asarray(samples), -1.0, 1.0) * 32767.0).astype("<i2").tobytes()
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(int(sr))
        w.writeframes(pcm)
    return buf.getvalue()


def synth_wav_bytes(text: str, duration: float, **kw) -> bytes:
    """Convenience: :func:`synthesize` then :func:`to_wav_bytes`."""
    samples, sr = synthesize(text, duration, **kw)
    return to_wav_bytes(samples, sr)
