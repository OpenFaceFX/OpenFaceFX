"""Deterministic energy-ranked channel-budget reduction (issue #37).

Rigs have fixed morph-target budgets and collapse secondary facial detail at
distance. This ranks a solved track's channels by **total energy** -- the summed
absolute key-to-key value delta (total variation), i.e. how much a channel
actually *moves* -- and keeps the top N, dropping the low-energy secondary
micro-channels (subtle brow / cheek / nostril) entirely. In a speech clip the jaw
and primary lip visemes move the most, so the ranking keeps them naturally; no
protect-set is needed (and one would risk evicting a higher-energy channel to
honour the cap).

The cap applies to the ``[0, 1]`` **morph/weight** channels only -- visemes,
emotion channels, and ``[0, 1]`` gesture weights (blink/brow). The signed
head/eye **pose** channels (``headPitch``/``headYaw``/``headRoll``/``eyePitch``/
``eyeYaw`` -- the exact set :mod:`openfacefx.inspect` classifies) **pass through
unchanged and are not counted toward N**, since they drive bones, not morph
targets, and their degree-scale deltas would otherwise dwarf the ``[0, 1]``
weights purely on units. So ``N`` means "at most N morph channels".

Two modes share this machinery: a standalone hard cap (``transform
--max-channels N`` for a fixed morph-target platform) and a per-LOD budget (``lod
--max-channels N1,N2,..`` -- higher LODs keep fewer channels). Both emit the
per-channel energy ranking as sidecar metadata. Dropped channels are *removed*
(as `reduce_to_track` skips never-firing ones), never zeroed with dead keys.
Absent a cap the track is returned unchanged (byte-identical). numpy is not
needed -- pure stdlib arithmetic, deterministic (ties broken by channel name).
"""

from __future__ import annotations

from typing import Iterable, List, Optional, Tuple

from .curves import Channel, FaceTrack
from .inspect import POSE_CHANNELS


def channel_energy(channel: Channel) -> float:
    """Total energy of a channel: the summed absolute key-to-key value delta
    (total variation). A constant or single-key channel has zero energy."""
    keys = channel.keys
    return float(sum(abs(keys[i + 1].value - keys[i].value)
                     for i in range(len(keys) - 1)))


def rank_channels(track: FaceTrack) -> List[dict]:
    """The **weight** channels ranked by descending energy, ties broken by channel
    name (stable, deterministic). Returns ``[{name, energy, rank}, ...]`` with a
    0-based ``rank`` and ``energy`` rounded to 6 dp. Signed pose channels
    (:data:`POSE_CHANNELS`) are excluded -- their degree-scale energy is not
    comparable to a ``[0, 1]`` weight's, so they are neither ranked nor capped."""
    order = sorted(((channel_energy(c), c.name) for c in track.channels
                    if c.name not in POSE_CHANNELS),
                   key=lambda ec: (-ec[0], ec[1]))
    return [{"name": name, "energy": round(energy, 6), "rank": i}
            for i, (energy, name) in enumerate(order)]


def keep_top_weight(track: FaceTrack, ranking: List[dict],
                    max_channels: int) -> FaceTrack:
    """Keep the top ``max_channels`` weight channels by ``ranking`` **plus every
    pose channel** (which always passes through), dropping the rest. ``ranking``
    may come from another track (e.g. the LOD source) so the kept sets nest."""
    keep = {r["name"] for r in ranking if r["rank"] < max_channels}
    keep |= {c.name for c in track.channels if c.name in POSE_CHANNELS}
    return keep_channels(track, keep)


def keep_channels(track: FaceTrack, names: Iterable[str]) -> FaceTrack:
    """Return ``track`` with only the channels in ``names`` (original order,
    events / variants / target_set carried through). Unchanged (same object) when
    every channel is kept, so a no-op cap stays byte-identical."""
    keep = set(names)
    channels = [c for c in track.channels if c.name in keep]
    if len(channels) == len(track.channels):
        return track
    out = FaceTrack(track.fps, channels,
                    list(track.target_set) if track.target_set is not None else None)
    out.events = list(getattr(track, "events", None) or [])
    out.variants = getattr(track, "variants", None)
    return out


def budget_channels(track: FaceTrack, max_channels: Optional[int]
                    ) -> Tuple[FaceTrack, List[dict]]:
    """Keep the ``max_channels`` highest-energy **weight** channels, dropping the
    rest; signed pose channels always pass through and are not counted.

    Returns ``(track, ranking)`` where each ranking entry (weight channels only)
    carries a ``kept`` flag. ``max_channels=None`` (or ``>=`` the weight-channel
    count) keeps everything and returns the input track unchanged. A cap of ``N``
    never yields more than ``N`` weight channels."""
    ranking = rank_channels(track)
    if max_channels is None:
        for r in ranking:
            r["kept"] = True
        return track, ranking
    if max_channels < 0:
        raise ValueError(f"max_channels must be >= 0, got {max_channels}")
    for r in ranking:
        r["kept"] = r["rank"] < max_channels
    return keep_top_weight(track, ranking, max_channels), ranking


def budget_metadata(ranking: List[dict], max_channels: Optional[int]) -> dict:
    """The ``openfacefx.budget`` sidecar: the cap and the full per-channel energy
    ranking (with ``kept`` flags). Plain JSON-ready and deterministic."""
    kept = sum(1 for r in ranking if r.get("kept"))
    return {
        "format": "openfacefx.budget",
        "version": 1,
        "max_channels": max_channels,
        "kept": kept,
        "dropped": len(ranking) - kept,
        "ranking": ranking,
    }
