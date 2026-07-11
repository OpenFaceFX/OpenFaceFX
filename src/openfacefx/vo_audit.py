"""VO delivery QA at scale (issue #42): reconcile a delivered folder against the
loc-table manifest.

Localization vendors run a pre-delivery QA pass that reconciles the delivered VO
against the script — missing lines, orphan files, naming-convention violations,
empty takes, and script<->audio duration mismatches. This is that pass as
deterministic set/arithmetic over file stats + the #40 manifest — no ML, and
**read-only** over the delivered folder (it only walks it and reads WAV headers;
it never writes there). It is the reconciliation pair to
:mod:`openfacefx.batch_manifest`: it shares that module's
:func:`~openfacefx.batch_manifest.read_manifest` parser and reuses
:func:`openfacefx.pipeline.wav_duration` for stats.

:func:`audit_delivery` returns a deterministic, itemized report — a superset of
the ``batch_summary.json`` shape (``format``/``version`` self-describing, stable
key order, every list sorted) — keyed by loc-ID:

  * **missing** — a manifest row whose declared audio is absent from the delivery;
  * **orphan** — a delivered ``.wav`` that no manifest row references;
  * **duration** — actual length outside a configurable tolerance of the length
    estimated from the transcript (``len(text) / cps`` seconds);
  * **empty** — a zero-duration or near-silent (~0 RMS) take;
  * **naming** — a delivered file whose stem does not match the loc-ID convention;
  * **unreadable** — a file ``wav_duration`` cannot parse.

plus a **language-coverage matrix** (``{loc-ID: {locale: present}}``) that surfaces
per-locale holes. Audio paths in the manifest are resolved relative to the
delivered folder (the delivery root). Deterministic and stdlib + numpy (RMS only).
"""

from __future__ import annotations

import os
import re
from typing import Dict, List, Optional

from .batch_manifest import read_manifest
from .pipeline import wav_duration

AUDIT_FORMAT = "openfacefx.vo_audit"
AUDIT_VERSION = 1

#: A take at or below this many seconds is "empty"; at or below this normalized
#: RMS (0..1, 16-bit) it is "near-silent". Both are floors, never flagged above.
EMPTY_DURATION = 0.02
SILENT_RMS = 1e-4

#: Default speaking rate (characters per second) for the expected-length
#: estimate, and the default relative duration tolerance (a fraction).
DEFAULT_CPS = 14.0
DEFAULT_TOLERANCE = 0.5

# Worst-first ordering for the human table / itemized list (stable secondary
# sort on id/locale/file keeps CI diffs deterministic).
_SEVERITY = {"missing": 0, "empty": 1, "unreadable": 2, "duration": 3,
             "naming": 4, "orphan": 5}


def _scan_wavs(root: str) -> List[str]:
    """Every ``.wav`` under ``root`` as a sorted, forward-slash relative path
    (read-only: an ``os.walk``, no writes)."""
    out = []
    for dirpath, _dirs, files in os.walk(root):
        for f in files:
            if f.lower().endswith(".wav"):
                rel = os.path.relpath(os.path.join(dirpath, f), root)
                out.append(rel.replace(os.sep, "/"))
    return sorted(out)


def _norm_rel(path: str) -> str:
    """A manifest audio path as a delivered-relative key (forward slashes)."""
    return os.path.normpath(path).replace(os.sep, "/")


def _expected_stem(loc_id: str) -> str:
    """The filename stem the loc-ID convention implies (the last path/segment
    component of the ID) — what a delivered file's stem is checked against."""
    return re.split(r"[\\/:]", loc_id)[-1]


def _rms(path: str) -> Optional[float]:
    """Normalized RMS (0..1) of a 16-bit PCM WAV, or ``None`` for other widths
    (the near-silent check is skipped then). Read-only sample read."""
    import wave

    import numpy as np
    with wave.open(path, "rb") as w:
        if w.getsampwidth() != 2:
            return None
        frames = w.readframes(w.getnframes())
    if not frames:
        return 0.0
    data = np.frombuffer(frames, dtype=np.int16).astype(np.float64)
    return float(np.sqrt(np.mean(data * data))) / 32768.0


def audit_delivery(manifest_path: str, delivered_dir: str, *,
                   duration_tolerance: float = DEFAULT_TOLERANCE,
                   cps: float = DEFAULT_CPS) -> Dict:
    """Reconcile ``delivered_dir`` against the loc-table ``manifest_path``.

    Returns the deterministic audit report dict. ``duration_tolerance`` is the
    fraction a take may differ from its ``len(text)/cps`` estimate before it is
    flagged — a take inside ``[expected*(1-tol), expected*(1+tol)]`` is never a
    duration issue. Nothing under ``delivered_dir`` is written."""
    rows = read_manifest(manifest_path)
    delivered = set(_scan_wavs(delivered_dir))
    languages = sorted({(r.get("language") or "") for r in rows})
    referenced: set = set()
    coverage: Dict[str, Dict[str, bool]] = {}
    issues: List[Dict] = []

    for i, row in enumerate(rows):
        loc = row.get("id") or (
            os.path.splitext(os.path.basename(row["audio"]))[0]
            if row.get("audio") else "row%d" % (i + 1))
        lang = row.get("language") or ""
        audio = row.get("audio")
        present = False
        if not audio:
            issues.append(dict(id=loc, language=lang, kind="missing", audio=None,
                               detail="no audio path declared in the manifest"))
        else:
            rel = _norm_rel(audio)
            referenced.add(rel)
            full = os.path.join(delivered_dir, audio)
            if not os.path.isfile(full):
                issues.append(dict(id=loc, language=lang, kind="missing",
                                   audio=rel, detail="declared audio not delivered"))
            else:
                present = _audit_present(loc, lang, rel, full, row, cps,
                                         duration_tolerance, issues)
        cov = coverage.setdefault(loc, {})
        cov[lang] = cov.get(lang, False) or present

    for f in sorted(delivered - referenced):
        issues.append(dict(id=None, language=None, kind="orphan", audio=f,
                           detail="delivered audio has no manifest key"))

    issues.sort(key=lambda it: (_SEVERITY.get(it["kind"], 9), it.get("id") or "",
                                it.get("language") or "", it.get("audio") or ""))
    matrix = {loc: {lang: coverage[loc].get(lang, False) for lang in languages}
              for loc in sorted(coverage)}
    counts = dict(rows=len(rows), delivered=len(delivered),
                  referenced=len(referenced), issues=len(issues))
    for kind in _SEVERITY:
        counts[kind] = sum(1 for it in issues if it["kind"] == kind)
    return dict(format=AUDIT_FORMAT, version=AUDIT_VERSION,
                manifest=manifest_path, delivered=delivered_dir,
                cps=cps, duration_tolerance=duration_tolerance,
                counts=counts, languages=languages, coverage=matrix,
                issues=issues)


def _audit_present(loc, lang, rel, full, row, cps, tol, issues) -> bool:
    """Naming + duration + empty checks for a delivered file. Returns whether it
    counts as usable coverage (a readable, non-empty take)."""
    stem = os.path.splitext(os.path.basename(rel))[0]
    want = _expected_stem(loc)
    if stem != want:
        issues.append(dict(id=loc, language=lang, kind="naming", audio=rel,
                           detail="file stem %r does not match loc-ID %r"
                           % (stem, want)))
    try:
        dur = wav_duration(full)
        rms = _rms(full)
    except Exception as exc:                          # unreadable / not PCM WAV
        issues.append(dict(id=loc, language=lang, kind="unreadable", audio=rel,
                           detail="%s: %s" % (type(exc).__name__, exc)))
        return False
    if dur <= EMPTY_DURATION or (rms is not None and rms <= SILENT_RMS):
        issues.append(dict(id=loc, language=lang, kind="empty", audio=rel,
                           duration=round(dur, 4),
                           rms=None if rms is None else round(rms, 6),
                           detail="zero-duration or near-silent take"))
        return False
    text = row.get("text")
    if text and cps > 0:
        expected = len(text) / cps
        lo, hi = expected * (1.0 - tol), expected * (1.0 + tol)
        if dur < lo or dur > hi:
            issues.append(dict(id=loc, language=lang, kind="duration", audio=rel,
                               expected=round(expected, 4), actual=round(dur, 4),
                               detail="%.2fs outside +/-%.0f%% of the %.2fs estimate"
                               % (dur, tol * 100, expected)))
    return True


def audit_report_text(report: Dict) -> str:
    """A human, worst-first summary of an :func:`audit_delivery` report: the
    itemized issues then the language-coverage matrix (present ``*`` / hole ``.``)."""
    c = report["counts"]
    lines = ["VO delivery audit: %d row(s), %d delivered file(s), %d issue(s)"
             % (c["rows"], c["delivered"], c["issues"])]
    if c["issues"]:
        lines.append("  " + "  ".join("%s=%d" % (k, c[k]) for k in _SEVERITY
                                      if c[k]))
    for it in report["issues"]:
        who = it.get("id") or "-"
        lang = (" [%s]" % it["language"]) if it.get("language") else ""
        lines.append("  %-9s %s%s  %s" % (it["kind"], who, lang, it["detail"]))
    langs = report["languages"]
    if langs and report["coverage"]:
        lines.append("\ncoverage  " + "  ".join(langs))
        for loc, cov in report["coverage"].items():
            cells = "  ".join(("*" if cov[la] else ".").center(max(len(la), 1))
                              for la in langs)
            lines.append("  %-16s %s" % (loc, cells))
    return "\n".join(lines)
