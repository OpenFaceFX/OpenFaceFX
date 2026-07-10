"""Prosody extraction: pitch, loudness and speaking-rate -> typed events.

Where :mod:`openfacefx.energy` follows *how loud* the voice is, this module also
follows *how high* it is: a short-time **autocorrelation pitch tracker** recovers
a per-frame fundamental frequency (F0) and a voiced/unvoiced flag, and — reusing
``energy.py``'s WAV reader and RMS follower for loudness — turns the two tracks
into the prosodic events an animator wants: **emphasis** (pitch *and* loudness
spike together), **phrase_boundary** (a silent pause or the utterance end) and
**question_rise** (a rising terminal F0, the yes/no-question cue), plus a global
**speaking rate** (a syllable-ish proxy).

    PCM samples -> framed short-time autocorrelation -> F0 + voicing + clarity
                -> robust pitch/energy z-scores -> emphasis / boundary / question
                   events (:class:`openfacefx.events.Event`)

**This is DSP heuristics, not an ML prosody model.** The tracker is the standard
non-neural pipeline (windowed autocorrelation debiased by the window's own
autocorrelation, à la Boersma/Praat; parabolic peak interpolation; an octave-cost
period pick), so expect it to behave accordingly:

  * **Accuracy / voicing.** On clean speech F0 lands within a few percent on
    voiced frames but makes occasional octave errors and mislabels voicing on
    whispered/breathy/creaky voice or low SNR (the voicing gate is clarity+energy,
    not a trained VAD); an ML tracker (CREPE/pYIN) is cleaner. Fine here because
    the *events* need only **relative** pitch movement, not calibrated Hz — an
    octave-shifted F0 still lands the emphasis and question-rise correctly.
  * **Prominence / questions are rule-based** cue layers, not ToBI labelling:
    emphasis keys on coincident pitch+energy peaks (~75-80 % vs ~85 % human
    agreement), and question detection keys purely on terminal F0 rise, so it
    misses falling *wh*-questions and can false-fire on list/uptalk intonation.
  * **Will misbehave on** music/singing, background noise, overlapping speakers
    and heavy reverb. Input must be 16-bit PCM WAV (same as ``energy.py``);
    convert first, e.g. ``ffmpeg -i in.mp3 -c:a pcm_s16le out.wav``.

Events are ordinary :class:`openfacefx.events.Event` records (issue #6), so they
attach to a :class:`~openfacefx.curves.FaceTrack` and serialise through the JSON /
Unity ``.anim`` / Unreal-notify exporters like an authored layer. numpy + stdlib
``wave`` only; no RNG, so identical audio gives identical events across runs,
platforms and Python versions.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import List, Optional, Tuple, Union

import numpy as np

from .energy import _read_wav_mono, _frame_rms, _REF_PERCENTILE
from .events import Event

Source = Union[str, np.ndarray]


# --------------------------------------------------------------------------- #
# Tunable dials (mirrors CoartParams / GestureParams — one place for defaults) #
# --------------------------------------------------------------------------- #

@dataclass
class ProsodyParams:
    """Every threshold the tracker and event derivation use, in one dataclass so
    the defaults live in one place and callers can retune without touching the
    algorithm. Defaults follow Praat's raw-autocorrelation pitch settings and the
    prosodic-prominence literature (see the module docstring)."""

    # -- pitch tracker --
    fmin: float = 80.0            # pitch floor (Hz); frames below read as unvoiced
    fmax: float = 400.0           # pitch ceiling (Hz)
    voicing_threshold: float = 0.45   # min normalized-autocorr clarity to voice
    silence_frac: float = 0.03    # energy gate = silence_frac * 95th-pct RMS
    octave_cost: float = 0.01     # favour higher-F0 candidates (suppress down-octave)

    # -- emphasis (prominence = pitch AND loudness spike) --
    emph_thresh: float = 1.0      # prominence score (robust z units) to fire
    min_emph: float = 0.05        # min run length (s) above threshold
    emph_merge: float = 0.12      # merge emphases closer than this (s)

    # -- phrase boundary (silent pause / utterance end) --
    min_pause: float = 0.18       # min silent run (s) counted as a boundary
    clause_max: float = 0.45      # 0.18..this -> "clause"; longer -> "sentence"

    # -- question rise (terminal F0) --
    q_look: float = 0.30          # window (s) before a boundary to test for rise
    q_min_voiced: int = 4         # min voiced samples in that window
    q_rise: float = 2.0           # min net rise (semitones) to fire

    # -- speaking rate (syllable-nucleus proxy from the loudness envelope) --
    rate_smooth: float = 0.05     # moving-average width (s) on the RMS envelope
    rate_min_spacing: float = 0.12   # min spacing (s) between syllable peaks


@dataclass
class ProsodyTrack:
    """Per-frame prosody bundle at the analysis frame rate ``fps``. ``f0`` is Hz
    on voiced frames and ``nan`` on unvoiced ones; ``voiced`` is the boolean gate;
    ``rms`` is the loudness follower (same units as ``energy._frame_rms``);
    ``clarity`` is the 0..1 autocorrelation periodicity; ``speaking_rate`` is a
    global syllable-per-(voiced-)second estimate."""

    fps: float
    times: np.ndarray
    f0: np.ndarray
    voiced: np.ndarray
    rms: np.ndarray
    clarity: np.ndarray
    speaking_rate: float = 0.0


# --------------------------------------------------------------------------- #
# Small numeric helpers                                                        #
# --------------------------------------------------------------------------- #

def _next_even(n: int) -> int:
    return int(n) + (int(n) & 1)


def _next_pow2(n: int) -> int:
    return 1 << (max(int(n) - 1, 1)).bit_length()


def _frame_length(rate: int, fmin: float, tau_max: int) -> int:
    """Analysis window in samples: long enough to hold ~2.5 of the lowest period
    (and at least 40 ms), and strictly longer than one full lowest period plus a
    lag — the autocorrelation at ``tau_max`` needs a sample to interpolate against."""
    n = _next_even(max(round(2.5 * rate / fmin), round(0.04 * rate)))
    return max(n, tau_max + 2)


def _median_filter(x: np.ndarray, k: int) -> np.ndarray:
    """``k``-tap sliding median with *reflect* padding (numpy, deterministic).

    Reflect rather than edge-replicate so a spike at the very first/last sample —
    e.g. the bogus at-ceiling F0 a tracker throws at a voiced/silence boundary —
    is out-voted by its mirrored neighbours instead of being propagated into the
    pad. That terminal-outlier rejection is what keeps a boundary artifact from
    faking a terminal pitch rise."""
    if len(x) < k or k < 2:
        return x.astype(np.float64, copy=True)
    r = k // 2
    xp = np.pad(x, r, mode="reflect")
    return np.array([np.median(xp[i:i + k]) for i in range(len(x))], dtype=np.float64)


def _robust_z(x: np.ndarray) -> np.ndarray:
    """Median/MAD z-score (outlier-resistant); zeros if the MAD collapses."""
    if len(x) == 0:
        return x.astype(np.float64, copy=True)
    center = float(np.median(x))
    mad = float(np.median(np.abs(x - center)))
    scale = 1.4826 * mad
    if scale < 1e-9:
        return np.zeros_like(x, dtype=np.float64)
    return (x - center) / scale


# --------------------------------------------------------------------------- #
# Framing + autocorrelation pitch tracker                                      #
# --------------------------------------------------------------------------- #

def _frame_signal(signal: np.ndarray, rate: int, fps: float,
                  N: int) -> Tuple[np.ndarray, np.ndarray]:
    """``(times, frames)`` where ``frames[i]`` is the length-``N`` window centred
    on output frame ``i`` (one every ``1/fps`` s). The signal is zero-padded by
    ``N//2`` each side so ``len(frames) == len(times)`` and edge frames are whole.
    Frame count / centres mirror :func:`energy._frame_rms` so the pitch and RMS
    grids line up sample-for-sample."""
    duration = len(signal) / float(rate)
    n = max(int(round(duration * fps)) + 1, 1)
    times = np.arange(n) / fps
    if len(signal) == 0:
        return times, np.zeros((n, N))
    half = N // 2
    padded = np.concatenate([np.zeros(half), signal, np.zeros(N)])
    centres = np.round(np.arange(n) * rate / fps).astype(np.int64)
    # In padded coordinates the window [c-half, c-half+N) starts at exactly c.
    idx = centres[:, None] + np.arange(N)[None, :]
    return times, padded[idx]


def _autocorrelation(frames: np.ndarray, tau_max: int) -> np.ndarray:
    """Window-debiased normalized autocorrelation of each (DC-removed, Hann-
    windowed) frame, rows scaled so ``r[:, 0] == 1`` (Boersma). Returns
    ``(n_frames, tau_max + 1)``."""
    N = frames.shape[1]
    frames = frames - frames.mean(axis=1, keepdims=True)
    window = np.hanning(N)
    xw = frames * window
    nfft = _next_pow2(2 * N)
    spec = np.fft.rfft(xw, nfft, axis=1)
    ac = np.fft.irfft(spec * np.conj(spec), nfft, axis=1)[:, :tau_max + 1]
    # Divide by the window's own autocorrelation to undo its taper bias.
    wspec = np.fft.rfft(window, nfft)
    wac = np.fft.irfft(wspec * np.conj(wspec), nfft)[:tau_max + 1]
    r = ac / np.maximum(wac, 1e-9)
    r0 = np.maximum(r[:, :1], 1e-9)
    return r / r0


def _pick_period(r_row: np.ndarray, tau_min: int, tau_max: int, rate: int,
                 fmax: float, octave_cost: float) -> Tuple[float, float]:
    """Best ``(f0, clarity)`` for one autocorrelation row: among the local maxima
    in ``[tau_min, tau_max]``, pick the lag maximising ``r - octave_cost *
    log2(fmax * tau / rate)`` (Praat's octave cost, which favours the higher-F0
    candidate and so suppresses the down-octave error), then refine with parabolic
    interpolation. ``(0.0, clarity)`` if no interior peak exists."""
    lo = max(tau_min, 1)
    hi = min(tau_max, len(r_row) - 2)
    best_tau, best_score, best_clar = -1, -np.inf, 0.0
    for tau in range(lo, hi + 1):
        rt = r_row[tau]
        if rt > r_row[tau - 1] and rt >= r_row[tau + 1]:
            score = rt - octave_cost * np.log2(fmax * tau / rate)
            if score > best_score:
                best_tau, best_score, best_clar = tau, score, float(rt)
    if best_tau < 0:
        return 0.0, 0.0
    y0, y1, y2 = r_row[best_tau - 1], r_row[best_tau], r_row[best_tau + 1]
    denom = y0 - 2.0 * y1 + y2
    delta = 0.5 * (y0 - y2) / denom if abs(denom) > 1e-12 else 0.0
    delta = float(np.clip(delta, -1.0, 1.0))
    return rate / (best_tau + delta), best_clar


def _postfilter_f0(f0: np.ndarray, voiced: np.ndarray) -> np.ndarray:
    """Median-smooth the voiced F0 series and repair leftover octave jumps (a
    voiced frame more than ~0.4 octave off its local median is halved/doubled
    toward it). Deterministic; a no-op on a steady pitch."""
    out = f0.copy()
    idx = np.flatnonzero(voiced)
    if len(idx) < 3:
        return out
    vals = f0[idx]
    med = _median_filter(vals, 5)
    local = _median_filter(med, 5)
    ratio = np.log2(np.maximum(med, 1e-9) / np.maximum(local, 1e-9))
    med = np.where(ratio > 0.4, med / 2.0, med)
    med = np.where(ratio < -0.4, med * 2.0, med)
    out[idx] = med
    return out


def _analyze(signal: np.ndarray, rate: int, fps: float,
             p: ProsodyParams) -> Tuple[np.ndarray, ...]:
    """One framing + autocorrelation pass -> ``(times, f0, voiced, clarity, rms)``
    — the single source of truth behind :func:`pitch_track` and
    :func:`prosody_features` (so clarity in the bundle is exactly the tracker's).
    ``f0`` is Hz on voiced frames and ``np.nan`` elsewhere. A frame is voiced only
    if it clears **both** gates: RMS above ``silence_frac`` of the clip's
    95th-percentile RMS (an energy floor) **and** autocorrelation clarity at least
    ``voicing_threshold`` (periodicity)."""
    tau_min = int(np.floor(rate / p.fmax))
    tau_max = int(np.ceil(rate / p.fmin))
    N = _frame_length(rate, p.fmin, tau_max)
    times, frames = _frame_signal(signal, rate, fps, N)
    f0 = np.zeros(len(times))
    clarity = np.zeros(len(times))
    if len(signal) >= 2:
        r = _autocorrelation(frames, tau_max)
        for i in range(len(times)):
            f0[i], clarity[i] = _pick_period(r[i], tau_min, tau_max, rate,
                                              p.fmax, p.octave_cost)
    rms = _frame_rms(signal, rate, fps, window=N / float(rate))
    ref = float(np.percentile(rms, _REF_PERCENTILE)) if len(rms) else 0.0
    floor = p.silence_frac * ref
    voiced = (clarity >= p.voicing_threshold) & (rms > floor) & (f0 > 0.0)
    f0 = _postfilter_f0(f0, voiced)
    return times, np.where(voiced, f0, np.nan), voiced, clarity, rms


def pitch_track(
    source: Source,
    rate: Optional[int] = None,
    fps: float = 100.0,
    fmin: float = 80.0,
    fmax: float = 400.0,
    params: Optional[ProsodyParams] = None,
) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Per-frame fundamental frequency of speech, as ``(times, f0, voiced)``.

    ``source`` is a 16-bit PCM WAV path *or* a float sample array in ``[-1, 1]``
    (then ``rate`` is required). ``fps`` is the analysis frame rate (hop) in Hz —
    100 by default, i.e. a 10 ms step; pitch accuracy is set by the window (which
    holds ~2.5 of the lowest period), not by ``fps``. ``f0`` is Hz on voiced
    frames and ``np.nan`` on unvoiced ones; ``voiced`` is the boolean gate. See
    the module docstring for accuracy caveats — this is a heuristic tracker, not
    an ML pitch model.
    """
    p = params or ProsodyParams()
    fmin = float(fmin if fmin is not None else p.fmin)
    fmax = float(fmax if fmax is not None else p.fmax)
    if fmin <= 0 or fmax <= fmin:
        raise ValueError(f"need 0 < fmin < fmax, got fmin={fmin}, fmax={fmax}")
    if isinstance(source, str):
        signal, rate = _read_wav_mono(source)
    else:
        if rate is None:
            raise ValueError("pitch_track needs the sample rate when given samples")
        signal = np.asarray(source, dtype=np.float64)
    times, f0, voiced, _clar, _rms = _analyze(signal, int(rate), fps,
                                              replace(p, fmin=fmin, fmax=fmax))
    return times, f0, voiced


def prosody_features(
    wav_path: str,
    fps: float = 100.0,
    params: Optional[ProsodyParams] = None,
) -> ProsodyTrack:
    """Bundle a WAV's pitch, loudness, voicing/clarity and speaking rate into a
    :class:`ProsodyTrack` at the ``fps`` analysis rate. Reuses
    :func:`openfacefx.energy._frame_rms` for the loudness track, so prosody and
    the energy fallback measure loudness the same way."""
    p = params or ProsodyParams()
    signal, rate = _read_wav_mono(wav_path)
    times, f0, voiced, clarity, rms = _analyze(signal, rate, fps, p)
    rate_hz = _speaking_rate(times, rms, voiced, fps, p)
    return ProsodyTrack(fps=fps, times=times, f0=f0, voiced=voiced, rms=rms,
                        clarity=clarity, speaking_rate=rate_hz)


def _speaking_rate(times: np.ndarray, rms: np.ndarray, voiced: np.ndarray,
                   fps: float, p: ProsodyParams) -> float:
    """Syllables per voiced second: count peaks of a smoothed loudness envelope
    (syllable nuclei) above half the median peak height, spaced by at least
    ``rate_min_spacing``, over the total voiced time. A loudness proxy, not a
    phonetic syllabifier."""
    n = len(rms)
    voiced_s = float(voiced.sum()) / fps
    if n < 3 or voiced_s <= 0.0:
        return 0.0
    k = max(int(round(p.rate_smooth * fps)), 1)
    kern = np.ones(k) / k
    env = np.convolve(rms, kern, mode="same")
    peaks = [(env[i], times[i]) for i in range(1, n - 1)
             if env[i] > env[i - 1] and env[i] >= env[i + 1]]
    if not peaks:
        return 0.0
    thresh = 0.5 * float(np.median([h for h, _ in peaks]))
    peaks = [(h, t) for h, t in peaks if h >= thresh]
    peaks.sort(key=lambda ht: ht[0], reverse=True)
    kept: List[float] = []
    for _, t in peaks:
        if all(abs(t - kt) >= p.rate_min_spacing for kt in kept):
            kept.append(t)
    return len(kept) / voiced_s


# --------------------------------------------------------------------------- #
# Event derivation                                                             #
# --------------------------------------------------------------------------- #

def _fill_short_gaps(values: np.ndarray, voiced: np.ndarray, times: np.ndarray,
                     max_gap: float) -> np.ndarray:
    """Interpolate ``values`` across unvoiced runs shorter than ``max_gap`` (for
    a continuous pitch score through brief consonants); longer runs stay ``nan``."""
    out = np.where(voiced, values, np.nan)
    idx = np.flatnonzero(voiced)
    if len(idx) < 2:
        return out
    i = 0
    while i < len(idx) - 1:
        a, b = idx[i], idx[i + 1]
        if b > a + 1 and (times[b] - times[a]) <= max_gap:
            span = np.arange(a + 1, b)
            out[span] = np.interp(times[span], [times[a], times[b]],
                                  [values[a], values[b]])
        i += 1
    return out


def _runs(mask: np.ndarray) -> List[Tuple[int, int]]:
    """Contiguous ``[start, end)`` index spans where ``mask`` is True."""
    out: List[Tuple[int, int]] = []
    i, n = 0, len(mask)
    while i < n:
        if not mask[i]:
            i += 1
            continue
        j = i
        while j < n and mask[j]:
            j += 1
        out.append((i, j))
        i = j
    return out


def _emphasis_events(track: ProsodyTrack, p: ProsodyParams) -> List[Event]:
    """Beats where pitch and loudness rise together. Prominence
    ``s = 0.6*max(z_f0,0) + 0.4*max(z_e,0)``; a run above ``emph_thresh`` lasting
    ``min_emph`` becomes one emphasis at its peak, strength ``clip(mean s / 3)``.
    Both correlates matter, per the prominence literature (Tamburini/Kochanski)."""
    times, voiced = track.times, track.voiced
    if not voiced.any():
        return []
    lf0 = np.where(voiced, np.log2(np.where(voiced, track.f0, 1.0)), np.nan)
    z_f0_v = _robust_z(lf0[voiced])
    z_f0 = np.full(len(times), np.nan)
    z_f0[voiced] = z_f0_v
    z_f0 = _fill_short_gaps(np.nan_to_num(z_f0), voiced, times, 0.08)
    z_e = _robust_z(20.0 * np.log10(track.rms + 1e-9))
    s = 0.6 * np.maximum(np.nan_to_num(z_f0), 0.0) + 0.4 * np.maximum(z_e, 0.0)

    events: List[Event] = []
    min_len = max(int(round(p.min_emph * track.fps)), 1)
    for a, b in _runs(s >= p.emph_thresh):
        if b - a < min_len:
            continue
        peak = a + int(np.argmax(s[a:b]))
        strength = float(np.clip(s[a:b].mean() / 3.0, 0.0, 1.0))
        f0_peak = float(track.f0[peak]) if voiced[peak] else 0.0
        events.append(Event(
            t=float(times[peak]), type="emphasis", name="emphasis",
            dur=float(times[b - 1] - times[a]),
            payload={"strength": round(strength, 4),
                     "f0": round(f0_peak, 2) if f0_peak > 0 else 0.0}))
    return _merge_close(events, p.emph_merge)


def _merge_close(events: List[Event], gap: float) -> List[Event]:
    """Drop emphases within ``gap`` seconds of a stronger kept one (keep louder)."""
    order = sorted(events, key=lambda e: e.payload.get("strength", 0.0), reverse=True)
    kept: List[Event] = []
    for e in order:
        if all(abs(e.t - k.t) >= gap for k in kept):
            kept.append(e)
    kept.sort(key=lambda e: e.t)
    return kept


def _boundary_spans(track: ProsodyTrack, p: ProsodyParams) -> List[Tuple[float, float]]:
    """``(start, end)`` of each silent pause: an unvoiced-and-quiet run at least
    ``min_pause`` long."""
    ref = float(np.percentile(track.rms, _REF_PERCENTILE)) if len(track.rms) else 0.0
    floor = p.silence_frac * ref
    quiet = (~track.voiced) & (track.rms <= floor)
    spans: List[Tuple[float, float]] = []
    min_len = max(int(round(p.min_pause * track.fps)), 1)
    for a, b in _runs(quiet):
        if b - a >= min_len:
            spans.append((float(track.times[a]), float(track.times[b - 1])))
    return spans


def _phrase_events(spans: List[Tuple[float, float]], end_t: float,
                   p: ProsodyParams) -> List[Event]:
    """A phrase_boundary at each pause plus the utterance end. ``level`` is
    ``clause`` for a short pause, ``sentence`` for a long one (or the end)."""
    events: List[Event] = []
    for a, b in spans:
        dur = b - a
        level = "clause" if dur <= p.clause_max else "sentence"
        events.append(Event(
            t=round((a + b) / 2.0, 6), type="marker", name="phrase_boundary",
            dur=float(dur),
            payload={"level": level, "strength": round(float(np.clip(dur / 0.6, 0.0, 1.0)), 4)}))
    if end_t > 0.0 and not any(abs(end_t - (a + b) / 2.0) < p.min_pause for a, b in spans):
        events.append(Event(t=round(end_t, 6), type="marker",
                            name="phrase_boundary", dur=0.0,
                            payload={"level": "sentence", "strength": 1.0}))
    return events


def _question_events(track: ProsodyTrack, boundaries: List[Event],
                     p: ProsodyParams) -> List[Event]:
    """A question_rise before any boundary whose terminal F0 rises by at least
    ``q_rise`` semitones (least-squares slope > 0 over the last ``q_look`` s).
    Terminal-F0 rise is the canonical yes/no-question cue — and only that, so it
    misses falling *wh*-questions and can false-fire on uptalk/list intonation."""
    times, voiced = track.times, track.voiced
    st = 12.0 * np.log2(np.where(voiced, track.f0, 1.0))  # semitones
    events: List[Event] = []
    for b in boundaries:
        lo = b.t - p.q_look
        sel = (times >= lo) & (times <= b.t) & voiced
        if int(sel.sum()) < p.q_min_voiced:
            continue
        tt, ss = times[sel], st[sel]
        slope = float(np.polyfit(tt, ss, 1)[0])
        net_rise = float(ss[-1] - ss[0])
        if slope > 0.0 and net_rise >= p.q_rise:
            events.append(Event(
                t=round(float(tt[0]), 6), type="marker", name="question_rise",
                dur=float(tt[-1] - tt[0]),
                payload={"net_rise": round(net_rise, 3),
                         "slope": round(slope, 3),
                         "strength": round(float(np.clip(net_rise / 6.0, 0.0, 1.0)), 4)}))
    return events


def detect_events(track: ProsodyTrack, params: Optional[ProsodyParams] = None,
                  segments=None) -> List[Event]:
    """Derive the typed prosodic events from a :class:`ProsodyTrack`: emphasis
    (pitch+loudness beats), phrase_boundary (pauses + utterance end) and
    question_rise (rising terminal F0). Returns a time-sorted ``list[Event]``.

    ``segments`` (optional phoneme segments) currently only sharpen the utterance
    end used for the trailing phrase boundary; pitch/energy drive everything else.
    All events are plain :class:`openfacefx.events.Event`, so they attach and
    serialise exactly like an authored event layer."""
    p = params or ProsodyParams()
    if len(track.times) == 0:
        return []
    end_t = float(segments[-1].end) if segments else float(track.times[-1])
    emphasis = _emphasis_events(track, p)
    spans = _boundary_spans(track, p)
    boundaries = _phrase_events(spans, end_t, p)
    questions = _question_events(track, boundaries, p)
    events = emphasis + boundaries + questions
    events.sort(key=lambda e: e.t)
    return events


def prosody_events(
    wav_path: str,
    fps: float = 100.0,
    segments=None,
    params: Optional[ProsodyParams] = None,
) -> List[Event]:
    """One call from a WAV to typed prosodic events: extract the
    :class:`ProsodyTrack` then :func:`detect_events`. ``fps`` is the analysis rate
    (event times are absolute seconds, independent of any render fps). Reuses
    ``energy.py``'s reader, so the same 16-bit-PCM-WAV constraint applies."""
    p = params or ProsodyParams()
    track = prosody_features(wav_path, fps=fps, params=p)
    return detect_events(track, p, segments=segments)
