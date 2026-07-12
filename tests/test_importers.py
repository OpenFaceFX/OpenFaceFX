"""Cue importers (``openfacefx.importers``, issue #44) -- the inverse of
``export_cues``.

The round-trip contract, per writer:

  * **Rhubarb tsv/xml/json** round-trip *byte-identically*
    (``write -> import -> write`` reproduces the file); Rhubarb is seconds-based
    and never emits adjacent duplicate shapes.
  * The frame-based **Moho .dat** and **Papagayo .pgo** reach a byte-exact
    *idempotent fixed point* (``write(import(f)) == write(import(write(import(f))))``)
    and preserve the *collapsed* (shape, frame-boundary) cue sequence exactly. A
    byte difference on the first pass can only be a redundant duplicate switch the
    writer's frame quantisation emitted (an intermediate run dropped at the lower
    rate) -- it holds the same mouth, carries no animation, and ``dominant_cues``
    structurally cannot re-emit it.

Also pinned: monotonic / non-overlapping intervals from every format; the
imported track validates through ``io_export`` and re-exports through every track
exporter; the shape->viseme tables really invert the retarget presets; unknown
shapes error (never dropped); determinism; and the ``from-cues`` CLI.
"""

import os
import re
import sys

import pytest

try:
    import openfacefx  # noqa: F401  (installed wheel wins; see test_core)
except ImportError:
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from openfacefx.cli import main as cli_main
from openfacefx.export_cues import (dominant_cues, write_moho_dat, write_pgo,
                                     write_rhubarb_json, write_rhubarb_tsv,
                                     write_rhubarb_xml)
from openfacefx.importers import (PRESTON_BLAIR_TO_VISEME, RHUBARB_TO_VISEME,
                                  _collapse_unknown, _map_to_visemes,
                                  _validate_intervals, build_cue_track,
                                  detect_format, import_cues, parse_moho_dat,
                                  parse_pgo, parse_rhubarb_json,
                                  parse_rhubarb_tsv, parse_rhubarb_xml)
from openfacefx.io_export import from_dict, to_dict
from openfacefx.pipeline import generate_from_alignment, naive_segments
from openfacefx.retarget import PRESETS
from openfacefx.visemes import VISEMES

TEXT, DUR = "hello brave new world", 2.3

WRITERS = {
    "tsv": (write_rhubarb_tsv, ".tsv"),
    "xml": (write_rhubarb_xml, ".xml"),
    "json": (write_rhubarb_json, ".json"),
    "dat": (write_moho_dat, ".dat"),
    "pgo": (write_pgo, ".pgo"),
}


def _track(fps=60.0, text=TEXT, dur=DUR):
    return generate_from_alignment(naive_segments(text, dur), fps=fps)


def _intervals(path, tag, fps=24.0):
    """(start, end, shape) intervals straight from a cue file (no viseme map)."""
    text = open(path).read()
    if tag == "tsv":
        return parse_rhubarb_tsv(text)[0]
    if tag == "xml":
        return parse_rhubarb_xml(text)[0]
    if tag == "json":
        return parse_rhubarb_json(text)[0]
    if tag == "dat":
        return parse_moho_dat(text, fps)[0]
    return parse_pgo(text)[0]


def _collapsed(path, tag, fps=24.0):
    """(boundary, shape) sequence with adjacent duplicate shapes merged."""
    out = []
    for start, _end, shape in _intervals(path, tag, fps):
        key = round(start, 2)
        if out and out[-1][1] == shape:
            continue
        out.append((key, shape))
    return out


# --------------------------------------------------------------------------- #
# 1. per-writer round-trip                                                     #
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("tag", ["tsv", "xml", "json"])
def test_rhubarb_round_trip_byte_identical(tag, tmp_path):
    writer, ext = WRITERS[tag]
    t = _track()
    f1, f2 = str(tmp_path / ("a" + ext)), str(tmp_path / ("b" + ext))
    writer(t, f1)
    imported, _warns = import_cues(f1)
    writer(imported, f2)
    assert open(f1).read() == open(f2).read()


@pytest.mark.parametrize("tag", ["dat", "pgo"])
def test_frame_round_trip_fixed_point_and_sequence(tag, tmp_path):
    writer, ext = WRITERS[tag]
    t = _track()
    f1 = str(tmp_path / ("a" + ext))
    f2 = str(tmp_path / ("b" + ext))
    f3 = str(tmp_path / ("c" + ext))
    writer(t, f1)
    writer(import_cues(f1)[0], f2)
    writer(import_cues(f2)[0], f3)
    # byte-exact idempotent fixed point (the normalised file is a perfect inverse)
    assert open(f2).read() == open(f3).read()
    # every real cue survives -- only redundant duplicate switches are collapsed
    assert _collapsed(f1, tag) == _collapsed(f2, tag)


def test_moho_dat_rhubarb_letters_round_trip(tmp_path):
    """A .dat written with raw Rhubarb A-H/X letters (preston_blair=False)."""
    t = _track()
    f1, f2, f3 = (str(tmp_path / f"{n}.dat") for n in "abc")
    write_moho_dat(t, f1, preston_blair=False)
    write_moho_dat(import_cues(f1)[0], f2, preston_blair=False)
    write_moho_dat(import_cues(f2)[0], f3, preston_blair=False)
    assert open(f2).read() == open(f3).read()
    assert _collapsed(f1, "dat") == _collapsed(f2, "dat")


def test_round_trip_reproduces_shape_dominant_cues(tmp_path):
    """The imported track's own dominant_cues reproduce the file's shape runs
    (mapped back through the same preset), boundaries included."""
    t = _track()
    f = str(tmp_path / "a.tsv")
    write_rhubarb_tsv(t, f)
    from openfacefx.export_cues import _coerce, _RHUBARB_SHAPES
    imported, _ = import_cues(f)
    got = dominant_cues(_coerce(imported, _RHUBARB_SHAPES, "rhubarb", None), "X")
    file_ivs = _intervals(f, "tsv")
    assert [(round(s, 2), name) for s, _e, name in got] == \
           [(round(s, 2), name) for s, _e, name in file_ivs]


# --------------------------------------------------------------------------- #
# 2. monotonic, non-overlapping intervals                                      #
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("tag", ["tsv", "xml", "json", "dat", "pgo"])
def test_intervals_monotonic_non_overlapping(tag, tmp_path):
    writer, ext = WRITERS[tag]
    f = str(tmp_path / ("a" + ext))
    writer(_track(), f)
    ivs = _intervals(f, tag)
    _validate_intervals(ivs)                      # does not raise
    prev_end = None
    for start, end, _name in ivs:
        assert start <= end + 1e-9
        if prev_end is not None:
            assert abs(start - prev_end) <= 1e-9  # tiled, no gaps/overlaps
        prev_end = end


def test_validate_intervals_rejects_overlap():
    with pytest.raises(ValueError):
        _validate_intervals([(0.0, 0.5, "A"), (0.3, 0.8, "B")])


def test_parse_rejects_non_monotonic_tsv():
    with pytest.raises(ValueError):
        parse_rhubarb_tsv("0.50\tA\n0.20\tB\n1.00\tX\n")


# --------------------------------------------------------------------------- #
# 3. io_export validation + re-export through every track exporter             #
# --------------------------------------------------------------------------- #

def test_imported_track_validates_io_export(tmp_path):
    f = str(tmp_path / "a.tsv")
    write_rhubarb_tsv(_track(), f)
    imported, _ = import_cues(f)
    d = to_dict(imported)
    assert to_dict(from_dict(d)) == d
    assert imported.target_set is None            # clean native viseme vocabulary
    assert {c.name for c in imported.channels} <= set(VISEMES)


def test_imported_track_reexports_through_all_exporters(tmp_path):
    from openfacefx.export_unity import write_unity_anim
    from openfacefx.export_godot import write_godot_anim
    from openfacefx.export_live2d import write_live2d_motion
    from openfacefx.io_export import write_csv, write_json
    f = str(tmp_path / "a.dat")
    write_moho_dat(_track(), f)
    imported, _ = import_cues(f)
    for fn, ext in [(write_unity_anim, ".anim"), (write_godot_anim, ".tres"),
                    (write_live2d_motion, ".motion3.json"),
                    (write_rhubarb_json, ".json"), (write_csv, ".csv"),
                    (write_json, ".track.json")]:
        out = str(tmp_path / ("o" + ext))
        fn(imported, out)                         # must not raise
        assert os.path.getsize(out) > 0


# --------------------------------------------------------------------------- #
# 4. shape -> viseme tables, fallback, unknown shapes                          #
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("name,inv", [("rhubarb", RHUBARB_TO_VISEME),
                                      ("preston_blair", PRESTON_BLAIR_TO_VISEME)])
def test_inverse_tables_really_invert_presets(name, inv):
    preset = PRESETS[name]
    emitted = {shape for targets in preset.values() for shape, _w in targets}
    assert emitted <= set(inv)                    # every emitted shape is invertible
    for shape, viseme in inv.items():
        if shape in emitted:                      # (WQ is a documented hand-add)
            assert shape in {s for s, _w in preset[viseme]}


def test_collapse_unknown_follows_fallback():
    assert _collapse_unknown("G", {"A": "PP", "C": "E"}) == "A"   # G -> A
    assert _collapse_unknown("H", {"C": "E"}) == "C"             # H -> C
    assert _collapse_unknown("Q", RHUBARB_TO_VISEME) is None     # truly unknown


def test_unknown_shape_errors_never_dropped():
    with pytest.raises(ValueError) as ei:
        _map_to_visemes([(0.0, 1.0, "Q")], "rhubarb", [])
    assert "Q" in str(ei.value)


def test_extended_shapes_map_directly_high_fidelity():
    # G (f/v) and H (tongue-up) invert straight to FF / nn, not a lossy fallback.
    assert RHUBARB_TO_VISEME["G"] == "FF"
    assert RHUBARB_TO_VISEME["H"] == "nn"
    assert RHUBARB_TO_VISEME["X"] == "sil"


# --------------------------------------------------------------------------- #
# 5. determinism, detection, vocab, coarticulate                              #
# --------------------------------------------------------------------------- #

def test_import_is_deterministic(tmp_path):
    f = str(tmp_path / "a.pgo")
    write_pgo(_track(), f)
    a, _ = import_cues(f)
    b, _ = import_cues(f)
    assert to_dict(a) == to_dict(b)


def test_detect_format_by_extension_and_first_line(tmp_path):
    assert detect_format("x.tsv", "0.00\tA\n") == "tsv"
    assert detect_format("x.json", '{"mouthCues":[]}') == "json"
    assert detect_format("x.dat", "MohoSwitch1\n1 rest\n") == "dat"
    assert detect_format("x.pgo", "lipsync version 1\n") == "pgo"
    # first line wins over a misleading extension
    assert detect_format("mislabelled.txt", "MohoSwitch1\n1 rest\n") == "dat"
    assert detect_format("mislabelled.txt", "lipsync version 1\n") == "pgo"


def test_coarticulate_smooths_the_steps(tmp_path):
    f = str(tmp_path / "a.tsv")
    write_rhubarb_tsv(_track(), f)
    stepped, _ = import_cues(f)
    smoothed, _ = import_cues(f, coarticulate=True)
    steps = sum(len(c.keys) for c in stepped.channels)
    coart = sum(len(c.keys) for c in smoothed.channels)
    assert coart > steps                          # the dominance solve adds ramps
    assert {c.name for c in smoothed.channels} <= set(VISEMES)


def test_empty_cue_file_gives_empty_track(tmp_path):
    f = str(tmp_path / "empty.tsv")
    with open(f, "w") as fh:
        fh.write("0.00\tX\n")                     # only the terminal sentinel
    track, _ = import_cues(f)
    assert track.channels == []


# --------------------------------------------------------------------------- #
# 6. CLI                                                                        #
# --------------------------------------------------------------------------- #

def test_cli_from_cues_produces_a_track(tmp_path):
    cue = str(tmp_path / "c.tsv")
    write_rhubarb_tsv(_track(), cue)
    out = str(tmp_path / "back.json")
    assert cli_main(["from-cues", cue, "-o", out]) == 0
    from openfacefx.io_export import read_json
    back = read_json(out)
    assert {c.name for c in back.channels} <= set(VISEMES)


def test_cli_from_cues_reexports_and_retargets(tmp_path):
    cue = str(tmp_path / "c.dat")
    write_moho_dat(_track(), cue)
    # --retarget applies to JSON/CSV output (.anim carries its own naming presets)
    out = str(tmp_path / "back_arkit.json")
    assert cli_main(["from-cues", cue, "--retarget", "arkit", "-o", out]) == 0
    from openfacefx.io_export import read_json
    assert any(c.name.startswith("mouth") or c.name == "jawOpen"
               for c in read_json(out).channels)
    # and a plain .anim re-export (no retarget) works too
    anim = str(tmp_path / "back.anim")
    assert cli_main(["from-cues", cue, "-o", anim]) == 0
    assert os.path.getsize(anim) > 0


def test_cli_from_cues_unknown_shape_exits(tmp_path):
    bad = str(tmp_path / "bad.tsv")
    with open(bad, "w") as fh:
        fh.write("0.00\tQ\n0.50\tA\n1.00\tX\n")
    with pytest.raises(SystemExit):
        cli_main(["from-cues", bad, "-o", str(tmp_path / "x.json")])


# --------------------------------------------------------------------------- #
# B4: validation-path coverage for the moho/papagayo parsers + the dispatcher   #
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("call, match", [
    (lambda: parse_moho_dat("nope\n1 A\n", 30.0), "first line must be 'MohoSwitch1'"),
    (lambda: parse_moho_dat("MohoSwitch1\ngarbage here\n", 30.0),
     r"expected '<frame> <shape>'"),
    (lambda: parse_pgo("not a pgo file\n"), r"first line must be 'lipsync version"),
    (lambda: parse_rhubarb_tsv("1.0\tA\n0.5\tB\n"), "cue times must be non-decreasing"),
])
def test_b4_importer_malformed_raises(call, match):
    with pytest.raises(ValueError, match=match):
        call()


def test_b4_import_cues_unknown_format_raises(tmp_path):
    p = tmp_path / "x.txt"
    p.write_text("whatever\n")
    with pytest.raises(ValueError, match="unknown cue format 'bogus'"):
        import_cues(str(p), fmt="bogus")
