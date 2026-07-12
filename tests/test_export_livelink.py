"""Apple ARKit / Epic Live Link Face wide-CSV exporter (issue #61).

The write side of importers_csv's wide branch. Verifies the canonical 61-column
header, a TRUE round-trip through our own wide-CSV importer (write → read_csv
reconstructs the channels within the RDP epsilon), exact deterministic bytes,
SMPTE timecode round-trip, and the viseme-space guard.
"""

import os
import sys

import numpy as np
import pytest

try:
    import openfacefx  # noqa: F401
except ImportError:
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from openfacefx.curves import Channel, FaceTrack, Keyframe
from openfacefx.edits import sample as _sample
from openfacefx.importers_csv import _parse_timecode, read_csv
from openfacefx.export_livelink import (LIVELINK_COLUMNS, _ARKIT_52,
                                        livelink_csv_string, write_livelink_csv)


# --- 1. the header is the canonical 61-column Live Link Face order -----------
def test_header_is_canonical_61_columns():
    assert len(LIVELINK_COLUMNS) == 61
    assert len(_ARKIT_52) == 52
    assert LIVELINK_COLUMNS[:52] == _ARKIT_52
    assert LIVELINK_COLUMNS[0] == "EyeBlinkLeft"
    assert LIVELINK_COLUMNS[17] == "JawOpen"
    assert LIVELINK_COLUMNS[51] == "TongueOut"
    assert LIVELINK_COLUMNS[52:] == ["HeadYaw", "HeadPitch", "HeadRoll",
                                     "LeftEyeYaw", "LeftEyePitch", "LeftEyeRoll",
                                     "RightEyeYaw", "RightEyePitch", "RightEyeRoll"]
    text, _ = livelink_csv_string(FaceTrack(fps=60, channels=[]))
    header = text.splitlines()[0]
    assert header == "Timecode,BlendShapeCount," + ",".join(LIVELINK_COLUMNS)


# --- 2. TRUE round-trip through our own wide-CSV importer --------------------
def test_roundtrip_through_own_importer(tmp_path):
    src = FaceTrack(fps=60, channels=[
        Channel("jawOpen", [Keyframe(0.0, 0.0), Keyframe(0.5, 1.0),
                            Keyframe(1.0, 0.2)]),
        Channel("mouthSmileLeft", [Keyframe(0.0, 0.3), Keyframe(1.0, 0.3)]),
        Channel("tongueOut", [Keyframe(0.25, 0.0), Keyframe(0.75, 0.8)]),
    ])
    p = tmp_path / "take.livelink.csv"
    matched = write_livelink_csv(src, str(p), fps=60)
    assert matched == 3                                   # all three ARKit columns

    recon, warnings = read_csv(str(p), fps=60)            # wide branch
    assert warnings == []                                 # nothing clamped
    recon_lut = {c.name.lower(): c for c in recon.channels}
    src_lut = {c.name.lower(): c for c in src.channels}
    grid = np.linspace(0.0, 1.0, 240)
    for name in ("jawopen", "mouthsmileleft", "tongueout"):
        a = _sample(src_lut[name], grid)
        b = _sample(recon_lut[name], grid)                # PascalCase col, lowered key
        assert np.max(np.abs(a - b)) <= 0.03              # within the RDP epsilon


# --- 3. exact deterministic bytes (golden, no magic hash) --------------------
def test_exact_bytes_for_a_minimal_track():
    src = FaceTrack(fps=60, channels=[Channel("JawOpen", [Keyframe(0.0, 0.5)])])
    text, matched = livelink_csv_string(src, fps=60)
    assert matched == 1
    expected_header = "Timecode,BlendShapeCount," + ",".join(LIVELINK_COLUMNS)
    expected_row = "00:00:00:00,61," + ",".join(
        "0.500000" if col == "JawOpen" else "0.000000" for col in LIVELINK_COLUMNS)
    assert text == expected_header + "\n" + expected_row + "\n"


def test_deterministic():
    src = FaceTrack(fps=60, channels=[
        Channel("jawOpen", [Keyframe(0.0, 0.1), Keyframe(0.3, 0.9)])])
    a, _ = livelink_csv_string(src, fps=60)
    b, _ = livelink_csv_string(src, fps=60)
    assert a == b


# --- 4. SMPTE timecode round-trips through the importer's parser -------------
def test_timecode_roundtrips_through_parser():
    # 91 rows at 60fps: frame 90 is 00:00:01:30 → 1.5s
    src = FaceTrack(fps=60, channels=[Channel("jawOpen",
                    [Keyframe(0.0, 0.0), Keyframe(1.5, 1.0)])])
    text, _ = livelink_csv_string(src, fps=60)
    rows = text.splitlines()[1:]
    assert len(rows) == 91
    assert rows[90].startswith("00:00:01:30,")
    assert _parse_timecode("00:00:01:30", 60.0) == pytest.approx(1.5)
    assert _parse_timecode(rows[0].split(",")[0], 60.0) == pytest.approx(0.0)


def test_timecode_at_30fps():
    text, _ = livelink_csv_string(
        FaceTrack(fps=30, channels=[Channel("jawOpen",
                  [Keyframe(0.0, 0.0), Keyframe(2.0, 1.0)])]), fps=30)
    rows = text.splitlines()[1:]
    assert rows[45].startswith("00:00:01:15,")            # frame 45 @30fps = 1.5s
    assert _parse_timecode("00:00:01:15", 30.0) == pytest.approx(1.5)


# --- 5. a still-viseme-space track populates nothing (the CLI warns) ---------
def test_viseme_track_matches_nothing():
    src = FaceTrack(fps=60, channels=[
        Channel("aa", [Keyframe(0.0, 1.0)]), Channel("PP", [Keyframe(0.5, 1.0)])])
    text, matched = livelink_csv_string(src, fps=60)
    assert matched == 0                                   # no ARKit names matched
    # every data cell is zero (viseme names aren't ARKit columns)
    for row in text.splitlines()[1:]:
        cells = row.split(",")[2:]
        assert all(float(c) == 0.0 for c in cells)


# --- 6. retarget(arkit) output feeds straight in (the intended pipeline) -----
def test_retargeted_arkit_track_populates_columns():
    from openfacefx.retarget import retarget, PRESETS
    visemes = FaceTrack(fps=60, channels=[
        Channel("aa", [Keyframe(0.0, 0.0), Keyframe(0.5, 1.0), Keyframe(1.0, 0.0)])])
    arkit = retarget(visemes, PRESETS["arkit"])           # → jawOpen etc. (camelCase)
    _, matched = livelink_csv_string(arkit, fps=60)
    assert matched >= 1                                   # jawOpen at least


# --- 7. bad fps rejected -----------------------------------------------------
@pytest.mark.parametrize("bad", [0, -5, float("inf")])
def test_invalid_fps_rejected(bad):
    with pytest.raises(ValueError, match="fps"):
        livelink_csv_string(FaceTrack(fps=60, channels=[]), fps=bad)
