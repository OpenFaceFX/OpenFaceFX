"""Streaming / real-time generation (issue #43): a long-lived coarticulation
generator that carries state across pushed chunks in bounded memory.

The offline pipeline solves a whole clip at once. A live pipeline (a TTS engine
emitting phonemes as it speaks) needs to emit animation *incrementally*, with
constant memory regardless of stream length. :class:`StreamingGenerator` does
that: ``push(chunk_of_segments)`` returns the keyframe frames that just became
final, ``flush()`` emits the tail. It reuses the **exact** offline component math
— :func:`openfacefx.coarticulation._blend` (the shared core of
``build_viseme_curves``) — over a bounded segment **window**, so the streamed
frames reproduce the offline solve.

**Honesty — reproduces the offline solve WITHIN TOLERANCE, not bit-exactly.**
The coarticulation dominance is a Laplacian bump ``D_i(t) = alpha·exp(-theta·|t −
c_i|)`` (``coarticulation.py``), **exponential/infinite support**, and the blend
normalizes over *every* segment. Bounded memory (pruning old segments) and a
finite look-ahead (dropping far-future ones) therefore both omit exponentially
small tails, so **no finite window is bit-identical** to
``generate_from_alignment`` — that is fundamental to this dominance model, not an
implementation gap. It converges fast, though: the per-frame error from a window
``W`` seconds wide is bounded by ``O(exp(-theta·W))`` (slowest θ≈2.9/s for a long
vowel), so W≈1.5 s → ~1e‑2 (≈ the RDP epsilon), W≈3 s → ~1e‑4 (≈ the 4‑dp keyframe
grid), W≈4.5 s → ~1e‑6. **``look_ahead`` is the single latency↔fidelity dial**:
``0`` = zero latency / no anticipatory coarticulation (causal only); larger = more
anticipation, tighter to offline. There is one exact case: when the window covers
the whole clip (``look_ahead`` and ``back_span`` ≥ clip duration) the per-frame
blend is **bit-identical** to offline. Chunk boundaries never matter — the same
clip pushed in 1 or K chunks yields **bit-identical** frames (windows are selected
by frame time, not by arrival).

Causal only: a frame, once emitted, is never revised, and its value depends only
on inputs within its window — a later chunk cannot alter an already-emitted
keyframe (that immutability is the flip side of the tolerance). The optional
``causal_smooth`` is a **past-only** one-pole filter, distinct from the offline
symmetric :func:`openfacefx.postprocess.smooth_matrix` (which reads both ways and
so is not reproducible causally). Network transport is out of scope — this is an
in-process generator.
"""

from __future__ import annotations

from dataclasses import replace
from typing import List, Optional, Tuple

import numpy as np

from .alignment import PhonemeSegment
from .coarticulation import CoartParams, _DIPHTHONGS, _blend
from .curves import FaceTrack, reduce_to_track
from .phonemes import SILENCE, strip_stress
from .visemes import VISEMES

#: One emitted frame: ``(time_seconds, values[n_targets])`` on the fps grid.
Frame = Tuple[float, np.ndarray]

#: The look-ahead dial default (seconds) — a modest real-time latency; raise it
#: toward the clip length to track the offline solve within ~1e-6 (see the
#: module docstring's exp(-theta·W) bound). ``back_span`` (past window) costs
#: only memory, so its default is generous.
DEFAULT_LOOK_AHEAD = 0.5
DEFAULT_BACK_SPAN = 2.0


class StreamingGenerator:
    """Incremental coarticulation over pushed chunks with O(window) memory.

    ``push(segments)`` cooks the new :class:`~openfacefx.alignment.PhonemeSegment`
    chunk (the same silence-absorb + diphthong-split ``_preprocess`` does, applied
    causally) and returns the frames now covered by the look-ahead; ``flush()``
    (alias ``close()``) emits the tail. ``look_ahead`` / ``back_span`` are the
    future / past window extents in seconds; ``causal_smooth`` is a past-only
    one-pole time constant (0 = off). Reuse :func:`frames_to_track` to assemble
    the emitted frames into a :class:`~openfacefx.curves.FaceTrack`."""

    def __init__(self, fps: float = 60.0, mapping=None,
                 params: Optional[CoartParams] = None, *,
                 look_ahead: float = DEFAULT_LOOK_AHEAD,
                 back_span: float = DEFAULT_BACK_SPAN,
                 causal_smooth: float = 0.0) -> None:
        self.fps = float(fps)
        self.mapping = mapping
        self.params = params or CoartParams()
        # streaming does its own (causal) conditioning: never the offline
        # symmetric smoother, so strip it from the blend params.
        self._bp = replace(self.params, smooth=0.0)
        self.look_ahead = max(float(look_ahead), 0.0)
        self.back_span = max(float(back_span), 0.0)
        self.causal_smooth = max(float(causal_smooth), 0.0)
        self.target_names = (list(mapping.target_names) if mapping is not None
                             else list(VISEMES))
        self.n_targets = len(self.target_names)
        # cooking state (mirrors coarticulation._preprocess, incrementally)
        self._raw_tail: Optional[PhonemeSegment] = None
        self._raw_idx = -1
        self._next_idx = 0
        self._pending: Optional[PhonemeSegment] = None    # open cooked segment
        self._cooked: List[PhonemeSegment] = []           # bounded ring buffer
        self._cooked_end = 0.0
        # grid + emission
        self._started = False
        self._t0 = 0.0
        self._emit_idx = 0
        self._sm_prev: Optional[np.ndarray] = None        # causal filter state
        self._closed = False

    # -- public API -------------------------------------------------------- #

    def push(self, segments) -> List[Frame]:
        """Feed a chunk of segments; return the frames finalized by it."""
        if self._closed:
            raise RuntimeError("StreamingGenerator: push() after flush()/close()")
        out: List[Frame] = []
        for s in segments:
            if not self._started:
                self._t0 = self._grid_origin(s)
                self._started = True
            if self._raw_tail is not None:          # its successor is known: not
                self._cook(self._raw_tail, self._raw_idx, is_last=False)  # last
                out += self._emit_ready()
            self._raw_tail, self._raw_idx = s, self._next_idx
            self._next_idx += 1
        return out

    def flush(self) -> List[Frame]:
        """Cook the held-back last segment, emit every remaining frame."""
        out: List[Frame] = []
        if self._raw_tail is not None:
            self._cook(self._raw_tail, self._raw_idx, is_last=True)
            self._raw_tail = None
        self._finalize_pending()
        out += self._emit_ready(final=True)
        self._closed = True
        return out

    close = flush

    @property
    def buffered_segments(self) -> int:
        """Cooked segments currently retained — O(window), for the memory test."""
        return len(self._cooked) + (1 if self._pending else 0)

    # -- cooking (= _preprocess, applied causally) ------------------------- #

    def _grid_origin(self, first: PhonemeSegment) -> float:
        t0 = first.start - self.params.preroll                # matches offline
        if not self.params.allow_negative_time:
            t0 = max(t0, 0.0) if first.start >= 0.0 else first.start
        return t0

    def _cook(self, s: PhonemeSegment, i: int, is_last: bool) -> None:
        # absorb a short interior silence into the open segment (never the first
        # or last), else freeze the open segment and open a new one.
        if (self._pending is not None and i > 0 and not is_last
                and s.phoneme == SILENCE and s.dur < self.params.short_silence):
            p = self._pending
            self._pending = PhonemeSegment(p.phoneme, p.start, s.end)
        else:
            self._finalize_pending()
            self._pending = s

    def _finalize_pending(self) -> None:
        if self._pending is None:
            return
        for seg in self._split_diphthong(self._pending):
            self._cooked.append(seg)
            self._cooked_end = max(self._cooked_end, seg.end)
        self._pending = None

    def _split_diphthong(self, s: PhonemeSegment) -> List[PhonemeSegment]:
        if not self.params.split_diphthongs:
            return [s]
        parts = _DIPHTHONGS.get(strip_stress(s.phoneme).upper())
        if parts and s.dur > 1e-3:
            cut = s.start + s.dur * 0.55
            return [PhonemeSegment(parts[0], s.start, cut),
                    PhonemeSegment(parts[1], cut, s.end)]
        return [s]

    # -- emission ---------------------------------------------------------- #

    def _emit_ready(self, final: bool = False) -> List[Frame]:
        out: List[Frame] = []
        if not self._started:
            return out
        if final:                               # match offline's frame count
            last = int(round((self._cooked_end - self._t0) * self.fps))
        else:                                   # only fully look-ahead-covered
            horizon = self._cooked_end - self.look_ahead - self._t0
            last = int(np.floor(horizon * self.fps + 1e-9))
        while self._emit_idx <= last:
            out.append(self._emit_frame(self._emit_idx))
            self._emit_idx += 1
        if not final and self.back_span < float("inf"):     # prune the past tail
            cutoff = (self._t0 + self._emit_idx / self.fps) - self.back_span
            self._cooked = [s for s in self._cooked if s.end >= cutoff - 1e-9]
        return out

    def _emit_frame(self, idx: int) -> Frame:
        t = self._t0 + idx / self.fps
        lo, hi = t - self.back_span, t + self.look_ahead
        # window selected by frame time (not arrival) -> chunk-order-independent
        window = [s for s in self._cooked
                  if lo - 1e-9 <= (s.start + s.end) / 2.0 <= hi + 1e-9]
        if not window:
            row = np.zeros(self.n_targets)
        else:
            # a +/-1 frame local grid so _enforce_closures' argmin lands on the
            # same frame the full offline grid would (see module notes)
            grid = self._t0 + np.array([idx - 1, idx, idx + 1]) / self.fps
            row = _blend(window, grid, self.mapping, self._bp, self.fps)[1].copy()
        return t, self._causal_step(row)     # t == the offline grid time exactly

    def _causal_step(self, row: np.ndarray) -> np.ndarray:
        """Past-only one-pole smoothing (0 = off): pure function of this and
        earlier frames, so it never reads the future."""
        if self.causal_smooth <= 0.0:
            return row
        alpha = 1.0 - np.exp(-1.0 / (self.causal_smooth * self.fps))
        if self._sm_prev is None:
            self._sm_prev = row.copy()
        else:
            self._sm_prev = self._sm_prev + alpha * (row - self._sm_prev)
        return self._sm_prev.copy()


def frames_to_track(frames: List[Frame], fps: float, mapping=None,
                    epsilon: float = 0.015) -> FaceTrack:
    """Assemble emitted ``(time, values)`` frames into a
    :class:`~openfacefx.curves.FaceTrack` via the same ``reduce_to_track`` the
    offline pipeline uses — so a full-window stream reduces to the same keyframes
    as ``generate_from_alignment``."""
    if not frames:
        target_set = None if mapping is None else list(mapping.target_names)
        return FaceTrack(fps=fps, channels=[], target_set=target_set)
    times = np.array([t for t, _ in frames])
    matrix = np.vstack([v for _, v in frames])
    targets = mapping.targets if mapping is not None else None
    return reduce_to_track(times, matrix, fps, epsilon, targets)
