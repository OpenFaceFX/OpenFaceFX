# Layered multi-track export

Engines often want to re-blend or toggle facial layers at runtime rather than
receive one flattened curve set — Unreal Sequencer/Control Rig keep lip-sync and
expression on separate additive tracks, and SALSA blends layers by priority
(issue #39). The pipeline already produces speech / emotion / gesture on
**disjoint** channels before concatenating them into one `FaceTrack`, so this is a
data-reshuffle of arrays it already has plus a little metadata; the runtime mix
stays the engine's job.

```bash
python -m openfacefx export-layers merged.track.json -o layered.track.json
```

`export-layers` writes the **same flat track** with an extra top-level `layers`
block — so a reader that ignores the block still sees the merged track, and the
default (no layers) output is unchanged.

## The layers block

`build_layers(track)` decomposes a merged track into named sub-tracks by channel
classification — `gesture` (the issue-#5 gesture channels), `emotion` (the issue-#38
`VA_EMOTION_CHANNELS`), and `speech` (everything else: visemes and any rig
blendshapes). Each layer carries its channel list, a per-layer **blend-weight
curve** (`[[t, w], …]`, default constant `1.0`) and an integer **priority** (engine
layering order — speech 0, emotion 10, gesture 20). Empty layers are omitted.

```jsonc
{
  "format": "openfacefx.track", "version": 1, "fps": 60.0, "duration": 2.3,
  "channels": [ /* the flat merged track, unchanged */ ],
  "layers": [
    { "name": "speech",  "priority": 0,  "weight": [[0.0, 1.0]], "channels": [ /* visemes */ ] },
    { "name": "emotion", "priority": 10, "weight": [[0.0, 1.0]], "channels": [ /* smile, brow_raise, … */ ] },
    { "name": "gesture", "priority": 20, "weight": [[0.0, 1.0]], "channels": [ /* blink, headYaw, … */ ] }
  ]
}
```

Because every channel lands in exactly one layer, **`flatten_layers` summing the
layers at weight 1 reproduces the merged channels exactly** — a faithful, lossless
decomposition (pinned by a round-trip test). `priority` is engine metadata, ignored
by the flatten. Events (including prosody, issue #4) remain the track's own event
layer, unchanged — prosody drives notifies, not curves, so it is not a channel
layer.

## Invariants

- **Byte-identical default**: `to_dict(track)` with no `layers` (and no
  `track.layers`) is byte-for-byte what it was before — the `layers` key is
  appended only when non-empty. The entire existing test suite is the proof.
- `to_dict(track, layers=…)` / `write_json(…, layers=…)` embed the block;
  `from_dict`/`read_json` restore it to `track.layers`, so names, weights and
  priorities survive `write_json`→`read_json`.
- Empty/absent layers are omitted, never emitted as dead channels.

Library callers get `build_layers`, `flatten_layers`, `layers_to_dict`,
`layers_from_dict` and the `Layer` dataclass. numpy + stdlib, deterministic across
Python 3.9/3.13, additive.

::: openfacefx.layers
