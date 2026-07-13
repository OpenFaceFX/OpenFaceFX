"""Adapters for transcript-free **acoustic phoneme recognizers** (issue-tracked).

FaceFX Studio's headline capability is *audio analysis*: recognize the phonemes
straight from a waveform — no transcript required — then drive the rig. OpenFaceFX
is text/alignment-driven, so it has always needed the words. This closes that gap
the way the project integrates every other external tool (cf. :mod:`aligners` for
Whisper/Gentle, :mod:`timing` for the TTS adapters): the ML/DSP recognition runs
in a best-of-breed external recognizer the user already has, and these adapters
parse its **phone + timestamp** output into :class:`~openfacefx.alignment.PhonemeSegment`
s that feed ``generate_from_alignment`` directly — **no ``--text``**. The core
stays numpy + stdlib, deterministic and headless-verifiable; the neural part lives
outside, exactly like Whisper/WhisperX/Gentle.

  * :func:`from_allosaurus` — `Allosaurus <https://github.com/xinjli/allosaurus>`_
    ``--timestamp true`` output: one ``start duration phone`` line per phone
    (seconds, space-separated, **IPA**), e.g. ``0.210 0.045 æ``. Gaps between
    phones become silence. This is the universal-phone-recognizer path.
  * :func:`from_phone_timestamps` — the generic adapter for any recognizer that can
    emit phone timings (wav2vec2 phoneme-CTC, PocketSphinx ``-allphone``, a custom
    exporter): a text/TSV block, a JSON array, or an iterable of
    ``(start, end|duration, phone)`` rows, with a selectable ``alphabet``
    (``ipa``/``arpabet``/``sampa``) and ``timing`` (``start_end``/``start_dur``).

A phone outside the recognized inventory passes through and falls to ``sil`` at the
viseme stage (the documented :mod:`aligners` rule), never a crash. Timestamps from
CTC recognizers are approximate — Allosaurus says so itself; a downstream
``--smooth`` or an anchored re-time can clean them up.
"""

from __future__ import annotations

import json as _json
from typing import Iterable, List, Sequence, Tuple, Union

from .alignment import PhonemeSegment
from .phonemes import ALPHABETS, SILENCE, from_alphabet

_TIMINGS = ("start_end", "start_dur")
_Row = Tuple[object, object, object]         # (start, end-or-duration, phone)


def _is_number(s: str) -> bool:
    try:
        float(s)
        return True
    except ValueError:
        return False


def _finite(x, what: str) -> float:
    try:
        v = float(x)
    except (TypeError, ValueError):
        raise ValueError(f"{what}: not a number ({x!r})") from None
    if v != v or v in (float("inf"), float("-inf")):
        raise ValueError(f"{what}: not finite ({x!r})")
    return v


def _build_segments(rows: Iterable[_Row], *, alphabet: str, timing: str,
                    fill_silence: bool, who: str) -> List[PhonemeSegment]:
    """Shared core: ``(start, end|dur, phone)`` rows -> contiguous PhonemeSegments.

    Phones convert from ``alphabet`` into the internal ARPAbet inventory; a gap
    before a phone becomes silence; a mild CTC overlap (a phone whose start dips
    below the previous end) is clamped so segments never overlap."""
    if alphabet not in ALPHABETS:
        raise ValueError(f"{who}: unknown alphabet {alphabet!r} (use {ALPHABETS})")
    if timing not in _TIMINGS:
        raise ValueError(f"{who}: unknown timing {timing!r} (use {_TIMINGS})")
    segs: List[PhonemeSegment] = []
    cursor = 0.0
    for i, row in enumerate(rows):
        start_raw, second_raw, phone = row
        start = _finite(start_raw, f"{who} phone {i} start")
        field = "duration" if timing == "start_dur" else "end"
        second = _finite(second_raw, f"{who} phone {i} {field}")
        end = start + second if timing == "start_dur" else second
        if start < -1e-9:
            raise ValueError(f"{who} phone {i}: negative start {start}")
        if end < start - 1e-9:
            raise ValueError(f"{who} phone {i}: {field} {second} yields end "
                             f"{end:.6f} before start {start:.6f}")
        sym = from_alphabet(str(phone).strip(), alphabet) or SILENCE
        if fill_silence and start > cursor + 1e-9:
            segs.append(PhonemeSegment(SILENCE, cursor, start))
            seg_start = start
        else:
            seg_start = max(start, cursor)         # contiguous / clamp overlap
        segs.append(PhonemeSegment(sym, seg_start, max(end, seg_start)))
        cursor = max(end, seg_start)
    if not segs:
        raise ValueError(f"{who}: no phones found (is the recognizer output empty?)")
    return segs


def from_allosaurus(text: str, *, fill_silence: bool = True) -> List[PhonemeSegment]:
    """Allosaurus ``--timestamp true`` output -> phone-level :class:`PhonemeSegment` s.

    Each non-blank line is ``start duration phone`` in seconds (space-separated,
    IPA), e.g. ``0.210 0.045 æ``. Requires the *timestamped* output — the plain
    space-separated phone string carries no timing. Gaps between phones become
    silence; feeds ``generate_from_alignment`` with no transcript."""
    rows: List[_Row] = []
    for lineno, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line:
            continue
        parts = line.split(None, 2)
        if len(parts) < 3 or not _is_number(parts[0]) or not _is_number(parts[1]):
            raise ValueError(
                f"allosaurus line {lineno}: expected 'start duration phone' — run "
                f"allosaurus with --timestamp true — got {raw!r}")
        rows.append((parts[0], parts[1], parts[2]))
    return _build_segments(rows, alphabet="ipa", timing="start_dur",
                           fill_silence=fill_silence, who="allosaurus")


def _rows_from_json(data: str, timing: str) -> List[_Row]:
    obj = _json.loads(data)
    if isinstance(obj, dict):
        obj = obj.get("phones") or obj.get("segments") or obj.get("result") or []
    if not isinstance(obj, list):
        raise ValueError("phones: JSON must be an array of phone objects (or an "
                         "object with a 'phones'/'segments' array)")
    field = "duration" if timing == "start_dur" else "end"
    out: List[_Row] = []
    for i, r in enumerate(obj):
        if not isinstance(r, dict):
            raise ValueError(f"phones: JSON item {i} is not an object")
        start = r.get("start", r.get("start_time"))
        second = r.get(field, r.get(field + "_time"))
        phone = r.get("phone", r.get("phoneme", r.get("label", r.get("symbol"))))
        if start is None or second is None or phone is None:
            raise ValueError(f"phones: JSON item {i} needs 'start', {field!r} and "
                             f"'phone' (got keys {sorted(r)})")
        out.append((start, second, phone))
    return out


def _rows_from_text(data: str, timing: str) -> List[_Row]:
    field = "duration" if timing == "start_dur" else "end"
    out: List[_Row] = []
    for lineno, raw in enumerate(data.splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.replace("\t", " ").split(None, 2)
        if len(parts) < 3:
            raise ValueError(f"phones line {lineno}: expected 'start {field} phone', "
                             f"got {raw!r}")
        out.append((parts[0], parts[1], parts[2]))
    return out


def from_phone_timestamps(data: Union[str, Iterable[Sequence]], *,
                          alphabet: str = "ipa", timing: str = "start_end",
                          fill_silence: bool = True) -> List[PhonemeSegment]:
    """Generic acoustic phone-timing adapter (wav2vec2 CTC / PocketSphinx allphone /
    any recognizer) -> :class:`PhonemeSegment` s.

    ``data`` is a JSON string (array of ``{start, end|duration, phone}`` objects),
    a text/TSV block (``start <end|dur> phone`` per line, ``#`` comments allowed),
    or an iterable of ``(start, end|duration, phone)`` rows. ``alphabet`` is one of
    ``ipa``/``arpabet``/``sampa`` (default ``ipa``); ``timing`` is ``start_end`` or
    ``start_dur`` (default ``start_end``, the common CTC-export convention)."""
    if isinstance(data, str):
        rows: Iterable[_Row] = (_rows_from_json(data, timing)
                                if data.lstrip()[:1] in "[{"
                                else _rows_from_text(data, timing))
    else:
        rows = [tuple(r) for r in data]
    return _build_segments(rows, alphabet=alphabet, timing=timing,
                           fill_silence=fill_silence, who="phones")
