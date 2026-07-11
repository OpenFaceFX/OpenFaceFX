"""First-class adapters for the free, open-source aligners / ASR (issue #54).

OpenFaceFX ships timing adapters for every commercial TTS source (Azure,
ElevenLabs, Kokoro, Google, Piper, Cartesia, Polly) but used to punt the
open-source tools — Whisper, WhisperX, Gentle — to the user with a "write a
~15-line adapter" note in three places. These are those adapters: siblings of
:func:`openfacefx.anchors.from_azure_word_boundaries`, stdlib ``json`` only,
returning the normalized :class:`~openfacefx.anchors.Anchor` list (or, for
Gentle's phone timings, :class:`~openfacefx.alignment.PhonemeSegment` s directly).

  * :func:`from_whisper_json` — OpenAI Whisper ``verbose_json`` word timestamps
    (``segments[].words[]`` or a top-level ``words[]``). **Tolerant** of the key
    variance across openai-whisper / faster-whisper / whisper.cpp (``word`` vs
    ``text``, ``probability`` vs ``score``).
  * :func:`from_whisperx` — WhisperX ``segments[].words[]`` (``word``/``start``/
    ``end``/``score``).
  * :func:`from_gentle` — Gentle ``words[]`` (word-level anchors); and
    :func:`from_gentle_phones` — Gentle's per-word ``phones[]`` (relative
    ``duration`` s, ARPAbet with ``_B``/``_I``/``_E``/``_S`` position suffixes)
    accumulated from the word start into phone-level segments, a path that skips
    the naive spacer entirely.

**Deterministic missing-timestamp rule:** aligners leave some words unaligned
(Whisper omits timestamps, Gentle marks ``case != "success"``). Such a word is
**dropped** — its neighbours' anchors still pin the timeline and the aligner
spreads the gap. A phone/word symbol outside the ARPAbet inventory passes through
and falls to ``sil`` at the viseme stage (documented), never a crash.
"""

from __future__ import annotations

import re
from typing import List

from .alignment import PhonemeSegment
from .anchors import Anchor, _first, _json, _num, _obj_list
from .phonemes import SILENCE

_HAS_WORD_CHAR = re.compile(r"\w", re.UNICODE)      # skip pure-punctuation tokens


def _word_anchors(words, who: str) -> List[Anchor]:
    """Shared word-list -> ``Anchor`` core for Whisper / WhisperX. A word with a
    missing/null ``start`` (the aligner left it unaligned) or that is
    punctuation-only is dropped; ``end`` may be ``None`` (inferred from the next
    anchor). Tolerant of ``word``/``text`` keys."""
    out: List[Anchor] = []
    for i, w in enumerate(words):
        if not isinstance(w, dict):
            raise ValueError(f"{who}: word {i} is not an object")
        text = _first(w, ("word", "text"))
        if not isinstance(text, str):
            raise ValueError(f"{who}: word {i} needs a 'word'/'text' string")
        text = text.strip()
        start = _first(w, ("start", "start_time"))
        if start is None or not _HAS_WORD_CHAR.search(text):
            continue                              # unaligned / punctuation -> drop
        end = _first(w, ("end", "end_time"))
        out.append(Anchor(text, _num(start, f"{who} word {i} 'start'"),
                          None if end is None else _num(end, f"{who} word {i} 'end'")))
    if not out:
        raise ValueError(f"{who}: no aligned words found")
    return out


def _segment_words(segments, who: str) -> list:
    return [w for seg in segments if isinstance(seg, dict)
            for w in (seg.get("words") or [])]


def from_whisper_json(json_text: str) -> List[Anchor]:
    """OpenAI Whisper ``verbose_json`` (``word_timestamps=True``) -> word anchors.

    Accepts the ``{"segments": [{"words": [...]}]}`` shape and the flat
    ``{"words": [...]}`` / bare-array shapes different wrappers emit, and the
    ``word``/``text`` + ``probability``/``score`` key variance across
    openai-whisper, faster-whisper and whisper.cpp. Words Whisper left without a
    timestamp are dropped."""
    d = _json(json_text, "whisper")
    if isinstance(d, dict) and isinstance(d.get("segments"), list):
        words = _segment_words(d["segments"], "whisper")
    elif isinstance(d, dict) and isinstance(d.get("words"), list):
        words = d["words"]
    elif isinstance(d, list):
        words = d
    else:
        raise ValueError("whisper: expected 'segments[].words[]', a 'words[]' "
                         "array, or a bare word array")
    return _word_anchors(words, "whisper")


def from_whisperx(json_text: str) -> List[Anchor]:
    """WhisperX alignment (``segments[].words[]`` with ``word``/``start``/``end``/
    ``score``) -> word anchors. Words WhisperX could not align (no ``start``) are
    dropped, the same deterministic rule as :func:`from_whisper_json`."""
    d = _json(json_text, "whisperx")
    if not (isinstance(d, dict) and isinstance(d.get("segments"), list)):
        raise ValueError("whisperx: expected a 'segments' array")
    return _word_anchors(_segment_words(d["segments"], "whisperx"), "whisperx")


def from_gentle(json_text: str) -> List[Anchor]:
    """Gentle forced-aligner JSON (``words[]``) -> word anchors. Only
    ``case == "success"`` words are kept; ``not-found-in-audio`` /
    ``not-found-in-transcript`` words fall out as gaps."""
    items = _obj_list(_json(json_text, "gentle"), ("words",), "gentle")
    out: List[Anchor] = []
    for i, w in enumerate(items):
        if not isinstance(w, dict):
            raise ValueError(f"gentle: word {i} is not an object")
        if w.get("case") != "success":
            continue
        text = _first(w, ("alignedWord", "word"))
        start = _first(w, ("start",))
        if not isinstance(text, str) or start is None:
            continue
        end = _first(w, ("end",))
        out.append(Anchor(text, _num(start, f"gentle word {i} 'start'"),
                          None if end is None else _num(end, f"gentle word {i} 'end'")))
    if not out:
        raise ValueError("gentle: no successfully-aligned words found")
    return out


_GENTLE_SUFFIX = re.compile(r"_[BIES]$")


def _gentle_phone(symbol: str) -> str:
    """Gentle phone token -> internal ARPAbet: strip the ``_B``/``_I``/``_E``/
    ``_S`` position suffix and upper-case (``hh_B`` -> ``HH``, ``ow1_E`` ->
    ``OW1``); Gentle's silence / out-of-vocab tokens map to ``sil``."""
    ph = _GENTLE_SUFFIX.sub("", symbol).upper()
    return SILENCE if ph in ("SIL", "OOV", "SP", "") else ph


def from_gentle_phones(json_text: str) -> List[PhonemeSegment]:
    """Gentle ``words[].phones[]`` -> phone-level :class:`PhonemeSegment` s.

    Each successful word's phones carry a relative ``duration``; accumulating
    them from the word's ``start`` gives absolute phone times (so the last phone
    ends at the word span within float tolerance). Gaps between successful words
    become silence. This is the accurate phone path — it skips the naive spacer
    and feeds ``generate_from_alignment`` directly."""
    items = _obj_list(_json(json_text, "gentle-phones"), ("words",), "gentle-phones")
    segs: List[PhonemeSegment] = []
    cursor = 0.0
    for i, w in enumerate(items):
        if not isinstance(w, dict) or w.get("case") != "success":
            continue
        start = _first(w, ("start",))
        phones = w.get("phones")
        if start is None or not isinstance(phones, list) or not phones:
            continue
        t = _num(start, f"gentle-phones word {i} 'start'")
        if t > cursor + 1e-9:                     # silence gap before this word
            segs.append(PhonemeSegment(SILENCE, cursor, t))
        for j, p in enumerate(phones):
            if not isinstance(p, dict):
                raise ValueError(f"gentle-phones: word {i} phone {j} is not an object")
            dur = _num(_first(p, ("duration",)), f"gentle-phones {i}.{j} 'duration'")
            ph = _gentle_phone(_first(p, ("phone", "phoneme")) or "")
            segs.append(PhonemeSegment(ph, t, t + dur))
            t += dur
        cursor = t
    if not segs:
        raise ValueError("gentle-phones: no aligned phones found")
    return segs
