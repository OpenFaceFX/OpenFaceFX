"""Blendshape-weight CSV import (``openfacefx.importers_csv``, issue #45).

Pins the two layouts and their guarantees:

  * **OpenFaceFX long CSV** (`time,channel,value`) is the exact inverse of
    `io_export.write_csv` — `read_csv(write_csv(track))` reconstructs the same
    channels/keyframes, and re-writing is byte-identical.
  * **Wide per-frame CSV** (Apple ARKit / Epic Live Link Face) imports with the
    blendshape column names verbatim (rig-space), SMPTE-timecode or row-index
    timing converted to seconds, values clamped to `[0, 1]`, and deterministic RDP
    thinning (a hard-coded golden that must reproduce on Python 3.9/3.13).

Plus: the imported track validates through `io_export` and re-exports through
Unity/Godot/Live2D; malformed rows raise a clear `ValueError`; out-of-range
columns warn (never silently corrupt); and the `from-csv` CLI.
"""

import os
import sys

import numpy as np
import pytest

try:
    import openfacefx  # noqa: F401  (installed wheel wins; see test_core)
except ImportError:
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from openfacefx.cli import main as cli_main
from openfacefx.importers import read_csv as read_csv_reexport
from openfacefx.importers_csv import read_csv
from openfacefx.io_export import from_dict, read_json, to_dict, write_csv
from openfacefx.pipeline import generate_from_alignment, naive_segments

# A Live Link Face-style wide CSV: Timecode + BlendShapeCount metadata, then two
# ARKit blendshape columns, four frames at 60 fps.
LLF_CSV = (
    "Timecode,BlendShapeCount,jawOpen,mouthSmileLeft\n"
    "0:00:00:00,2,0.0,0.0\n"
    "0:00:00:01,2,0.5,0.1\n"
    "0:00:00:02,2,1.0,0.2\n"
    "0:00:00:03,2,0.4,0.0\n"
)

# Golden RDP output for LLF_CSV at 60 fps (MUST reproduce on 3.9 and 3.13).
LLF_GOLDEN = {
    "jawOpen": [(0.0, 0.0), (0.0333, 1.0), (0.05, 0.4)],
    "mouthSmileLeft": [(0.0, 0.0), (0.0333, 0.2), (0.05, 0.0)],
}


def _write(tmp_path, name, text):
    p = str(tmp_path / name)
    with open(p, "w", encoding="utf-8") as fh:
        fh.write(text)
    return p


def _track(text="hello brave world", dur=1.8, fps=60.0):
    return generate_from_alignment(naive_segments(text, dur), fps=fps)


def _keys(track):
    return {c.name: [(round(k.time, 4), round(k.value, 4)) for k in c.keys]
            for c in track.channels}


# --------------------------------------------------------------------------- #
# 1. long CSV round-trip (inverse of write_csv)                               #
# --------------------------------------------------------------------------- #

def test_long_round_trip_reconstructs_channels_and_keys(tmp_path):
    track = _track()
    f = _write(tmp_path, "a.csv", "")
    write_csv(track, f)
    back, warns = read_csv(f)
    assert warns == []
    assert [c.name for c in back.channels] == [c.name for c in track.channels]
    # exact to the 4 dp the CSV stores
    assert _keys(back) == _keys(track)


def test_long_round_trip_byte_identical(tmp_path):
    track = _track()
    f1 = _write(tmp_path, "a.csv", "")
    write_csv(track, f1)
    f2 = _write(tmp_path, "b.csv", "")
    write_csv(read_csv(f1)[0], f2)
    assert open(f1).read() == open(f2).read()


def test_long_values_clamped_and_reexported(tmp_path):
    f = _write(tmp_path, "long.csv",
               "time,channel,value\n0.0,jawOpen,0.0\n0.5,jawOpen,1.0\n"
               "1.0,mouthSmileLeft,0.25\n")
    track, _ = read_csv(f)
    assert {c.name for c in track.channels} == {"jawOpen", "mouthSmileLeft"}
    assert all(0.0 <= k.value <= 1.0 for c in track.channels for k in c.keys)
    assert to_dict(from_dict(to_dict(track))) == to_dict(track)


def test_read_csv_is_reexported_from_importers_and_package():
    assert read_csv_reexport is read_csv
    assert openfacefx.read_csv is read_csv


# --------------------------------------------------------------------------- #
# 2. wide per-frame CSV (ARKit / Live Link Face)                              #
# --------------------------------------------------------------------------- #

def test_wide_llf_names_timing_and_golden(tmp_path):
    f = _write(tmp_path, "llf.csv", LLF_CSV)
    track, warns = read_csv(f, fps=60.0)
    assert warns == []
    # verbatim rig-space names; BlendShapeCount metadata dropped
    assert [c.name for c in track.channels] == ["jawOpen", "mouthSmileLeft"]
    assert track.target_set == ["jawOpen", "mouthSmileLeft"]
    # frame -> seconds (frame 3 -> 3/60 = 0.05) and deterministic RDP
    assert _keys(track) == LLF_GOLDEN


def test_wide_values_in_unit_range(tmp_path):
    f = _write(tmp_path, "llf.csv", LLF_CSV)
    track, _ = read_csv(f, fps=60.0)
    assert all(0.0 <= k.value <= 1.0 for c in track.channels for k in c.keys)


def test_wide_row_index_timing_without_timecode(tmp_path):
    # no Timecode column -> row index / fps drives the timeline
    csv = "jawOpen,mouthSmileLeft\n0.0,0.0\n1.0,0.5\n0.0,0.0\n"
    f = _write(tmp_path, "plain.csv", csv)
    track, _ = read_csv(f, fps=10.0)
    jaw = next(c for c in track.channels if c.name == "jawOpen")
    assert jaw.keys[-1].time == pytest.approx(0.2)   # frame 2 at 10 fps


def test_wide_explicit_timecode_col(tmp_path):
    csv = ("frame_tc,jawOpen\n0:00:01:00,0.0\n0:00:01:30,1.0\n")
    f = _write(tmp_path, "tc.csv", csv)
    track, _ = read_csv(f, fps=60.0, timecode_col="frame_tc")
    jaw = next(c for c in track.channels if c.name == "jawOpen")
    assert jaw.keys[0].time == pytest.approx(1.0)    # 0:00:01:00 -> 1.0s
    assert jaw.keys[-1].time == pytest.approx(1.5)   # +30 frames at 60 fps


def test_wide_out_of_range_column_warns_and_clamps(tmp_path):
    csv = "Timecode,jawOpen,HeadYaw\n0:00:00:00,0.5,45.0\n0:00:00:01,0.6,-30.0\n"
    f = _write(tmp_path, "rot.csv", csv)
    track, warns = read_csv(f, fps=60.0)
    assert any("HeadYaw" in w for w in warns)
    head = next(c for c in track.channels if c.name == "HeadYaw")
    assert all(0.0 <= k.value <= 1.0 for k in head.keys)


def test_wide_reexports_through_engine_exporters(tmp_path):
    from openfacefx.export_unity import write_unity_anim
    from openfacefx.export_godot import write_godot_anim
    from openfacefx.export_live2d import write_live2d_motion
    f = _write(tmp_path, "llf.csv", LLF_CSV)
    track, _ = read_csv(f, fps=60.0)
    assert to_dict(from_dict(to_dict(track))) == to_dict(track)
    for fn, ext in [(write_unity_anim, ".anim"), (write_godot_anim, ".tres"),
                    (write_live2d_motion, ".motion3.json")]:
        out = str(tmp_path / ("o" + ext))
        fn(track, out)
        assert os.path.getsize(out) > 0


def test_wide_import_is_deterministic(tmp_path):
    f = _write(tmp_path, "llf.csv", LLF_CSV)
    a, _ = read_csv(f, fps=60.0)
    b, _ = read_csv(f, fps=60.0)
    assert to_dict(a) == to_dict(b)


# --------------------------------------------------------------------------- #
# 3. validation / malformed input                                             #
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("csv, needle", [
    ("time,channel,value\n0.0,jawOpen\n", "3 fields"),          # short long row
    ("time,channel,value\nx,jawOpen,0.5\n", "non-numeric"),     # bad long value
    ("Timecode,jawOpen\n0:00:00:00,nope\n", "non-numeric"),     # bad wide weight
    ("Timecode,jawOpen\nbadtc,0.5\n", "timecode"),              # bad timecode
    ("Timecode,jawOpen\n0:00:00:00,0.5\n0:00:00:00\n", "fields"),  # ragged row
    ("Timecode,BlendShapeCount\n0:00:00:00,2\n", "no blendshape"),  # no data cols
    ("Timecode,jawOpen,jawOpen\n0:00:00:00,0.1,0.2\n", "duplicate"),  # dup columns
])
def test_malformed_raises_clear_valueerror(tmp_path, csv, needle):
    f = _write(tmp_path, "bad.csv", csv)
    with pytest.raises(ValueError) as ei:
        read_csv(f)
    assert needle in str(ei.value)


def test_timecode_not_monotonic_rejected(tmp_path):
    csv = "Timecode,jawOpen\n0:00:00:10,0.1\n0:00:00:05,0.2\n"
    f = _write(tmp_path, "back.csv", csv)
    with pytest.raises(ValueError):
        read_csv(f, fps=60.0)


def test_empty_csv_gives_empty_track(tmp_path):
    f = _write(tmp_path, "empty.csv", "")
    track, warns = read_csv(f)
    assert track.channels == []


# --------------------------------------------------------------------------- #
# 4. CLI                                                                        #
# --------------------------------------------------------------------------- #

def test_cli_from_csv_long_round_trip(tmp_path):
    cli_main(["naive", "--text", "hello world", "--duration", "1.2",
              "-o", str(tmp_path / "base.json")])
    base = read_json(str(tmp_path / "base.json"))
    csv = str(tmp_path / "long.csv")
    write_csv(base, csv)
    out = str(tmp_path / "back.json")
    assert cli_main(["from-csv", csv, "-o", out]) == 0
    assert _keys(read_json(out)) == _keys(base)


def test_cli_from_csv_wide_to_anim(tmp_path):
    f = _write(tmp_path, "llf.csv", LLF_CSV)
    out = str(tmp_path / "llf.anim")
    assert cli_main(["from-csv", f, "--fps", "60", "-o", out]) == 0
    assert os.path.getsize(out) > 0


def test_cli_from_csv_malformed_exits(tmp_path):
    f = _write(tmp_path, "bad.csv", "time,channel,value\n0.0,jawOpen\n")
    with pytest.raises(SystemExit):
        cli_main(["from-csv", f, "-o", str(tmp_path / "x.json")])
