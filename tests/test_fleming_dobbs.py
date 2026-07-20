"""Fleming-Dobbs mouth-shape retarget preset (#71).

A sibling of the preston_blair/rhubarb presets for the Papagayo-NG / traditional-
animation audience, re-expressed from the published Fleming & Dobbs convention (a
functional fact) — not copied from Papagayo-NG's GPLv3 data. Tests: the preset
covers every viseme and emits only FD shapes, the derived inverse table round-trips
and imports an FD-labelled timeline, and it auto-wires to --retarget.
"""

import json
import os
import sys

import pytest

try:
    import openfacefx  # noqa: F401
except ImportError:
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from openfacefx.cli import main as cli_main
from openfacefx.importers import FLEMING_DOBBS_TO_VISEME, build_cue_track
from openfacefx.io_export import from_dict, to_dict
from openfacefx.pipeline import generate_from_alignment, naive_segments
from openfacefx.retarget import PRESETS, retarget
from openfacefx.visemes import VISEMES

FD_SHAPES = {"rest", "MBP", "FV", "TH", "NLTDR", "GK", "SH", "EHSZ", "AA", "IY", "O"}


def test_preset_registered_and_covers_all_visemes():
    assert "fleming_dobbs" in PRESETS
    assert set(PRESETS["fleming_dobbs"]) == set(VISEMES)   # no viseme dropped


def test_retarget_emits_only_fd_shapes():
    src = from_dict(to_dict(generate_from_alignment(
        naive_segments("hello brave new world", 2.3), fps=60.0)))
    out = retarget(src, PRESETS["fleming_dobbs"])
    assert out.channels
    assert all(c.name in FD_SHAPES for c in out.channels)


def test_inverse_table_covers_every_emitted_shape_and_round_trips():
    forward = {sh for tgts in PRESETS["fleming_dobbs"].values() for sh, _ in tgts}
    assert set(FLEMING_DOBBS_TO_VISEME) == forward        # every shape invertible
    # each shape's representative viseme retargets straight back to that shape
    for shape, viseme in FLEMING_DOBBS_TO_VISEME.items():
        assert PRESETS["fleming_dobbs"][viseme][0][0] == shape


def test_import_fd_timeline_via_inverse_table():
    fd = [(0.0, 0.5, "MBP"), (0.5, 1.0, "AA"), (1.0, 1.5, "O")]
    vis = [(s, e, FLEMING_DOBBS_TO_VISEME[sh]) for s, e, sh in fd]
    assert [v for _, _, v in vis] == ["PP", "aa", "O"]
    track = build_cue_track(vis, fps=60.0)
    assert {c.name for c in track.channels} >= {"PP", "aa", "O"}


def test_cli_retarget_fleming_dobbs(tmp_path):
    out = str(tmp_path / "fd.json")
    assert cli_main(["naive", "--text", "hello brave world", "--duration", "2.0",
                     "--retarget", "fleming_dobbs", "-o", out]) == 0
    track = from_dict(json.load(open(out)))
    assert track.channels
    assert all(c.name in FD_SHAPES for c in track.channels)
