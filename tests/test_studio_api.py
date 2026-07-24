"""Tests for the OpenFaceFX Studio native-backend handlers (``openfacefx.studio``).

These are the pure functions behind the ``/api`` routes. The Pyodide bridge
(``PY_BRIDGE`` in ``studio_web/studio.js``) mirrors them, so pinning these pins
the shared contract. The load-bearing invariant: the Face Graph export threading
(``fgmap`` / ``fgconst`` / ``fglink``) is **opt-in** — with none supplied, the
retargeted exports are byte-identical to the plain ``arkit`` retarget.
"""

import base64
import json
import os
import tempfile

import pytest

from openfacefx import (naive_segments, generate_from_alignment, to_dict,
                        from_dict, retarget, PRESETS, write_a2f, write_json,
                        write_gltf, write_vmd, write_livelink_csv, write_rhubarb_tsv)
from openfacefx.alignment import dump_segments
from openfacefx.mapping import Mapping
from openfacefx.studio import (_generate, _export, _events, _mapping_default,
                              _mapping_json, _qa, _presets, _preset, _normalize,
                              _import, _align)

TEXT = "hello brave new world"


def _track(dur=2.4, fps=30):
    return generate_from_alignment(naive_segments(TEXT, dur), fps=fps)


def _arkit_edit():
    """The arkit preset as the Studio's editable {viseme: [[target, weight]...]}."""
    return {v: [[t, w] for (t, w) in tgts] for v, tgts in PRESETS["arkit"].items()}


# --------------------------------------------------------------------------- #
# generate
# --------------------------------------------------------------------------- #
def test_generate_basic():
    r = _generate({"text": TEXT, "dur": 2.4, "fps": 30})
    assert r["track"]["channels"]
    assert r["segments"] and all("phoneme" in s for s in r["segments"])
    assert r["duration"] > 0 and r["fps"] == 30
    assert isinstance(r["words"], list)


def test_generate_custom_mapping_changes_output():
    dm = _mapping_default()
    edit = {ph: [["O", 1.0]] for ph in dm}          # remap everything to viseme O
    mj = _mapping_json({"edit": edit})["json"]
    base = _generate({"text": TEXT, "dur": 2.4, "fps": 30})
    cust = _generate({"text": TEXT, "dur": 2.4, "fps": 30, "mapping_json": mj})
    assert base["track"] != cust["track"]
    assert {c["name"] for c in cust["track"]["channels"]} <= {"O", "sil"}


# --------------------------------------------------------------------------- #
# events
# --------------------------------------------------------------------------- #
def test_events_emphasis_and_phrase():
    segs = dump_segments(naive_segments(TEXT, 2.4))
    r = _events({"segments": segs, "emphasis": True, "phrase": True})
    types = {e["type"] for e in r["events"]}
    assert types and types <= {"emphasis", "marker"}


def test_events_toggle_emphasis_off():
    segs = dump_segments(naive_segments(TEXT, 2.4))
    r = _events({"segments": segs, "emphasis": False, "phrase": True})
    assert all(e["type"] != "emphasis" for e in r["events"])


# --------------------------------------------------------------------------- #
# mapping
# --------------------------------------------------------------------------- #
def test_mapping_default_is_phoneme_to_viseme():
    dm = _mapping_default()
    assert len(dm) > 20
    for ph, rows in dm.items():
        assert all(isinstance(t, str) and 0.0 <= w <= 1.0 for t, w in rows)


def test_mapping_json_roundtrips(tmp_path):
    dm = _mapping_default()
    r = _mapping_json({"edit": dm})
    p = tmp_path / "m.json"
    p.write_text(r["json"])
    m = Mapping.from_json(str(p))                    # validates the canonical format
    assert len(m.rows) == len(dm)
    assert json.loads(r["json"])["format"] == "openfacefx.mapping"


# --------------------------------------------------------------------------- #
# export — byte-identity invariant
# --------------------------------------------------------------------------- #
def test_export_default_equals_plain_arkit_retarget():
    d = to_dict(_track())
    got = base64.b64decode(_export("a2f", d)["b64"])
    ref = os.path.join(tempfile.mkdtemp(), "ref.a2f.json")
    write_a2f(retarget(from_dict(d), PRESETS["arkit"]), ref)
    with open(ref, "rb") as f:
        assert got == f.read()


def test_export_no_facegraph_args_is_identity():
    d = to_dict(_track())
    assert _export("a2f", d)["b64"] == _export("a2f", d, None, None, None)["b64"]
    assert _export("livelink", d)["b64"] == _export("livelink", d, None)["b64"]


@pytest.mark.parametrize("fmt", [
    "json", "csv", "glb", "vrma", "spine", "live2d", "exp3", "unity",
    "godot", "vmd", "livelink", "a2f", "rhubarb", "moho",
])
def test_export_all_formats_smoke(fmt):
    r = _export(fmt, to_dict(_track()))
    assert r.get("b64")


# --------------------------------------------------------------------------- #
# export — Face Graph threading (opt-in)
# --------------------------------------------------------------------------- #
def test_export_fgmap_adds_cloned_output():
    d = to_dict(_track())
    fgmap = _arkit_edit()
    fgmap["aa"] = fgmap["aa"] + [["jawOpen_copy", 1.0]]
    js = json.loads(base64.b64decode(_export("a2f", d, fgmap)["b64"]))
    assert "jawOpen_copy" in js["facsNames"]


def test_export_fgconst_flattens_output():
    d = to_dict(_track())
    fgmap = _arkit_edit()
    fgmap["aa"] = fgmap["aa"] + [["jawOpen_copy", 1.0]]
    js = json.loads(base64.b64decode(_export("a2f", d, fgmap, {"jawOpen_copy": 0.8})["b64"]))
    col = [row[js["facsNames"].index("jawOpen_copy")] for row in js["weightMat"]]
    assert max(col) == pytest.approx(0.8) and min(col) == pytest.approx(0.8)


def test_export_fglink_quadratic_compresses():
    d = to_dict(_track())
    fgmap = _arkit_edit()
    fgmap["aa"] = fgmap["aa"] + [["jawOpen_copy", 1.0]]
    lin = json.loads(base64.b64decode(_export("a2f", d, fgmap)["b64"]))
    quad = json.loads(base64.b64decode(
        _export("a2f", d, fgmap, None, {"jawOpen_copy": "quadratic"})["b64"]))
    cl = [r[lin["facsNames"].index("jawOpen_copy")] for r in lin["weightMat"]]
    cq = [r[quad["facsNames"].index("jawOpen_copy")] for r in quad["weightMat"]]
    assert cq != cl
    assert all(q <= l + 1e-9 for l, q in zip(cl, cq))   # x**2 <= x on [0, 1]


# --------------------------------------------------------------------------- #
# qa / normalize / presets
# --------------------------------------------------------------------------- #
def test_qa_counts_channels():
    d = to_dict(_track())
    segs = dump_segments(naive_segments(TEXT, 2.4))
    r = _qa({"track": d, "segments": segs, "text": TEXT})
    assert r["channels"] == len(d["channels"])


def test_normalize_folds_punctuation():
    r = _normalize({"text": "“quotes” — dash"})
    assert '"quotes"' in r["text"] and "--" in r["text"]
    assert "“" not in r["text"] and "—" not in r["text"]


def test_presets_and_preset_shape():
    assert "arkit" in _presets()
    m = _preset("arkit")
    assert isinstance(m, dict) and m
    for vis, rows in m.items():
        assert all(isinstance(t, str) for t, _ in rows)


# --------------------------------------------------------------------------- #
# import — the read side of the exporters (round-trips a written file into a take)
# --------------------------------------------------------------------------- #
def _imp(tmp_path, writer, obj, filename):
    p = tmp_path / filename
    writer(obj, str(p))
    with open(p, "rb") as f:
        b64 = base64.b64encode(f.read()).decode()
    return _import({"name": filename, "b64": b64})


def test_import_track_json_roundtrip(tmp_path):
    r = _imp(tmp_path, write_json, _track(), "t.track.json")
    assert "error" not in r and r["channels"] > 0 and r["track"]["channels"]


def test_import_gltf_and_vmd(tmp_path):
    assert _imp(tmp_path, write_gltf, _track(), "t.glb").get("channels", 0) > 0
    assert _imp(tmp_path, write_vmd, _track(), "t.vmd").get("channels", 0) > 0


def test_import_arkit_formats(tmp_path):
    ark = retarget(_track(), PRESETS["arkit"])
    assert _imp(tmp_path, write_livelink_csv, ark, "t.livelink.csv").get("channels", 0) > 0
    a2f = _imp(tmp_path, write_a2f, ark, "t.a2f.json")            # .json sniffed as a2f, not track
    assert a2f.get("channels", 0) > 0


def test_import_cue_file(tmp_path):
    assert _imp(tmp_path, write_rhubarb_tsv, _track(), "t.tsv").get("channels", 0) > 0


def test_import_bad_data_errors_cleanly():
    r = _import({"name": "x.glb", "b64": base64.b64encode(b"not a glb").decode()})
    assert "error" in r and "track" not in r


# --------------------------------------------------------------------------- #
# Bethesda .lip / .fuz export (from phoneme segments)
# --------------------------------------------------------------------------- #
def test_export_lip_from_segments():
    d = to_dict(_track())
    segs = dump_segments(naive_segments(TEXT, 2.4))
    r = _export("lip", d, segments=segs)
    assert "error" not in r and len(base64.b64decode(r["b64"])) > 0
    assert "error" in _export("lip", d, segments=[])          # .lip needs segments


def test_align_srt_self_transcribing():
    srt = ("1\n00:00:00,000 --> 00:00:00,600\nhello\n\n"
           "2\n00:00:00,600 --> 00:00:01,200\nworld\n")
    r = _align({"format": "srt", "text": srt, "transcript": ""})   # SRT carries the words
    assert "error" not in r and r["channels"] > 0 and r["segments"]
    assert r["duration"] == pytest.approx(1.2, abs=0.2)


def test_align_words_needs_transcript_then_works():
    words = json.dumps([{"word": "hello", "start": 0.0, "end": 0.6},
                        {"word": "world", "start": 0.6, "end": 1.2}])
    assert "error" in _align({"format": "words", "text": words, "transcript": ""})
    r = _align({"format": "words", "text": words, "transcript": "hello world"})
    assert "error" not in r and r["channels"] > 0


def test_align_whisper_json():
    wh = json.dumps({"segments": [{"words": [
        {"word": "hello", "start": 0.0, "end": 0.6},
        {"word": "world", "start": 0.6, "end": 1.2}]}]})
    r = _align({"format": "whisper", "text": wh, "transcript": ""})
    assert "error" not in r and r["channels"] > 0


def test_export_fuz_bundles_lip_and_audio():
    from openfacefx.bethesda import FUZ_MAGIC, read_fuz
    d = to_dict(_track())
    segs = dump_segments(naive_segments(TEXT, 2.4))
    audio = b"RIFFfake-audio" * 20
    r = _export("fuz", d, segments=segs, audio_b64=base64.b64encode(audio).decode())
    data = base64.b64decode(r["b64"])
    assert data[:4] == FUZ_MAGIC
    assert "error" in _export("fuz", d, segments=segs)         # .fuz needs audio
    import tempfile as _tf, os as _os
    p = _os.path.join(_tf.mkdtemp(), "t.fuz")
    with open(p, "wb") as f:
        f.write(data)
    lip, back = read_fuz(p)                                    # round-trips
    assert len(lip) > 0 and back == audio
