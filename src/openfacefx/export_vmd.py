"""Export a FaceTrack as a MikuMikuDance ``.vmd`` morph animation.

MMD (MikuMikuDance) and its ecosystem — MMM, blender_mmd_tools, three.js
``MMDLoader``, babylon-mmd, Saba, MMDAgent-EX — play facial animation from a
**Vocaloid Motion Data** (``.vmd``) file. MMD reads neither glTF nor our other
formats, so this is the one exporter that reaches the (largely Japanese) MMD /
VTuber world. Like the Bethesda ``.lip`` and glTF writers it is pure-stdlib,
deterministic and verifiable without the target app (a ``.vmd`` round-trips
through the same ``struct`` layout that wrote it).

The format is a little-endian binary blob (verified against the MMD Wiki,
blender_mmd_tools and babylon-mmd):

  * **Header** — ASCII ``Vocaloid Motion Data 0002`` NUL-padded to 30 bytes.
  * **Model name** — ShiftJIS, NUL-padded to 20 bytes (VMD v2; v1 used 10).
  * **Bone frames** — ``uint32`` count = 0 (this is a morph-only motion).
  * **Morph frames** — ``uint32`` count N, then per frame a ``char[15]`` ShiftJIS
    morph name (NUL-padded) + ``uint32`` frame number + ``float32`` weight.
  * **Trailing sections** — camera / light / self-shadow / IK-property counts,
    each an explicit ``uint32`` 0. These MUST be written even though they are
    zero, or a loader walks off the end and rejects the file (the same
    "emit default-valued trailing fields" lesson as the Godot/Live2D writers).

Every viseme keyframe becomes a morph keyframe at ``round(time * fps)`` (``fps``
defaults to 30, MMD-native, and is independent of the solver's sampling rate).
The native viseme set already *is* the Oculus/OVR set, and MMD models ship the
five Japanese vowel morphs あいうえお plus ん, so :data:`DEFAULT_MORPH_MAP` sends
the vowels there and collapses each consonant viseme onto the nearest vowel/closed
shape (the same philosophy as the five-vowel ``vrm`` retarget preset). Several
visemes may share a morph; their weights are summed and clamped exactly as
:func:`retarget` does. The map is overridable, like every other exporter's.
"""

from __future__ import annotations

import struct
from typing import Dict, List, Optional, Tuple

from .curves import FaceTrack
from .retarget import retarget

# --- VMD binary constants (little-endian) ------------------------------------
_MAGIC = b"Vocaloid Motion Data 0002"   # 25 bytes, NUL-padded to _HEADER_LEN
_HEADER_LEN = 30
_MODEL_NAME_LEN = 20                     # VMD v2 model-name field (v1 was 10)
_MORPH_NAME_LEN = 15                     # per-frame morph name field
_DEFAULT_FPS = 30.0                      # MMD's native frame rate
_DEFAULT_MODEL_NAME = "OpenFaceFX"

# Native viseme (Oculus/OVR) -> MMD Japanese lip morph. Vowels map to the five
# あいうえお morphs every MMD model exposes and ``nn`` to ん; the consonant
# visemes collapse to the nearest vowel/closed shape following the ``vrm`` preset
# (FF/TH/DD/SS -> い, kk -> あ, CH/RR -> う). A bilabial ``PP`` and ``sil`` map to
# "" — no morph, i.e. a closed/at-rest mouth. Override via ``morph_map=``.
DEFAULT_MORPH_MAP: Dict[str, str] = {
    "aa": "あ", "E": "え", "I": "い", "O": "お", "U": "う",
    "nn": "ん",
    "FF": "い", "TH": "い", "DD": "い", "SS": "い",
    "kk": "あ",
    "CH": "う", "RR": "う",
    "PP": "", "sil": "",
}


def _pad(raw: bytes, length: int, field: str) -> bytes:
    """Right-pad ``raw`` with NULs to ``length`` bytes; raise if it overflows
    (silently truncating could split a multi-byte ShiftJIS character)."""
    if len(raw) > length:
        raise ValueError(
            f"{field}: {len(raw)} bytes exceeds the {length}-byte VMD field")
    return raw + b"\x00" * (length - len(raw))


def _morph_frames(track: FaceTrack, fps: float, morph_map: Dict[str, str]
                  ) -> List[Tuple[str, int, float]]:
    """The ``(morph_name, frame_number, weight)`` triples for ``track``.

    Viseme channels are combined onto their morphs via :func:`retarget` (summed,
    clamped, on the union of key times — several visemes may share one morph), and
    each resulting keyframe is quantised to a VMD frame number. Frames are keyed by
    ``(morph, frame#)`` with the last value winning if two keys of one morph round
    to the same frame, then emitted in a deterministic ``(morph, frame#)`` order."""
    mapping = {v: [(m, 1.0)] for v, m in morph_map.items() if m}
    baked = retarget(track, mapping)
    frames: Dict[Tuple[str, int], float] = {}
    for ch in baked.channels:
        for k in ch.keys:
            # VMD frame numbers are uint32: clamp a negative (anticipatory /
            # preroll) keyframe time to frame 0, exactly as export_gltf does, so a
            # legit negative-time track doesn't crash struct.pack("<I", ...).
            fno = max(int(round(k.time * fps)), 0)
            frames[(ch.name, fno)] = float(k.value)
    return [(name, fno, w) for (name, fno), w in
            sorted(frames.items(), key=lambda kv: (kv[0][0], kv[0][1]))]


def vmd_bytes(track: FaceTrack, *, model_name: str = _DEFAULT_MODEL_NAME,
              fps: Optional[float] = None,
              morph_map: Optional[Dict[str, str]] = None) -> bytes:
    """Encode ``track`` as MikuMikuDance ``.vmd`` bytes (morph-only motion).

    ``model_name`` is embedded verbatim (ShiftJIS, ≤20 bytes). ``fps`` sets the
    frame-number quantisation (default 30, MMD-native; ``frame# = round(time*fps)``
    — independent of the track's own sampling rate). ``morph_map`` overrides the
    viseme→morph table (:data:`DEFAULT_MORPH_MAP`); a "" morph drops that viseme.
    Deterministic: constant name bytes and ``struct.pack('<I'/'<f')`` on any
    interpreter, so the bytes are identical on py3.9 and py3.13.
    """
    fps = _DEFAULT_FPS if fps is None else fps
    if not (isinstance(fps, (int, float)) and not isinstance(fps, bool)
            and 0.0 < fps < float("inf")):
        raise ValueError(f"vmd: fps must be a finite value > 0, got {fps!r}")
    morph_map = DEFAULT_MORPH_MAP if morph_map is None else morph_map
    triples = _morph_frames(track, fps, morph_map)

    out = bytearray()
    out += _pad(_MAGIC, _HEADER_LEN, "header")
    out += _pad(model_name.encode("shift_jis"), _MODEL_NAME_LEN, "model name")
    out += struct.pack("<I", 0)                       # bone-frame count (none)
    out += struct.pack("<I", len(triples))            # morph-frame count
    for name, frame_no, weight in triples:
        out += _pad(name.encode("shift_jis"), _MORPH_NAME_LEN, "morph name")
        out += struct.pack("<If", frame_no, weight)
    # Trailing sections, all empty — but their counts MUST be written or a loader
    # reads past the morph block and rejects the file: camera, light, self-shadow,
    # IK/property.
    out += struct.pack("<IIII", 0, 0, 0, 0)
    return bytes(out)


def write_vmd(track: FaceTrack, path: str, *,
              model_name: str = _DEFAULT_MODEL_NAME,
              fps: Optional[float] = None,
              morph_map: Optional[Dict[str, str]] = None) -> None:
    """Write ``track`` as a MikuMikuDance ``.vmd`` file (see :func:`vmd_bytes`)."""
    with open(path, "wb") as fh:
        fh.write(vmd_bytes(track, model_name=model_name, fps=fps,
                           morph_map=morph_map))
