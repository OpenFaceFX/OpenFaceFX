"""Export a FaceTrack as a Live2D Cubism ``motion3.json``.

Cubism runtimes play lip-sync as *parameter curves* baked into a motion3.json;
a model's ``model3.json`` declares a ``Groups: LipSync`` list so those curves
retarget onto whatever parameter Ids a given rig exposes
(https://docs.live2d.com/en/cubism-sdk-manual/lipsync/). The editor's own
audio pipeline is volume-only, so phoneme-accurate curves are a quality upgrade.

Two targeting modes:

  * **Default (zero config)** -- collapse the whole viseme track to a single
    ``ParamMouthOpenY`` curve: the summed weight of every *non-silence* viseme,
    clamped to ``0..1``. That is an openness/loudness proxy (it equals ``1 -
    sil`` on normalised coarticulation output) and matches the one mouth-open
    parameter almost every Cubism model exposes. The target Id is configurable
    (``ParamMouthOpenY`` is the Cubism default, not a hard requirement).
  * **Per-parameter** -- pass a ``viseme -> ParamId`` map (e.g. the ParamA/I/U/
    E/O convention some VTuber rigs use, which is *not* a standard) and get one
    curve per distinct parameter.

Both modes are just a :func:`retarget` -- summed, clamped contributions on the
union of key times -- so the writer only ever serialises a track whose channel
names are already Cubism parameter Ids.

Curves use **linear segments only** (segment id ``0``): a leading ``(t, v)``
point, then one ``(t, v)`` point per following keyframe. The ``Meta`` counts
MUST equal what the ``Curves`` array actually contains -- Cubism loaders trust
them and walk past the data otherwise -- so they are *derived from the emitted
segments*, never guessed.
"""

from __future__ import annotations

import json
from typing import Dict, List, Optional

from .curves import FaceTrack
from .retarget import retarget, _sampler
from .visemes import VISEMES

# Segment ids in the flat Cubism segment stream. Each *point* is two numbers
# (time, value); a segment is an id followed by its point(s). We only emit
# LINEAR, but the counter below understands BEZIER's 3-point stride too so the
# Meta stays correct if that is ever added.
_LINEAR = 0
_BEZIER = 1

DEFAULT_MOUTH_PARAM = "ParamMouthOpenY"
#: Cubism editor default fade for an expression (seconds).
DEFAULT_EXPRESSION_FADE = 1.0


def _num(x: float) -> str:
    """A JSON number that keeps a decimal point for floats (matching the
    Cubism editor, which writes ``3.0``/``30.0``) and never uses exponents."""
    return json.dumps(round(float(x), 4))


def _segments(keys) -> List:
    """Flat Cubism segment array for one channel: a leading ``(t, v)`` point
    then a linear segment (id 0, one point) per following key."""
    seg: List = [round(keys[0].time, 4), round(keys[0].value, 4)]
    for k in keys[1:]:
        seg += [_LINEAR, round(k.time, 4), round(k.value, 4)]
    return seg


def _count(seg: List) -> tuple:
    """``(segment_count, point_count)`` for a flat segment array, by the same
    stride a Cubism loader uses: a leading point, then each segment is one id
    plus its points (bezier = 3 points, everything else = 1)."""
    points = 1  # the leading point
    segments = 0
    i = 2
    n = len(seg)
    while i < n:
        pts = 3 if seg[i] == _BEZIER else 1
        segments += 1
        points += pts
        i += 1 + 2 * pts
    return segments, points


def _collapse_mapping(params: Optional[Dict[str, str]], mouth_param: str):
    """Build the retarget mapping for the chosen mode: one shared mouth-open
    target for every non-silence viseme, or the caller's ``viseme -> ParamId``."""
    if params is None:
        return {v: [(mouth_param, 1.0)] for v in VISEMES if v != "sil"}
    return {v: [(pid, 1.0)] for v, pid in params.items()}


def lipsync_param_ids(model3_path: str) -> List[str]:
    """The parameter Ids of a ``model3.json``'s ``Groups: LipSync`` entry.

    Cubism models list the parameters driven by lip-sync here, so pointing the
    exporter at a model auto-discovers its mouth parameter(s). Returns ``[]``
    when the file has no LipSync group.
    """
    with open(model3_path, encoding="utf-8") as fh:
        data = json.load(fh)
    for group in data.get("Groups", []):
        if group.get("Name") == "LipSync":
            return list(group.get("Ids", []))
    return []


def write_live2d_motion(
    track: FaceTrack,
    path: str,
    *,
    params: Optional[Dict[str, str]] = None,
    mouth_param: str = DEFAULT_MOUTH_PARAM,
    fps: Optional[float] = None,
    loop: bool = False,
) -> None:
    """Write ``track`` as a Cubism ``motion3.json`` (Version 3).

    ``params`` (``viseme -> ParamId``) selects per-parameter mode; the default
    ``None`` collapses to a single ``mouth_param`` curve. ``fps`` overrides the
    track's own rate in the ``Meta`` block (Cubism plays the curves by time
    regardless). Output is pure-stdlib JSON with LF line endings; the ``Meta``
    counts are computed from the emitted ``Curves`` so they cannot drift.
    """
    mapping = _collapse_mapping(params, mouth_param)
    baked = retarget(track, mapping)

    curves = []
    total_segments = total_points = 0
    duration = 0.0
    for ch in baked.channels:
        if not ch.keys:
            continue
        seg = _segments(ch.keys)
        n_seg, n_pt = _count(seg)
        total_segments += n_seg
        total_points += n_pt
        duration = max(duration, ch.keys[-1].time)
        curves.append((ch.name, seg))

    rate = track.fps if fps is None else fps
    lines = [
        "{",
        '  "Version": 3,',
        '  "Meta": {',
        f'    "Duration": {_num(duration)},',
        f'    "Fps": {_num(rate)},',
        f'    "Loop": {"true" if loop else "false"},',
        '    "AreBeziersRestricted": true,',
        f'    "CurveCount": {len(curves)},',
        f'    "TotalSegmentCount": {total_segments},',
        f'    "TotalPointCount": {total_points},',
        '    "UserDataCount": 0,',
        '    "TotalUserDataSize": 0',
        "  },",
        '  "Curves": [',
    ]
    last = len(curves) - 1
    for i, (name, seg) in enumerate(curves):
        comma = "" if i == last else ","
        lines += [
            "    {",
            '      "Target": "Parameter",',
            f'      "Id": {json.dumps(name)},',
            f'      "Segments": {json.dumps(seg)}',
            "    }" + comma,
        ]
    lines += ["  ]", "}"]
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("\n".join(lines) + "\n")


def _pose_time(baked: FaceTrack, at: Optional[float]) -> float:
    """The instant to freeze the pose at: an explicit ``at``, else the peak-
    activity frame (the keyframe time where the summed parameter value is highest —
    the most expressive moment, a sensible default for a snapshot)."""
    if at is not None:
        return float(at)
    times = sorted({k.time for ch in baked.channels for k in ch.keys})
    if not times:
        return 0.0
    samplers = [_sampler(ch) for ch in baked.channels]
    best_t, best = times[0], float("-inf")
    for t in times:
        s = sum(sm(t) for sm in samplers)
        if s > best:
            best, best_t = s, t
    return best_t


def build_expression(track: FaceTrack, *, at: Optional[float] = None,
                     params: Optional[Dict[str, str]] = None,
                     mouth_param: str = DEFAULT_MOUTH_PARAM,
                     fade_in: float = DEFAULT_EXPRESSION_FADE,
                     fade_out: float = DEFAULT_EXPRESSION_FADE) -> Dict:
    """Build a Cubism ``exp3.json`` expression dict: the track's pose at **one
    instant** as absolute (``Blend`` = ``Overwrite``) parameter values.

    Same viseme→ParamId targeting as :func:`write_live2d_motion` (``params`` for
    per-parameter mode, else a single ``mouth_param``); ``at`` picks the instant
    (default: the peak-activity frame). ``Overwrite`` is used because a frozen pose
    is an absolute value, not a delta from the parameter's default (``Add`` would
    store the wrong thing)."""
    mapping = _collapse_mapping(params, mouth_param)
    baked = retarget(track, mapping)
    t = _pose_time(baked, at)
    parameters = [
        {"Id": ch.name, "Value": round(float(_sampler(ch)(t)), 4),
         "Blend": "Overwrite"}
        for ch in baked.channels if ch.keys
    ]
    return {
        "Type": "Live2D Expression",
        "FadeInTime": round(float(fade_in), 4),
        "FadeOutTime": round(float(fade_out), 4),
        "Parameters": parameters,
    }


def write_live2d_expression(track: FaceTrack, path: str, *,
                            at: Optional[float] = None,
                            params: Optional[Dict[str, str]] = None,
                            mouth_param: str = DEFAULT_MOUTH_PARAM,
                            fade_in: float = DEFAULT_EXPRESSION_FADE,
                            fade_out: float = DEFAULT_EXPRESSION_FADE) -> None:
    """Write the track's pose at one instant as a Cubism ``exp3.json`` expression —
    the static, hotkey-bindable companion to :func:`write_live2d_motion` (VTube
    Studio binds these to hotkeys). See :func:`build_expression`. Pure-stdlib JSON,
    LF endings; the schema forbids extra keys, so only ``Type``/``FadeInTime``/
    ``FadeOutTime``/``Parameters`` are emitted."""
    doc = build_expression(track, at=at, params=params, mouth_param=mouth_param,
                           fade_in=fade_in, fade_out=fade_out)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(doc, fh, indent=2)
        fh.write("\n")
