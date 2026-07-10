"""Edit preservation: hand-tweaks that survive regeneration (issue #9).

The pipeline is a pure function ``(audio, text) -> FaceTrack``; re-running it with
new dials (intensity, coarticulation, energy gain) throws away any manual curve
edits an animator made. This module mirrors FaceFX's two-layer ownership model so
it doesn't have to: analysis *owns* the generated curves, while a user keeps a
small, separate record of what they changed. FaceFX offers two ways to edit an
"Owned by Analysis" curve -- add an OFFSET curve (keys added to the analysis value,
then clamped to the node's min/max), or take full manual ownership (regeneration
skips the curve). We store that as a **sidecar** ``*.edits.json`` rather than
inline flags, so the ``.track`` stays clean, versioned interchange and the solver
stays untouched.

Regeneration is then: re-run the pipeline to a fresh :class:`FaceTrack`, then
:func:`apply_edits` overlays the sidecar back on. The inverse, :func:`diff_edits`,
captures what a user changed (a hand-edited track vs the baseline it came from)
into a sidecar.

Two per-channel edit modes, each optionally restricted to a time ``span`` (the
"locked region"):

  * ``offset`` -- FaceFX offset curve: ``keys`` are deltas added to whatever the
    solver now produces, then clamped. Being *relative*, an offset survives dial
    changes (intensity/coarticulation/energy), which is the primary use case.
  * ``replace`` -- FaceFX manual ownership: ``keys`` are absolute values that
    replace the generated channel (whole-channel, or just inside ``span``).

Conflict handling is conservative: an edit on a channel the regeneration dropped
is **preserved and flagged** (``keep-edit``, the default -- a hand-edit is never
silently lost) or discarded (``take-generated``); a locked region always wins
over the regenerated content inside its span while the fresh curve shows through
everywhere else.

Deterministic and dependency-light: only ``numpy`` (``interp``/``clip`` and the
existing :func:`openfacefx.curves._rdp` thinner), ``hashlib`` and ``json``. No RNG,
stable 4-dp rounding, identical output across Python 3.9/3.13. **Additive**: a
track generated without a sidecar is byte-identical to previous releases -- the
whole layer is opt-in.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import numpy as np

from .curves import Channel, FaceTrack, Keyframe, _rdp
from .visemes import VISEMES

FORMAT = "openfacefx.edits"
VERSION = 1
_MODES = ("offset", "replace")
_ON_CONFLICT = ("keep-edit", "take-generated")


# --------------------------------------------------------------------------- #
# Sampling: FaceTrack channels are piecewise-linear, so np.interp is exact.    #
# --------------------------------------------------------------------------- #

def _key_arrays(keys) -> Tuple[np.ndarray, np.ndarray]:
    """``(times, values)`` float arrays from a :class:`Channel`, a list of
    :class:`Keyframe`, or a list of ``[time, value]`` pairs."""
    seq = keys.keys if isinstance(keys, Channel) else keys
    kt: List[float] = []
    kv: List[float] = []
    for k in seq:
        if isinstance(k, Keyframe):
            kt.append(float(k.time)); kv.append(float(k.value))
        else:
            kt.append(float(k[0])); kv.append(float(k[1]))
    return np.asarray(kt, dtype=float), np.asarray(kv, dtype=float)


def sample(keys, T) -> np.ndarray:
    """Values of a keyframe list at times ``T`` by linear interpolation.

    Uses ``np.interp``'s endpoint-hold (flat before the first key / after the
    last), matching how a curve editor reads a piecewise-linear channel. An empty
    key list samples to zeros. ``keys`` may be a :class:`Channel`, a list of
    :class:`Keyframe`, or ``[time, value]`` pairs (offset/replace records)."""
    Ta = np.asarray(T, dtype=float)
    kt, kv = _key_arrays(keys)
    if kt.size == 0:
        return np.zeros(Ta.shape, dtype=float)
    return np.interp(Ta, kt, kv)


# --------------------------------------------------------------------------- #
# Stable content ids (sidecar <-> source / baseline provenance).              #
# --------------------------------------------------------------------------- #

def _sha1_track(track: FaceTrack) -> str:
    """``sha1:...`` of a track's canonical JSON -- the baseline a sidecar was
    diffed against, so a future reader can tell it is being applied to a regen of
    the same source rather than an unrelated track."""
    from .io_export import to_dict
    blob = json.dumps(to_dict(track), sort_keys=True,
                      separators=(",", ":")).encode("utf-8")
    return "sha1:" + hashlib.sha1(blob).hexdigest()


def _sha1_source(wav_path: str) -> str:
    """``sha1:...`` of a WAV's bytes -- a stable ``source_id`` keying a sidecar to
    the audio it was authored against."""
    with open(wav_path, "rb") as fh:
        return "sha1:" + hashlib.sha1(fh.read()).hexdigest()


# --------------------------------------------------------------------------- #
# Sidecar document + validation                                               #
# --------------------------------------------------------------------------- #

@dataclass
class EditsDoc:
    """A parsed ``openfacefx.edits`` sidecar.

    ``channels`` maps a channel name to an edit *record* -- a validated dict
    ``{"mode": "offset"|"replace", "keys": [[t, v], ...], "clamp"?: [lo, hi],
    "span"?: [t0, t1]}``. ``base_hash`` / ``source_id`` are provenance (the
    baseline the edits were captured from, and the audio id); ``fps`` /
    ``viseme_set`` echo the track for reference."""
    channels: Dict[str, dict] = field(default_factory=dict)
    fps: float = 60.0
    source_id: Optional[str] = None
    base_hash: Optional[str] = None
    viseme_set: Optional[List[str]] = None

    def to_dict(self) -> Dict:
        d: Dict = {"format": FORMAT, "version": VERSION}
        if self.source_id is not None:
            d["source_id"] = self.source_id
        if self.base_hash is not None:
            d["base_hash"] = self.base_hash
        d["fps"] = self.fps
        if self.viseme_set is not None:
            d["viseme_set"] = list(self.viseme_set)
        d["channels"] = self.channels
        return d

    @classmethod
    def from_dict(cls, d: Dict) -> "EditsDoc":
        """Parse and validate a sidecar dict, raising ``ValueError`` with a clear,
        channel-named message on any malformed field."""
        if d.get("format") != FORMAT or d.get("version") != VERSION:
            raise ValueError(
                f"expected format {FORMAT!r} version {VERSION}, got "
                f"{d.get('format')!r} version {d.get('version')!r}")
        fps = d.get("fps", 60.0)
        if not _finite(fps) or fps <= 0.0:
            raise ValueError(f"edits 'fps' must be a positive number, got {fps!r}")
        return cls(
            channels=_validate_channels(d.get("channels")),
            fps=float(fps),
            source_id=d.get("source_id"),
            base_hash=d.get("base_hash"),
            viseme_set=d.get("viseme_set"),
        )


def _finite(x) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)


def _validate_keys(name: str, keys, field_name: str = "keys") -> List[List[float]]:
    if not isinstance(keys, list) or not keys:
        raise ValueError(
            f"channel {name!r}: {field_name!r} must be a non-empty list of "
            f"[time, value] pairs")
    out: List[List[float]] = []
    last: Optional[float] = None
    for i, pair in enumerate(keys):
        if not isinstance(pair, (list, tuple)) or len(pair) != 2:
            raise ValueError(
                f"channel {name!r}: {field_name}[{i}] must be a [time, value] "
                f"pair, got {pair!r}")
        t, v = pair
        if not _finite(t) or not _finite(v):
            raise ValueError(
                f"channel {name!r}: {field_name}[{i}] has a non-finite entry {pair!r}")
        t = float(t)
        if last is not None and t < last:
            raise ValueError(
                f"channel {name!r}: {field_name} times must be ascending "
                f"({field_name}[{i}] time {t} < {last})")
        last = t
        out.append([t, float(v)])
    return out


def _validate_clamp(name: str, c) -> List[float]:
    if (not isinstance(c, (list, tuple)) or len(c) != 2
            or not _finite(c[0]) or not _finite(c[1])):
        raise ValueError(f"channel {name!r}: 'clamp' must be [lo, hi] numbers")
    lo, hi = float(c[0]), float(c[1])
    if not (0.0 <= lo <= hi <= 1.0):
        raise ValueError(
            f"channel {name!r}: clamp must satisfy 0 <= lo <= hi <= 1, "
            f"got [{lo}, {hi}]")
    return [lo, hi]


def _validate_span(name: str, s) -> List[float]:
    if (not isinstance(s, (list, tuple)) or len(s) != 2
            or not _finite(s[0]) or not _finite(s[1])):
        raise ValueError(f"channel {name!r}: 'span' must be [t0, t1] numbers")
    t0, t1 = float(s[0]), float(s[1])
    if t1 < t0:
        raise ValueError(f"channel {name!r}: span end {t1} precedes start {t0}")
    return [t0, t1]


def _validate_channels(raw) -> Dict[str, dict]:
    if not isinstance(raw, dict):
        raise ValueError("edits 'channels' must be a JSON object of name -> record")
    out: Dict[str, dict] = {}
    for name, rec in raw.items():
        if not isinstance(rec, dict):
            raise ValueError(f"channel {name!r}: record must be an object")
        mode = rec.get("mode")
        if mode not in _MODES:
            raise ValueError(
                f"channel {name!r}: 'mode' must be one of {_MODES}, got {mode!r}")
        norm = {"mode": mode, "keys": _validate_keys(name, rec.get("keys"))}
        if rec.get("clamp") is not None:
            norm["clamp"] = _validate_clamp(name, rec["clamp"])
        if rec.get("span") is not None:
            norm["span"] = _validate_span(name, rec["span"])
        out[name] = norm
    return out


def save_edits(doc: EditsDoc, path: str) -> None:
    """Write a sidecar as pretty JSON (2-space indent, trailing newline)."""
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(doc.to_dict(), fh, indent=2)
        fh.write("\n")


def load_edits(path: str) -> EditsDoc:
    """Read and validate a sidecar from ``path``."""
    with open(path, encoding="utf-8") as fh:
        try:
            d = json.load(fh)
        except json.JSONDecodeError as e:
            raise ValueError(f"{path}: not valid JSON ({e})") from None
    return EditsDoc.from_dict(d)


# --------------------------------------------------------------------------- #
# diff: capture a hand-edited track vs its baseline into a sidecar             #
# --------------------------------------------------------------------------- #

def diff_edits(base: FaceTrack, edited: FaceTrack, mode: str = "offset",
               span: Optional[Tuple[float, float]] = None, tol: float = 0.01,
               eps: float = 0.01, clamps: Optional[Dict[str, Tuple[float, float]]] = None,
               source_id: Optional[str] = None) -> EditsDoc:
    """Build a sidecar from a hand-``edited`` track and the ``base`` it was edited
    from. A channel whose curves differ by more than ``tol`` anywhere is recorded;
    identical channels are skipped (so an untouched track yields an empty sidecar).

    ``mode='offset'`` (default) stores the delta ``edited - base`` (RDP-thinned at
    ``eps``) plus the per-channel ``clamp`` -- the relative form that survives
    later dial changes. ``mode='replace'`` stores the absolute edited values.
    ``span=(t0, t1)`` restricts capture to a time window (a locked region).
    ``clamps`` maps channel -> (lo, hi) (e.g. from ``mapping.Target``); missing
    entries default to ``[0, 1]``."""
    if mode not in _MODES:
        raise ValueError(f"diff mode must be one of {_MODES}, got {mode!r}")
    bmap = {c.name: c for c in base.channels}
    umap = {c.name: c for c in edited.channels}
    channels: Dict[str, dict] = {}
    for name in sorted(set(bmap) | set(umap)):
        B, U = bmap.get(name), umap.get(name)
        clamp = list(clamps[name]) if (clamps and name in clamps) else [0.0, 1.0]
        if U is not None and B is None:                       # user added a channel
            channels[name] = {"mode": "replace",
                              "keys": [[round(k.time, 4), round(k.value, 4)]
                                       for k in U.keys]}
            continue
        if B is not None and U is None:                       # user silenced it
            channels[name] = {"mode": "replace",
                              "keys": [[round(B.keys[0].time, 4), 0.0],
                                       [round(B.keys[-1].time, 4), 0.0]]}
            continue
        times = sorted({k.time for k in B.keys} | {k.time for k in U.keys})
        if span is not None:
            times = [t for t in times if span[0] <= t <= span[1]]
        if not times:
            continue
        Ta = np.asarray(times, dtype=float)
        delta = sample(U, Ta) - sample(B, Ta)
        if float(np.max(np.abs(delta))) <= tol:               # untouched channel
            continue
        channels[name] = _diff_record(mode, U, times, Ta, delta, clamp, span, eps)
    return EditsDoc(
        channels=channels, fps=float(base.fps), source_id=source_id,
        base_hash=_sha1_track(base),
        viseme_set=list(base.target_set) if base.target_set is not None else None)


def _diff_record(mode, U, times, Ta, delta, clamp, span, eps) -> dict:
    if mode == "replace":
        if span is None:
            keys = [[round(k.time, 4), round(k.value, 4)] for k in U.keys]
        else:
            vals = sample(U, Ta)
            keys = [[round(float(t), 4), round(float(v), 4)]
                    for t, v in zip(times, vals)]
        rec = {"mode": "replace", "keys": keys}
    else:
        idx = _rdp(Ta, delta, eps)
        rec = {"mode": "offset",
               "keys": [[round(float(times[i]), 4), round(float(delta[i]), 4)]
                        for i in idx],
               "clamp": clamp}
    if span is not None:
        rec["span"] = [float(span[0]), float(span[1])]
    return rec


# --------------------------------------------------------------------------- #
# apply: overlay a sidecar onto a freshly regenerated track                    #
# --------------------------------------------------------------------------- #

def apply_edits(gen: FaceTrack, edits: EditsDoc, eps: float = 0.015,
                on_conflict: str = "keep-edit") -> Tuple[FaceTrack, List[dict]]:
    """Overlay ``edits`` onto a freshly generated ``gen`` track and return
    ``(merged_track, conflicts)``.

    A generated channel with no record passes through untouched. An ``offset``
    record adds its (interpolated) deltas to the current curve and clamps; a
    ``replace`` record substitutes its values. A ``span`` confines the edit to a
    window -- the fresh curve is preserved exactly outside it, so a locked region
    wins only where it was drawn.

    ``conflicts`` lists edits whose channel is absent from ``gen`` (renamed,
    or its word removed on re-alignment). ``on_conflict='keep-edit'`` (default)
    re-injects them so a hand-edit is never silently lost;
    ``'take-generated'`` drops them for the fresh output. Either way the conflict
    is reported so the caller can warn."""
    if on_conflict not in _ON_CONFLICT:
        raise ValueError(
            f"on_conflict must be one of {_ON_CONFLICT}, got {on_conflict!r}")
    gen_names = {c.name for c in gen.channels}
    out: List[Channel] = []
    conflicts: List[dict] = []
    for c in gen.channels:
        rec = edits.channels.get(c.name)
        out.append(_apply_record(c, rec, eps) if rec is not None else c)
    target_set = list(gen.target_set) if gen.target_set is not None else None
    for name in sorted(edits.channels):                       # edits on dropped channels
        if name in gen_names:
            continue
        action = ("dropped (take-generated)" if on_conflict == "take-generated"
                  else "preserved (keep-edit)")
        conflicts.append({
            "channel": name, "reason": "absent-from-regen",
            "detail": f"channel not in the regenerated track; edit {action} "
                      f"-- verify it was not renamed or its word removed"})
        if on_conflict == "take-generated":
            continue
        ch = _apply_record(Channel(name, []), edits.channels[name], eps)
        if ch.keys:
            out.append(ch)
            if target_set is None:            # None == the built-in viseme vocab;
                target_set = list(VISEMES)    # make it explicit before extending
            if name not in target_set:
                target_set.append(name)
    merged = FaceTrack(gen.fps, out, target_set)
    # Curve edits leave the fresh generation's event/take layer (issue #6) intact,
    # so apply order relative to the event layer does not matter.
    merged.events = list(getattr(gen, "events", None) or [])
    merged.variants = getattr(gen, "variants", None)
    return merged, conflicts


def _apply_record(base_ch: Channel, rec: dict, eps: float) -> Channel:
    mode = rec["mode"]
    span = rec.get("span")
    if mode == "replace":
        if span is None:
            return Channel(base_ch.name,
                           [Keyframe(round(float(t), 4), round(float(v), 4))
                            for t, v in rec["keys"]])
        return _splice(base_ch, float(span[0]), float(span[1]),
                       [(float(t), float(v)) for t, v in rec["keys"]])
    return _apply_offset(base_ch, rec, eps)


def _apply_offset(base_ch: Channel, rec: dict, eps: float) -> Channel:
    clamp = rec.get("clamp", [0.0, 1.0])
    okeys = rec["keys"]
    span = rec.get("span")
    if span is not None:
        t0, t1 = float(span[0]), float(span[1])
        inner_t = sorted({float(t) for t, _ in okeys if t0 <= t <= t1}
                         | {k.time for k in base_ch.keys if t0 <= k.time <= t1})
        Ta = np.asarray(inner_t or [t0, t1], dtype=float)
        vals = np.clip(sample(base_ch, Ta) + sample(okeys, Ta), clamp[0], clamp[1])
        return _splice(base_ch, t0, t1, list(zip(Ta.tolist(), vals.tolist())))
    times = sorted({k.time for k in base_ch.keys} | {float(t) for t, _ in okeys})
    Ta = np.asarray(times, dtype=float)
    v = np.clip(sample(base_ch, Ta) + sample(okeys, Ta), clamp[0], clamp[1])
    idx = _rdp(Ta, v, eps)
    return Channel(base_ch.name,
                   [Keyframe(round(float(Ta[i]), 4), round(float(v[i]), 4))
                    for i in idx])


def _splice(base_ch: Channel, t0: float, t1: float,
            inner: List[Tuple[float, float]]) -> Channel:
    """Substitute the ``inner`` (time, value) points across ``[t0, t1]`` while
    keeping the generated keys outside verbatim. The generated keyframes before
    ``t0`` and after ``t1`` are preserved bit-for-bit (the fresh curve shows
    through everywhere outside the lock), and the user's keys own the closed span
    ``[t0, t1]`` (edges included); the two segments that bridge the boundary are a
    straight connector between the last outside key and the first inside key."""
    before = [Keyframe(k.time, k.value) for k in base_ch.keys if k.time < t0]
    after = [Keyframe(k.time, k.value) for k in base_ch.keys if k.time > t1]
    mid = [Keyframe(round(float(t), 4), round(float(v), 4))
           for t, v in inner if t0 <= t <= t1]
    if not mid:                       # nothing lands in the span: leave gen as-is
        return Channel(base_ch.name, [Keyframe(k.time, k.value) for k in base_ch.keys])
    return Channel(base_ch.name, before + mid + after)
