"""Pure-numpy formant speech synthesis — turn a transcript into a speech-like
waveform, so the Studio can *generate* voice audio instead of only loading it.

A source-filter / additive-formant synthesizer built on **continuous trajectories**
(the key to sounding less robotic): the formants glide smoothly between phonemes
(coarticulation), the glottal source keeps a single continuous phase across the
whole utterance (no per-segment clicks/buzz), pitch has a gentle declination +
vibrato, and voiced tone is mixed with shaped noise by a smooth voicing envelope.
It's still recognisably *synthetic* (eSpeak/Klatt tradition), but far cleaner than
per-segment concatenation. Deterministic, dependency-free (numpy + stdlib ``wave``,
Pyodide-safe), reusing :func:`openfacefx.pipeline.naive_segments` for timing so the
words line up with the mouth. It produces a real audio buffer that drives the energy
engine + spectrogram + playback.
"""

from __future__ import annotations

import io
import re
import wave
from typing import Optional, Tuple

import numpy as np

from .g2p import G2P
from .pipeline import naive_segments

__all__ = ["synthesize", "to_wav_bytes", "synth_wav_bytes"]

# --- phoneme acoustics (ARPABET base, stress stripped) --------------------- #
_VOWEL = {
    "AA": (700, 1220, 2600), "AE": (660, 1720, 2410), "AH": (620, 1220, 2550),
    "AO": (600, 900, 2600), "EH": (550, 1770, 2490), "ER": (490, 1350, 1690),
    "IH": (400, 1900, 2570), "IY": (300, 2300, 3000), "UH": (450, 1100, 2350),
    "UW": (320, 900, 2200), "AX": (620, 1220, 2550),
}
_DIPH = {
    "AY": ((660, 1080, 2410), (330, 2200, 2900)),
    "EY": ((480, 1720, 2520), (330, 2200, 2900)),
    "OY": ((550, 960, 2400), (330, 2200, 2900)),
    "AW": ((640, 1230, 2550), (450, 1000, 2300)),
    "OW": ((500, 900, 2200), (330, 900, 2200)),
}
_NASAL = {"M": (250, 1100, 2300), "N": (250, 1700, 2600), "NG": (250, 2300, 2750)}
_APPROX = {"L": (360, 1300, 2900), "R": (490, 1350, 1690),
           "W": (300, 610, 2200), "Y": (260, 2070, 3020)}
_FRIC_U = {"F": (4200, 3200), "TH": (5800, 2600), "S": (6500, 1400),
           "SH": (3000, 1400), "HH": (1600, 2600)}
_FRIC_V = {"V": (4200, 3200), "DH": (5800, 2600), "Z": (6500, 1400), "ZH": (3000, 1400)}
_STOP_U = {"P", "T", "K"}
_STOP_V = {"B", "D", "G"}
_AFFR = {"CH": "SH", "JH": "ZH"}
_SILENCE = {"SIL", "SP", "_", ""}       # compared against the stress-stripped, upper-cased base

_NEUTRAL = (500.0, 1500.0, 2500.0)      # schwa-ish formants for silence/noise regions
_BW = np.array([80.0, 110.0, 150.0])    # formant bandwidths (F1,F2,F3)


def _base(phoneme: str) -> str:
    return re.sub(r"\d+$", "", (phoneme or "").strip()).upper()


def _stop_centre(base: str) -> float:
    if base in ("T", "D"):
        return 3500.0
    if base in ("K", "G"):
        return 1700.0
    return 900.0                          # P/B — low burst


def _classify(base: str) -> dict:
    """Per-phoneme synthesis targets: formant glide (start,end), voicing fraction,
    amplitude, optional (noise-centre, noise-bw), and whether it's a stop (closure+burst)."""
    if base in _SILENCE:
        return dict(f=(_NEUTRAL, _NEUTRAL), voi=0.0, amp=0.0, noise=None, stop=False)
    if base in _DIPH:
        a, b = _DIPH[base]
        return dict(f=(a, b), voi=1.0, amp=1.0, noise=None, stop=False)
    if base in _VOWEL:
        fm = _VOWEL[base]
        return dict(f=(fm, fm), voi=1.0, amp=1.0, noise=None, stop=False)
    if base in _NASAL:
        fm = _NASAL[base]
        return dict(f=(fm, fm), voi=1.0, amp=0.6, noise=None, stop=False)
    if base in _APPROX:
        fm = _APPROX[base]
        return dict(f=(fm, fm), voi=1.0, amp=0.78, noise=None, stop=False)
    if base in _FRIC_U:
        return dict(f=(_NEUTRAL, _NEUTRAL), voi=0.0, amp=0.42, noise=_FRIC_U[base], stop=False)
    if base in _FRIC_V:
        return dict(f=((350, 1200, 2400), (350, 1200, 2400)), voi=0.5, amp=0.5, noise=_FRIC_V[base], stop=False)
    if base in _STOP_U:
        return dict(f=(_NEUTRAL, _NEUTRAL), voi=0.0, amp=0.5, noise=(_stop_centre(base), 1900), stop=True)
    if base in _STOP_V:
        return dict(f=((350, 1200, 2400), (350, 1200, 2400)), voi=0.28, amp=0.5, noise=(_stop_centre(base), 1900), stop=True)
    if base in _AFFR:
        return dict(f=(_NEUTRAL, _NEUTRAL), voi=0.0, amp=0.5, noise=_FRIC_U[_AFFR[base]], stop=True)
    return dict(f=(_NEUTRAL, _NEUTRAL), voi=0.6, amp=0.5, noise=None, stop=False)   # unknown → soft vowel


def _norm(x: np.ndarray) -> np.ndarray:
    mx = np.abs(x).max()
    return x / mx if mx > 1e-9 else x


def _smooth(x: np.ndarray, sr: int, ms: float) -> np.ndarray:
    """Box-filter smoothing (coarticulation / envelope glides)."""
    k = max(1, int(sr * ms / 1000.0))
    if k <= 1:
        return x
    win = np.ones(k) / k
    if x.ndim == 1:
        return np.convolve(x, win, "same")
    return np.stack([np.convolve(x[:, i], win, "same") for i in range(x.shape[1])], axis=1)


def _shape_noise(n: int, sr: int, centre: float, bw: float,
                 rng: np.random.Generator) -> np.ndarray:
    if n <= 0:
        return np.zeros(0)
    x = rng.standard_normal(n)
    spec = np.fft.rfft(x)
    f = np.fft.rfftfreq(n, 1.0 / sr)
    spec *= np.exp(-0.5 * ((f - centre) / bw) ** 2)
    return _norm(np.fft.irfft(spec, n))


def synthesize(text: str, duration: float, *, sr: int = 16000,
               g2p: Optional[G2P] = None, f0: float = 112.0,
               seed: int = 7) -> Tuple[np.ndarray, int]:
    """Synthesize ``text`` into a mono float32 waveform in ``[-1, 1]`` over
    ``duration`` seconds. Returns ``(samples, sr)``."""
    if duration <= 0:
        raise ValueError("duration must be > 0")
    segs = naive_segments(text, duration, g2p=g2p)
    n = int(round(duration * sr))
    if n <= 0:
        return np.zeros(0, dtype=np.float32), sr
    t = np.arange(n) / sr
    rng = np.random.default_rng(seed)

    # per-sample envelopes + formant control points, filled per segment
    voi = np.zeros(n)
    amp = np.zeros(n)
    noise = np.zeros(n)
    ct: list = [0.0]
    cf: list = [np.array(_NEUTRAL, dtype=float)]
    for s in segs:
        c = _classify(_base(s.phoneme))
        a = int(round(s.start * sr))
        b = min(int(round(s.end * sr)), n)
        if b <= a:
            continue
        fa = np.array(c["f"][0], dtype=float)
        fb = np.array(c["f"][1], dtype=float)
        dur = max(1e-4, s.end - s.start)
        ct += [s.start + 0.2 * dur, s.start + 0.8 * dur]      # glide within the segment
        cf += [fa, fb]
        if c["stop"]:                                         # closure (silent) then burst
            closed = a + int((b - a) * 0.6)
            voi[closed:b] = c["voi"]; amp[closed:b] = c["amp"]
            if c["noise"]:
                noise[closed:b] += _shape_noise(b - closed, sr, c["noise"][0], c["noise"][1], rng)
        else:
            voi[a:b] = c["voi"]; amp[a:b] = c["amp"]
            if c["noise"]:
                noise[a:b] += _shape_noise(b - a, sr, c["noise"][0], c["noise"][1], rng)
    ct.append(duration); cf.append(np.array(_NEUTRAL, dtype=float))

    # smooth formant trajectory (coarticulation) + envelope glides
    ct_a = np.asarray(ct)
    cf_a = np.asarray(cf)
    order = np.argsort(ct_a, kind="stable")
    ct_a, cf_a = ct_a[order], cf_a[order]
    F = np.stack([np.interp(t, ct_a, cf_a[:, i]) for i in range(3)], axis=1)
    F = _smooth(F, sr, 8.0)
    voi = np.clip(_smooth(voi, sr, 14.0), 0.0, 1.0)
    amp = _smooth(amp, sr, 12.0)

    # pitch: declination + gentle vibrato
    frac = np.clip(t / max(duration, 1e-3), 0.0, 1.0)
    f0t = f0 * (1.06 - 0.16 * frac) * (1.0 + 0.015 * np.sin(2 * np.pi * 4.5 * t))
    phase = 2 * np.pi * np.cumsum(f0t) / sr

    # voiced source: continuous phase, per-sample formant-shaped harmonics
    voiced = np.zeros(n)
    hmax = int((sr / 2) / max(70.0, float(f0t.min())))
    for h in range(1, hmax + 1):
        fh = h * f0t
        g = np.zeros(n)
        for i in range(3):
            g += np.exp(-0.5 * ((fh - F[:, i]) / _BW[i]) ** 2)
        voiced += ((g + 0.004) / h ** 1.15) * np.sin(h * phase)

    voiced = _norm(voiced)
    noise = _norm(noise)
    out = amp * (voi * voiced + (1.0 - voi) * noise)
    out = np.tanh(out * 1.4)                                  # gentle soft-clip (warmth, no harsh peaks)
    out = _norm(out) * 0.9
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
