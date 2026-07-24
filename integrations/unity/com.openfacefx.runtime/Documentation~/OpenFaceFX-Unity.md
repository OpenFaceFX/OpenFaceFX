# OpenFaceFX Runtime for Unity — reference

A small runtime that plays OpenFaceFX facial-animation takes on a Unity character by writing
blendshape weights (and optional head/eye bone rotations) each frame.

## Data flow

```
OpenFaceFX (Studio or CLI)                     Unity
  synth/align → retarget(arkit) ──► .offxtrack ─┐
                                └──► ARKit CSV ──┴─► OffxParser ─► OffxClip ─► OffxFacePlayer ─► SkinnedMeshRenderer
```

## Formats

### Track JSON — `openfacefx.track` (recommended)
Self-describing; carries explicit per-key times, so playback is frame-rate independent.

```json
{ "format": "openfacefx.track", "version": 1, "fps": 30, "duration": 2.2,
  "channels": [ { "name": "jawOpen", "keys": [[0.0, 0.05], [0.033, 0.11], ...] }, ... ] }
```

Save it with the **`.offxtrack`** extension and the ScriptedImporter turns it into an `OffxClip`
asset. (`write_json` / `-f json` write this content regardless of extension.)

### ARKit Live Link Face CSV
`Timecode,BlendShapeCount,<52 ARKit shapes>,<9 head/eye rotations>`, one row per frame. Rows are a
uniform grid, so time is `rowIndex / fps`. OpenFaceFX writes the timecode at **60 fps** by default —
set **Csv Fps** on the importer/player if you exported at another rate. Values are `0..1`; the 9
rotation channels (`HeadYaw/Pitch/Roll`, `Left/RightEyeYaw/Pitch/Roll`) are **degrees**.

## API

### `OffxClip : ScriptableObject`
- `float fps`, `float duration`, `List<OffxChannel> channels`
- `float Sample(string channelName, float t)` — linear sample, 0 if absent
- `OffxChannel Get(string name)`, `IEnumerable<string> ChannelNames()`
- `static OffxClip Parse(string text)` / `Parse(TextAsset)` — auto-detects JSON vs CSV

### `OffxChannel`
- `string name`, `float[] times` (ascending), `float[] values`
- `float Sample(float t)` — clamped linear interpolation (binary search)

### `OffxFacePlayer : MonoBehaviour`
| Field | Meaning |
|---|---|
| `clip` / `sourceText` / `csvFps` | the performance (a clip asset, or a track-JSON/CSV `TextAsset` parsed on Awake) |
| `faceRenderer` | target `SkinnedMeshRenderer` (must have the ARKit blendshapes) |
| `blendShapePrefix` | optional prefix on the mesh's blendshape names |
| `weightScale` | full-activation weight (Unity is 0..100; OFFX is 0..1) |
| `playOnAwake`, `loop`, `speed` | playback |
| `applyHeadPose`, `headBone`, `leftEyeBone`, `rightEyeBone`, `headPoseScale` | optional bone pose |

Methods: `Play() / Pause() / Stop() / Seek(seconds)`, `ApplyAt(seconds)` (drive from an external
clock — Timeline, audio, network), `SetClip(OffxClip)`, `Bind()`. Properties: `Time01Seconds`,
`IsPlaying`, `Duration`, `BoundShapeCount`.

### Name resolution
For each channel the player tries, case-insensitively: `prefix+name`, `name`, `lowerFirst(name)`
(`JawOpen`→`jawOpen`), `upperFirst(name)`, `prefix+lowerFirst(name)`; it also indexes the tail after
a `.` so `Head.jawOpen` matches `jawOpen`. Head/eye rotation channels are routed to bones, not shapes.

## Troubleshooting

- **`bound 0 blendshapes`** — `faceRenderer` is unset or points at a mesh without ARKit blendshapes,
  or every shape has a prefix you haven't set. Check the mesh's *BlendShapes* list and **Blend Shape Prefix**.
- **Face barely moves** — raise **Weight Scale** (should be 100), or the take wasn't retargeted to
  `arkit` (viseme channel names like `aa`/`PP` won't match ARKit blendshapes; export with `--preset arkit`).
- **Timing drifts on a CSV** — you exported at a non-60 rate; set **Csv Fps** to match, or use the
  `.offxtrack` JSON (times are explicit).
- **Head turns the wrong way** — set **Head Pose Scale** to `-1`, or leave pose off and animate bones separately.

## Compatibility

Unity **2021.3 LTS+** (ScriptedImporter via `UnityEditor.AssetImporters`). No third-party packages.
Tested formats are produced by OpenFaceFX ≥ 0.22.
