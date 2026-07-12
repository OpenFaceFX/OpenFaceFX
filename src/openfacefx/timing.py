"""Normalized TTS timing schema + vendor adapters (issue #14).

TTS engines already know exactly when each phoneme or viseme happens, so they
can replace the aligner entirely. This module defines one intermediate schema --
``TimingEvent(unit, symbol, start, end)`` -- and parsers that turn each vendor's
dump into a list of them. From there:

  * phoneme-unit events -> ``to_segments`` -> the existing weighted mapping and
    coarticulation, unchanged. The symbol is the phoneme label verbatim, so the
    source alphabet must match the mapping's (ARPABET by default; Piper/Cartesia
    IPA and MBROLA SAMPA want a matching ``--mapping`` / ``allow_custom_symbols``).
  * viseme-unit events (Azure, Polly, VOICEVOX) skip phoneme->target mapping: the
    vendor's fixed symbol set remaps straight onto the Oculus-15 targets via
    ``AZURE_VISEME_TO_TARGET`` / ``POLLY_VISEME_TO_TARGET`` / ``VOICEVOX_TO_TARGET``
    (VOICEVOX's are OpenJTalk phonemes) and coarticulates via a custom-symbol Mapping.

Only start times are guaranteed; ``resolve_ends`` fills any missing end from the
next event's start (Azure/Polly), with a configurable final-event duration.

Time units, all -> float seconds: MBROLA .pho ms (cumulative); Piper sample-counts
/ sample_rate; Cartesia start/end seconds; Azure 100-ns ticks (/1e4 = ms); Polly
ms; VOICEVOX per-mora seconds ÷ speedScale. Parsers are pure stdlib text/JSON (no
numpy), reject malformed input with a clear ValueError, and accept the obvious
field-name aliases (capture scripts: docs/timing.md).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from .alignment import PhonemeSegment
from .mapping import Mapping, Target, _DEFAULT_CLASSES
from .phonemes import SILENCE
from .visemes import VISEMES


@dataclass
class TimingEvent:
    unit: str                       # 'phoneme' or 'viseme'
    symbol: str                     # phoneme label or vendor viseme symbol
    start: float                    # seconds
    end: Optional[float] = None     # seconds; None until resolve_ends fills it

    def __post_init__(self):
        # reject NaN/Infinity (json.loads parses both) — a non-finite time crashes the solver
        for lbl, x in (("start", self.start), ("end", self.end)):
            if x is not None and not (float("-inf") < x < float("inf")):
                raise ValueError(f"TimingEvent {lbl} must be finite, got {x!r}")


def resolve_ends(events: List[TimingEvent],
                 final_duration: float = 0.08) -> List[TimingEvent]:
    """Fill every ``end is None`` from the next event's start; the last such
    event gets ``start + final_duration``. Events that already carry an end (the
    duration / explicit-end sources) are left untouched. Order is preserved."""
    if final_duration <= 0.0:
        raise ValueError("final_duration must be > 0")
    out: List[TimingEvent] = []
    n = len(events)
    for i, e in enumerate(events):
        end = e.end
        if end is None:
            end = events[i + 1].start if i + 1 < n else e.start + final_duration
        if end < e.start:                # guard out-of-order / coincident starts
            end = e.start
        out.append(TimingEvent(e.unit, e.symbol, e.start, end))
    return out


def to_segments(events: List[TimingEvent]) -> List[PhonemeSegment]:
    """Phoneme-unit events -> ``PhonemeSegment`` list (ends must be resolved).
    The symbol becomes the phoneme label verbatim."""
    segs: List[PhonemeSegment] = []
    for e in events:
        if e.unit != "phoneme":
            raise ValueError(
                f"to_segments: expected phoneme-unit events, got {e.unit!r}")
        if e.end is None:
            raise ValueError(
                "to_segments: unresolved end (call resolve_ends first)")
        segs.append(PhonemeSegment(e.symbol, e.start, e.end))
    return segs


# --------------------------------------------------------------------------- #
# Parsers. Each returns a list of TimingEvent in source order.                 #
# --------------------------------------------------------------------------- #

def parse_pho(text: str) -> List[TimingEvent]:
    """MBROLA .pho: one ``PHONEME DURATION_MS [pos% pitch_hz ...]`` per line.
    ``;`` starts a comment; blank and comment lines are ignored. Durations are
    cumulative from t=0; the trailing pitch-target pairs are ignored (only the
    timing matters). Symbols are the MBROLA voice's alphabet (a SAMPA variant),
    not ARPABET."""
    events: List[TimingEvent] = []
    t = 0.0
    for lineno, raw in enumerate(text.splitlines(), 1):
        line = raw.split(";", 1)[0].strip()
        if not line:
            continue
        parts = line.split()
        if len(parts) < 2:
            raise ValueError(
                f".pho line {lineno}: expected 'PHONEME DURATION_MS', got {raw!r}")
        try:
            dur_ms = float(parts[1])
        except ValueError:
            raise ValueError(
                f".pho line {lineno}: duration {parts[1]!r} is not a number"
            ) from None
        if dur_ms < 0.0:
            raise ValueError(f".pho line {lineno}: negative duration {dur_ms}")
        start = t
        t += dur_ms / 1000.0
        events.append(TimingEvent("phoneme", parts[0], start, t))
    if not events:
        raise ValueError(".pho: no phoneme lines found")
    return events


def parse_piper_alignments(json_text: str, sample_rate: int) -> List[TimingEvent]:
    """Piper per-phoneme alignments: audio **sample counts** -> seconds via the
    voice ``sample_rate``. Accepts either parallel arrays
    ``{"phonemes": [...], "phoneme_id_samples": [...]}`` (the Python AudioChunk
    attribute names; ``alignments`` / ``samples`` are accepted aliases) or a list
    ``{"alignments": [{"phoneme": "h", "num_samples": 2205}, ...]}``. Counts are
    cumulative from t=0. Piper's docs name the fields but ship no example JSON, so
    both shapes are supported; the sample-count semantics are documented."""
    if sample_rate <= 0:
        raise ValueError("sample_rate must be a positive integer")
    try:
        d = json.loads(json_text)
    except json.JSONDecodeError as e:
        raise ValueError(f"piper: not valid JSON ({e})") from None
    phonemes, samples = _piper_arrays(d)
    if len(phonemes) != len(samples):
        raise ValueError(
            f"piper: {len(phonemes)} phonemes but {len(samples)} sample counts")
    if not phonemes:
        raise ValueError("piper: no phonemes in alignment dump")
    events: List[TimingEvent] = []
    t = 0.0
    for ph, ns in zip(phonemes, samples):
        if isinstance(ns, bool) or not isinstance(ns, (int, float)) or ns < 0:
            raise ValueError(f"piper: bad sample count {ns!r} for phoneme {ph!r}")
        start = t
        t += ns / sample_rate
        events.append(TimingEvent("phoneme", str(ph), start, t))
    return events


def _piper_arrays(d) -> Tuple[list, list]:
    aligns = d.get("alignments") if isinstance(d, dict) else None
    if isinstance(aligns, list) and aligns and isinstance(aligns[0], dict):
        try:
            phonemes = [r["phoneme"] for r in aligns]
        except (KeyError, TypeError) as e:
            raise ValueError(f"piper: alignment entry missing 'phoneme' ({e})") from None
        samples = [r.get("num_samples", r.get("samples")) for r in aligns]
        if any(s is None for s in samples):
            raise ValueError("piper: alignment entry missing 'num_samples'")
        return phonemes, samples
    if not isinstance(d, dict):
        raise ValueError("piper: expected a JSON object")
    phonemes = d.get("phonemes")
    samples = d.get("phoneme_id_samples")
    if samples is None:
        samples = d.get("alignments")
    if samples is None:
        samples = d.get("samples")
    if not isinstance(phonemes, list) or not isinstance(samples, list):
        raise ValueError(
            "piper: expected 'phonemes' and 'phoneme_id_samples' arrays")
    return phonemes, samples


def parse_cartesia(json_text: str) -> List[TimingEvent]:
    """Cartesia ``phoneme_timestamps``: parallel ``phonemes`` / ``start`` /
    ``end`` arrays already in **seconds**. Accepts the full stream message
    ``{"phoneme_timestamps": {...}}`` or a bare object carrying the three
    arrays. Symbols are IPA."""
    try:
        d = json.loads(json_text)
    except json.JSONDecodeError as e:
        raise ValueError(f"cartesia: not valid JSON ({e})") from None
    if isinstance(d, dict) and "phoneme_timestamps" in d:
        d = d["phoneme_timestamps"]
    if not isinstance(d, dict):
        raise ValueError("cartesia: expected a 'phoneme_timestamps' object")
    phonemes, starts, ends = d.get("phonemes"), d.get("start"), d.get("end")
    if not (isinstance(phonemes, list) and isinstance(starts, list)
            and isinstance(ends, list)):
        raise ValueError(
            "cartesia: 'phonemes', 'start' and 'end' arrays are required")
    if not len(phonemes) == len(starts) == len(ends):
        raise ValueError("cartesia: phonemes/start/end arrays differ in length")
    if not phonemes:
        raise ValueError("cartesia: empty phoneme_timestamps")
    events: List[TimingEvent] = []
    for ph, s, e in zip(phonemes, starts, ends):
        try:
            s, e = float(s), float(e)
        except (TypeError, ValueError):
            raise ValueError(
                f"cartesia: non-numeric time for phoneme {ph!r}") from None
        events.append(TimingEvent("phoneme", str(ph), s, e))
    return events


_AZURE_OFFSET_KEYS = ("audio_offset", "offset", "AudioOffset", "audioOffset")
_AZURE_ID_KEYS = ("viseme_id", "id", "VisemeId", "visemeId")


def parse_azure_visemes(json_text: str) -> List[TimingEvent]:
    """Azure VisemeReceived events: audio offset in **100-ns ticks**
    (ticks / 10000 = ms) + integer viseme ID (documented range 0-21). Accepts a
    bare JSON array of events or ``{"visemes"|"events": [...]}``; each event
    needs an offset (``audio_offset``) and an id (``viseme_id``). Ends come from
    the next event (``resolve_ends``). Symbol is ``str(id)``; IDs outside the
    documented table are kept here and flagged as unknown at mapping time."""
    try:
        d = json.loads(json_text)
    except json.JSONDecodeError as e:
        raise ValueError(f"azure: not valid JSON ({e})") from None
    items = _event_list(d, ("visemes", "events"), "azure")
    events: List[TimingEvent] = []
    for i, ev in enumerate(items):
        if not isinstance(ev, dict):
            raise ValueError(f"azure: event {i} is not an object")
        ticks = _first_key(ev, _AZURE_OFFSET_KEYS)
        vid = _as_int(_first_key(ev, _AZURE_ID_KEYS))
        if ticks is None or vid is None:
            raise ValueError(
                f"azure: event {i} needs a numeric offset and integer id, got {ev!r}")
        try:
            ticks = float(ticks)
        except (TypeError, ValueError):
            raise ValueError(
                f"azure: event {i} offset {ticks!r} not numeric") from None
        events.append(TimingEvent("viseme", str(vid), ticks / 1e7, None))
    if not events:
        raise ValueError("azure: no viseme events found")
    return events


def parse_polly_marks(text: str) -> List[TimingEvent]:
    """Amazon Polly speech marks: newline-delimited JSON, one object per line.
    Only ``type == 'viseme'`` lines are kept; ``time`` is integer
    **milliseconds** and ``value`` the viseme symbol. A mark's ``start``/``end``
    are byte offsets into the input text, not time, and are ignored. Ends come
    from the next viseme mark (``resolve_ends``)."""
    events: List[TimingEvent] = []
    for lineno, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line:
            continue
        try:
            mark = json.loads(line)
        except json.JSONDecodeError as e:
            raise ValueError(f"polly line {lineno}: not valid JSON ({e})") from None
        if not isinstance(mark, dict) or "type" not in mark:
            raise ValueError(f"polly line {lineno}: not a speech-mark object")
        if mark["type"] != "viseme":
            continue
        if "time" not in mark or "value" not in mark:
            raise ValueError(
                f"polly line {lineno}: viseme mark needs 'time' and 'value'")
        try:
            ms = float(mark["time"])
        except (TypeError, ValueError):
            raise ValueError(
                f"polly line {lineno}: time {mark['time']!r} not numeric") from None
        events.append(TimingEvent("viseme", str(mark["value"]), ms / 1000.0, None))
    if not events:
        raise ValueError("polly: no viseme marks found")
    return events


def parse_voicevox(json_text: str) -> List[TimingEvent]:
    """VOICEVOX ``/audio_query`` JSON -> viseme-unit events (OpenJTalk phoneme
    symbols, remapped by :data:`VOICEVOX_TO_TARGET`; also covers the API-compatible
    forks COEIROINK / SHAREVOX / LMROID / AivisSpeech).

    All durations are **seconds** ÷ ``speedScale``. The first phoneme starts at
    ``prePhonemeLength``; each mora advances by ``consonant_length`` (when present)
    then ``vowel_length``; a ``pause_mora`` gap is ``(pauseLength if set else
    pause_mora.vowel_length) * pauseLengthScale`` — the VOICEVOX pause-override
    fields (#59), matching the engine's replace-then-scale order and both
    defaulting to a no-op; a trailing ``postPhonemeLength`` closes it. Unvoiced
    (uppercase) vowels still allocate time. Ends are set here (``resolve_ends`` is
    a no-op); unmapped symbols route to silence."""
    try:
        d = json.loads(json_text)
    except json.JSONDecodeError as e:
        raise ValueError(f"voicevox: not valid JSON ({e})") from None
    if not isinstance(d, dict) or not isinstance(d.get("accent_phrases"), list):
        raise ValueError(
            "voicevox: expected an AudioQuery object with an 'accent_phrases' array")
    try:
        raw_speed = d.get("speedScale", 1.0)             # explicit null -> 1.0
        speed = 1.0 if raw_speed is None else float(raw_speed)  # keep a real 0.0
        pre = float(d.get("prePhonemeLength") or 0.0)
        post = float(d.get("postPhonemeLength") or 0.0)
        pl, ps = d.get("pauseLength"), d.get("pauseLengthScale")
        pause_len = None if pl is None else float(pl)    # None -> use pause_mora len
        pause_scale = 1.0 if ps is None else float(ps)   # both default to a no-op
    except (TypeError, ValueError):
        raise ValueError("voicevox: non-numeric speedScale / pre|post|pause field") from None
    if speed <= 0.0:
        raise ValueError(f"voicevox: speedScale must be > 0, got {speed!r}")
    if pause_scale < 0.0 or (pause_len is not None and pause_len < 0.0):
        raise ValueError("voicevox: pauseLength / pauseLengthScale must be >= 0")

    events: List[TimingEvent] = []
    clock = [0.0]

    def emit(symbol: str, dur) -> None:
        try:
            step = float(dur) / speed
        except (TypeError, ValueError):
            raise ValueError(f"voicevox: non-numeric duration {dur!r} for {symbol!r}") from None
        if step < 0.0:                                # negative mora/pause length
            raise ValueError(f"voicevox: negative duration {dur!r} for {symbol!r}")
        events.append(TimingEvent("viseme", symbol, clock[0], clock[0] + step))
        clock[0] += step

    if pre > 0.0:
        emit("pau", pre)
    for ap in d["accent_phrases"]:
        if not isinstance(ap, dict):
            raise ValueError("voicevox: each accent_phrase must be an object")
        for mora in ap.get("moras", []):
            if mora.get("consonant") and mora.get("consonant_length") is not None:
                emit(str(mora["consonant"]), mora["consonant_length"])
            if mora.get("vowel") is None or mora.get("vowel_length") is None:
                raise ValueError(f"voicevox: mora needs 'vowel' and 'vowel_length': {mora!r}")
            emit(str(mora["vowel"]), mora["vowel_length"])
        pause = ap.get("pause_mora")
        if isinstance(pause, dict) and pause.get("vowel_length") is not None:
            # honor the pauseLength / pauseLengthScale overrides (#59); /speed in emit
            base = pause_len if pause_len is not None else float(pause["vowel_length"])
            emit(str(pause.get("vowel") or "pau"), base * pause_scale)
    if post > 0.0:
        emit("pau", post)
    if not events:
        raise ValueError("voicevox: no moras found in accent_phrases")
    return events


def _event_list(d, keys, who):
    if isinstance(d, list):
        return d
    if isinstance(d, dict):
        for k in keys:
            if isinstance(d.get(k), list):
                return d[k]
    raise ValueError(f"{who}: expected a JSON array of events")


def _first_key(d, keys):
    for k in keys:
        if k in d:
            return d[k]
    return None


def _as_int(x) -> Optional[int]:
    if isinstance(x, bool):
        return None
    if isinstance(x, int):
        return x
    if isinstance(x, float) and x.is_integer():
        return int(x)
    return None


# --------------------------------------------------------------------------- #
# Viseme-unit remap presets: vendor viseme symbol -> Oculus-15 target.         #
# --------------------------------------------------------------------------- #

# Azure's 22 viseme IDs, from the documented IPA groupings
# (learn.microsoft.com/.../how-to-speech-synthesis-viseme). Where one viseme
# lumps sounds that split across Oculus targets (4 = e/U, 19 = d/t/n/theta) the
# representative articulation is chosen.
AZURE_VISEME_TO_TARGET: Dict[int, str] = {
    0: "sil",    # silence
    1: "aa",     # ae schwa caret
    2: "aa",     # alpha
    3: "O",      # open-o
    4: "E",      # epsilon (and U)
    5: "RR",     # r-colored schwa
    6: "I",      # j i small-cap-I
    7: "U",      # w u
    8: "O",      # o
    9: "O",      # a-u diphthong
    10: "O",     # open-o-i diphthong
    11: "aa",    # a-i diphthong
    12: "kk",    # h
    13: "RR",    # turned-r
    14: "DD",    # l
    15: "SS",    # s z
    16: "CH",    # esh, t-esh, d-ezh, ezh
    17: "TH",    # eth
    18: "FF",    # f v
    19: "DD",    # d t n theta
    20: "kk",    # k g eng
    21: "PP",    # p b m
}

# Amazon Polly en-US viseme symbols
# (docs.aws.amazon.com/polly/.../ph-table-english-us.html). Polly also emits
# "sil" for silence. Symbols are case-significant: s != S, t != T, e != E, o != O.
POLLY_VISEME_TO_TARGET: Dict[str, str] = {
    "sil": "sil",
    "p": "PP",   # p b m
    "t": "DD",   # d t n
    "S": "CH",   # esh, t-esh, d-ezh, ezh
    "T": "TH",   # theta eth
    "f": "FF",   # f v
    "k": "kk",   # k g h eng
    "i": "I",    # i small-cap-I j
    "r": "RR",   # turned-r
    "s": "SS",   # s z
    "u": "U",    # u U w
    "@": "aa",   # schwa, r-colored schwa
    "a": "aa",   # ae, a-i, a-u, alpha
    "e": "E",    # e-i diphthong
    "E": "E",    # epsilon, caret, r-colored schwa
    "o": "O",    # o-u diphthong
    "O": "O",    # open-o, open-o-i
    "l": "DD",   # l
}


# VOICEVOX / OpenJTalk phoneme -> Oculus-15 target (the same direct-remap idea as
# the Azure/Polly tables). Vowels follow VOICEVOX's own OVR-LipSync vowels
# (uppercase = unvoiced, same shape); N -> nn; cl/pau -> sil; consonants collapse
# to the nearest native viseme, palatalized -y variants following their base.
# Symbols absent here route to silence with a QA warning, never a crash.
VOICEVOX_TO_TARGET: Dict[str, str] = {
    "pau": "sil", "cl": "sil", "sil": "sil",
    "a": "aa", "A": "aa", "i": "I", "I": "I", "u": "U", "U": "U",
    "e": "E", "E": "E", "o": "O", "O": "O", "N": "nn",
    "p": "PP", "py": "PP", "b": "PP", "by": "PP", "m": "PP", "my": "PP",
    "f": "FF", "v": "FF",
    "t": "DD", "d": "DD", "dy": "DD", "n": "nn", "ny": "nn",
    "s": "SS", "z": "SS", "ts": "SS",
    "sh": "CH", "j": "CH", "ch": "CH",
    "k": "kk", "ky": "kk", "g": "kk", "gy": "kk", "h": "kk", "hy": "kk",
    "r": "RR", "ry": "RR", "w": "RR", "y": "I",
}


def _str_table(table: Dict) -> Dict[str, str]:
    """Normalize table keys to str so Azure's int IDs and Polly's str symbols
    share one code path (events carry ``str(id)``)."""
    return {str(k): v for k, v in table.items()}


def build_vendor_mapping(table: Dict) -> Mapping:
    """A ``Mapping`` over the Oculus-15 targets whose rows are the vendor viseme
    symbols, each driving its target at weight 1.0. ``allow_custom_symbols`` lets
    numeric Azure IDs and case-significant Polly letters through verbatim. A
    ``sil`` fallback row absorbs any symbol routed to silence."""
    table = _str_table(table)
    targets = [Target(v, _DEFAULT_CLASSES.get(v, "basic")) for v in VISEMES]
    rows: Dict[str, Dict[str, float]] = {s: {t: 1.0} for s, t in table.items()}
    rows[SILENCE] = {"sil": 1.0}
    return Mapping(targets, rows, allow_custom_symbols=True)


def viseme_events_to_segments(
    events: List[TimingEvent], table: Dict,
) -> Tuple[List[PhonemeSegment], List[str]]:
    """Resolved viseme events -> (segments, warnings). Each segment's phoneme is
    the vendor symbol, consumed by ``build_vendor_mapping(table)``. Symbols
    absent from ``table`` are routed to silence and reported as QA warnings
    (never a crash), per the acceptance criteria."""
    known = _str_table(table)
    segs: List[PhonemeSegment] = []
    unknown: Dict[str, int] = {}
    for e in events:
        if e.unit != "viseme":
            raise ValueError(
                f"viseme_events_to_segments: expected viseme-unit events, "
                f"got {e.unit!r}")
        if e.end is None:
            raise ValueError(
                "viseme_events_to_segments: unresolved end (call resolve_ends first)")
        if e.symbol in known:
            segs.append(PhonemeSegment(e.symbol, e.start, e.end))
        else:
            unknown[e.symbol] = unknown.get(e.symbol, 0) + 1
            segs.append(PhonemeSegment(SILENCE, e.start, e.end))
    warnings = [f"unknown viseme symbol {s!r} ({n}x) routed to silence"
                for s, n in sorted(unknown.items())]
    return segs, warnings
