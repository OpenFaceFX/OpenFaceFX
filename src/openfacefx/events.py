"""Timed, typed events and deterministic "takes" -- the game-engine notify layer.

A finished :class:`~openfacefx.curves.FaceTrack` says *how the face moves*; this
module says *what happened and when* -- named, timed, typed records with a
freeform JSON payload that a runtime turns into gameplay (play a sound, trigger a
camera shake, fire a Blueprint node). It mirrors FaceFX's payload-only event
model and the engine primitives it maps onto: Unreal's ``AnimNotify`` /
``AnimNotifyState`` and Unity's ``AnimationEvent`` (see
:mod:`openfacefx.export_unity` and :mod:`openfacefx.export_unreal_notifies`).

The layer is **additive and orthogonal**: :class:`FaceTrack` gains two optional
fields (``events`` / ``variants``) that default empty, so the solver,
coarticulation, RDP reduction and every existing exporter are untouched and a
track with no events serialises byte-identically to before.

Variation ("takes") is authored as weighted alternative event-sets per group and
resolved **deterministically by hashing a line id with SHA-256** -- no RNG
object, no wall-clock, no ML. The same ``line_id`` always resolves to the same
take, forever and across Python versions/OSes (SHA-256 is fixed by FIPS 180-4;
the builtin ``hash()`` is *not* used -- it is ``PYTHONHASHSEED``-salted). This
mirrors the determinism discipline of :mod:`openfacefx.gestures`.

stdlib only (``hashlib`` for the seed, ``json`` for payload sizing). No numpy --
the optional auto-derivation of events from speech lives in
:func:`openfacefx.pipeline.derive_events`, which reuses the numpy peak/stress
detection, so this data model stays dependency-light.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

#: Controlled event-type vocabulary. ``custom`` is the escape hatch; any string
#: is accepted (an unknown type is not an error), but staying within this set
#: keeps exporter ``event_func_map`` lookups and downstream tooling predictable.
EVENT_TYPES = frozenset({
    "gesture", "emphasis", "gaze", "blink", "brow", "marker", "sound", "custom",
})

# FaceFX runtime ceilings, honoured as validation *warnings* (never hard errors):
# an animation may hold at most this many events, with a bounded total payload,
# and engines (Unity especially) require ascending event time. See
# https://github.com/FaceFX/FaceFX-UE5/blob/main/Documentation/Events.md
MAX_EVENTS = 4096
MAX_TOTAL_PAYLOAD_BYTES = 65536


@dataclass
class Event:
    """One timed notify. ``t`` is absolute seconds on the same clock as
    :class:`~openfacefx.curves.Keyframe.time`; ``dur == 0`` is instantaneous (an
    ``AnimNotify`` / point ``AnimationEvent``), ``dur > 0`` is ranged (an
    ``AnimNotifyState`` with a begin and an end). ``payload`` is any
    JSON-serialisable dict carried to gameplay. ``blend_in`` / ``blend_out`` are
    optional FaceFX-style hermite blend seconds; ``channel`` an optional target
    hint (bone/curve); ``id`` an optional stable id for replace/de-dup."""
    t: float
    type: str
    name: str
    dur: float = 0.0
    payload: Dict = field(default_factory=dict)
    blend_in: float = 0.0
    blend_out: float = 0.0
    channel: Optional[str] = None
    id: Optional[str] = None


@dataclass
class Alternative:
    """One weighted candidate event-set inside a :class:`VariantGroup`. Weights
    are relative (need not sum to 1); a weight ``<= 0`` is never selected."""
    weight: float = 1.0
    events: List[Event] = field(default_factory=list)


@dataclass
class VariantGroup:
    """A named set of mutually-exclusive :class:`Alternative`\\ s. Exactly one is
    chosen per line. ``group`` names the axis of variation (e.g. ``"headgest"``);
    ``seed_salt`` lets two groups that would otherwise hash identically diverge."""
    group: str
    alternatives: List[Alternative] = field(default_factory=list)
    seed_salt: str = ""


@dataclass
class Variants:
    """Authoring-time variation for a line. Each group resolves independently, so
    (say) a head-gesture choice and a gaze choice vary independently for the same
    ``line_id``. ``line_id=None`` resolves to a constant default (first bucket of
    every group) -- deterministic, but not varied."""
    line_id: Optional[str]
    groups: List[VariantGroup] = field(default_factory=list)


# --------------------------------------------------------------------------- #
# Deterministic selection (pure SHA-256 arithmetic, no RNG state)              #
# --------------------------------------------------------------------------- #

def _unit(line_id: Optional[str], group: str, salt: str = "") -> float:
    """A stable ``u in [0, 1)`` from ``(line_id, group, salt)``.

    The key is the three fields joined by ``\\x1f`` (unit separator, so the parts
    can never collide) and UTF-8 encoded; ``u`` is the top 64 bits of its SHA-256
    digest divided by ``2**64``. Fixed by FIPS 180-4, hence identical across
    Python versions, platforms and processes -- unlike the salted builtin
    ``hash()``. ``line_id=None`` is treated as the empty string."""
    key = f"{line_id or ''}\x1f{group}\x1f{salt}".encode("utf-8")
    n = int.from_bytes(hashlib.sha256(key).digest()[:8], "big")
    return n / 2.0 ** 64


def choose(alternatives: List[Alternative], line_id: Optional[str],
           group: str, salt: str = "") -> Alternative:
    """Deterministically pick one :class:`Alternative` by weighted CDF, indexing
    it with :func:`_unit`. Same ``(line_id, group, salt)`` always yields the same
    pick. Raises ``ValueError`` on an empty list (a group must offer a choice)."""
    if not alternatives:
        raise ValueError("choose() needs at least one alternative")
    weights = [max(a.weight, 0.0) for a in alternatives]
    total = sum(weights) or 1.0
    u = _unit(line_id, group, salt) * total
    acc = 0.0
    for alt, w in zip(alternatives, weights):
        acc += w
        if u < acc:
            return alt
    return alternatives[-1]          # floating-point safety fallback


def resolve(track) -> List[Event]:
    """Concrete, time-sorted events for ``track``: its explicit ``events`` plus,
    for each variant group, the events of the deterministically chosen
    alternative. Duck-typed on ``track.events`` / ``track.variants`` so this
    module never imports :class:`FaceTrack` (keeping the dependency one-way).

    Exporters call this to get a flat, ascending-time list; the sort is stable,
    so equal-``t`` events keep authoring order (explicit events before variant
    events, group order preserved)."""
    events = list(getattr(track, "events", None) or [])
    variants = getattr(track, "variants", None)
    if variants is not None:
        for g in variants.groups:
            if not g.alternatives:
                continue
            events += choose(g.alternatives, variants.line_id,
                             g.group, g.seed_salt).events
    return sorted(events, key=lambda e: e.t)


# --------------------------------------------------------------------------- #
# Authoring helpers                                                            #
# --------------------------------------------------------------------------- #

def add_event(track, t: float, type: str, name: str, **kwargs) -> Event:
    """Append a new :class:`Event` to ``track.events`` and return it. A thin
    convenience over ``track.events.append(Event(...))`` for the explicit-API
    authoring path; ``kwargs`` are the optional :class:`Event` fields
    (``dur``, ``payload``, ``blend_in``/``blend_out``, ``channel``, ``id``)."""
    ev = Event(t=t, type=type, name=name, **kwargs)
    track.events.append(ev)
    return ev


def attach_events(track, events: Optional[List[Event]] = None,
                  variants: Optional[Variants] = None):
    """Attach ``events`` (extending, never replacing) and/or ``variants`` (set)
    onto ``track``, then return it. The one-call way to bolt an authored or
    derived event layer onto a finished track."""
    if events:
        track.events.extend(events)
    if variants is not None:
        track.variants = variants
    return track


# --------------------------------------------------------------------------- #
# Validation (FaceFX runtime ceilings -> warnings, never exceptions)          #
# --------------------------------------------------------------------------- #

def validate_events(events: List[Event]) -> List[str]:
    """Human-readable warnings if ``events`` would strain a FaceFX-style runtime:
    too many events, non-ascending time (Unity requires ascending), or an
    oversized total payload. Returns an empty list when all is well -- callers
    decide whether to print or ignore; nothing here raises."""
    warnings: List[str] = []
    if len(events) > MAX_EVENTS:
        warnings.append(
            f"{len(events)} events exceeds the FaceFX runtime limit of "
            f"{MAX_EVENTS}; some runtimes will drop the overflow")
    times = [e.t for e in events]
    if times != sorted(times):
        warnings.append(
            "events are not in ascending time order; call resolve(track) "
            "(Unity AnimationEvents and FaceFX both require ascending time)")
    total = sum(len(json.dumps(e.payload, separators=(",", ":"),
                               sort_keys=True, ensure_ascii=True).encode("utf-8"))
                for e in events)
    if total > MAX_TOTAL_PAYLOAD_BYTES:
        warnings.append(
            f"total payload {total} bytes exceeds the FaceFX limit of "
            f"{MAX_TOTAL_PAYLOAD_BYTES}")
    return warnings


# --------------------------------------------------------------------------- #
# Serialisation (additive JSON block; readers ignore unknown keys)            #
# --------------------------------------------------------------------------- #

def event_to_dict(e: Event) -> Dict:
    """One event as a JSON-ready dict. The core fields (``t``/``type``/``name``/
    ``dur``/``payload``/``blend_in``/``blend_out``) are always present; the
    optional ``channel``/``id`` only when set, so common events stay compact.
    Times are rounded to 4 dp to match the rest of the track format."""
    d: Dict = {
        "t": round(float(e.t), 4),
        "type": e.type,
        "name": e.name,
        "dur": round(float(e.dur), 4),
        "payload": e.payload,
        "blend_in": round(float(e.blend_in), 4),
        "blend_out": round(float(e.blend_out), 4),
    }
    if e.channel is not None:
        d["channel"] = e.channel
    if e.id is not None:
        d["id"] = e.id
    return d


def event_from_dict(d: Dict) -> Event:
    """Inverse of :func:`event_to_dict`. Unknown keys are ignored (the additive
    forward-compat rule); missing optional keys fall back to :class:`Event`
    defaults."""
    if not isinstance(d, dict):
        raise ValueError(f"event: expected an object, got {type(d).__name__}")
    for req in ("t", "type", "name"):
        if req not in d:
            raise ValueError(f"event: missing required {req!r} (got keys {sorted(d)})")
    return Event(
        t=float(d["t"]),
        type=str(d["type"]),
        name=str(d["name"]),
        dur=float(d.get("dur", 0.0)),
        payload=dict(d.get("payload") or {}),
        blend_in=float(d.get("blend_in", 0.0)),
        blend_out=float(d.get("blend_out", 0.0)),
        channel=d.get("channel"),
        id=d.get("id"),
    )


def variants_to_dict(v: Variants) -> Dict:
    """A :class:`Variants` tree as nested JSON-ready dicts (groups ->
    alternatives -> events)."""
    return {
        "line_id": v.line_id,
        "groups": [
            {
                "group": g.group,
                "seed_salt": g.seed_salt,
                "alternatives": [
                    {"weight": float(a.weight),
                     "events": [event_to_dict(e) for e in a.events]}
                    for a in g.alternatives
                ],
            }
            for g in v.groups
        ],
    }


def variants_from_dict(d: Dict) -> Variants:
    """Inverse of :func:`variants_to_dict`."""
    groups = []
    for i, g in enumerate(d.get("groups", [])):
        if not isinstance(g, dict) or "group" not in g:
            raise ValueError(f"variant group {i}: missing required 'group'")
        groups.append(VariantGroup(
            group=str(g["group"]),
            seed_salt=str(g.get("seed_salt", "")),
            alternatives=[
                Alternative(
                    weight=float(a.get("weight", 1.0)),
                    events=[event_from_dict(e) for e in a.get("events", [])],
                )
                for a in g.get("alternatives", [])
            ],
        ))
    return Variants(line_id=d.get("line_id"), groups=groups)


def read_events(track_dict: Dict) -> Tuple[List[Event], Optional[Variants]]:
    """Load the ``(events, variants)`` layer back from a parsed track dict (the
    reader half of :func:`openfacefx.io_export.to_dict`). Returns ``([], None)``
    for a track that carries no event layer, so callers can round-trip
    unconditionally."""
    events = [event_from_dict(x) for x in track_dict.get("events", [])]
    vblock = track_dict.get("variants")
    variants = variants_from_dict(vblock) if vblock else None
    return events, variants
