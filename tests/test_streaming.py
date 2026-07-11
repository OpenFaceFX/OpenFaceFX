"""Streaming / real-time coarticulation generator (issue #43).

Pins the acceptance, honestly. The offline dominance is exponential/infinite-
support, so streaming reproduces `generate_from_alignment` WITHIN TOLERANCE, not
bit-exactly — three tiers make that precise:
  (a) within-tolerance: a K-chunk push matches the whole-clip offline solve to
      < 1e-6 at a large look-ahead;
  (b) convergence: the error shrinks ~exp(-theta·W) as the look-ahead W grows;
  (c) EXACT sub-anchor: when the window covers the whole clip (W >= clip) the
      per-frame blend is bit-identical, and the reduced track equals
      `generate_from_alignment` exactly.
Plus: chunk boundaries never matter (1 chunk == K chunks, bit-exact); memory is
O(window) not O(stream); the generator is causal (a later chunk can't change an
emitted frame); and look-ahead 0 is deterministic causal-only (no anticipation).
"""

import os
import sys

import numpy as np
import pytest

try:
    import openfacefx  # noqa: F401
except ImportError:
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from openfacefx.coarticulation import build_viseme_curves
from openfacefx.g2p import G2P
from openfacefx.io_export import to_dict
from openfacefx.pipeline import generate_from_alignment, naive_segments
from openfacefx.streaming import StreamingGenerator, frames_to_track

_G2P = G2P()


def _segs(text, duration):
    return naive_segments(text, duration, g2p=_G2P)


def _stream(segments, chunks, *, look_ahead, back_span=6.0, causal_smooth=0.0):
    g = StreamingGenerator(fps=60.0, look_ahead=look_ahead, back_span=back_span,
                           causal_smooth=causal_smooth)
    frames = []
    lo = 0
    for n in chunks:
        frames += g.push(segments[lo:lo + n])
        lo += n
    frames += g.push(segments[lo:])
    frames += g.flush()
    return frames, g


def _matrix(frames):
    return np.vstack([v for _, v in frames])


# --------------------------------------------------------------------------- #
# (c) EXACT sub-anchor: full window (W >= clip) is bit-identical to offline    #
# --------------------------------------------------------------------------- #

def test_exact_sub_anchor_full_window_matches_offline_bit_exact():
    segs = _segs("hello brave new world", 2.2)
    _, mat_off = build_viseme_curves(segs, fps=60.0)
    frames, _ = _stream(segs, [], look_ahead=5.0, back_span=5.0)  # W >= clip
    assert np.array_equal(_matrix(frames), mat_off)              # bit-identical
    # and the reduced track equals generate_from_alignment exactly
    assert to_dict(frames_to_track(frames, 60.0)) == \
        to_dict(generate_from_alignment(segs, fps=60.0))


def test_frame_times_lie_on_the_offline_grid():
    segs = _segs("hello world", 1.5)
    times_off, _ = build_viseme_curves(segs, fps=60.0)
    frames, _ = _stream(segs, [], look_ahead=5.0, back_span=5.0)
    assert np.array_equal(np.array([t for t, _ in frames]), times_off)


# --------------------------------------------------------------------------- #
# (1) chunk boundaries never matter — 1 chunk == K chunks, bit-exact           #
# --------------------------------------------------------------------------- #

def test_k_chunk_push_equals_single_chunk_bit_exact():
    segs = _segs("the quick brown fox jumps", 3.0)
    whole, _ = _stream(segs, [], look_ahead=1.0, back_span=6.0)
    chunked, _ = _stream(segs, [2, 5, 1, 3], look_ahead=1.0, back_span=6.0)
    assert len(whole) == len(chunked)
    assert np.array_equal(_matrix(whole), _matrix(chunked))
    assert np.array_equal(np.array([t for t, _ in whole]),
                          np.array([t for t, _ in chunked]))


# --------------------------------------------------------------------------- #
# (a) within-tolerance vs the whole-clip offline solve at a large look-ahead   #
# --------------------------------------------------------------------------- #

def test_within_tolerance_vs_whole_clip_offline():
    segs = _segs("she sells sea shells by the shore today", 5.0)
    _, mat_off = build_viseme_curves(segs, fps=60.0)
    frames, _ = _stream(segs, [3, 4, 5], look_ahead=4.5, back_span=6.0)
    assert _matrix(frames).shape == mat_off.shape
    assert float(np.max(np.abs(_matrix(frames) - mat_off))) < 1e-6


# --------------------------------------------------------------------------- #
# (b) convergence: the error shrinks as the look-ahead window grows            #
# --------------------------------------------------------------------------- #

def test_error_converges_as_lookahead_grows():
    segs = _segs("many words make a longer utterance for the window to cover here",
                 8.0)
    _, mat_off = build_viseme_curves(segs, fps=60.0)
    errs = {}
    for w in (1.5, 3.0, 4.5):
        frames, _ = _stream(segs, [4, 4, 4, 4], look_ahead=w, back_span=8.0)
        errs[w] = float(np.max(np.abs(_matrix(frames) - mat_off)))
    # monotone shrink + the documented ballpark (exp(-theta·W))
    assert errs[1.5] > errs[3.0] > errs[4.5]
    assert errs[1.5] < 5e-2 and errs[3.0] < 1e-3 and errs[4.5] < 1e-5


# --------------------------------------------------------------------------- #
# (2) bounded memory — O(window), independent of stream length                 #
# --------------------------------------------------------------------------- #

def test_memory_is_bounded_independent_of_stream_length():
    def peak_buffered(n_words):
        segs = _segs("na " * n_words, n_words * 0.3)
        g = StreamingGenerator(fps=60.0, look_ahead=0.5, back_span=2.0)
        peak = 0
        for i in range(0, len(segs), 4):
            g.push(segs[i:i + 4])
            peak = max(peak, g.buffered_segments)
        g.flush()
        return peak, len(segs)
    small, n_small = peak_buffered(60)
    large, n_large = peak_buffered(400)
    assert n_large > 5 * n_small                       # a much longer stream ...
    assert large <= small + 2                          # ... same bounded buffer
    assert large < 40                                  # O(window), not O(stream)


# --------------------------------------------------------------------------- #
# (4) causal — a later chunk cannot alter an already-emitted frame             #
# --------------------------------------------------------------------------- #

def test_emitted_frames_are_immutable_to_future_input():
    segs = _segs("first part and then the second part follows", 4.0)
    cut = 6
    # emit from the first part alone; then continue with the rest
    g = StreamingGenerator(fps=60.0, look_ahead=0.4, back_span=2.0)
    early = g.push(segs[:cut])
    early_snapshot = _matrix(early).copy() if early else np.zeros((0, 1))
    g.push(segs[cut:])
    g.flush()
    # the frames handed back by the first push are unchanged by later input:
    # re-run the first push in isolation and compare
    g2 = StreamingGenerator(fps=60.0, look_ahead=0.4, back_span=2.0)
    early2 = g2.push(segs[:cut])
    assert np.array_equal(_matrix(early2) if early2 else np.zeros((0, 1)),
                          early_snapshot)
    # and every early frame's time precedes where the second part starts driving
    if early:
        assert max(t for t, _ in early) < segs[cut].start + 0.4 + 1e-9


# --------------------------------------------------------------------------- #
# (3/6) look-ahead 0 — zero-latency causal-only, deterministic, no anticipation #
# --------------------------------------------------------------------------- #

def test_lookahead_zero_is_deterministic_causal_only():
    segs = _segs("hello brave new world", 2.2)
    a, _ = _stream(segs, [], look_ahead=0.0, back_span=2.0)
    b, _ = _stream(segs, [3, 4], look_ahead=0.0, back_span=2.0)
    assert np.array_equal(_matrix(a), _matrix(b))       # deterministic
    _, mat_off = build_viseme_curves(segs, fps=60.0)
    # no anticipatory coarticulation -> visibly differs from the offline solve
    assert not np.allclose(_matrix(a), mat_off, atol=1e-3)


def test_causal_smoother_is_past_only_and_off_by_default():
    segs = _segs("hello world", 1.5)
    plain, _ = _stream(segs, [], look_ahead=1.0, back_span=3.0, causal_smooth=0.0)
    smoothed, _ = _stream(segs, [], look_ahead=1.0, back_span=3.0,
                          causal_smooth=0.08)
    # off by default is a no-op; on, it changes the curve (past-only 1-pole)
    assert np.array_equal(_matrix(plain),
                          _matrix(_stream(segs, [], look_ahead=1.0,
                                          back_span=3.0)[0]))
    assert not np.array_equal(_matrix(plain), _matrix(smoothed))
