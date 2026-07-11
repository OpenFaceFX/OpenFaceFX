"""Batch directory processing: a tree of voice lines in, a tree of tracks out.

For every ``.wav`` under ``--dir``, look for a same-stem ``.TextGrid`` (MFA --
accurate path, preferred) or ``.txt`` transcript (naive path), generate a
track, and write it to the mirrored path under ``--out``. A manifest makes
``--modified-only`` re-runs incremental; a summary (printed table + JSON)
reports per-file status, counts, OOV words that fell through to the G2P rule
fallback, and worst-first aligner confidence when the aligner supplies it.
Per-file failures do not stop the batch; the exit code reports them at the
end.

Three opt-in additions (issue #35) layer on top without touching the default
output -- with their flags absent the printed table and ``batch_summary.json``
are byte-identical to before:

  * ``--machine-readable`` streams an NDJSON event log to stderr
    (``start``/``progress``/``warning``/``failure``/``done``, one JSON object per
    line) so a supervising process can follow a large run live. Events are
    emitted in processing order (the summary table stays worst-first sorted).
  * ``--ledger FILE`` appends one NDJSON record per run to a file (args snapshot,
    per-input size/mtime, outcome counts) for reproducibility/audit; it survives
    ``--modified-only`` and carries a deterministic, wall-clock-free run id.
  * ``--cue-warnings`` folds ``qa.cue_flags()`` counts into each summary row and
    the worst-first ranking, so too-short/too-long phoneme cues rank alongside
    OOV words and low confidence.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
from multiprocessing import Pool
from typing import List, Optional

from .coarticulation import style_params
from .g2p import G2P
from .alignment import load_mfa_textgrid
from .io_export import to_dict, write_csv, write_json
from .mapping import Mapping
from .pipeline import generate_from_alignment, naive_segments, wav_duration
from .qa import cue_flags, MIN_CUE, MAX_CUE

MANIFEST_NAME = ".openfacefx-manifest.json"
SUMMARY_NAME = "batch_summary.json"
LEDGER_FORMAT = "openfacefx.batch.ledger"
LEDGER_VERSION = 1


def _stamp(path: str) -> Optional[List[float]]:
    try:
        st = os.stat(path)
        return [st.st_mtime, st.st_size]
    except OSError:
        return None


def find_jobs(in_dir: str, out_dir: str, recurse: bool, ext: str) -> List[dict]:
    jobs = []
    for root, dirs, files in os.walk(in_dir):
        if not recurse:
            dirs.clear()
        for f in sorted(files):
            if not f.lower().endswith(".wav"):
                continue
            stem = os.path.splitext(f)[0]
            wav = os.path.join(root, f)
            rel = os.path.relpath(wav, in_dir)
            tg = os.path.join(root, stem + ".TextGrid")
            txt = os.path.join(root, stem + ".txt")
            out_rel = os.path.splitext(rel)[0] + "." + ext
            jobs.append(dict(
                rel=rel, wav=wav,
                textgrid=tg if os.path.exists(tg) else None,
                txt=txt if os.path.exists(txt) else None,
                out=os.path.join(out_dir, out_rel),
                out_rel=out_rel,
            ))
    return jobs


def _naive_track(text, job, cmudict_path, fps, mapping, params, row):
    """The naive-path track for a transcript, from either a same-stem ``.txt``
    file (directory mode) or an inline manifest ``text`` (issue #40). The
    phoneme segments are returned alongside so the caller can run ``cue_flags``;
    byte-identical to the old inline ``generate_naive(...)`` call."""
    text = text.strip()
    if not text:
        raise ValueError("transcript file is empty")
    g2p = G2P()
    if cmudict_path:
        g2p.load_cmudict(cmudict_path)
    row["oov"] = g2p.oov_words(text)
    dur = wav_duration(job["wav"])
    segs = naive_segments(text, dur, g2p=g2p)
    return segs, generate_from_alignment(segs, fps=fps, mapping=mapping,
                                         params=params)


def _process_one(args) -> dict:
    """Worker (top-level for Windows spawn): returns a summary row.

    ``cue`` is ``None`` (the byte-identical default: no ``cue_warnings`` key on
    the row) or a ``(min_dur, max_dur)`` pair, in which case the row gains an
    integer ``cue_warnings`` count of phoneme cues outside those bounds.

    Manifest jobs (issue #40) additionally carry an ``id`` and per-row
    ``mapping``/``style``/``text``/``language``/``character``; directory jobs
    carry none of those keys, so their rows and tracks stay byte-identical."""
    job, mapping_path, cmudict_path, fps, cue = args
    # out is reported relative to the output tree: relpath against the CWD
    # breaks on Windows when they sit on different drives
    row = dict(file=job["rel"], status="ok", out=job["out_rel"],
               error=None, duration=None, channels=0, keyframes=0,
               oov=[], min_confidence=None, mode=None)
    if cue is not None:
        row["cue_warnings"] = 0
    if "id" in job:                      # manifest-only loc-table metadata
        row["id"] = job["id"]
        row["language"] = job.get("language")
        row["character"] = job.get("character")
    try:
        # per-row mapping/style (manifest) fall back to the batch-global mapping
        # and no style (None) -> directory mode is unchanged.
        mapping_path_eff = job.get("mapping") or mapping_path
        mapping = Mapping.from_json(mapping_path_eff) if mapping_path_eff else None
        params = style_params(job["style"]) if job.get("style") else None
        if job.get("textgrid"):
            row["mode"] = "mfa"
            segs = load_mfa_textgrid(job["textgrid"])
            confs = [s.confidence for s in segs if s.confidence is not None]
            if confs:
                row["min_confidence"] = min(confs)
            track = generate_from_alignment(segs, fps=fps, mapping=mapping,
                                            params=params)
        elif job.get("text") is not None:            # manifest inline transcript
            row["mode"] = "naive"
            segs, track = _naive_track(job["text"], job, cmudict_path, fps,
                                       mapping, params, row)
        elif job.get("txt"):                          # directory same-stem .txt
            row["mode"] = "naive"
            with open(job["txt"], encoding="utf-8") as fh:
                text = fh.read()
            segs, track = _naive_track(text, job, cmudict_path, fps, mapping,
                                       params, row)
        else:
            raise FileNotFoundError(
                "no transcript: expected same-stem .TextGrid or .txt")

        os.makedirs(os.path.dirname(job["out"]) or ".", exist_ok=True)
        if job["out"].endswith(".csv"):
            write_csv(track, job["out"])
        else:
            write_json(track, job["out"])
        d = to_dict(track)
        row.update(duration=d["duration"], channels=len(d["channels"]),
                   keyframes=sum(len(c["keys"]) for c in d["channels"]))
        if cue is not None:
            row["cue_warnings"] = len(cue_flags(segs, cue[0], cue[1]))
    except Exception as e:  # keep the batch going; report at the end
        row.update(status="failed", error=f"{type(e).__name__}: {e}")
    return row


def _row_warnings(row: dict) -> List[str]:
    """Deterministic per-file warning strings for the NDJSON stream: the OOV
    G2P-fallback rollup, then (with --cue-warnings) the out-of-bounds cue count.
    Derived purely from the row, so the same row yields the same warnings."""
    warns: List[str] = []
    oov = row.get("oov") or []
    if oov:
        shown = ", ".join(oov[:6]) + ("..." if len(oov) > 6 else "")
        warns.append(f"{len(oov)} word(s) fell back to G2P rules: {shown}")
    if row.get("cue_warnings"):
        warns.append(f"{row['cue_warnings']} phoneme cue(s) outside the "
                     f"duration bounds")
    return warns


def _ledger_record(*, args_snapshot: dict, inputs: List[dict],
                   outcome: dict, ext: str) -> dict:
    """One append-only run-ledger record. The ``run`` id is a SHA-256 over the
    run's identity (args snapshot + per-input rel/size/mtime), so it is
    deterministic given those inputs and carries no wall-clock: two identical
    re-runs hash the same, a changed file or arg hashes differently. ``mtime`` is
    file metadata (not ``Date.now``); it lives under ``inputs`` for audit and,
    being part of the identity, means an edited input yields a fresh id."""
    identity = json.dumps({"args": args_snapshot, "inputs": inputs},
                          sort_keys=True, ensure_ascii=True)
    run = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:16]
    return {
        "format": LEDGER_FORMAT,
        "version": LEDGER_VERSION,
        "run": run,
        "args": args_snapshot,
        "inputs": {"count": len(inputs), "files": inputs},
        "outcome": outcome,
        "ext": ext,
    }


def _append_ledger(path: str, record: dict) -> None:
    """Append one NDJSON line to the run ledger (created with its parents)."""
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(record) + "\n")


def run_batch(in_dir: str, out_dir: str, recurse: bool = False,
              modified_only: bool = False, jobs: int = 1,
              mapping: Optional[str] = None, cmudict: Optional[str] = None,
              fps: float = 60.0, ext: str = "json",
              machine_readable: bool = False, quiet: bool = False,
              ledger: Optional[str] = None, cue_warnings: bool = False,
              min_cue: Optional[float] = None,
              max_cue: Optional[float] = None,
              manifest_file: Optional[str] = None) -> int:
    """Returns a process exit code (0 = all ok, 1 = at least one failure).

    Every parameter after ``ext`` is additive and opt-in (issue #35); with the
    defaults the printed table and ``batch_summary.json`` are byte-identical to
    before. ``machine_readable`` streams an NDJSON event log to stderr, ``quiet``
    suppresses the human table (the summary JSON and any NDJSON/ledger are still
    written), ``ledger`` appends one NDJSON run record to a file, and
    ``cue_warnings`` folds ``qa.cue_flags()`` counts into each row and the
    worst-first sort (``min_cue``/``max_cue`` default to qa's thresholds).

    ``manifest_file`` (issue #40) selects the loc-table driver: instead of
    walking ``in_dir``, read a CSV/TSV manifest and emit one track per row
    through the same pipeline + writers + summary/NDJSON/ledger. With it absent
    the directory-walk path is byte-identical."""
    lo = MIN_CUE if min_cue is None else min_cue
    hi = MAX_CUE if max_cue is None else max_cue
    cue = (lo, hi) if cue_warnings else None

    def emit(obj: dict) -> None:
        if machine_readable:
            sys.stderr.write(json.dumps(obj) + "\n")
            sys.stderr.flush()

    def emit_progress(index: int, row: dict) -> None:
        warns = _row_warnings(row)
        emit({"event": "progress", "index": index, "file": row["file"],
              "out": row["out"], "status": row["status"], "mode": row["mode"],
              "channels": row["channels"], "keyframes": row["keyframes"],
              "oov": row["oov"], "cue_warnings": row.get("cue_warnings", 0),
              "min_confidence": row["min_confidence"], "warnings": warns})
        for w in warns:
            emit({"event": "warning", "index": index, "file": row["file"],
                  "message": w})
        if row["status"] != "ok":
            emit({"event": "failure", "index": index, "file": row["file"],
                  "error": row["error"]})

    def args_snapshot() -> dict:
        snap = {"dir": in_dir, "out": out_dir, "recurse": recurse,
                "modified_only": modified_only, "jobs": jobs, "ext": ext,
                "mapping": mapping, "cmudict": cmudict, "fps": fps,
                "cue_warnings": cue_warnings, "min_cue": lo, "max_cue": hi}
        if manifest_file is not None:            # only in manifest mode -> the
            snap["manifest"] = manifest_file     # directory-mode snapshot is
        return snap                              # byte-identical

    def input_fingerprints(work: List[dict]) -> List[dict]:
        fps_out = []
        for job in work:
            st = _stamp(job["wav"]) or [None, None]
            kind = ("mfa" if job["textgrid"] else
                    "naive" if (job["txt"] or job.get("text")) else "none")
            fps_out.append({"file": job["rel"], "mtime": st[0], "size": st[1],
                            "transcript": kind})
        fps_out.sort(key=lambda r: r["file"])
        return fps_out

    if manifest_file is not None:
        from .batch_manifest import manifest_jobs, read_manifest
        base = os.path.dirname(os.path.abspath(manifest_file))
        try:
            work = manifest_jobs(read_manifest(manifest_file), out_dir, ext, base)
        except (OSError, ValueError) as exc:
            if not quiet:
                print(f"cannot read manifest {manifest_file}: {exc}")
            return 1
    else:
        work = find_jobs(in_dir, out_dir, recurse, ext)
    if not work:
        emit({"event": "start", "total": 0, "todo": 0, "skipped": 0,
              "jobs": jobs, "ext": ext, "recurse": recurse})
        emit({"event": "done", "processed": 0, "failed": 0, "skipped": 0,
              "exit": 1})
        if ledger:
            _append_ledger(ledger, _ledger_record(
                args_snapshot=args_snapshot(), inputs=[],
                outcome={"processed": 0, "failed": 0, "skipped": 0, "exit": 1},
                ext=ext))
        if not quiet:
            print(f"no rows found in manifest {manifest_file}" if manifest_file
                  else f"no .wav files found under {in_dir}")
        return 1

    manifest_path = os.path.join(out_dir, MANIFEST_NAME)
    manifest = {}
    if os.path.exists(manifest_path):
        with open(manifest_path, encoding="utf-8") as fh:
            manifest = json.load(fh)

    def fingerprint(job):
        fp = {
            "wav": _stamp(job["wav"]),
            "transcript": _stamp(job["textgrid"] or job["txt"] or ""),
            "mapping": _stamp(mapping) if mapping else None,
            "out": job["out"],
        }
        # manifest jobs carry the transcript inline and may name a per-row
        # mapping; fold both into the modified-only key. Directory jobs have
        # neither, so their manifest fingerprint stays byte-identical.
        if job.get("text") is not None:
            fp["text"] = hashlib.sha256(
                job["text"].encode("utf-8")).hexdigest()[:16]
        if job.get("mapping"):
            fp["mapping"] = _stamp(job["mapping"])
        return fp

    todo, skipped = [], 0
    for job in work:
        if (modified_only and manifest.get(job["rel"]) == fingerprint(job)
                and os.path.exists(job["out"])):
            skipped += 1
            continue
        todo.append(job)

    emit({"event": "start", "total": len(work), "todo": len(todo),
          "skipped": skipped, "jobs": jobs, "ext": ext, "recurse": recurse})

    args = [(job, mapping, cmudict, fps, cue) for job in todo]
    if jobs > 1 and len(todo) > 1:
        with Pool(processes=jobs) as pool:
            rows = pool.map(_process_one, args)
        for i, row in enumerate(rows):
            emit_progress(i, row)
    else:
        rows = []
        for i, a in enumerate(args):
            row = _process_one(a)
            rows.append(row)
            emit_progress(i, row)

    for job, row in zip(todo, rows):
        if row["status"] == "ok":
            manifest[job["rel"]] = fingerprint(job)
        else:
            manifest.pop(job["rel"], None)
    os.makedirs(out_dir, exist_ok=True)
    with open(manifest_path, "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, indent=2)

    # worst files first: failures, then lowest confidence, then most OOV. With
    # --cue-warnings the cue count is an extra final tiebreaker -- a strict
    # superset of the old key, so the order (and thus the bytes) are unchanged
    # without the flag.
    def _key(r):
        base = (r["status"] == "ok",
                r["min_confidence"] if r["min_confidence"] is not None else 2.0,
                -len(r["oov"]))
        return base + (-r["cue_warnings"],) if cue_warnings else base
    rows.sort(key=_key)

    summary = dict(processed=len(rows), skipped_unchanged=skipped,
                   failed=sum(1 for r in rows if r["status"] != "ok"),
                   rows=rows)
    with open(os.path.join(out_dir, SUMMARY_NAME), "w", encoding="utf-8") as fh:
        json.dump(summary, fh, indent=2)
    rc = 1 if summary["failed"] else 0

    emit({"event": "done", "processed": len(rows), "failed": summary["failed"],
          "skipped": skipped, "exit": rc})
    if ledger:
        _append_ledger(ledger, _ledger_record(
            args_snapshot=args_snapshot(), inputs=input_fingerprints(work),
            outcome={"processed": len(rows), "failed": summary["failed"],
                     "skipped": skipped, "exit": rc}, ext=ext))

    if not quiet:
        width = max([len(r["file"]) for r in rows] + [4])
        head = (f"{'file':<{width}}  {'status':<7} {'mode':<5} {'dur':>6} "
                f"{'keys':>5}")
        if cue_warnings:
            head += f" {'cue':>4}"
        head += "  oov"
        print(head)
        for r in rows:
            dur = f"{r['duration']:.2f}" if r["duration"] is not None else "-"
            oov = ",".join(r["oov"][:4]) + ("…" if len(r["oov"]) > 4 else "")
            line = (f"{r['file']:<{width}}  {r['status']:<7} "
                    f"{r['mode'] or '-':<5} {dur:>6} {r['keyframes']:>5}")
            if cue_warnings:
                line += f" {r['cue_warnings']:>4}"
            line += f"  {oov}"
            print(line)
            if r["error"]:
                print(f"{'':<{width}}  ! {r['error']}")
        print(f"\n{len(rows)} processed, {skipped} skipped (unchanged), "
              f"{summary['failed']} failed -> "
              f"{os.path.join(out_dir, SUMMARY_NAME)}")
    return rc
