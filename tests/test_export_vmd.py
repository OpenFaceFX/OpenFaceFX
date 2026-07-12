"""MikuMikuDance ``.vmd`` morph-animation exporter (issue #57).

Verifies the writer against the VMD binary spec by re-parsing its own bytes with
the same ``struct`` layout (no MMD needed), pins a golden byte-hash for
cross-version byte-identity, and checks the overridable viseme->morph map.
"""

import hashlib
import os
import struct
import sys

import pytest

try:
    import openfacefx  # noqa: F401
except ImportError:
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from openfacefx.curves import Channel, FaceTrack, Keyframe
from openfacefx.export_vmd import (DEFAULT_MORPH_MAP, _HEADER_LEN,
                                   _MODEL_NAME_LEN, _MORPH_NAME_LEN, vmd_bytes,
                                   write_vmd)


def _fixture_track():
    """A small deterministic track exercising a vowel morph, two visemes sharing
    one morph (combine), and a dropped ("" morph) channel."""
    return FaceTrack(fps=30, channels=[
        Channel("aa", [Keyframe(0.0, 0.0), Keyframe(0.5, 1.0), Keyframe(1.0, 0.0)]),
        Channel("I", [Keyframe(0.25, 0.5), Keyframe(0.75, 0.5)]),
        Channel("FF", [Keyframe(0.4, 0.8)]),
        Channel("sil", [Keyframe(0.0, 1.0)]),
    ])


def _parse_vmd(b):
    """Re-parse VMD bytes with the same struct layout the writer used. Returns
    (magic, model_name, bone_count, [(morph, frame#, weight)], trailing4) and
    asserts the whole buffer is consumed (no trailing junk / short read)."""
    magic = b[:_HEADER_LEN].split(b"\x00")[0]
    model = b[_HEADER_LEN:_HEADER_LEN + _MODEL_NAME_LEN].split(
        b"\x00")[0].decode("shift_jis")
    off = _HEADER_LEN + _MODEL_NAME_LEN
    (bone_n,) = struct.unpack_from("<I", b, off); off += 4
    (morph_n,) = struct.unpack_from("<I", b, off); off += 4
    frames = []
    for _ in range(morph_n):
        name = b[off:off + _MORPH_NAME_LEN].split(b"\x00")[0].decode("shift_jis")
        off += _MORPH_NAME_LEN
        fno, w = struct.unpack_from("<If", b, off); off += 8
        frames.append((name, fno, w))
    trailing = struct.unpack_from("<IIII", b, off); off += 16
    assert off == len(b), (off, len(b))
    return magic, model, bone_n, frames, trailing


def test_vmd_roundtrip_recovers_every_frame():
    magic, model, bone_n, frames, trailing = _parse_vmd(vmd_bytes(_fixture_track()))
    assert magic == b"Vocaloid Motion Data 0002"
    assert model == "OpenFaceFX"
    assert bone_n == 0                                   # morph-only motion
    assert trailing == (0, 0, 0, 0)                      # camera/light/shadow/prop
    # aa -> あ at frames round(t*30) = 0, 15, 30 with the input weights
    aa = [(f, round(w, 4)) for n, f, w in frames if n == "あ"]
    assert aa == [(0, 0.0), (15, 1.0), (30, 0.0)]
    # I and FF both map to い -> retarget combines them into one い morph track
    assert {n for n, _, _ in frames} == {"あ", "い"}
    assert all(0.0 <= w <= 1.0 for _, _, w in frames)


def test_vmd_golden_byte_hash():
    # Pinned for byte-identity across py3.9 / py3.13 (deterministic struct + ShiftJIS).
    b = vmd_bytes(_fixture_track())
    assert len(b) == 212
    assert hashlib.sha256(b).hexdigest() == (
        "185aabe83c1efe9f2c374effe64c4ea72e0df52e992314232754d02553315093")


def test_vmd_default_map_is_kana_and_overridable():
    assert DEFAULT_MORPH_MAP["aa"] == "あ" and DEFAULT_MORPH_MAP["nn"] == "ん"
    assert DEFAULT_MORPH_MAP["sil"] == "" and DEFAULT_MORPH_MAP["PP"] == ""
    for morph in DEFAULT_MORPH_MAP.values():
        morph.encode("shift_jis")                        # every morph is ShiftJIS
    track = FaceTrack(fps=30, channels=[Channel("aa", [Keyframe(0.0, 1.0)])])
    # a custom map renames the morph ...
    _, _, _, frames, _ = _parse_vmd(vmd_bytes(track, morph_map={"aa": "口"}))
    assert [n for n, _, _ in frames] == ["口"]
    # ... and a "" morph drops the viseme entirely
    _, _, _, dropped, _ = _parse_vmd(vmd_bytes(track, morph_map={"aa": ""}))
    assert dropped == []


def test_vmd_fps_quantizes_frame_numbers():
    track = FaceTrack(fps=60, channels=[Channel("aa", [Keyframe(1.0, 1.0)])])
    _, _, _, at30, _ = _parse_vmd(vmd_bytes(track))          # default 30 fps
    _, _, _, at60, _ = _parse_vmd(vmd_bytes(track, fps=60.0))
    assert at30[0][1] == 30 and at60[0][1] == 60            # round(1.0 * fps)


def test_vmd_model_name_overflow_raises():
    track = FaceTrack(fps=30, channels=[Channel("aa", [Keyframe(0.0, 1.0)])])
    with pytest.raises(ValueError, match="model name"):
        vmd_bytes(track, model_name="x" * 21)              # > 20-byte field


def test_write_vmd_file(tmp_path):
    p = tmp_path / "m.vmd"
    write_vmd(_fixture_track(), str(p))
    assert p.read_bytes() == vmd_bytes(_fixture_track())
