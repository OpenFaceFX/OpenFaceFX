"""Word/segment-anchored alignment (issue #15).

The naive aligner spreads phonemes across a whole utterance by duration priors.
That is only as good as the assumption that speech fills the clip uniformly --
it does not. Anything that already knows *when* words happen (subtitle cue times,
TTS word timestamps) can pin the aligner at those boundaries and let it
distribute phonemes *within* each span. Same zero-ML machinery, far better sync.

This module adds:

  * ``Anchor(text, start, end=None)`` -- one or more words pinned to a time span.
    ``end=None`` means "unknown": the span runs to the next anchor's start (or a
    duration-proportional share when uncovered words sit in that gap).
  * ``anchored_segments(transcript, duration, anchors, g2p=None)`` -- the naive
    aligner, anchored. Anchor words are matched against the transcript
    sequentially (case/punctuation-insensitive, the regex ``G2P.phrase`` uses);
    their phonemes stay inside the anchor span; transcript words no anchor covers
    fill the time gaps between anchors; wordless gaps longer than ~0.15 s relax
    to ``sil``; utterance edges are padded with ``sil`` exactly like
    ``pipeline.naive_segments`` (with no anchors the output is byte-identical).

  * Parsers/converters, each pure stdlib and rejecting malformed input with a
    clear ValueError:
      - ``parse_srt`` -- SubRip cues (segment anchors), multi-line, ``HH:MM:SS,mmm``.
      - ``parse_word_anchors`` -- generic ``[{text, start, end?}]`` (this project's
        own schema).
      - ``from_azure_word_boundaries`` -- Azure ``WordBoundary`` events.
      - ``from_elevenlabs_alignment`` -- character arrays grouped into words.
      - ``from_kokoro_tokens`` -- per-token ``start_ts``/``end_ts`` (None-tolerant).
      - ``google_ssml_with_marks`` / ``from_google_timepoints`` -- inject one
        ``<mark/>`` per word, then map returned timepoints back to words.

Field names are verified against vendor docs where a vendor exists (see each
docstring: VERIFIED vs assumed); the snake_case aliases and object wrappers are
the tolerant extras, mirroring ``timing.py``. Times land in float seconds:
Azure offsets/durations are 100-ns ticks (÷1e7); ElevenLabs, Kokoro and Google
are already seconds.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import List, Optional

from .alignment import NaiveAligner, PhonemeSegment, _prior
from .g2p import G2P
from .phonemes import SILENCE

# Wordless gaps between cues shorter than this stay put (the mouth does not
# relax for a heartbeat pause); longer ones become a ``sil`` segment.
_SIL_GAP_MIN = 0.15

# The exact tokeniser G2P.phrase uses, plus its per-word normalisation, so anchor
# text matches the transcript the same way graphemes reach the dictionary.
_WORD_RE = re.compile(r"[A-Za-z']+")


def _key(word: str) -> str:
    return re.sub(r"[^a-z']", "", word.lower())


@dataclass
class Anchor:
    text: str                       # one or more words matched to the transcript
    start: float                    # seconds
    end: Optional[float] = None     # seconds; None -> next anchor start / share


# --------------------------------------------------------------------------- #
# Anchored alignment                                                            #
# --------------------------------------------------------------------------- #

def anchored_segments(
    transcript: str,
    duration: float,
    anchors: Optional[List[Anchor]] = None,
    g2p: Optional[G2P] = None,
) -> List[PhonemeSegment]:
    """Time-stamped phonemes for ``transcript`` over ``duration`` seconds, pinned
    at ``anchors``. With no anchors this is exactly ``pipeline.naive_segments``."""
    if duration <= 0.0:
        raise ValueError("duration must be > 0")
    g2p = g2p or G2P()
    anchors = list(anchors or [])
    words = _WORD_RE.findall(transcript)
    phones = [g2p.word(w) for w in words]
    keys = [_key(w) for w in words]

    _validate_anchors(anchors, duration)
    runs = _match_runs(anchors, keys)
    ends = _resolve_ends(anchors, runs, phones, len(words), duration)

    segs: List[PhonemeSegment] = []
    prev_t, prev_w = 0.0, 0
    n = len(anchors)
    for i, a in enumerate(anchors):
        s = max(0.0, min(a.start, duration))
        e = max(s, min(ends[i], duration))
        ws, we = runs[i]
        # Uncovered words sitting before this anchor share the gap time.
        segs += _gap(prev_t, s, phones[prev_w:ws], leading=(i == 0), trailing=False)
        aphones = [p for k in range(ws, we) for p in phones[k]]
        if aphones and e > s:
            segs += NaiveAligner().align(aphones, e - s, start=s)
        elif not aphones and e - s > 1e-9:
            segs.append(PhonemeSegment(SILENCE, s, e))   # non-lexical anchor (e.g. "♪")
        prev_t, prev_w = e, we
    segs += _gap(prev_t, duration, phones[prev_w:], leading=(n == 0), trailing=True)
    return segs


def _validate_anchors(anchors: List[Anchor], duration: float) -> None:
    for i, a in enumerate(anchors):
        if not isinstance(a.start, (int, float)) or isinstance(a.start, bool):
            raise ValueError(f"anchor {i} ({a.text!r}): start must be a number")
        if a.start < -1e-9:
            raise ValueError(f"anchor {i} ({a.text!r}): start {a.start} < 0")
        if a.start > duration + 1e-6:
            raise ValueError(
                f"anchor {i} ({a.text!r}): start {a.start} is after the audio "
                f"ends ({duration})")
        if a.end is not None and a.end < a.start - 1e-9:
            raise ValueError(
                f"anchor {i} ({a.text!r}): end {a.end} is before start {a.start}")
        if i > 0:
            p = anchors[i - 1]
            bound = p.end if p.end is not None else p.start
            if a.start < bound - 1e-9:
                raise ValueError(
                    f"anchors overlap / are not time-ordered: anchor {i} "
                    f"({a.text!r}) starts at {a.start} before anchor {i - 1} "
                    f"({p.text!r}) ends at {bound}")


def _match_runs(anchors, keys):
    """Match each anchor's words to a contiguous run of transcript words, at or
    after the running cursor. Returns (word_start, word_end) per anchor."""
    runs = []
    cursor = 0
    for i, a in enumerate(anchors):
        sub = [k for k in (_key(w) for w in _WORD_RE.findall(a.text)) if k]
        if not sub:                       # non-lexical cue: matches no words
            runs.append((cursor, cursor))
            continue
        j = _find(keys, sub, cursor)
        if j is None:
            raise ValueError(
                f"anchor {i} text {a.text!r}: word(s) {sub} not found in the "
                f"transcript at or after word index {cursor}")
        runs.append((j, j + len(sub)))
        cursor = j + len(sub)
    return runs


def _find(keys, sub, start):
    m = len(sub)
    for j in range(start, len(keys) - m + 1):
        if keys[j:j + m] == sub:
            return j
    return None


def _resolve_ends(anchors, runs, phones, n_words, duration):
    """Fill each ``end is None``: run to the next anchor's start, but if uncovered
    words sit in that gap, take only a phoneme-weight-proportional share so those
    words keep their slice (this is what keeps anchored phonemes inside the span)."""
    ends = []
    for i, a in enumerate(anchors):
        if a.end is not None:
            ends.append(min(a.end, duration))
            continue
        upper = min(anchors[i + 1].start if i + 1 < len(anchors) else duration,
                    duration)
        ws, we = runs[i]
        nxt = runs[i + 1][0] if i + 1 < len(anchors) else n_words
        aw = sum(_prior(p) for k in range(ws, we) for p in phones[k])
        gw = sum(_prior(p) for k in range(we, nxt) for p in phones[k])
        s = a.start
        if gw <= 0.0 or aw + gw <= 0.0:
            end = upper
        else:
            end = s + (upper - s) * aw / (aw + gw)
        ends.append(max(s, min(end, upper)))
    return ends


def _gap(s, e, gap_words, leading, trailing):
    """Fill the time span [s, e] holding the uncovered words ``gap_words`` (a list
    of phoneme-lists). Edge gaps get a padding ``sil`` like ``naive_segments``;
    a wordless interior gap relaxes to ``sil`` only when longer than 0.15 s."""
    width = e - s
    if width <= 1e-9:
        return []
    words = [p for ph in gap_words for p in ph]
    if words:
        pad_l = [SILENCE] if leading else []
        pad_r = [SILENCE] if trailing else []
        return NaiveAligner().align(pad_l + words + pad_r, width, start=s)
    pad = ([SILENCE] if leading else []) + ([SILENCE] if trailing else [])
    if pad:                                # utterance edge: always relax
        return NaiveAligner().align(pad, width, start=s)
    if width > _SIL_GAP_MIN:               # pause between cues
        return [PhonemeSegment(SILENCE, s, e)]
    return []


def anchors_transcript(anchors: List[Anchor]) -> str:
    """Concatenate cue text into a transcript -- what ``--anchors-format srt``
    uses when ``--text`` is omitted."""
    return " ".join(a.text for a in anchors if a.text).strip()


# --------------------------------------------------------------------------- #
# Parsers / converters. Each returns a list of Anchor.                          #
# --------------------------------------------------------------------------- #

_SRT_TIME = re.compile(r"(\d+):(\d{2}):(\d{2})[,.](\d{1,3})")


def parse_srt(text: str) -> List[Anchor]:
    """SubRip subtitles -> segment anchors. Cues are blank-line separated; the
    ``HH:MM:SS,mmm --> HH:MM:SS,mmm`` line sets the span (``.`` also accepted as
    the decimal mark) and every following line is the cue text, joined with
    spaces. A leading index line is ignored. Formatting tags (``<i>``, ``{\\an8}``)
    are stripped so they never leak into word matching."""
    out: List[Anchor] = []
    for block in re.split(r"\r?\n[ \t]*\r?\n", text.strip()):
        lines = block.splitlines()
        ti = next((k for k, ln in enumerate(lines) if "-->" in ln), None)
        if ti is None:
            continue
        stamps = list(_SRT_TIME.finditer(lines[ti]))
        if len(stamps) < 2:
            raise ValueError(f"srt: malformed timecode line {lines[ti]!r}")
        cue = " ".join(lines[ti + 1:])
        cue = re.sub(r"<[^>]+>", "", cue)          # <i>, <b>, <font ...>
        cue = re.sub(r"\{[^}]*\}", "", cue)        # {\an8}, {\pos(..)}
        cue = re.sub(r"\s+", " ", cue).strip()
        out.append(Anchor(cue, _srt_seconds(stamps[0]), _srt_seconds(stamps[1])))
    if not out:
        raise ValueError("srt: no cues found")
    return out


def _srt_seconds(m) -> float:
    h, mm, ss, ms = m.groups()
    ms = (ms + "000")[:3]
    return int(h) * 3600 + int(mm) * 60 + int(ss) + int(ms) / 1000.0


def parse_word_anchors(json_text: str) -> List[Anchor]:
    """Generic word/segment anchors: a JSON array (or ``{"anchors"|"words": [...]}``)
    of ``{"text": str, "start": seconds, "end": seconds|null}``. This is
    OpenFaceFX's own normalized schema -- the target every converter below
    produces -- so ``start``/``end`` are plain float seconds. ``end`` may be
    omitted or null (inferred from the next anchor)."""
    items = _obj_list(_json(json_text, "word-anchors"), ("anchors", "words"),
                      "word-anchors")
    out: List[Anchor] = []
    for i, it in enumerate(items):
        if not isinstance(it, dict):
            raise ValueError(f"word-anchors: item {i} is not an object")
        text = it.get("text", it.get("word"))
        if not isinstance(text, str):
            raise ValueError(f"word-anchors: item {i} needs a string 'text'")
        start = _num(it.get("start", it.get("start_s")), f"word-anchors {i} 'start'")
        raw_end = it.get("end", it.get("end_s"))
        end = None if raw_end is None else _num(raw_end, f"word-anchors {i} 'end'")
        out.append(Anchor(text, start, end))
    if not out:
        raise ValueError("word-anchors: no anchors found")
    return out


_AZ_OFFSET = ("audio_offset", "offset", "AudioOffset", "audioOffset")
_AZ_DUR = ("duration", "Duration", "duration_in_ticks", "DurationInTicks")
_AZ_TEXT = ("text", "Text")
_AZ_BOUNDARY = ("boundary_type", "BoundaryType")


def from_azure_word_boundaries(json_text: str) -> List[Anchor]:
    """Azure Speech ``WordBoundary`` events -> anchors. VERIFIED field names
    (learn.microsoft.com/.../how-to-speech-synthesis): ``Text``, ``AudioOffset``
    and ``Duration`` in **100-ns ticks** (÷1e7 = seconds), ``BoundaryType`` in
    {Word, Punctuation, Sentence}. The Python SDK lower-cases these
    (``text``/``audio_offset``/``duration``/``boundary_type``); both, plus a
    ``{"words"|"events": [...]}`` wrapper, are accepted. Punctuation boundaries
    (and any tokens without letters) are skipped so they do not offset matching."""
    items = _obj_list(_json(json_text, "azure-words"),
                      ("words", "events", "word_boundaries"), "azure-words")
    out: List[Anchor] = []
    for i, ev in enumerate(items):
        if not isinstance(ev, dict):
            raise ValueError(f"azure-words: event {i} is not an object")
        bt = _first(ev, _AZ_BOUNDARY)
        if isinstance(bt, str) and ("punct" in bt.lower() or "sentence" in bt.lower()):
            continue
        text = _first(ev, _AZ_TEXT)
        off = _first(ev, _AZ_OFFSET)
        if not isinstance(text, str) or off is None:
            raise ValueError(
                f"azure-words: event {i} needs 'Text' and 'AudioOffset', got {ev!r}")
        if not _WORD_RE.search(text):          # punctuation-only token
            continue
        start = _num(off, f"azure-words {i} offset") / 1e7
        dur = _first(ev, _AZ_DUR)
        end = None if dur is None else start + _num(dur, f"azure-words {i} duration") / 1e7
        out.append(Anchor(text, start, end))
    if not out:
        raise ValueError("azure-words: no word-boundary events found")
    return out


def from_elevenlabs_alignment(json_text: str) -> List[Anchor]:
    """ElevenLabs timestamped TTS -> anchors. VERIFIED field names
    (elevenlabs.io/docs/.../convert-with-timestamps): a top-level ``alignment``
    and/or ``normalized_alignment`` object, each with parallel ``characters``,
    ``character_start_times_seconds`` and ``character_end_times_seconds`` arrays
    (already **seconds**). ``normalized_alignment`` is preferred when present. A
    bare alignment object (the three arrays at top level) is also accepted.
    Characters are grouped into words at whitespace; a word spans its first
    character's start to its last character's end."""
    d = _json(json_text, "elevenlabs")
    if not isinstance(d, dict):
        raise ValueError("elevenlabs: expected a JSON object")
    al = d.get("normalized_alignment") or d.get("alignment")
    if al is None and "characters" in d:
        al = d
    if not isinstance(al, dict):
        raise ValueError(
            "elevenlabs: no 'alignment' or 'normalized_alignment' object")
    chars = al.get("characters")
    starts = al.get("character_start_times_seconds")
    ends = al.get("character_end_times_seconds")
    if not (isinstance(chars, list) and isinstance(starts, list)
            and isinstance(ends, list)):
        raise ValueError(
            "elevenlabs: alignment needs 'characters', "
            "'character_start_times_seconds', 'character_end_times_seconds' arrays")
    if not len(chars) == len(starts) == len(ends):
        raise ValueError("elevenlabs: character arrays differ in length")
    if not chars:
        raise ValueError("elevenlabs: empty alignment")
    out: List[Anchor] = []
    cur: List[str] = []
    cs = ce = 0.0
    for ch, s, e in zip(chars, starts, ends):
        if not isinstance(ch, str):
            raise ValueError("elevenlabs: non-string character in 'characters'")
        if ch.strip() == "":                    # whitespace closes the word
            if cur:
                out.append(Anchor("".join(cur), cs, ce))
                cur = []
            continue
        if not cur:
            cs = _num(s, "elevenlabs start")
        cur.append(ch)
        ce = _num(e, "elevenlabs end")
    if cur:
        out.append(Anchor("".join(cur), cs, ce))
    if not out:
        raise ValueError("elevenlabs: no words after grouping at whitespace")
    return out


def from_kokoro_tokens(json_text: str) -> List[Anchor]:
    """Kokoro per-token timestamps -> anchors. Field names from the community
    write-up (ryanwelch.co.uk/blog/kokoro-word-timestamps; not a vendor doc):
    tokens carry ``text``, ``start_ts`` and ``end_ts`` (float **seconds**, either
    may be ``None``). A JSON array or ``{"tokens": [...]}`` is accepted. Tokens
    with ``start_ts is None`` are dropped (that word falls back to the surrounding
    gap distribution); ``end_ts is None`` leaves the span open to the next token."""
    items = _obj_list(_json(json_text, "kokoro"), ("tokens", "words"), "kokoro")
    out: List[Anchor] = []
    for i, tok in enumerate(items):
        if not isinstance(tok, dict):
            raise ValueError(f"kokoro: token {i} is not an object")
        text = tok.get("text", tok.get("graphemes"))
        if not isinstance(text, str) or not _WORD_RE.search(text):
            continue                            # whitespace / punctuation token
        st = tok.get("start_ts")
        if st is None:                          # no start -> cannot anchor it
            continue
        start = _num(st, f"kokoro token {i} start_ts")
        et = tok.get("end_ts")
        end = None if et is None else _num(et, f"kokoro token {i} end_ts")
        out.append(Anchor(text.strip(), start, end))
    if not out:
        raise ValueError("kokoro: no timestamped word tokens found")
    return out


def google_ssml_with_marks(transcript: str) -> str:
    """Insert an SSML ``<mark name="wN"/>`` before each transcript word so Google
    Cloud TTS (synthesized with ``enableTimePointing=SSML_MARK``) returns one
    timepoint per word. Pure text transform, no SDK: the original text is kept
    intact (punctuation and spacing preserved for natural prosody) with XML
    metacharacters escaped; only the marks are added. ``from_google_timepoints``
    maps the ``wN`` marks back to words using the same tokenisation."""
    out: List[str] = []
    pos = 0
    for i, m in enumerate(_WORD_RE.finditer(transcript)):
        out.append(_xml_escape(transcript[pos:m.start()]))   # inter-word text
        out.append(f'<mark name="w{i}"/>')
        out.append(_xml_escape(m.group()))
        pos = m.end()
    out.append(_xml_escape(transcript[pos:]))                # trailing text
    return "<speak>" + "".join(out) + "</speak>"


def from_google_timepoints(json_text: str, transcript: str) -> List[Anchor]:
    """Google Cloud TTS timepoints -> anchors. VERIFIED field names
    (cloud.google.com/.../v1beta1/text/synthesize): a top-level ``timepoints``
    array of ``{"markName": str, "timeSeconds": number}`` (already **seconds**). A
    bare array is also accepted. Each ``wN`` mark (from ``google_ssml_with_marks``)
    identifies transcript word N and pins its start; ends are left open (Google
    marks give word starts only), so the next word's start closes each span."""
    items = _obj_list(_json(json_text, "google"), ("timepoints",), "google")
    words = _WORD_RE.findall(transcript)
    out: List[Anchor] = []
    for i, tp in enumerate(items):
        if not isinstance(tp, dict):
            raise ValueError(f"google: timepoint {i} is not an object")
        name = tp.get("markName", tp.get("mark_name"))
        if not isinstance(name, str):
            raise ValueError(f"google: timepoint {i} needs a string 'markName'")
        mt = re.fullmatch(r"w(\d+)", name.strip())
        if not mt:
            raise ValueError(
                f"google: markName {name!r} is not 'w<index>' "
                "(use google_ssml_with_marks to inject marks)")
        idx = int(mt.group(1))
        if idx >= len(words):
            raise ValueError(
                f"google: mark {name!r} index {idx} is beyond the transcript "
                f"({len(words)} words)")
        ts = tp.get("timeSeconds", tp.get("time_seconds"))
        out.append(Anchor(words[idx], _num(ts, f"google timepoint {i} timeSeconds"),
                          None))
    if not out:
        raise ValueError("google: no timepoints found")
    return out


# --------------------------------------------------------------------------- #
# Shared JSON helpers (same shape as timing.py's).                              #
# --------------------------------------------------------------------------- #

def _json(text, who):
    try:
        return json.loads(text)
    except json.JSONDecodeError as e:
        raise ValueError(f"{who}: not valid JSON ({e})") from None


def _obj_list(d, keys, who):
    if isinstance(d, list):
        return d
    if isinstance(d, dict):
        for k in keys:
            if isinstance(d.get(k), list):
                return d[k]
    alts = " / ".join(repr(k) for k in keys)
    raise ValueError(f"{who}: expected a JSON array (or an object with {alts})")


def _first(d, keys):
    for k in keys:
        if k in d:
            return d[k]
    return None


def _num(x, what):
    if isinstance(x, bool) or not isinstance(x, (int, float)):
        raise ValueError(f"{what} must be a number, got {x!r}")
    return float(x)


def _xml_escape(s: str) -> str:
    return (s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            .replace('"', "&quot;"))
