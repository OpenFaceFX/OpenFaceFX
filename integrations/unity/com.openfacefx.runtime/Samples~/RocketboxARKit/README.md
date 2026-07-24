# Sample — Rocketbox ARKit avatar

Drive a [Microsoft Rocketbox](https://github.com/microsoft/Microsoft-Rocketbox) avatar with an
OpenFaceFX take. Rocketbox avatars are MIT-licensed, rigged FBX characters; since **June 2022**
they ship **ARKit blendshapes**, which line up directly with OpenFaceFX's `arkit` retarget preset —
so no manual blendshape mapping is needed.

## What's in here

- `hello.offxtrack` — a real OpenFaceFX take (`"Hello from OpenFaceFX"`, ARKit-retargeted). Imports
  to an `OffxClip` automatically.
- `hello.arkit.csv` — the same take as an ARKit Live Link Face CSV (shows the CSV path; assign it as
  a `TextAsset` or right-click ▸ *OpenFaceFX ▸ Convert to OffX Clip*).
- `OffxRocketboxSample.cs` — a tiny driver with an on-screen scrubber.

## Wiring it onto a Rocketbox avatar

1. Import a Rocketbox avatar (FBX) into your project. Add its ARKit-blendshape variant to a scene.
2. Find the **`SkinnedMeshRenderer`** that carries the face blendshapes (usually the head mesh — its
   *BlendShapes* list will include `jawOpen`, `mouthFunnel`, `eyeBlinkLeft`, …).
3. Add **`OffxFacePlayer`** to the avatar root (or the head):
   - **Face Renderer** → that head `SkinnedMeshRenderer`.
   - **Clip** → `hello` (the imported `OffxClip`).
   - Leave **Blend Shape Prefix** empty for Rocketbox; if your rig names shapes like
     `head_blendShapes.jawOpen`, the trailing name still matches automatically.
4. Press **Play**. Check the Console for `bound N blendshapes` (N should be ~50 for a full ARKit rig).

### Head & eye motion (optional)
Tick **Apply Head Pose** and drag the avatar's **head** and **eye** bones into the player. If the
head turns the wrong way for your rig, set **Head Pose Scale** to `-1`.

### Lip-sync from your own audio
Generate a take from a real recording (aligned to the audio) and export it the same way:

```bash
openfacefx synth-audio voice.wav --transcript "…" --preset arkit -f json -o line01.offxtrack
```

Drop `line01.offxtrack` in and swap the player's **Clip**. Play it alongside the audio (an
`AudioSource`) — OpenFaceFX timing matches the clip, so they stay in sync.
