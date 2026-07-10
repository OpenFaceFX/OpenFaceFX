"""Offline LOD variant export (openfacefx.lod, issue #36).

Pins the acceptance: one solve yields K deterministic variants and **LOD0 is
byte-identical to the source at the same epsilon**; higher tiers carry a
monotonically non-increasing keyframe count; an fps-resample tier lands keys ONLY
on the coarse grid while a pure-RDP tier never invents a key (always a subset of
the source); the metadata sidecar round-trips through JSON and names each
variant's epsilon + fps; and the source track is never mutated (purely additive).
"""

import copy
import json
import os
import sys

import pytest

try:
    import openfacefx  # noqa: F401  (installed wheel wins; see test_core)
except ImportError:
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from openfacefx.cli import main as cli_main
from openfacefx.events import Event
from openfacefx.io_export import from_dict, read_json, to_dict
from openfacefx.lod import (LOD_DEFAULT_FPS, LOD_DEFAULT_RDP, generate_lods,
                            lod_metadata, make_lod, switching_table)
from openfacefx.pipeline import generate_from_alignment, naive_segments

TEXT, DUR = "hello brave new world", 2.3


def _source(fps=60.0, events=False):
    t = generate_from_alignment(naive_segments(TEXT, DUR), fps=fps)
    if events:
        t.events = [Event(t=0.5, type="gesture", name="nod", dur=0.2, payload={})]
    return from_dict(to_dict(t))                      # 4-dp form, as `lod` loads it


def _keycount(track):
    return sum(len(c.keys) for c in track.channels)


# --------------------------------------------------------------------------- #
# 1. LOD0 byte-identical; K deterministic variants                            #
# --------------------------------------------------------------------------- #

def test_lod0_byte_identical_to_source_at_same_epsilon():
    src = _source()
    # source was solved at the default epsilon 0.015; LOD0 at 0.015 (pure RDP)
    variants, _ = generate_lods(src, rdp=[0.015, 0.03, 0.06], fps=[60, 60, 60])
    assert to_dict(variants[0]) == to_dict(src)
    # a finer LOD0 epsilon than the source keeps every key too
    variants2, _ = generate_lods(src, rdp=[0.002, 0.01, 0.04], fps=[60, 30, 15])
    assert to_dict(variants2[0]) == to_dict(src)


def test_lod0_carries_events():
    src = _source(events=True)
    variants, _ = generate_lods(src, rdp=[0.015], fps=[60])
    assert to_dict(variants[0]) == to_dict(src)       # events preserved verbatim


def test_generate_lods_is_deterministic():
    src = _source()
    a, _ = generate_lods(src)
    b, _ = generate_lods(copy.deepcopy(src))
    assert [to_dict(v) for v in a] == [to_dict(v) for v in b]


def test_default_tiers_yield_k_variants():
    src = _source()
    variants, levels = generate_lods(src)
    assert len(variants) == len(LOD_DEFAULT_RDP) == len(LOD_DEFAULT_FPS)
    assert [f for _e, f in levels] == LOD_DEFAULT_FPS


# --------------------------------------------------------------------------- #
# 2. monotonic keyframe counts                                                #
# --------------------------------------------------------------------------- #

def test_higher_tiers_have_monotonic_non_increasing_keyframes():
    src = _source()
    variants, _ = generate_lods(src)
    counts = [_keycount(v) for v in variants]
    assert counts[0] == _keycount(src)                # LOD0 is the full clip
    assert all(counts[i] >= counts[i + 1] for i in range(len(counts) - 1)), counts


def test_rdp_only_tiers_monotonic():
    src = _source()
    variants, _ = generate_lods(src, rdp=[0.002, 0.02, 0.1], fps=[60, 60, 60])
    counts = [_keycount(v) for v in variants]
    assert all(counts[i] >= counts[i + 1] for i in range(len(counts) - 1)), counts


# --------------------------------------------------------------------------- #
# 3. fps tier lands on the coarse grid; RDP tier never invents keys           #
# --------------------------------------------------------------------------- #

def test_fps_resample_tier_keys_land_only_on_the_coarse_grid():
    src = _source(fps=60.0)
    variants, levels = generate_lods(src, rdp=[0.01, 0.01], fps=[30, 15])
    for variant, (_eps, fps) in zip(variants, levels):
        assert variant.fps == fps
        for c in variant.channels:
            for k in c.keys:
                assert abs(k.time * fps - round(k.time * fps)) < 1e-9


def test_pure_rdp_tier_never_invents_keys():
    src = _source()
    src_times = {c.name: {k.time for k in c.keys} for c in src.channels}
    variants, _ = generate_lods(src, rdp=[0.03], fps=[60])       # fps == source
    for c in variants[0].channels:
        assert all(k.time in src_times[c.name] for k in c.keys)


def test_fps_above_source_is_pure_rdp_not_upsampled():
    src = _source(fps=30.0)
    variants, _ = generate_lods(src, rdp=[0.015], fps=[120])     # capped at 30
    assert variants[0].fps == 30.0
    assert to_dict(variants[0]) == to_dict(src)                  # eps 0.015 == source


# --------------------------------------------------------------------------- #
# 4. metadata sidecar                                                         #
# --------------------------------------------------------------------------- #

def test_metadata_round_trips_and_names_epsilon_and_fps():
    src = _source()
    variants, levels = generate_lods(src)
    files = [f"clip_lod{i}.json" for i in range(len(variants))]
    meta = lod_metadata(src, levels, variants, files)
    assert meta == json.loads(json.dumps(meta))                 # JSON round-trip
    assert meta["format"] == "openfacefx.lod"
    assert [l["epsilon"] for l in meta["levels"]] == [e for e, _f in levels]
    assert [l["fps"] for l in meta["levels"]] == [v.fps for v in variants]
    assert [l["keyframes"] for l in meta["levels"]] == [_keycount(v)
                                                        for v in variants]
    assert len(meta["switching"]) == len(variants)
    assert meta["switching"][-1]["min_screen_height"] == 0.0     # fallback level


def test_switching_table_is_advisory_and_descending():
    tbl = switching_table(3)
    heights = [e["min_screen_height"] for e in tbl]
    assert heights == sorted(heights, reverse=True)
    assert [e["lod"] for e in tbl] == [0, 1, 2]


# --------------------------------------------------------------------------- #
# 5. additive / validation / variants are well-formed                         #
# --------------------------------------------------------------------------- #

def test_generate_lods_does_not_mutate_source():
    src = _source(events=True)
    before = to_dict(src)
    generate_lods(src)
    assert to_dict(src) == before                               # source untouched


def test_variants_validate_and_round_trip():
    from openfacefx.inspect import validate_asset
    src = _source()
    for v in generate_lods(src)[0]:
        d = to_dict(v)
        assert to_dict(from_dict(d)) == d
        assert not [p for p in validate_asset(d)[1] if p["severity"] == "error"]


def test_bad_tier_specs_raise():
    src = _source()
    with pytest.raises(ValueError):
        generate_lods(src, rdp=[0.01, 0.02], fps=[60])          # length mismatch
    with pytest.raises(ValueError):
        generate_lods(src, rdp=[-0.01], fps=[60])               # negative epsilon
    with pytest.raises(ValueError):
        generate_lods(src, rdp=[0.01], fps=[0])                 # non-positive fps


# --------------------------------------------------------------------------- #
# 6. CLI                                                                        #
# --------------------------------------------------------------------------- #

def test_cli_lod_writes_variants_and_metadata(tmp_path):
    src = str(tmp_path / "clip.json")
    cli_main(["naive", "--text", TEXT, "--duration", str(DUR), "-o", src])
    assert cli_main(["lod", src, "-o", str(tmp_path / "out" / "clip")]) == 0
    out = tmp_path / "out"
    assert (out / "clip_lod0.json").exists()
    assert (out / "clip_lod1.json").exists()
    assert (out / "clip_lod2.json").exists()
    meta = json.load(open(out / "clip_lod.json"))
    assert meta["format"] == "openfacefx.lod" and len(meta["levels"]) == 3
    # LOD0 file matches the source byte-for-byte when the epsilons match
    eqdir = tmp_path / "eq"
    cli_main(["lod", src, "--rdp", "0.015,0.03", "--fps", "60,60",
              "-o", str(eqdir / "clip")])
    assert open(src, "rb").read() == open(eqdir / "clip_lod0.json", "rb").read()


def test_cli_lod_csv_format(tmp_path):
    src = str(tmp_path / "clip.json")
    cli_main(["naive", "--text", TEXT, "--duration", str(DUR), "-o", src])
    assert cli_main(["lod", src, "--format", "csv",
                     "-o", str(tmp_path / "c" / "clip")]) == 0
    assert (tmp_path / "c" / "clip_lod0.csv").exists()
    assert (tmp_path / "c" / "clip_lod.json").exists()          # metadata still json
