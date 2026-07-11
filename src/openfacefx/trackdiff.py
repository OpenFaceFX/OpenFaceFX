"""Structured A/B track drift report with a tolerance-gated verdict (issue #50).

OpenFaceFX ships a hard determinism guarantee but no tool to *leverage* it. This
is the golden-file / snapshot gate: *"did this solver-param / coarticulation /
retarget change actually move the curves, and by how much?"* It is distinct from
its neighbours -- :func:`openfacefx.inspect.validate_asset` checks a **single**
file against the format contract, and :func:`openfacefx.edits.diff_edits` **writes
a sidecar for re-application**; ``diff`` always takes **two** tracks and **never
writes**. A raw ``cmp`` is too brittle (4-dp time quantisation, RDP key
placement), so this compares *semantically*: per-channel value deltas on a shared
dense grid (the same ``np.interp`` resampling :func:`openfacefx.edits.sample`
uses), plus structural drift (added/removed channels, duration/fps, events).

The report is a plain, JSON-ready dict with a deterministic, sorted problem list
(``{channel, metric, value}``); ``ok`` is true iff every delta is within
``tolerance`` and there is no structural drift. numpy + stdlib, no solver, no RNG,
no writes -- same inputs, same bytes on Python 3.9/3.13.
"""

from __future__ import annotations

from typing import Dict, List

import numpy as np

from .curves import Channel, FaceTrack
from .edits import sample

FORMAT = "openfacefx.diff"
VERSION = 1
_GRID_MIN = 2


def _p(channel: str, metric: str, value: float) -> Dict:
    return {"channel": channel, "metric": metric, "value": value}


def _coverage(ch: Channel):
    if not ch.keys:
        return 0.0, 0.0, 0.0
    ts = [k.time for k in ch.keys]
    first, last = round(min(ts), 6), round(max(ts), 6)
    return first, last, round(last - first, 6)


def _event_diff(a: FaceTrack, b: FaceTrack) -> Dict:
    """Event add/remove/changed, matched by ``(t, type, name)`` so a payload or
    blend edit at the same cue reads as *changed*, not add+remove."""
    from .events import event_to_dict
    ad = [event_to_dict(e) for e in (getattr(a, "events", None) or [])]
    bd = [event_to_dict(e) for e in (getattr(b, "events", None) or [])]

    def bucket(dicts):
        out: Dict = {}
        for e in dicts:
            out.setdefault((round(float(e["t"]), 4), e["type"], e["name"]), []).append(e)
        return out

    ab, bb = bucket(ad), bucket(bd)
    added = removed = changed = 0
    for key in set(ab) | set(bb):
        al, bl = ab.get(key, []), bb.get(key, [])
        matched = min(len(al), len(bl))
        changed += sum(1 for i in range(matched) if al[i] != bl[i])
        removed += len(al) - matched
        added += len(bl) - matched
    return {"a": len(ad), "b": len(bd), "added": added, "removed": removed,
            "changed": changed}


def diff_tracks(a: FaceTrack, b: FaceTrack, *, tolerance: float = 0.0) -> Dict:
    """A structured drift report between tracks ``a`` and ``b``.

    ``tolerance`` (default ``0.0`` -> exact match) gates the ``ok`` verdict: every
    per-channel value delta (max-abs / RMS / mean-abs, coverage and first/last-key
    drift) and the duration/fps deltas must be ``<= tolerance``, and there must be
    no structural drift (added/removed channels, changed events). The magnitudes
    are symmetric, so ``diff(a, b)`` and ``diff(b, a)`` agree up to sign and the
    added/removed channel lists swap."""
    tol = float(tolerance)
    problems: List[Dict] = []

    dur_a, dur_b = round(a.duration, 4), round(b.duration, 4)
    ddur = round(dur_a - dur_b, 6)
    if abs(ddur) > tol:
        problems.append(_p("", "duration", abs(ddur)))
    dfps = round(float(a.fps) - float(b.fps), 6)
    if abs(dfps) > tol:
        problems.append(_p("", "fps", abs(dfps)))

    amap = {c.name: c for c in a.channels}
    bmap = {c.name: c for c in b.channels}
    added = sorted(set(bmap) - set(amap))       # in B, not A
    removed = sorted(set(amap) - set(bmap))     # in A, not B
    shared = sorted(set(amap) & set(bmap))
    for name in added:
        problems.append(_p(name, "added", 1.0))
    for name in removed:
        problems.append(_p(name, "removed", 1.0))

    end = max(dur_a, dur_b)
    fps = max(float(a.fps), float(b.fps)) or 60.0
    n = max(_GRID_MIN, int(round(end * fps)) + 1) if end > 0 else _GRID_MIN
    grid = np.linspace(0.0, end, n)
    deltas: List[Dict] = []
    for name in shared:
        d = sample(amap[name], grid) - sample(bmap[name], grid)
        max_abs = round(float(np.max(np.abs(d))), 6)
        rms = round(float(np.sqrt(np.mean(d * d))), 6)
        mean_abs = round(float(np.mean(np.abs(d))), 6)
        fa, la, cva = _coverage(amap[name])
        fb, lb, cvb = _coverage(bmap[name])
        cov, first_k, last_k = round(cva - cvb, 6), round(fa - fb, 6), round(la - lb, 6)
        deltas.append({
            "channel": name, "max_abs": max_abs, "rms": rms, "mean_abs": mean_abs,
            "coverage_delta": cov, "first_key_delta": first_k,
            "last_key_delta": last_k,
        })
        for metric, value in (("max_abs", max_abs), ("rms", rms),
                              ("mean_abs", mean_abs), ("coverage", abs(cov)),
                              ("first_key", abs(first_k)), ("last_key", abs(last_k))):
            if value > tol:
                problems.append(_p(name, metric, round(value, 6)))

    events = _event_diff(a, b)
    for kind in ("added", "removed", "changed"):
        if events[kind]:
            problems.append(_p("", "events_" + kind, float(events[kind])))

    problems.sort(key=lambda pr: (pr["channel"], pr["metric"]))
    return {
        "format": FORMAT,
        "version": VERSION,
        "tolerance": tol,
        "ok": not problems,
        "duration": {"a": dur_a, "b": dur_b, "delta": ddur},
        "fps": {"a": a.fps, "b": b.fps, "delta": dfps},
        "channels": {"added": added, "removed": removed, "shared": shared},
        "events": events,
        "deltas": deltas,
        "problems": problems,
    }


def render_diff(report: Dict) -> str:
    """A compact worst-first human summary of a :func:`diff_tracks` report."""
    if report["ok"]:
        return f"OK: tracks match within tolerance {report['tolerance']}"
    lines = [f"DRIFT: {len(report['problems'])} problem(s) over tolerance "
             f"{report['tolerance']}"]
    du, fp = report["duration"], report["fps"]
    if du["delta"]:
        lines.append(f"  duration {du['a']} vs {du['b']} (delta {du['delta']})")
    if fp["delta"]:
        lines.append(f"  fps {fp['a']} vs {fp['b']}")
    ch = report["channels"]
    if ch["added"]:
        lines.append(f"  added channels: {', '.join(ch['added'])}")
    if ch["removed"]:
        lines.append(f"  removed channels: {', '.join(ch['removed'])}")
    worst = sorted(report["deltas"], key=lambda d: -d["max_abs"])
    for d in worst:
        if d["max_abs"] or d["rms"]:
            lines.append(f"  {d['channel']:<16} max_abs {d['max_abs']:<10} "
                         f"rms {d['rms']:<10} mean_abs {d['mean_abs']}")
    ev = report["events"]
    if ev["added"] or ev["removed"] or ev["changed"]:
        lines.append(f"  events: +{ev['added']} -{ev['removed']} "
                     f"~{ev['changed']}")
    return "\n".join(lines)
