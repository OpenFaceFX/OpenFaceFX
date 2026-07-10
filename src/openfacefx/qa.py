"""QA and embedding ergonomics (issue #23).

Small, stdlib-only, deterministic helpers that make OpenFaceFX compose cleanly
inside other tools and CI -- machine-readable status instead of scraped console
text:

  * ``normalize_transcript`` -- fold the Unicode punctuation a TTS engine or a
    copy-pasted script tends to carry (ellipsis, en/em dashes, curly quotes,
    non-breaking space) down to ASCII *before* phonemisation, and report what
    was substituted. Already-clean ASCII is returned unchanged, so it is a
    byte-identical no-op on the transcripts existing callers pass.

  * ``cue_flags`` -- phoneme cues shorter/longer than a threshold: the analogue
    of the red-flagged cues a lip-sync editor surfaces for a human glance (a 5 ms
    viseme reads as a click, a 900 ms one as a stuck mouth).

  * ``summarize`` -- a deterministic, machine-readable QA summary of a generated
    track (counts, OOV words, cue outliers, normalization substitutions,
    warnings). The single source of truth behind the CLI ``--json`` / ``--report``
    flags, and callable directly when embedding the pipeline.

Same inputs, same bytes: nothing here uses a clock, a locale, or an RNG.
"""

from __future__ import annotations

from typing import Dict, List, Optional

from .phonemes import SILENCE, strip_stress

# Unicode -> ASCII folds applied ahead of G2P. The source characters are pairwise
# distinct and no replacement reintroduces another source, so counts taken
# against the original text are exact and the pass order does not matter.
_NORMALIZE = [
    ("…", "..."),   # … horizontal ellipsis
    ("—", "--"),    # — em dash
    ("–", "-"),     # – en dash
    ("‘", "'"),     # ‘ left single quote
    ("’", "'"),     # ’ right single quote / apostrophe
    ("“", '"'),     # “ left double quote
    ("”", '"'),     # ” right double quote
    ("\u00a0", " "),    # U+00A0 non-breaking space -> ASCII space
]

# Default cue-duration thresholds (seconds); overridable per call and via the CLI
# --min-cue/--max-cue. Below/above these a phoneme cue is worth manual attention.
MIN_CUE = 0.03
MAX_CUE = 0.5


def normalize_transcript(text: str):
    """Fold transcript Unicode punctuation to ASCII before phonemisation.

    Returns ``(normalized_text, substitutions)`` where ``substitutions`` is a
    deterministic ``list[{"from", "to", "count"}]`` of the folds that fired --
    empty for already-ASCII text, i.e. a byte-identical no-op. The curly
    apostrophe is the load-bearing case: ``it's`` typed with U+2019 otherwise
    splits into two tokens at G2P and mangles the phonemes; the rest (ellipsis,
    dashes, curly double quotes, NBSP) are word separators either way but are
    folded so the substitution report is honest about what the input contained."""
    subs: List[Dict] = []
    for src, dst in _NORMALIZE:
        n = text.count(src)
        if n:
            subs.append({"from": src, "to": dst, "count": n})
    out = text
    for src, dst in _NORMALIZE:
        out = out.replace(src, dst)
    return out, subs


def cue_flags(segments, min_dur: float = MIN_CUE,
              max_dur: float = MAX_CUE) -> List[Dict]:
    """Phoneme cues whose duration falls outside ``[min_dur, max_dur]`` seconds.

    Silence cues are ignored (a long pause is not an error). Returns a
    time-sorted ``list[{"phoneme", "start", "duration", "kind"}]`` where ``kind``
    is ``"short"`` or ``"long"`` -- clip, time and duration for each cue a
    lip-sync editor would flag for manual attention."""
    out: List[Dict] = []
    for s in segments or []:
        if s.phoneme == SILENCE or strip_stress(s.phoneme).lower() == "sil":
            continue
        d = s.end - s.start
        kind = "short" if d < min_dur else "long" if d > max_dur else None
        if kind:
            out.append({"phoneme": s.phoneme, "start": round(s.start, 4),
                        "duration": round(d, 4), "kind": kind})
    out.sort(key=lambda c: (c["start"], c["phoneme"]))
    return out


def summarize(track=None, *, output: Optional[str] = None,
              command: Optional[str] = None, segments=None,
              oov_words=None, substitutions=None, warnings=None,
              min_cue: float = MIN_CUE, max_cue: float = MAX_CUE) -> Dict:
    """A deterministic, machine-readable QA summary of a generated track.

    The single source of truth behind the CLI ``--json`` / ``--report`` output,
    and callable directly when embedding the pipeline -- ``summarize(track)``
    returns a plain, JSON-ready dict (stable key order; ``format``/``version``
    self-describing) so a CI step can assert on counts, OOV words, cue outliers
    and warnings without scraping human text.

    ``track`` may be ``None`` (a ``.lip`` write has phoneme ``segments`` but no
    curve track): channel/keyframe counts are then 0 and the duration comes from
    the segments. Every key is always present -- lists empty rather than absent --
    so the schema is stable across inputs."""
    from .gestures import GESTURE_CHANNELS
    chans = list(track.channels) if track is not None else []
    if track is not None:
        duration = track.duration
    elif segments:
        duration = max((s.end for s in segments), default=0.0)
    else:
        duration = 0.0
    oov = list(oov_words or [])
    # warnings[] is the at-a-glance "needs attention" list: caller-collected
    # warnings (unknown vendor symbols, edit conflicts, ...) first, then the two
    # rollups summarize() can derive itself so a library caller gets them for
    # free. Fixed order => deterministic.
    warns = list(warnings or [])
    if oov:
        shown = ", ".join(oov[:6]) + ("…" if len(oov) > 6 else "")
        warns.append(f"{len(oov)} word(s) fell back to G2P rules "
                     f"(add to a pronunciation dict): {shown}")
    if track is not None and not chans:
        warns.append("no channels generated: silent or empty input")
    return {
        "format": "openfacefx.qa",
        "version": 1,
        "command": command,
        "output": output,
        "fps": track.fps if track is not None else None,
        "duration": round(duration, 4),
        "channels": len(chans),
        "keyframes": sum(len(c.keys) for c in chans),
        "gestures": sum(1 for c in chans if c.name in GESTURE_CHANNELS),
        "events": len(track.events) if track is not None else 0,
        "oov_words": oov,
        "substitutions": [dict(s) for s in (substitutions or [])],
        "cue_warnings": cue_flags(segments, min_cue, max_cue) if segments else [],
        "warnings": warns,
    }
