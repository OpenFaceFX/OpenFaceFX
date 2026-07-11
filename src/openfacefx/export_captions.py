"""Subtitle / caption exporter (issue #41): SRT + WebVTT from the SAME alignment.

Captions and lip motion should come from one source of truth so they stay in
sync. OpenFaceFX already *ingests* word/segment timings (``anchors.parse_srt``,
the Azure / ElevenLabs word boundaries) but had no caption *output*. This is that
output — deterministic string formatting over the timing arrays the pipeline
already produces, **not** a new alignment: :func:`word_timings` pulls per-word
spans from :func:`openfacefx.texttags.naive_word_segments`, which builds the
identical ``[sil] + phones + [sil]`` sequence :func:`openfacefx.pipeline.
naive_segments` does — so the words the captions carry are timed by the very
segments the viseme curves were reduced from.

The pipeline:

  * :func:`word_timings` -> per-word ``(token, start, end)`` (punctuation-bearing
    display tokens paired to the word spans; a hyphenated token spans its parts);
  * :func:`build_cues` groups words into cues — greedily packed under a
    max-line-length / max-lines **wrap budget**, broken at sentence-ending
    punctuation and audible gaps, each cue's duration extended toward a
    reading-speed (**characters-per-second**) minimum and clamped so cues stay
    monotonic and non-overlapping;
  * :func:`srt_text` / :func:`vtt_text` serialise to SubRip (``HH:MM:SS,mmm``) and
    WebVTT (``HH:MM:SS.mmm`` behind the ``WEBVTT`` header), with optional
    word-level **karaoke** highlighting (WebVTT ``<c>`` spans + inline cue
    timestamps that fall inside their cue span).

:func:`write_srt` / :func:`write_vtt` (and the extension-dispatching
:func:`write_captions`) write LF-terminated UTF-8. Pure stdlib, deterministic;
``srt_text`` is the inverse of ``anchors.parse_srt`` (a round-trip recovers the
cue spans within millisecond rounding).
"""

from __future__ import annotations

import os
from typing import List, NamedTuple, Optional, Sequence, Tuple

#: Reading speed (characters per second). ~15-17 cps is the common subtitle
#: readability ceiling (BBC / Netflix guidance); a cue is held at least
#: ``len(text) / cps`` seconds where the timeline leaves room.
DEFAULT_CPS = 17.0
#: Max characters per line and max lines per cue (BBC/Netflix ~ 37-42, <= 2).
DEFAULT_MAX_LINE = 42
DEFAULT_MAX_LINES = 2
#: A silence of at least this many seconds between two words breaks the cue.
DEFAULT_GAP = 0.5

#: One word on the timeline: ``(display token, start seconds, end seconds)``.
WordTiming = Tuple[str, float, float]

_SENTENCE_END = (".", "!", "?", "…")   # . ! ? …
_TRAILING = "\"')]}”’"            # quotes/brackets after the stop


class CaptionCue(NamedTuple):
    """One subtitle cue: a 1-based ``index``, a ``[start, end]`` span in seconds,
    the wrapped display ``lines``, and the per-word ``words`` (for karaoke)."""
    index: int
    start: float
    end: float
    lines: List[str]
    words: List[WordTiming]

    @property
    def text(self) -> str:
        return " ".join(self.lines)


def format_timestamp(seconds: float, sep: str = ",") -> str:
    """``seconds`` -> ``HH:MM:SS<sep>mmm`` (``sep=","`` for SRT, ``"."`` for
    WebVTT), rounded to the nearest millisecond and floored at zero."""
    ms = int(round(max(seconds, 0.0) * 1000.0))
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return "%02d:%02d:%02d%s%03d" % (h, m, s, sep, ms)


def word_timings(text: str, duration: float, g2p=None) -> List[WordTiming]:
    """Per-word ``(token, start, end)`` from the same naive alignment the lip
    curves use. Display tokens keep their punctuation (so cue grouping can see
    sentence ends); each is paired to its ``[A-Za-z']+`` word span(s) — a
    hyphenated token spans from its first part's start to its last part's end,
    and a pure-punctuation token (no word) contributes no timing."""
    from .g2p import G2P
    from .texttags import WORD_RE, naive_word_segments
    _, spans = naive_word_segments(text, duration, g2p or G2P())
    out: List[WordTiming] = []
    idx = 0
    for token in text.split():
        n = len(WORD_RE.findall(token))
        if n == 0:
            continue
        out.append((token, spans[idx][0], spans[idx + n - 1][1]))
        idx += n
    return out


def _wrap(tokens: Sequence[str], max_line: int) -> List[str]:
    """Greedy word-wrap ``tokens`` into lines each at most ``max_line`` chars. A
    lone token longer than ``max_line`` takes its own (over-long) line rather
    than being dropped — words are never split."""
    lines: List[str] = []
    cur = ""
    for tok in tokens:
        cand = tok if not cur else cur + " " + tok
        if not cur or len(cand) <= max_line:
            cur = cand
        else:
            lines.append(cur)
            cur = tok
    if cur:
        lines.append(cur)
    return lines


def _ends_sentence(token: str) -> bool:
    return token.rstrip(_TRAILING).endswith(_SENTENCE_END)


def _group(words: List[WordTiming], max_line: int, max_lines: int,
           gap: float) -> List[List[WordTiming]]:
    """Pack words into cue groups: close the current group before a word when
    adding it would overflow the ``max_lines`` x ``max_line`` wrap budget, when
    the previous token ended a sentence, or when a silence of at least ``gap``
    precedes it."""
    groups: List[List[WordTiming]] = []
    cur: List[WordTiming] = []
    for w in words:
        if cur:
            prev = cur[-1]
            overflow = len(_wrap([x[0] for x in cur + [w]], max_line)) > max_lines
            if overflow or _ends_sentence(prev[0]) or (w[1] - prev[2]) >= gap:
                groups.append(cur)
                cur = []
        cur.append(w)
    if cur:
        groups.append(cur)
    return groups


def build_cues(words: Sequence[WordTiming], *, cps: float = DEFAULT_CPS,
               max_line: int = DEFAULT_MAX_LINE,
               max_lines: int = DEFAULT_MAX_LINES,
               gap: float = DEFAULT_GAP) -> List[CaptionCue]:
    """Group ``words`` into subtitle cues.

    Each cue wraps into at most ``max_lines`` lines of at most ``max_line`` chars
    (never exceeded, bar a single unbreakable over-long word), starts at its
    first word and ends at ``max(last word end, start + chars/cps)`` — held long
    enough to read at ``cps`` characters per second — with every end clamped to
    the next cue's start so cues are monotonic and non-overlapping."""
    groups = _group(list(words), max_line, max_lines, gap)
    spans: List[List] = []
    for g in groups:
        lines = _wrap([w[0] for w in g], max_line)
        start = g[0][1]
        chars = sum(len(ln) for ln in lines)
        end = max(g[-1][2], start + chars / cps if cps > 0 else g[-1][2])
        spans.append([start, end, lines, list(g)])
    for i in range(len(spans) - 1):                  # non-overlap: clamp to next
        if spans[i][1] > spans[i + 1][0]:
            spans[i][1] = spans[i + 1][0]
    return [CaptionCue(i + 1, s[0], s[1], s[2], s[3])
            for i, s in enumerate(spans)]


def _karaoke_payload(cue: CaptionCue) -> str:
    """WebVTT karaoke: ``<c>`` word spans with an inline ``<HH:MM:SS.mmm>`` cue
    timestamp before every word after the first (the first highlights at the cue
    start). Each timestamp is a word start, so it lies inside the cue span."""
    parts: List[str] = []
    for j, (token, ws, _we) in enumerate(cue.words):
        stamp = "" if j == 0 else "<%s>" % format_timestamp(ws, ".")
        parts.append("%s<c>%s</c>" % (stamp, token))
    return " ".join(parts)


def srt_text(cues: Sequence[CaptionCue]) -> str:
    """Serialise cues as SubRip: ``index`` / ``HH:MM:SS,mmm --> ...`` / text,
    blank-line separated (the inverse of ``anchors.parse_srt``)."""
    blocks = ["%d\n%s --> %s\n%s\n" % (
        c.index, format_timestamp(c.start, ","), format_timestamp(c.end, ","),
        "\n".join(c.lines)) for c in cues]
    return "\n".join(blocks)


def vtt_text(cues: Sequence[CaptionCue], *, karaoke: bool = False) -> str:
    """Serialise cues as WebVTT (``WEBVTT`` header, ``HH:MM:SS.mmm`` dot
    timestamps); ``karaoke`` emits per-word ``<c>`` spans with inline cue
    timestamps instead of the plain wrapped text."""
    blocks = ["WEBVTT\n"]
    for c in cues:
        body = _karaoke_payload(c) if karaoke else "\n".join(c.lines)
        blocks.append("%s --> %s\n%s\n" % (
            format_timestamp(c.start, "."), format_timestamp(c.end, "."), body))
    return "\n".join(blocks)


def _write_text(path: str, text: str) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)


def write_srt(cues: Sequence[CaptionCue], path: str) -> None:
    """Write ``cues`` as a SubRip ``.srt`` file (LF, UTF-8)."""
    _write_text(path, srt_text(cues))


def write_vtt(cues: Sequence[CaptionCue], path: str, *,
              karaoke: bool = False) -> None:
    """Write ``cues`` as a WebVTT ``.vtt`` file (LF, UTF-8)."""
    _write_text(path, vtt_text(cues, karaoke=karaoke))


def write_captions(text: str, duration: float, path: str, *, g2p=None,
                   karaoke: bool = False, cps: float = DEFAULT_CPS,
                   max_line: int = DEFAULT_MAX_LINE,
                   max_lines: int = DEFAULT_MAX_LINES,
                   gap: float = DEFAULT_GAP) -> List[CaptionCue]:
    """Derive cues from ``text`` + ``duration`` (the shared naive alignment) and
    write them to ``path`` as ``.vtt`` (WebVTT) or ``.srt`` (SubRip, the default
    for any other extension). Returns the cues. ``karaoke`` applies to WebVTT."""
    cues = build_cues(word_timings(text, duration, g2p), cps=cps,
                      max_line=max_line, max_lines=max_lines, gap=gap)
    if path.lower().endswith(".vtt"):
        write_vtt(cues, path, karaoke=karaoke)
    else:
        write_srt(cues, path)
    return cues
