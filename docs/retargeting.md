# Retargeting the 15 visemes onto other rigs

OpenFaceFX channels are the Oculus/Meta 15-viseme set. `openfacefx.retarget`
maps them onto other conventions:

```python
from openfacefx import generate_naive, retarget, rename_only, PRESETS, write_json

track = generate_naive("hello world", duration=1.2)
write_json(retarget(track, PRESETS["arkit"]), "track_arkit.json")

# Ready Player Me / Oculus-reference rigs just prefix the same visemes — the
# readyplayerme preset (below) is exactly this rename:
write_json(retarget(track, rename_only(prefix="viseme_")), "track_rpm.json")
```

Each mapping sends a viseme to one or more target shapes with a weight scale;
`retarget` resamples on the union of key times, sums contributions, clamps to
[0, 1]. Presets are plain data — copy one and tune it to your mesh.

## Presets and their provenance

| Preset | Target convention | Provenance |
|---|---|---|
| `arkit` | Apple ARKit 52 blendshapes | Weights reproduced verbatim from [met4citizen/TalkingHead](https://github.com/met4citizen/TalkingHead/blob/main/blender/build-visemes-from-arkit.py) (MIT), a shipping viseme→ARKit map. Apple's canonical shape list: [ARFaceAnchor.BlendShapeLocation](https://developer.apple.com/documentation/arkit/arfaceanchor/blendshapelocation). |
| `rhubarb` | [Rhubarb Lip Sync](https://github.com/DanielSWolf/rhubarb-lip-sync) mouth shapes A–H + X | Shape semantics from the Rhubarb README; nearest-pose assignment is ours. Uses extended shapes G (F/V) and H (L); if your rig only has the six basic shapes, remap G→A, H→C. |
| `preston_blair` | Preston-Blair series (Papagayo/Moho/OpenToonz) | Shape set from [garycmartin.com](https://www.garycmartin.com/mouth_shapes.html) and Rhubarb's DAT exporter; assignment ours. Consonant catch-all is named `etc` (the exact layer name OpenToonz/Moho expect); `WQ` is omitted — viseme-level input can't split W from UW. |
| `vrm` | VRM 1.0 expression presets (`aa ih ou ee oh`) | Preset semantics from the [VRM spec](https://github.com/vrm-c/vrm-specification/blob/master/specification/VRMC_vrm-1.0/expressions.md). The five vowels map canonically; **VRM has no consonant visemes**, so consonants borrow the nearest vowel mouth at reduced weight and `PP`/`sil` rest at zero — coarse by design. |
| `vrm0` | VRM 0.x BlendShapePreset vowels (`A I U E O`) | Preset names from the [VRM 0.0 spec](https://github.com/vrm-c/vrm-specification/blob/master/specification/0.0/README.md): VRM 0.x names the lip-sync presets with uppercase single letters, which the spec pairs one-to-one with 1.0's names — `A (aa)`, `I (ih)`, `U (ou)`, `E (E)`, `O (oh)`. Same five-vowel projection and consonant borrowing as `vrm`. Use this for VRoid Studio / VRM 0.x exports; use `vrm` for VRM 1.0. |
| `cc4` | Reallusion CC4 / iClone viseme panel (also CC3) | Names from the [Reallusion manual](https://manual.reallusion.com/Character-Creator-4/Content/ENU/4.0/06-Facial-Profile-Editor/The-Mapping-Between-Facial-Profile-Editor-and-Viseme-Panel.htm); CC4's set is nearly a 1:1 rename of the Oculus 15. The same Viseme Panel phoneme-pair labels back CC3 and CC4 — CC4's `ExPlus` / "CC4 Extended" additions sit in the facial-profile layer beneath the panel, not in these names — so this preset covers CC3 too. |
| `readyplayerme` | Ready Player Me Oculus visemes (`viseme_*`) | Ready Player Me avatars expose the Oculus 15 as morph targets named `viseme_<name>` in verbatim Oculus casing ([RPM Oculus OVR LipSync](https://docs.readyplayer.me/ready-player-me/api-reference/avatars/morph-targets/oculus-ovr-libsync); names per the [Meta viseme reference](https://developers.meta.com/horizon/documentation/unity/audio-ovrlipsync-viseme-reference/)). Identical to `rename_only(prefix="viseme_")`. RPM also ships the ARKit 52 blendshapes — drive those with the `arkit` preset instead. |

## Known quirks worth knowing

- **`arkit`**: kept as published — `CH` and `RR` are identical combos, and `PP`
  seals with `mouthRollLower/Upper` rather than `mouthClose`. If PP reads weak
  on your mesh, try `{mouthClose: 0.9, mouthPressLeft/Right: 0.3}` instead.
- **Meta does not publish** an official Oculus-viseme→ARKit table; every such
  mapping (including this one) is community-made. Treat weights as starting
  points.
- **Rhubarb/Preston-Blair are pose-based**: several consonant visemes collapse
  onto one "consonants"/B shape. That is how those conventions work, not data
  loss you can tune away.
- **`vrm` / `vrm0`** carry only five vowel mouths, so consonants are coarse (see
  the table). They are the same projection under two spec versions — pick `vrm0`
  for VRM 0.x / VRoid Studio uppercase presets, `vrm` for VRM 1.0.

## Rigs covered by an existing preset (no new table needed)

Some ecosystems reuse another rig's shape names, so they want documentation, not
a duplicate table:

- **Meta Avatars / Quest**: the Oculus/Meta LipSync SDK's 15 visemes *are*
  OpenFaceFX's native channel set (`sil PP FF … O U`, [Meta viseme
  reference](https://developers.meta.com/horizon/documentation/unity/audio-ovrlipsync-viseme-reference/)),
  so no retarget is needed — or `rename_only()` for a bare rename. Ready Player
  Me prefixes them (`viseme_*`): the `readyplayerme` preset.
- **Epic MetaHuman**: the face rig exposes the 52 ARKit shapes as poses with a
  bundled ARKit mapping asset (`mh_arkit_mapping_pose`), so `arkit` output is the
  right input for the standard MetaHuman ARKit route. MetaHuman Animator's own
  ~130 `CTRL_expressions` curves are a different, proprietary layer — community
  remaps exist (e.g. AntiAnti/MetahumVisemeCurves' 12 `LS_*` poses) but there is
  no authoritative table to ship.
- **NVIDIA Audio2Face-3D**: emits [ARKit
  blendshapes](https://docs.nvidia.com/ace/audio2face-3d-microservice/latest/text/getting-started/overview.html),
  so drive it with `arkit`. A2F does not animate the tongue — pass `available=`
  without `tongueOut` and the arkit fallback reroutes the `TH`/`nn` tongue weight
  to a small `jawOpen` (below). (A2F's `MouthClose` also folds in jaw opening,
  deviating from Apple's, but that is moot here: `arkit` seals `PP` with
  lip-roll, not `mouthClose`.)
- **Reallusion CC3**: shares CC4's Viseme Panel labels — use `cc4`.

## Optional shapes and fallbacks

A rig rarely has *every* shape a preset names. Pass `available=` (the shapes it
does have) and any missing target reroutes through the preset's fallback table
instead of dropping silently:

```python
from openfacefx import retarget, PRESETS, PRESET_FALLBACKS

# an Audio2Face rig with no tongue shape:
shapes = {"jawOpen", "mouthFunnel", "mouthPucker", ...}   # the rig's real ARKit shapes
track = retarget(track, PRESETS["arkit"], available=shapes,
                 fallbacks=PRESET_FALLBACKS["arkit"])      # tongueOut -> jawOpen
```

`fallbacks` is a `{target: [(replacement, scale), ...]}` table (an empty list
`[]` means *drop this target*). Substitutions **chain** — a replacement that is
itself missing is resolved again, weights multiplying — and cycles are broken,
not looped. With `available=None` (the default) nothing is filtered, so a plain
`retarget(track, mapping)` is unchanged. Shipped tables (`PRESET_FALLBACKS`):

| Preset | Fallback | Rationale |
|---|---|---|
| `arkit` | `tongueOut → jawOpen × 0.2` | Tongue-less rigs (e.g. Audio2Face) keep a hint of the `TH`/`nn` opening instead of losing it. Our heuristic, not an Apple convention. |
| `rhubarb` | `G → A`, `H → C`, `X → A` | Rhubarb's own documented [basic-set collapse](https://github.com/DanielSWolf/rhubarb-lip-sync) for art with only the six basic shapes. Single source of truth: the cue exporters (`--rhubarb-shapes`) derive their collapse from this same table. |

The tables are data — extend `PRESET_FALLBACKS` or pass your own `fallbacks=`.

### On the CLI: `--retarget-shapes`

The same restrict-and-reroute is reachable from the CLI with `--retarget-shapes`
— a JSON **array** of the shape names the rig actually has. Missing targets
reroute through the preset's `PRESET_FALLBACKS` table, exactly like the library
`available=`/`fallbacks=` path:

```bash
# shapes.json: ["jawOpen", "jawForward", "mouthFunnel", "mouthPucker", ...]  (no tongueOut)
python -m openfacefx mfa --textgrid voice.TextGrid -o a2f.json \
    --retarget arkit --retarget-shapes shapes.json          # tongueOut -> jawOpen
```

`--retarget-shapes` needs `--retarget` (it filters that preset's shapes), and an
empty array is rejected — an empty rig would drop every channel.

## Per-target gain and offset (`adjust`)

Presets are averages; a specific mesh often wants one shape a touch weaker, or
another held slightly open, without forking a weight table. Pass
`adjust={target: (gain, offset)}` and each named target's value becomes
`clamp(gain*value + offset, 0, 1)` **after** the weighted sum — the preset stays
byte-identical:

```python
from openfacefx import retarget, apply_adjust, PRESETS

track = retarget(track, PRESETS["arkit"], adjust={
    "jawOpen": (0.8, 0.0),           # 20% weaker jaw
    "mouthSmileLeft":  (1.0, 0.15),  # always slightly smiling ...
    "mouthSmileRight": (1.0, 0.15),  # ... even though arkit never drives smile
})
```

`gain` scales, `offset` shifts, the result clamps to `[0, 1]`. A target the rig
never receives but given a **positive offset** is materialised as a constant
channel over the clip (and added to the declared `target_set`) — that is how the
`mouthSmile*` above turn on with no mapping edit; `gain` is irrelevant there (the
absent base is 0). `apply_adjust(track, adjust)` is the same transform as a
standalone post-process on any `FaceTrack`, so `retarget(track, m, adjust=A)` is
exactly `apply_adjust(retarget(track, m), A)`. An empty/omitted `adjust` is a
byte-identical no-op.

On the CLI, per-target trims come from a JSON **object** via `--adjust` (an ARKit
rig's ~52 shapes are too many for flags), on the curve outputs (`json`/`csv`/
`anim`); each shape's object takes an optional `gain` (default 1.0) and `offset`
(default 0.0):

```bash
# adjust.json: {"jawOpen": {"gain": 0.8}, "mouthSmileLeft": {"offset": 0.15}}
python -m openfacefx mfa --textgrid voice.TextGrid -o rig.json \
    --retarget arkit --adjust adjust.json
```

It composes with `--retarget-shapes` (shapes are filtered first, then trimmed)
and is validated at the CLI boundary — an unknown key or a non-numeric
gain/offset is a clear error, not a stray failure deeper in. `--adjust` is a
trim on retargeted curves, so it is rejected on the pose-based cue/Live2D/Godot
formats.
