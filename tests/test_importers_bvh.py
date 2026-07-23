"""Tests for the BVH head/eye-rotation importer (openfacefx.importers_bvh)."""

import numpy as np
import pytest

from openfacefx import parse_bvh, read_bvh
from openfacefx.edits import sample

# Hips (6 ch: 3 pos + Zrot Xrot Yrot) then Head (3 ch: Zrot Xrot Yrot). Head ramps
# Xrot(pitch) 0->10, Yrot(yaw) 0->-16, Zrot(roll) 0->2 over 3 frames at 30 fps.
_BVH = """HIERARCHY
ROOT Hips
{
  OFFSET 0.00 0.00 0.00
  CHANNELS 6 Xposition Yposition Zposition Zrotation Xrotation Yrotation
  JOINT Head
  {
    OFFSET 0.00 10.00 0.00
    CHANNELS 3 Zrotation Xrotation Yrotation
    End Site
    {
      OFFSET 0.00 5.00 0.00
    }
  }
}
MOTION
Frames: 3
Frame Time: 0.0333333
0 0 0 0 0 0   0 0 0
0 0 0 0 99 99   1 5 -8
0 0 0 0 99 99   2 10 -16
"""


def test_bvh_head_axes_and_fps():
    tk, warns = parse_bvh(_BVH)
    names = {c.name for c in tk.channels}
    assert names == {"headPitch", "headYaw", "headRoll"}
    assert tk.fps == 30.0
    ch = {c.name: c for c in tk.channels}
    dt = 0.0333333
    # last-frame values recovered straight through (Euler degrees, X/Y/Z -> P/Y/R)
    assert sample(ch["headPitch"], 2 * dt) == pytest.approx(10.0, abs=0.05)
    assert sample(ch["headYaw"], 2 * dt) == pytest.approx(-16.0, abs=0.05)
    assert sample(ch["headRoll"], 2 * dt) == pytest.approx(2.0, abs=0.05)
    # the Hips rotation columns (99) must NOT leak into head pose
    assert sample(ch["headPitch"], 0.0) == pytest.approx(0.0, abs=0.05)


def test_bvh_fps_override():
    tk, _ = parse_bvh(_BVH, fps=60.0)
    assert tk.fps == 60.0


def test_bvh_neck_fallback():
    bvh = _BVH.replace("JOINT Head", "JOINT Neck")
    tk, warns = parse_bvh(bvh)
    assert {c.name for c in tk.channels} == {"headPitch", "headYaw", "headRoll"}


def test_bvh_no_head_is_empty_with_warning():
    only_hips = """HIERARCHY
ROOT Hips
{
  OFFSET 0 0 0
  CHANNELS 6 Xposition Yposition Zposition Zrotation Xrotation Yrotation
}
MOTION
Frames: 2
Frame Time: 0.033
0 0 0 1 2 3
0 0 0 4 5 6
"""
    tk, warns = parse_bvh(only_hips)
    assert tk.channels == []
    assert any("no head" in w for w in warns)


def test_bvh_eyes_averaged_into_gaze():
    bvh = """HIERARCHY
ROOT Head
{
  OFFSET 0 0 0
  CHANNELS 3 Zrotation Xrotation Yrotation
  JOINT LeftEye
  {
    OFFSET 1 0 0
    CHANNELS 3 Zrotation Xrotation Yrotation
    End Site
    {
      OFFSET 0 0 1
    }
  }
  JOINT RightEye
  {
    OFFSET -1 0 0
    CHANNELS 3 Zrotation Xrotation Yrotation
    End Site
    {
      OFFSET 0 0 1
    }
  }
}
MOTION
Frames: 2
Frame Time: 0.0333333
0 0 0   0 0 10   0 0 20
0 0 0   0 4 12   0 6 24
"""
    tk, warns = parse_bvh(bvh)
    names = {c.name for c in tk.channels}
    assert "eyeYaw" in names          # Yrotation of both eyes -> averaged gaze
    ch = {c.name: c for c in tk.channels}
    # frame 0 yaw = mean(LeftEye 10, RightEye 20) = 15
    assert sample(ch["eyeYaw"], 0.0) == pytest.approx(15.0, abs=0.05)
    # frame 1 eye pitch = mean(LeftEye Xrot 4, RightEye Xrot 6) = 5
    assert sample(ch["eyePitch"], 0.0333333) == pytest.approx(5.0, abs=0.05)


def test_bvh_read_from_file(tmp_path):
    p = tmp_path / "cap.bvh"
    p.write_text(_BVH)
    tk, _ = read_bvh(str(p))
    assert {c.name for c in tk.channels} == {"headPitch", "headYaw", "headRoll"}


def test_bvh_rejects_non_bvh():
    with pytest.raises(ValueError):
        parse_bvh("this is not a bvh file")
