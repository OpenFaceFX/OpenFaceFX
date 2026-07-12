"""MikuMikuDance ``.vmd`` morph-animation importer (issue #60).

The read side of ``export_vmd``. Verifies a TRUE inverse round-trip against the
shipping writer (no MMD needed), passthrough of unknown morphs (never dropped),
robust traversal of a foreign file with a populated bone block + camera/light/IK
tail, and the head-pose quaternion decomposition against ``export_gltf``'s own
forward map.
"""

import math
import os
import struct
import sys

import numpy as np
import pytest

try:
    import openfacefx  # noqa: F401
except ImportError:
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from openfacefx.curves import Channel, FaceTrack, Keyframe
from openfacefx.export_vmd import _HEADER_LEN, _MORPH_NAME_LEN, vmd_bytes, write_vmd
from openfacefx.export_gltf import _euler_quaternions
from openfacefx.importers_vmd import (_MAGIC_V2, _quat_to_head_euler, parse_vmd,
                                      read_vmd)

FPS = 30.0


def _sjis15(name):
    """A NUL-padded 15-byte ShiftJIS morph/bone name field."""
    raw = name.encode("shift_jis")
    return raw + b"\x00" * (_MORPH_NAME_LEN - len(raw))


# --- 1. true inverse round-trip against the writer ---------------------------
def test_roundtrip_vowels_recovers_channels():
    """A vowel-only track (the 1:1-invertible subset) survives vmd_bytes →
    parse_vmd: same channels, frame-aligned times exact, weights within f32."""
    # frames 0,5,10 → times 0.0, 5/30, 10/30 are exact under round(t*fps).
    times = [0.0, 5 / FPS, 10 / FPS]
    src = FaceTrack(fps=FPS, channels=[
        Channel("aa", [Keyframe(times[0], 0.0), Keyframe(times[1], 1.0)]),
        Channel("I",  [Keyframe(times[1], 0.5), Keyframe(times[2], 0.25)]),
        Channel("U",  [Keyframe(times[0], 0.8)]),
        Channel("E",  [Keyframe(times[2], 0.6)]),
        Channel("O",  [Keyframe(times[1], 0.3)]),
        Channel("nn", [Keyframe(times[2], 0.9)]),
    ])
    got = parse_vmd(vmd_bytes(src), fps=FPS)
    got_ch = {c.name: c for c in got.channels}
    assert set(got_ch) == {"aa", "I", "U", "E", "O", "nn"}
    for c in src.channels:
        recovered = {round(k.time, 9): k.value for k in got_ch[c.name].keys}
        for k in c.keys:
            assert round(k.time, 9) in recovered
            assert recovered[round(k.time, 9)] == pytest.approx(k.value, abs=1e-6)


def test_roundtrip_ignores_dropped_and_collapses_consonants():
    """PP/sil ("" morphs) vanish and consonants that share い are not mis-inverted
    back onto their own names — they surface under the vowel channel い maps to."""
    src = FaceTrack(fps=FPS, channels=[
        Channel("aa", [Keyframe(0.0, 0.7)]),
        Channel("FF", [Keyframe(0.0, 0.4)]),   # → い morph on write
        Channel("sil", [Keyframe(0.0, 1.0)]),  # → "" dropped on write
    ])
    got = {c.name: c for c in parse_vmd(vmd_bytes(src), fps=FPS).channels}
    assert "sil" not in got and "PP" not in got   # dropped morph never written
    assert "FF" not in got                        # can't un-collapse a consonant
    assert "aa" in got and got["aa"].keys[0].value == pytest.approx(0.7, abs=1e-6)
    assert "I" in got                             # い inverts to the vowel I


# --- 2. passthrough: unknown morphs become channels, never dropped -----------
def test_unknown_morph_passes_through():
    """A morph name outside the vowel set (e.g. a blink) is preserved as its own
    channel rather than silently dropped."""
    body = bytearray()
    body += _MAGIC_V2 + b"\x00" * (_HEADER_LEN - len(_MAGIC_V2))
    body += b"m" + b"\x00" * 19                    # 20-byte v2 model name
    body += struct.pack("<I", 0)                   # no bone frames
    body += struct.pack("<I", 2)                   # two morph frames
    body += _sjis15("まばたき") + struct.pack("<If", 3, 1.0)   # blink (unknown)
    body += _sjis15("あ") + struct.pack("<If", 0, 0.5)         # vowel → aa
    got = {c.name: c for c in parse_vmd(bytes(body), fps=FPS).channels}
    assert "まばたき" in got                        # reported, not dropped
    assert got["まばたき"].keys[0].value == pytest.approx(1.0, abs=1e-6)
    assert "aa" in got


# --- 3. robust traversal of a foreign file with bones + a populated tail ------
def _build_foreign_vmd(head_quat):
    """A hand-built v2 .vmd: one 頭 bone frame, one あ morph frame, then populated
    camera / light / self-shadow / IK sections that a facial reader must ignore."""
    b = bytearray()
    b += _MAGIC_V2 + b"\x00" * (_HEADER_LEN - len(_MAGIC_V2))
    b += "テスト".encode("shift_jis").ljust(20, b"\x00")
    # bone block: 1 frame for 頭 (name15 + frame u32 + pos 3f + quat 4f + interp 64B)
    b += struct.pack("<I", 1)
    b += _sjis15("頭") + struct.pack("<I", 6)
    b += struct.pack("<fff", 0.0, 0.0, 0.0)
    b += struct.pack("<ffff", *head_quat)
    b += b"\x00" * 64
    # morph block: 1 frame
    b += struct.pack("<I", 1)
    b += _sjis15("あ") + struct.pack("<If", 0, 0.42)
    # populated trailing sections the reader must not choke on
    b += struct.pack("<I", 1) + b"\x00" * 61       # camera: 1 × 61B
    b += struct.pack("<I", 1) + b"\x00" * 28       # light: 1 × 28B
    b += struct.pack("<I", 1) + b"\x00" * 9        # self-shadow: 1 × 9B
    b += struct.pack("<I", 0)                      # IK/property: 0
    return bytes(b)


def test_foreign_file_with_bones_and_tail():
    track = parse_vmd(_build_foreign_vmd((0.0, 0.0, 0.0, 1.0)))   # identity quat
    ch = {c.name: c for c in track.channels}
    assert ch["aa"].keys[0].value == pytest.approx(0.42, abs=1e-6)   # morph survived
    # identity head quaternion → ~0 head angles, present as channels
    assert {"headPitch", "headYaw", "headRoll"} <= set(ch)
    for name in ("headPitch", "headYaw", "headRoll"):
        assert ch[name].keys[0].value == pytest.approx(0.0, abs=1e-4)
        assert ch[name].keys[0].time == pytest.approx(6 / FPS)


def test_head_pose_can_be_disabled():
    track = parse_vmd(_build_foreign_vmd((0.0, 0.0, 0.0, 1.0)), head_pose=False)
    names = {c.name for c in track.channels}
    assert "aa" in names and not (names & {"headPitch", "headYaw", "headRoll"})


# --- 4. head-pose decomposition is the exact inverse of the forward map -------
@pytest.mark.parametrize("pitch,yaw,roll", [
    (0.0, 0.0, 0.0), (12.0, 0.0, 0.0), (0.0, -20.0, 0.0), (0.0, 0.0, 8.0),
    (10.0, 15.0, -7.0), (-25.0, 5.0, 3.0),
])
def test_quat_to_head_euler_inverts_euler_quaternions(pitch, yaw, roll):
    grid = np.array([0.0])
    track = FaceTrack(fps=FPS, channels=[
        Channel("headPitch", [Keyframe(0.0, pitch)]),
        Channel("headYaw", [Keyframe(0.0, yaw)]),
        Channel("headRoll", [Keyframe(0.0, roll)]),
    ])
    qx, qy, qz, qw = [float(v) for v in _euler_quaternions(track, grid)[0]]
    p, y, r = _quat_to_head_euler(qx, qy, qz, qw)
    assert p == pytest.approx(pitch, abs=1e-3)
    assert y == pytest.approx(yaw, abs=1e-3)
    assert r == pytest.approx(roll, abs=1e-3)


# --- 5. malformed input raises a clear ValueError ----------------------------
def test_bad_magic_rejected():
    with pytest.raises(ValueError, match="not a Vocaloid"):
        parse_vmd(b"not a vmd file at all, but long enough header........")


def test_truncated_file_rejected():
    # 4 morph frames (4×23 B) so lopping 30 bytes lands inside the morph block,
    # not merely in the ignorable 16-byte trailing section.
    good = vmd_bytes(FaceTrack(fps=FPS, channels=[
        Channel("aa", [Keyframe(i / FPS, 0.5) for i in range(4)])]))
    with pytest.raises(ValueError, match="truncated|too short"):
        parse_vmd(good[:-30])                      # chop into the morph block


def test_invalid_fps_rejected():
    with pytest.raises(ValueError, match="fps"):
        parse_vmd(vmd_bytes(FaceTrack(fps=FPS, channels=[])), fps=0)


# --- 6. read_vmd file wrapper + determinism ----------------------------------
def test_read_vmd_file_roundtrip(tmp_path):
    src = FaceTrack(fps=FPS, channels=[Channel("O", [Keyframe(0.0, 0.5),
                                                     Keyframe(9 / FPS, 0.9)])])
    p = tmp_path / "pose.vmd"
    write_vmd(src, str(p))
    got = {c.name: c for c in read_vmd(str(p)).channels}
    assert "O" in got and len(got["O"].keys) == 2


def test_parse_is_deterministic():
    data = vmd_bytes(FaceTrack(fps=FPS, channels=[
        Channel("aa", [Keyframe(0.0, 0.3), Keyframe(5 / FPS, 0.7)])]))
    a = parse_vmd(data)
    b = parse_vmd(data)
    dump = lambda t: [(c.name, [(k.time, k.value) for k in c.keys]) for c in t.channels]
    assert dump(a) == dump(b)
