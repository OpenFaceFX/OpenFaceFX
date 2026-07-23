"""glTF 2.0 morph-target animation **importer** — read weight animation back
into a :class:`~openfacefx.curves.FaceTrack`.

Closes the round-trip with :mod:`openfacefx.export_gltf` (we could write glTF but
not read it), and is the feasible **headless** path for FBX: convert ``FBX ->
glTF`` (FBX2glTF / Blender) then read it here, since a pure-Python binary-FBX
parser is version-fragile.

It reads the animation channel whose ``target.path == "weights"``: the sampler's
``input`` accessor is the time grid and its ``output`` accessor is
``n_frames * N`` morph weights (frame-major — the convention
:mod:`export_gltf` writes). The ``N`` target names come from the node's mesh
``extras.targetNames`` (the de-facto glТF convention). The ``(times, weights)``
matrix is reduced to a track via :func:`~openfacefx.curves.reduce_to_track`,
exactly like the CSV/VMD importers. Signed head/eye **pose** (rotation) channels
are not imported — glTF morph weights are the ``[0, 1]`` blendshape model.

Handles both containers (``.glb`` binary + ``.gltf`` JSON with base64/external
buffers), the standard accessor component types (float + normalized ints) and
``CUBICSPLINE`` samplers (keeps the value, drops tangents). Pure stdlib + numpy.
"""

from __future__ import annotations

import base64
import json
import os
import struct
from types import SimpleNamespace
from typing import List, Optional, Tuple

import numpy as np

from .curves import FaceTrack, reduce_to_track

_GLB_MAGIC = 0x46546C67          # "glTF"
_CHUNK_JSON = 0x4E4F534A         # "JSON"
_CHUNK_BIN = 0x004E4942          # "BIN\0"
_CT_DTYPE = {5120: "<i1", 5121: "<u1", 5122: "<i2", 5123: "<u2", 5125: "<u4", 5126: "<f4"}
_CT_NORM_MAX = {5120: 127.0, 5121: 255.0, 5122: 32767.0, 5123: 65535.0}
_NCOMP = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4, "MAT2": 4, "MAT3": 9, "MAT4": 16}


def _load(path: str) -> Tuple[dict, List[bytes]]:
    """Parse a .glb (binary) or .gltf (JSON) into (gltf-dict, [buffer-bytes])."""
    with open(path, "rb") as fh:
        data = fh.read()
    if data[:4] == struct.pack("<I", _GLB_MAGIC):
        off, gltf, bin_blob = 12, None, b""
        while off + 8 <= len(data):
            clen, ctype = struct.unpack_from("<II", data, off)
            chunk = data[off + 8:off + 8 + clen]
            off += 8 + clen
            if ctype == _CHUNK_JSON:
                gltf = json.loads(chunk.decode("utf-8"))
            elif ctype == _CHUNK_BIN:
                bin_blob = chunk
        if gltf is None:
            raise ValueError("glb has no JSON chunk")
        return gltf, [bin_blob]
    gltf = json.loads(data.decode("utf-8"))
    base = os.path.dirname(os.path.abspath(path))
    buffers: List[bytes] = []
    for b in gltf.get("buffers", []):
        uri = b.get("uri")
        if uri is None:
            buffers.append(b"")
        elif uri.startswith("data:"):
            buffers.append(base64.b64decode(uri.split(",", 1)[1]))
        else:
            with open(os.path.join(base, uri), "rb") as f:
                buffers.append(f.read())
    return gltf, buffers


def _accessor(gltf: dict, buffers: List[bytes], idx: int) -> np.ndarray:
    """Decode accessor ``idx`` to a float64 array (SCALAR -> 1-D, else 2-D)."""
    acc = gltf["accessors"][idx]
    bv = gltf["bufferViews"][acc["bufferView"]]
    buf = buffers[bv.get("buffer", 0)]
    start = bv.get("byteOffset", 0) + acc.get("byteOffset", 0)
    ct = acc["componentType"]
    ncomp = _NCOMP[acc["type"]]
    count = acc["count"]
    dt = np.dtype(_CT_DTYPE[ct])
    arr = np.frombuffer(buf, dtype=dt, count=count * ncomp, offset=start).astype(np.float64)
    if acc.get("normalized") and ct in _CT_NORM_MAX:
        arr = arr / _CT_NORM_MAX[ct]
    return arr.reshape(count, ncomp) if ncomp > 1 else arr


def read_gltf(path: str, *, fps: Optional[float] = None,
              epsilon: float = 0.015) -> Tuple[FaceTrack, List[str]]:
    """Read morph-weight animation from a ``.glb``/``.gltf`` file.

    Returns ``(track, warnings)``. ``fps`` overrides the rate inferred from the
    sampler times. Empty track (no error) when the file has no weight animation.
    """
    warnings: List[str] = []
    gltf, buffers = _load(path)

    chosen = None
    for anim in gltf.get("animations", []):
        for ch in anim.get("channels", []):
            if ch.get("target", {}).get("path") == "weights":
                chosen = (anim, ch)
                break
        if chosen:
            break
    if chosen is None:
        return (FaceTrack(fps=fps or 60.0, channels=[], target_set=None),
                ["no morph-weight animation found in the glTF"])

    anim, ch = chosen
    samp = anim["samplers"][ch["sampler"]]
    times = _accessor(gltf, buffers, samp["input"]).reshape(-1)
    out = _accessor(gltf, buffers, samp["output"]).reshape(-1)
    nt = len(times)

    # target names + count from the animated node's mesh
    names, N = None, None
    node = ch["target"].get("node")
    nodes = gltf.get("nodes", [])
    if node is not None and node < len(nodes):
        mesh_i = nodes[node].get("mesh")
        if mesh_i is not None and mesh_i < len(gltf.get("meshes", [])):
            mesh = gltf["meshes"][mesh_i]
            prim = (mesh.get("primitives") or [{}])[0]
            names = ((prim.get("extras") or {}).get("targetNames")
                     or (mesh.get("extras") or {}).get("targetNames"))
            N = len(mesh.get("weights", []) or (prim.get("targets") or []) or [])

    interp = samp.get("interpolation", "LINEAR")
    if interp == "CUBICSPLINE" and N and nt and len(out) == 3 * nt * N:
        out = out.reshape(nt, 3, N)[:, 1, :].reshape(-1)   # keep value, drop tangents
        warnings.append("CUBICSPLINE sampler: kept the keyframe value (tangents dropped)")

    if not N and nt:
        N = len(out) // nt
    if not N:
        return (FaceTrack(fps=fps or 60.0, channels=[], target_set=None),
                warnings + ["could not determine the morph-target count"])

    matrix = np.clip(out.reshape(nt, N), 0.0, 1.0)
    if not names or len(names) != N:
        if names and len(names) != N:
            warnings.append(f"targetNames ({len(names)}) != morph count ({N}); using indices")
        names = [f"morph_{j}" for j in range(N)]

    if fps is None:
        fps = round((nt - 1) / times[-1], 3) if nt > 1 and times[-1] > 0 else 60.0
    targets = [SimpleNamespace(name=nm, lo=0.0, hi=1.0) for nm in names]
    track = reduce_to_track(np.asarray(times, dtype=np.float64), matrix,
                            fps=float(fps), epsilon=epsilon, targets=targets)
    return track, warnings
