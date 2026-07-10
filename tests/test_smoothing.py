"""Post-solve curve conditioning (issue #10): temporal smoothing + lag/lead.

The contract mirrors the intensity dials: OFF by default, byte-identical output,
deterministic, numpy + stdlib only. The load-bearing invariants are (1) defaults
change nothing, (2) smoothing softens jitter while the coarticulation partition-
energy sum (~1 per frame) survives, (3) enforced lip closures stay sharp through
smoothing (PP >= 0.89), and (4) lag slides keyframe times and clamps into the
clip.
"""

import json
import os
import sys
import wave

import numpy as np
import pytest

try:
    import openfacefx  # noqa: F401  (installed wheel wins; see test_core)
except ImportError:
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from openfacefx import generate_from_alignment, to_dict
from openfacefx.alignment import PhonemeSegment
from openfacefx.coarticulation import CoartParams, build_viseme_curves
from openfacefx.curves import Channel, FaceTrack, Keyframe
from openfacefx.pipeline import naive_segments
from openfacefx.postprocess import smooth_matrix, time_shift
from openfacefx.visemes import VISEMES

ROOT = os.path.join(os.path.dirname(__file__), "..")
VOICE = os.path.join(ROOT, "examples", "voice.wav")


# --------------------------------------------------------------------------- #
# byte-identity at defaults -- the key acceptance criterion
# --------------------------------------------------------------------------- #
def test_smoothing_lag_defaults_are_byte_identical():
    # smooth 0 + lag 0 must be a behavioural no-op: params=None, a default
    # CoartParams, and an explicit smooth=0/lag=0 all produce the same track.
    segs = naive_segments("the quick brown fox jumps", duration=2.5)
    none_ = to_dict(generate_from_alignment(segs))
    default = to_dict(generate_from_alignment(segs, params=CoartParams()))
    explicit = to_dict(generate_from_alignment(
        segs, params=CoartParams(smooth=0.0, lag=0.0)))
    assert none_ == default == explicit


def test_smooth_matrix_sigma_zero_is_noop():
    # the function itself is a byte-identical no-op at sigma 0 (returns input).
    m = np.random.default_rng(1).random((40, 6))
    assert smooth_matrix(m, 0.0, 60.0) is m
    assert np.array_equal(smooth_matrix(m, -1.0, 60.0), m)


def test_time_shift_zero_is_noop():
    tk = _track()
    before = [[(k.time, k.value) for k in c.keys] for c in tk.channels]
    time_shift(tk, 0.0)
    after = [[(k.time, k.value) for k in c.keys] for c in tk.channels]
    assert before == after


# --------------------------------------------------------------------------- #
# smoothing softens jitter, keeps the partition invariant
# --------------------------------------------------------------------------- #
def test_smooth_matrix_reduces_frame_variance_on_jittery_track():
    rng = np.random.default_rng(0)
    ramp = np.repeat(np.linspace(0.0, 1.0, 20), 6)[:, None]
    jittery = np.clip(ramp + rng.normal(0.0, 0.15, (ramp.shape[0], 1)), 0.0, 1.0)
    smoothed = smooth_matrix(jittery, sigma=0.03, fps=60.0)

    tv = lambda a: np.abs(np.diff(a[:, 0])).sum()
    assert tv(smoothed) < 0.4 * tv(jittery)             # total variation slashed
    assert np.std(np.diff(smoothed[:, 0])) < np.std(np.diff(jittery[:, 0]))
    assert smoothed.min() >= 0.0 and smoothed.max() <= 1.0   # stays in-range


def test_smooth_matrix_preserves_partition_energy():
    # a normalized partition (rows sum to 1) still sums to ~1 after smoothing,
    # because the kernel is a unit-sum partition of unity with edge-hold padding.
    rng = np.random.default_rng(7)
    m = rng.random((50, 5))
    m /= m.sum(axis=1, keepdims=True)
    sm = smooth_matrix(m, sigma=0.05, fps=60.0)
    assert np.allclose(sm.sum(axis=1), 1.0, atol=1e-9)


def test_smoothing_keeps_curves_bounded_and_partitioned():
    # the coarticulation partition-energy invariant (test_core mirror) survives
    # smoothing: build with a strong sigma and check rows still sum to ~1.
    segs = naive_segments("the quick brown fox", duration=2.0)
    _, m = build_viseme_curves(segs, fps=60, params=CoartParams(smooth=0.04))
    assert m.min() >= 0.0 and m.max() <= 1.0
    assert np.allclose(m.sum(axis=1), 1.0, atol=2e-3)


def test_smoothing_reduces_variation_of_built_curves():
    # end to end: a smoothed build has lower total variation than the raw build
    # on the busiest channel, i.e. the post-process actually smooths.
    segs = naive_segments("the quick brown fox jumps over", duration=2.0)
    _, raw = build_viseme_curves(segs, fps=60)
    _, sm = build_viseme_curves(segs, fps=60, params=CoartParams(smooth=0.04))
    v = int(np.argmax(np.abs(np.diff(raw, axis=0)).sum(axis=0)))   # busiest col
    assert np.abs(np.diff(sm[:, v])).sum() < np.abs(np.diff(raw[:, v])).sum()


# --------------------------------------------------------------------------- #
# closure protection: bilabial/labiodental seals stay sharp through smoothing
# --------------------------------------------------------------------------- #
def test_smoothing_preserves_bilabial_closure_peak():
    # /p/ between two open vowels: the PP seal reaches the closure floor and must
    # stay there even with heavy smoothing, because closures are re-enforced
    # AFTER the filter. Verify the >= 0.89 peak the team asked for, plus the
    # partition invariant at the sealed frame.
    segs = [PhonemeSegment("AA", 0.0, 0.25), PhonemeSegment("P", 0.25, 0.33),
            PhonemeSegment("AA", 0.33, 0.6)]
    pp = VISEMES.index("PP")
    times, m = build_viseme_curves(segs, fps=120, params=CoartParams(smooth=0.03))
    mid = int(np.argmin(np.abs(times - 0.29)))
    assert m[mid, pp] >= 0.89, m[mid, pp]
    assert np.allclose(m.sum(axis=1), 1.0, atol=2e-3)


def test_closure_protection_is_load_bearing():
    # Without the re-enforce-after-smooth ordering the seal would wash out:
    # smoothing the already-enforced matrix directly drags PP well below the
    # floor. This is exactly what the in-pipeline ordering prevents.
    segs = [PhonemeSegment("AA", 0.0, 0.25), PhonemeSegment("P", 0.25, 0.33),
            PhonemeSegment("AA", 0.33, 0.6)]
    pp = VISEMES.index("PP")
    times, enforced = build_viseme_curves(segs, fps=120)      # PP == floor
    mid = int(np.argmin(np.abs(times - 0.29)))
    naive_smoothed = smooth_matrix(enforced, sigma=0.03, fps=120.0)
    assert naive_smoothed[mid, pp] < 0.89                     # seal lost...
    _, protected = build_viseme_curves(
        segs, fps=120, params=CoartParams(smooth=0.03))
    assert protected[mid, pp] >= 0.89                         # ...but not in-pipe


def test_labiodental_closure_also_protected():
    # /f/ (FF, also a lips-class closure) seals through smoothing just like /p/.
    segs = [PhonemeSegment("AA", 0.0, 0.25), PhonemeSegment("F", 0.25, 0.35),
            PhonemeSegment("AA", 0.35, 0.6)]
    ff = VISEMES.index("FF")
    times, m = build_viseme_curves(segs, fps=120, params=CoartParams(smooth=0.03))
    mid = int(np.argmin(np.abs(times - 0.30)))
    assert m[mid, ff] >= 0.89, m[mid, ff]


# --------------------------------------------------------------------------- #
# lag / lead time-shift
# --------------------------------------------------------------------------- #
def _track():
    return FaceTrack(fps=60, channels=[
        Channel("aa", [Keyframe(0.0, 0.0), Keyframe(0.5, 0.8), Keyframe(1.0, 0.0)]),
        Channel("PP", [Keyframe(0.2, 0.9), Keyframe(0.9, 0.1), Keyframe(1.0, 0.0)]),
    ])


def test_time_shift_lag_moves_keys_and_clamps_at_duration():
    tk = _track()
    dur = tk.duration
    time_shift(tk, 0.05)                       # +50 ms lag
    aa = tk.channels[0].keys
    assert aa[0].time == pytest.approx(0.05)   # interior keys move by delta
    assert aa[1].time == pytest.approx(0.55)
    assert aa[2].time == pytest.approx(dur)    # end key clamps at duration
    assert tk.duration == pytest.approx(dur)   # clip not stretched


def test_time_shift_lead_clamps_at_zero():
    tk = _track()
    time_shift(tk, -0.05)                       # -50 ms lead
    aa = tk.channels[0].keys
    assert aa[0].time == 0.0                    # start key clamps at 0
    assert aa[1].time == pytest.approx(0.45)
    assert aa[2].time == pytest.approx(0.95)


def test_time_shift_per_channel_leaves_others_and_duration():
    # issue #10 criterion: shifting ONE channel moves only its keys, leaves the
    # other channels' keyframes untouched, and does not alter track duration.
    tk = _track()
    dur = tk.duration
    others_before = [(k.time, k.value) for k in tk.channels[1].keys]
    time_shift(tk, 0.05, channels=["aa"])
    assert [(k.time, k.value) for k in tk.channels[1].keys] == others_before
    assert tk.duration == pytest.approx(dur)
    assert tk.channels[0].keys[1].time == pytest.approx(0.55)


def test_time_shift_keeps_keys_sorted_and_deduped():
    # two keys near the end must not produce out-of-order / duplicate times.
    tk = FaceTrack(fps=60, channels=[
        Channel("aa", [Keyframe(0.9, 0.2), Keyframe(0.98, 0.5), Keyframe(1.0, 0.1)]),
    ])
    time_shift(tk, 0.1)                          # push the tail past duration
    keys = tk.channels[0].keys
    ts = [k.time for k in keys]
    assert ts == sorted(ts)
    assert len(ts) == len(set(ts))               # collapsed the boundary pile-up
    assert ts[-1] == pytest.approx(1.0)


def test_lag_in_pipeline_shifts_visemes():
    segs = naive_segments("hello world", duration=1.5)
    base = generate_from_alignment(segs)
    lagged = generate_from_alignment(segs, params=CoartParams(lag=0.05))
    assert to_dict(base) != to_dict(lagged)      # lag is not a no-op when set
    # every keyframe time is within the clip and sorted after the shift
    for c in lagged.channels:
        ts = [k.time for k in c.keys]
        assert ts == sorted(ts)
        assert all(0.0 <= t <= lagged.duration + 1e-9 for t in ts)


# --------------------------------------------------------------------------- #
# determinism (this file is also run under 3.9 and 3.13 in CI)
# --------------------------------------------------------------------------- #
def test_smoothing_and_lag_are_deterministic():
    segs = naive_segments("the quick brown fox", duration=2.0)
    p = CoartParams(smooth=0.03, lag=0.02)
    assert to_dict(generate_from_alignment(segs, params=p)) == to_dict(
        generate_from_alignment(segs, params=p))
    m = np.random.default_rng(3).random((30, 6))
    assert np.array_equal(smooth_matrix(m, 0.03, 60.0),
                          smooth_matrix(m, 0.03, 60.0))


# --------------------------------------------------------------------------- #
# CLI wiring
# --------------------------------------------------------------------------- #
def test_cli_smooth_lag_parse_errors(tmp_path):
    from openfacefx.cli import main as cli_main
    out = str(tmp_path / "o.json")
    base = ["naive", "--text", "hi", "--duration", "0.5", "-o", out]
    for bad in (["--smooth", "-0.1"],       # negative sigma
                ["--smooth", "nan"],        # non-finite
                ["--lag", "inf"]):          # non-finite
        with pytest.raises(SystemExit):
            cli_main(base + bad)
    assert cli_main(base + ["--smooth", "0.02", "--lag", "20"]) == 0
    assert os.path.exists(out)


def test_cli_smooth_lag_defaults_byte_identical(tmp_path):
    from openfacefx.cli import main as cli_main
    a = str(tmp_path / "a.json")
    b = str(tmp_path / "b.json")
    argv = ["naive", "--text", "the quick brown fox", "--duration", "2.0"]
    assert cli_main(argv + ["-o", a]) == 0
    assert cli_main(argv + ["--smooth", "0", "--lag", "0", "-o", b]) == 0
    assert open(a, encoding="utf-8").read() == open(b, encoding="utf-8").read()


def test_cli_smooth_lag_change_output(tmp_path):
    from openfacefx.cli import main as cli_main
    a = str(tmp_path / "a.json")
    b = str(tmp_path / "b.json")
    argv = ["naive", "--text", "the quick brown fox", "--duration", "2.0"]
    cli_main(argv + ["-o", a])
    cli_main(argv + ["--smooth", "0.03", "--lag", "25", "-o", b])
    assert open(a, encoding="utf-8").read() != open(b, encoding="utf-8").read()


@pytest.mark.skipif(not os.path.exists(VOICE), reason="examples/voice.wav absent")
def test_energy_smooth_lag_wired(tmp_path):
    # energy mode is the non-coarticulation path; --smooth/--lag still apply and
    # default to a byte-identical no-op there too.
    from openfacefx.energy import generate_from_energy
    base = to_dict(generate_from_energy(VOICE, fps=60))
    noop = to_dict(generate_from_energy(VOICE, fps=60, smooth=0.0, lag=0.0))
    cond = to_dict(generate_from_energy(VOICE, fps=60, smooth=0.03, lag=0.02))
    assert base == noop
    assert base != cond
    for c in cond["channels"]:                    # still a valid, sorted track
        ts = [k[0] for k in c["keys"]]
        assert ts == sorted(ts)
        assert all(0.0 <= v <= 1.0 for _, v in c["keys"])
