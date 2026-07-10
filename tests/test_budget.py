"""Energy-ranked channel-budget reduction (openfacefx.budget, issue #37).

Pins the acceptance: the ranking is deterministic and stable for ties (by channel
name); a cap of N never returns more than N non-empty channels and the jaw +
primary lip visemes survive a speech clip (they are the highest-energy, so the
ranking keeps them naturally); dropped channels are removed entirely, not zeroed;
the energy metadata sums match a straightforward independent recomputation; and
absent the cap the track is returned unchanged (byte-identical).
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

from openfacefx.budget import (budget_channels, budget_metadata, channel_energy,
                               keep_channels, rank_channels)
from openfacefx.cli import main as cli_main
from openfacefx.curves import Channel, FaceTrack, Keyframe
from openfacefx.io_export import from_dict, read_json, to_dict
from openfacefx.gestures import GestureParams
from openfacefx.inspect import POSE_CHANNELS
from openfacefx.pipeline import generate_from_alignment, naive_segments

TEXT, DUR = "hello brave new world", 2.3


def _source(fps=60.0, gestures=False):
    gp = GestureParams(seed=1) if gestures else None
    return from_dict(to_dict(generate_from_alignment(naive_segments(TEXT, DUR),
                                                     fps=fps, gestures=gp)))


def _weight(track):
    return [c for c in track.channels if c.name not in POSE_CHANNELS]


def _pose(track):
    return [c for c in track.channels if c.name in POSE_CHANNELS]


def _recompute_energy(channel):
    v = [k.value for k in channel.keys]
    return sum(abs(v[i + 1] - v[i]) for i in range(len(v) - 1))


# --------------------------------------------------------------------------- #
# 1. ranking deterministic + stable for ties                                  #
# --------------------------------------------------------------------------- #

def test_ranking_deterministic():
    src = _source()
    assert rank_channels(src) == rank_channels(from_dict(to_dict(src)))


def test_ties_broken_by_channel_name():
    t = FaceTrack(60.0, [
        Channel("zebra", [Keyframe(0, 0.0), Keyframe(1, 0.5)]),
        Channel("apple", [Keyframe(0, 0.0), Keyframe(1, 0.5)]),   # equal energy
        Channel("mango", [Keyframe(0, 0.0), Keyframe(1, 0.5)]),
    ], None)
    assert [r["name"] for r in rank_channels(t)] == ["apple", "mango", "zebra"]


def test_ranks_are_contiguous_and_descending():
    ranking = rank_channels(_source())
    assert [r["rank"] for r in ranking] == list(range(len(ranking)))
    energies = [r["energy"] for r in ranking]
    assert energies == sorted(energies, reverse=True)


# --------------------------------------------------------------------------- #
# 2. cap invariant; jaw + primary lips survive                                #
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("n", [1, 3, 5, 8])
def test_cap_never_returns_more_than_n_weight_channels(n):
    src = _source()                                              # viseme-only clip
    capped, _ = budget_channels(src, n)
    assert len(_weight(capped)) == min(n, len(_weight(src)))
    assert all(c.keys for c in capped.channels)                  # all non-empty


def test_cap_is_morph_aware_pose_passes_through_uncounted():
    src = _source(gestures=True)                                 # has head/eye pose
    assert _pose(src)                                            # sanity: pose present
    capped, ranking = budget_channels(src, 6)
    # at most N WEIGHT channels; pose is not counted toward N
    assert len(_weight(capped)) == 6
    # every pose channel passes through UNTOUCHED (byte-identical keys)
    src_pose = {c.name: [(k.time, k.value) for k in c.keys] for c in _pose(src)}
    cap_pose = {c.name: [(k.time, k.value) for k in c.keys] for c in _pose(capped)}
    assert cap_pose == src_pose and set(cap_pose) == set(src_pose)
    # the kept weight channels are the top visemes; pose is not in the ranking
    kept_weight = {c.name for c in _weight(capped)}
    assert "aa" in kept_weight                                   # jaw-open viseme
    assert not any(r["name"] in POSE_CHANNELS for r in ranking)


def test_degree_scale_pose_does_not_outrank_visemes():
    # headPitch's total-variation energy is large purely from its degree units;
    # excluding it from the ranking means it can never evict a viseme.
    src = _source(gestures=True)
    names = [r["name"] for r in rank_channels(src)]
    assert "headPitch" not in names and "eyePitch" not in names
    assert names[0] == "aa"                                      # a viseme leads


def test_jaw_and_primary_lip_visemes_survive_a_speech_clip():
    src = _source()
    cap = len(src.channels) - 2                                  # drop the 2 weakest
    capped, ranking = budget_channels(src, cap)
    kept = {c.name for c in capped.channels}
    # aa is the open-jaw viseme; PP/FF the primary bilabial/labiodental lips
    assert {"aa", "PP", "FF"} <= kept
    # and the dropped ones are strictly the lowest energy
    kept_e = [r["energy"] for r in ranking if r["kept"]]
    dropped_e = [r["energy"] for r in ranking if not r["kept"]]
    assert min(kept_e) >= max(dropped_e)


def test_kept_channels_are_exactly_the_top_n_by_energy():
    src = _source()
    _capped, ranking = budget_channels(src, 5)
    kept = {r["name"] for r in ranking if r["kept"]}
    assert kept == {r["name"] for r in ranking[:5]}


# --------------------------------------------------------------------------- #
# 3. dropped removed entirely, not zeroed                                      #
# --------------------------------------------------------------------------- #

def test_dropped_channels_removed_not_zeroed():
    src = _source()
    capped, ranking = budget_channels(src, 4)
    dropped = {r["name"] for r in ranking if not r["kept"]}
    present = {c.name for c in capped.channels}
    assert dropped and present.isdisjoint(dropped)               # gone, not present
    assert len(present) == 4


# --------------------------------------------------------------------------- #
# 4. energy sums match an independent recompute                                #
# --------------------------------------------------------------------------- #

def test_energy_matches_independent_recompute():
    src = _source()
    by_name = {c.name: c for c in src.channels}
    for r in rank_channels(src):
        assert r["energy"] == pytest.approx(_recompute_energy(by_name[r["name"]]),
                                            abs=1e-6)


def test_channel_energy_is_total_variation():
    c = Channel("x", [Keyframe(0, 0.0), Keyframe(1, 0.8), Keyframe(2, 0.3)])
    assert channel_energy(c) == pytest.approx(0.8 + 0.5)         # |0.8| + |-0.5|
    assert channel_energy(Channel("flat", [Keyframe(0, 0.4)])) == 0.0


# --------------------------------------------------------------------------- #
# 5. absent the cap -> unchanged; metadata; keep_channels                     #
# --------------------------------------------------------------------------- #

def test_no_cap_returns_input_unchanged():
    src = _source()
    same, ranking = budget_channels(src, None)
    assert same is src and to_dict(same) == to_dict(src)
    assert all(r["kept"] for r in ranking)


def test_cap_at_or_above_channel_count_is_byte_identical():
    src = _source()
    same, _ = budget_channels(src, len(src.channels))
    assert to_dict(same) == to_dict(src)
    same2, _ = budget_channels(src, 9999)
    assert to_dict(same2) == to_dict(src)


def test_negative_cap_rejected():
    with pytest.raises(ValueError):
        budget_channels(_source(), -1)


def test_budget_does_not_mutate_source():
    src = _source()
    before = to_dict(src)
    budget_channels(src, 4)
    assert to_dict(src) == before


def test_budget_metadata_shape():
    src = _source()
    _c, ranking = budget_channels(src, 5)
    md = budget_metadata(ranking, 5)
    assert md == json.loads(json.dumps(md))
    assert md["format"] == "openfacefx.budget"
    assert md["max_channels"] == 5
    assert md["kept"] == 5 and md["dropped"] == len(ranking) - 5


def test_keep_channels_preserves_order_and_carries_layers():
    src = _source()
    from openfacefx.events import Event
    src.events = [Event(t=0.5, type="gesture", name="n", dur=0.1, payload={})]
    names = {c.name for c in src.channels[:3]}
    kept = keep_channels(src, names | {"__absent__"})
    assert [c.name for c in kept.channels] == [c.name for c in src.channels
                                               if c.name in names]
    assert kept.events == src.events                             # layer carried


# --------------------------------------------------------------------------- #
# 6. CLI: transform --max-channels + sidecar; lod per-tier                     #
# --------------------------------------------------------------------------- #

def test_cli_transform_max_channels_writes_capped_track_and_sidecar(tmp_path):
    src = str(tmp_path / "clip.json")
    cli_main(["naive", "--text", TEXT, "--duration", str(DUR), "-o", src])
    out = str(tmp_path / "capped.json")
    assert cli_main(["transform", src, "--max-channels", "6", "-o", out]) == 0
    assert len(read_json(out).channels) == 6
    side = json.load(open(tmp_path / "capped.budget.json"))
    assert side["format"] == "openfacefx.budget" and side["max_channels"] == 6
    assert side["kept"] == 6
    # independent recompute of the sidecar energies matches the source track
    by_name = {c.name: c for c in read_json(src).channels}
    for r in side["ranking"]:
        assert r["energy"] == pytest.approx(_recompute_energy(by_name[r["name"]]),
                                            abs=1e-6)


def test_cli_transform_no_max_channels_writes_no_sidecar(tmp_path):
    src = str(tmp_path / "clip.json")
    cli_main(["naive", "--text", TEXT, "--duration", str(DUR), "-o", src])
    out = str(tmp_path / "r.json")
    cli_main(["transform", src, "--retime", "1.0", "-o", out])
    assert not (tmp_path / "r.budget.json").exists()


def test_cli_lod_per_tier_budget_nests_and_records_metadata(tmp_path):
    src = str(tmp_path / "clip.json")
    cli_main(["naive", "--text", TEXT, "--duration", str(DUR), "-o", src])
    assert cli_main(["lod", src, "--max-channels", "11,6,3",
                     "-o", str(tmp_path / "l" / "clip")]) == 0
    meta = json.load(open(tmp_path / "l" / "clip_lod.json"))
    assert "ranking" in meta
    assert [l["max_channels"] for l in meta["levels"]] == [11, 6, 3]
    assert [l["channels"] for l in meta["levels"]] == [11, 6, 3]
    # channel sets nest (each higher LOD is a subset of the lower one)
    sets = [{c.name for c in read_json(str(tmp_path / "l" / f"clip_lod{i}.json"))
             .channels} for i in range(3)]
    assert sets[2] <= sets[1] <= sets[0]


def test_cli_lod_budget_length_mismatch_errors(tmp_path):
    src = str(tmp_path / "clip.json")
    cli_main(["naive", "--text", TEXT, "--duration", str(DUR), "-o", src])
    with pytest.raises(SystemExit):
        cli_main(["lod", src, "--max-channels", "6,3",
                  "-o", str(tmp_path / "x" / "clip")])
