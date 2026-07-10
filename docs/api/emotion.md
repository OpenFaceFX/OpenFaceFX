# Emotion & expression layer

An additive emotion/expression layer baked over a speech-solved track (issue
#38). Production rigs keep expression on a separate additive layer over lip-sync
and add it onto the base at runtime — SALSA's
[EmoteR](https://crazyminnowstudio.com/unity-3d/lip-sync-salsa/) blends
emphasis-timed emotes over speech, and
[Unreal additive animation](https://mocaponline.com/blogs/mocap-news/animation-layers-guide)
is defined as the *difference between a pose and a reference (T/A) pose* added
onto the base. This module does the same in pure numpy: an authored emotion
envelope becomes a true additive delta `channel_value - reference_value`, is
added onto the speech-solved channels, scaled by a global intensity dial, clamped
per channel and re-thinned. The result is an ordinary
[`FaceTrack`](visemes.md) that exports through every existing exporter unchanged.

## Authoring modes

An envelope (`format: "openfacefx.emotion"`, `version: 1`) carries one of two
input modes:

- **`channels`** — direct emotion-channel keyframes, authored on the timeline
  like any curve:

    ```jsonc
    { "format": "openfacefx.emotion", "version": 1, "mode": "channels",
      "reference": { "smile": 0.1 },
      "clamps":    { "smile": [0.0, 1.0] },
      "channels": {
        "smile":      [[0.0, 0.0], [0.6, 0.8], [1.4, 0.0]],
        "brow_raise": [[0.0, 0.0], [0.6, 0.5], [1.4, 0.0]]
      } }
    ```

- **`valence_arousal`** — a compact valence/arousal keyframe track (both in
  `[-1, 1]`) mapped through the fixed, hand-authored table below:

    ```jsonc
    { "format": "openfacefx.emotion", "version": 1, "mode": "valence_arousal",
      "va": {
        "valence": [[0.0, 0.0], [0.75, 1.0], [1.5, 0.3]],
        "arousal": [[0.0, 0.2], [0.75, 0.9], [1.5, 0.4]]
      } }
    ```

`reference` is the neutral/rest pose the additive delta is measured against; a
channel absent from it rests at `0`. `clamps` optionally bounds a channel's baked
output to `[lo, hi]` (`0 <= lo <= hi <= 1`).

## The valence/arousal table

Valence and arousal are each sampled at three nodes, `VA_AXIS = (-1, 0, +1)`, and
a query point is **bilinearly interpolated** inside the resulting 3×3 grid — a
table lookup and interpolation only, **no ML**. Because `0` is a real node on
each axis, neutral affect `valence = arousal = 0` maps to an all-zero pose (a true
no-op). The weights follow the circumplex model of affect and FACS: pleasant
valence drives a zygomatic `smile` with a Duchenne `cheek_raise`; unpleasant
valence with rising arousal drives a corrugator `brow_lower` (anger) over a
mouth-corner-down `frown`; high arousal at neutral valence reads as a raised,
surprised `brow_raise`.

`VA_TABLE[channel][i_v][i_a]` is the channel weight at `valence = VA_AXIS[i_v]`
and `arousal = VA_AXIS[i_a]` (rows run valence −1 / 0 / +1, columns arousal −1 /
0 / +1):

| channel | v−1,a−1 | v−1,a0 | v−1,a+1 | v0,a−1 | v0,a0 | v0,a+1 | v+1,a−1 | v+1,a0 | v+1,a+1 |
|---|--|--|--|--|--|--|--|--|--|
| `smile`       | 0.00 | 0.00 | 0.00 | 0.00 | **0.00** | 0.00 | 0.45 | 0.70 | 0.90 |
| `cheek_raise` | 0.00 | 0.00 | 0.00 | 0.00 | **0.00** | 0.00 | 0.30 | 0.55 | 0.80 |
| `brow_raise`  | 0.25 | 0.05 | 0.10 | 0.00 | **0.00** | 0.70 | 0.00 | 0.10 | 0.30 |
| `brow_lower`  | 0.10 | 0.45 | 0.85 | 0.00 | **0.00** | 0.00 | 0.00 | 0.00 | 0.00 |
| `frown`       | 0.70 | 0.55 | 0.55 | 0.00 | **0.00** | 0.00 | 0.00 | 0.00 | 0.00 |

`va_to_pose(valence, arousal)` returns the interpolated weights for one point;
values outside `[-1, 1]` clamp to the nearest edge.

## Baking

`bake_emotion(track, envelope, *, intensity=1.0, clamps=None, eps=0.015)`
resamples the base curve and the delta onto a shared grid via
[`edits.sample`](retarget.md) (the piecewise-linear delta primitive), adds
`base + intensity * (pose - reference)`, clamps to each channel's `[lo, hi]` and
re-thins with the shared RDP thinner. Channels the base track lacks are appended
and their names added to `target_set`; channels it already has are updated in
place.

The **curve** exporters (JSON, CSV, Unity `.anim`, Godot `.tres`, Live2D
`.motion3.json`) carry every channel, including the emotion channels; the
**mouth-only** cue exporters (Rhubarb, Moho/OpenToonz, Papagayo) and the `.lip`
writer ignore the recognised expression channels (`smile`, `cheek_raise`,
`brow_raise`, `brow_lower`, `frown`) exactly as they ignore gesture channels, and
`--retarget` passes them through unchanged.

## Invariants

- **Additive / opt-in**: with `intensity == 0`, no emotion channels, a neutral
  valence/arousal track, or an exactly-zero delta on every channel, the input
  track is returned **byte-identical**.
- **Deterministic**: numpy `interp`/`clip` + the shared RDP thinner and a fixed
  table, no RNG and no wall-clock — identical on Python 3.9/3.13.
- `intensity` scales the delta **linearly**; the delta encoding round-trips
  (`base + (pose − reference)` reconstructs the target pose within float
  tolerance).

## CLI

```bash
# generate a speech track, then bake emotion over it
python -m openfacefx naive --text "..." --duration 1.5 -o base.json
python -m openfacefx emotion base.json happy.emotion.json --intensity 1.0 -o baked.json
# --intensity 0 (or a neutral envelope) => baked.json is byte-identical to base.json
```

The `emotion` command writes any of the usual output formats
(`.json`/`.csv`/`.anim`/`.tres`/`.motion3.json`/cue formats) and composes with
`--retarget`/`--adjust`. Per-channel clamps can be set with a repeatable
`--clamp CHANNEL LO HI`.

::: openfacefx.emotion
