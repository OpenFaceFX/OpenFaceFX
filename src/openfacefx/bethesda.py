"""Bethesda FUZ container and LIP header utilities (Skyrim / Fallout 4).

What ships here is exactly what public sources verify (see
docs/COMPATIBILITY.md):

  * the .fuz voice container — FULLY specced: ``b"FUZE"`` magic, uint32
    version, uint32 lip size, embedded .lip bytes, then xWMA audio;
  * the modern .lip 12-byte header — int32 version / size / flags, with the
    flag meanings from the public FaceFXWrapper interface.

Writing a .lip *payload* is not yet possible: it is a FaceFX facial-animation
blob with no public byte-level spec — every existing generator drives
Bethesda's own Creation Kit code instead of writing bytes. Progress is
tracked in issue #12; ``lip_info`` exists to help that effort along.
"""

from __future__ import annotations

import struct
import zlib
from dataclasses import dataclass
from typing import Optional, Tuple

FUZ_MAGIC = b"FUZE"

LIP_FLAG_COMPRESSED = 1
LIP_FLAG_BIG_ENDIAN = 2
LIP_FLAG_HAS_GESTURES = 4
LIP_FLAG_VARIABLE_TARGETS = 8

# Skyrim's 16 speech targets, in engine order (the MFG morph channels).
SKYRIM_TARGETS = [
    "Aah", "BigAah", "BMP", "ChjSh", "DST", "Eee", "Eh", "FV",
    "i", "k", "N", "Oh", "OohQ", "R", "Th", "W",
]
FALLOUT4_NUM_TARGETS = 43  # names not publicly documented


@dataclass
class LipHeader:
    version: int
    size: int
    flags: int

    @property
    def compressed(self) -> bool:
        return bool(self.flags & LIP_FLAG_COMPRESSED)

    @property
    def big_endian(self) -> bool:
        return bool(self.flags & LIP_FLAG_BIG_ENDIAN)

    @property
    def has_gestures(self) -> bool:
        return bool(self.flags & LIP_FLAG_HAS_GESTURES)

    @property
    def variable_targets(self) -> bool:
        return bool(self.flags & LIP_FLAG_VARIABLE_TARGETS)


def parse_lip_header(data: bytes) -> LipHeader:
    """Parse the 12-byte header at the start of a modern .lip file."""
    if len(data) < 12:
        raise ValueError(f"lip data too short for header: {len(data)} bytes")
    version, size, flags = struct.unpack_from("<iii", data)
    return LipHeader(version, size, flags)


def lip_info(data: bytes) -> dict:
    """Diagnostic summary of a .lip blob: header fields plus whether the
    payload inflates as zlib (the compressed flag's presumed codec)."""
    hdr = parse_lip_header(data)
    payload = data[12:]
    info = {
        "version": hdr.version,
        "size": hdr.size,
        "flags": hdr.flags,
        "compressed": hdr.compressed,
        "big_endian": hdr.big_endian,
        "has_gestures": hdr.has_gestures,
        "variable_targets": hdr.variable_targets,
        "payload_bytes": len(payload),
        "zlib_inflates": False,
        "inflated_bytes": None,
    }
    if payload:
        try:
            info["inflated_bytes"] = len(zlib.decompress(payload))
            info["zlib_inflates"] = True
        except zlib.error:
            pass
    return info


def read_fuz(path: str) -> Tuple[bytes, bytes]:
    """Split a .fuz file into (lip_bytes, audio_bytes).

    ``lip_bytes`` is empty when the container carries no lip data. The audio
    is returned as stored (normally xWMA; convert externally if needed).
    """
    with open(path, "rb") as fh:
        data = fh.read()
    if data[:4] != FUZ_MAGIC:
        raise ValueError(f"not a FUZE container: magic {data[:4]!r}")
    if len(data) < 12:
        raise ValueError(f"fuz too short for header: {len(data)} bytes")
    _version, lip_size = struct.unpack_from("<II", data, 4)
    if 12 + lip_size > len(data):
        raise ValueError(f"lip size {lip_size} exceeds file ({len(data)} bytes)")
    return data[12:12 + lip_size], data[12 + lip_size:]


def write_fuz(path: str, audio: bytes, lip: Optional[bytes] = None,
              version: int = 1) -> None:
    """Write a .fuz container: audio (xWMA expected) plus optional lip data."""
    lip = lip or b""
    with open(path, "wb") as fh:
        fh.write(FUZ_MAGIC)
        fh.write(struct.pack("<II", version, len(lip)))
        fh.write(lip)
        fh.write(audio)
