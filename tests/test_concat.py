"""concat / sequence — splice finished tracks along a timeline (issue #51).

The sequential complement to #48 `trim`. Pins the acceptance: `concat([a,b])`
lasts `a.duration + b.duration` (plus gaps) and `trim` at the seam reproduces `a`
and the time-shifted `b` within tolerance (concat and trim are seam inverses);
event/variant times land at their segment offset and a `--gap` inserts exactly
that silence and shifts everything after; a channel absent from a segment samples
to `0` across its span (no cross-seam bleed); a single-track `concat([a])` is
byte-identical to `a`; and `crossfade=0` is a pure relabel/offset (no re-thin).
"""

import copy
import os
import sys

import numpy as np
import pytest

try:
    import openfacefx  # noqa: F401  (installed wheel wins; see test_core)
except ImportError:
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from openfacefx.cli import main as cli_main
from openfacefx.curves import Channel, FaceTrack, Keyframe
from openfacefx.edits import sample
from openfacefx.events import Event
from openfacefx.io_export import from_dict, read_json, to_dict, write_json
from openfacefx.pipeline import generate_from_alignment, naive_segments
from openfacefx.transforms import concat, trim


def _rt(track):
    return from_dict(to_dict(track))                  # 4-dp form, as the CLI loads


def _a():
    return _rt(generate_from_alignment(naive_segments("hello world", 1.5), fps=60.0))


def _b():
    return _rt(generate_from_alignment(naive_segments("brave new day", 1.2),
                                       fps=60.0))


def _sampled_diff(x, y, lo, hi):
    """Max abs sampled diff over the interior of [lo, hi] (absent channel = 0),
    away from the seam endpoints (a hard cut is a measure-zero step)."""
    xm = {c.name: c for c in x.channels}
    ym = {c.name: c for c in y.channels}
    T = np.linspace(lo, hi, 60)[1:-1]
    return max((float(np.max(np.abs(
        (sample(xm[n], T) if n in xm else np.zeros(len(T)))
        - (sample(ym[n], T) if n in ym else np.zeros(len(T))))))
        for n in set(xm) | set(ym)), default=0.0)


# --------------------------------------------------------------------------- #
# single-track identity                                                        #
# --------------------------------------------------------------------------- #

def test_single_track_concat_is_byte_identical():
    a = _a()
    out = concat([a])
    assert out is a and to_dict(out) == to_dict(a)


def test_cli_single_file_sequence_byte_identical(tmp_path):
    a = str(tmp_path / "a.json")
    write_json(_a(), a)
    out = str(tmp_path / "s.json")
    assert cli_main(["sequence", a, "-o", out]) == 0
    assert open(a, "rb").read() == open(out, "rb").read()


# --------------------------------------------------------------------------- #
# duration + trim-at-seam inverse                                              #
# --------------------------------------------------------------------------- #

def test_duration_is_sum_plus_gaps():
    a, b = _a(), _b()
    assert concat([a, b]).duration == pytest.approx(a.duration + b.duration)
    assert concat([a, b], gaps=[0.5]).duration == pytest.approx(
        a.duration + b.duration + 0.5)
    assert concat([a, b, a], gaps=0.25).duration == pytest.approx(
        2 * a.duration + b.duration + 0.5)


def test_trim_at_seam_reproduces_segments():
    a, b = _a(), _b()
    c = concat([a, b])
    head = trim(c, 0.0, a.duration)
    tail = trim(c, a.duration, c.duration)            # already rebased to 0 by trim
    assert _sampled_diff(head, a, 0.0, a.duration) < 1e-6
    assert _sampled_diff(tail, b, 0.0, b.duration) < 1e-6


def test_trim_at_seam_inverse_with_gap():
    a, b = _a(), _b()
    c = concat([a, b], gaps=[0.4])
    assert _sampled_diff(trim(c, 0.0, a.duration), a, 0.0, a.duration) < 1e-6
    tail = trim(c, a.duration + 0.4, c.duration)
    assert _sampled_diff(tail, b, 0.0, b.duration) < 1e-6


# --------------------------------------------------------------------------- #
# events / variants offset + gap shift                                        #
# --------------------------------------------------------------------------- #

def test_event_times_land_at_segment_offset_with_gap():
    a, b = _a(), _b()
    a.events = [Event(t=0.5, type="gesture", name="n", dur=0.1, payload={})]
    b.events = [Event(t=0.3, type="emphasis", name="e", dur=0.0, payload={})]
    c = concat([a, b], gaps=[0.5])
    times = sorted(round(e.t, 4) for e in c.events)
    assert times == [0.5, round(a.duration + 0.5 + 0.3, 4)]


def test_variants_offset_by_segment_start():
    from openfacefx.events import Alternative, VariantGroup, Variants
    a, b = _a(), _b()
    b.variants = Variants("line", [VariantGroup("g", [Alternative(
        1.0, [Event(t=0.2, type="gesture", name="x", dur=0.0, payload={})])])])
    c = concat([a, b])
    ev = c.variants.groups[0].alternatives[0].events[0]
    assert ev.t == pytest.approx(a.duration + 0.2)


# --------------------------------------------------------------------------- #
# channel union: absent -> 0, no bleed                                        #
# --------------------------------------------------------------------------- #

def test_channel_union_absent_samples_to_zero_no_bleed():
    A = FaceTrack(60.0, [Channel("X", [Keyframe(0.0, 0.2), Keyframe(1.0, 0.9)])],
                  ["X"])
    B = FaceTrack(60.0, [Channel("Y", [Keyframe(0.0, 0.3), Keyframe(0.8, 0.7)])],
                  ["Y"])
    c = concat([A, B])
    m = {ch.name: ch for ch in c.channels}
    assert set(m) == {"X", "Y"}
    # X (absent in B) is 0 across B's span [1.0, 1.8] — no bleed of X's 0.9
    xb = sample(m["X"], np.linspace(1.05, 1.75, 20))
    assert float(np.max(np.abs(xb))) < 1e-9
    # Y (absent in A) is 0 across A's span [0, 1.0]
    ya = sample(m["Y"], np.linspace(0.05, 0.95, 20))
    assert float(np.max(np.abs(ya))) < 1e-9
    # and each channel keeps its own values in its own segment
    # X: (0,0.2)->(1.0,0.9), so at t=0.5 -> 0.2 + 0.5*0.7 = 0.55
    assert sample(m["X"], np.array([0.5]))[0] == pytest.approx(0.55)


# --------------------------------------------------------------------------- #
# crossfade                                                                    #
# --------------------------------------------------------------------------- #

def test_crossfade_zero_is_pure_offset_no_rethin():
    a, b = _a(), _b()
    c = concat([a, b])                                # crossfade default 0
    m = {ch.name: ch for ch in c.channels}
    # every one of a's keys survives verbatim (offset 0); no RDP re-thinning
    for ch in a.channels:
        got = {(round(k.time, 4), round(k.value, 4)) for k in m[ch.name].keys}
        want = {(round(k.time, 4), round(k.value, 4)) for k in ch.keys}
        assert want <= got


def test_crossfade_smooths_the_seam():
    a, b = _a(), _b()
    hard = concat([a, b])
    soft = concat([a, b], crossfade=0.15)
    assert to_dict(soft)                              # valid track
    # the crossfaded track differs from the hard cut near the seam
    assert to_dict(soft) != to_dict(hard)


# --------------------------------------------------------------------------- #
# determinism, validation, CLI                                                #
# --------------------------------------------------------------------------- #

def test_concat_is_deterministic():
    a, b = _a(), _b()
    assert to_dict(concat([a, b], gaps=[0.3])) == \
        to_dict(concat([copy.deepcopy(a), copy.deepcopy(b)], gaps=[0.3]))


def test_bad_args_rejected():
    a, b = _a(), _b()
    with pytest.raises(ValueError):
        concat([])
    with pytest.raises(ValueError):
        concat([a, b], gaps=[0.1, 0.2])               # wrong gap count
    with pytest.raises(ValueError):
        concat([a, b], gaps=[-1.0])                   # negative gap
    with pytest.raises(ValueError):
        concat([a, b], crossfade=-0.1)


def test_cli_sequence_gap_and_duration(tmp_path):
    a, b = str(tmp_path / "a.json"), str(tmp_path / "b.json")
    write_json(_a(), a)
    write_json(_b(), b)
    out = str(tmp_path / "seq.json")
    assert cli_main(["sequence", a, b, "--gap", "0.5", "-o", out]) == 0
    assert read_json(out).duration == pytest.approx(_a().duration + _b().duration
                                                    + 0.5, abs=1e-4)
