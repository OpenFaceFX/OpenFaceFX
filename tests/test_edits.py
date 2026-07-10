"""Edit-preservation layer (`openfacefx.edits`, issue #9).

The layer is additive, opt-in and deterministic. These tests pin: the
byte-identity of an edit-free track (`to_dict` grows no keys, `version` stays 1,
`from_dict` is a faithful inverse), the offset/replace round-trips (a hand-edit
captured by `diff_edits` and re-applied by `apply_edits` survives regeneration --
exactly for `replace`, and *relative* for `offset` so it also survives an
intensity change), the locked-region semantics (the span wins, the fresh curve
shows through elsewhere), the absent-channel conflict (preserved+warned, or
dropped under take-generated), sidecar JSON schema validation with clear errors,
and a hard-coded golden merge that MUST reproduce on Python 3.9 and 3.13 (pure
numpy + `_rdp`, no RNG, stable 4-dp rounding).
"""

import copy
import json
import os
import sys

import numpy as np
import pytest

try:
    import openfacefx  # noqa: F401  (installed wheel wins; see test_core)
except ImportError:
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from openfacefx.cli import main as cli_main
from openfacefx.coarticulation import CoartParams
from openfacefx.curves import Channel, FaceTrack, Keyframe
from openfacefx.edits import (
    FORMAT, VERSION, EditsDoc, apply_edits, diff_edits, load_edits, sample,
    save_edits, _sha1_source, _sha1_track,
)
from openfacefx.io_export import from_dict, read_json, to_dict, write_json
from openfacefx.pipeline import generate_from_alignment, naive_segments

TEXT, DUR = "hello world", 1.5


def _base(params=None, text=TEXT, dur=DUR, fps=60.0):
    return generate_from_alignment(naive_segments(text, dur), fps=fps, params=params)


def _by_name(track):
    return {c.name: c for c in track.channels}


def _bump(track, name, delta):
    """A deepcopy of ``track`` with ``name``'s keys shifted by ``delta`` (clamped)."""
    ed = copy.deepcopy(track)
    for c in ed.channels:
        if c.name == name:
            for k in c.keys:
                k.value = round(min(1.0, max(0.0, k.value + delta)), 4)
    return ed


def _common_channel(a, b):
    """A non-silence channel present in both tracks (something to edit)."""
    shared = [n for n in _by_name(a) if n in _by_name(b) and n != "sil"]
    assert shared, "expected a shared editable channel"
    return shared[0]


# --- additive / byte-identity ----------------------------------------------

def test_no_edits_to_dict_byte_identical():
    # A track carries no edit metadata: to_dict grows no keys, version stays 1.
    tr = _base()
    d = to_dict(tr)
    assert list(d)[:6] == ["format", "version", "fps", "duration",
                           "viseme_set", "channels"]
    assert d["version"] == 1
    assert "source_id" not in d


def test_source_id_is_opt_in():
    # Passing source_id embeds one key; without it the dict is byte-identical.
    tr = _base()
    base = to_dict(tr)
    withid = to_dict(tr, source_id="sha1:abc")
    assert withid["source_id"] == "sha1:abc"
    del withid["source_id"]
    assert withid == base


def test_from_dict_read_json_is_faithful_inverse(tmp_path):
    tr = _base()
    d = to_dict(tr)
    assert to_dict(from_dict(d)) == d           # round-trips byte-for-byte
    p = str(tmp_path / "t.json")
    write_json(tr, p)
    assert to_dict(read_json(p)) == d


def test_from_dict_rejects_foreign_format():
    with pytest.raises(ValueError, match="openfacefx.track"):
        from_dict({"format": "nope", "version": 1, "fps": 60, "channels": []})


# --- round-trips: an edit survives regeneration ----------------------------

def test_replace_roundtrip_is_exact():
    base = _base()
    name = _common_channel(base, base)
    edited = _bump(base, name, 0.15)
    doc = diff_edits(base, edited, mode="replace")
    assert doc.channels[name]["mode"] == "replace"
    merged, conflicts = apply_edits(_base(), doc)      # same inputs -> same gen
    assert not conflicts
    got = [[round(k.time, 4), round(k.value, 4)] for k in _by_name(merged)[name].keys]
    want = [[round(k.time, 4), round(k.value, 4)] for k in _by_name(edited)[name].keys]
    assert got == want


def test_offset_roundtrip_recovers_edit():
    base = _base()
    name = _common_channel(base, base)
    edited = _bump(base, name, 0.2)
    doc = diff_edits(base, edited, mode="offset")
    assert doc.channels[name]["mode"] == "offset"
    merged, _ = apply_edits(_base(), doc)
    T = np.linspace(0, base.duration, 60)
    err = np.max(np.abs(sample(_by_name(merged)[name], T)
                        - sample(_by_name(edited)[name], T)))
    assert err < 0.02


def test_offset_survives_intensity_change():
    # The primary path: an offset is relative, so it re-applies onto a *different*
    # regenerated curve (here, one produced at a higher articulation intensity).
    base = _base()
    hot = _base(params=_intensity(1.4))
    name = _common_channel(base, hot)
    edited = _bump(base, name, 0.2)
    doc = diff_edits(base, edited, mode="offset")
    merged, conflicts = apply_edits(hot, doc)
    assert not conflicts
    T = np.linspace(0, base.duration, 200)
    off = sample(doc.channels[name]["keys"], T)
    clamp = doc.channels[name]["clamp"]
    m = sample(_by_name(merged)[name], T)
    hot_v = sample(_by_name(hot)[name], T)
    # apply reproduces clip(regenerated + offset): the hand-edit rode onto the NEW
    # curve, not the stale baseline. Mean error (the clip kinks a piecewise-linear
    # curve can't capture are sparse) and the clamp is respected.
    assert np.mean(np.abs(m - np.clip(hot_v + off, *clamp))) < 0.01
    assert m.max() <= clamp[1] + 1e-6 and m.min() >= clamp[0] - 1e-6
    assert np.mean(np.abs(m - hot_v)) > 0.05          # the edit actually moved it


def _intensity(v):
    p = CoartParams()
    p.intensity = v
    return p


# --- locked region: the span wins, the fresh curve shows through elsewhere ---

def _ramp_track():
    return FaceTrack(60, [Channel("aa", [
        Keyframe(0.0, 0.0), Keyframe(0.25, 0.2), Keyframe(0.5, 0.5),
        Keyframe(0.75, 0.8), Keyframe(1.0, 1.0), Keyframe(1.5, 0.0)])])


def test_locked_region_overrides_span_untouched_elsewhere():
    gen = _ramp_track()
    t0, t1 = 0.5, 1.0
    user = [[0.5, 0.9], [0.7, 0.9], [1.0, 0.9]]
    doc = EditsDoc(channels={"aa": {"mode": "replace", "span": [t0, t1],
                                    "keys": user}}, fps=60.0)
    merged, conflicts = apply_edits(gen, doc)
    assert not conflicts
    ch = _by_name(merged)["aa"]
    # inside the span, the user's keys win
    for t, v in user:
        if t0 < t < t1:
            assert abs(float(sample(ch, [t])[0]) - v) < 1e-9
    # outside the span, every generated keyframe survives verbatim
    for k in gen.channels[0].keys:
        if k.time < t0 or k.time > t1:
            assert abs(float(sample(ch, [k.time])[0]) - k.value) < 1e-9


def test_offset_span_only_touches_its_window():
    gen = _ramp_track()
    doc = EditsDoc(channels={"aa": {"mode": "offset", "clamp": [0.0, 1.0],
                                    "span": [0.5, 1.0],
                                    "keys": [[0.5, 0.05], [1.0, 0.05]]}}, fps=60.0)
    merged, _ = apply_edits(gen, doc)
    ch = _by_name(merged)["aa"]
    for k in gen.channels[0].keys:            # outside [0.5,1.0] untouched
        if k.time < 0.5 or k.time > 1.0:
            assert abs(float(sample(ch, [k.time])[0]) - k.value) < 1e-9
    assert float(sample(ch, [0.75])[0]) > float(sample(gen.channels[0], [0.75])[0])


# --- conflicts: an edit on a now-absent channel ----------------------------

def test_absent_channel_preserved_and_warned():
    gen = _base()
    doc = EditsDoc(channels={"ZZ_absent": {"mode": "replace",
                                           "keys": [[0.1, 0.5], [0.5, 0.6]]}},
                   fps=gen.fps)
    merged, conflicts = apply_edits(gen, doc)                 # keep-edit (default)
    assert "ZZ_absent" in _by_name(merged)
    assert any(c["channel"] == "ZZ_absent" and c["reason"] == "absent-from-regen"
               for c in conflicts)
    # the preserved channel is announced in target_set too (like the gesture layer)
    assert merged.target_set is not None and "ZZ_absent" in merged.target_set


def test_take_generated_drops_absent_channel_but_still_warns():
    gen = _base()
    doc = EditsDoc(channels={"ZZ_absent": {"mode": "replace",
                                           "keys": [[0.1, 0.5], [0.5, 0.6]]}},
                   fps=gen.fps)
    merged, conflicts = apply_edits(gen, doc, on_conflict="take-generated")
    assert "ZZ_absent" not in _by_name(merged)
    assert conflicts and conflicts[0]["channel"] == "ZZ_absent"


def test_apply_leaves_untouched_channels_alone():
    gen = _base()
    name = _common_channel(gen, gen)
    doc = EditsDoc(channels={name: {"mode": "offset", "clamp": [0.0, 1.0],
                                    "keys": [[0.0, 0.0], [gen.duration, 0.0]]}},
                   fps=gen.fps)
    merged, _ = apply_edits(gen, doc)
    for other in _by_name(gen):
        if other == name:
            continue
        a = [[k.time, k.value] for k in _by_name(gen)[other].keys]
        b = [[k.time, k.value] for k in _by_name(merged)[other].keys]
        assert a == b


# --- diff edge cases -------------------------------------------------------

def test_diff_skips_untouched_channels():
    base = _base()
    doc = diff_edits(base, copy.deepcopy(base), mode="offset")
    assert doc.channels == {}                     # nothing changed -> empty sidecar
    assert doc.base_hash == _sha1_track(base)


def test_diff_captures_added_and_silenced_channels():
    base = _base()
    name = _common_channel(base, base)
    # user adds a brand-new channel and silences an existing one
    edited = copy.deepcopy(base)
    edited.channels.append(Channel("browUp", [Keyframe(0.2, 0.8), Keyframe(0.6, 0.0)]))
    edited.channels = [c for c in edited.channels if c.name != name]
    doc = diff_edits(base, edited, mode="offset")
    assert doc.channels["browUp"]["mode"] == "replace"       # added -> replace
    assert doc.channels[name]["mode"] == "replace"           # silenced -> flat zero
    assert all(v == 0.0 for _, v in doc.channels[name]["keys"])


# --- sidecar schema validation ---------------------------------------------

@pytest.mark.parametrize("doc, match", [
    ({"format": "x", "version": 1, "channels": {}}, "openfacefx.edits"),
    ({"format": FORMAT, "version": 2, "channels": {}}, "version"),
    ({"format": FORMAT, "version": 1, "fps": 0, "channels": {}}, "fps"),
    ({"format": FORMAT, "version": 1, "channels": []}, "object"),
    ({"format": FORMAT, "version": 1,
      "channels": {"aa": {"mode": "bogus", "keys": [[0, 0]]}}}, "mode"),
    ({"format": FORMAT, "version": 1,
      "channels": {"aa": {"mode": "offset", "keys": []}}}, "non-empty"),
    ({"format": FORMAT, "version": 1,
      "channels": {"aa": {"mode": "offset", "keys": [[0.0, float("nan")]]}}},
     "non-finite"),
    ({"format": FORMAT, "version": 1,
      "channels": {"aa": {"mode": "replace", "keys": [[1.0, 0.1], [0.0, 0.2]]}}},
     "ascending"),
    ({"format": FORMAT, "version": 1,
      "channels": {"aa": {"mode": "offset", "keys": [[0, 0]], "clamp": [1.0, 0.0]}}},
     "clamp"),
    ({"format": FORMAT, "version": 1,
      "channels": {"aa": {"mode": "replace", "keys": [[0, 0]], "span": [1.0, 0.5]}}},
     "span"),
])
def test_schema_validation_errors(doc, match):
    with pytest.raises(ValueError, match=match):
        EditsDoc.from_dict(doc)


def test_sidecar_json_roundtrips(tmp_path):
    # Author a mix of modes via the API, then confirm load == the doc and that the
    # canonical (validated) form re-saves byte-identically.
    doc = EditsDoc(
        channels={
            "PP": {"mode": "offset", "keys": [[0.1, 0.05], [0.5, -0.1]],
                   "clamp": [0.0, 1.0]},
            "aa": {"mode": "replace", "keys": [[0.4, 0.7], [0.9, 0.6]],
                   "span": [0.4, 0.9]},
        },
        fps=60.0, source_id="sha1:abc", base_hash="sha1:def")
    p = str(tmp_path / "e.edits.json")
    save_edits(doc, p)
    back = load_edits(p)
    assert back.channels == doc.channels
    assert (back.fps, back.source_id, back.base_hash) == (60.0, "sha1:abc", "sha1:def")
    p2 = str(tmp_path / "e2.edits.json")
    save_edits(back, p2)
    assert open(p).read() == open(p2).read()


def test_diff_output_is_load_stable(tmp_path):
    # A sidecar produced by diff_edits reloads to an identical doc (validation
    # does not reorder or mutate the canonical record shape).
    base = _base()
    name = _common_channel(base, base)
    doc = diff_edits(base, _bump(base, name, 0.15), mode="offset")
    p = str(tmp_path / "d.edits.json")
    save_edits(doc, p)
    assert load_edits(p).channels == doc.channels


# --- determinism + cross-version golden ------------------------------------

def test_apply_is_deterministic():
    gen = _base()
    name = _common_channel(gen, gen)
    doc = EditsDoc(channels={name: {"mode": "offset", "clamp": [0.0, 1.0],
                                    "keys": [[0.2, 0.1], [0.9, 0.1]]}}, fps=gen.fps)
    a, _ = apply_edits(gen, doc)
    b, _ = apply_edits(gen, doc)
    assert to_dict(a) == to_dict(b)


def test_offset_merge_golden():
    # Hard-coded: offset of a constant +0.1 onto a triangle, clamped at 1.0. These
    # exact keys must reproduce on Python 3.9/3.13 (np.interp + np.clip + _rdp +
    # 4-dp rounding are all version-stable, no RNG).
    gen = FaceTrack(60, [Channel("aa", [Keyframe(0.0, 0.0),
                                        Keyframe(0.5, 1.0), Keyframe(1.0, 0.0)])])
    doc = EditsDoc(channels={"aa": {"mode": "offset", "clamp": [0.0, 1.0],
                                    "keys": [[0.0, 0.1], [1.0, 0.1]]}}, fps=60.0)
    merged, _ = apply_edits(gen, doc, eps=0.001)
    keys = [[round(k.time, 4), round(k.value, 4)] for k in merged.channels[0].keys]
    assert keys == [[0.0, 0.1], [0.5, 1.0], [1.0, 0.1]]


# --- CLI end-to-end --------------------------------------------------------

def test_cli_diff_and_regen_survives(tmp_path):
    base = str(tmp_path / "base.json")
    edited = str(tmp_path / "edited.json")
    edits = str(tmp_path / "e.edits.json")
    out = str(tmp_path / "out.json")

    assert cli_main(["naive", "--text", TEXT, "--duration", str(DUR), "-o", base]) == 0
    # hand-edit a channel in the written baseline
    tr = read_json(base)
    name = [c.name for c in tr.channels if c.name != "sil"][0]
    for c in tr.channels:
        if c.name == name:
            for k in c.keys:
                k.value = round(min(1.0, k.value + 0.2), 4)
    write_json(tr, edited)

    assert cli_main(["diff-edits", base, edited, "-o", edits, "--mode", "offset"]) == 0
    doc = load_edits(edits)
    assert name in doc.channels

    assert cli_main(["naive", "--text", TEXT, "--duration", str(DUR),
                     "--edits", edits, "-o", out]) == 0
    merged, ed = read_json(out), read_json(edited)
    T = np.linspace(0, merged.duration, 50)
    err = np.max(np.abs(sample(_by_name(merged)[name], T)
                        - sample(_by_name(ed)[name], T)))
    assert err < 0.03


def test_cli_without_edits_is_byte_identical(tmp_path):
    a = str(tmp_path / "a.json")
    b = str(tmp_path / "b.json")
    cli_main(["naive", "--text", TEXT, "--duration", str(DUR), "-o", a])
    cli_main(["naive", "--text", TEXT, "--duration", str(DUR), "-o", b])
    assert open(a).read() == open(b).read()
    d = json.load(open(a))
    assert d["version"] == 1 and "source_id" not in d


def test_cli_diff_source_id_keys_sidecar(tmp_path):
    # a WAV-less source id can still be stamped from any file's bytes
    base = str(tmp_path / "base.json")
    edited = str(tmp_path / "edited.json")
    edits = str(tmp_path / "e.edits.json")
    cli_main(["naive", "--text", TEXT, "--duration", str(DUR), "-o", base])
    tr = read_json(base)
    tr.channels[1].keys[0].value = round(min(1.0, tr.channels[1].keys[0].value + 0.3), 4)
    write_json(tr, edited)
    cli_main(["diff-edits", base, edited, "-o", edits, "--source", base])
    doc = load_edits(edits)
    assert doc.source_id == _sha1_source(base)
