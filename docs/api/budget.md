# Channel-budget reduction

Rigs have fixed morph-target budgets and collapse secondary facial detail at
distance. The budget pass (issue #37) ranks a solved track's channels by **total
energy** — the summed absolute key-to-key value delta (total variation), i.e. how
much a channel actually *moves* — and keeps the top N, dropping the low-energy
secondary micro-channels (subtle brow / cheek / nostril) **entirely** (as
`reduce_to_track` skips never-firing channels; nothing is zeroed with dead keys).

In a speech clip the jaw and primary lip visemes move the most, so the ranking
keeps them naturally — no protect-set is needed (and one would risk evicting a
higher-energy channel to honour the cap). The ranking is deterministic, ties
broken by channel name.

**The cap applies to the `[0,1]` morph channels only.** The signed head/eye
**pose** channels (`headPitch`/`headYaw`/`headRoll`/`eyePitch`/`eyeYaw` — the same
set [`inspect`/`validate`](inspect.md) classifies) pass through unchanged and are
**not counted toward N**, since they drive bones rather than morph targets and
their degree-scale deltas would otherwise dwarf a `[0,1]` weight purely on units.
So `N` means "at most N morph channels".

## Two modes

```bash
# (a) standalone hard cap for a fixed morph-target platform — composes with the
#     other transforms; writes a <out>.budget.json energy-ranking sidecar
python -m openfacefx transform clip.track.json --max-channels 20 -o rig.json
python -m openfacefx transform clip.track.json --retime 1.5 --max-channels 12 -o out.json

# (b) per-LOD budget paired with the lod command — higher LODs keep fewer channels,
#     nested by the source ranking (so channel sets don't pop between levels)
python -m openfacefx lod clip.track.json --rdp 0.002,0.01,0.04 --fps 60,30,15 \
       --max-channels 15,8,4 -o out/clip
```

The per-channel energy ranking is emitted as sidecar metadata **regardless of
mode**: `transform` writes `<out>.budget.json` (`format: openfacefx.budget`) and
`lod` folds the source ranking plus each tier's `max_channels` into its
`*_lod.json`.

```jsonc
{
  "format": "openfacefx.budget", "version": 1,
  "max_channels": 6, "kept": 6, "dropped": 5,
  "ranking": [
    { "name": "aa", "energy": 2.6954, "rank": 0, "kept": true },
    { "name": "E",  "energy": 2.6159, "rank": 1, "kept": true },
    …
    { "name": "nn", "energy": 0.673,  "rank": 10, "kept": false }
  ]
}
```

Absent the flag the track is returned **unchanged** (byte-identical). A cap of N
never yields more than N channels. Library callers get `channel_energy`,
`rank_channels`, `budget_channels(track, N) -> (track, ranking)`, `keep_channels`
and `budget_metadata`; pure stdlib arithmetic, deterministic across Python
3.9/3.13, additive.

::: openfacefx.budget
