# Streaming / real-time generation

The offline pipeline solves a whole clip at once. A live pipeline — a TTS engine
emitting phonemes as it speaks — needs to emit animation **incrementally**, with
memory that stays constant no matter how long the stream runs.
[`StreamingGenerator`][openfacefx.streaming.StreamingGenerator] (issue #43) does
that: `push(chunk_of_segments)` returns the keyframe frames that just became
final, `flush()` (alias `close()`) emits the tail. It reuses the **exact** offline
component math — [`coarticulation._blend`](visemes.md), the shared core of
`build_viseme_curves` — over a bounded segment **window**.

```python
from openfacefx import StreamingGenerator, frames_to_track

gen = StreamingGenerator(fps=60.0, look_ahead=0.5)   # look_ahead = latency dial
frames = []
for chunk in phoneme_segment_chunks:                 # e.g. from a live aligner
    frames += gen.push(chunk)
frames += gen.flush()
track = frames_to_track(frames, 60.0)                # -> a normal FaceTrack
```

## Honesty: reproduces the offline solve *within tolerance*, not bit-exactly

This is important and stated plainly. The coarticulation dominance is a Laplacian
bump `D_i(t) = alpha·exp(-theta·|t − c_i|)` — **exponential, infinite support** —
and the blend normalizes over *every* segment. Bounded memory (pruning old
segments) and a finite look-ahead (dropping far-future ones) therefore both omit
exponentially small tails, so **no finite window is bit-identical** to
`generate_from_alignment`. That is fundamental to this dominance model, not an
implementation gap.

It converges fast, though. The per-frame error from a window `W` seconds wide is
bounded by `O(exp(-theta·W))` (slowest θ ≈ 2.9/s for a long vowel):

| look-ahead `W` | ~ max per-frame error | ≈ |
|---|---|---|
| 1.5 s | 1e-2 | the RDP epsilon (0.015) |
| 3 s | 1e-4 | the 4-dp keyframe grid |
| 4.5 s | 1e-6 | 10⁴× below perceptual / storage precision |

`look_ahead` is the **single latency ↔ fidelity dial**: `0` = zero latency, no
anticipatory coarticulation (causal only); larger = more anticipation, tighter to
offline. There is one **exact** case: when the window covers the whole clip
(`look_ahead` and `back_span` ≥ clip duration) the per-frame blend is
**bit-identical** to offline, and `frames_to_track` then reduces to the same
keyframes as `generate_from_alignment`.

## Guarantees

- **Chunk boundaries never matter.** The same clip pushed in 1 or K chunks yields
  **bit-identical** frames — windows are selected by frame time, not by arrival.
- **Bounded memory.** The cooked-segment ring buffer stays `O(look_ahead +
  back_span)`, independent of stream length.
- **Causal.** A frame, once emitted, is never revised; its value depends only on
  inputs within its window, so a later chunk cannot alter an already-emitted
  keyframe (the flip side of the tolerance). The optional `causal_smooth` is a
  **past-only** one-pole filter, deliberately distinct from the offline symmetric
  `postprocess.smooth_matrix` (which reads both directions and so is not
  reproducible causally).
- **Not offline RDP.** Frames are emitted incrementally (dense), so the generator
  does not reproduce offline's global Ramer–Douglas–Peucker keyframe reduction
  (that needs the whole column = O(stream)); `frames_to_track` applies the same
  RDP once at the end for storage.

Network transport (gRPC / WebSocket) is out of scope — this is an in-process
generator only. Deterministic; numpy + stdlib.

::: openfacefx.streaming
