"""Transcript text tags (issue #7): direct animation from the script.

A writer steers the generated animation with inline tags in the transcript, the
way FaceFX's *text tagging* stage does: tags are extracted **before** G2P, the
clean words are phonemised and lip-synced as usual, and each tag is mapped onto
the timeline using the word timings the aligner produced. The syntax is modelled
on the FaceFX docs (https://facefx.github.io/documentation/doc/text-tagging) and,
for ``[emphasis]``/``[pause]``, on SSML ``<emphasis>``/``<break>``. Four families:

  * **Curve** — ``[Name type=quad|lt|ct|tt v1=.. v2=.. v3=.. v4=.. easein=..
    easeout=.. timeshift=.. duration=..]words[/Name]`` adds a channel ``Name``
    keyframed over the span (leading/centered/trailing triplet or quadruplet,
    0.2 s ease defaults) — see :func:`build_curve_channel`.
  * **Event** — ``[event:NAME k=v ...]`` / ``[gesture:NAME ...]`` (or the FaceFX
    curly ``{"group|anim" start=.. payload=".." ...}``) inject an
    :class:`openfacefx.events.Event` at the *following* word's start (end of the
    last word if trailing); non-timing params are kept in the payload.
  * **Emphasis** — ``[emphasis]word[/emphasis]`` (optional ``strength=``) locally
    raises articulation, reusing the issue-#18 dominance-amplitude pass through
    :attr:`CoartParams.emphasis_windows`.
  * **Chunk / pause** — ``<T>`` markers chunk the naive utterance into phrases
    pinned to audio times, ``sil`` filling the gaps; ``[pause:SECONDS]`` inserts
    silence at a boundary and ``[phrase]`` drops a ``marker/phrase`` event.

Deterministic and stdlib-only (``re``/``shlex``); numpy is never imported. A
transcript with no tags parses to itself with an empty tag list, so the naive
pipeline stays byte-identical when tags are absent.
"""

from __future__ import annotations

import re
import shlex
from dataclasses import dataclass, field, replace
from typing import Dict, List, Optional, Tuple

from .alignment import NaiveAligner, PhonemeSegment
from .curves import Channel, Keyframe
from .events import Event, EVENT_TYPES
from .g2p import G2P
from .phonemes import SILENCE

#: Same word tokenizer G2P uses, so tag word-indices line up with the phonemised
#: words one-for-one.
WORD_RE = re.compile(r"[A-Za-z']+")

#: FaceFX curve-tag ease defaults (seconds before/after the word span).
DEFAULT_EASE = 0.2
#: Default local-emphasis strength (dominance gain ``1 + strength``); mirrors the
#: 0.5 bare-flag default of the issue-#18 ``--stress-emphasis`` dial.
DEFAULT_STRENGTH = 0.5

# One pass recognises every tag family. Ordered so the specific forms win over the
# generic ``[Name ...]`` curve open: chunk, closing/opening curly event, closing
# bracket, reserved inline keywords, then the catch-all curve/emphasis open.
_TAG_RE = re.compile(
    r"""
      (?P<chunk><\s*(?P<ctime>[0-9]*\.?[0-9]+)\s*>)
    | (?P<eventc>\{\s*/\s*"[^"]*"\s*\})
    | (?P<evento>\{\s*"(?P<ename>[^"]*)"(?P<eargs>[^}]*)\})
    | (?P<close>\[\s*/\s*(?P<cname>[A-Za-z_][\w]*)\s*\])
    | (?P<inline>\[\s*(?P<iname>event|gesture|pause|break|phrase|mark)\b(?P<iargs>[^\]]*)\])
    | (?P<open>\[\s*(?P<oname>[A-Za-z_][\w]*)\b(?P<oargs>[^\]]*)\])
    """,
    re.VERBOSE,
)

# Auto-detect: only clear, unambiguous tags flip the naive command into tag mode.
# A lone ``[bracketed]`` word (no close, no keyword) does NOT, so ordinary
# transcripts that happen to contain brackets are untouched.
_AUTOTAG_RE = re.compile(
    r'\[\s*/[A-Za-z_]|\{\s*"|<\s*[0-9]|\[\s*(?:event|gesture|pause|break|phrase|mark)\b'
)


@dataclass
class Tag:
    """One parsed tag anchored to the clean-text word stream.

    ``word_index`` is the word the tag attaches to: for wrapping tags
    (``curve``/``emphasis``) the first spanned word, with ``end_word_index`` the
    exclusive last; for inline tags the *following* word (``== len(words)`` when
    trailing). ``value`` carries the ``pause`` seconds / ``chunk`` marker time;
    ``params`` the raw ``key=value`` map from the tag body."""
    kind: str                       # curve | event | emphasis | pause | phrase | chunk
    word_index: int
    end_word_index: int = -1
    name: str = ""
    params: Dict[str, str] = field(default_factory=dict)
    value: Optional[float] = None


def has_tags(text: str) -> bool:
    """True if ``text`` contains an unambiguous tag — the auto-detect the naive
    command uses to enable tag parsing without an explicit ``--tags`` flag."""
    return bool(_AUTOTAG_RE.search(text or ""))


def _f(x, default: float) -> float:
    """``float(x)`` with a fallback for missing / unparseable tag arguments."""
    if x is None:
        return default
    try:
        return float(x)
    except (TypeError, ValueError):
        return default


def _parse_params(s: str) -> Dict[str, str]:
    """``key=value`` pairs (values may be ``"quoted"``) plus bare flags -> ``"true"``.

    ``shlex`` handles the quoting FaceFX uses for multi-word payloads
    (``payload="Data for your game"``); keys are lower-cased so parameters are
    case-insensitive as FaceFX specifies."""
    out: Dict[str, str] = {}
    try:
        toks = shlex.split(s, posix=True)
    except ValueError:
        toks = s.split()
    for tok in toks:
        if "=" in tok:
            k, v = tok.split("=", 1)
            out[k.strip().lower()] = v
        elif tok.strip():
            out[tok.strip().lower().lstrip(":")] = "true"
    return out


def _inline_tag(iname: str, iargs: str, nwords: int) -> Tag:
    """Build the :class:`Tag` for a reserved inline keyword (``event``/``gesture``/
    ``pause``/``break``/``phrase``/``mark``) anchored at the following word."""
    iname = iname.lower()
    body = iargs.strip()
    if iname in ("event", "gesture"):
        rest = body.lstrip(":").strip()
        first, _, tail = rest.partition(" ")
        name = ("gesture|" + first) if iname == "gesture" else first
        return Tag("event", nwords, name=name, params=_parse_params(tail))
    if iname in ("pause", "break"):
        params = _parse_params(body.lstrip(":"))
        if "time" in params:
            secs = _f(params["time"], 0.0)
        else:
            token = body.lstrip(":").strip().split()
            secs = _f(token[0], 0.0) if token else 0.0
        return Tag("pause", nwords, value=secs)
    # phrase / mark both drop a phrase marker; mark may name it.
    name = _parse_params(body).get("name", "phrase")
    return Tag("phrase", nwords, name=name)


def parse_tagged_transcript(text: str) -> Tuple[str, List[Tag]]:
    """Split a tagged transcript into ``(clean_text, tags)``.

    ``clean_text`` is the spoken words with every tag removed (whitespace left by
    a removal is collapsed), ready for G2P; ``tags`` is the deterministic list of
    :class:`Tag` records, each anchored to a word index in ``clean_text``'s own
    ``[A-Za-z']+`` tokenization. A transcript with no tags is returned unchanged
    with an empty list, so the caller's downstream path is byte-identical.
    Malformed tags pass through gracefully (an unclosed open or stray close yields
    no tag; the spanned words are still spoken); the one hard error is chunk
    ordering, validated in :func:`chunked_segments`."""
    tags: List[Tag] = []
    parts: List[str] = []
    stack: List[Tuple[str, int, Dict[str, str]]] = []  # (name, start_word, params)
    nwords = 0
    pos = 0
    found = False
    for m in _TAG_RE.finditer(text):
        found = True
        gap = text[pos:m.start()]
        parts.append(gap)
        nwords += len(WORD_RE.findall(gap))
        pos = m.end()
        if m.group("chunk"):
            tags.append(Tag("chunk", nwords, value=_f(m.group("ctime"), 0.0)))
        elif m.group("eventc"):
            pass  # ranged-event close: stripped (point events only, documented)
        elif m.group("evento"):
            tags.append(Tag("event", nwords, name=m.group("ename"),
                            params=_parse_params(m.group("eargs") or "")))
        elif m.group("close"):
            cname = m.group("cname")
            for j in range(len(stack) - 1, -1, -1):
                nm, start_w, prm = stack[j]
                if nm == cname:
                    del stack[j]
                    kind = "emphasis" if nm.lower() == "emphasis" else "curve"
                    tags.append(Tag(kind, start_w, end_word_index=nwords,
                                    name=nm, params=prm))
                    break
        elif m.group("inline"):
            tags.append(_inline_tag(m.group("iname"), m.group("iargs") or "", nwords))
        elif m.group("open"):
            stack.append((m.group("oname"), nwords,
                          _parse_params(m.group("oargs") or "")))
    parts.append(text[pos:])
    clean = "".join(parts)
    if found:
        clean = re.sub(r"[ \t]{2,}", " ", clean).strip()
    return clean, tags


# --------------------------------------------------------------------------- #
# Word-level naive alignment (word time spans anchor the tags)                 #
# --------------------------------------------------------------------------- #

def _word_phones(clean_text: str, g2p: G2P) -> Tuple[List[str], List[List[str]]]:
    words = WORD_RE.findall(clean_text)
    return words, [g2p.word(w) for w in words]


def naive_word_segments(clean_text: str, duration: float, g2p: G2P
                        ) -> Tuple[List[PhonemeSegment], List[Tuple[float, float]]]:
    """Naive-path segments plus a ``(start, end)`` time span per word.

    Builds the exact ``[sil] + phones + [sil]`` sequence :func:`naive_segments`
    uses and distributes it over ``duration``, so with no chunk/pause tags the
    segments are byte-identical to the plain path — the word spans are recovered
    from the phone index ranges."""
    words, phones_per = _word_phones(clean_text, g2p)
    phones: List[str] = [SILENCE]
    ranges: List[Tuple[int, int]] = []
    for pl in phones_per:
        start = len(phones)
        phones.extend(pl)
        ranges.append((start, len(phones)))
    phones.append(SILENCE)
    segs = NaiveAligner().align(phones, total_duration=duration)
    spans: List[Tuple[float, float]] = []
    for start, end in ranges:
        if end > start:
            spans.append((segs[start].start, segs[end - 1].end))
        else:  # a token with no phones (bare apostrophe): zero-length anchor
            t = segs[start].start if start < len(segs) else duration
            spans.append((t, t))
    return segs, spans


def chunked_segments(clean_text: str, duration: float, chunks: List[Tag], g2p: G2P
                     ) -> Tuple[List[PhonemeSegment], List[Tuple[float, float]]]:
    """Align the naive utterance into ``<T>``-delimited chunks.

    Each marker pins a text position to an audio time; the words between two
    markers spread over that ``[t_i, t_{i+1}]`` span and the gaps (plus the
    pre-roll and tail) fill with ``sil``. Marker times must be non-negative,
    within ``duration`` and non-decreasing (equal only between two phrases), else
    ``ValueError`` — a non-monotonic/overlapping timeline cannot be rendered."""
    words, phones_per = _word_phones(clean_text, g2p)
    positions = [t.word_index for t in chunks]
    times = [float(t.value or 0.0) for t in chunks]
    for i, tt in enumerate(times):
        if tt < 0.0:
            raise ValueError(f"chunk marker <{tt}> is negative")
        if tt > duration + 1e-9:
            raise ValueError(
                f"chunk marker <{tt}> exceeds the audio duration {duration}")
        if i > 0 and tt < times[i - 1] - 1e-9:
            raise ValueError(
                f"chunk markers must not decrease / overlap: <{times[i - 1]}> "
                f"then <{tt}>")

    bound_pos = [0] + positions + [len(words)]
    bound_time = [0.0] + times + [float(duration)]
    segs: List[PhonemeSegment] = []
    spans: List[Optional[Tuple[float, float]]] = [None] * len(words)
    aligner = NaiveAligner()
    cursor = 0.0
    for i in range(len(bound_pos) - 1):
        wa, wb = bound_pos[i], bound_pos[i + 1]
        ta, tb = bound_time[i], bound_time[i + 1]
        if ta - cursor > 1e-9:                 # gap before this cell -> silence
            segs.append(PhonemeSegment(SILENCE, cursor, ta))
            cursor = ta
        cell_phones: List[str] = []
        cell_ranges: List[Tuple[int, int, int]] = []
        for wi in range(wa, wb):
            s = len(cell_phones)
            cell_phones.extend(phones_per[wi])
            cell_ranges.append((wi, s, len(cell_phones)))
        if cell_phones and tb - ta > 1e-9:
            cseg = aligner.align(cell_phones, total_duration=tb - ta, start=ta)
            for wi, s, e in cell_ranges:
                spans[wi] = (cseg[s].start, cseg[e - 1].end) if e > s else (ta, ta)
            segs.extend(cseg)
            cursor = tb
        else:                                   # empty / zero-width cell
            if tb - ta > 1e-9:
                segs.append(PhonemeSegment(SILENCE, ta, tb))
                cursor = tb
            for wi, _, _ in cell_ranges:
                spans[wi] = (ta, ta)
    if duration - cursor > 1e-9:
        segs.append(PhonemeSegment(SILENCE, cursor, float(duration)))
    filled = [sp if sp is not None else (0.0, 0.0) for sp in spans]
    return segs, filled


def _insert_pauses(segs: List[PhonemeSegment], spans: List[Tuple[float, float]],
                   pauses: List[Tag], duration: float
                   ) -> Tuple[List[PhonemeSegment], List[Tuple[float, float]]]:
    """Splice ``[pause:N]`` silences into an aligned timeline, shifting everything
    after each insertion point later by ``N`` (so a pause *adds* time)."""
    n = len(spans)
    inserts: List[Tuple[float, float]] = []
    for p in pauses:
        secs = float(p.value or 0.0)
        if secs <= 0.0:
            continue
        wi = p.word_index
        t = spans[wi][0] if wi < n else (segs[-1].end if segs else float(duration))
        inserts.append((t, secs))
    if not inserts:
        return segs, spans
    inserts.sort()
    out: List[PhonemeSegment] = []
    cum = 0.0
    idx = 0
    for seg in segs:
        while idx < len(inserts) and seg.start + 1e-9 >= inserts[idx][0]:
            t0, secs = inserts[idx]
            out.append(PhonemeSegment(SILENCE, t0 + cum, t0 + cum + secs))
            cum += secs
            idx += 1
        out.append(PhonemeSegment(seg.phoneme, seg.start + cum, seg.end + cum))
    while idx < len(inserts):                   # pause(s) trailing the last word
        base = out[-1].end if out else 0.0
        _, secs = inserts[idx]
        out.append(PhonemeSegment(SILENCE, base, base + secs))
        cum += secs
        idx += 1
    shifted: List[Tuple[float, float]] = []
    for a, b in spans:
        off = sum(s for t0, s in inserts if t0 <= a + 1e-9)
        shifted.append((a + off, b + off))
    return out, shifted


def resolve_tagged_segments(clean_text: str, duration: float, tags: List[Tag],
                            g2p: G2P) -> Tuple[List[PhonemeSegment],
                                               List[Tuple[float, float]],
                                               List[Tuple[float, float, float]]]:
    """Phoneme segments, per-word time spans, and emphasis windows for a tagged
    transcript. Chunk markers pick the chunked aligner; otherwise the plain naive
    alignment runs and any ``[pause]`` tags are spliced in afterwards."""
    chunks = [t for t in tags if t.kind == "chunk"]
    if chunks:
        segs, spans = chunked_segments(clean_text, duration, chunks, g2p)
    else:
        segs, spans = naive_word_segments(clean_text, duration, g2p)
        pauses = [t for t in tags if t.kind == "pause"]
        if pauses:
            segs, spans = _insert_pauses(segs, spans, pauses, duration)
    return segs, spans, emphasis_windows(tags, spans)


# --------------------------------------------------------------------------- #
# Tag -> timeline objects (channels, events, emphasis windows)                 #
# --------------------------------------------------------------------------- #

def emphasis_windows(tags: List[Tag], spans: List[Tuple[float, float]]
                     ) -> List[Tuple[float, float, float]]:
    """``(t0, t1, gain)`` windows for :attr:`CoartParams.emphasis_windows` from the
    ``[emphasis]`` tags, gain ``1 + strength`` over the tagged word span."""
    n = len(spans)
    out: List[Tuple[float, float, float]] = []
    for t in tags:
        if t.kind != "emphasis" or t.word_index >= n:
            continue
        a = t.word_index
        b = min(max(t.end_word_index, a + 1), n)
        strength = _f(t.params.get("strength"), DEFAULT_STRENGTH)
        out.append((spans[a][0], spans[b - 1][1], 1.0 + strength))
    return out


def emphasis_params(params, windows: List[Tuple[float, float, float]]):
    """Return ``params`` carrying ``windows`` on :attr:`emphasis_windows` (a fresh
    :class:`CoartParams` when ``params`` is ``None``), or ``params`` untouched when
    there is nothing to emphasize."""
    if not windows:
        return params
    from .coarticulation import CoartParams
    base = params if params is not None else CoartParams()
    return replace(base, emphasis_windows=list(windows))


def build_curve_channel(name: str, params: Dict[str, str],
                        w_start: float, w_end: float) -> Channel:
    """A FaceFX curve-tag channel over the word span ``[w_start, w_end]``.

    ``type`` selects the shape: ``lt``/``ct``/``tt`` are three-point triplets that
    peak at the word start / centre / end, ``quad`` (default) the four-point
    apex-and-valley curve. ``v1..v4`` are the keyframe values (``v1=0 v2=1``,
    ``v3=1 v4=0`` for quad / ``v3=0`` triplets), ``easein``/``easeout`` (0.2 s)
    push the first/last key before/after the span, ``duration`` sets the quad
    hold, ``timeshift`` slides the curve. Times clamp at 0 and de-duplicate;
    values are unclamped, so a curve may drive a channel past 1."""
    typ = (params.get("type") or "quad").lower()
    easein = _f(params.get("easein"), DEFAULT_EASE)
    easeout = _f(params.get("easeout"), DEFAULT_EASE)
    shift = _f(params.get("timeshift"), 0.0)
    v1, v2 = _f(params.get("v1"), 0.0), _f(params.get("v2"), 1.0)
    if typ in ("lt", "ct", "tt"):
        v3 = _f(params.get("v3"), 0.0)
        peak = w_start if typ == "lt" else w_end if typ == "tt" else (w_start + w_end) / 2.0
        raw = [(w_start - easein, v1), (peak, v2), (w_end + easeout, v3)]
    else:
        v3, v4 = _f(params.get("v3"), 1.0), _f(params.get("v4"), 0.0)
        t3 = w_start + _f(params.get("duration"), 0.0) if params.get("duration") else w_end
        raw = [(w_start - easein, v1), (w_start, v2), (t3, v3), (w_end + easeout, v4)]
    raw = sorted(((max(t + shift, 0.0), v) for t, v in raw), key=lambda p: p[0])
    keys: List[Keyframe] = []
    for t, v in raw:
        rt, rv = round(t, 4), round(v, 4)
        if keys and abs(keys[-1].time - rt) < 1e-9:
            keys[-1] = Keyframe(rt, rv)          # collapse a clamped duplicate time
        else:
            keys.append(Keyframe(rt, rv))
    return Channel(name, keys)


def curve_channels(tags: List[Tag], spans: List[Tuple[float, float]],
                   duration: float) -> List[Channel]:
    """One :class:`Channel` per ``curve`` tag, keyed over its word span."""
    n = len(spans)
    out: List[Channel] = []
    for t in tags:
        if t.kind != "curve" or t.word_index >= n:
            continue
        a = t.word_index
        b = min(max(t.end_word_index, a + 1), n)
        out.append(build_curve_channel(t.name, t.params, spans[a][0], spans[b - 1][1]))
    return out


def _coerce(v: str):
    """Payload value coercion: bare flags -> ``True``, numbers -> ``float``, else
    the raw string — so a JSON payload round-trips as the writer typed it."""
    if v == "true":
        return True
    try:
        return float(v)
    except (TypeError, ValueError):
        return v


def _build_event(tag: Tag, base: float) -> Event:
    """One :class:`Event` from an event tag anchored at ``base`` seconds.

    ``start`` shifts the time, ``duration``/``blendin``/``blendout`` map to the
    :class:`Event` fields, and every remaining parameter (magnitude, probability,
    ``payload="..."`` -> ``data``, custom keys) is preserved in the payload. The
    ``group|anim`` name splits into ``type`` (a known :data:`EVENT_TYPES` member,
    else ``custom``) and ``name``."""
    p = dict(tag.params)
    start = 0.0
    for k in ("start", "minstart"):
        if k in p:
            start = _f(p.pop(k), 0.0)
            break
    dur = 0.0
    for k in ("duration", "dur", "minduration"):
        if k in p:
            dur = _f(p.pop(k), 0.0)
            break
    blend_in = _f(p.pop("blendin", None), 0.0)
    blend_out = _f(p.pop("blendout", None), 0.0)
    if "|" in tag.name:
        group, _, anim = tag.name.partition("|")
        typ = group if group in EVENT_TYPES else "custom"
        name = anim or group
    else:
        typ = tag.name if tag.name in EVENT_TYPES else "custom"
        name = tag.name or "event"
    payload: Dict = {}
    if "payload" in p:
        payload["data"] = p.pop("payload")
    for k, v in p.items():
        payload[k] = _coerce(v)
    return Event(round(base + start, 4), typ, name, dur=dur, payload=payload,
                 blend_in=blend_in, blend_out=blend_out)


def tag_events(tags: List[Tag], spans: List[Tuple[float, float]],
               duration: float) -> List[Event]:
    """Time-sorted events from the ``event`` and ``phrase`` tags, each anchored at
    the following word's start (the end of the last word when trailing)."""
    n = len(spans)
    end_time = spans[-1][1] if spans else float(duration)
    out: List[Event] = []
    for t in tags:
        if t.kind not in ("event", "phrase"):
            continue
        base = spans[t.word_index][0] if t.word_index < n else end_time
        if t.kind == "phrase":
            out.append(Event(round(base, 4), "marker", "phrase"))
        else:
            out.append(_build_event(t, base))
    out.sort(key=lambda e: e.t)
    return out
