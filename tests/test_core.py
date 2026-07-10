"""Core tests. Run with:  python -m pytest  (or)  python tests/test_core.py"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import numpy as np

from openfacefx import (
    G2P, NaiveAligner, PhonemeSegment, phoneme_to_viseme,
    build_viseme_curves, generate_naive, to_dict,
)
from openfacefx.visemes import VISEMES


def test_phoneme_to_viseme_groups_bilabials():
    assert phoneme_to_viseme("P") == "PP"
    assert phoneme_to_viseme("B") == "PP"
    assert phoneme_to_viseme("M") == "PP"


def test_stress_is_stripped():
    assert phoneme_to_viseme("AA1") == phoneme_to_viseme("AA0") == "aa"


def test_g2p_known_and_oov():
    g = G2P()
    assert g.word("hello") == ["HH", "AH0", "L", "OW1"]
    # OOV word still returns *some* phonemes, never empty
    assert len(g.word("zqxblorp")) > 0


def test_naive_aligner_covers_full_span_in_order():
    segs = NaiveAligner().align(["HH", "AH0", "L", "OW1"], total_duration=1.0)
    assert abs(segs[0].start - 0.0) < 1e-9
    assert abs(segs[-1].end - 1.0) < 1e-6
    # monotonic, non-overlapping
    for a, b in zip(segs, segs[1:]):
        assert abs(a.end - b.start) < 1e-9


def test_curves_are_bounded_and_partition_energy():
    segs = NaiveAligner().align(["P", "AA1", "T"], total_duration=0.6)
    times, m = build_viseme_curves(segs, fps=60)
    assert m.min() >= 0.0 and m.max() <= 1.0
    # dominance-weighted average => each frame's channels sum to ~1
    row_sums = m.sum(axis=1)
    assert np.allclose(row_sums, 1.0, atol=1e-6)


def test_pipeline_produces_valid_track():
    track = generate_naive("the quick brown fox", duration=2.0, fps=60)
    d = to_dict(track)
    assert d["format"] == "openfacefx.track"
    assert d["channels"], "expected at least one active channel"
    # PP (from 'brown' b) and aa vowels should appear
    names = {c["name"] for c in d["channels"]}
    assert "PP" in names or "aa" in names


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print("PASS", fn.__name__)
    print(f"\n{len(fns)} tests passed")
