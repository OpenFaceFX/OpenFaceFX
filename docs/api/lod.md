# LOD variant export

Game runtimes carry facial animation at several detail levels and thin it with
distance — Unity's "Optimal" compression is keyframe reduction under an error
tolerance, and MetaHuman drops curve detail and updates at ~30 fps above LOD0.
OpenFaceFX already owns the machinery (`curves._rdp` / `edits.sample`), so the
`lod` command (issue #36) is a pure re-run at a tiered tolerance table — no ML, no
engine, no camera. From one solved track it emits **K variants, finest first**:

```bash
python -m openfacefx lod clip.track.json -o out/clip                    # default 3 tiers
python -m openfacefx lod clip.track.json --rdp 0.002,0.01,0.04 --fps 60,30,15 -o out/clip
python -m openfacefx lod clip.track.json --rdp 0.005,0.02 --fps 60,60 --format csv -o out/clip
```

writes `out/clip_lod0.json`, `out/clip_lod1.json`, … plus a `out/clip_lod.json`
metadata sidecar.

## Two tiers

- **RDP tier** — re-run `_rdp` per channel at a tolerance table (default
  `--rdp 0.002,0.01,0.04`): LOD0 keeps the dense curves, higher tiers keep only
  the major inflections. A pure RDP tier only ever *selects* a subset of the
  source keyframes — it never invents one — so a tier at the source epsilon
  reproduces the input **byte-identically** (LOD0).
- **fps tier** — before thinning, step/linear-resample each channel onto a
  coarser grid via `edits.sample` (default `--fps 60,30,15`), so a distant LOD
  updates less often; the kept keys land only on that coarse grid.

Each tier is `(epsilon, fps)`: a tier at (or above) the source fps is pure-RDP; a
coarser fps resamples first. `--fps` is capped at the source rate (LOD never
upsamples). Higher tiers carry a monotonically non-increasing keyframe count.

## Metadata sidecar

`*_lod.json` (`format: openfacefx.lod`) round-trips through JSON and names every
variant's `epsilon`, `fps`, channel and keyframe counts, plus an **advisory**
screen-coverage → LOD-index switching table (Unity `LODGroup`-style
`min_screen_height` thresholds, descending, last = `0.0` fallback). OpenFaceFX has
no camera at export, so the actual switch stays the engine's job — this is advice.

```jsonc
{
  "format": "openfacefx.lod", "version": 1,
  "source_fps": 60.0, "duration": 2.3,
  "levels": [
    { "index": 0, "file": "clip_lod0.json", "epsilon": 0.002, "fps": 60.0, "channels": 11, "keyframes": 172 },
    { "index": 1, "file": "clip_lod1.json", "epsilon": 0.01,  "fps": 30.0, "channels": 11, "keyframes": 169 },
    { "index": 2, "file": "clip_lod2.json", "epsilon": 0.04,  "fps": 15.0, "channels": 9,  "keyframes": 92 }
  ],
  "switching": [
    { "lod": 0, "min_screen_height": 0.5 },
    { "lod": 1, "min_screen_height": 0.2 },
    { "lod": 2, "min_screen_height": 0.0 }
  ]
}
```

The `FaceTrack.variants` slot is the issue-#6 event-take alternatives and is
**not** overloaded for LOD — variants are separate files. Each variant carries the
event/take layer through unchanged. Library callers get `generate_lods(track, *,
rdp, fps)`, `make_lod`, `lod_metadata`, `switching_table`, and the
`LOD_DEFAULT_RDP`/`LOD_DEFAULT_FPS` tables. numpy + stdlib, deterministic across
Python 3.9/3.13; purely additive (the default pipeline is unchanged without the
command).

::: openfacefx.lod
