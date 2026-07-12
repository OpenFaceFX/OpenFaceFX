"""Read a MikuMikuDance ``.vmd`` morph animation back into a :class:`FaceTrack`.

Read-side inverse of :mod:`openfacefx.export_vmd` (issue #60). MMD and its
ecosystem (MMM, blender_mmd_tools, three.js ``MMDLoader``, babylon-mmd,
readfacevmd) trade ``.vmd`` facial motion; we already *write* it, and — exactly
like :mod:`importers` is the read side of :mod:`export_cues` and
:mod:`importers_csv` the read side of a wide CSV — a reader for our own writer is
first-class, letting a studio bring a ``.vmd`` lip library in to re-coarticulate,
retarget or re-export it.

Binary layout (little-endian, ShiftJIS), the exact bytes :func:`export_vmd.vmd_bytes`
emits (verified against blender_mmd_tools and babylon-mmd):

  * **Header** ``char[30]`` — ``Vocaloid Motion Data 0002`` (v2) or ``… file`` (v1).
  * **Model name** — ShiftJIS, NUL-padded (20 bytes for v2, 10 for v1).
  * **Bone frames** — ``uint32`` count, then 111 bytes each (``char[15]`` name +
    ``uint32`` frame + 3×f32 position + 4×f32 quaternion + 64-byte interpolation).
    We traverse the whole block (real motions put head motion here) and harvest
    the 頭/首 head bones into ``headPitch/headYaw/headRoll`` on re-export.
  * **Morph frames** — ``uint32`` count, then 23 bytes each (``char[15]`` ShiftJIS
    name + ``uint32`` frame + ``float32`` weight). The payload.
  * **Trailing camera / light / self-shadow / IK sections** carry no facial data;
    we stop after the morph block, so an absent or truncated tail is a non-issue.

Morph name → channel inverts only the unambiguous vowel subset of the viseme→morph
map (あ→aa, い→I, う→U, え→E, お→O, ん→nn): the forward map is many→one (consonant
visemes collapse onto the vowel morphs), so un-collapsing a consonant is
impossible and must not be guessed. Every other morph name becomes its own
passthrough channel — reported in the output, never silently dropped (the
:mod:`importers` rule). ``time = frame / fps`` (fps 30 default) inverts
``round(time*fps)``. Deterministic: pure ``struct`` + stdlib, identical on py3.9
and py3.13.
"""

from __future__ import annotations

import math
import struct
from typing import Dict, List, Optional, Tuple

from .curves import Channel, FaceTrack, Keyframe
from .export_vmd import DEFAULT_MORPH_MAP, _DEFAULT_FPS, _HEADER_LEN, _MORPH_NAME_LEN

# VMD record sizes (little-endian). Bone frame = name char[15] + u32 frame +
# 3×f32 pos + 4×f32 quat + 64-byte interpolation; morph frame = name char[15] +
# u32 frame + f32 weight.
_BONE_RECORD_LEN = _MORPH_NAME_LEN + 4 + 12 + 16 + 64   # 111
_MORPH_RECORD_LEN = _MORPH_NAME_LEN + 4 + 4             # 23
_QUAT_OFFSET = _MORPH_NAME_LEN + 4 + 12                 # name + frame + pos → quat

_MAGIC_V2 = b"Vocaloid Motion Data 0002"
_MAGIC_V1 = b"Vocaloid Motion Data file"
_MODEL_NAME_LEN_V2 = 20
_MODEL_NAME_LEN_V1 = 10

# MMD head/neck bone names (ShiftJIS in-file). Head is preferred; neck is the
# fallback when a motion animates only 首. Composition of both is not attempted.
_HEAD_BONE = "頭"   # 頭 "head"
_NECK_BONE = "首"   # 首 "neck"

# Vowel visemes whose DEFAULT_MORPH_MAP entries invert 1:1 (see module docstring).
_VOWEL_VISEMES = ("aa", "I", "U", "E", "O", "nn")


def _validate_fps(fps) -> float:
    if not (isinstance(fps, (int, float)) and not isinstance(fps, bool)
            and 0.0 < float(fps) < float("inf")):
        raise ValueError(f"vmd: fps must be a finite value > 0, got {fps!r}")
    return float(fps)


def _inverse_vowel_map(morph_map: Dict[str, str]) -> Dict[str, str]:
    """Morph name → vowel viseme, the unambiguous inverse of a viseme→morph map.

    Only the five vowel morphs and ``nn`` invert cleanly; a morph reachable from a
    vowel is attributed to that vowel (first in :data:`_VOWEL_VISEMES` order wins),
    and every other morph is left for passthrough. Empty ("" = dropped) morphs are
    ignored."""
    inv: Dict[str, str] = {}
    for vis in _VOWEL_VISEMES:
        m = morph_map.get(vis)
        if m:
            inv.setdefault(m, vis)
    return inv


def _decode_name(raw: bytes) -> str:
    """Decode a NUL-padded ShiftJIS name field: drop padding at the first NUL and
    tolerate a malformed trailing byte rather than crash the parse."""
    return raw.split(b"\x00", 1)[0].decode("shift_jis", errors="replace")


def _quat_to_head_euler(x: float, y: float, z: float, w: float
                        ) -> Tuple[float, float, float]:
    """Inverse of ``export_gltf._euler_quaternions`` (intrinsic yaw(Y)·pitch(X)·
    roll(Z)). Returns ``(headPitch, headYaw, headRoll)`` in degrees."""
    n = math.sqrt(x * x + y * y + z * z + w * w) or 1.0
    x, y, z, w = x / n, y / n, z / n, w / n
    sp = max(-1.0, min(1.0, 2.0 * (x * w - y * z)))     # sin(pitch) = -R[1][2]
    pitch = math.asin(sp)
    yaw = math.atan2(2.0 * (x * z + y * w), 1.0 - 2.0 * (x * x + y * y))
    roll = math.atan2(2.0 * (x * y + z * w), 1.0 - 2.0 * (x * x + z * z))
    return math.degrees(pitch), math.degrees(yaw), math.degrees(roll)


def parse_vmd(data: bytes, *, fps: Optional[float] = None,
              morph_map: Optional[Dict[str, str]] = None,
              head_pose: bool = True) -> FaceTrack:
    """Decode MikuMikuDance ``.vmd`` bytes into a :class:`FaceTrack` — the read
    side of :func:`openfacefx.export_vmd.vmd_bytes`.

    ``fps`` sets the frame→time base (default 30, MMD-native; ``time = frame/fps``).
    ``morph_map`` is the same viseme→morph table the writer takes; its unambiguous
    vowel subset is inverted and any other morph passes through as its own channel.
    ``head_pose`` harvests the 頭/首 bone quaternions into head-pose channels.
    Raises :class:`ValueError` on a non-VMD or truncated file."""
    fps = _DEFAULT_FPS if fps is None else _validate_fps(fps)
    inv = _inverse_vowel_map(DEFAULT_MORPH_MAP if morph_map is None else morph_map)

    if len(data) < _HEADER_LEN:
        raise ValueError("vmd: file too short to contain a 30-byte header")
    magic = data[:_HEADER_LEN]
    if magic.startswith(_MAGIC_V2):
        name_len = _MODEL_NAME_LEN_V2
    elif magic.startswith(_MAGIC_V1):
        name_len = _MODEL_NAME_LEN_V1
    else:
        raise ValueError("vmd: not a Vocaloid Motion Data file (bad magic)")

    end = len(data)
    off = _HEADER_LEN + name_len

    def _count(o: int, what: str) -> int:
        try:
            return struct.unpack_from("<I", data, o)[0]
        except struct.error:
            raise ValueError(f"vmd: truncated file (expected the {what} count)")

    # --- bone frames: traverse to reach the morph block, harvesting head pose ---
    bone_count = _count(off, "bone-frame")
    off += 4
    head_frames: Dict[str, List[Tuple[int, Tuple[float, float, float, float]]]] = {}
    for i in range(bone_count):
        if off + _BONE_RECORD_LEN > end:
            raise ValueError(
                f"vmd: truncated bone block ({i}/{bone_count} frames read)")
        if head_pose:
            name = _decode_name(data[off:off + _MORPH_NAME_LEN])
            if name in (_HEAD_BONE, _NECK_BONE):
                frame = struct.unpack_from("<I", data, off + _MORPH_NAME_LEN)[0]
                quat = struct.unpack_from("<ffff", data, off + _QUAT_OFFSET)
                head_frames.setdefault(name, []).append((frame, quat))
        off += _BONE_RECORD_LEN

    # --- morph frames (the payload) ---
    morph_count = _count(off, "morph-frame")
    off += 4
    chans: Dict[str, List[Keyframe]] = {}
    for i in range(morph_count):
        if off + _MORPH_RECORD_LEN > end:
            raise ValueError(
                f"vmd: truncated morph block ({i}/{morph_count} frames read)")
        name = _decode_name(data[off:off + _MORPH_NAME_LEN])
        frame, weight = struct.unpack_from("<If", data, off + _MORPH_NAME_LEN)
        off += _MORPH_RECORD_LEN
        chname = inv.get(name, name)            # vowel viseme, else passthrough
        chans.setdefault(chname, []).append(Keyframe(frame / fps, float(weight)))
    # Camera / light / self-shadow / IK sections follow but carry no facial data;
    # stopping here means an absent or truncated tail cannot break the parse.

    channels = [Channel(name=n, keys=sorted(ks, key=lambda k: k.time))
                for n, ks in chans.items()]

    if head_pose:
        src = head_frames.get(_HEAD_BONE) or head_frames.get(_NECK_BONE)
        if src:
            pit: List[Keyframe] = []
            yawk: List[Keyframe] = []
            rol: List[Keyframe] = []
            for frame, quat in sorted(src, key=lambda fq: fq[0]):
                p, y, r = _quat_to_head_euler(*quat)
                t = frame / fps
                pit.append(Keyframe(t, p))
                yawk.append(Keyframe(t, y))
                rol.append(Keyframe(t, r))
            channels += [Channel("headPitch", pit), Channel("headYaw", yawk),
                         Channel("headRoll", rol)]

    return FaceTrack(fps=fps, channels=channels)


def read_vmd(path: str, *, fps: Optional[float] = None,
             morph_map: Optional[Dict[str, str]] = None,
             head_pose: bool = True) -> FaceTrack:
    """Read a MikuMikuDance ``.vmd`` file into a :class:`FaceTrack` (see
    :func:`parse_vmd`)."""
    with open(path, "rb") as fh:
        return parse_vmd(fh.read(), fps=fps, morph_map=morph_map,
                         head_pose=head_pose)
