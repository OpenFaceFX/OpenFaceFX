"""Read-only track/asset inspection and a CI-friendly contract linter (issue #47).

Two deterministic, read-only views that never open the previewer and never write
a file:

  * :func:`inspect_track` -- *what's in this track?* A schema-stable stats dict
    (duration, fps, channel/keyframe counts, per-channel key count / min / max /
    time-coverage, event & variant counts, the weight/pose/gesture channel split),
    reusing :func:`openfacefx.qa.summarize` and ``cue_flags`` for the shared
    counters. Every documented key is always present -- lists are empty, not
    absent -- so a CI step can assert on it without scraping console text.

  * :func:`validate_asset` -- *is this asset well-formed?* The format contract
    ``io_export`` / ``edits`` / ``events`` already imply but never enforce
    standalone. It parses a ``.track.json``, an ``*.edits.json`` sidecar, or a
    standalone events file (kind auto-detected), checks the contract, and returns
    a **deterministic, sorted, machine-readable problem list** (``severity`` /
    ``code`` / ``where`` / ``detail``) so a lint gate can fail a build with a clean
    diff. ``strict`` promotes warnings (empty channels, zero-length track) to
    errors.

Weight vs pose: viseme / blendshape / emotion channels are ``[0, 1]`` weights;
the signed pose channels (head/eye *angles*) are flagged only when *wildly* out of
range, since they legitimately carry degrees or ``[-1, 1]``. numpy is not needed
here -- pure stdlib (``math``/``json``), no clock, no RNG.
"""

from __future__ import annotations

import json
import math
from typing import Dict, List, Optional, Tuple

from .io_export import from_dict
from .visemes import VISEMES

FORMAT = "openfacefx.inspect"
VERSION = 1

#: Signed pose/angle channels (degrees, or ``[-1, 1]`` when not in degrees) --
#: the head/eye rotation channels from the gesture layer (issue #5). Everything
#: else (visemes, blink/brow weights, emotion channels, rig blendshapes) is a
#: ``[0, 1]`` weight. Distinguishing them is the same weight-vs-angle split the
#: CSV importer hit in issue #45.
_POSE_CHANNELS = frozenset({
    "headPitch", "headYaw", "headRoll", "eyePitch", "eyeYaw",
})
#: A signed pose value beyond this magnitude is "wildly out of range" (a full
#: rotation is 360 deg; the ``[-1, 1]`` mode is trivially inside it), i.e. garbage
#: rather than a legitimate pose.
_POSE_ABS_LIMIT = 360.0
_EPS = 1e-9
_SEVERITY_RANK = {"error": 0, "warning": 1}


# --------------------------------------------------------------------------- #
# inspect -- schema-stable stats                                              #
# --------------------------------------------------------------------------- #

def inspect_track(track, *, segments=None, oov_words=None) -> Dict:
    """A deterministic, schema-stable stats dict for ``track``.

    Reuses :func:`openfacefx.qa.summarize` for the shared counters and cue-flag
    logic, then adds per-channel detail and the weight/pose/gesture split. Every
    key is always present; ``channel_detail`` is sorted by name."""
    from .qa import summarize
    from .gestures import GESTURE_CHANNELS
    qa = summarize(track, segments=segments, oov_words=oov_words)
    duration = qa["duration"]
    detail: List[Dict] = []
    weight = pose = gesture = 0
    for c in sorted(track.channels, key=lambda ch: ch.name):
        vals = [k.value for k in c.keys]
        times = [k.time for k in c.keys]
        is_pose = c.name in _POSE_CHANNELS
        pose += is_pose
        weight += not is_pose
        gesture += c.name in GESTURE_CHANNELS
        start = round(min(times), 4) if times else 0.0
        end = round(max(times), 4) if times else 0.0
        detail.append({
            "name": c.name,
            "kind": "pose" if is_pose else "weight",
            "keys": len(c.keys),
            "min": round(min(vals), 4) if vals else 0.0,
            "max": round(max(vals), 4) if vals else 0.0,
            "start": start,
            "end": end,
            "coverage": round((end - start) / duration, 4) if duration > 0 else 0.0,
        })
    vs = list(track.target_set) if track.target_set is not None else list(VISEMES)
    return {
        "format": FORMAT,
        "version": VERSION,
        "fps": qa["fps"],
        "duration": duration,
        "channels": qa["channels"],
        "keyframes": qa["keyframes"],
        "weight_channels": weight,
        "pose_channels": pose,
        "gesture_channels": gesture,
        "events": qa["events"],
        "variants": _variant_count(track),
        "viseme_set": vs,
        "channel_detail": detail,
        "cue_warnings": qa["cue_warnings"],
        "oov_words": qa["oov_words"],
        "warnings": qa["warnings"],
    }


def _variant_count(track) -> int:
    v = getattr(track, "variants", None)
    return len(v.groups) if v is not None else 0


def render_inspect(doc: Dict) -> str:
    """A compact human table of an :func:`inspect_track` dict."""
    lines = [
        f"duration {doc['duration']}s @ {doc['fps']} fps",
        f"channels {doc['channels']} ({doc['weight_channels']} weight, "
        f"{doc['pose_channels']} pose; {doc['gesture_channels']} gesture) | "
        f"keyframes {doc['keyframes']} | events {doc['events']} | "
        f"variants {doc['variants']}",
        f"{'channel':<16} {'kind':<6} {'keys':>5} {'min':>7} {'max':>7} "
        f"{'start':>7} {'end':>7} {'cover':>6}",
    ]
    for d in doc["channel_detail"]:
        lines.append(f"{d['name']:<16} {d['kind']:<6} {d['keys']:>5} "
                     f"{d['min']:>7} {d['max']:>7} {d['start']:>7} "
                     f"{d['end']:>7} {d['coverage']:>6}")
    if doc["cue_warnings"]:
        lines.append(f"cue warnings: {len(doc['cue_warnings'])}")
    for w in doc["warnings"]:
        lines.append(f"warning: {w}")
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# validate -- format/contract linter                                          #
# --------------------------------------------------------------------------- #

def _p(severity: str, code: str, where: str, detail: str) -> Dict:
    return {"severity": severity, "code": code, "where": where, "detail": detail}


def detect_kind(data) -> str:
    """``track`` / ``edits`` / ``events`` / ``unknown`` from a parsed JSON dict."""
    if not isinstance(data, dict):
        return "unknown"
    fmt = data.get("format")
    if fmt == "openfacefx.track":
        return "track"
    if fmt == "openfacefx.edits":
        return "edits"
    if "events" in data or "variants" in data:
        return "events"
    return "unknown"


def validate_asset(data, *, kind: Optional[str] = None,
                   strict: bool = False) -> Tuple[str, List[Dict]]:
    """Validate a parsed asset dict; returns ``(kind, problems)``.

    ``problems`` is a deterministic, sorted list of ``{severity, code, where,
    detail}``. ``strict`` relabels every ``warning`` as an ``error`` (so the gate
    fails on empty channels / a zero-length track too)."""
    kind = kind or detect_kind(data)
    if kind == "track":
        problems = _validate_track(data)
    elif kind == "edits":
        problems = _validate_edits(data)
    elif kind == "events":
        problems = _validate_events_file(data)
    else:
        problems = [_p("error", "unknown_asset", "file",
                       "not a recognised openfacefx track, edits sidecar, or "
                       "events file")]
    if strict:
        for p in problems:
            if p["severity"] == "warning":
                p["severity"] = "error"
    problems.sort(key=lambda p: (_SEVERITY_RANK.get(p["severity"], 9),
                                 p["code"], p["where"], p["detail"]))
    return kind, problems


def validate_file(path: str, *, strict: bool = False) -> Tuple[str, List[Dict]]:
    """Read ``path`` and :func:`validate_asset` it. JSON / read errors become a
    single problem (so a lint gate still exits nonzero, never crashes)."""
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except OSError as e:
        return "unknown", [_p("error", "unreadable", "file", f"cannot read: {e}")]
    except json.JSONDecodeError as e:
        return "unknown", [_p("error", "not_json", "file", f"not valid JSON: {e}")]
    return validate_asset(data, strict=strict)


def _validate_track(data: Dict) -> List[Dict]:
    try:
        track = from_dict(data)
    except (ValueError, KeyError, TypeError) as e:
        return [_p("error", "parse_error", "file", str(e))]
    problems: List[Dict] = []
    duration = track.duration
    if duration <= 0.0:
        problems.append(_p("warning", "zero_length_track", "file",
                           "track has zero duration (no keyframes)"))
    declared = set(track.target_set) if track.target_set is not None else set(VISEMES)
    for c in track.channels:
        where = f"channel {c.name!r}"
        if c.name not in declared:
            problems.append(_p("error", "channel_not_in_viseme_set", where,
                               "channel name is absent from the declared "
                               "viseme_set/target_set"))
        if not c.keys:
            problems.append(_p("warning", "empty_channel", where,
                               "channel has no keyframes"))
            continue
        _check_channel_keys(c, where, duration, problems)
    _check_events(track.events, problems)
    return problems


def _check_channel_keys(c, where: str, duration: float,
                        problems: List[Dict]) -> None:
    is_pose = c.name in _POSE_CHANNELS
    last: Optional[float] = None
    for i, k in enumerate(c.keys):
        at = f"{where} key {i}"
        if not math.isfinite(k.time) or not math.isfinite(k.value):
            problems.append(_p("error", "non_finite", at,
                               f"non-finite time/value ({k.time}, {k.value})"))
            continue
        if last is not None and k.time < last - _EPS:
            problems.append(_p("error", "times_not_monotonic", at,
                               f"time {k.time} precedes previous {last}"))
        last = k.time
        if k.time < -_EPS or k.time > duration + _EPS:
            problems.append(_p("error", "time_out_of_bounds", at,
                               f"time {k.time} outside [0, {round(duration, 4)}]"))
        if is_pose:
            if abs(k.value) > _POSE_ABS_LIMIT:
                problems.append(_p("error", "pose_wildly_out_of_range", at,
                                   f"pose value {k.value} exceeds "
                                   f"±{_POSE_ABS_LIMIT}"))
        elif k.value < -_EPS or k.value > 1.0 + _EPS:
            problems.append(_p("error", "weight_out_of_range", at,
                               f"weight {k.value} outside [0, 1]"))


def _check_events(events, problems: List[Dict]) -> None:
    from .events import validate_events, EVENT_TYPES
    for i, e in enumerate(events):
        if e.type not in EVENT_TYPES:
            problems.append(_p("error", "unknown_event_type", f"event {i}",
                               f"type {e.type!r} not in {sorted(EVENT_TYPES)}"))
    for w in validate_events(events):
        problems.append(_p("warning", "event_warning", "events", w))


def _validate_edits(data: Dict) -> List[Dict]:
    from .edits import EditsDoc
    try:
        EditsDoc.from_dict(data)
    except (ValueError, KeyError, TypeError) as e:
        return [_p("error", "edits_invalid", "file", str(e))]
    return []


def _validate_events_file(data: Dict) -> List[Dict]:
    from .events import read_events
    try:
        events, variants = read_events(data)
    except (ValueError, KeyError, TypeError) as e:
        return [_p("error", "events_invalid", "file", str(e))]
    problems: List[Dict] = []
    _check_events(events, problems)
    if variants is not None:
        for gi, g in enumerate(variants.groups):
            for e in g.alternatives:
                _check_events(e.events, problems)
    if not events and variants is None:
        problems.append(_p("warning", "empty_events", "file",
                           "no events or variants in the file"))
    return problems


def render_problems(kind: str, problems: List[Dict]) -> str:
    """Human summary of a :func:`validate_asset` result."""
    if not problems:
        return f"OK: {kind} is well-formed (0 problems)"
    errs = sum(1 for p in problems if p["severity"] == "error")
    warns = len(problems) - errs
    lines = [f"{kind}: {errs} error(s), {warns} warning(s)"]
    for p in problems:
        lines.append(f"  {p['severity']:<7} [{p['code']}] {p['where']}: "
                     f"{p['detail']}")
    return "\n".join(lines)
