# OpenFaceFX Runtime for Unity

Play [OpenFaceFX](https://openfacefx.com) lip-sync / facial-animation on your Unity
characters. Point it at a face mesh, hand it a take, and it streams **ARKit blendshape
weights** (and, optionally, **head/eye bone pose**) onto a `SkinnedMeshRenderer` every
frame — the same idea as FaceFX's Unity integration, driven by the open, numpy-only
OpenFaceFX pipeline.

- **Two input formats**, both produced by OpenFaceFX out of the box:
  - `*.offxtrack` — the self-describing OpenFaceFX **track JSON** (explicit times + fps). Recommended.
  - `*.csv` — Apple **ARKit "Live Link Face"** wide CSV (52 ARKit shapes + 9 head/eye rotations).
- **No third-party dependencies** — a tiny built-in JSON reader; nothing from the Asset Store or NuGet.
- **Works with [Microsoft Rocketbox](https://github.com/microsoft/Microsoft-Rocketbox) ARKit avatars**
  (MIT, 115 rigged avatars with ARKit blendshapes) — and most ARKit-blendshape rigs. See the sample.

> Requires **Unity 2021.3** or newer.

---

## Install

**Package Manager ▸ Add package from git URL** (or a local path / tarball):

```
https://github.com/OpenFaceFX/OpenFaceFX.git?path=integrations/unity/com.openfacefx.runtime
```

or add to `Packages/manifest.json`:

```json
"com.openfacefx.runtime": "https://github.com/OpenFaceFX/OpenFaceFX.git?path=integrations/unity/com.openfacefx.runtime"
```

---

## Quick start (60 seconds)

1. **Make a take** with OpenFaceFX and export ARKit data:

   ```bash
   # a self-describing track JSON, saved with the extension the importer claims
   openfacefx synth "Hello from OpenFaceFX" --preset arkit -f json -o hello.offxtrack
   # …or an ARKit Live Link CSV
   openfacefx synth "Hello from OpenFaceFX" --preset arkit -f livelink -o hello.arkit.csv
   ```

   (Or use the OpenFaceFX **Studio** → *Export* → Live Link CSV / track JSON.)

2. **Drop the file into your Unity project.** A `.offxtrack` imports straight to an
   `OffxClip` asset. For a `.csv`, right-click it → **OpenFaceFX ▸ Convert to OffX Clip**
   (or just assign the CSV `TextAsset` to the player's *Source Text* field).

3. **Add the player** to your character:
   - Select the avatar, **Add Component ▸ OpenFaceFX ▸ OffX Face Player**.
   - Set **Face Renderer** to the `SkinnedMeshRenderer` that has the ARKit blendshapes.
   - Set **Clip** to your `OffxClip` (or **Source Text** to the CSV `TextAsset`).

4. **Press Play.** The face talks. `Loop` and `Speed` are on the component; call
   `Play() / Pause() / Seek(t)` from your own code.

```csharp
var player = avatar.GetComponent<OpenFaceFX.OffxFacePlayer>();
player.Seek(0f);
player.Play();
Debug.Log($"bound {player.BoundShapeCount} blendshapes");   // sanity-check the rig wiring
```

---

## How channel names are matched

The player resolves each channel to a blendshape on your mesh **case-insensitively**, and
tolerates the two things that usually differ between rigs:

- a **mesh prefix** — `Head.jawOpen`, `head_blendShapes.jawOpen` (set **Blend Shape Prefix**);
- **PascalCase vs camelCase** — Live Link's `JawOpen` ⇄ ARKit's `jawOpen` are both tried.

So an OpenFaceFX ARKit take lines up with a Rocketbox ARKit avatar with no manual mapping.
`BoundShapeCount` tells you how many matched — if it's 0, check the renderer and prefix.

Unity blendshape weights are `0..100`; OpenFaceFX values are `0..1`, scaled by **Weight
Scale** (default 100).

## Head & eye pose (optional)

An ARKit Live Link take also carries `HeadYaw/Pitch/Roll` and per-eye rotations (degrees).
Tick **Apply Head Pose** and assign the **Head / Left Eye / Right Eye** bones to drive them.
Axis conventions vary between rigs — flip **Head Pose Scale** negative to reverse an axis.

## Driving it yourself

`OffxFacePlayer.ApplyAt(float seconds)` samples and applies a single instant, so you can
drive the face from a Timeline, an audio clock, or a network stream instead of the built-in
`Update`. `OffxClip.Sample(name, t)` reads one channel if you want to wire things by hand.

---

See **Documentation~/OpenFaceFX-Unity.md** for the full reference and the **Rocketbox ARKit**
sample (Package Manager ▸ OpenFaceFX Runtime ▸ Samples) for a worked example.

MIT-licensed, like OpenFaceFX itself.
