"""Esoteric Spine slot-attachment exporter (openfacefx.export_spine, #63).

A Spine attachment timeline is switch data — animations.<a>.slots.<slot>.attachment
= [{"time": seconds, "name": attachment}]. The Spine editor is the external gate
(can't run here); the in-repo proof is a true round-trip (read_spine_cues recovers
the written cues) plus a splice-preservation check: splicing a mouth timeline into
an artist's Spine JSON leaves every other section (skeleton/bones/skins/other
slots/other animations) deep-equal and does not mutate the caller's dict. Plus
structural validity, determinism, and the CLI paths (standalone + splice).
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
from openfacefx.export_cues import _rhubarb_cues
from openfacefx.export_spine import (DEFAULT_ATTACHMENT_MAP, build_spine,
                                     read_spine_cues, splice_spine, write_spine)
from openfacefx.io_export import from_dict, to_dict
from openfacefx.pipeline import generate_from_alignment, naive_segments

TEXT, DUR, FPS = "hello brave new world", 2.3, 60.0


def _source():
    return from_dict(to_dict(generate_from_alignment(naive_segments(TEXT, DUR),
                                                     fps=FPS)))


def _expected_keyframes(track):
    """The intended (time, attachment) timeline, straight from the cue reduction."""
    out, prev = [], None
    for start, _end, shape in _rhubarb_cues(track, None, None):
        name = DEFAULT_ATTACHMENT_MAP[shape]
        if name != prev:
            out.append((round(float(start), 4), name))
            prev = name
    return out


def _fake_base():
    return {
        "skeleton": {"spine": "4.1.24", "hash": "Zx9", "images": "./images/"},
        "bones": [{"name": "root"}, {"name": "head", "parent": "root"}],
        "slots": [{"name": "mouth", "bone": "head", "attachment": "mouth_x"},
                  {"name": "eyes", "bone": "head", "attachment": "eyes_open"}],
        "skins": [{"name": "default", "attachments": {
            "mouth": {"mouth_a": {"width": 64, "height": 40}},
            "eyes": {"eyes_open": {}}}}],
        "animations": {"blink": {"slots": {
            "eyes": {"attachment": [{"time": 0.5, "name": "eyes_closed"}]}}}},
    }


# --------------------------------------------------------------------------- #
# round-trip + structure (standalone)                                          #
# --------------------------------------------------------------------------- #

def test_standalone_round_trip(tmp_path):
    src = _source()
    path = str(tmp_path / "m.spine.json")
    write_spine(src, path)
    got = read_spine_cues(path)
    assert got == _expected_keyframes(src)
    assert len(got) > 1                                   # real speech -> changes
    assert got[0][0] == 0.0                               # first keyframe at t=0
    assert all(b[0] > a[0] for a, b in zip(got, got[1:]))  # strictly increasing


def test_standalone_structure(tmp_path):
    src = _source()
    doc = build_spine(src, anim_name="lipsync", slot="mouth")
    assert [b["name"] for b in doc["bones"]] == ["root"]
    assert doc["slots"][0]["name"] == "mouth"
    kf = doc["animations"]["lipsync"]["slots"]["mouth"]["attachment"]
    names = {k["name"] for k in kf}
    # every referenced attachment is declared in the skin, and is a mapped name
    skin_atts = set(doc["skins"][0]["attachments"]["mouth"])
    assert names <= skin_atts
    assert names <= set(DEFAULT_ATTACHMENT_MAP.values())
    assert doc["slots"][0]["attachment"] in skin_atts     # rest pose is real


def test_custom_attachment_map_and_dedup(tmp_path):
    src = _source()
    # collapse every shape to ONE attachment -> a single keyframe survives dedup
    flat = {s: "m" for s in "ABCDEFGHX"}
    path = str(tmp_path / "flat.spine.json")
    write_spine(src, path, attachment_map=flat)
    got = read_spine_cues(path)
    assert got == [(0.0, "m")]


# --------------------------------------------------------------------------- #
# splice mode                                                                  #
# --------------------------------------------------------------------------- #

def test_splice_preserves_all_other_content():
    src = _source()
    base = _fake_base()
    frozen = json.loads(json.dumps(base))                 # a pristine snapshot
    out = splice_spine(base, src, anim_name="lipsync", slot="mouth")
    # caller's dict is untouched (deepcopy)
    assert base == frozen
    # every non-spliced section is deep-equal to the input
    assert out["skeleton"] == frozen["skeleton"]
    assert out["bones"] == frozen["bones"]
    assert out["slots"] == frozen["slots"]
    assert out["skins"] == frozen["skins"]
    assert out["animations"]["blink"] == frozen["animations"]["blink"]
    # and only the mouth timeline was added
    assert out["animations"]["lipsync"]["slots"]["mouth"]["attachment"] == \
        [{"time": t, "name": n} for t, n in _expected_keyframes(src)]


def test_splice_into_existing_animation_keeps_sibling_slots():
    src = _source()
    base = _fake_base()
    base["animations"]["lipsync"] = {"slots": {
        "eyes": {"attachment": [{"time": 0.0, "name": "eyes_open"}]}}}
    out = splice_spine(base, src, anim_name="lipsync", slot="mouth")
    assert out["animations"]["lipsync"]["slots"]["eyes"] == \
        {"attachment": [{"time": 0.0, "name": "eyes_open"}]}
    assert "mouth" in out["animations"]["lipsync"]["slots"]


def test_splice_rejects_missing_slot():
    src = _source()
    base = _fake_base()
    with pytest.raises(ValueError, match="not declared"):
        splice_spine(base, src, slot="nonexistent")


# --------------------------------------------------------------------------- #
# determinism + CLI                                                            #
# --------------------------------------------------------------------------- #

def test_deterministic_bytes(tmp_path):
    src = _source()
    a, b = str(tmp_path / "a.spine.json"), str(tmp_path / "b.spine.json")
    write_spine(src, a)
    write_spine(src, b)
    assert open(a, "rb").read() == open(b, "rb").read()


def test_cli_standalone_and_convert(tmp_path):
    tj = str(tmp_path / "t.json")
    assert cli_main(["naive", "--text", TEXT, "--duration", str(DUR), "-o", tj]) == 0
    out = str(tmp_path / "gen.spine.json")
    assert cli_main(["naive", "--text", TEXT, "--duration", str(DUR),
                     "-o", out]) == 0
    assert len(read_spine_cues(out)) > 1
    conv = str(tmp_path / "conv.spine.json")
    assert cli_main(["convert", tj, "-o", conv]) == 0
    assert open(out, "rb").read() == open(conv, "rb").read()   # same track, same bytes


def test_cli_splice(tmp_path):
    base_path = str(tmp_path / "rig.json")
    with open(base_path, "w") as fh:
        json.dump(_fake_base(), fh)
    out = str(tmp_path / "rigged.spine.json")
    assert cli_main(["naive", "--text", TEXT, "--duration", str(DUR),
                     "--spine-base", base_path, "-o", out]) == 0
    doc = json.load(open(out))
    assert doc["bones"] == _fake_base()["bones"]           # rig preserved
    assert "mouth" in doc["animations"]["lipsync"]["slots"]


def test_cli_rejects_retarget(tmp_path):
    with pytest.raises(SystemExit, match="retarget"):
        cli_main(["naive", "--text", TEXT, "--duration", str(DUR),
                  "--retarget", "vrm", "-o", str(tmp_path / "x.spine.json")])
