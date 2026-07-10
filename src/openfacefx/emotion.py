"""Additive emotion/expression layer baked over speech (issue #38).

Production rigs keep expression on a *separate additive layer* over lip-sync and
add it onto the base at runtime: SALSA's EmoteR blends emphasis-timed emotes over
speech (https://crazyminnowstudio.com/unity-3d/lip-sync-salsa/), and Unreal
additive animation is defined as the *difference between a pose and a reference
(T/A) pose* added onto the base
(https://mocaponline.com/blogs/mocap-news/animation-layers-guide). This module
does the same in pure numpy: an authored emotion envelope becomes a true additive
delta ``channel_value - reference_value`` and is added onto the speech-solved
channels, scaled by a global intensity dial, clamped per channel, and re-thinned.

Two authoring modes (mirroring the two ways a writer thinks about affect):

  * ``channels`` -- direct emotion-channel keyframes (``smile``/``frown``/
    ``brow_raise`` ...), authored on the timeline like any curve.
  * ``valence_arousal`` -- a compact valence/arousal keyframe track (both in
    ``[-1, 1]``) mapped through the FIXED, hand-authored :data:`VA_TABLE` by
    bilinear interpolation. No ML: a table lookup and interpolation only, so the
    circumplex centre ``valence=arousal=0`` is the neutral node and maps to an
    all-zero pose.

The bake (:func:`bake_emotion`) resamples the base curve and the delta onto a
common grid via :func:`openfacefx.edits.sample` (piecewise-linear ``np.interp``,
the delta primitive), adds ``base + intensity * (pose - reference)``, clamps to
each channel's ``[lo, hi]`` (reusing the ``edits`` clamp conventions), and
re-thins with :func:`openfacefx.curves._rdp`. The result is an ordinary
:class:`~openfacefx.curves.FaceTrack` that exports through every existing
exporter unchanged.

**Additive / opt-in**: with no emotion, an all-zero delta, or ``intensity=0`` the
input track is returned untouched -- byte-identical output. Deterministic: numpy
``interp``/``clip`` + the shared RDP thinner and a fixed table, no RNG and no
wall-clock, identical on Python 3.9/3.13. numpy + stdlib only.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import numpy as np

from .curves import Channel, FaceTrack, Keyframe, _rdp
from .edits import sample, _finite, _validate_keys, _validate_clamp
from .visemes import VISEMES

FORMAT = "openfacefx.emotion"
VERSION = 1
_MODES = ("channels", "valence_arousal")


# --------------------------------------------------------------------------- #
# Fixed valence/arousal -> emotion-channel table (hand-authored, no ML).       #
# --------------------------------------------------------------------------- #

#: Valence and arousal are each sampled at these three nodes; a query point is
#: bilinearly interpolated inside the resulting 3x3 grid. Using ``-1, 0, +1``
#: (rather than only the corners) makes the exact centre ``valence=arousal=0`` a
#: real grid node, so neutral affect maps to an all-zero pose -- a true no-op.
VA_AXIS: Tuple[float, float, float] = (-1.0, 0.0, 1.0)

#: Emotion channels the valence/arousal table drives, in a stable emit order.
VA_EMOTION_CHANNELS: Tuple[str, ...] = (
    "smile", "cheek_raise", "brow_raise", "brow_lower", "frown",
)

#: The hand-authored affect table. ``VA_TABLE[channel][i_v][i_a]`` is the channel
#: weight at ``valence = VA_AXIS[i_v]`` and ``arousal = VA_AXIS[i_a]``; rows run
#: valence -1 / 0 / +1, columns run arousal -1 / 0 / +1. Weights follow the
#: circumplex model of affect (Russell) and FACS: pleasant valence drives a
#: zygomatic smile with a Duchenne cheek raise; unpleasant valence with rising
#: arousal drives a corrugator brow-lower (anger) over a mouth-corner-down frown;
#: high arousal at neutral valence reads as a raised, surprised brow. Every
#: channel is 0 at the neutral node ``[1][1]`` (valence=arousal=0).
VA_TABLE: Dict[str, List[List[float]]] = {
    # pleasant -> smile, opening wider with arousal (content -> happy -> elated)
    "smile":       [[0.00, 0.00, 0.00],
                    [0.00, 0.00, 0.00],
                    [0.45, 0.70, 0.90]],
    # Duchenne cheek raise shadows a genuine (aroused) smile
    "cheek_raise": [[0.00, 0.00, 0.00],
                    [0.00, 0.00, 0.00],
                    [0.30, 0.55, 0.80]],
    # surprise/alert brow at high arousal; a little oblique "sad" brow when
    # unpleasant and calm
    "brow_raise":  [[0.25, 0.05, 0.10],
                    [0.00, 0.00, 0.70],
                    [0.00, 0.10, 0.30]],
    # corrugator: unpleasant valence + rising arousal -> anger brow-lower
    "brow_lower":  [[0.10, 0.45, 0.85],
                    [0.00, 0.00, 0.00],
                    [0.00, 0.00, 0.00]],
    # mouth-corner-down: unpleasant -> frown (sad when calm, displeased when aroused)
    "frown":       [[0.70, 0.55, 0.55],
                    [0.00, 0.00, 0.00],
                    [0.00, 0.00, 0.00]],
}


def _bilerp(grid: np.ndarray, v: np.ndarray, a: np.ndarray) -> np.ndarray:
    """Bilinear interpolation of a 3x3 ``grid`` (indexed ``[i_v][i_a]`` over
    :data:`VA_AXIS`) at arrays ``v``/``a``, each clamped to ``[-1, 1]``."""
    v = np.clip(np.asarray(v, dtype=float), -1.0, 1.0) + 1.0   # -> [0, 2]
    a = np.clip(np.asarray(a, dtype=float), -1.0, 1.0) + 1.0
    iv = np.clip(np.floor(v).astype(int), 0, 1)
    ia = np.clip(np.floor(a).astype(int), 0, 1)
    tv, ta = v - iv, a - ia
    return (grid[iv, ia]         * (1.0 - tv) * (1.0 - ta)
            + grid[iv + 1, ia]     * tv * (1.0 - ta)
            + grid[iv, ia + 1]     * (1.0 - tv) * ta
            + grid[iv + 1, ia + 1] * tv * ta)


def va_to_pose(valence: float, arousal: float) -> Dict[str, float]:
    """Map one ``(valence, arousal)`` point in ``[-1, 1]^2`` to emotion-channel
    weights by bilinear interpolation over :data:`VA_TABLE`.

    Deterministic and reproducible -- a table lookup and interpolation, no ML.
    Values outside ``[-1, 1]`` clamp to the nearest edge. ``va_to_pose(0, 0)`` is
    the all-zero neutral pose."""
    v = np.asarray([valence], dtype=float)
    a = np.asarray([arousal], dtype=float)
    return {ch: float(_bilerp(np.asarray(VA_TABLE[ch], dtype=float), v, a)[0])
            for ch in VA_EMOTION_CHANNELS}


def _va_pose_arrays(V: np.ndarray, A: np.ndarray) -> Dict[str, np.ndarray]:
    """Vectorised :func:`va_to_pose`: per-channel weight arrays for the (already
    sampled) valence/arousal arrays ``V``/``A``."""
    return {ch: _bilerp(np.asarray(VA_TABLE[ch], dtype=float), V, A)
            for ch in VA_EMOTION_CHANNELS}


# --------------------------------------------------------------------------- #
# Envelope document + validation (modelled on edits.EditsDoc)                  #
# --------------------------------------------------------------------------- #

@dataclass
class EmotionEnvelope:
    """A parsed ``openfacefx.emotion`` envelope.

    Two input modes select which field carries the authored track:

    * ``mode="channels"`` -- ``channels`` maps an emotion-channel name to a
      keyframe list ``[[t, v], ...]`` authored directly.
    * ``mode="valence_arousal"`` -- ``va`` holds ``valence`` and/or ``arousal``
      keyframe lists (values in ``[-1, 1]``); :data:`VA_TABLE` maps them onto the
      channels in :data:`VA_EMOTION_CHANNELS`.

    ``reference`` is the neutral/rest pose the additive delta is measured against
    (``channel_value - reference_value``); a channel absent from it rests at 0.
    ``clamps`` optionally bounds a channel's *baked* output to ``[lo, hi]``
    (``0 <= lo <= hi <= 1``). ``fps`` is the grid rate the valence/arousal track
    is sampled on when baking.
    """
    mode: str
    channels: Dict[str, List[List[float]]] = field(default_factory=dict)
    va: Dict[str, List[List[float]]] = field(default_factory=dict)
    reference: Dict[str, float] = field(default_factory=dict)
    clamps: Dict[str, List[float]] = field(default_factory=dict)
    fps: float = 60.0

    def to_dict(self) -> Dict:
        d: Dict = {"format": FORMAT, "version": VERSION, "mode": self.mode,
                   "fps": self.fps}
        if self.mode == "channels":
            d["channels"] = self.channels
        else:
            d["va"] = self.va
        if self.reference:
            d["reference"] = self.reference
        if self.clamps:
            d["clamps"] = self.clamps
        return d

    @classmethod
    def from_dict(cls, d: Dict) -> "EmotionEnvelope":
        """Parse and validate an envelope dict, raising ``ValueError`` with a
        clear message on any malformed field."""
        if d.get("format") != FORMAT or d.get("version") != VERSION:
            raise ValueError(
                f"expected format {FORMAT!r} version {VERSION}, got "
                f"{d.get('format')!r} version {d.get('version')!r}")
        mode = d.get("mode")
        if mode not in _MODES:
            raise ValueError(
                f"emotion 'mode' must be one of {_MODES}, got {mode!r}")
        fps = d.get("fps", 60.0)
        if not _finite(fps) or fps <= 0.0:
            raise ValueError(f"emotion 'fps' must be a positive number, got {fps!r}")
        channels: Dict[str, List[List[float]]] = {}
        va: Dict[str, List[List[float]]] = {}
        if mode == "channels":
            channels = _validate_emotion_channels(d.get("channels"))
        else:
            va = _validate_va(d.get("va"))
        return cls(
            mode=mode, channels=channels, va=va,
            reference=_validate_reference(d.get("reference")),
            clamps=_validate_clamps(d.get("clamps")),
            fps=float(fps),
        )


def _validate_emotion_channels(raw) -> Dict[str, List[List[float]]]:
    if not isinstance(raw, dict) or not raw:
        raise ValueError("emotion 'channels' must be a non-empty object of "
                         "name -> [[t, v], ...] keyframes")
    return {str(name): _validate_keys(str(name), keys)
            for name, keys in raw.items()}


def _validate_va(raw) -> Dict[str, List[List[float]]]:
    if not isinstance(raw, dict):
        raise ValueError("emotion 'va' must be an object with 'valence' and/or "
                         "'arousal' keyframe lists")
    unknown = set(raw) - {"valence", "arousal"}
    if unknown:
        raise ValueError(f"emotion 'va' has unknown key(s) {sorted(unknown)}; "
                         "only 'valence' and 'arousal' are recognised")
    out: Dict[str, List[List[float]]] = {}
    for axis in ("valence", "arousal"):
        if raw.get(axis) is not None:
            out[axis] = _validate_keys(axis, raw[axis])
    if not out:
        raise ValueError("emotion 'va' needs at least one of 'valence'/'arousal'")
    return out


def _validate_reference(raw) -> Dict[str, float]:
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise ValueError("emotion 'reference' must be an object of channel -> value")
    out: Dict[str, float] = {}
    for name, v in raw.items():
        if not _finite(v):
            raise ValueError(
                f"emotion reference {name!r} must be a finite number, got {v!r}")
        out[str(name)] = float(v)
    return out


def _validate_clamps(raw) -> Dict[str, List[float]]:
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise ValueError("emotion 'clamps' must be an object of channel -> [lo, hi]")
    return {str(name): _validate_clamp(str(name), c) for name, c in raw.items()}


def save_envelope(env: EmotionEnvelope, path: str) -> None:
    """Write an envelope as pretty JSON (2-space indent, trailing newline)."""
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(env.to_dict(), fh, indent=2)
        fh.write("\n")


def load_envelope(path: str) -> EmotionEnvelope:
    """Read and validate an envelope from ``path``."""
    with open(path, encoding="utf-8") as fh:
        try:
            d = json.load(fh)
        except json.JSONDecodeError as e:
            raise ValueError(f"{path}: not valid JSON ({e})") from None
    return EmotionEnvelope.from_dict(d)


# --------------------------------------------------------------------------- #
# Additive delta + bake                                                        #
# --------------------------------------------------------------------------- #

def _va_grid(env: EmotionEnvelope, duration: float) -> np.ndarray:
    """Uniform sampling grid for the valence/arousal track: ``0`` to the later of
    ``duration`` and the last keyframe time, at ``env.fps`` (both endpoints
    included). Dense enough that the nonlinear table lookup is captured before
    RDP re-thins the result."""
    last = 0.0
    for keys in env.va.values():
        if keys:
            last = max(last, float(keys[-1][0]))
    end = max(float(duration), last)
    if end <= 0.0:
        return np.asarray([0.0], dtype=float)
    n = max(2, int(round(end * env.fps)) + 1)
    return np.linspace(0.0, end, n)


def _emotion_deltas(env: EmotionEnvelope, duration: float
                    ) -> Dict[str, Tuple[np.ndarray, np.ndarray]]:
    """Per-channel additive delta curves ``pose - reference`` as ``(times,
    deltas)`` arrays on their own knot grid.

    ``channels`` mode keys the delta on the authored keyframe times; the
    ``valence_arousal`` mode samples the (nonlinear) table output on the uniform
    per-frame :func:`_va_grid` and subtracts the reference."""
    out: Dict[str, Tuple[np.ndarray, np.ndarray]] = {}
    if env.mode == "channels":
        for ch, keys in env.channels.items():
            T = np.asarray([t for t, _ in keys], dtype=float)
            pose = np.asarray([v for _, v in keys], dtype=float)
            out[ch] = (T, pose - env.reference.get(ch, 0.0))
        return out
    T = _va_grid(env, duration)
    V = sample(env.va.get("valence", [[0.0, 0.0]]), T)
    A = sample(env.va.get("arousal", [[0.0, 0.0]]), T)
    poses = _va_pose_arrays(V, A)
    for ch in VA_EMOTION_CHANNELS:
        out[ch] = (T, poses[ch] - env.reference.get(ch, 0.0))
    return out


def _validate_clamps_param(clamps) -> Dict[str, List[float]]:
    if clamps is None:
        return {}
    if not isinstance(clamps, dict):
        raise ValueError("clamps must be a dict of channel -> (lo, hi)")
    return {str(name): _validate_clamp(str(name), c) for name, c in clamps.items()}


def _bake_channel(base_ch: Channel, delta: Tuple[np.ndarray, np.ndarray],
                  intensity: float, clamp: Tuple[float, float], eps: float
                  ) -> Tuple[Channel, bool]:
    """Add ``intensity * delta`` onto an existing base channel. Returns
    ``(channel, changed)``; when the additive contribution is exactly zero the
    base channel is returned verbatim and ``changed`` is ``False`` (so an
    untouched channel stays byte-identical)."""
    Tk, dv = delta
    times = sorted({float(k.time) for k in base_ch.keys} | {float(t) for t in Tk})
    T = np.asarray(times, dtype=float)
    contrib = intensity * sample(list(zip(Tk.tolist(), dv.tolist())), T)
    if not np.any(contrib != 0.0):
        return base_ch, False
    v = np.clip(sample(base_ch, T) + contrib, clamp[0], clamp[1])
    idx = _rdp(T, v, eps)
    return Channel(base_ch.name,
                   [Keyframe(round(float(T[i]), 4), round(float(v[i]), 4))
                    for i in idx]), True


def _bake_new_channel(name: str, delta: Tuple[np.ndarray, np.ndarray],
                      intensity: float, clamp: Tuple[float, float], eps: float
                      ) -> Optional[Channel]:
    """Bake an emotion channel absent from the base track (base value 0). Returns
    ``None`` when the additive contribution is exactly zero (the channel never
    fires and is skipped, as :func:`curves.reduce_to_track` skips silent ones)."""
    Tk, dv = delta
    if Tk.size == 0:
        return None
    contrib = intensity * dv
    if not np.any(contrib != 0.0):
        return None
    v = np.clip(contrib, clamp[0], clamp[1])
    idx = _rdp(Tk, v, eps)
    return Channel(name, [Keyframe(round(float(Tk[i]), 4), round(float(v[i]), 4))
                          for i in idx])


def _extend_target_set(track: FaceTrack, new_names: List[str]):
    if not new_names:
        return track.target_set
    base = list(track.target_set) if track.target_set is not None else list(VISEMES)
    for n in new_names:
        if n not in base:
            base.append(n)
    return base


def bake_emotion(track: FaceTrack, envelope, *, intensity: float = 1.0,
                 clamps: Optional[Dict[str, Tuple[float, float]]] = None,
                 eps: float = 0.015) -> FaceTrack:
    """Bake an additive emotion ``envelope`` onto a speech-solved ``track``.

    ``envelope`` is an :class:`EmotionEnvelope` or a raw dict (parsed and
    validated). For each emotion channel the additive delta ``pose - reference``
    is resampled onto a grid shared with the base curve, scaled by ``intensity``,
    added, clamped to the channel's ``[lo, hi]`` and re-thinned with the shared
    RDP thinner at ``eps``. Channels the base track lacks are appended and their
    names added to ``target_set``; channels it already has are updated in place.

    ``clamps`` maps ``channel -> (lo, hi)`` and overrides the envelope's per-channel
    ``clamps`` (default ``[0, 1]``). ``intensity`` scales the delta linearly and
    must be finite and ``>= 0``.

    Additive / opt-in: with ``intensity == 0``, no emotion channels, or an
    exactly-zero delta on every channel, the input ``track`` is returned
    unchanged -- byte-identical output. Deterministic; the result is an ordinary
    :class:`~openfacefx.curves.FaceTrack` that exports through every exporter."""
    if not _finite(intensity) or intensity < 0.0:
        raise ValueError(f"intensity must be a finite number >= 0, got {intensity!r}")
    if not _finite(eps) or eps < 0.0:
        raise ValueError(f"eps must be a finite number >= 0, got {eps!r}")
    env = (envelope if isinstance(envelope, EmotionEnvelope)
           else EmotionEnvelope.from_dict(envelope))
    override = _validate_clamps_param(clamps)

    if intensity == 0.0:
        return track
    deltas = _emotion_deltas(env, track.duration)
    if not deltas:
        return track

    def _clamp_for(name: str) -> Tuple[float, float]:
        c = override.get(name) or env.clamps.get(name) or [0.0, 1.0]
        return float(c[0]), float(c[1])

    base_names = {c.name for c in track.channels}
    out_channels: List[Channel] = []
    modified = False
    for ch in track.channels:
        d = deltas.get(ch.name)
        if d is None:
            out_channels.append(ch)               # untouched speech channel
            continue
        new_ch, changed = _bake_channel(ch, d, intensity, _clamp_for(ch.name), eps)
        out_channels.append(new_ch if changed else ch)
        modified = modified or changed

    added: List[Channel] = []
    for name in sorted(set(deltas) - base_names):
        ch = _bake_new_channel(name, deltas[name], intensity, _clamp_for(name), eps)
        if ch is not None:
            added.append(ch)

    if not modified and not added:
        return track                              # nothing fired -> byte-identical

    out_channels.extend(added)
    baked = FaceTrack(track.fps, out_channels,
                      _extend_target_set(track, [c.name for c in added]))
    # Carry the additive event/take layer (issue #6) through untouched.
    baked.events = list(getattr(track, "events", None) or [])
    baked.variants = getattr(track, "variants", None)
    return baked
