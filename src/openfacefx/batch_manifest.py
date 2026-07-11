"""Loc-table / dialogue-database manifest ingestion for ``batch`` (issue #40).

Real game VO is driven by a **localization string table**, not a directory of
same-stem ``.wav`` + ``.txt`` files: Unity / Godot / Unreal export String Table
Collections keyed by a loc-ID, and FaceFX keys VO to an *entrytag*. This module
is the stdlib ``csv`` ingestion layer that feeds those tables into the existing
:mod:`openfacefx.batch` pipeline — distinct from the i18n pronunciation model
(#8); this is purely the *ingestion* front-end.

:func:`read_manifest` parses a CSV/TSV table into normalized rows, and
:func:`manifest_jobs` turns those rows into the same job dicts
:func:`openfacefx.batch._process_one` already consumes, so each row runs through
the unchanged pipeline + output writers and lands in the same summary table,
NDJSON stream and ledger. A ``--manifest`` run and a directory-walk run share
everything downstream of job discovery.

The column mapping is **header-driven and forgiving** — headers are matched
case-insensitively with punctuation/spacing ignored, against a table of common
names (see :data:`COLUMN_ALIASES`). The recognized fields:

  * **id** (``id`` / ``key`` / ``loc-id`` / ``entrytag`` / ``name``) — the row
    key; also the summary label and the derived output stem.
  * **audio** (``audio`` / ``wav`` / ``file`` / ``voice`` / ``sound``) — the
    voice clip, resolved relative to the manifest's own directory.
  * **text** (``text`` / ``transcript`` / ``line`` / ``dialogue`` / ``string``)
    — the spoken transcript, inline in the table.
  * **language** (``lang`` / ``language`` / ``locale``) and **character**
    (``character`` / ``speaker`` / ``actor``) — threaded onto the row as
    metadata.
  * **mapping** (``mapping`` / ``rig``) and **style** (``style`` / ``coart``) —
    per-row solve options (a rig JSON, a coarticulation style preset).
  * **textgrid** (``textgrid`` / ``alignment``) — an optional per-row MFA
    TextGrid (the accurate path); **out** (``out`` / ``output`` / ``track``) —
    an explicit output path, else ``<id>.<ext>`` under the output tree.

CSV/TSV only; PO / XLIFF and the pivoted one-column-per-locale layout are noted
future follow-ups (a pivoted table degrades to per-row "no transcript" failures,
never a crash). Stdlib only (``csv``); deterministic.
"""

from __future__ import annotations

import csv
import io
import os
import re
from typing import Dict, List, Optional

#: Normalized header (lower-cased, non-alphanumerics stripped) -> canonical field.
COLUMN_ALIASES: Dict[str, str] = {
    "id": "id", "key": "id", "locid": "id", "loc": "id", "locionid": "id",
    "entrytag": "id", "name": "id", "stringid": "id", "line_id": "id",
    "lineid": "id",
    "audio": "audio", "wav": "audio", "file": "audio", "audiofile": "audio",
    "audiopath": "audio", "voice": "audio", "sound": "audio", "clip": "audio",
    "voiceclip": "audio", "filename": "audio",
    "text": "text", "transcript": "text", "line": "text", "dialogue": "text",
    "string": "text", "source": "text", "value": "text", "utterance": "text",
    "lang": "language", "language": "language", "locale": "language",
    "character": "character", "char": "character", "speaker": "character",
    "actor": "character", "voiceactor": "character", "npc": "character",
    "mapping": "mapping", "rig": "mapping", "map": "mapping",
    "style": "style", "coart": "style", "coartstyle": "style",
    "textgrid": "textgrid", "alignment": "textgrid", "align": "textgrid",
    "out": "out", "output": "out", "outpath": "out", "track": "out",
    "outputpath": "out", "outfile": "out",
}

# The canonical fields a normalized manifest row always carries (all Optional).
_FIELDS = ("id", "audio", "text", "language", "character", "mapping", "style",
           "textgrid", "out")


def _norm_header(h: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (h or "").strip().lower())


def _delimiter(path: str, text: str) -> str:
    """Tab for ``.tsv``/``.tab``, comma for ``.csv``; otherwise sniff the header
    line (tab-delimited when it has more tabs than commas)."""
    low = path.lower()
    if low.endswith((".tsv", ".tab")):
        return "\t"
    if low.endswith(".csv"):
        return ","
    head = text.splitlines()[0] if text else ""
    return "\t" if head.count("\t") > head.count(",") else ","


def read_manifest(path: str) -> List[Dict[str, Optional[str]]]:
    """Parse a CSV/TSV loc-table into normalized rows.

    Each row is a dict over :data:`the canonical fields <COLUMN_ALIASES>` with
    ``None`` for any absent/blank cell. The header is matched forgivingly (case,
    spacing and punctuation are ignored); the first column mapping to a canonical
    field wins. A UTF-8 BOM (common in Unity exports) is stripped. Raises
    ``ValueError`` if the file has no header row (a file-level error, distinct
    from a bad row — which surfaces later as a per-row failure)."""
    with open(path, encoding="utf-8-sig", newline="") as fh:
        text = fh.read()
    reader = csv.DictReader(io.StringIO(text), delimiter=_delimiter(path, text))
    if not reader.fieldnames:
        raise ValueError(f"manifest {path} has no header row")
    colmap: Dict[str, str] = {}
    for raw in reader.fieldnames:
        canon = COLUMN_ALIASES.get(_norm_header(raw))
        if canon and canon not in colmap.values():   # first column wins
            colmap[raw] = canon
    rows: List[Dict[str, Optional[str]]] = []
    for raw_row in reader:
        row: Dict[str, Optional[str]] = {f: None for f in _FIELDS}
        for raw, canon in colmap.items():
            val = (raw_row.get(raw) or "").strip()
            row[canon] = val or None
        rows.append(row)
    return rows


def _resolve(pathish: Optional[str], base_dir: str) -> Optional[str]:
    """Resolve a manifest path relative to the manifest's own directory (an
    absolute path is left untouched); ``None`` stays ``None``."""
    if not pathish:
        return None
    return pathish if os.path.isabs(pathish) else os.path.join(base_dir, pathish)


def _out_stem(loc_id: str) -> str:
    """A filesystem-safe flat stem from a loc-ID (``ui/menu:start`` ->
    ``ui_menu_start``), so a derived output path can never escape the tree."""
    stem = re.sub(r"[^A-Za-z0-9._-]+", "_", loc_id).strip("_.")
    return stem or "row"


def manifest_jobs(rows: List[Dict[str, Optional[str]]], out_dir: str, ext: str,
                  base_dir: str) -> List[Dict]:
    """Turn normalized manifest rows into ``batch._process_one`` job dicts.

    Audio / mapping / textgrid paths resolve relative to ``base_dir`` (the
    manifest's directory). The output path is the row's explicit ``out`` column
    (relative to ``out_dir``, or absolute) when given, else ``<id>.<ext>`` under
    ``out_dir``. The loc-ID is the row key, summary label and, when needed, the
    output stem; a row with neither id nor audio falls back to ``row<N>``."""
    jobs: List[Dict] = []
    for i, row in enumerate(rows):
        loc_id = row.get("id") or (
            os.path.splitext(os.path.basename(row["audio"]))[0]
            if row.get("audio") else "row%d" % (i + 1))
        out_col = row.get("out")
        if out_col:
            out_rel = out_col
            out = out_col if os.path.isabs(out_col) else os.path.join(out_dir,
                                                                      out_col)
        else:
            out_rel = _out_stem(loc_id) + "." + ext
            out = os.path.join(out_dir, out_rel)
        jobs.append(dict(
            id=loc_id, rel=loc_id, out=out, out_rel=out_rel,
            wav=_resolve(row.get("audio"), base_dir) or "",
            text=row.get("text"),                 # inline transcript (may be None)
            textgrid=_resolve(row.get("textgrid"), base_dir),
            txt=None,
            mapping=_resolve(row.get("mapping"), base_dir),
            style=row.get("style"),
            language=row.get("language"),
            character=row.get("character"),
        ))
    return jobs
