"""glTF 2.0 morph-target animation exporter -- the vendor-neutral 3D asset.

Every other 3D exporter here is engine-specific (Unity ``.anim``, Godot ``.tres``,
Live2D). glTF 2.0 is the ISO/IEC 12113 runtime interchange standard, imported by
Blender / Three.js / Babylon / Godot / Unity / Unreal, and the base of **VRM** --
and its animation natively drives **morph-target weights**
(``animation.channel.target.path = "weights"``), which is exactly the ``[0, 1]``
viseme/blendshape model. This writes one self-contained file any glTF consumer can
play:

  * ``.gltf`` -- JSON with the binary buffer base64-embedded as a ``data:`` URI.
  * ``.glb``  -- the binary container: a 12-byte header + a JSON chunk (space-
    padded to 4 bytes) + a BIN chunk (zero-padded), via stdlib ``struct``/``base64``.

The asset is a stub ``mesh`` declaring N morph targets named after the track's
``[0, 1]`` weight channels (``mesh.extras.targetNames`` -- the de-facto convention
a consumer remaps by), a ``node`` referencing it, and one ``animation`` whose
LINEAR sampler drives that node's ``weights`` path. Accessors are packed as
little-endian ``FLOAT`` (componentType 5126): a shared ``input`` accessor (the
per-frame time grid, strictly increasing, with ``min``/``max``) and a frame-major
``output`` of ``n_frames * N`` weights, densified from the sparse channels with
``np.interp`` (via :func:`openfacefx.edits.sample`).

Only ``[0, 1]`` weight channels become morph weights; the signed head/eye **pose**
channels (:data:`openfacefx.inspect.POSE_CHANNELS`, degrees) are excluded by
default -- an opt-in ``head_node`` encodes ``headPitch/Yaw/Roll`` as a separate
node ``rotation`` (Euler→quaternion) sampler.

**Verification.** The Khronos glTF Validator is the documented external gate; it
cannot run in this environment, so the asset is built strictly to the glTF 2.0
spec (so it would pass) and the in-repo proof is a full accessor **round-trip**
(decode the LE float32 buffers, reconstruct every weight channel within 1e-6).
numpy + stdlib only, deterministic bytes on Python 3.9/3.13.
"""

from __future__ import annotations

import base64
import json
import struct
from typing import Dict, List, Tuple

import numpy as np

from .curves import FaceTrack
from .edits import sample
from .inspect import POSE_CHANNELS

_GLB_MAGIC = 0x46546C67      # "glTF"
_CHUNK_JSON = 0x4E4F534A     # "JSON"
_CHUNK_BIN = 0x004E4942      # "BIN\0"
_FLOAT = 5126                # glTF componentType FLOAT


def _add_accessor(parts: List[bytes], bufferviews: List[Dict],
                  accessors: List[Dict], arr: np.ndarray, ncomp: int,
                  gltf_type: str) -> int:
    """Pack ``arr`` as a little-endian FLOAT accessor + bufferView, appending to
    the running ``parts``/``bufferviews``/``accessors`` lists and returning the new
    accessor index. All accessors here are float32, so byte offsets stay 4-aligned.

    Shared by :func:`build_gltf` and :mod:`openfacefx.export_vrma` (the VRM
    animation exporter reuses this exact packer, GLB writer and ``data:`` URI path)
    so both produce identical, spec-conformant accessor blocks."""
    arr = np.ascontiguousarray(arr, dtype=np.float32)
    blob = arr.tobytes()                                   # LE float32
    offset = sum(len(p) for p in parts)                    # 4-aligned (all float32)
    bufferviews.append({"buffer": 0, "byteOffset": offset,
                        "byteLength": len(blob)})
    flat = arr.reshape(-1, ncomp)
    accessors.append({
        "bufferView": len(bufferviews) - 1,
        "componentType": _FLOAT,
        "count": int(flat.shape[0]),
        "type": gltf_type,
        "min": [float(v) for v in flat.min(axis=0)],
        "max": [float(v) for v in flat.max(axis=0)],
    })
    parts.append(blob)
    return len(accessors) - 1


def _weight_channels(track: FaceTrack):
    """The ``[0, 1]`` morph-weight channels, in track order (pose excluded)."""
    return [c for c in track.channels if c.name not in POSE_CHANNELS]


def _grid(track: FaceTrack) -> np.ndarray:
    """The per-frame sampler time grid ``[0 .. duration]`` at the track fps."""
    dur = track.duration
    fps = float(track.fps) or 60.0
    n = max(0, int(round(dur * fps)))
    return np.array([i / fps for i in range(n + 1)], dtype=np.float64)


def build_gltf(track: FaceTrack, *, head_node: bool = False
               ) -> Tuple[Dict, bytes]:
    """Build the glTF JSON dict (buffer without a URI) and the packed BIN bytes.

    :func:`write_gltf` embeds the bytes as a ``data:`` URI (``.gltf``) or a BIN
    chunk (``.glb``)."""
    wch = _weight_channels(track)
    names = [c.name for c in wch]
    n = len(wch)
    grid = _grid(track)
    times = grid.astype(np.float32)
    nt = len(times)

    weights = np.zeros((nt, n), dtype=np.float32)          # frame-major
    for j, c in enumerate(wch):
        weights[:, j] = np.clip(sample(c, grid), 0.0, 1.0)

    parts: List[bytes] = []
    bufferviews: List[Dict] = []
    accessors: List[Dict] = []

    def add(arr: np.ndarray, ncomp: int, gltf_type: str) -> int:
        return _add_accessor(parts, bufferviews, accessors, arr, ncomp, gltf_type)

    a_pos = add(np.zeros((1, 3), np.float32), 3, "VEC3")   # stub base POSITION
    a_delta = add(np.zeros((1, 3), np.float32), 3, "VEC3")  # shared zero morph delta
    a_time = add(times, 1, "SCALAR")                       # sampler input (times)
    a_wt = add(weights.reshape(-1), 1, "SCALAR")           # sampler output (weights)

    gltf: Dict = {
        "asset": {"version": "2.0", "generator": "openfacefx"},
        "scene": 0,
        "scenes": [{"nodes": [0]}],
        "nodes": [{"mesh": 0, "weights": [0.0] * n, "name": "face"}],
        "meshes": [{
            "primitives": [{"attributes": {"POSITION": a_pos},
                            "targets": [{"POSITION": a_delta} for _ in range(n)]}],
            "weights": [0.0] * n,
            "extras": {"targetNames": names},
        }],
        "animations": [{
            "samplers": [{"input": a_time, "output": a_wt,
                          "interpolation": "LINEAR"}],
            "channels": [{"sampler": 0,
                          "target": {"node": 0, "path": "weights"}}],
        }],
        "accessors": accessors,
        "bufferViews": bufferviews,
        "buffers": [],
    }

    if head_node:
        a_rot = add(_euler_quaternions(track, grid), 4, "VEC4")
        gltf["nodes"].append({"name": "head", "rotation": [0.0, 0.0, 0.0, 1.0]})
        head = len(gltf["nodes"]) - 1
        gltf["scenes"][0]["nodes"].append(head)
        anim = gltf["animations"][0]
        anim["samplers"].append({"input": a_time, "output": a_rot,
                                 "interpolation": "LINEAR"})
        anim["channels"].append({"sampler": len(anim["samplers"]) - 1,
                                 "target": {"node": head, "path": "rotation"}})

    return gltf, b"".join(parts)


def _euler_quaternions(track: FaceTrack, grid: np.ndarray) -> np.ndarray:
    """Per-frame ``(x, y, z, w)`` unit quaternions from the head pose channels
    (degrees), composed intrinsic yaw(Y)·pitch(X)·roll(Z)."""
    chans = {c.name: c for c in track.channels}

    def rad(name):
        c = chans.get(name)
        return np.radians(sample(c, grid)) if c is not None else np.zeros(len(grid))

    def axis_quat(angle, ax):
        h = angle / 2.0
        q = np.zeros((len(angle), 4), dtype=np.float64)
        q[:, ax] = np.sin(h)
        q[:, 3] = np.cos(h)
        return q

    q = _qmul(_qmul(axis_quat(rad("headYaw"), 1), axis_quat(rad("headPitch"), 0)),
              axis_quat(rad("headRoll"), 2))
    norm = np.linalg.norm(q, axis=1, keepdims=True)
    return (q / np.where(norm == 0, 1.0, norm)).astype(np.float32)


def _qmul(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    ax, ay, az, aw = a[:, 0], a[:, 1], a[:, 2], a[:, 3]
    bx, by, bz, bw = b[:, 0], b[:, 1], b[:, 2], b[:, 3]
    return np.stack([
        aw * bx + ax * bw + ay * bz - az * by,
        aw * by - ax * bz + ay * bw + az * bx,
        aw * bz + ax * by - ay * bx + az * bw,
        aw * bw - ax * bx - ay * by - az * bz,
    ], axis=1)


def write_gltf(track: FaceTrack, path: str, *, head_node: bool = False) -> None:
    """Write ``track`` as glTF 2.0; ``.glb`` picks the binary container, anything
    else the JSON form with a base64 ``data:`` buffer."""
    gltf, blob = build_gltf(track, head_node=head_node)
    if path.endswith(".glb"):
        _write_glb(gltf, blob, path)
        return
    gltf["buffers"] = [{
        "byteLength": len(blob),
        "uri": "data:application/octet-stream;base64," +
               base64.b64encode(blob).decode("ascii"),
    }]
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(gltf, fh, indent=2)


def _write_glb(gltf: Dict, blob: bytes, path: str) -> None:
    gltf["buffers"] = [{"byteLength": len(blob)}]
    json_bytes = json.dumps(gltf, separators=(",", ":")).encode("utf-8")
    json_chunk = json_bytes + b" " * (-len(json_bytes) % 4)   # pad with SPACES
    bin_chunk = blob + b"\x00" * (-len(blob) % 4)              # pad with ZEROS
    total = 12 + 8 + len(json_chunk) + 8 + len(bin_chunk)
    with open(path, "wb") as fh:
        fh.write(struct.pack("<III", _GLB_MAGIC, 2, total))
        fh.write(struct.pack("<II", len(json_chunk), _CHUNK_JSON))
        fh.write(json_chunk)
        fh.write(struct.pack("<II", len(bin_chunk), _CHUNK_BIN))
        fh.write(bin_chunk)
