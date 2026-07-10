# Retargeting the 15 visemes onto other rigs

OpenFaceFX channels are the Oculus/Meta 15-viseme set. `openfacefx.retarget`
maps them onto other conventions:

```python
from openfacefx import generate_naive, retarget, rename_only, PRESETS, write_json

track = generate_naive("hello world", duration=1.2)
write_json(retarget(track, PRESETS["arkit"]), "track_arkit.json")

# rigs that just prefix the same visemes (Oculus reference / Ready Player Me):
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
| `cc4` | Reallusion CC4 / iClone viseme panel | Names from the [Reallusion manual](https://manual.reallusion.com/Character-Creator-4/Content/ENU/4.0/06-Facial-Profile-Editor/The-Mapping-Between-Facial-Profile-Editor-and-Viseme-Panel.htm); CC4's set is nearly a 1:1 rename of the Oculus 15. |

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

## MetaHuman and friends (no preset yet)

Epic's MetaHuman face rig exposes the 52 ARKit shapes as poses with a bundled
ARKit mapping asset (`mh_arkit_mapping_pose`), so the `arkit` preset output is
the right input for the standard MetaHuman ARKit route. MetaHuman Animator's
own ~130 `CTRL_expressions` curves are a different, proprietary layer —
community remaps exist (e.g. AntiAnti/MetahumVisemeCurves' 12 `LS_*` poses)
but there is no authoritative table to ship.
