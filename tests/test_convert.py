"""`convert` command (issue #46): re-export an existing track.json to any format
without re-running the solver.

The guarantee is **byte-identical-to-generate by construction** — `convert` loads
a track and routes it through the *exact same* `_apply_edits_layer` → `_write`
path the generate commands use, so no behaviour can drift. These tests assert
that equivalence for every exporter and every passthrough transform
(`--retarget`/`--adjust`/`--retarget-shapes`/`--edits`), the native round-trip,
and the shared `.lip` guard.

One precision note pinned below: the `openfacefx.track` JSON stores keyframe
*times* at 4 dp (`io_export.to_dict` rounds them). So an exporter that renders
finer time precision (Unity `.anim` at 6 dp, Godot/Live2D) reflects that 4 dp
quantisation when the track's times are not 4-dp-representable — the difference is
the *format's* time storage, not `convert`. Generating at an fps whose frame
times are 4-dp-clean (e.g. 100) removes it, and then `convert` is byte-identical
for **every** exporter; at the default fps 60 it is byte-identical for every
exporter that quantises time to ≤ 4 dp (CSV, cues, the native track JSON).
"""

import os
import sys

import pytest

try:
    import openfacefx  # noqa: F401  (installed wheel wins; see test_core)
except ImportError:
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from openfacefx.cli import main as cli_main
from openfacefx.io_export import read_json

TEXT, DUR = "hello brave new world", 2.3


def _naive(out, *flags, fps=100):
    assert cli_main(["naive", "--text", TEXT, "--duration", str(DUR),
                     "--fps", str(fps), *flags, "-o", out]) == 0


def _convert(track_json, out, *flags):
    assert cli_main(["convert", track_json, *flags, "-o", out]) == 0


def _blob(path):
    with open(path, "rb") as fh:
        return fh.read()


def _base(tmp_path, fps=100):
    tj = str(tmp_path / "track.json")
    _naive(tj, fps=fps)
    return tj


# --------------------------------------------------------------------------- #
# byte-identical to generate, across every exporter (4-dp-clean fps 100)       #
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("ext, flags", [
    (".anim", ["--anim-naming", "vrchat"]),
    (".tres", ["--godot-node", "Face"]),
    (".motion3.json", []),
    (".csv", []),
    (".json", []),
    (".tsv", []),
    (".xml", []),
    (".dat", []),
    (".pgo", []),
])
def test_convert_byte_identical_to_generate(tmp_path, ext, flags):
    tj = _base(tmp_path)
    gen, conv = str(tmp_path / ("gen" + ext)), str(tmp_path / ("conv" + ext))
    _naive(gen, *flags)
    _convert(tj, conv, *flags)
    assert _blob(gen) == _blob(conv)


@pytest.mark.parametrize("flags", [
    ["--retarget", "arkit"],
    ["--retarget", "vrm"],
    ["--retarget", "arkit", "--retarget-shapes"],   # shapes json appended below
    ["--retarget", "arkit", "--adjust"],            # adjust json appended below
])
def test_convert_transforms_match_generate(tmp_path, flags):
    tj = _base(tmp_path)
    flags = list(flags)
    if flags[-1] == "--retarget-shapes":
        shapes = str(tmp_path / "shapes.json")
        with open(shapes, "w") as fh:
            fh.write('["jawOpen","mouthClose","mouthFunnel"]')
        flags.append(shapes)
    if flags[-1] == "--adjust":
        adj = str(tmp_path / "adj.json")
        with open(adj, "w") as fh:
            fh.write('{"jawOpen":{"gain":0.8,"offset":0.05}}')
        flags.append(adj)
    gen, conv = str(tmp_path / "gen.json"), str(tmp_path / "conv.json")
    _naive(gen, *flags)
    _convert(tj, conv, *flags)
    assert _blob(gen) == _blob(conv)


def test_convert_edits_match_generate(tmp_path):
    import copy
    from openfacefx.edits import diff_edits, save_edits
    tj = _base(tmp_path)
    base = read_json(tj)
    edited = copy.deepcopy(base)
    for c in edited.channels:                       # nudge one channel
        if c.name == "aa":
            for k in c.keys:
                k.value = round(min(1.0, k.value + 0.1), 4)
    sidecar = str(tmp_path / "e.edits.json")
    save_edits(diff_edits(base, edited, mode="offset"), sidecar)
    gen, conv = str(tmp_path / "gen.json"), str(tmp_path / "conv.json")
    _naive(gen, "--edits", sidecar)
    _convert(tj, conv, "--edits", sidecar)
    assert _blob(gen) == _blob(conv)


# --------------------------------------------------------------------------- #
# default fps 60: byte-identical for the ≤ 4-dp-time exporters                 #
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("ext", [".csv", ".json", ".tsv", ".dat", ".pgo"])
def test_convert_byte_identical_at_default_fps(tmp_path, ext):
    tj = str(tmp_path / "track.json")
    _naive(tj, fps=60)                              # default rate, non-4dp times
    gen, conv = str(tmp_path / ("gen" + ext)), str(tmp_path / ("conv" + ext))
    _naive(gen, fps=60)
    _convert(tj, conv)
    assert _blob(gen) == _blob(conv)


# --------------------------------------------------------------------------- #
# native round-trip, extensions, .lip guard                                    #
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("fps", [60, 100])
def test_convert_native_round_trip_byte_identical(tmp_path, fps):
    tj = str(tmp_path / "track.json")
    _naive(tj, fps=fps)
    out = str(tmp_path / "rt.json")
    _convert(tj, out)
    assert _blob(tj) == _blob(out)
    # and to_dict(from_dict(d)) == d for the loaded track
    from openfacefx.io_export import to_dict, from_dict
    d = to_dict(read_json(tj))
    assert to_dict(from_dict(d)) == d


def test_convert_accepts_every_generate_extension(tmp_path):
    tj = _base(tmp_path)
    for ext in (".json", ".csv", ".anim", ".tres", ".motion3.json",
                ".tsv", ".xml", ".dat", ".pgo"):
        out = str(tmp_path / ("o" + ext))
        _convert(tj, out)
        assert os.path.getsize(out) > 0


def test_convert_lip_is_guarded_like_generate(tmp_path):
    tj = _base(tmp_path)
    # .lip is phoneme-based; the shared _write guard rejects it for non-naive/mfa,
    # and convert has no phonemes to fabricate — the same clear error stands.
    with pytest.raises(SystemExit) as ei:
        cli_main(["convert", tj, "-o", str(tmp_path / "x.lip")])
    assert ".lip" in str(ei.value)


def test_convert_missing_file_errors(tmp_path):
    with pytest.raises(SystemExit):
        cli_main(["convert", str(tmp_path / "nope.json"),
                  "-o", str(tmp_path / "o.json")])


def test_convert_fps_restamp(tmp_path):
    tj = _base(tmp_path, fps=100)
    out = str(tmp_path / "re.json")
    _convert(tj, out, "--fps", "30")
    assert read_json(out).fps == 30.0
