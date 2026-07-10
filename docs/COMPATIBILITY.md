# FaceFX-ecosystem compatibility

OpenFaceFX is a **clean-room, functional replacement** for the FaceFX authoring
pipeline. It does **not** read or write the proprietary FaceFX binary formats
(`.facefx`, `.fxa`, `.fxe`, `.ffxc`) and is not affiliated with OC3
Entertainment or Speech Graphics.

This document is an honest survey (July 2026) of the FaceFX wrappers and tools
that exist in the wild, and what "working with" each of them actually means.

## The key fact about FaceFX wrappers

Every public "FaceFX wrapper" is a **parallel generator**, not a curve consumer:
each takes *audio + text* and runs its own alignment to emit a game-specific
lipsync artifact. None of them accept animation curves from another tool as
input — so no lip-sync generator (ours or anyone's) can feed them directly.
The practical integration surface is their **output formats**: pipelines built
around a wrapper can swap in OpenFaceFX once OpenFaceFX writes the same
artifact the wrapper would have produced.

## Survey

### Nukem9/FaceFXWrapper — the de-facto standard wrapper
- **Repo:** <https://github.com/Nukem9/FaceFXWrapper> (MIT-labelled, last release v0.41, Dec 2021)
- **What it does:** generates Bethesda **`.LIP`** lipsync files for Skyrim /
  Fallout without the Creation Kit. Used by xVASynth's `lip_fuz` plugin and the
  Mantella / Pantella AI-NPC pipelines.
- **Inputs:** game type + `FonixData.cdf` (user-supplied, Bethesda-owned) +
  16 kHz 16-bit mono WAV + dialogue text. Windows executable; embeds Creation
  Kit-derived code, so it is not itself clean-room.
- **Compatibility today:** OpenFaceFX now writes Skyrim `.lip` directly from its
  phoneme segments (`openfacefx.export_lip.write_lip`, or `-o out.lip` on the
  `naive`/`mfa` commands) — an **experimental** clean-room writer. **Path to
  full compatibility:** in-game verification (volunteers welcome on
  [issue #12](https://github.com/OpenFaceFX/OpenFaceFX/issues/12)).
  **Format-research finding (Jul 2026, updated):** the `.fuz` container and
  Skyrim's 16 speech-target names were already publicly verified; the `.lip`
  *payload* — a FaceFX facial-animation blob with no public byte-level spec —
  has now been reverse-engineered from four real samples (three mod-author
  placeholders plus one vanilla Creation-Kit asset). It is a frame-major curve
  grid; our codec (`tools/lip_codec_research.py`) re-serializes all four samples
  **byte-identically** and the writer's own output round-trips exactly. Shipped,
  but flagged experimental because **the game has not yet loaded a written file**
  and two facts stay unverifiable without the engine: the slot→morph assignment
  (numbered curve slots, not names) and the header `u22` field (we copy the
  vanilla asset's value). Note the "12-byte version/size/flags header" is
  FaceFXWrapper's *interface* view — the on-disk payloads (including the
  `.fuz`-embedded vanilla asset) begin directly with the 24-byte FaceFX
  animation header. No Creation Kit code, `FonixData.cdf`, or game assets are
  used: format facts are derived by analysis and the writer is original code.

#### Calibrating the slot → mouth-target map (help wanted, ~20 min, no tools)

The one unresolved fact is which numbered curve **slot** drives which mouth
morph — the payload routes by slot index, not name, so `SKYRIM_SLOT_MAP` in
`openfacefx.export_lip` is a hypothesis (only its jaw guess at slot 22 has any
evidence). You can resolve it in-game with nothing but a save and your eyes:

    python -m openfacefx lip-calibrate --out calib

writes `calib/slot_00.lip` .. `slot_32.lip` (one per Skyrim grid slot) plus a
`README.txt`. Each file sweeps exactly one slot 0→1→0 (~2 s) with everything
else at rest. Then, for each file:

  1. Pick any voiced NPC line you can retrigger in-game.
  2. Substitute `slot_NN.lip` for that line's lip data (rename it in place, or
     repack the line's `.fuz` — `openfacefx.bethesda.write_fuz` does this).
  3. Play the line and note **which** part of the face moves (jaw open, lip
     closure, lip round, tongue, brow, none…). Record `slot NN → <part>`.
  4. Repeat across all 33 files, then post your slot→part table on
     [issue #12](https://github.com/OpenFaceFX/OpenFaceFX/issues/12).

That table is exactly what `SKYRIM_SLOT_MAP` needs; once confirmed, the
experimental writer's mouth shapes become correct rather than provisional.

### FaceFX/FaceFX-UE4 and FaceFX/FaceFX-UE5 — official Unreal plugins
- **Repos:** <https://github.com/FaceFX/FaceFX-UE4>, <https://github.com/FaceFX/FaceFX-UE5>
  (actively maintained; MIT for the plugin interface code only)
- **What they do:** load compiled, actor-bound `.ffxc` / `.facefx` assets
  authored in FaceFX Studio. The FaceFX **Runtime** itself is not open source —
  it is an EULA-gated binary from facefx.com (a widely repeated claim that a
  `FaceFX/Runtime` source repo exists on GitHub is false).
- **Compatibility:** **fundamentally incompatible.** Producing `.ffxc` requires
  the proprietary Runtime compiler, and compiled assets are locked to the
  actor they were built for. The supported route into Unreal is to bypass
  these plugins entirely: OpenFaceFX JSON → engine curve assets / morph-target
  tracks (the 15 channels are plain named float curves).

### yokimklein/H3EK-FaceFXWrapper — Halo 3 / Reach editing kits
- **Repo:** <https://github.com/yokimklein/H3EK-FaceFXWrapper> (C#, last release Jun 2022)
- **What it does:** shims the Dragon Age-era FaceFX Studio build into the Halo
  editing kits and converts its LTF output into Halo's binary `.FXX`.
- **Compatibility:** parallel audio+text generator for one game family; not a
  practical integration target.

### Same-viseme-set neighbours (not FaceFX, but 1:1 with our output)
- **Meta/Oculus OVRLipSync** and **radiatoryang/lipstick** (Unity fork): use the
  **exact 15-viseme set OpenFaceFX emits** (`sil PP FF TH DD kk CH SS nn RR aa
  E I O U`). Lowest-friction interop; an `OVRLipSyncSequence`/AnimationClip
  exporter is on the roadmap.
- **hecomi/uLipSync** (Unity, MIT): blendshape-curve driven; reachable via a
  Unity AnimationClip exporter.
- **DanielSWolf/rhubarb-lip-sync**: different (Preston-Blair-style) mouth-shape
  model; interop needs a viseme remap table, not a format bridge.

## Summary matrix

| Tool | What it is | Consumes OpenFaceFX output today? | Route to compatibility |
|---|---|---|---|
| Nukem9/FaceFXWrapper | audio+text → Bethesda `.LIP` | **Experimental** Skyrim `.lip` writer shipped (`-o out.lip`); re-encodes real samples byte-exact, **in-game verification pending** ([#12](https://github.com/OpenFaceFX/OpenFaceFX/issues/12)). `.fuz` container + `.lip` header tools also shipped (`openfacefx.bethesda`) | Verify in-game; slot→morph map + header `u22` are documented assumptions |
| FaceFX-UE4 / UE5 plugins | load compiled `.ffxc`/`.facefx` | No | None (proprietary compiler required) — drive UE curves directly instead |
| H3EK-FaceFXWrapper | audio+text → Halo `.FXX` | No | Not a practical target |
| OVRLipSync / lipstick | Oculus 15-viseme runtime (Unity) | **Yes, via `.anim` export** (`write_unity_anim`, `-o out.anim`) | Shipped; `OVRLipSyncSequence` .asset deliberately skipped (version-coupled) |
| uLipSync | Unity blendshape lipsync | Yes, via `.anim` export | Shipped |
| rhubarb-lip-sync | audio → 2D mouth shapes | n/a (different viseme model) | `rhubarb` retarget preset shipped (docs/retargeting.md) |

## Consuming OpenFaceFX output yourself

The JSON is deliberately trivial — a reference reader is ~15 lines:

```python
import json

def load_track(path):
    d = json.load(open(path))
    assert d["format"] == "openfacefx.track" and d["version"] == 1
    # {"PP": [(t0, v0), (t1, v1), ...], ...}  — linearly interpolate between keys
    return {c["name"]: [tuple(k) for k in c["keys"]] for c in d["channels"]}
```

Channel names are blendshape names from the Oculus 15-viseme convention;
values are weights in `[0, 1]`; keys are `[time_seconds, value]`, sorted,
sparse (RDP-reduced). Retargeting to another rig is a name-remap plus
optional weight scale — see `visemes.py` (`PHONEME_TO_VISEME`, `VISEMES`).

## Trademark note

FaceFX® is a registered trademark of OC3 Entertainment, Inc. (whose assets
were acquired by Speech Graphics in September 2025). OpenFaceFX is an
independent open-source project: not affiliated with, endorsed by, or
connected to OC3 Entertainment or Speech Graphics, and it contains no code or
data from FaceFX products. The name is used descriptively to indicate the
category of tool it replaces.
