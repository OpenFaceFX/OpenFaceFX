"""Import stepped mouth-cue files back into a FaceTrack (issue #44).

The verified inverse of :mod:`openfacefx.export_cues`. That module *writes* the
stepped mouth-shape lists the indie 2D lip-sync ecosystem reads; this one *reads*
them back, so a studio sitting on a Rhubarb / Papagayo / Moho library can bring
it into OpenFaceFX to coarticulate, retarget, layer gestures/events, condition
and re-export. Because we own both halves, each parser is the exact inverse of
shipping code:

  * Rhubarb Lip Sync TSV / XML / JSON   -- ``parse_rhubarb_{tsv,xml,json}``
  * Moho / OpenToonz switch data (.dat) -- ``parse_moho_dat``
  * Papagayo-NG (.pgo)                  -- ``parse_pgo``

Each parser yields a canonical list of ``(start, end, shape)`` second-intervals.
Rhubarb files are seconds-based (``%.2f``); Moho ``.dat`` / Papagayo ``.pgo`` are
1-based truncated frames, inverting ``export_cues._frame_at`` / ``_to_frames``
(``.dat`` has no stored rate, default 24; ``.pgo`` carries its own). Shape IDs map
back to viseme channels by inverting the very retarget presets the writers use
(:data:`RHUBARB_TO_VISEME` / :data:`PRESTON_BLAIR_TO_VISEME`, *derived* from
``retarget.PRESETS`` so they can never drift); an extended/unknown shape routes
through the documented ``RHUBARB_EXTENDED_FALLBACK`` and is *reported*, never
silently dropped. The result is a stepped viseme :class:`FaceTrack`
(``reduce_to_track``, one ``[0, 1]`` channel per viseme, ``sil`` in the gaps) that
flows unchanged through every exporter and ``--retarget``; ``--coarticulate``
re-solves it through the dominance blend to smooth the hard steps.

Deterministic, stdlib + numpy only (``csv``-style split, ``xml.etree``, ``json``,
``re``). A new, purely additive command/module: no existing output changes.
"""

from __future__ import annotations

import json
import os
import re
import xml.etree.ElementTree as ET
from typing import Dict, List, Optional, Tuple

import numpy as np

from .curves import FaceTrack, reduce_to_track
from .export_cues import RHUBARB_EXTENDED_FALLBACK, _PB_SHAPES, _RHUBARB_SHAPES
from .phonemes import SILENCE
from .retarget import PRESETS
from .visemes import PHONEME_TO_VISEME, VISEME_INDEX, VISEMES

# Blendshape-weight CSV reader (issue #45) lives in its own module to keep this
# one under the size budget; re-exported here so `importers.read_csv` resolves.
from .importers_csv import read_csv  # noqa: E402,F401

Interval = Tuple[float, float, str]     # (start_sec, end_sec, shape or viseme)

_FORMATS = ("tsv", "xml", "json", "dat", "pgo")
_EXT_FORMAT = {".tsv": "tsv", ".xml": "xml", ".json": "json",
               ".dat": "dat", ".pgo": "pgo"}
# Seconds-based Rhubarb prints hundredths (%.2f); reconstruct on the matching
# 100 fps grid so every cue boundary lands exactly on a frame and re-export is
# lossless. Frame-based .dat has no stored rate.
_RHUBARB_FPS = 100.0
_DAT_DEFAULT_FPS = 24.0
_EPS = 1e-9


# --------------------------------------------------------------------------- #
# Inverse shape tables -- derived from the forward presets so they never drift #
# --------------------------------------------------------------------------- #

def _invert_preset(preset: Dict) -> Dict[str, str]:
    """``shape -> representative viseme``: the first viseme in the canonical
    :data:`VISEMES` order that the forward preset sends to that shape. Many
    visemes collapse to one shape (e.g. several consonants -> Rhubarb ``B``); the
    representative is deterministic and, by construction, retargets straight back
    to the same shape -- so a stepped track of these visemes re-exports to the
    identical cue file."""
    inv: Dict[str, str] = {}
    for viseme in VISEMES:
        for shape, _weight in preset.get(viseme, ()):
            inv.setdefault(shape, viseme)
    return inv


#: Rhubarb A-H/X shape -> canonical viseme (inverse of ``PRESETS['rhubarb']``).
RHUBARB_TO_VISEME: Dict[str, str] = _invert_preset(PRESETS["rhubarb"])

#: Preston-Blair shape -> canonical viseme (inverse of ``PRESETS['preston_blair']``).
#: ``WQ`` is the one Preston-Blair shape the forward preset never emits (viseme
#: input can't split W from UW), so it is mapped by hand to the rounded ``U``.
PRESTON_BLAIR_TO_VISEME: Dict[str, str] = dict(_invert_preset(PRESETS["preston_blair"]))
PRESTON_BLAIR_TO_VISEME.setdefault("WQ", "U")

#: Fleming-Dobbs shape -> canonical viseme (inverse of ``PRESETS['fleming_dobbs']``,
#: issue #71) — for importing an FD-labelled mouth-shape timeline back to visemes
#: via :func:`build_cue_track`. Every FD shape the forward preset emits is covered.
FLEMING_DOBBS_TO_VISEME: Dict[str, str] = _invert_preset(PRESETS["fleming_dobbs"])

#: Representative ARPABET phoneme per viseme (inverse of ``PHONEME_TO_VISEME``),
#: used only by ``--coarticulate`` to synthesise segments for the dominance solve.
_VISEME_TO_PHONEME: Dict[str, str] = {}
for _phoneme, _viseme in PHONEME_TO_VISEME.items():
    _VISEME_TO_PHONEME.setdefault(_viseme, _phoneme)
_VISEME_TO_PHONEME.setdefault("sil", SILENCE)


# --------------------------------------------------------------------------- #
# Format detection                                                             #
# --------------------------------------------------------------------------- #

def detect_format(path: str, text: str) -> str:
    """Pick a parser by file extension, then confirm/override by the first line
    (so a mislabelled extension still routes correctly)."""
    head = text.lstrip()[:64]
    if head.startswith("MohoSwitch1"):
        return "dat"
    if head.startswith("lipsync version"):
        return "pgo"
    fmt = _EXT_FORMAT.get(os.path.splitext(path)[1].lower())
    if fmt is not None:
        return fmt
    if head.startswith("<?xml") or head.startswith("<rhubarb"):
        return "xml"
    if head.startswith("{"):
        return "json"
    return "tsv"


# --------------------------------------------------------------------------- #
# Parsers -> canonical (start, end, shape) second-intervals                    #
# --------------------------------------------------------------------------- #

def _nonblank(text: str) -> List[str]:
    return [ln for ln in text.splitlines() if ln.strip()]


def _switch_intervals(rows: List[Tuple[float, str]]) -> Tuple[List[Interval], float]:
    """Turn switch rows ``(time, shape)`` (each shape held until the next row,
    the final row a terminal end sentinel) into ``(start, end, shape)`` intervals.
    Matches the TSV / .dat writers, whose last row bounds the last real cue."""
    times = [t for t, _ in rows]
    for a, b in zip(times, times[1:]):
        if b < a - _EPS:
            raise ValueError(f"cue times must be non-decreasing, got {a} then {b}")
    intervals = [(rows[i][0], rows[i + 1][0], rows[i][1])
                 for i in range(len(rows) - 1)]
    return intervals, (times[-1] if times else 0.0)


def parse_rhubarb_tsv(text: str) -> Tuple[List[Interval], float]:
    rows: List[Tuple[float, str]] = []
    for i, ln in enumerate(_nonblank(text), 1):
        parts = ln.split("\t")
        if len(parts) != 2:
            raise ValueError(f"rhubarb tsv line {i}: expected 'start<TAB>shape', got {ln!r}")
        try:
            start = float(parts[0])
        except ValueError:
            raise ValueError(
                f"rhubarb tsv line {i}: non-numeric start time {parts[0]!r}") from None
        rows.append((start, parts[1].strip()))
    return _switch_intervals(rows)


def parse_rhubarb_xml(text: str) -> Tuple[List[Interval], float]:
    try:
        root = ET.fromstring(text)
    except ET.ParseError as e:
        raise ValueError(f"rhubarb xml: not well-formed ({e})") from None
    cues = root.find("mouthCues")
    intervals: List[Interval] = []
    for i, mc in enumerate(list(cues) if cues is not None else []):
        start, end = mc.get("start"), mc.get("end")
        if start is None or end is None:
            raise ValueError(
                f"rhubarb xml: <mouthCue> {i} missing 'start'/'end' attribute")
        try:
            intervals.append((float(start), float(end), (mc.text or "").strip()))
        except ValueError:
            raise ValueError(f"rhubarb xml: <mouthCue> {i} non-numeric start/end "
                             f"({start!r}, {end!r})") from None
    dur_el = root.find("metadata/duration")
    dur = (float(dur_el.text) if dur_el is not None and dur_el.text
           else (intervals[-1][1] if intervals else 0.0))
    return intervals, dur


def parse_rhubarb_json(text: str) -> Tuple[List[Interval], float]:
    try:
        d = json.loads(text)
    except json.JSONDecodeError as e:
        raise ValueError(f"rhubarb json: not valid JSON ({e})") from None
    if not isinstance(d, dict) or "mouthCues" not in d:
        raise ValueError("rhubarb json: no 'mouthCues' array "
                         "(is this an openfacefx.track, not a cue file?)")
    cues = d["mouthCues"]
    if not isinstance(cues, list):
        raise ValueError(
            f"rhubarb json: 'mouthCues' must be a list, got {type(cues).__name__}")
    intervals: List[Interval] = []
    for i, c in enumerate(cues):
        if not isinstance(c, dict):
            raise ValueError(
                f"rhubarb json: cue {i} must be an object, got {type(c).__name__}")
        if "start" not in c or "end" not in c or "value" not in c:
            raise ValueError(
                f"rhubarb json: cue {i} missing required 'start'/'end'/'value'")
        try:
            intervals.append((float(c["start"]), float(c["end"]), str(c["value"])))
        except (TypeError, ValueError):
            raise ValueError(f"rhubarb json: cue {i} has non-numeric start/end") from None
    meta = d.get("metadata") or {}
    dur = float(meta.get("duration", intervals[-1][1] if intervals else 0.0))
    return intervals, dur


def parse_moho_dat(text: str, fps: float) -> Tuple[List[Interval], float]:
    rows = _nonblank(text)
    if not rows or rows[0].strip() != "MohoSwitch1":
        raise ValueError("moho .dat: first line must be 'MohoSwitch1'")
    switch: List[Tuple[float, str]] = []
    for ln in rows[1:]:
        parts = ln.split()
        if len(parts) != 2 or not parts[0].isdigit():
            raise ValueError(f"moho .dat: expected '<frame> <shape>', got {ln!r}")
        switch.append(((int(parts[0]) - 1) / fps, parts[1]))
    return _switch_intervals(switch)


def parse_pgo(text: str) -> Tuple[List[Interval], float, float]:
    """Papagayo-NG ``.pgo``. Returns ``(intervals, duration, fps)`` -- fps and the
    total-frame count are stored in the header, and each phoneme line holds until
    the next (the last runs to the clip end)."""
    lines = text.splitlines()
    if not lines or not lines[0].startswith("lipsync version"):
        raise ValueError("papagayo .pgo: first line must be 'lipsync version ...'")
    try:
        fps = float(int(lines[2].strip()))
        total_frames = int(lines[3].strip())
    except (IndexError, ValueError):
        raise ValueError("papagayo .pgo: header must carry integer fps and "
                         "total-frame count on lines 3 and 4") from None
    phon = [(int(m.group(1)), m.group(2)) for m in
            (re.match(r"^\t\t\t\t(\d+) (\S+)$", ln) for ln in lines) if m]
    dur = total_frames / fps if fps else 0.0
    if not phon:
        return [], dur, fps
    starts = [(f - 1) / fps for f, _ in phon]
    dur = max(dur, starts[-1])
    intervals = [(starts[i], starts[i + 1] if i + 1 < len(phon) else dur, phon[i][1])
                 for i in range(len(phon))]
    return intervals, dur, fps


# --------------------------------------------------------------------------- #
# shape -> viseme, with the documented fallback for extended/unknown shapes    #
# --------------------------------------------------------------------------- #

def _dat_vocab(shapes: set) -> str:
    """A Moho ``.dat`` may carry Preston-Blair names or raw Rhubarb letters
    (``preston_blair=False``). Prefer Preston-Blair (the writer default; ``E`` is
    valid in both) and fall back to Rhubarb."""
    if shapes <= _PB_SHAPES:
        return "pb"
    if shapes <= _RHUBARB_SHAPES:
        return "rhubarb"
    return "pb" if shapes & _PB_SHAPES else "rhubarb"


def _collapse_unknown(shape: str, table: Dict[str, str]) -> Optional[str]:
    """Follow ``RHUBARB_EXTENDED_FALLBACK`` (G->A, H->C, X->A) until a shape the
    inverse ``table`` knows is reached; ``None`` if none is."""
    seen: set = set()
    name = shape
    while name in RHUBARB_EXTENDED_FALLBACK and name not in seen:
        seen.add(name)
        name = RHUBARB_EXTENDED_FALLBACK[name]
        if name in table:
            return name
    return None


def _map_to_visemes(intervals: List[Interval], vocab: str,
                    warnings: List[str]) -> List[Interval]:
    table = RHUBARB_TO_VISEME if vocab == "rhubarb" else PRESTON_BLAIR_TO_VISEME
    out: List[Interval] = []
    reported: set = set()
    for start, end, shape in intervals:
        viseme = table.get(shape)
        if viseme is None:
            collapsed = _collapse_unknown(shape, table)
            if collapsed is None:
                raise ValueError(
                    f"unknown {vocab} mouth shape {shape!r}: not in the inverse "
                    f"table {sorted(table)} nor resolvable via "
                    f"RHUBARB_EXTENDED_FALLBACK")
            viseme = table[collapsed]
            if shape not in reported:
                warnings.append(
                    f"remapped extended/unknown shape {shape!r} -> {collapsed!r} "
                    f"-> viseme {viseme!r} via RHUBARB_EXTENDED_FALLBACK")
                reported.add(shape)
        out.append((start, end, viseme))
    return out


def _merge_adjacent(intervals: List[Interval]) -> List[Interval]:
    """Collapse contiguous runs of the same viseme into one interval. A frame-
    based writer (``_to_frames``) can emit two adjacent identical shapes when it
    drops an intermediate run that collides at the lower frame rate -- a redundant
    switch that holds the same mouth, carrying no animation. Merging yields the
    canonical cue list (and ``dominant_cues`` merges them on re-export anyway)."""
    out: List[Interval] = []
    for start, end, name in intervals:
        if out and out[-1][2] == name and abs(out[-1][1] - start) <= _EPS:
            out[-1] = (out[-1][0], end, name)
        else:
            out.append((start, end, name))
    return out


def _validate_intervals(intervals: List[Interval]) -> None:
    prev_end: Optional[float] = None
    for start, end, _name in intervals:
        if end < start - _EPS:
            raise ValueError(f"cue interval end {end} precedes start {start}")
        if prev_end is not None and start < prev_end - _EPS:
            raise ValueError(
                f"cue intervals overlap: start {start} < previous end {prev_end}")
        prev_end = end


# --------------------------------------------------------------------------- #
# Build a stepped viseme FaceTrack                                             #
# --------------------------------------------------------------------------- #

def build_cue_track(viseme_intervals: List[Interval], fps: float,
                    coarticulate: bool = False) -> FaceTrack:
    """One ``[0, 1]`` channel per viseme, stepped over its intervals (``sil`` in
    the gaps), reduced with :func:`reduce_to_track`. ``coarticulate`` instead
    re-solves synthetic segments through the dominance blend to smooth the steps.

    Built on the ``fps`` grid so the cue boundaries land exactly on frames -- a
    re-``dominant_cues`` reproduces the same runs (the round-trip contract)."""
    if not viseme_intervals:
        return FaceTrack(fps=fps, channels=[], target_set=None)
    if coarticulate:
        return _coarticulate(viseme_intervals, fps)
    duration = viseme_intervals[-1][1]
    n = int(round(duration * fps))
    times = np.array([i / fps for i in range(n + 1)], dtype=float)
    matrix = np.zeros((n + 1, len(VISEMES)), dtype=float)
    j = 0
    for i in range(n + 1):
        t = times[i]
        while j + 1 < len(viseme_intervals) and t >= viseme_intervals[j][1] - _EPS:
            j += 1
        matrix[i, VISEME_INDEX[viseme_intervals[j][2]]] = 1.0
    # epsilon=0: keep every step corner exactly (only collinear holds are thinned)
    # so the flattened steps survive a re-export bit-for-bit.
    return reduce_to_track(times, matrix, fps=fps, epsilon=0.0)


def _coarticulate(viseme_intervals: List[Interval], fps: float) -> FaceTrack:
    from .alignment import PhonemeSegment
    from .pipeline import generate_from_alignment
    segments = [PhonemeSegment(_VISEME_TO_PHONEME.get(vis, SILENCE), start, end)
                for start, end, vis in viseme_intervals]
    return generate_from_alignment(segments, fps=fps)


# --------------------------------------------------------------------------- #
# Public entry point                                                           #
# --------------------------------------------------------------------------- #

def import_cues(path: str, *, fmt: Optional[str] = None, fps: Optional[float] = None,
                coarticulate: bool = False) -> Tuple[FaceTrack, List[str]]:
    """Read a mouth-cue file into a stepped viseme :class:`FaceTrack`.

    ``fmt`` overrides the extension/first-line auto-detection (one of
    ``tsv``/``xml``/``json``/``dat``/``pgo``; ``json-cues`` is accepted as an
    alias of ``json``). ``fps`` sets the frame rate for the rate-less Moho
    ``.dat`` (default 24); Rhubarb is seconds-based (reconstructed at 100 fps) and
    Papagayo carries its own rate, so ``fps`` is ignored for those. Returns
    ``(track, warnings)`` -- ``warnings`` lists any extended/unknown shapes that
    were remapped via the fallback (reported, never dropped)."""
    with open(path, encoding="utf-8") as fh:
        text = fh.read()
    fmt = (fmt or detect_format(path, text)).replace("json-cues", "json")
    if fmt not in _FORMATS:
        raise ValueError(f"unknown cue format {fmt!r}; expected one of {_FORMATS}")
    warnings: List[str] = []
    if fmt in ("tsv", "xml", "json"):
        intervals, _dur = (parse_rhubarb_tsv(text) if fmt == "tsv" else
                           parse_rhubarb_xml(text) if fmt == "xml" else
                           parse_rhubarb_json(text))
        vocab, rfps = "rhubarb", _RHUBARB_FPS
    elif fmt == "dat":
        rfps = float(fps) if fps else _DAT_DEFAULT_FPS
        intervals, _dur = parse_moho_dat(text, rfps)
        vocab = _dat_vocab({name for _s, _e, name in intervals})
    else:  # pgo
        intervals, _dur, rfps = parse_pgo(text)
        vocab = "pb"
    _validate_intervals(intervals)
    viseme_intervals = _merge_adjacent(_map_to_visemes(intervals, vocab, warnings))
    return build_cue_track(viseme_intervals, rfps, coarticulate), warnings
