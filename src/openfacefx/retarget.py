"""Retarget viseme channels onto another rig's blendshape convention.

A mapping sends each Oculus-15 viseme to one or more target shapes with a
weight scale::

    {"PP": [("mouthClose", 0.9), ("mouthPressLeft", 0.35)], ...}

``retarget`` resamples on the union of the contributing channels' key times
(linear interpolation, matching intended playback), sums the scaled
contributions per target, clamps to [0, 1], and returns a new ``FaceTrack``.
Presets for common rigs live in ``PRESETS``; they are plain data — copy and
tweak for your character.

An integrator can trim individual rig shapes without editing those weighted
tables by passing ``adjust={target: (gain, offset)}`` (or running the standalone
``apply_adjust``): each named target's value becomes ``clamp(gain*value + offset,
0, 1)`` after the weighted sum — e.g. a weaker ``jawOpen`` or a ``mouthSmile``
held slightly on. ``retarget(..., adjust=A)`` is exactly
``apply_adjust(retarget(...), A)``.

A rig that lacks some of a preset's target shapes can pass ``available=`` (an
iterable of the shapes it actually has). Any mapped target not in that set is
rerouted through a per-preset ``PRESET_FALLBACKS`` table so its weight
redistributes instead of vanishing (e.g. a tongue-less ARKit rig sends
``tongueOut`` to a small ``jawOpen``); a target with no fallback rule is
dropped. Provenance and the fallback tables: docs/retargeting.md.
"""

from __future__ import annotations

from typing import Dict, Iterable, List, Optional, Sequence, Tuple, Union

from .curves import Channel, FaceTrack, Keyframe
from .links import apply_link
from .visemes import VISEMES

Mapping = Dict[str, Sequence[Tuple[str, float]]]
# rig target -> a (gain, offset) linear trim, OR a nonlinear link spec
# ``{"function": name, ...params}`` (#68). The tuple form stays byte-identical.
Adjust = Dict[str, Union[Tuple[float, float], Dict]]


def _sampler(channel: Channel):
    keys = channel.keys

    def sample(t: float) -> float:
        if not keys:
            return 0.0
        if t <= keys[0].time:
            return keys[0].value
        if t >= keys[-1].time:
            return keys[-1].value
        lo, hi = 0, len(keys) - 1
        while hi - lo > 1:
            mid = (lo + hi) // 2
            if keys[mid].time <= t:
                lo = mid
            else:
                hi = mid
        k0, k1 = keys[lo], keys[hi]
        f = (t - k0.time) / ((k1.time - k0.time) or 1e-9)
        return k0.value + (k1.value - k0.value) * f

    return sample


def _resolve_target(target: str, scale: float, available: Optional[set],
                    fallbacks: Optional[Mapping],
                    _seen: frozenset = frozenset()):
    """Expand one ``(target, scale)`` contribution against a rig's shapes.

    With ``available=None`` (or the target present) the contribution is yielded
    unchanged. An unavailable target reroutes through ``fallbacks`` — chained
    (a replacement may itself be unavailable) and cycle-guarded — with weights
    multiplied along the way; a target with no fallback rule yields nothing
    (dropped). An explicit empty rule ``()`` is the way to say "drop this".
    """
    if available is None or target in available:
        yield (target, scale)
        return
    rule = fallbacks.get(target) if fallbacks else None
    if rule is None or target in _seen:
        return
    seen = _seen | {target}
    for repl, s in rule:
        yield from _resolve_target(repl, scale * s, available, fallbacks, seen)


def retarget(track: FaceTrack, mapping: Mapping,
             available: Optional[Iterable[str]] = None,
             fallbacks: Optional[Mapping] = None,
             adjust: Optional[Adjust] = None) -> FaceTrack:
    """Return a new FaceTrack with channels renamed/combined per ``mapping``.

    Source channels absent from the mapping are dropped (deliberately: a rig
    that lacks a shape should not receive its weight).

    ``available`` is an iterable of the target shape names the rig actually has;
    any mapped target outside it reroutes through ``fallbacks`` (a
    ``{target: [(replacement, scale), ...]}`` table, typically
    ``PRESET_FALLBACKS[name]``) so weight redistributes rather than dropping
    silently. ``available=None`` (default) disables filtering and is identical
    to a plain rename/combine.

    ``adjust`` is an optional ``{target: (gain, offset)}`` per-shape trim applied
    to the finished channels (see ``apply_adjust``); it leaves the weighted
    ``mapping`` untouched, so ``retarget(..., adjust=A)`` is exactly
    ``apply_adjust(retarget(...), A)``. ``adjust=None`` (default) is a no-op.
    """
    avail = set(available) if available is not None else None
    # target name -> list of (sampler, key times, scale)
    contributors: Dict[str, List[tuple]] = {}
    for ch in track.channels:
        times = [k.time for k in ch.keys]
        for target, scale in mapping.get(ch.name, ()):
            for final, s in _resolve_target(target, scale, avail, fallbacks):
                contributors.setdefault(final, []).append((_sampler(ch), times, s))

    channels: List[Channel] = []
    for target, sources in contributors.items():
        times = sorted({t for _, ts, _ in sources for t in ts})
        keys = []
        for t in times:
            v = sum(sample(t) * scale for sample, _, scale in sources)
            keys.append(Keyframe(t, round(min(max(v, 0.0), 1.0), 4)))
        channels.append(Channel(target, keys))
    channels.sort(key=lambda c: c.name)
    # Declared vocabulary = every target the mapping can actually produce for
    # this rig (fallback-resolved), so an available= run advertises only the
    # shapes it emits.
    all_targets = sorted({ft for targets in mapping.values() for t, _ in targets
                          for ft, _ in _resolve_target(t, 1.0, avail, fallbacks)})
    # Carry the event/take layer through the remap (issue #34): retargeting
    # renames mouth channels; the events are timeline metadata and survive it.
    ft = FaceTrack(fps=track.fps, channels=channels, target_set=all_targets,
                   events=track.events, variants=track.variants)
    return apply_adjust(ft, adjust) if adjust else ft


def _clip_span(track: FaceTrack):
    """(min, max) key time across every channel, or None for a keyless track."""
    times = [k.time for c in track.channels for k in c.keys]
    return (min(times), max(times)) if times else None


def apply_adjust(track: FaceTrack, adjust: Adjust) -> FaceTrack:
    """Return a new FaceTrack with a per-target ``(gain, offset)`` trim applied.

    Every channel whose name has an ``adjust`` entry has each key value ``v``
    remapped and clamped to ``[0, 1]`` (rounded like the rest of the pipeline);
    channels without an entry pass through unchanged. An entry is either a
    ``(gain, offset)`` tuple — the linear ``clamp(gain*v + offset)`` trim — or a
    nonlinear **link function** spec ``{"function": name, ...params}`` (#68, e.g.
    ``{"function": "quadratic", "m": 1.4}``); see :mod:`openfacefx.links`. This is a
    pure post-process on already-retargeted (or native) curves, so the weighted
    preset tables stay byte-identical — the way to trim ``jawOpen`` a touch weaker,
    give it an ease-in response, or hold ``mouthSmileLeft`` slightly on without
    editing a mapping.

    An entry whose target has *no* channel yet but a positive constant
    (``clamp(offset, 0, 1) > 0``) is materialised as a constant channel spanning
    the clip and added to ``target_set`` — that is how "always slightly on" lifts
    a shape the rig would otherwise never receive. ``gain`` is irrelevant there
    (the absent base is 0). An empty/None ``adjust`` returns ``track`` unchanged
    (byte-identical).
    """
    if not adjust:
        return track
    present = {c.name for c in track.channels}
    channels: List[Channel] = []
    for c in track.channels:
        adj = adjust.get(c.name)
        if adj is None:
            channels.append(c)
            continue
        if isinstance(adj, tuple):                 # linear gain/offset (byte-identical)
            g, o = adj
            keys = [Keyframe(k.time, round(min(max(g * k.value + o, 0.0), 1.0), 4))
                    for k in c.keys]
        else:                                      # nonlinear link function (#68)
            fn = adj["function"]
            params = {k: v for k, v in adj.items() if k != "function"}
            keys = [Keyframe(k.time,
                             round(min(max(float(apply_link(k.value, fn, params)),
                                           0.0), 1.0), 4))
                    for k in c.keys]
        channels.append(Channel(c.name, keys))
    # "Always slightly on": a positive-offset target with no curve yet becomes a
    # constant channel over the clip (deterministic, appended in name order).
    span = _clip_span(track)
    created: List[Channel] = []
    for name in sorted(adjust):
        if name in present or span is None:
            continue
        spec = adjust[name]
        if isinstance(spec, tuple):
            base = spec[1]                          # the offset (absent base is 0)
        elif spec.get("function") == "constant":
            base = spec.get("c", 0.0)              # a constant link needs no input
        else:
            continue                               # other links need an input channel
        val = round(min(max(base, 0.0), 1.0), 4)
        if val <= 0.0:
            continue
        t0, t1 = span
        keys = ([Keyframe(t0, val)] if t0 == t1
                else [Keyframe(t0, val), Keyframe(t1, val)])
        created.append(Channel(name, keys))
    channels.extend(created)
    target_set = track.target_set
    if created:
        # None == the built-in viseme vocab; make it explicit before extending.
        target_set = list(VISEMES if target_set is None else target_set)
        for c in created:
            if c.name not in target_set:
                target_set.append(c.name)
    return FaceTrack(fps=track.fps, channels=channels, target_set=target_set,
                     events=track.events, variants=track.variants)


def rename_only(prefix: str = "", names: Dict[str, str] = None) -> Mapping:
    """Build a 1:1 mapping that renames each viseme, e.g. ``viseme_PP``."""
    names = names or {}
    return {v: [(names.get(v, prefix + v), 1.0)] for v in VISEMES}


# ---------------------------------------------------------------------------
# Presets. Provenance, quirks and alternatives: docs/retargeting.md.
# Weighted tables are data, not gospel — copy one and tune it to your mesh.
# ---------------------------------------------------------------------------

# Apple ARKit 52 blendshapes. Weights reproduced verbatim from
# met4citizen/TalkingHead (MIT), a shipping viseme->ARKit map, EXCEPT the
# alveolar tongueOut on DD noted below. Known quirks kept as-published: CH and
# RR are identical; PP seals via lip-roll rather than mouthClose.
#
# tongueOut coverage (issue #53): the alveolar family drives ARKit's tongue-
# protrusion morph — TH 0.4, nn 0.2, and now DD 0.2 (t/d/l, "tongue to the
# alveolar ridge", added to match nn and close the gap). kk (k/g) deliberately
# does NOT: it is velar ("back of tongue raised"), the tongue TIP stays down, so
# protrusion would misrepresent it — and ARKit has no tongue-back morph. This is
# a deliberate, versioned change to the shipped preset's output (docs/retargeting.md).
_ARKIT = {
    "PP": (("mouthRollLower", 0.8), ("mouthRollUpper", 0.8),
           ("mouthUpperUpLeft", 0.3), ("mouthUpperUpRight", 0.3)),
    "FF": (("mouthPucker", 1.0), ("mouthShrugUpper", 1.0),
           ("mouthRollLower", 1.0), ("mouthDimpleLeft", 1.0),
           ("mouthDimpleRight", 1.0), ("mouthLowerDownLeft", 0.2),
           ("mouthLowerDownRight", 0.2)),
    "TH": (("mouthRollUpper", 0.6), ("jawOpen", 0.2), ("tongueOut", 0.4)),
    "DD": (("mouthPressLeft", 0.8), ("mouthPressRight", 0.8),
           ("mouthFunnel", 0.5), ("jawOpen", 0.2), ("tongueOut", 0.2)),
    # kk is velar — no tongueOut on purpose (see the header note).
    "kk": (("mouthLowerDownLeft", 0.4), ("mouthLowerDownRight", 0.4),
           ("mouthDimpleLeft", 0.3), ("mouthDimpleRight", 0.3),
           ("mouthFunnel", 0.3), ("mouthPucker", 0.3), ("jawOpen", 0.15)),
    "CH": (("mouthPucker", 0.5), ("jawOpen", 0.2)),
    "SS": (("mouthPressLeft", 0.8), ("mouthPressRight", 0.8),
           ("mouthLowerDownLeft", 0.5), ("mouthLowerDownRight", 0.5),
           ("jawOpen", 0.1)),
    "nn": (("mouthLowerDownLeft", 0.4), ("mouthLowerDownRight", 0.4),
           ("mouthDimpleLeft", 0.3), ("mouthDimpleRight", 0.3),
           ("mouthFunnel", 0.3), ("mouthPucker", 0.3), ("jawOpen", 0.15),
           ("tongueOut", 0.2)),
    "RR": (("mouthPucker", 0.5), ("jawOpen", 0.2)),
    "aa": (("jawOpen", 0.6),),
    "E":  (("mouthPressLeft", 0.8), ("mouthPressRight", 0.8),
           ("mouthDimpleLeft", 1.0), ("mouthDimpleRight", 1.0),
           ("jawOpen", 0.3)),
    "I":  (("mouthPressLeft", 0.6), ("mouthPressRight", 0.6),
           ("mouthDimpleLeft", 0.6), ("mouthDimpleRight", 0.6),
           ("jawOpen", 0.2)),
    "O":  (("mouthPucker", 1.0), ("jawForward", 0.6), ("jawOpen", 0.2)),
    "U":  (("mouthFunnel", 1.0),),
}

# Rhubarb Lip Sync mouth shapes (A-H + X, extended shapes G/H used).
# Rhubarb is pose-based: nearest single shape at full weight.
_RHUBARB = {
    "sil": (("X", 1.0),), "PP": (("A", 1.0),), "FF": (("G", 1.0),),
    "TH": (("B", 1.0),), "DD": (("B", 1.0),), "kk": (("B", 1.0),),
    "CH": (("B", 1.0),), "SS": (("B", 1.0),), "nn": (("H", 1.0),),
    "RR": (("E", 1.0),), "aa": (("D", 1.0),), "E": (("C", 1.0),),
    "I": (("B", 1.0),), "O": (("F", 1.0),), "U": (("F", 1.0),),
}

# Preston-Blair series (Papagayo / Moho / OpenToonz convention). The
# canonical consonant catch-all layer is named "etc" — OpenToonz's lip-sync
# import matches layer names exactly, so emitting anything else silently
# fails to switch. (The full set also has WQ; viseme-level input can't
# distinguish W from UW, so U stays on U.)
_PRESTON_BLAIR = {
    "sil": (("rest", 1.0),), "PP": (("MBP", 1.0),), "FF": (("FV", 1.0),),
    "TH": (("etc", 1.0),), "DD": (("etc", 1.0),),
    "kk": (("etc", 1.0),), "CH": (("etc", 1.0),),
    "SS": (("etc", 1.0),), "RR": (("etc", 1.0),),
    "nn": (("L", 1.0),), "aa": (("AI", 1.0),), "I": (("AI", 1.0),),
    "E": (("E", 1.0),), "O": (("O", 1.0),), "U": (("U", 1.0),),
}

# VRM 1.0 expression presets: only five vowel mouths exist in the standard.
# Vowels map canonically; consonants borrow the nearest vowel mouth (coarse
# by design — VRM has no consonant visemes). PP/sil rest at zero.
_VRM = {
    "aa": (("aa", 1.0),), "I": (("ih", 1.0),), "U": (("ou", 1.0),),
    "E": (("ee", 1.0),), "O": (("oh", 1.0),),
    "FF": (("ih", 0.2),), "TH": (("ih", 0.3),), "DD": (("ih", 0.5),),
    "kk": (("aa", 0.3),), "CH": (("ou", 0.5),), "SS": (("ih", 0.4),),
    "nn": (("ih", 0.4),), "RR": (("ou", 0.4),),
}

# VRM 0.x BlendShapePreset vowels. Same five-vowel design as `vrm`, but VRM 0.x
# names the presets with uppercase single letters (A I U E O) where VRM 1.0
# renamed them (aa ih ou ee oh). The 0.x<->1.0 correspondence is the spec's own:
# "A (aa)", "I (ih)", "U (ou)", "E (E)", "O (oh)". Consonant borrowing mirrors
# `vrm` exactly — coarse by design, VRM has no consonant visemes.
_VRM0 = {
    "aa": (("A", 1.0),), "I": (("I", 1.0),), "U": (("U", 1.0),),
    "E": (("E", 1.0),), "O": (("O", 1.0),),
    "FF": (("I", 0.2),), "TH": (("I", 0.3),), "DD": (("I", 0.5),),
    "kk": (("A", 0.3),), "CH": (("U", 0.5),), "SS": (("I", 0.4),),
    "nn": (("I", 0.4),), "RR": (("U", 0.4),),
}

# Reallusion Character Creator 4 / iClone viseme set (near 1:1 rename). The same
# Viseme Panel phoneme-pair labels back CC3 and CC4 (CC4's ExPlus / "CC4
# Extended" changes sit in the facial-profile layer beneath the panel, not in
# these names), so this preset covers CC3 too.
_CC4 = {
    "PP": (("B_M_P", 1.0),), "FF": (("F_V", 1.0),), "TH": (("Th", 1.0),),
    "DD": (("T_L_D_N", 1.0),), "nn": (("T_L_D_N", 1.0),),
    "kk": (("K_G_H_NG", 1.0),), "CH": (("Ch_J", 1.0),),
    "SS": (("S_Z", 1.0),), "RR": (("R", 1.0),), "aa": (("Ah", 1.0),),
    "E": (("AE", 1.0),), "I": (("EE", 1.0),), "O": (("Oh", 1.0),),
    "U": (("W_OO", 1.0),),
}

# Ready Player Me exposes the Oculus 15 visemes as morph targets named
# ``viseme_<name>`` in verbatim Oculus casing (viseme_sil, viseme_PP, ...,
# viseme_aa, viseme_E, viseme_I, viseme_O, viseme_U), so the map is exactly
# ``rename_only(prefix="viseme_")``. (RPM also ships the ARKit 52 for that route
# — use the ``arkit`` preset instead if you drive those.)
_READY_PLAYER_ME = {v: ((f"viseme_{v}", 1.0),) for v in VISEMES}

PRESETS: Dict[str, Mapping] = {
    "arkit": _ARKIT,
    "rhubarb": _RHUBARB,
    "preston_blair": _PRESTON_BLAIR,
    "vrm": _VRM,
    "vrm0": _VRM0,
    "cc4": _CC4,
    "readyplayerme": _READY_PLAYER_ME,
}

# Optional-shape fallbacks: when ``retarget(..., available=)`` is given a rig's
# real shapes, a mapped target the rig lacks reroutes through its preset's table
# here (``{target: [(replacement, scale), ...]}``; ``()`` means drop) instead of
# vanishing. Data, like the weight tables — tune or extend per rig.
PRESET_FALLBACKS: Dict[str, Mapping] = {
    # Rhubarb's documented basic-set collapse (README, "Extended mouth shapes"):
    # a rig with only the six basic shapes A-F loses G/H/X. Canonical home for
    # the table export_cues derives its cue-label view from.
    "rhubarb": {"G": (("A", 1.0),), "H": (("C", 1.0),), "X": (("A", 1.0),)},
    # Tongue-less ARKit rigs (e.g. NVIDIA Audio2Face, which does not animate the
    # tongue) reroute tongueOut to a small jaw opening rather than dropping the
    # TH/nn tongue weight. Our heuristic, not an Apple convention.
    "arkit": {"tongueOut": (("jawOpen", 0.2),)},
}
