# glTF 2.0 export (vendor-neutral)

Every other 3D exporter here is engine-specific (Unity `.anim`, Godot `.tres`,
Live2D `.motion3.json`). **glTF 2.0** (issue #49) is the ISO/IEC 12113 runtime
interchange standard — imported directly by Blender, Three.js, Babylon.js, Godot,
Unity and Unreal, and the base of **VRM** (the VTuber avatar format we already
ship `vrm`/`vrm0` retarget presets for). Its animation natively drives
**morph-target weights** (`animation.channel.target.path = "weights"`), which is
exactly OpenFaceFX's `[0, 1]` viseme/blendshape channel model — so one portable
file plays anywhere.

```bash
python -m openfacefx naive --text "..." --wav v.wav -o face.gltf     # JSON + base64 buffer
python -m openfacefx naive --text "..." --wav v.wav -o face.glb      # binary container
python -m openfacefx convert track.json -o face.glb                  # from an existing track
python -m openfacefx convert track.json --gltf-head-node -o face.glb # + head rotation
```

`.gltf`/`.glb` are wired into all four generate commands and `convert` through the
shared exporter dispatch, exactly like the other exporters.

## Structure

A stub `mesh` declares **N morph targets** named after the track's `[0, 1]` weight
channels via `mesh.extras.targetNames` (the de-facto convention a consumer remaps
by), a `node` references it with a `weights` array, and one `animation` has a
**LINEAR** sampler whose `input` is the per-frame time grid (strictly increasing,
with `min`/`max`) and whose `output` is the frame-major `n_frames × N` morph
weights. The sparse `FaceTrack` channels are densified onto that grid with
`np.interp`. Accessors are packed with numpy as **little-endian FLOAT**
(componentType 5126); `.gltf` embeds the buffer as a base64 `data:` URI, `.glb`
packs a 12-byte header + a JSON chunk (space-padded to 4 bytes) + a BIN chunk
(zero-padded) via stdlib `struct`.

**Only `[0, 1]` weight channels** become morph weights. The signed head/eye
**pose** channels (`headPitch/Yaw/Roll`, `eyePitch/Yaw` — degrees) are excluded by
default, since they are not morph weights. The opt-in `--gltf-head-node` encodes
`headPitch/Yaw/Roll` as a separate node `rotation` (Euler→quaternion) sampler,
honestly kept distinct from the morphs.

## Verification

The [Khronos glTF Validator](https://github.com/KhronosGroup/glTF-Validator) is
the documented external CI/manual gate — it cannot run in this repo's environment,
so the asset is built strictly to the glTF 2.0 spec (so it would pass) and the
**in-repo proof is a full accessor round-trip** (pure `json`/`base64`/`struct`, no
external dependency): decoding the LE-float32 `input`/`output` accessors
reconstructs every weight channel within `1e-6` of the source, with `accessor.min`
/ `max` / `count` / `byteLength`, buffer/chunk alignment and `componentType` all
asserted correct and sampler times strictly increasing — for both `.gltf` and
`.glb`. Deterministic bytes across Python 3.9/3.13, numpy + stdlib only, additive.

::: openfacefx.export_gltf
