# Track transforms (retime / mirror / trim)

Deterministic post-production edits on an existing track (issue #48) — the kind
`postprocess.time_shift` can't do (it only *slides*, never stretches). They
compose with [`convert`](../index.md) and the [importers](importers.md): bring a
capture in, retime it to the new VO, mirror it for an opposite-facing character,
slice a range out, re-export.

```bash
python -m openfacefx transform track.json --retime 1.5 -o slow.json         # 1.5x slower
python -m openfacefx transform track.json --duration 3.2 -o fit.json         # to 3.2 s
python -m openfacefx transform track.json --wav newvo.wav -o redub.json      # to a WAV length
python -m openfacefx transform track.json --mirror -o flipped.json           # L/R mirror
python -m openfacefx transform track.json --trim 0.5 2.0 -o slice.json        # keep [0.5, 2.0]
```

The `transform` command reads a `.track.json`, applies the selected ops in the
order **retime → mirror → trim**, and writes through the shared exporter dispatch
(so any `-o` extension and `--retarget`/`--adjust` work, exactly as `convert`).

## retime / stretch

`retime(track, factor, *, anchor=0.0)` scales every keyframe **time** (and event
time) about `anchor` — `t' = anchor + (t - anchor) * factor` — leaving channel
**values** untouched and letting the track `duration` follow.
`retime_to_duration(track, target)` picks the factor to hit a target length;
`--wav` uses `wav_duration`. `factor` must be finite and positive.

A uniform scale preserves collinearity and (when stretching) only widens key
spacing, so it introduces no redundant keys — retime therefore **keeps every key**
(deduping only an exact 4-dp time collision under heavy compression) rather than
RDP-resampling, which would move keys and break the "every key time scales, values
unchanged" contract. Retime to 2× doubles every key time, event time, and the
duration exactly.

## mirror

`mirror(track)` produces the opposite-facing performance:

- swaps `*Left` ↔ `*Right` channel pairs via the extensible :data:`MIRROR_PAIRS`
  table (plain data, the same style as the retarget presets — ARKit blendshapes
  plus the gesture-layer `blink_L`/`blink_R`; copy and extend for your rig);
- negates the signed **lateral** pose channels :data:`MIRROR_NEGATE` (`headYaw`,
  `headRoll`, `eyeYaw`) — a left turn becomes a right turn;
- leaves centered channels (all visemes, `jawOpen`, and `headPitch` / `eyePitch`,
  which are up/down not lateral) **untouched**.

It is a **pure relabel + sign flip** — no time change, no re-thin, channel order
preserved — so `mirror(mirror(track))` is **byte-identical** to `track` (pinned by
a `to_dict` and a CLI `cmp` test).

## trim / slice

`trim(track, t0, t1)` keeps `[t0, t1]`, rebased so `t0` becomes `0`. Only
in-window keys are kept (a channel left empty is dropped); events whose start is
in-window are rebased and their duration reclamped to the window, the rest
dropped. An empty or out-of-range window yields an empty track — never a crash.

## concat / sequence

`concat(tracks, *, gaps=None, crossfade=0.0)` — the sequential complement to
`trim` — splices finished tracks end-to-end into one timeline, offsetting every
keyframe **and** event/variant time of segment *k* by its cumulative start and
setting `duration = Σ durations + Σ gaps`. Use it to stitch per-line VO into one
conversation track, build a barks reel, or insert beats between lines:

```bash
python -m openfacefx sequence line1.json line2.json line3.json -o scene.json
python -m openfacefx sequence a.json b.json --gap 0.5 -o with_beat.json     # silence between
python -m openfacefx sequence a.json b.json --crossfade 0.15 -o blended.json  # soft seam
```

Channels are **unioned** across segments: a channel absent from a segment reads as
rest (`0`) across its span — a `0` key at each of that segment's boundaries stops
the previous segment's last value bleeding over the seam. `--gap SECONDS` inserts
silence and shifts everything after it. A single-track `concat([a])` (no gap, no
crossfade) returns `a` **byte-identical**, and `concat` is the seam inverse of
`trim`: `trim` at the seam reproduces `a` and the time-shifted `b`.

By default (`crossfade=0`) the splice is a **pure relabel/offset with no re-thin**
(a hard cut, exact). `--crossfade S` linearly blends the shared channels over
`±S` seconds at each abutting seam, RDP-thinning only that window.

Every transform is deterministic (stdlib arithmetic, no clock, no RNG — identical
on Python 3.9/3.13), additive, and leaves existing command output unchanged.
Library callers get `retime`, `retime_to_duration`, `mirror`, `trim`, `concat`,
`MIRROR_PAIRS`, and `MIRROR_NEGATE`.

::: openfacefx.transforms
