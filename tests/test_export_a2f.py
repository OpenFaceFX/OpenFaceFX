"""NVIDIA Audio2Face blendshape-JSON interop (openfacefx.export_a2f, #64).

A2F blendshape JSON is {facsNames, weightMat, numFrames, numPoses, exportFps}. No
A2F install here, so the proof is: (a) export faithfulness — weightMat equals the
track sampled on the fps grid (6dp); (b) a true write->read round-trip through the
RDP thinner reconstructs every channel within the epsilon tolerance; plus structure,
facsNames-verbatim, viseme-track detection, import validation, exportFps precedence,
determinism, and the CLI export + from-a2f import paths.
"""

import json
import os
import sys

import numpy as np
import pytest

try:
    import openfacefx  # noqa: F401
except ImportError:
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from openfacefx.cli import main as cli_main
from openfacefx.edits import sample
from openfacefx.export_a2f import a2f_dict, parse_a2f, read_a2f, write_a2f
from openfacefx.inspect import POSE_CHANNELS
from openfacefx.io_export import from_dict, to_dict
from openfacefx.pipeline import generate_from_alignment, naive_segments
from openfacefx.retarget import PRESETS, retarget

TEXT, DUR, FPS = "hello brave new world", 2.3, 30.0


def _arkit_source():
    t = generate_from_alignment(naive_segments(TEXT, DUR), fps=60.0)
    return retarget(t, PRESETS["arkit"])          # channels in ARKit blendshape space


# --------------------------------------------------------------------------- #
# export faithfulness + structure                                              #
# --------------------------------------------------------------------------- #

def test_export_structure_and_faithfulness():
    src = _arkit_source()
    doc, matched = a2f_dict(src, fps=FPS)
    names = doc["facsNames"]
    assert doc["numPoses"] == len(names)
    assert doc["numFrames"] == len(doc["weightMat"])
    assert doc["exportFps"] == FPS
    assert matched > 0                            # arkit names recognised
    assert all(len(row) == doc["numPoses"] for row in doc["weightMat"])
    # weightMat equals the track sampled on the export grid (6dp)
    n = doc["numFrames"]
    grid = np.arange(n, dtype=float) / FPS
    cmap = {c.name: c for c in src.channels}
    W = np.array(doc["weightMat"])
    for k, name in enumerate(names):
        want = np.round(np.clip(sample(cmap[name], grid), 0.0, 1.0), 6)
        assert np.allclose(W[:, k], want, atol=1e-9)


def test_pose_channels_excluded():
    # A2F carries [0,1] weights, not rotation angles -> pose channels are dropped
    from openfacefx.curves import Channel, FaceTrack, Keyframe
    pose = sorted(POSE_CHANNELS)[0]
    src = FaceTrack(fps=30.0, channels=[
        Channel("jawOpen", [Keyframe(0.0, 0.0), Keyframe(1.0, 0.5)]),
        Channel(pose, [Keyframe(0.0, 0.0), Keyframe(1.0, 10.0)]),
    ])
    doc, _ = a2f_dict(src, fps=FPS)
    assert "jawOpen" in doc["facsNames"]
    assert pose not in doc["facsNames"]
    assert not (set(doc["facsNames"]) & set(POSE_CHANNELS))


def test_viseme_track_reports_zero_matched():
    src = from_dict(to_dict(generate_from_alignment(naive_segments(TEXT, DUR),
                                                    fps=60.0)))
    _, matched = a2f_dict(src, fps=FPS)           # Oculus visemes, not ARKit
    assert matched == 0


# --------------------------------------------------------------------------- #
# round-trip through the RDP thinner                                            #
# --------------------------------------------------------------------------- #

def test_write_read_round_trip(tmp_path):
    src = _arkit_source()
    path = str(tmp_path / "a.a2f.json")
    write_a2f(src, path, fps=FPS)
    track2, warnings = read_a2f(path)                      # default RDP epsilon 0.015
    assert warnings == []
    assert track2.channels
    # the recovered track matches the original within the RDP-thinning tolerance
    # (import is lossy by design; export faithfulness is proven exactly above)
    n = int(round(src.duration * FPS)) + 1
    grid = np.arange(n, dtype=float) / FPS
    c1 = {c.name: c for c in src.channels}
    c2 = {c.name: c for c in track2.channels}
    assert set(c2) == set(c1)
    for name in c1:
        a = np.clip(sample(c1[name], grid), 0.0, 1.0)
        b = np.clip(sample(c2[name], grid), 0.0, 1.0)
        assert float(np.max(np.abs(a - b))) < 0.04


def test_facsnames_verbatim_and_exportfps_roundtrips(tmp_path):
    src = _arkit_source()
    path = str(tmp_path / "b.a2f.json")
    write_a2f(src, path, fps=48.0)
    doc = json.load(open(path))
    assert doc["exportFps"] == 48.0
    # names come straight from the track's channels (no forced 52-column header)
    assert doc["facsNames"] == [c.name for c in src.channels
                                if c.name not in POSE_CHANNELS]


# --------------------------------------------------------------------------- #
# import validation + fps precedence                                           #
# --------------------------------------------------------------------------- #

def test_import_fps_precedence():
    doc = {"exportFps": 24.0, "numFrames": 3, "numPoses": 1,
           "facsNames": ["jawOpen"], "weightMat": [[0.0], [1.0], [0.0]]}
    # file exportFps drives timing: 3 frames @24 -> last key at 2/24
    t_file, _ = parse_a2f(doc)
    assert t_file.duration == pytest.approx(2 / 24.0, abs=1e-6)
    # explicit fps overrides the file
    t_over, _ = parse_a2f(doc, fps=12.0)
    assert t_over.duration == pytest.approx(2 / 12.0, abs=1e-6)
    # missing exportFps falls back to the fps arg
    doc2 = dict(doc); del doc2["exportFps"]
    t_fb, _ = parse_a2f(doc2, fps=10.0)
    assert t_fb.duration == pytest.approx(2 / 10.0, abs=1e-6)


@pytest.mark.parametrize("bad,msg", [
    ({"facsNames": "x", "weightMat": []}, "facsNames"),
    ({"facsNames": ["a"], "weightMat": {}}, "weightMat"),
    ({"facsNames": ["a", "a"], "weightMat": [[0, 0]]}, "duplicate"),
    ({"facsNames": ["a", "b"], "weightMat": [[0.0]]}, "row 0"),
    ({"facsNames": ["a"], "weightMat": [[float("nan")]]}, "non-finite"),
])
def test_import_rejects_malformed(bad, msg):
    with pytest.raises(ValueError, match=msg):
        parse_a2f(bad)


def test_import_clamps_and_warns():
    doc = {"exportFps": 30.0, "numFrames": 2, "numPoses": 1,
           "facsNames": ["jawOpen"], "weightMat": [[1.5], [-0.2]]}
    track, warnings = parse_a2f(doc)
    assert any("outside [0, 1]" in w for w in warnings)
    assert all(0.0 <= k.value <= 1.0 for c in track.channels for k in c.keys)


# --------------------------------------------------------------------------- #
# determinism + CLI                                                            #
# --------------------------------------------------------------------------- #

def test_deterministic_bytes(tmp_path):
    src = _arkit_source()
    a, b = str(tmp_path / "a.a2f.json"), str(tmp_path / "b.a2f.json")
    write_a2f(src, a, fps=FPS)
    write_a2f(src, b, fps=FPS)
    assert open(a, "rb").read() == open(b, "rb").read()


def test_cli_export_and_from_a2f_round_trip(tmp_path):
    out = str(tmp_path / "gen.a2f.json")
    assert cli_main(["naive", "--text", TEXT, "--duration", str(DUR),
                     "--retarget", "arkit", "-o", out]) == 0
    doc = json.load(open(out))
    assert "weightMat" in doc and doc["numPoses"] > 0
    back = str(tmp_path / "back.json")
    assert cli_main(["from-a2f", out, "-o", back]) == 0
    track = from_dict(json.load(open(back)))
    assert track.channels
