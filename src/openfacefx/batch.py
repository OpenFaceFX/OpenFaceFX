"""Batch directory processing: a tree of voice lines in, a tree of tracks out.

For every ``.wav`` under ``--dir``, look for a same-stem ``.TextGrid`` (MFA —
accurate path, preferred) or ``.txt`` transcript (naive path), generate a
track, and write it to the mirrored path under ``--out``. A manifest makes
``--modified-only`` re-runs incremental; a summary (printed table + JSON)
reports per-file status, counts, OOV words that fell through to the G2P rule
fallback, and worst-first aligner confidence when the aligner supplies it.
Per-file failures do not stop the batch; the exit code reports them at the
end.
"""

from __future__ import annotations

import json
import os
from multiprocessing import Pool
from typing import List, Optional, Tuple

from .g2p import G2P
from .alignment import load_mfa_textgrid
from .io_export import to_dict, write_csv, write_json
from .mapping import Mapping
from .pipeline import generate_from_alignment, generate_naive, wav_duration

MANIFEST_NAME = ".openfacefx-manifest.json"
SUMMARY_NAME = "batch_summary.json"


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
            out = os.path.join(out_dir, os.path.splitext(rel)[0] + "." + ext)
            jobs.append(dict(
                rel=rel, wav=wav,
                textgrid=tg if os.path.exists(tg) else None,
                txt=txt if os.path.exists(txt) else None,
                out=out,
            ))
    return jobs


def _process_one(args: Tuple[dict, Optional[str], Optional[str], float]) -> dict:
    """Worker (top-level for Windows spawn): returns a summary row."""
    job, mapping_path, cmudict_path, fps = args
    row = dict(file=job["rel"], status="ok", out=os.path.relpath(job["out"]),
               error=None, duration=None, channels=0, keyframes=0,
               oov=[], min_confidence=None, mode=None)
    try:
        mapping = Mapping.from_json(mapping_path) if mapping_path else None
        if job["textgrid"]:
            row["mode"] = "mfa"
            segs = load_mfa_textgrid(job["textgrid"])
            confs = [s.confidence for s in segs if s.confidence is not None]
            if confs:
                row["min_confidence"] = min(confs)
            track = generate_from_alignment(segs, fps=fps, mapping=mapping)
        elif job["txt"]:
            row["mode"] = "naive"
            with open(job["txt"], encoding="utf-8") as fh:
                text = fh.read().strip()
            if not text:
                raise ValueError("transcript file is empty")
            g2p = G2P()
            if cmudict_path:
                g2p.load_cmudict(cmudict_path)
            row["oov"] = g2p.oov_words(text)
            dur = wav_duration(job["wav"])
            track = generate_naive(text, dur, fps=fps, g2p=g2p,
                                   mapping=mapping)
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
    except Exception as e:  # keep the batch going; report at the end
        row.update(status="failed", error=f"{type(e).__name__}: {e}")
    return row


def run_batch(in_dir: str, out_dir: str, recurse: bool = False,
              modified_only: bool = False, jobs: int = 1,
              mapping: Optional[str] = None, cmudict: Optional[str] = None,
              fps: float = 60.0, ext: str = "json") -> int:
    """Returns a process exit code (0 = all ok, 1 = at least one failure)."""
    work = find_jobs(in_dir, out_dir, recurse, ext)
    if not work:
        print(f"no .wav files found under {in_dir}")
        return 1

    manifest_path = os.path.join(out_dir, MANIFEST_NAME)
    manifest = {}
    if os.path.exists(manifest_path):
        with open(manifest_path, encoding="utf-8") as fh:
            manifest = json.load(fh)

    def fingerprint(job):
        return {
            "wav": _stamp(job["wav"]),
            "transcript": _stamp(job["textgrid"] or job["txt"] or ""),
            "mapping": _stamp(mapping) if mapping else None,
            "out": job["out"],
        }

    todo, skipped = [], 0
    for job in work:
        if (modified_only and manifest.get(job["rel"]) == fingerprint(job)
                and os.path.exists(job["out"])):
            skipped += 1
            continue
        todo.append(job)

    args = [(job, mapping, cmudict, fps) for job in todo]
    if jobs > 1 and len(todo) > 1:
        with Pool(processes=jobs) as pool:
            rows = pool.map(_process_one, args)
    else:
        rows = [_process_one(a) for a in args]

    for job, row in zip(todo, rows):
        if row["status"] == "ok":
            manifest[job["rel"]] = fingerprint(job)
        else:
            manifest.pop(job["rel"], None)
    os.makedirs(out_dir, exist_ok=True)
    with open(manifest_path, "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, indent=2)

    # worst files first: failures, then lowest confidence, then most OOV
    rows.sort(key=lambda r: (r["status"] == "ok",
                             r["min_confidence"] if r["min_confidence"]
                             is not None else 2.0,
                             -len(r["oov"])))
    summary = dict(processed=len(rows), skipped_unchanged=skipped,
                   failed=sum(1 for r in rows if r["status"] != "ok"),
                   rows=rows)
    with open(os.path.join(out_dir, SUMMARY_NAME), "w", encoding="utf-8") as fh:
        json.dump(summary, fh, indent=2)

    width = max([len(r["file"]) for r in rows] + [4])
    print(f"{'file':<{width}}  {'status':<7} {'mode':<5} {'dur':>6} "
          f"{'keys':>5}  oov")
    for r in rows:
        dur = f"{r['duration']:.2f}" if r["duration"] is not None else "-"
        oov = ",".join(r["oov"][:4]) + ("…" if len(r["oov"]) > 4 else "")
        print(f"{r['file']:<{width}}  {r['status']:<7} {r['mode'] or '-':<5} "
              f"{dur:>6} {r['keyframes']:>5}  {oov}")
        if r["error"]:
            print(f"{'':<{width}}  ! {r['error']}")
    print(f"\n{len(rows)} processed, {skipped} skipped (unchanged), "
          f"{summary['failed']} failed -> {os.path.join(out_dir, SUMMARY_NAME)}")
    return 1 if summary["failed"] else 0
