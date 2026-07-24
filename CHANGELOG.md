# Changelog

All notable changes to OpenFaceFX are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses
[Semantic Versioning](https://semver.org/): while on `0.x`, minor versions may
contain breaking changes; the JSON track format is versioned independently via
its `version` field.

## [Unreleased]

### Added
- **Studio: audio plays with the transport** — when a take has audio (a loaded clip
  or an AI-generated voice), pressing play now plays it, in sync — the audio is the
  transport clock while it runs, so the face/curves/playhead track the real audio.
  Web Audio (`decodeAudioData` + `AudioBufferSourceNode`); scrub/seek re-cue it, it
  restarts on loop, and a transport **🔊/🔇 mute** toggles it.

## [0.23.0] - 2026-07-25

### Added
- **`openfacefx.tts` — built-in speech synthesis** — a pure-numpy formant/source-filter
  synthesizer (`synthesize`, `synth_wav_bytes`, `to_wav_bytes`) turns a transcript into a
  speech-like WAV, reusing `naive_segments` timing so the words line up with the mouth.
  Dependency-free (numpy + stdlib `wave`), deterministic. In the **Studio**, a **🔊 Generate
  voice** button synthesizes the transcript and drives the take from it (spectrogram + energy
  lip-sync) — generate audio instead of only loading it, no key or network. (Robotic/formant
  quality; a natural neural-TTS option via a BYO key is a possible follow-up.)
- **Studio: shared 3D preview + model chooser + custom avatars** — the Workspace now
  shows the **same** model as the Preview tab (the 3D ARKit head is relocated between
  them instead of the Workspace using a flat 2D face). A **3D head / 2D schematic**
  selector makes it a choice, and **＋ avatar** loads your own `.glb` at runtime.
  The Workspace's curves are now the full keyframe editor too — drag a dot to edit,
  double-click to add, alt/right-click to delete, drag empty space to box-select
  (`Preview3D.loadModel`). The preview morph mapping is now a per-mesh resolver that
  matches ARKit channel names across naming schemes (facecap `_L/_R`, ARKit `Left/Right`,
  camel/Pascal case), so a **Microsoft Rocketbox** avatar (exported to glTF) — or any
  ARKit-blendshape head — is driven correctly, not just the built-in head.
- **Unity runtime integration** (`integrations/unity/com.openfacefx.runtime`) — a
  dependency-free UPM package that plays OpenFaceFX takes on Unity characters,
  streaming ARKit blendshape weights (and optional head/eye bone pose) onto a
  `SkinnedMeshRenderer`. Reads the `openfacefx.track` JSON (`.offxtrack`) and the
  ARKit **Live Link Face** CSV; case-insensitive / prefix / PascalCase⇄camelCase
  blendshape name matching lines an `arkit` take up with **Microsoft Rocketbox**
  ARKit avatars (and most ARKit rigs) with no manual mapping. Editor importer +
  "Convert to OffX Clip" menu + a Rocketbox sample. Requires Unity 2021.3+.
- **Studio dockable Workspace** — a **Workspace** tab showing the neutral face,
  Face Graph, animation curves and the audio spectrogram at once on a single
  playhead, as a **resizable dock**: 2×2 panes with draggable splitters, and each
  pane's dropdown puts any panel in any section (layout persists). Includes a
  **curve-select rail** (live value per channel, click to select + solo), a
  **node properties** readout, and full curve editing in-pane.
- **Studio: opt-in curve simplify** — a **⌇ simplify** button RDP-thins the
  selected (or all visible) channel's keyframes for cleaner, FaceFX-sparse curves.
- **Studio: IPA phonemes + word grouping** — the Phonemes strip is two-tier,
  showing phonemes as **IPA** over the **words** they belong to (ARPABET internally;
  IPA is display-only, toggleable). The spectrogram appears once a voice clip is
  loaded; the no-audio state is now labelled.
- **Studio Phonemes-bar editor** — drag a phoneme boundary on the Phonemes strip
  to fine-tune its timing; the viseme curves re-solve from the edited segments
  (gesture/emotion/pose channels are preserved). Undo captures the edit whole. New
  `studio_resolve` bridge + native `/api/resolve`; 3 tests.
- **Studio spectrogram** — the Phonemes lane now draws a real STFT spectrogram of
  the loaded audio (Hann-windowed FFT, ~0–5 kHz, theme-aware heatmap), falling back
  to the waveform/synthetic envelope when there's no clip.
- **Studio pronunciation editor** — Run QA lists each out-of-vocabulary word that fell
  back to G2P with an editable **ARPAbet** field (pre-filled with the rule guess); saving
  writes a CMUdict entry onto the take and **Save all & re-generate** re-solves with the
  corrected pronunciations. QA respects the take's dictionary, so fixed words drop off.

### Fixed
- **Studio Phonemes waveform** — with no audio loaded, the synthetic waveform
  summed every viseme channel, which saturated to 1.0 across ~83% of the clip and
  rendered as a flat band. It now uses a mouth-openness-weighted energy proxy
  normalised to the clip's own peak, drawn as a filled envelope — a real,
  varying waveform. (Real-audio waveforms were unaffected.)

### Added
- **Studio align-from-file** — an **Align from…** control in the Generate panel
  times a take from a real-audio **ASR/caption alignment** instead of naive/energy
  timing: Whisper / WhisperX / Gentle / Vosk JSON, SRT / WebVTT, word-anchor JSON,
  Azure / ElevenLabs word timings, and Allosaurus phones. SRT/VTT and the ASR
  formats carry their own transcript; word-timing formats use the Transcript box.
  Routes to the matching `anchors`/`aligners` adapter → `anchored_segments` →
  track (mirrors the CLI `--anchors` flow) via a `studio_align` bridge + native
  `/api/align`, producing a take timed to the actual speech with its phoneme
  segments (visible on the Phonemes tab). 3 new tests.

## [0.22.0] - 2026-07-24

### Changed
- **`arkit` retarget preset — phonetically corrected shapes.** The viseme→ARKit
  weight table was rebuilt from each viseme's articulatory phonetics (bilabial lip
  seal for `PP`, labiodental lip-to-teeth for `FF`, dental tongue for `TH`,
  sibilant bared teeth for `SS`, rounded funnel/pucker for `O`/`U`/`CH`/`RR`,
  spread front vowels, open jaw), replacing an earlier table that read as generic
  on a real face (F/V rounded the lips, P bared the teeth, S pressed them shut,
  O jutted the jaw forward). **This changes ARKit / Live Link Face / Audio2Face
  export output** for clips with those sounds — a deliberate, versioned change.
  The Studio 3D preview drives the same shapes (`studio_web/studio.js`).

### Added
- **Studio export — Bethesda `.lip` / `.fuz`** — the Export grid now includes the
  Skyrim **`.lip`** writer (experimental, #12 — built from the take's phoneme
  segments) and a **`.fuz`** FUZE container that bundles the `.lip` with the
  loaded voice clip (`openfacefx.bethesda.write_fuz`; Skyrim expects xWMA audio,
  noted on the card). Threads the segments (and, for `.fuz`, the audio) into
  `studio_export`/`_export`; clear errors when a `.lip` has no segments or a `.fuz`
  has no audio. Surfaces the `bethesda.py` + `export_lip.py` tooling in the Studio.
- **Studio import — the read side of Export** — an **Import track…** button in the
  Generate panel loads an exported/interchange file into a new take, mirroring the
  exporters: **track JSON**, **glTF** (.glb/.gltf), **VMD**, **ARKit / Live Link
  CSV**, **NVIDIA Audio2Face** (.a2f.json), **BVH** mocap, and **Rhubarb / Moho /
  Papagayo cue** files. Format is auto-detected by extension (and A2F vs. track
  JSON by content); the file is routed to the matching `read_*` / `import_cues`
  importer via a new `studio_import` bridge + native `/api/import`, then a take is
  created and selected. An importer missing from the running build reports a clear
  message instead of failing. Read-only wrt the pipeline; every importer is the
  numpy/stdlib one already shipped.
- **Studio Curves — box-select multi-keyframe editing** — drag a marquee over
  empty space in the Curves editor to select multiple keyframes of the current
  channel; drag any selected key to **move the whole group** together (re-sorted
  as they cross), or press **Delete/Backspace** to remove them (a channel keeps at
  least one key). Escape or clicking clears the selection; a plain click still
  seeks. Selected keys are highlighted. Frontend-only, pipeline untouched.
- **Studio batch generate** — a **Batch** button (shown when the transcript has
  more than one line) turns each non-empty line into its own take under the
  current actor, generating them in sequence with the same source settings — the
  Studio's take on FaceFX batch analysis. New takes only (an empty first slot is
  reused; nothing is overwritten), then switch between them in the Take menu.
- **Studio pose library** — the Preview pose panel gains named **expression
  presets**: six built-ins (Neutral / Happy / Sad / Surprised / Content / Worried)
  plus your own — set the expression sliders, name it, **save**. **Apply** writes
  the preset's channel values as keys at the playhead (unlisted channels reset to
  0, so a preset is a complete expression state) and syncs the sliders + 3D head.
  Saved presets persist in the workspace. Display-only; the pipeline is untouched.
- **Studio Face Graph — editable, cloneable rig outputs** — select a rig-output
  node and the inspector becomes an editor: tune the weight of each viseme driving
  it, add or remove a viseme, rename it, **clone** the output to tune a variant, or
  delete it — the graph updates live as you edit. Edits are reversible: a per-output
  **↺ Default** restores that output's preset visemes, and a header **↺ Reset**
  reloads the whole rig's defaults (discarding all Face Graph edits). **Custom outputs export**: an
  edited/cloned arkit graph feeds the retargeted formats (Live Link CSV / Audio2Face)
  through an opt-in `fgmap` threaded into `studio_export`/`_export`, and the custom
  preset persists in the saved workspace. Default (unedited) export stays
  byte-identical — verified the cloned output reaches the A2F `facsNames` while the
  default export is unchanged. Also **hardened the rig-preset dropdown** (clears the
  stale selected node, guards the async preset fetch, always rebuilds the inspector +
  redraws) so switching rigs always refreshes. A cloned output additionally takes a
  direct **manual Value** — a built-in output's value is driven (read-only), but a
  clone's is editable: type any constant and it overrides the viseme sum, greys the
  now-inert incoming edges, and exports as a flat channel at that value (`fgconst`);
  a ↺ reverts it to viseme-driven. A viseme-driven custom output also takes a
  **Response** (link) function — `linear` / `quadratic` / `cubic` / `sqrt` (the
  `links.py` #68 shapes) with a live curve preview — that shapes how its summed
  viseme input maps to output, live in the graph and exported via `fglink`
  (per-keyframe, post-retarget). This completes the editable Face Graph.
- **Studio Face Graph — live signal flow** — the Face Graph now animates: press
  play and each input viseme's activation at the playhead propagates through the
  weights, glowing its node and brightening/pulsing its links to the rig targets
  it drives (with live value labels and a travelling pulse along active edges).
  Turns a static diagram into a real-time diagnostic (e.g. watch `FF` light up
  `mouthShrugLower`/`mouthUpperUp`). Display-only — reads the take at the playhead;
  the pipeline and every exporter are untouched. (Editable link functions are the
  next step.)
- **Studio Mapping tab** (#15) — a new **Mapping** view that edits the
  **phoneme → viseme** weight table (the `openfacefx.mapping` / `retarget
  --mapping` layer the solver applies before coarticulation — distinct from the
  Face Graph's viseme→rig retarget preset). Adjust a weight, rename a viseme
  target, add/remove one per phoneme; **Reset** restores the built-in Oculus-15
  table. Turn on **apply on Generate** and the edited mapping drives the next take
  — the generated curves and preview change (verified: remapping every phoneme to
  one viseme collapses the output to that channel). **Download** emits a canonical
  `openfacefx.mapping` JSON that `Mapping.from_json` / `--mapping` accept. Wired in
  both runtimes (`studio_mapping_default`/`studio_mapping_json` Pyodide bridges +
  native `/api/mapping_default` and `/api/mapping_json`, and an opt-in `mapping_json`
  threaded through `studio_generate`/`_generate`). Default (no custom mapping) stays
  byte-identical.
- **Site favicon** — every page now carries the OpenFaceFX logo as its favicon
  (`docs/logo.svg`): the Studio (previously none), the try-it and talking-face
  demos, and the generated track-preview pages, alongside the landing page that
  already had it. The logo ships inside `studio_web/` so it resolves in all three
  Studio runtimes (native, static, deployed).
- **Studio Events tab** (#14) — a new **Events** view in OpenFaceFX Studio that
  auto-authors a typed event layer from the take's own speech
  (`pipeline.derive_events`): **emphasis** beats on stressed syllables and
  **phrase** markers at pauses — the same accents the gesture layer reads. Events
  render on a two-lane timeline over the phoneme/word strip (click a marker to
  seek), list with their payloads, and toggle emphasis/phrase live. They ride in
  the take's track JSON, so the existing exporters emit them as **engine notifies**
  (Unreal `AnimNotify`, Unity events). Wired in both runtimes — a `studio_events`
  Pyodide bridge and a native `/api/events` route reconstruct `PhonemeSegment`s and
  call the already-shipped engine code; no new pipeline logic. Display-only, the
  generated curves stay byte-identical.
- **BVH head/eye-rotation importer** (#32) — a `from-bvh` command and
  `read_bvh(path)` / `parse_bvh(text)` library entries that import a Biovision
  Hierarchy mocap file's head (and eye) joint rotation into signed
  `headPitch/headYaw/headRoll` (+ averaged `eyePitch/eyeYaw` gaze) pose channels —
  the same pose model the VMD importer harvests from 頭/首 bones, so a captured
  head performance (nods, turns, tilts, gaze) layers onto generated lip motion.
  BVH is already intrinsic Euler degrees, so head axes map straight through
  (X→pitch, Y→yaw, Z→roll; neck as a fallback), columns are RDP-thinned and dead
  all-zero axes dropped. Crucially the signed values pass through **unclamped** —
  built directly with `_rdp` rather than `reduce_to_track`, whose positive-only
  "never fires" filter and `[0,1]` clamp (correct for weight channels) would
  discard a head turned only one way. BVH axis conventions vary by exporter, so a
  sign may need flipping per-rig; the values are faithful to the file. Pure numpy
  + stdlib, deterministic, additive.
- **glTF morph-animation importer** (#13) — a `from-gltf` command and
  `read_gltf(path, fps=…, epsilon=…)` library entry that decode morph-target
  (blendshape) **weight** animation from any glTF 2.0 `.glb`/`.gltf` back into a
  track — the read side of the glTF exporter (#49), and the **headless FBX path**
  (FBX has no pure-Python reader, so convert `FBX → glTF` with FBX2glTF/Blender
  once, then import). Reads the `weights`-path animation channel: the sampler input
  is the time grid, its output is the `n_frames × N` frame-major weight matrix,
  and the N morph names come from `mesh.extras.targetNames`; the matrix is
  RDP-thinned to a track via `reduce_to_track`, exactly like the CSV/VMD importers.
  Handles both containers (binary `.glb` chunk, base64 `data:` buffer, external
  `.bin`), float **and** normalized-int accessors, and `CUBICSPLINE` samplers (keeps
  the keyframe value). Signed head/eye pose is not imported — glTF morph weights are
  the `[0,1]` blendshape model. A write → read round-trip recovers every channel
  within the RDP tolerance. Pure numpy + stdlib, deterministic, additive.
- **OpenFaceFX Studio** — a web-based facial-animation studio (the FaceFX Studio
  workflow, open) served by a new `openfacefx studio` command. One dependency-free
  SPA (`src/openfacefx/studio_web/`) with FaceFX-style views — **Preview** (live
  viseme+gesture blend), **Phonemes** (waveform + aligned strip), **Curves**
  (coarticulated curves), **Face Graph** (viseme→rig retarget network via the
  `links` #68 functions and `mapping` presets), and **Export** (every exporter,
  in-browser) — plus an **Assistant**. Runs three ways from the same frontend:
  in the browser via Pyodide (zero-install, pipeline runs client-side), as a
  standalone desktop tool (`openfacefx studio` serves it against the native
  pipeline over a stdlib HTTP API — Tauri/Electron-wrappable), and as the basis
  for a SaaS. See `docs/studio.md`.
- **AI Assistant with bring-your-own-key LLMs** — transcript cleanup, OOV→ARPAbet
  pronunciation, emotion direction (valence/arousal), and performance direction,
  via Anthropic (direct browser calls), OpenAI/Gemini (through a stateless
  `/api/llm` relay), and local open-source models (Ollama/vLLM/LM Studio,
  OpenAI-compatible). Provider API keys are stored **zero-knowledge**: encrypted
  in the browser with a master password (Web Crypto PBKDF2-SHA256 · 600k →
  non-extractable AES-256-GCM), only ciphertext persisted — key and password never
  leave the machine.

## [0.21.0] - 2026-07-20

### Added
- **Low-confidence phoneme QA flagging** (#72) — a `confidence_flags(segments,
  min_confidence)` helper (a sibling of `cue_flags`) and a `confidence_warnings`
  key in `qa.summarize()`, plus a `--min-confidence` CLI flag alongside
  `--min-cue`/`--max-cue`. Surfaces the low-confidence phonemes FaceFX flags for
  hand-fixing — a time-sorted `[{phoneme, start, confidence}]`. It reads the
  `PhonemeSegment.confidence` the track already carries; no built-in aligner
  populates it, so it is **inert (empty) unless a custom external-aligner adapter
  supplied per-phone confidences** — the QA schema gains the always-present key
  without changing any existing output. Pure-stdlib, deterministic, additive.
- **Fleming-Dobbs mouth-shape retarget preset** (#71) — a `fleming_dobbs` preset
  mapping the Oculus-15 visemes onto the Fleming & Dobbs phoneme-cluster shapes
  (`MBP`/`NLTDR`/`FV`/`TH`/`GK`/`SH`/`EHSZ`/`AA`/`IY`/`O`/`rest`), a sibling of the
  `preston_blair`/`rhubarb` presets for the Papagayo-NG / traditional-animation
  audience. Auto-wires to `--retarget fleming_dobbs` and the API; a derived
  `FLEMING_DOBBS_TO_VISEME` inverse table imports FD-labelled timelines back to
  visemes (via `build_cue_track`). The grouping is re-expressed from the published
  Fleming & Dobbs convention (ISBN 9781886801813) — a functional fact — **not**
  copied from Papagayo-NG's GPLv3 data. Pure data, deterministic, additive.
- **Vosk offline-ASR word-timestamp adapter** (#70) — `from_vosk(json_text,
  min_conf=0.0)` and `--anchors-format vosk` read Vosk's `SetWords(True)` output
  (`{"result": [{word, start, end, conf}], "text": ...}`, or a list of streaming
  chunks — their `result` arrays are concatenated) into anchors, filling the one
  gap in the offline lightweight-ASR adapters (Whisper/WhisperX/Gentle/Allosaurus
  already ship). Vosk is self-transcribing (`--text` optional), and a `--vosk-min-conf`
  gate drops words below a per-word confidence — the increment over the generic
  `parse_word_anchors`. Vosk is Apache-2.0, so parsing its JSON carries no GPL
  contamination; recognition runs externally, we only parse (pure-Python,
  deterministic). Additive.
- **Procedural breathing channel** (#69) — an opt-in idle chest rise/fall added to
  the gesture layer: a `breath` channel in `[0, 1]` (a slow ~0.25 Hz sine,
  ~15 breaths/min, with a per-clip rate jitter and random phase), for a rig with a
  breath target such as Live2D `ParamBreath`. Enabled with `GestureParams(
  breath_enable=True)` or the CLI `--breath` flag (alongside `--gestures`), built
  like the proven `_ambient()` head-sway generator on its own rng sub-stream (4) so
  turning it on leaves the blink/brow/head/gaze channels byte-identical; OFF by
  default, so existing output is unchanged. Flows through the pipeline by name
  (retarget/glTF/Unity/Godot carry it as a `breath` target). Pure numpy,
  deterministic in the seed, additive.
- **Nonlinear link functions** (#68) — FaceFX-style response curves beyond the
  linear gain+offset, in a new `openfacefx.links` module: `linear`, `quadratic`,
  `cubic`, `sqrt`, `negate`, `constant`, and `clamped_linear`. Applied at the two
  sites that already compute `clamp(gain*v + offset)`: an `--adjust` entry (and
  `retarget.apply_adjust`) may now be a link spec `{"function": name, ...params}`
  instead of a `(gain, offset)` trim, and a mapping-file target may carry a per-
  target `link` (mapping schema **v3**). Gives integrators FaceFX-grade control
  over how a driver reshapes a target's response (e.g. an ease-in `jawOpen`, or a
  thresholded `tongueOut`). The linear/no-link paths are byte-identical (a mapping
  with no link stays schema v2), so all existing output is unchanged. FaceFX's
  `inverse` (no published formula; reciprocal semantics ill-suited to `[0,1]`
  weights) and the two-input `corrective`/`one-clamp` links are intentionally out
  of scope. Pure numpy+stdlib, deterministic on py3.9/3.13, additive.
- **VRM emotion-expression mapping** (#67) — the `.vrma` exporter now drives the
  VRM emotion presets from the emotion layer, connecting two subsystems that
  existed but were disconnected: `emotion.py` produces FACS-style AU channels and
  `export_vrma` has `happy`/`angry`/`sad`/`surprised` slots, but `PRESETS["vrm"]`
  mapped only the five vowels, so retargeting silently dropped emotion. A new
  `VRM_EMOTION_MAP` overlay (`smile`→happy, `frown`→sad, `brow_lower`→angry,
  `brow_raise`→surprised) is applied **inside** `build_vrma` on top of the vowel
  preset — so an emotion-baked track's AUs now fill the `.vrma` emotion slots with
  no exporter API change and no CLI flag (bake emotion, export `.vrma`). It is NOT
  merged into the shared `vrm` preset, so `--retarget vrm` output for every other
  exporter is byte-identical, and a viseme-only `.vrma` is unchanged. `relaxed`
  and `cheek_raise` are intentionally left unmapped (no clean low-arousal AU; the
  smile channel grows with arousal → happy). Deterministic, additive.
- **Reviewable OOV pronunciation-dictionary emit** (#66) — a new `emit-oov-dict`
  CLI verb and `G2P.emit_oov_dict(text)` turn the out-of-vocabulary words the tool
  already detects (`g2p.oov_words`, which `qa.summarize` warns to "add to a
  pronunciation dict") into an **editable CMUdict** of rule-G2P guesses: one
  `WORD  P1 P2 P3` line per OOV (uppercase, ARPAbet, sorted for stable bytes) behind
  a header flagging them as guesses to review — the offline analogue of MFA's
  validate→g2p workflow. Fix the phonemes and load them back with `--cmudict`; the
  round-trip resolves exactly the words that were OOV. `--transcript FILE` or
  `--text`, `--cmudict` pre-load skips already-defined words. Pure-stdlib,
  deterministic, additive.
- **Live2D Cubism `.exp3.json` expression-pose exporter** (#65) —
  `write_live2d_expression(track, path)` / `build_expression(track)` and a
  `.exp3.json` output extension freeze the track's pose at **one instant** as a
  Cubism expression, the static companion to the shipped animated `motion3.json`
  (issue #20). VTube Studio binds these to hotkeys. Same viseme→ParamId targeting
  as the motion exporter (`--live2d-params`/`--live2d-model3`/`--live2d-param`);
  `--exp3-at` picks the instant (default: the peak-activity frame). Values are
  absolute (`Blend: "Overwrite"` — a frozen pose is a value, not a delta from the
  parameter default). Emits exactly the schema-permitted keys (`Type`/`FadeInTime`/
  `FadeOutTime`/`Parameters` with `Id`/`Value`/`Blend`; no `Version`,
  `additionalProperties: false`), verified against Live2D's CubismSpecs. Pure-stdlib
  JSON, deterministic on py3.9/3.13, additive.
- **NVIDIA Audio2Face blendshape-JSON interop** (#64) — `write_a2f(track, path)` /
  `read_a2f(path)` with a `.a2f.json` output extension and a `from-a2f` CLI verb
  read and write A2F's dense per-frame ARKit-FACS JSON (`facsNames` + `weightMat`
  + `numFrames`/`numPoses`/`exportFps`), the public non-USD interchange path for the
  dominant audio-driven-face tool. **Export** twins `export_livelink`: sample the
  track onto a fixed-fps grid, one row per frame; `facsNames` are the track's own
  `[0,1]` channel names verbatim (A2F's pose set is a configurable ~46/52 list, so no
  fixed header is forced), so a `--retarget arkit` track lands as ARKit names and a
  still-viseme track is reported. Head/eye pose channels are excluded (A2F carries
  weights, not angles). **Import** twins `importers_csv`'s wide branch: `facsNames`
  become channel names verbatim, `weightMat` rows become frames timed by
  `exportFps` (a `--fps` override wins; a file without it falls back), clipped to
  `[0,1]` and RDP-thinned via `reduce_to_track`. `write_a2f`→`read_a2f` reconstructs
  every channel (round-trip proof). stdlib `json` + numpy, deterministic on
  py3.9/3.13 (fixed 6-dp weights), additive.
- **VRM Animation (`.vrma`) expression-clip exporter** (#62) — `write_vrma(track,
  path)` / `build_vrma(track)` and a `.vrma` output extension write a VRM 1.0
  animation clip a VRM avatar will actually lip-sync to. Our glTF exporter drives
  generic morph-target `weights`, which VRM runtimes **ignore** for expression
  playback; this emits the `VRMC_vrm_animation` extension instead, where each
  expression is a node whose **translation X** carries the `[0, 1]` weight over
  time. Like the other engine exporters it maps onto the VRM vowel expressions
  (`aa/ih/ou/ee/oh`) internally via `PRESETS["vrm"]` (so `--retarget` is rejected),
  registers each under `expressions.preset.<name>.node`, and — since the root
  extension needs only `specVersion` — writes a skeleton-free, expression-only clip
  (no rig required). `--vrma-head-node` additionally maps the signed head pose onto
  `humanoid.humanBones.head` as a quaternion rotation. `.vrma` is the GLB binary
  container; the `.gltf` JSON form parses too. Reuses `export_gltf` wholesale (its
  float32 accessor packer, GLB writer, base64 `data:` URI path and
  Euler→quaternion helper). Consumed by UniVRM, `@pixiv/three-vrm-animation`, the
  Blender VRM add-on, VRoid Hub and VMagicMirror. numpy + stdlib, deterministic on
  py3.9/3.13, verified by a full accessor round-trip (each expression node's
  `translation.X` reconstructs its weight channel within 1e-6). Additive — existing
  output is byte-identical.
- **Esoteric Spine slot-attachment lip-sync exporter** (#63) — `write_spine(track,
  path)` / `build_spine` / `splice_spine` and a `.spine.json` output extension write
  a Spine (Esoteric Software) mouth-slot attachment timeline, reaching the de-facto
  2D game-animation runtime (Unity/Unreal/Godot/web/C++). A Spine attachment
  timeline is switch data — `animations.<anim>.slots.<slot>.attachment =
  [{"time": <seconds>, "name": <attachment>}]` — so this reuses the dominant-cue
  reduction in `export_cues` (one Rhubarb A–H/X shape per interval) and maps each
  shape to an attachment name (`mouth_a`..`mouth_x`, override with
  `--spine-attachments`). Two modes, matching Rhubarb's own Spine bridge:
  **splice** (`--spine-base rig.json`) inserts the mouth timeline into an artist's
  existing Spine project, leaving bones/skins/other slots/other animations
  byte-for-byte untouched; **standalone** emits a minimal skeleton (one bone, one
  mouth slot, a skin listing the attachments as region stubs). Times are seconds,
  so no fps quantisation. `read_spine_cues` recovers the timeline for the round-trip
  proof. Pure stdlib `json`, deterministic on py3.9/3.13, additive.

## [0.20.0] - 2026-07-13

### Added
- **VMD (MikuMikuDance) morph-animation importer** (#60) — `parse_vmd(bytes)` /
  `read_vmd(path)` and a `from-vmd` CLI verb read a `.vmd` back into a `FaceTrack`,
  the read side of the 0.19.0 `.vmd` exporter. Morph frames invert the unambiguous
  vowel subset (あ→aa, い→I, う→U, え→E, お→O, ん→nn); any other morph passes through as
  its own channel (reported, never dropped, since the forward map is many→one); the
  頭/首 head bones become `headPitch/headYaw/headRoll`, and the camera/light/IK
  tail is skipped. A studio can now bring a `.vmd` lip library in to re-coarticulate,
  retarget or re-export it. Pure `struct` + stdlib, deterministic on py3.9/3.13,
  verified by a true round-trip against the writer (no MMD needed). Additive —
  existing output is byte-identical.
- **ARKit / Live Link Face wide-CSV exporter** (#61) — `write_livelink_csv(track,
  path)` / `livelink_csv_string(track)` and a `.livelink.csv` output extension write
  the per-frame CSV Epic's Live Link Face app records, the write side of the
  `importers_csv` wide branch that had no counterpart. Emits the canonical 61-column
  header (`Timecode,BlendShapeCount,` + 52 ARKit `ARFaceAnchor.BlendShapeLocation`
  coefficients + 9 head/eye columns, verified against JimWest/PyLiveLinkFace) with
  SMPTE `HH:MM:SS:FF` timecodes; channels match columns case-insensitively so a
  `--retarget arkit` track lands directly (a still-viseme track warns), head/eye
  columns are zero-filled for now. So a synthetic performance can be replayed as a
  Live Link Face take in MetaHuman Animator / Unreal / DCC retarget tools. numpy +
  stdlib, deterministic, verified by a true round-trip through our own wide-CSV
  importer + exact-bytes golden. Additive.
- **Acoustic phoneme-recognizer adapters** — `from_allosaurus(text)` and the generic
  `from_phone_timestamps(...)` (new `aligners_acoustic.py`), plus the
  `--anchors-format allosaurus|phones` CLI formats. These close the one capability
  FaceFX Studio has that OpenFaceFX lacked — **phonemes from audio with no
  transcript**. The neural/DSP recognition runs in an external tool the user already
  has (Allosaurus, wav2vec2 phoneme-CTC, PocketSphinx `-allphone`, …); the adapter
  parses its `start duration/​end phone` output (IPA/ARPAbet/SAMPA) into
  `PhonemeSegment`s that feed `generate_from_alignment` — so the core stays numpy +
  stdlib and deterministic, exactly like the Whisper/Gentle adapters. Unknown phones
  fall to `sil`, gaps become silence, CTC overlaps are clamped. The phone-timing
  formats derive their timeline from the file, so `naive --anchors phones.txt
  --anchors-format allosaurus -o out.json` needs **neither `--text` nor `--duration`**
  (also relaxes the same requirement for `gentle-phones`). Verified end-to-end
  (recognizer output → track) plus the alphabet/timing/gap/overlap paths.
- **Acoustic demo** — `tools/build-acoustic-demo.sh` builds the honest end-to-end
  proof of the acoustic adapter: it runs a real phoneme recognizer (Allosaurus) on a
  speech clip with **no transcript**, feeds the recognized phones through
  `--anchors-format allosaurus`, and bakes a self-contained `examples/acoustic-demo.html`
  that plays the real audio with the phoneme lane scrolling in sync. Ships with a real
  3.27 s speech sample (`examples/speech.wav`). The recognizer (ML) runs once, locally;
  the committed `examples/speech.allosaurus.txt` pins its output so the hosted
  `acoustic.html` is rebuilt by the numpy-only pipeline on every Pages deploy — no ML
  in CI. (An earlier draft paired the render with a placeholder tone; replaced.)

## [0.19.1] - 2026-07-12

Hardening and performance. No API changes; every valid input produces
byte-identical output to 0.19.0 (the prior test suite passes unchanged).

### Fixed
- **VMD exporter crash on negative keyframe times** — `vmd_bytes`/`write_vmd`
  threw `struct.error` on tracks carrying negative (anticipatory/preroll,
  `allow_negative_time`) keyframe times; the frame number is now clamped to 0
  like the glTF exporter, and `fps` is validated `> 0`. VMD was the only
  exporter of the eight that crashed on such tracks.
- **Clear errors at input boundaries** — malformed hand-edited `.track.json`
  (`from_dict`/`read_json`), `events`/`variants` blocks, Rhubarb cue files
  (`from-cues`), and `--mapping` files now raise a `ValueError` naming the
  offending field/index instead of a bare `KeyError`/`TypeError`/`AttributeError`
  — which was both opaque and slipped past the CLI's `except (OSError, ValueError)`
  into a raw traceback. Non-finite (`NaN`/`Infinity`) times in the TTS/aligner
  parsers (parsed by `json.loads` by default) are rejected at the `TimingEvent`
  schema boundary, and negative VOICEVOX per-mora durations are rejected, before
  either can reach the solver.
- **fps/duration validation** — `generate_naive`/`generate_from_alignment`/
  `naive_segments` reject non-finite or `<= 0` fps/duration (previously a silent
  empty or `NaN` track, written with exit 0); the scalar-generate CLIs exit
  cleanly on a bad `--fps`/`--duration`.

### Changed
- **Long-clip performance** — the always-on dominance blend is frame-blocked,
  capping peak memory (the `O(clip²)` temporaries that thrashed at ~3.7 GB): a
  ~10-minute clip drops from ~35 s to ~6 s (6.9×) and ~3.7 GB to ~1 GB. The JALI
  hard-constraint pass uses `searchsorted` index ranges instead of per-segment
  boolean scans (21–31× on the `--jali` path). Both are **byte-identical** — no
  emitted track changes.

## [0.19.0] - 2026-07-12

### Added
- **MikuMikuDance `.vmd` exporter** ([#57](https://github.com/OpenFaceFX/OpenFaceFX/issues/57)):
  `write_vmd(track, path, ...)` + `vmd_bytes(track) -> bytes` in a new
  `export_vmd.py`, reaching the MMD / VTuber ecosystem (blender_mmd_tools, three.js
  `MMDLoader`, babylon-mmd, Saba, MMDAgent-EX) that reads neither glTF nor our
  other formats. Little-endian `struct` + stdlib only; native visemes map to the
  Japanese kana lip morphs (あ/い/う/え/お/ん, consonants collapsing to the nearest
  vowel/closed shape like the `vrm` preset), map overridable via `morph_map=`.
  Wired into the CLI (`-o motion.vmd`, `--vmd-model`/`--vmd-fps`). Deterministic
  (pinned golden byte-hash) and byte-identical on py3.9/3.13; verified by
  re-parsing its own bytes, no MMD needed.
- **VOICEVOX AudioQuery timing adapter** ([#58](https://github.com/OpenFaceFX/OpenFaceFX/issues/58)):
  `parse_voicevox(json_text) -> List[TimingEvent]` in `timing.py`, joining the
  `parse_cartesia`/`parse_azure_visemes`/`parse_polly_marks` vendor-adapter family
  (`--format voicevox`). Turns a VOICEVOX `/audio_query` into viseme-unit events —
  timeline from `prePhonemeLength`, per-mora `consonant_length`+`vowel_length`,
  `pause_mora` gaps, `postPhonemeLength`, all ÷ `speedScale` — and a
  `VOICEVOX_TO_TARGET` OpenJTalk-phoneme→Oculus-15 map. One adapter covers the
  API-compatible forks (COEIROINK, SHAREVOX, LMROID, AivisSpeech). With the `.vmd`
  exporter this is the first pure-Python VOICEVOX → visemes → MMD JP pipeline.
  Pure JSON in, deterministic, numpy-free.
  - **Pause-length overrides** ([#59](https://github.com/OpenFaceFX/OpenFaceFX/issues/59)):
    `parse_voicevox` now honors the top-level `pauseLength` (replaces every pause
    mora's length globally when set) and `pauseLengthScale` (multiplies) fields,
    composed as `(pauseLength ?? pause_mora.vowel_length) * pauseLengthScale ÷
    speedScale` — the VOICEVOX engine's verified replace-then-scale order. A pure
    superset: absent/null fields (with `pauseLengthScale` 1.0) are byte-identical to
    before.
- **JALI follow-ups** ([#53](https://github.com/OpenFaceFX/OpenFaceFX/issues/53),
  follow-up to [#19](https://github.com/OpenFaceFX/OpenFaceFX/issues/19)): two more
  coarticulation habits and NVIDIA-A2F-style tongue-channel tuning, all opt-in and
  byte-identical to v0.18.2 when off/default.
  - Two new JALI habits, each individually toggleable via `jali_rules` and **off
    by default**: `short_no_jaw` holds the jaw at the neighbouring vowel level
    through a *short* obstruent or nasal so a quick stop/nasal can't dip it, and
    `wordfinal_lip` gives a word-final lip-shaped phoneme an earlier onset so its
    lip shape anticipates (word-final is approximated as pre-silence /
    utterance-final, as the phoneme stream carries no inter-word boundaries). Adds
    an `obstruent` category and a `habits` block (thresholds) to
    `data/jali_rules.json`.
  - Optional per-target **`gain`/`offset`** fields on the mapping schema — at
    keyframe reduction a channel becomes `clamp(gain*value + offset, min, max)`,
    chiefly to scale/bias the independent tongue channel (NVIDIA-A2F-style). This
    bumps the **mapping schema to version 2**; version-1 files still load, the
    absent fields reading as the no-op defaults (`gain=1.0`, `offset=0.0`), so
    existing mappings stay byte-identical.

### Changed
- `Mapping.to_json` now writes schema **version 2**, emitting `gain`/`offset` only
  when non-default so a mapping that uses no A2F tuning keeps its minimal target
  entries. `Mapping.from_json` reads both version 1 and 2.
- **ARKit retarget preset output change** (part of
  [#53](https://github.com/OpenFaceFX/OpenFaceFX/issues/53)): the alveolar `DD`
  viseme (`t`/`d`/`l`) now drives `tongueOut` at 0.2, matching `nn`. A deliberate,
  versioned change to the shipped preset — tracks containing `t`/`d`/`l` gain a
  small `tongueOut` channel, and tongue-less rigs reroute it to `jawOpen` exactly
  as `TH`/`nn` already do. Velar `kk` (`k`/`g`) is left tongue-free on purpose
  (back-of-tongue articulation; ARKit's `tongueOut` is protrusion). Existing
  non-`t`/`d`/`l` output and the `TH`/`nn` channels are unchanged. See
  `docs/retargeting.md`.

## [0.18.2] — 2026-07-11

### Fixed
- PyPI project page: the logo and viseme-gallery images used repo-relative
  paths, which render on GitHub but break on PyPI. All `docs/` `<img>` sources
  are now absolute `raw.githubusercontent.com` URLs, so they render on both.

## [0.18.1] — 2026-07-11

### Added
- Animated **talking-head** demo (the README hero) — a self-contained SMIL
  flip-book generated from a real track by `tools/build_talking_head.py`, and a
  **"Hear it"** audio demo (`examples/talking-face-demo.html`, also on the site
  at `/face.html`) that plays real speech with the mouth synced to the audio.

### Changed
- Docs/metadata release (no library code change): README leads with `pip
  install openfacefx` (now on PyPI), links the Buy Me a Coffee funding page, and
  the project uses the `openfacefx.com` custom domain. Refreshes the PyPI page.

## [0.18.0] — 2026-07-11

### Added
- **WebVTT input (`parse_vtt`)** (closes
  [#55](https://github.com/OpenFaceFX/OpenFaceFX/issues/55)): the read-side inverse
  of the #41 caption exporter — `openfacefx.parse_vtt(text) -> List[Anchor]` and a
  `--anchors-format vtt` that drives generation from an existing subtitle file
  (self-transcribing like `srt`, no `--text`). It handles the `WEBVTT` header,
  blank-line cue blocks, optional cue identifiers, ignored `NOTE`/`STYLE`/`REGION`
  blocks, `HH:MM:SS.mmm` and hour-less `MM:SS.mmm` timing with cue settings
  stripped, and the **karaoke** case the #41 writer emits (inline
  `<timestamp><c>word</c>` spans) — recovering one anchor **per word**, each
  inside its cue span, otherwise a single cue-level anchor with inline tags
  stripped. Co-located in `export_captions` with `vtt_text`/`_karaoke_payload` so
  read and write can't drift: `parse_vtt(vtt_text(cues))` round-trips the timings
  within millisecond rounding in **both** plain and karaoke modes. Malformed input
  raises a clear `ValueError`; additive (no `--anchors-format vtt` → output
  unchanged); stdlib `re` only, deterministic.
- **First-class adapters for the open-source aligners: Whisper, WhisperX, Gentle**
  (closes [#54](https://github.com/OpenFaceFX/OpenFaceFX/issues/54)): a new
  `openfacefx.aligners` module — siblings of `from_azure_word_boundaries`, stdlib
  `json` only — closing the asymmetry that shipped adapters for every commercial
  TTS source but punted the free tools to the user in three places (now replaced
  with the built-ins). `from_whisper_json` parses OpenAI Whisper `verbose_json`
  (`segments[].words[]` or a flat `words[]`), `from_whisperx` parses WhisperX
  `segments[].words[]`, and `from_gentle` parses Gentle `words[]` — all returning
  the normalized `Anchor` list. **`from_gentle_phones`** turns Gentle's per-word
  `phones[]` (relative durations, ARPAbet with `_B`/`_I`/`_E`/`_S` suffixes) into
  absolute `PhonemeSegment`s that sum to the word span, a phone-accurate path
  that skips the naive spacer and feeds `generate_from_alignment` directly. The
  adapters tolerate the key variance across implementations (`word`/`text`,
  `probability`/`score`) and **drop unaligned words deterministically** (a missing
  timestamp, or Gentle `case != "success"`), never crashing; an out-of-inventory
  symbol falls to `sil`. Wired into the CLI as `--anchors-format whisper |
  whisperx | gentle | gentle-phones` — the word paths self-transcribe (the aligner
  supplies the words, so no `--text`). Additive: with no `--anchors-format`, output
  is unchanged. Deterministic, numpy + stdlib.

## [0.17.0] — 2026-07-11

### Added
- **Multi-language pronunciation framework** (closes
  [#8](https://github.com/OpenFaceFX/OpenFaceFX/issues/8)): the grapheme-to-phoneme
  stage is now a **protocol** (`openfacefx.pronounce.Pronouncer` — a tokenizer +
  word/phoneme map), with the existing `G2P` as the English implementation, plus
  the additive i18n machinery around it — all **opt-in, so the default English
  path is byte-identical** (verified against a captured baseline + the full
  suite). Adds: **IPA + X-SAMPA aliases** for all 39 phonemes in `phonemes.py`
  (`IPA_ALIASES`/`SAMPA_ALIASES` + `to_ipa`/`from_ipa`/`to_sampa`/`from_sampa`/
  `from_alphabet`), bijective so `internal → alias → internal` round-trips; a
  **dictionary loader** (`read_dictionary`, `G2P.load_dictionary`) for `.dict`
  files declaring a `locale` and phoneme `alphabet` (arpabet/ipa/sampa), mapped
  into the internal inventory; a **pronouncer hook** `callable(word, prev, next)
  -> phonemes | None` consulted between dictionary lookup and the rule fallback
  (FaceFX's lookup→pronouncer→rules order) with correct prev/next context; and a
  **pluggable tokenizer** so non-Latin scripts survive the default `[A-Za-z']+`
  split. A phoneme with no internal equivalent passes through and falls to `sil`
  at the viseme stage (documented), never a crash.
- **JALI coarticulation rules, empirical onset/decay timings** (closes
  [#19](https://github.com/OpenFaceFX/OpenFaceFX/issues/19)): a new
  `openfacefx.coart_jali` module + `data/jali_rules.json` **data-driven rule
  table** ([JALI, SIGGRAPH 2016](https://www.dgp.toronto.edu/~elf/JALISIG16.pdf))
  over the component coarticulation stage, entirely **opt-in** behind
  `CoartParams(jali=True)` and **byte-identical to the legacy path when off**
  (the whole existing suite stays green; verified against a captured baseline).
  It adds the 4 hard constraints (bilabial/labiodental lip closure, sibilant jaw
  narrowing, non-nasal lip opening), the high-value habits (duplicated-viseme
  merge across word boundaries — "po_p m_an"; lip-heavy visemes UW/OW/OY/w/S/Z/J/C
  anticipate and hold longer; tongue-only visemes never touch lip channels), and
  an **empirical per-phoneme onset/decay lookup** (context-dependent post-pause vs
  post-vowel onsets, ~150 ms lip-protrusion extension) replacing the per-class
  timing constants when `jali_timing` is on. Every constraint/habit is
  individually toggleable via `jali_rules` (`JALI_RULE_IDS`); the categories,
  floors/caps and timings are plain JSON so new measurements drop in. The tongue
  articulator class already existed in the mapping schema, so no schema version
  bump was needed.
  - **Deferred (flagged):** the short-obstruent/nasal "leave-the-jaw-untouched"
    and word-final anticipatory-lip habits, and the NVIDIA-A2F-style tongue-channel
    gain/offset mapping fields + ARKit tongue targets (which would bump the mapping
    schema / change the shipped preset) — left for a follow-up.

## [0.16.0] — 2026-07-11

### Added
- **Streaming / real-time generator** (closes
  [#43](https://github.com/OpenFaceFX/OpenFaceFX/issues/43)): a new
  `openfacefx.streaming` module (`StreamingGenerator`, `frames_to_track`) that
  carries coarticulation state across pushed phoneme chunks in **constant
  memory** and emits keyframes incrementally — `push(chunk)` returns the frames
  just finalized, `flush()` emits the tail. It reuses the **exact** offline
  component math (a `coarticulation._blend` extracted from `build_viseme_curves`,
  which stays byte-identical) over a bounded segment window.
  - **Honestly: it reproduces `generate_from_alignment` _within tolerance_, not
    bit-exactly.** The dominance is a Laplacian bump `exp(-theta·|t−c|)` —
    exponential, infinite support, normalized over every segment — so bounded
    memory and a finite look-ahead both omit exponentially small tails; no finite
    window is literally bit-identical to the offline solve. It converges fast:
    `look_ahead` is the single latency↔fidelity dial with an `O(exp(-theta·W))`
    error bound (W≈1.5 s → ~1e-2, W≈3 s → ~1e-4, W≈4.5 s → ~1e-6), and `0` is
    zero-latency causal-only (no anticipation). One case is **exact**: when the
    window covers the whole clip (`look_ahead`/`back_span` ≥ clip length) the
    per-frame blend is bit-identical to offline.
  - Chunk boundaries never matter (the same clip in 1 or K chunks yields
    bit-identical frames); the ring buffer is `O(window)`, not `O(stream)`; and
    the generator is causal — a later chunk cannot alter an already-emitted frame
    (the optional `causal_smooth` is a past-only one-pole filter, distinct from
    the offline symmetric `postprocess.smooth_matrix`). In-process only; network
    transport is out of scope. Deterministic, numpy + stdlib.
- **VO delivery QA auditor (`audit`)** (closes
  [#42](https://github.com/OpenFaceFX/OpenFaceFX/issues/42)): a new
  `openfacefx.vo_audit` module (`audit_delivery`, `audit_report_text`) and an
  `audit --manifest FILE --delivered FOLDER` command that reconciles a delivered
  VO folder against the #40 loc-table the way a localization vendor's
  pre-delivery QA pass does — the reconciliation pair to the manifest driver. It
  reports, deterministic and itemized keyed by loc-ID: **missing** lines (a row
  whose declared audio is absent), **orphan** files (delivered audio no row
  references), **duration** outliers (actual `wav_duration` outside a
  configurable `--duration-tolerance` of the `len(text)/--cps` estimate — a take
  inside tolerance is never flagged), **empty/near-silent** takes (~0 duration or
  ~0 RMS), **naming** violations (a stem that doesn't match the loc-ID), and a
  **language-coverage matrix** surfacing per-locale holes. It exits nonzero on
  issues (a CI gate), prints a human worst-first table or a `--json` report (the
  `batch_summary.json` schema style), is **read-only** over the delivered folder
  (writes nothing), reuses `pipeline.wav_duration`, and shares the #40
  `read_manifest` parser. Additive — no existing command changes.
- **Subtitle / caption co-generation (SRT + WebVTT)** (closes
  [#41](https://github.com/OpenFaceFX/OpenFaceFX/issues/41)): a new
  `openfacefx.export_captions` module (`write_captions`, `write_srt`,
  `write_vtt`, `build_cues`, `word_timings`, …) and a `captions` command that
  write SubRip (`HH:MM:SS,mmm`) and WebVTT (`HH:MM:SS.mmm`) subtitles from the
  **same alignment the lip curves use** — word spans come from
  `texttags.naive_word_segments`, whose phoneme segments are byte-identical to
  the `pipeline.naive_segments` the visemes are reduced from, so captions and
  lip motion share one source of truth. Cues are packed under a max-line-length ×
  max-lines **wrap budget** (never exceeded), split at sentence ends and pauses,
  held long enough to read at a configurable **reading speed** (characters per
  second), and kept monotonic + non-overlapping; `--karaoke` adds WebVTT `<c>`
  word spans with inline cue timestamps that fall inside their cue span.
  Co-generate a track and its captions in one run with `naive --emit-captions`,
  or write a caption sidecar next to every naive-mode track with `batch
  --captions srt|vtt`. `srt_text` is the exact inverse of `anchors.parse_srt`
  (a round-trip recovers the cue spans). Pure stdlib, deterministic.
- **Loc-table / dialogue-database batch driver (`batch --manifest`)** (closes
  [#40](https://github.com/OpenFaceFX/OpenFaceFX/issues/40)): a new
  `openfacefx.batch_manifest` module (`read_manifest`, `manifest_jobs`) and a
  `--manifest FILE` flag that drives `batch` from a **localization string table**
  — a CSV/TSV keyed by loc-ID, one row per line (`audio`, `text`, `language`,
  `character`, optional `mapping`/`style`/`out`) — the way real game VO is
  authored (Unity/Godot/Unreal String Table Collections, FaceFX entrytags),
  instead of a directory of same-stem files. Each row emits one track through the
  **same** pipeline, output writers, summary table, `--machine-readable` NDJSON
  stream and `--ledger`; the `mapping`/`style` columns thread into that row's
  solve and `language`/`character` ride along on the summary row. Columns are
  header-matched forgivingly (case/spacing/punctuation ignored). A missing-audio,
  unreadable or malformed row is an **isolated per-row failure** (the batch
  continues, surfaced in summary + NDJSON + ledger), matching directory-mode
  behaviour. Parsing is stdlib `csv` only (PO/XLIFF and pivoted one-column-per-
  locale tables are noted future follow-ups). The directory-walk mode is
  untouched — with `--manifest` absent its output is **byte-identical**.

## [0.15.0] — 2026-07-11

### Added
- **SSML input adapter: drive lip-sync from the same SSML you feed your TTS**
  (closes [#52](https://github.com/OpenFaceFX/OpenFaceFX/issues/52)): a new
  `openfacefx.ssml` module (`parse_ssml(text) -> (clean_text, tags)`) and a
  `naive --ssml` flag (auto-enabled when `--text` opens with a `<speak>` root).
  It is a **thin front-end over the #7 text tags**, not a new animation path:
  stdlib `xml.etree` parses the W3C markup Azure/Google/Polly consume and emits
  the **same `(clean_text, tags)`** the bracket front-end yields, then the
  unchanged naive pipeline runs. `<break time=..>`/`strength` → `[pause]`,
  `<emphasis level=..>` → `[emphasis]` (level → dominance strength),
  `<sub alias=..>` substitutes the spoken form, `<mark>`/`<p>`/`<s>` →
  `[phrase]`, and `<say-as>` routes its text through `qa.normalize_transcript`.
  `<phoneme ph=..>` pronunciation override is deferred to the i18n framework
  (#8); unknown elements degrade to their text content and malformed XML raises
  a clear `ValueError`. Each construct is byte-identical to the equivalent
  tagged transcript through the whole pipeline, and a construct-free
  `<speak>hello world</speak>` is byte-identical to plain `naive --text`.
  Deterministic and stdlib-only (no numpy), fully opt-in.
- **`concat` / `sequence`: splice finished tracks along a timeline** (closes
  [#51](https://github.com/OpenFaceFX/OpenFaceFX/issues/51)): `transforms.concat(
  tracks, *, gaps=None, crossfade=0.0)` and a `sequence` command that assemble
  already-solved tracks end-to-end — the sequential complement to the #48 `trim`
  (trim cuts a clip out; concat joins clips end-to-end). It offsets every keyframe
  **and** event/variant time of segment *k* by its cumulative start, sets
  `duration = Σ durations + Σ gaps`, and **unions channels** across segments: a
  channel absent from a segment reads as rest (`0`) across its span — a `0` key at
  each of that segment's boundaries stops the previous segment's last value
  bleeding over the seam. `--gap SECONDS` inserts silence and shifts everything
  after it; an optional `--crossfade S` linearly blends the shared channels over
  `±S` seconds at each abutting seam (RDP-thinning only that window). A
  single-track `concat([a])` is **byte-identical** to `a`, and `concat` is the
  seam inverse of `trim` (trim at the seam reproduces `a` and the time-shifted
  `b`). By default (`crossfade=0`) the splice is a pure relabel/offset with no
  re-thin. numpy + stdlib, deterministic across Python 3.9/3.13, additive.
- **glTF 2.0 morph-target animation exporter (`.gltf` / `.glb`)** (closes
  [#49](https://github.com/OpenFaceFX/OpenFaceFX/issues/49)): a new
  `openfacefx.export_gltf` module (`write_gltf`, `build_gltf`) and `.gltf`/`.glb`
  output on all four generate commands and `convert` — the first **vendor-neutral**
  3D asset (every other 3D exporter is engine-specific). glTF 2.0 is the ISO/IEC
  12113 interchange standard imported by Blender / Three.js / Babylon / Godot /
  Unity / Unreal and the base of VRM, and its animation natively drives
  **morph-target weights**, exactly OpenFaceFX's `[0,1]` channel model.
  - A stub `mesh` declares N morph targets named after the track's weight channels
    via `mesh.extras.targetNames`, a `node` references them, and one LINEAR
    `animation` drives the `weights` path; accessors are packed with numpy as
    little-endian FLOAT (componentType 5126) — a strictly-increasing per-frame
    `input` grid (with `min`/`max`) and a frame-major `output`, densified from the
    sparse channels with `np.interp`. `.gltf` embeds the buffer as a base64
    `data:` URI; `.glb` is the binary container (12-byte header + space-padded
    JSON chunk + zero-padded BIN chunk) via stdlib `struct`.
  - Only `[0,1]` weight channels become morphs; the signed head/eye **pose**
    channels are excluded by default, with an opt-in `--gltf-head-node` encoding
    `headPitch/Yaw/Roll` as a separate node `rotation` (Euler→quaternion) sampler.
  - The Khronos glTF Validator is the documented external gate (it can't run in
    this environment); the **in-repo proof is a full accessor round-trip** (pure
    `json`/`base64`/`struct`) reconstructing every weight channel within `1e-6`
    with all accessor `min`/`max`/`count`/`byteLength`, chunk alignment and
    `componentType` asserted, for both `.gltf` and `.glb`. Deterministic bytes on
    Python 3.9/3.13, numpy + stdlib only, additive.
- **`diff` command: A/B track drift report with a tolerance-gated exit code**
  (closes [#50](https://github.com/OpenFaceFX/OpenFaceFX/issues/50)): a new
  `openfacefx.trackdiff` module (`diff_tracks`, `render_diff`) and a read-only
  `diff A.track.json B.track.json [--tolerance T] [--json]` command — the
  golden-file / snapshot gate that finally leverages the determinism guarantee.
  It compares *semantically* (a raw `cmp` is too brittle given 4-dp time
  quantisation and RDP key placement): duration delta, `fps` mismatch, per-channel
  added/removed, and for shared channels the **max-abs / RMS / mean-abs** value
  delta on a shared dense grid (the same `np.interp` resampling `edits.sample`
  uses) plus time-coverage and first/last-key drift, and event add/remove/changed.
  It **exits `0` when every delta ≤ `--tolerance`** (default `0.0` → exact match)
  and nonzero otherwise, emitting a deterministic, sorted `{channel, metric,
  value}` problem list so CI diffs stay stable; `--json` prints the full
  schema-stable report, human mode a worst-first table. The delta magnitudes are
  symmetric. Distinct from `validate` (single-file contract) and `diff-edits`
  (writes a sidecar): `diff` takes two tracks and **never writes**. Pure numpy +
  stdlib, deterministic across Python 3.9/3.13, additive.

## [0.14.0] — 2026-07-11

### Added
- **Layered multi-track export** (closes
  [#39](https://github.com/OpenFaceFX/OpenFaceFX/issues/39)): a new
  `openfacefx.layers` module (`Layer`, `build_layers`, `flatten_layers`,
  `layers_to_dict`/`layers_from_dict`) and an `export-layers` command that emit a
  track's **speech / emotion / gesture** contributions as distinct named
  sub-tracks — each a normal channel list plus a per-layer blend-weight curve and
  integer priority — so an engine can re-blend or toggle facial layers at runtime
  (Unreal additive tracks, SALSA priority blending) instead of a single flattened
  set. `build_layers` decomposes a merged track by channel classification
  (gesture / emotion / the speech base); because every channel lands in exactly
  one layer, summing them at weight 1 reproduces the flat merged track exactly (a
  faithful, lossless decomposition — pinned by a round-trip test). Prosody stays
  the track's event layer (it drives notifies, not curves). numpy + stdlib,
  deterministic across Python 3.9/3.13.
  - `io_export.to_dict(track, ..., layers=None)` / `write_json(..., layers=None)`
    append an optional top-level `layers` block, and `from_dict`/`read_json`
    restore it to `track.layers` (names, weights and priorities survive the
    round-trip). **The default path is byte-identical**: with no layers the block
    is omitted, so an ordinary track serialises exactly as before — verified by
    the full existing test suite staying green and an explicit byte-for-byte check.
    Empty/absent layers are omitted, never emitted as dead channels.
- **Energy-ranked channel-budget reduction** (closes
  [#37](https://github.com/OpenFaceFX/OpenFaceFX/issues/37)): a new
  `openfacefx.budget` module (`channel_energy`, `rank_channels`, `keep_channels`,
  `keep_top_weight`, `budget_channels`, `budget_metadata`) that ranks a solved
  track's channels by **total energy** — the summed absolute key-to-key value
  delta (total variation) — and keeps the top N, dropping the low-energy secondary
  micro-channels entirely (as `reduce_to_track` skips never-firing channels;
  nothing is zeroed with dead keys). The ranking is deterministic, ties broken by
  channel name; in a speech clip the jaw + primary lip visemes are highest-energy
  so they survive naturally (no protect-set). The cap applies to the `[0,1]`
  **morph** channels only — the signed head/eye **pose** channels (`headPitch`/
  `headYaw`/`headRoll`/`eyePitch`/`eyeYaw`, the set `inspect`/`validate` already
  classifies) pass through unchanged and are not counted toward N, since they drive
  bones not morph targets and their degree-scale deltas aren't comparable to
  `[0,1]` weights. Two modes, both emitting the per-channel energy ranking as
  sidecar metadata:
  - a **standalone hard cap** `transform --max-channels N` (fixed morph-target
    platforms), composable with retime/mirror/trim, writing a `<out>.budget.json`
    (`format: openfacefx.budget`) sidecar;
  - a **per-LOD budget** `lod --max-channels N1,N2,..` (one per tier, higher LODs
    fewer), nested by the *source* ranking so channel sets don't pop between
    levels, folded into the `*_lod.json` metadata.

  A cap of N never yields more than N morph channels; absent the flag the track is
  returned unchanged (byte-identical). numpy-free stdlib arithmetic, deterministic
  across Python 3.9/3.13, additive.
- **`lod` command: offline LOD (level-of-detail) variant export** (closes
  [#36](https://github.com/OpenFaceFX/OpenFaceFX/issues/36)): a new
  `openfacefx.lod` module (`generate_lods`, `make_lod`, `lod_metadata`,
  `switching_table`, `LOD_DEFAULT_RDP`/`LOD_DEFAULT_FPS`) and a `lod` command that
  derive **K detail variants from one solved track**, finest first — a pure re-run
  of the `curves._rdp` / `edits.sample` machinery already shipped, at a tiered
  tolerance table (no ML, no engine, no camera). numpy + stdlib, deterministic
  across Python 3.9/3.13, purely additive.
  - **RDP tier** re-thins each channel at a rising epsilon (default
    `--rdp 0.002,0.01,0.04`); it only ever *selects* a subset of the source
    keyframes, never inventing one, so LOD0 at the source epsilon is
    **byte-identical** to the input. **fps tier** step/linear-resamples each
    channel onto a coarser grid (default `--fps 60,30,15`, capped at the source
    rate) before thinning, so the kept keys land only on the coarse grid. Higher
    tiers carry a monotonically non-increasing keyframe count.
  - Writes `PREFIX_lod0.json …` (or `--format csv`) plus a `PREFIX_lod.json`
    metadata sidecar (`format: openfacefx.lod`) that round-trips through JSON and
    names every variant's epsilon + fps + counts, with an **advisory**
    screen-coverage → LOD-index switching table (the engine owns the switch).
    `FaceTrack.variants` (the issue-#6 event-take layer) is **not** overloaded for
    LOD — variants are separate files, and each carries the event/take layer
    through unchanged.

### Fixed
- **`lod`: fps-resample tiers no longer drop all-negative signed pose channels**
  (a follow-up to [#36](https://github.com/OpenFaceFX/OpenFaceFX/issues/36), found
  while wiring the [#37](https://github.com/OpenFaceFX/OpenFaceFX/issues/37)
  budget): the per-channel liveness gate in `lod._resample_thin` used
  `np.any(vals > 1e-3)` — a `[0, 1]` test — which misread a signed pose channel
  that stays fully negative (e.g. `headRoll` / `eyeYaw` going into `[-x, 0]`) as
  "never fires" and dropped it from the coarser fps LOD tiers. It now tests
  magnitude (`np.abs(vals) > 1e-3`): identical for `[0, 1]` weight channels
  (values ≥ 0), and it correctly keeps signed pose channels at every tier.

## [0.13.0] — 2026-07-11

### Added
- **Track transforms: `transform` command (retime / mirror / trim)** (closes
  [#48](https://github.com/OpenFaceFX/OpenFaceFX/issues/48)): a new
  `openfacefx.transforms` module (`retime`, `retime_to_duration`, `mirror`,
  `trim`, plus the `MIRROR_PAIRS` / `MIRROR_NEGATE` tables) and a `transform`
  command for the post-production edits `postprocess.time_shift` can't do (it only
  slides, never stretches). Deterministic array arithmetic, numpy + stdlib,
  additive, identical on Python 3.9/3.13; composes with `convert` and the
  importers.
  - **retime / stretch** scales every keyframe *and* event time by `--retime
    FACTOR`, to a `--duration`, or to a `--wav` length, about an optional
    `--anchor`; channel *values* are unchanged and the track `duration` follows.
    2× exactly doubles every key time, event time, and the duration; retime-to-WAV
    matches `wav_duration` within one frame. A uniform scale introduces no
    redundant keys, so every key is preserved (only an exact time collision under
    heavy compression is de-duplicated) rather than RDP-resampled.
  - **mirror** swaps `*Left`/`*Right` channel pairs (an extensible plain-data
    table, ARKit blendshapes + the gesture-layer `blink_L`/`blink_R`) and negates
    the signed lateral pose channels (`headYaw`/`headRoll`/`eyeYaw`); centered
    channels (visemes, `jawOpen`, `headPitch`) pass through untouched. It is a pure
    relabel + sign flip (no time change, no re-thin, channel order preserved), so
    **`mirror ∘ mirror` is byte-identical** to the original — verified by a
    `to_dict` and a CLI `cmp` test.
  - **trim / slice** keeps `[t0, t1]`, rebases to `0`, and drops/reclamps events to
    the window; an empty or out-of-range window yields an empty track, not a crash.
- **`inspect` and `validate` commands: read-only track stats + a CI format
  linter** (closes [#47](https://github.com/OpenFaceFX/OpenFaceFX/issues/47)): a
  new `openfacefx.inspect` module (`inspect_track`, `validate_asset`/
  `validate_file`, `detect_kind`) and two read-only CLI commands.
  - **`inspect FILE [--json]`** — duration, fps, channel/keyframe counts,
    per-channel key count / min / max / start / end / time-coverage, event &
    variant counts, and the weight/pose/gesture channel split. Reuses
    `qa.summarize` / `cue_flags`; `--json` is **schema-stable** (every documented
    key always present, lists empty rather than absent) and deterministic.
  - **`validate FILE [--strict] [--json]`** — a lint gate that auto-detects a
    `.track.json`, an `*.edits.json` sidecar, or a standalone events file, checks
    the format contract (monotonic in-bounds key times; weight channels in
    `[0,1]`; signed head/eye **pose** angle channels flagged only when *wildly*
    out of range; `viseme_set`/`target_set` consistency; event/variant blocks via
    `events.validate_events` plus a known-`EVENT_TYPES` check), and **exits
    nonzero** with a deterministic, sorted, machine-readable problem list
    (`{severity, code, where, detail}`) so CI diffs stay clean. `--strict`
    promotes warnings (empty channels, zero-length track) to errors. It exits `0`
    on every track the generators and importers produce and nonzero on a
    corrupted one.
  - Read-only (never writes), additive, stdlib only, deterministic across Python
    3.9/3.13.
- **`convert` command: re-export or retarget an existing track without the
  solver** (closes [#46](https://github.com/OpenFaceFX/OpenFaceFX/issues/46)):
  `convert IN.track.json -o OUT.ext` loads an existing track and emits any
  exporter format (Unity `.anim`, Godot `.tres`, Live2D `.motion3.json`,
  Rhubarb/Moho/Papagayo cues, CSV, native JSON) — decoupling **generation** from
  **delivery** and completing the round trip with the new importers
  (`from-cues`/`from-csv` → `convert`). It routes the loaded track through the
  *exact same* `--edits` → `_write` dispatch the four generate commands share, so
  the output is **byte-identical to generating that track by construction**, with
  the same passthrough transforms — `--retarget`, `--adjust`, `--retarget-shapes`,
  `--edits` — and format flags (`--anim-naming`, `--godot-node`/`--godot-naming`,
  `--live2d-params`, `--cue-format`, `--fps`). Pure re-serialisation plus the
  existing `retarget`/`apply_adjust`/`apply_edits` transforms — no solver, no
  audio, no RNG; deterministic and additive (no existing command changes). The
  native round-trip `convert track.json -o out.track.json` is byte-identical, and
  `.lip` stays guarded exactly as in the generate path (a viseme track carries no
  phonemes to fabricate). Note: the track JSON stores keyframe *times* at 4 dp, so
  an exporter rendering finer time precision (Unity `.anim` at 6 dp) reflects that
  quantisation unless the track's frame times are 4-dp-representable — byte-identical
  for CSV/cues/JSON at any rate, and for every exporter at e.g. fps 100.
- **Import ARKit / Live Link Face blendshape-weight CSV** (closes
  [#45](https://github.com/OpenFaceFX/OpenFaceFX/issues/45)): a new
  `openfacefx.importers_csv` module (`read_csv`, re-exported from `importers`) and
  a `from-csv` command, extending the cue importers to the other big source of
  existing face animation — per-frame blendshape weights. Two layouts are
  auto-detected from the header: the OpenFaceFX **long** `time,channel,value`
  format (the exact inverse of `io_export.write_csv` — a byte-clean round-trip),
  and a **wide** per-frame CSV (Apple ARKit's 52 coefficients as recorded by Epic's
  Live Link Face, or any DCC/capture export) where a `Timecode` column (SMPTE
  `HH:MM:SS:FF`, sized by `--fps`/`--timecode-col`) or the row index converts to
  seconds and each column is RDP-thinned via `reduce_to_track` into sparse keys.
  - Channel names land in **rig space** verbatim (`jawOpen`, `mouthSmileLeft`, …)
    and values are clamped to `[0, 1]`; an out-of-range column (e.g. a
    non-blendshape head-rotation angle) is clamped and reported. It deliberately
    does **not** recover visemes (the forward viseme→ARKit map is many-to-one) —
    it brings the raw channels in to condition (`--smooth`/`--lag`), layer and
    re-export. Malformed rows/values raise a clear `ValueError`.
  - The imported track validates through `io_export.from_dict`/`to_dict` and
    re-exports through Unity/Godot/Live2D unchanged. numpy + stdlib (`csv`) only,
    deterministic across Python 3.9/3.13 (fixed RDP thinner, stable 4-dp
    rounding), and **purely additive** — no existing command's output changes.

## [0.12.0] — 2026-07-11

### Added
- **Import mouth-cue files back into a FaceTrack** (closes
  [#44](https://github.com/OpenFaceFX/OpenFaceFX/issues/44)): a new
  `openfacefx.importers` module and a `from-cues` command that read the stepped
  mouth-cue files OpenFaceFX already writes — Rhubarb TSV/XML/JSON, Moho/OpenToonz
  `.dat`, Papagayo-NG `.pgo` — back into an ordinary stepped `FaceTrack` (one
  `[0,1]` viseme channel, `sil` in the gaps, via `reduce_to_track`), giving a
  studio's Rhubarb/Papagayo library a migration path *into* the tool to
  coarticulate, retarget, layer gestures/events, condition and re-export. The
  format is auto-detected by extension + first line; `--coarticulate` re-solves
  the hard steps through the dominance blend. stdlib + numpy only (`xml.etree`,
  `json`, `re`), deterministic across Python 3.9/3.13, and **purely additive** —
  no existing command's output changes.
  - **Verified inverse of the cue exporters**: each parser inverts the exact
    grammar `export_cues` emits, and the shape→viseme tables
    (`RHUBARB_TO_VISEME` / `PRESTON_BLAIR_TO_VISEME`) are *derived from the forward
    retarget presets* so they cannot drift. `write → from-cues → write`
    round-trips **byte-identically** for the seconds-based Rhubarb formats and to
    a byte-exact **idempotent fixed point** — preserving the exact
    (shape, frame-boundary) cue sequence — for the frame-based `.dat` / `.pgo`
    (`.dat` defaults to 24 fps via `--fps`, `.pgo` carries its own; frame decode
    inverts `_frame_at` / `_to_frames`).
  - **Extended/unknown shapes** route through the documented
    `RHUBARB_EXTENDED_FALLBACK` (`G→A`, `H→C`, `X→A`) and are *reported*, or raise
    a clear error — never silently dropped. The imported track validates through
    `io_export.from_dict`/`to_dict` and re-exports through Unity/Godot/Live2D/cues
    unchanged. Library API: `import_cues`, `detect_format`, `build_cue_track`,
    exported from the package root.
- **Additive emotion/expression layer baked over speech** (closes
  [#38](https://github.com/OpenFaceFX/OpenFaceFX/issues/38)): a new
  `openfacefx.emotion` module and a standalone `emotion` command that bake an
  authored emotion envelope onto a speech-solved track as a true additive delta
  relative to a neutral/reference pose (`channel_value - reference_value`),
  mirroring how SALSA's EmoteR and Unreal additive animation layer expression over
  lip-sync. The delta is resampled onto a grid shared with the base curve (reusing
  `edits.sample`), scaled by a global `--intensity` dial, clamped per channel and
  re-thinned with the same RDP thinner — the result is an ordinary `FaceTrack`
  that exports through every exporter. numpy + stdlib, deterministic across Python
  3.9/3.13, and **byte-identical** with `--intensity 0`, a neutral envelope or a
  zero delta (verified by `cmp` of a baked track against its input).
  - **Two authoring modes** in one envelope schema (`openfacefx.emotion`,
    version 1, validated like the `edits` sidecar): direct emotion-channel
    keyframes (`smile`/`frown`/`brow_raise` …), or a compact `valence`/`arousal`
    keyframe track (both in `[-1, 1]`) mapped through the **fixed, hand-authored**
    `VA_TABLE` by bilinear interpolation — a table lookup only, **no ML** — with
    the circumplex centre `valence = arousal = 0` mapping to an all-zero pose.
    `va_to_pose(valence, arousal)` exposes the documented lookup.
  - **Composes with the existing exporters**: the curve exporters carry the
    emotion channels; the mouth-only cue and `.lip` writers ignore the recognised
    expression channels (`smile`/`cheek_raise`/`brow_raise`/`brow_lower`/`frown`)
    exactly as they ignore gesture channels, and `--retarget` passes them through.
  - **Library API** `bake_emotion(track, envelope, *, intensity, clamps, eps)`,
    `EmotionEnvelope`, `load_envelope`/`save_envelope`, `va_to_pose`, `VA_TABLE`
    and `VA_EMOTION_CHANNELS`, exported from the package root.
- **Transcript text tags for curves, events, emphasis, and audio chunking**
  (closes [#7](https://github.com/OpenFaceFX/OpenFaceFX/issues/7)): a new
  `openfacefx.texttags` module and a `--tags` flag on `naive` that let a writer
  direct animation from the script, modelled on the FaceFX
  [text-tagging](https://facefx.github.io/documentation/doc/text-tagging) syntax
  (and, for `[emphasis]`/`[pause]`, on SSML `<emphasis>`/`<break>`). Tags are
  extracted *before* G2P and mapped onto the timeline the aligner produced, so the
  words are still lip-synced. stdlib-only (`re`/`shlex`), deterministic across
  Python 3.9/3.13, and **byte-identical on a tagless transcript** — a plain
  transcript parses to itself with an empty tag list and takes the ordinary naive
  path, verified against a captured baseline.
  - **Curve tags** `[Name type=quad|lt|ct|tt v1=.. v2=.. v3=.. v4=.. easein=..
    easeout=.. timeshift=.. duration=..]word(s)[/Name]` add an animation channel
    `Name` keyframed over the tagged word span with the documented leading /
    centered / trailing-triplet or quadruplet shape and 0.2 s ease defaults, e.g.
    `[brow_raise type=ct v1=1]really[/brow_raise]` peaks a `brow_raise` channel
    over *really*.
  - **Event tags** `[event:NAME k=v ...]` / `[gesture:NAME ...]` — or the FaceFX
    curly form `{"group|anim" start=.. payload=".." ...}` — inject an
    `openfacefx.events.Event` at the **start of the following word** (the end of
    the last word when trailing), with `start`/`duration`/`blendin`/`blendout`
    mapped to the event fields and every other parameter preserved in the payload.
  - **Emphasis** `[emphasis]word[/emphasis]` (optional `strength=`) raises the
    local vowel peak by re-weighting the coarticulation solve over the tagged
    span, reusing the issue-#18 dominance-amplitude mechanism via a new
    `CoartParams.emphasis_windows` (empty = byte-identical no-op).
  - **Chunk / pause** `<T>` angle-bracket time markers split the naive utterance
    into phrases pinned to those audio times with `sil` filling the gaps —
    rejecting a non-monotonic, overlapping, negative, or past-duration timeline
    with a `ValueError`; `[pause:SECONDS]` (or `[break time=..]`) inserts silence
    at a word boundary and `[phrase]` drops a `marker/phrase` event.
  - **Preprocessor hook**: `generate_naive(..., preprocess=callable, parse_tags=
    True)` runs a `callable(text) -> text` before parsing, so a registered
    auto-tagger can insert tags programmatically — injecting a tag this way is
    byte-identical to hand-writing it. `--tags` auto-enables when a clear tag is
    present; it is rejected with `-o .lip` (which cannot carry curves/events) and
    with `--anchors`.
- **Batch NDJSON progress stream, run ledger, and cue-flag QA** (closes
  [#35](https://github.com/OpenFaceFX/OpenFaceFX/issues/35) and, with it, the
  batch half of [#23](https://github.com/OpenFaceFX/OpenFaceFX/issues/23)): three
  opt-in additions to `batch`, each byte-identical when its flag is absent — the
  printed table and `batch_summary.json` are unchanged, verified against a
  captured baseline. Deterministic across Python 3.9/3.13.
  - `--machine-readable` streams an NDJSON event log to **stderr** (one JSON
    object per line, `event` in `start|progress|warning|failure|done`) so a
    supervising process can follow a large run live instead of scraping the
    table. `start` carries the input/todo/skipped counts; one `progress` per
    processed file in processing order (`status`, `mode`, `channels`,
    `keyframes`, `oov`, `cue_warnings`, `min_confidence`, `warnings`); dedicated
    `warning`/`failure` events to filter on; `done` with the outcome counts and
    exit code. Fixed, documented field set and `ensure_ascii`, so the stream is
    pure ASCII and safe to line-parse. `--quiet` drops the human table from
    stdout while still writing the summary JSON and any NDJSON/ledger.
  - `--ledger FILE` appends one NDJSON record per run (never rewrites the file,
    so it survives `--modified-only`): the args snapshot, every discovered
    input's relative path + size + mtime + transcript kind, and the outcome
    counts — a reproducibility/audit trail for dialogue-scale runs. The `run` id
    is a SHA-256 over that identity, so it is **deterministic and wall-clock-
    free**: two identical re-runs hash the same, an edited input or arg hashes
    differently (`mtime` is file metadata for audit, never `Date.now`). Schema
    `format: openfacefx.batch.ledger`, `version: 1`.
  - `--cue-warnings` folds `qa.cue_flags()` (made public in #23) into the batch
    summary: each row gains an integer `cue_warnings` count of phoneme cues
    shorter than `--min-cue` (default 0.03 s) or longer than `--max-cue` (default
    0.5 s), and the worst-first ranking gains it as a final tiebreaker — a strict
    superset of the old failures/confidence/OOV key, so the order (and bytes) are
    unchanged without the flag. It is opt-in precisely because adding the count
    would otherwise change `batch_summary.json`.

Backlog: [issues](https://github.com/OpenFaceFX/OpenFaceFX/issues) — engine-side
distribution (#28 Pyodide, #29 Unity, #30 Unreal, #31 conda-forge), one large
unspecced feature (#8 i18n), the JALI rules follow-up (#19), the manual PyPI
publisher step (#24), and in-game confirmation of the `.lip` writer +
FaceFXWrapper shim (#12, #33).

## [0.11.0] — 2026-07-11

Direction: delivery styles, stressed articulation, and machine-readable output
for pipelines.

### Added
- **Delivery-style presets and a lexical-stress amplitude pass** (closes
  [#18](https://github.com/OpenFaceFX/OpenFaceFX/issues/18)): the two remaining
  layers on top of the JALI-style intensity/gain dials shipped in 0.10.0, both
  opt-in and byte-identical when neutral/off, on `naive`/`mfa`/`from-timing`.
  - `--style NAME` loads a named `CoartParams` dial preset capturing a delivery
    style — `neutral` (the defaults), `whisper`, `mumble`, `tense`, `exaggerated`,
    `broad` — as *data*, not code (`STYLE_PRESETS: {name: {field: override}}` and
    `style_params(name) -> CoartParams` in `coarticulation.py`, both public API).
    A low master intensity with tucked-in class gains mumbles/softens; a high one
    with opened jaw/lip gains broadens/hyper-articulates. `style_params("neutral")`
    **is** a default `CoartParams()`, so `--style neutral` is byte-identical to no
    `--style` (verified against the reference command). Explicit `--intensity`/
    `--gain` compose on top of a preset and win per field; enforced lip closures
    still seal afterwards, so a whispered bilabial fully closes.
  - `--stress-emphasis [AMOUNT]` (bare flag = 0.5; range 0..2; **0 = off,
    byte-identical**) reads the same ARPABET stress digit the gesture layer keys
    on and biases each vowel segment's *dominance* before the blend — primary
    (`1`) up by `AMOUNT`, secondary (`2`) by half, an explicitly unstressed vowel
    (`0`) down by `0.35·AMOUNT` — so stressed syllables articulate more strongly
    (their viseme peaks higher and holds) while unstressed ones yield to their
    neighbours (`CoartParams.stress_emphasis` for library callers). Scaling the
    dominance *amplitude* rather than the normalized weights is what keeps the
    per-frame partition intact: the factor multiplies segment `i` in both the
    blend numerator and its shared normalizing denominator, so every frame still
    sums to ~1 (proof in `_stress_gains`); the closure pass runs afterwards and
    still seals to the 0.9 floor. It is a graceful, byte-identical no-op on inputs
    without stress digits (vendor/IPA timing paths). The `energy` command is
    excluded from both flags for the same reason it lacks `--intensity`/`--gain`:
    it synthesises an amplitude partition with no articulator-class channels or
    phoneme stress for the dials/pass to act on. Deterministic across Python
    3.9/3.13.
- **Machine-readable QA output and an embeddable summary API**
  ([#23](https://github.com/OpenFaceFX/OpenFaceFX/issues/23), partial): the four
  generate commands (`naive`/`mfa`/`from-timing`/`energy`) take `--json` — a
  single-line JSON QA summary (`format: openfacefx.qa`) to stdout **instead of**
  the human `wrote …` line — and `--report FILE` to also write that JSON
  (indented) to a file while keeping the console line. The summary is
  deterministic and self-describing: `output`, `fps`, `duration`, channel/
  keyframe/gesture/event counts, `oov_words`, `cue_warnings`, normalization
  `substitutions`, and `warnings[]`. Warnings that were previously only printed
  (unknown vendor symbols, edit conflicts) now **also** surface in the summary,
  joined by two it derives itself — OOV words that fell back to the G2P rules,
  and an empty/silent track. The written track file is **byte-identical** with or
  without the flag, and without either flag the console output is unchanged. The
  same signals are public API for embedding without the CLI: `summarize(track) ->
  dict`, `normalize_transcript(text) -> (text, subs)`, and `cue_flags(segments,
  min_dur, max_dur)`, alongside the existing `G2P().oov_words` and `generate_*`.
- **Transcript normalization ahead of G2P** (part of #23): `naive` folds the
  Unicode punctuation a TTS engine or a pasted script carries — ellipsis `…`,
  en/em dashes, curly quotes `‘’“”`, non-breaking space — to ASCII before
  phonemisation and reports each fold in `substitutions`. The curly apostrophe
  (`it’s` typed with U+2019, otherwise split into two tokens) is the case that
  actually changes phonemes. On by default; `--no-normalize` opts out; ASCII
  transcripts are byte-identical either way.
- **Cue-duration flags** (part of #23): phoneme cues shorter than `--min-cue`
  (default 0.03 s) or longer than `--max-cue` (default 0.5 s) appear in the QA
  summary's `cue_warnings` with clip, time and duration — the analogue of the
  over-short/over-long cues a lip-sync editor flags for manual attention.

Still open on #23 (now tracked as #35): the `batch` `--machine-readable` NDJSON
event stream, the append-only run ledger, and wiring the new `cue_flags` into
the batch summary.

## [0.10.0] — 2026-07-11

Finishing touches: curve smoothing with lag/lead, per-shape retarget trim, and
an event-layer fix.

### Fixed
- **`--retarget` no longer drops the event/take layer** (#34): `retarget()`
  rebuilt the track without carrying `events`/`variants`, so retargeting a track
  that had events silently lost them. They now survive the remap.

### Added
- **Per-target gain/offset trim and CLI shape filtering when retargeting**
  ([#22](https://github.com/OpenFaceFX/OpenFaceFX/issues/22)): retargeting onto a
  rig can now trim individual shapes without forking a weighted preset table.
  `retarget(track, mapping, adjust={target: (gain, offset)})` — and the standalone
  `apply_adjust(track, adjust)` — remap each named target to `clamp(gain*value +
  offset, 0, 1)` **after** the weighted sum, leaving the preset **byte-identical**,
  so an integrator can soften `jawOpen` or hold `mouthSmile` slightly on with a
  data argument rather than a table edit. `retarget(..., adjust=A)` is exactly
  `apply_adjust(retarget(...), A)`; a target the rig never receives but given a
  positive `offset` is materialised as a constant channel over the clip (and added
  to `target_set`) — the way "always slightly on" lifts a shape the mapping never
  drives, `gain` being moot there (the absent base is 0). On the CLI, `--adjust
  adjust.json` (a JSON `{target: {"gain": G, "offset": O}}` object — an ARKit rig's
  ~52 shapes overflow the flag line) applies the trim to the curve outputs
  (`json`/`csv`/`anim`), and `--retarget-shapes shapes.json` (a JSON array of the
  rig's real shapes) exposes the existing `available=`/`fallbacks=` reroute path —
  e.g. a tongue-less Audio2Face rig sends `tongueOut` to a small `jawOpen`. Both
  compose (shapes filtered, then trimmed) and are validated at the CLI boundary.
  Default/empty ⇒ **byte-identical** output; deterministic across Python 3.9–3.13.
  Closes #22 (the `vrm0`/`readyplayerme` presets and the optional-shape fallback
  mechanism shipped earlier).
- **Curve smoothing and lag/lead post-processing**
  ([#10](https://github.com/OpenFaceFX/OpenFaceFX/issues/10)): a new
  `openfacefx.postprocess` module (numpy + stdlib only) adds FaceFX-style
  post-solve curve conditioning between the dominance solver and RDP keyframe
  reduction, where before there was none. `smooth_matrix(matrix, sigma, fps)`
  runs a normalized temporal **Gaussian** (sigma in seconds) over the dense
  viseme curves to soften jitter; because the kernel is a unit-sum partition of
  unity applied uniformly with edge-hold padding, each frame's channels still
  sum to ~1 (the coarticulation partition-energy invariant is preserved) and
  values stay in `[0, 1]`. Crucially, lip **closures are re-enforced after
  smoothing** — mirroring FaceFX's phoneme-influence toggle — so a bilabial or
  labiodental seal (`/p/ /b/ /m/ /f/ /v/`) the filter would otherwise round off
  stays sharp (`PP`/`FF` peak ≥ the closure floor). `time_shift(track, seconds)`
  slides keyframe times to make the visemes **lag** (`>0`) or **lead** (`<0`) the
  audio, clamped into the clip's `[0, duration]` envelope so a per-channel shift
  never disturbs other channels or the track length. Both are threaded through
  `CoartParams` (`smooth`, `lag`) and exposed as `--smooth SECONDS` /
  `--lag MS` on `naive`/`mfa`/`from-timing`/`energy`. Default off ⇒
  **byte-identical** output; deterministic across Python 3.9–3.13.

## [0.9.0] — 2026-07-11

Production workflow: follow the voice's pitch, and keep the animator's edits.

### Added
- **Edit preservation: hand-tweaks that survive regeneration**
  ([#9](https://github.com/OpenFaceFX/OpenFaceFX/issues/9)): a new
  `openfacefx.edits` module (numpy + stdlib only) lets an animator's manual curve
  edits outlive a pipeline re-run, mirroring FaceFX's two-layer ownership model —
  analysis *owns* the generated curves, the user keeps edits in a separate
  **sidecar** `*.edits.json` (never inline, so the `.track` stays clean interchange
  and its `version` stays `1`). `diff_edits(base, edited)` captures what changed
  into the sidecar; `apply_edits(regenerated, edits)` overlays it back onto a fresh
  `FaceTrack`. Two per-channel modes mirror FaceFX's *offset curve* and *owned-off*
  editing: **`offset`** stores the delta from the baseline and re-applies as
  `clamp(analysis + offset)` — being *relative*, it survives an `--intensity` /
  `--gain` / coarticulation change (the primary case); **`replace`** stores absolute
  values (full ownership), and an optional `span` locks just a **time region** while
  the fresh curve shows through elsewhere. Conflicts are conservative: an edit whose
  channel the regeneration dropped is **preserved and reported** (`keep-edit`
  default — a hand-edit is never silently lost) or discarded (`take-generated`); a
  locked region always wins inside its span. New CLI: `diff-edits BASE EDITED -o
  OUT [--mode offset|replace] [--span T0 T1] [--source WAV]` to capture, and
  `--edits FILE [--on-conflict …]` on `naive`/`mfa`/`from-timing`/`energy` to apply
  during generation. `openfacefx.io_export` gains the inverse loaders `from_dict` /
  `read_json` (to read a hand-edited `.track.json` back for diffing) and an optional
  `source_id` on `to_dict` / `write_json`. The merge is **deterministic** (numpy
  `interp`/`clip` + the existing RDP thinner, no RNG — identical on Python 3.9/3.13,
  with a hard-coded golden merge pinned in the tests) and **fully backward-compatible**:
  without `--edits`, output is byte-identical to previous releases. **Out of scope**
  (stays numpy + stdlib, deterministic, non-ML): no Bezier/tangent handles, no
  phoneme-anchored *rebase* of edit times onto a rewritten transcript (offsets on the
  same audio are the supported robustness path; a channel a transcript change drops is
  flagged, not auto-migrated), no 3-way / multi-user merge beyond keep / take.
- **Prosody events from a numpy pitch tracker**
  ([#4](https://github.com/OpenFaceFX/OpenFaceFX/issues/4)): a new
  `openfacefx.prosody` module (numpy + stdlib `wave` only) follows the *pitch* of
  the voice, not just its loudness, and derives typed prosodic events from it.
  `pitch_track()` is a short-time **autocorrelation** F0 tracker in the standard
  non-ML shape — windowed autocorrelation debiased by the window's own
  autocorrelation (Boersma/Praat), a two-part voicing gate (energy floor **and**
  clarity ≥ 0.45), an octave-cost period pick that suppresses the down-octave
  error, parabolic-interpolation peak refinement, and a reflect-padded median /
  octave-repair post-filter that rejects boundary spikes. `prosody_features()`
  bundles F0, voicing, clarity, the reused `energy._frame_rms` loudness follower
  and a syllable-rate proxy into a `ProsodyTrack`; `prosody_events()` turns those
  into `emphasis` (coincident pitch **and** loudness prominence), `phrase_boundary`
  (a silent pause, or the utterance end, tagged `clause`/`sentence`) and
  `question_rise` (a rising terminal F0 — the yes/no-question cue) records. The
  events are ordinary [`Event`s](https://github.com/OpenFaceFX/OpenFaceFX/issues/6),
  so `--prosody` on `naive`/`mfa`/`energy` (each reading the audio from `--wav`;
  `mfa` gains an optional `--wav`) attaches them onto the track and they ride the
  same JSON / Unity `.anim` / Unreal-notify path and **compose** with `--events`
  and `--gestures`. **Deterministic** — no RNG, and byte-identical events across
  runs, platforms and Python 3.9/3.13 (the FFT pipeline reproduces bit-for-bit,
  verified on numpy 2.0/2.5). **Honest limitations**: this is DSP heuristics, not
  an ML prosody model — autocorrelation F0 makes octave errors and mislabels
  voicing on whispered/breathy/creaky voice and low SNR, prominence/question
  detection are rule-based cue layers (not ToBI), and it will misbehave on
  music/noise/overlapping speakers; the animation only needs *relative* pitch
  movement, so this is acceptable. 16-bit PCM WAV in (convert first with
  `ffmpeg -c:a pcm_s16le`), same as `energy.py`. **Fully backward-compatible**:
  without `--prosody`, output is byte-identical to previous releases.

## [0.8.0] — 2026-07-11

The rip-and-replace release: a clean-room drop-in for the FaceFXWrapper the
whole AI-NPC modding ecosystem depends on, plus an engine event/take layer.

### Added
- **`FaceFXWrapper.exe`-compatible drop-in shim**
  ([#33](https://github.com/OpenFaceFX/OpenFaceFX/issues/33)): a CLI-compatible
  stand-in for Nukem9's `FaceFXWrapper.exe` — the tool xVASynth's `lip_fuz`
  plugin and the Mantella / Pantella AI-NPC pipelines shell out to for Skyrim
  `.lip` generation. A new `openfacefx.facefxwrapper` module reproduces the
  binary's exact contract (verified from `FFXW32/FFXW32.cpp`): **dispatch on
  argument count**, the input WAV at positional index 3 in both the 7-arg
  (resample) and 6-arg (pre-resampled) forms, the output `.lip` at index 5 / 4,
  dialogue text last, and `Type` ∈ `Skyrim`/`Fallout4` (case-insensitive). It
  generates a real (experimental, #12) Skyrim `.lip` through the pipeline instead
  of driving Creation Kit code, and matches the behaviours consumers actually
  depend on — **success is a byte-valid `.lip` at the output path** (exit code and
  stdout are ignored by consumers; we still return 0/1 and print the wrapper's
  `Unknown generator type` / `LIP generation failed` / usage messages), the
  resampled-WAV path is **never written** (consumers `os.remove` it only if
  present), and `Fallout4` fails honestly with no file so the caller uses its
  placeholder. Exposed as a native `facefxwrapper` console script and as
  `python -m openfacefx facefxwrapper …` (intercepted **before** argparse so raw
  positional args — flag-like tokens, paths with spaces — pass through verbatim);
  a `FonixData.cdf` **stub** requirement, the per-consumer drop-in recipe, and the
  PyInstaller `FaceFXWrapper.exe` build (runs under the consumers' Wine prefix) are
  documented in `docs/facefxwrapper.md`. **Honest limitations**: naive
  duration-based timing (not Fonix acoustic alignment), the `.lip` payload is
  experimental / unverified in-game (#12), and Fallout 4 is unsupported. The
  `.fuz`/xWMA repacking path stays out of scope (needs an external xWMA encoder).
- **Event & take layer** ([#6](https://github.com/OpenFaceFX/OpenFaceFX/issues/6)):
  named, timed, typed events with a freeform JSON payload — the game-engine
  notify layer, mirroring FaceFX events / Unreal `AnimNotify` / Unity
  `AnimationEvent`. A new numpy-free `openfacefx.events` module adds `Event`,
  weighted `Variants`/`VariantGroup`/`Alternative` "takes", and `resolve()`;
  `FaceTrack` gains optional `events`/`variants` fields (both default empty).
  **Takes are deterministic**: an alternative is chosen by hashing a line id with
  SHA-256 (FIPS 180-4, no RNG, no wall-clock), so the same `line_id` resolves to
  the same take on every machine and Python version, and each group hashes
  independently. `--events` auto-authors an `emphasis`/`phrase` layer from the
  speech (reusing the `--gestures` accent detection, but independent of the
  gesture channels); `--events-file` + `--line-id` attach and bake authored
  takes. Events serialize into the track JSON as **optional** top-level
  `events`/`variants` keys (emitted only when present, so `version` stays `1`)
  and fill the Unity `.anim` `m_Events` array (each event an `AnimationEvent`
  Unity SendMessage-invokes on the Animator, name+payload packed into the single
  `stringParameter`, ranged events expanding to a `_Begin`/`_End` pair,
  `DontRequireReceiver` so a missing handler never errors). A new
  `export_unreal_notifies` writes an `AnimNotify` sidecar JSON an editor-Python
  snippet stamps onto a `UAnimSequence`. **Fully backward-compatible**: a track
  with no events is byte-identical to previous releases, in JSON and `.anim`.

## [0.7.0] — 2026-07-11

Life beyond the mouth: procedural non-verbal gestures, a previewer that plays
the audio, and a full documentation site.

### Added
- **HTML preview: audio playback, waveform & phoneme lane**
  ([#11](https://github.com/OpenFaceFX/OpenFaceFX/issues/11)):
  `tools/build_preview.py` gains `--wav` (embeds the voice line as a base64
  `data:` URI; the transport plays in sync with the playhead and draws a
  client-side min/max waveform via the Web Audio API) and `--segments` (a
  clickable phoneme/word lane above the transport — click a segment to seek, or
  to hear just that slice when audio is embedded; optional per-segment
  `confidence` tints blocks red→green so low-confidence alignments stand out for
  QA). `--segments` accepts a segments JSON (`[{"phoneme", "start", "end"}, ...]`,
  optionally wrapped with a `words` lane) or a Praat `.TextGrid`; the
  `naive`/`mfa` commands dump that JSON with a new `--emit-segments PATH` flag.
  The page stays a single self-contained file with no network requests
  (openable from `file://`), and output is byte-identical to previous releases
  when neither flag is given.
- **Documentation site** ([#27](https://github.com/OpenFaceFX/OpenFaceFX/issues/27)):
  a MkDocs Material + mkdocstrings site published to
  [openfacefx.github.io/OpenFaceFX/docs/](https://openfacefx.github.io/OpenFaceFX/docs/),
  alongside the existing landing page and live demo. Full-text search, a
  light/dark toggle on the amber-on-dark brand, the compatibility / retargeting /
  TTS-timing guides surfaced straight from `docs/` (no content forks), an API
  reference generated from docstrings for every public module, and this changelog.
  Built and deployed by the Pages workflow; the pinned build tools live in a new
  `docs` extra (`pip install -e ".[docs]"`) and never touch the numpy-only runtime.
- **Procedural non-verbal gestures**
  ([#5](https://github.com/OpenFaceFX/OpenFaceFX/issues/5)): a new
  `openfacefx.gestures` module layers eye blinks, eyebrow raises, head nods and
  idle sway, and gaze saccades onto a finished lip-sync track. Timing is coupled
  to the speech the way FaceFX/JALI/SmartBody do it — Poisson blinks snap to
  pauses and stressed syllables (biphasic fast-close/slow-open lid), eyebrow
  flashes and head nods fire on `energy.py` peaks / primary-stress vowels, and a
  quasi-periodic sum-of-sines keeps the head from freezing. `GestureParams` is
  the artistic-dial dataclass (blink rate, amplitudes, degree bounds …);
  `generate_gestures()` / `gestures_from_wav()` / `add_gestures_to_track()` are
  the API. Everything is deterministic (seeded from `GestureParams.seed`, each
  component on its own sub-stream so toggling one never shifts another; identical
  keyframes on Python 3.9/3.12) and **opt-in**: `generate_from_alignment`,
  `generate_naive` and `generate_from_energy` gain a `gestures=` argument, and
  `naive`/`mfa`/`energy` gain `--gestures` (+ `--gesture-seed`, `--blink-rate`,
  `--no-brows`); with none given, output is byte-identical to prior releases.
  Blink/brow channels are `[0,1]` weights; head/eye are signed pose channels in
  degrees (or `[-1,1]`). They pass through `retarget` untouched and are ignored
  by the mouth-only cue/`.lip` exporters (they are never mistaken for a viseme).

## [0.6.1] — 2026-07-11

### Fixed
- **`import openfacefx` was broken in 0.6.0** (#12): 0.6.0 shipped a half-applied
  rename — `export_lip.py` defined `SKYRIM_SLOT_MAP` while `__init__.py`,
  `lip_bytes()` and `lip_calibrate()` still referenced the old
  `SKYRIM_SLOT_ORDER` / `_ALWAYS_ON_SLOT` names, so importing the package raised
  `ImportError` and the `.lip` writer never ran. The rename is now complete: the
  package imports, the writer produces byte-valid output, and the full suite is
  green again.

### Changed
- **`lip-calibrate` now probes every grid slot** (#12), not just the 16 slots the
  provisional `SKYRIM_SLOT_MAP` guesses are speech targets: it writes
  `slot_00.lip` .. `slot_32.lip` (one per Skyrim payload slot) plus a `README.txt`
  manifest of the procedure and current hypothesis. Probing all slots is what
  actually lets an in-game tester discover the slot→morph mapping — the real
  morph may live on a slot the guess doesn't use. Each file sweeps a single slot
  0→1→0 with a dup-safe resting anchor; all decode byte-exact. See the
  calibration procedure in `docs/COMPATIBILITY.md`.

## [0.6.0] — 2026-07-11

The white whale: a clean-room Bethesda `.lip` writer. The format that every
existing tool delegates to Bethesda's own embedded Creation Kit code has been
reverse-engineered from four public samples and is now writable — verified
byte-identical against the real vanilla asset, flagged experimental until
someone confirms it in-game (calibration kit included).

### Added
- **Experimental Bethesda `.lip` writer for Skyrim** (#12): a clean-room writer
  for the FaceFX facial-animation payload inside a Skyrim `.lip` file —
  `openfacefx.export_lip.write_lip(segments, duration_s, path)`, and `-o out.lip`
  on the `naive`/`mfa` commands (with `--lip-game skyrim`, the default). The
  byte format was reverse-engineered from four real samples (three mod-author
  placeholders plus one vanilla Creation-Kit asset); the research codec
  (`tools/lip_codec_research.py`, now with an `encode_curves` inverse)
  re-serializes all four **byte-identically**, and every track the writer emits
  round-trips through an independent decoder exactly (`tests/test_export_lip.py`,
  Oracle B/C). It drives the existing coarticulation solver through an
  ARPAbet→Skyrim-16 `Mapping` and samples the weight envelopes on Skyrim's 30 fps
  frame grid. **Flagged EXPERIMENTAL: not yet verified in-game.** Two facts stay
  unverifiable without the engine and are documented, prominent assumptions —
  the slot→morph assignment (the payload numbers curve slots, it does not name
  them) and the header `u22` field (copied from the vanilla asset). Fallout 4 is
  unsupported (its 43-target vocabulary is undocumented): `game='fallout4'`
  raises `NotImplementedError` rather than emit a bogus file. In-game testers
  wanted — please report on [#12](https://github.com/OpenFaceFX/OpenFaceFX/issues/12).
- **More retarget presets and optional-shape fallbacks** (#22): two new
  `--retarget` presets — `vrm0` (VRM 0.x / VRoid Studio uppercase `A I U E O`
  BlendShapePresets, the 0.x-named sibling of `vrm`) and `readyplayerme` (the
  Oculus 15 as Ready Player Me's `viseme_*` morph targets) — plus documentation
  that MetaHuman, Meta Avatars/Quest, NVIDIA Audio2Face and Reallusion CC3 are
  already covered by the existing `arkit`/native-Oculus/`cc4` presets rather than
  duplicated. `retarget()` gains `available=` (the shapes a rig actually has) and
  `fallbacks=`: a mapped target the rig lacks reroutes through a per-preset
  `PRESET_FALLBACKS` table — chained, cycle-guarded, weights multiplying —
  instead of dropping silently (e.g. a tongue-less Audio2Face rig sends
  `tongueOut → jawOpen × 0.2`). Rhubarb's documented basic-set collapse
  (`G→A H→C X→A`) now lives once in `PRESET_FALLBACKS`, and the cue exporters'
  `--rhubarb-shapes` derives its view from it (behaviour unchanged). Provenance
  and the fallback tables: `docs/retargeting.md`.

## [0.5.0] — 2026-07-10

The no-transcript-no-problem release: audio-energy fallback, artistic
dials, a Rhubarb-style README with a CI-rendered quickstart GIF and a
viseme gallery, Live2D and Godot exporters, and out-of-the-box IPA support
for TTS timing.

### Added
- **Built-in IPA phoneme preset for `from-timing`** (#32): `pho`, `piper` and
  `cartesia` now **auto-select** a bundled IPA→Oculus-15 mapping when no
  `--mapping` is given, so Piper/Cartesia (IPA) and espeak-ng MBROLA `.pho`
  (SAMPA) produce rich mouth shapes out of the box instead of degrading to
  silence — an explicit `--mapping` still wins. The preset (`openfacefx.ipa.
  IPA_MAPPING`) is data: it keys the targets by the inventory those engines
  emit, grounded in the espeak-ng phoneme guide, the Montreal Forced Aligner
  US-English phone set (which Cartesia's sonic models use), and the Wikipedia
  English IPA key; the symbol→viseme groupings are an articulatory synthesis,
  like `visemes.PHONEME_TO_VISEME`. A small `_normalize_ipa` folds the
  diacritics real dumps carry onto the base symbol **on lookup** (no row
  duplication): stress `ˈ ˌ`, length `ː ˑ`, the affricate **tie bar** (`t͡ʃ` =
  `tʃ`), MFA's `ʰ ʲ ʷ`, and any other combining mark (`t̪`→`t`, `n̩`→`n`). Both
  diphthong spellings (`aɪ…` and MFA's `aj…`), r-coloured `ɜ ɝ ɚ` and
  non-colliding SAMPA fallbacks (`@ { 3`) are covered; unknown symbols warn once
  per distinct symbol and relax to silence — never a crash. IPA vowels also feed
  the coarticulation dominance model (`is_ipa_vowel`), so vendor vowels get the
  broad vowel bump. The ARPABET default path is byte-for-byte unchanged
  (`ipa.py`; docs/timing.md).
- **Live2D Cubism `motion3.json` exporter** (#20): `-o mouth.motion3.json`
  bakes lip-sync as Cubism parameter curves (Version 3, linear segments). Two
  targeting modes. **Default (zero config)** collapses the whole viseme track to
  a single `ParamMouthOpenY` curve — the summed weight of every *non-silence*
  viseme, clamped to `0..1` (an openness/loudness proxy that equals `1 - sil` on
  normalised output), which is the one mouth-open parameter almost every Cubism
  model exposes; the target Id is overridable (`--live2d-param`). **Per-parameter**
  mode (`--live2d-params map.json`, a `viseme -> ParamId` object) emits one curve
  per distinct Id for rigs with per-vowel parameters — note `ParamA/I/U/E/O` are
  a VTuber *convention*, not a standard, so they must be supplied, not assumed.
  `--live2d-model3 model.json` auto-reads the mouth parameter from a model's
  `Groups: LipSync` entry. Both modes are a `retarget` under the hood (summed,
  clamped contributions on the union of key times). The `Meta` counts
  (`CurveCount`/`TotalSegmentCount`/`TotalPointCount`) are **derived from the
  emitted `Curves` by the same stride a Cubism loader walks**, never guessed — a
  `Meta` that disagrees with the segment data is the format's #1 gotcha (loaders
  read past the array). Pure-stdlib JSON, LF newlines (`export_live2d.py`).
- **Godot 4 `AnimationPlayer` resource exporter** (#21): `-o lipsync.tres`
  writes a `[gd_resource type="Animation" format=3]` resource with one **value
  track** per active viseme, keyed with the existing RDP-reduced keyframes and
  linear interpolation (`interp = 1`). Tracks drive blend shapes by node path
  (`NodePath("Head:blend_shapes/viseme_aa")`); Godot weights are `0..1` so
  channel values pass straight through (no ×100 as for Unity). Byte-formatting
  follows Godot's own text saver (verified against engine source): packed
  `times`/`transitions` arrays print trimmed (`0`, `1`, `0.1`) while the generic
  `values` array forces decimals (`0.0`, `1.0`) to stay float-typed, the keys
  dict is ordered `times, transitions, values, update`, and default-valued
  resource properties (`loop_mode 0`, `step 1/30`, empty `resource_name`, and
  `length` when 1.0) are omitted — so output matches what the editor re-saves.
  Shape naming
  reuses the Unity exporter's presets (`--godot-naming oculus|vrchat`) or a
  custom `viseme -> shape` map (`--godot-names map.json`); the node name is
  configurable (`--godot-node`, default `Head`). By default it also writes a
  constant-0 track for every viseme the line never fires, clearing weight a
  previous animation left on that shape. Value tracks (not the importer-only
  `blend_shape` track type) keep the resource hand-writable; text serialisation,
  LF newlines (`export_godot.py`). Runtime nodes/signals stay engine-side and out
  of scope. The optional audio-playback and 2D sprite-frame tracks from #21 are
  deferred (not in the byte-verified format spec this pass targeted).
- **README hero onboarding: quickstart GIF, viseme gallery, literal output**
  (#26). The README now opens with the live-demo link, an animated quickstart
  GIF, and `pip install openfacefx` above the fold; the long-form pipeline
  description moves below. The GIF is **recorded as code**, not hand-captured:
  [`docs/quickstart.tape`](https://github.com/OpenFaceFX/OpenFaceFX/blob/main/docs/quickstart.tape) drives [VHS](https://github.com/charmbracelet/vhs),
  and the Pages workflow (`pages.yml`) re-renders it on every push via
  `charmbracelet/vhs-action` and publishes it to the GitHub Pages site — so the
  GIF can never drift from the real CLI and no bot ever commits a binary to the
  repo (the README points at the Pages URL; `docs/quickstart.gif` is
  git-ignored). A new `tools/render_viseme_gallery.py` renders one small SVG per
  viseme (`docs/visemes/*.svg`, <800 bytes each, presentation-attributes-only so
  GitHub inlines them) by porting the schematic-mouth `drawFace` geometry from
  the previewer and evaluating each viseme at full weight — **no art
  dependency**; a table documents all 15 Oculus/Meta visemes with their
  phonemes. The hero also shows a literal `openfacefx.track` excerpt that matches
  the demo command's real output byte-for-byte. `test_viseme_gallery.py` guards
  the committed SVGs against drift from the generator (like the GIF from its
  tape). Docs/tooling only — no library behavior changed.
- **Articulation-intensity dials** (#18, partial — the JALI-style gain layer):
  `--intensity` (master) and repeatable `--gain class=value` (e.g. `--gain
  tongue=0.6 --gain jaw=1.2`) on `naive`/`mfa`/`from-timing` scale how strongly
  each articulator class opens, so one curve set spans mumble to hyper-
  articulated without retiming — `CoartParams.intensity` and `CoartParams.gains`
  for library callers. The scale is applied *after* normalization: every
  channel's opening is multiplied by `intensity * gains[its class]` (the class
  read from the mapping target, exactly as the coarticulation model reads a
  segment's) and `sil` reabsorbs the freed weight, so a frame still sums to ~1
  and the mouth genuinely opens/closes rather than the curve just being
  rescaled (`open' = Σ scale·open`, then `sil = max(1 − open', 0)` with the
  non-`sil` channels capped to fill the frame — proof of the sum-to-1 invariant
  in `_apply_intensity`). Enforced lip closures run afterwards and still win, so
  a whispered bilabial (`--intensity 0.5`) still seals to the 0.9 floor.
  **Defaults are a byte-identical no-op**: all `1.0` makes the per-channel scale
  a vector of ones and the step returns before touching the matrix (verified on
  the two reference commands and by a `params=None` vs `CoartParams()` equality
  test). Bad dials fail fast at the CLI boundary (unknown class, non-number,
  negative). The `energy` command keeps its own `--intensity`: it never builds
  coarticulated curves (no articulator-class channels — it synthesises an
  aa/E/O/sil partition from an RMS envelope), so the `CoartParams` master does
  not apply and its envelope-gain semantics are left unchanged. Not yet from
  #18: shipped style presets, the lexical-stress amplitude pass, and time-
  varying (keyframed) dials.
- **Audio-energy fallback lip-sync** (#17): `openfacefx energy --wav voice.wav
  -o track.json` drives a mouth from loudness alone — the first path that needs
  *no transcript and no aligner* (`energy.py`, numpy + stdlib `wave` only). This
  is the common non-ML mechanism behind SALSA/Moho/Live2D, and it is **an
  amplitude fallback, not viseme detection**: it cannot tell a /m/ from an /aa/
  and will open the mouth on a cough — docstrings, `--help` and the README all
  say so. `energy_envelope` computes per-frame RMS at the track fps, gates the
  noise floor and normalizes against a high percentile (not the max, so one
  plosive does not flatten the take), then runs an asymmetric envelope follower
  (fast attack, ~7x slower release — mouths snap open and relax shut).
  `generate_from_energy` turns that envelope into an ordinary Oculus-viseme
  `FaceTrack`: jaw-open (`aa`) is the primary channel, with a small, honestly
  *aesthetic* `spread` bled into two secondary shapes (louder leans rounded
  `O`, quieter leans mid `E`) so it does not read as one channel flapping, and
  `sil` takes the rest; each frame partitions unit weight (`sil + aa + E + O ==
  1`). Output is deterministic (no jitter/RNG) and flows through the existing
  keyframe reduction, so `--retarget`, `.anim` and every cue exporter compose
  unchanged. Input is 16-bit PCM WAV, mono or stereo (stereo is downmixed by
  averaging); other sample widths raise a clear `ValueError` with convert-first
  guidance (`ffmpeg -c:a pcm_s16le`). CLI knobs: `--fps` and `--intensity` (gain
  on the opening); library callers also get `window`, `gate`, `smoothing` and
  `spread`.

## [0.4.0] — 2026-07-10

The adapters-and-interchange release: skip the aligner with TTS timing, pin
it with subtitles, export to the 2D animation ecosystem, try it in the
browser. Also ships PyPI release automation (pending the one-time publisher
registration, #24) and the live demo site.

### Added
- **Rhubarb-dialect cue exporters** (#16): flatten a track into a stepped cue
  list (dominant viseme per interval) and serialise the formats the indie 2D
  ecosystem reads, making OpenFaceFX a drop-in Rhubarb replacement for those
  hosts (`export_cues.py`, pure stdlib, LF endings). Writers: Rhubarb `-f tsv`
  (`start<TAB>shape` lines + a terminal `X` row bounding the last cue), `-f xml`
  (`rhubarbResult` tree, soundFile/duration metadata, `mouthCue` start/end
  elements) and `-f json` (hand-formatted `metadata` + `mouthCues` array);
  Moho/OpenToonz `.dat` (`MohoSwitch1`, 1-based truncated frames with same-frame
  dedup and a terminal rest row, `--cue-fps` 24..100, Preston-Blair drawing
  names by default — required by OpenToonz's "Apply Lip Sync Data" — or Rhubarb
  A–H/X letters via `--no-dat-preston-blair`); and Papagayo-NG `.pgo` (single
  voice/phrase/word phoneme tree, TAB-indented). Shape vocabulary is handled
  automatically: an Oculus-15 track is retargeted through the built-in
  `rhubarb`/`preston_blair` presets, a track already in the target shapes passes
  through, anything else errors clearly. Extended shapes the art lacks collapse
  to a basic shape via Rhubarb's documented fallback (`--rhubarb-shapes ABCDEF`;
  G→A, H→C, X→A). CLI: `-o` dispatches on the `.tsv`/`.xml`/`.dat`/`.pgo`
  extension, or `--cue-format tsv|xml|json-cues|dat|pgo` selects explicitly
  (`json-cues` is needed because `.json` stays the native track format); every
  format is reachable from `naive`, `mfa` and `from-timing`. `soundFile`/sound
  path default to the literal `"openfacefx"`, never your local absolute path.
- **Word/segment-anchored alignment** (#15): the naive aligner now accepts
  anchors — `Anchor(text, start, end=None)` spans — and distributes each word's
  phonemes *within* its anchored span instead of across the whole utterance,
  a large accuracy win with zero ML (`anchors.py`, `anchored_segments`). Anchor
  words are matched to the transcript sequentially (case/punctuation-insensitive);
  uncovered words fill the gaps between anchors; wordless gaps over ~0.15 s relax
  to `sil`; with no anchors the output is byte-identical to `naive_segments`.
  Parsers/converters, each pure stdlib with a fixture test: `parse_srt` (SubRip
  cues, multi-line, tag-stripped), `parse_word_anchors` (generic
  `[{text,start,end?}]`), and converters from Azure `WordBoundary` events
  (100-ns ticks), ElevenLabs character alignments (grouped at whitespace,
  `normalized_alignment` preferred), Kokoro tokens (None-`start_ts`/`end_ts`
  tolerant) and Google Cloud TTS timepoints, plus a `google_ssml_with_marks`
  helper (pure text transform, one `<mark/>` per word). CLI: `openfacefx naive
  --anchors FILE --anchors-format srt|words|azure|elevenlabs|kokoro|google`
  (SRT supplies its own transcript when `--text` is omitted). Vendor field names
  verified against Azure/ElevenLabs/Google docs; snake_case aliases and object
  wrappers accepted as in `timing.py`.
- **TTS timing ingest** (#14): `openfacefx from-timing` skips the aligner and
  builds tracks straight from a TTS engine's own timing, through one normalized
  `TimingEvent(unit, symbol, start, end)` schema (`timing.py`). Parsers for
  MBROLA `.pho` (espeak-ng), Piper sample-count alignments, Cartesia
  `phoneme_timestamps`, Azure viseme events (100-ns ticks) and Polly viseme
  `.marks`, each converting its native time units (ms / sample counts / ticks /
  seconds) to seconds and rejecting malformed input with a clear error.
  Phoneme-unit sources feed the existing weighted mapping and coarticulation
  unchanged; viseme-unit sources (Azure, Polly) remap onto the Oculus-15 targets
  via built-in presets (`AZURE_VISEME_TO_TARGET`, `POLLY_VISEME_TO_TARGET`), with
  unknown symbols/IDs downgraded to a QA warning instead of a crash. Missing end
  times are inferred from the next event's start (`resolve_ends`, configurable
  `--final-duration`). `Mapping` gains `allow_custom_symbols` so vendor symbols
  (numeric IDs, case-significant letters, IPA) bypass ARPABET normalization.
  Capture scripts for Azure and the espeak-ng C API in `docs/timing.md`; GPL
  engines (espeak-ng, piper1-gpl) run as external processes only, never vendored.
- **Live demo site** (#25): https://openfacefx.github.io/OpenFaceFX/ — the
  previewer autoplays a track regenerated from the current pipeline on every
  push (`--autoplay` flag on `tools/build_preview.py`, Pages deploy workflow).
- **PyPI release automation** (#24): tag-triggered `release.yml` — version and
  changelog gates, sdist + universal wheel, the test suite run against the
  built wheel, GitHub release with artifacts and notes, OIDC trusted
  publishing (skipped until the pending publisher is registered). PEP 639
  metadata and `[project.urls]`.
- End-to-end test suite (`tests/test_e2e.py`) and a real-world
  `examples/dialogue/` voice tree covering both alignment paths through
  `batch`; PNG logo.

### Fixed
- `preston_blair` retarget preset: the consonant catch-all is now `etc` — the
  exact layer name Moho/OpenToonz match on — instead of `consonants`, which
  silently never switched the mouth (found by byte-exact format verification
  against Rhubarb's DAT exporter and the Papagayo phoneme tables).

## [0.3.1] — 2026-07-10

### Fixed
- Windows: `openfacefx batch` crashed with "path is on mount 'C:', start on
  mount 'D:'" when the output tree was on a different drive than the working
  directory (summary paths are now relative to the output tree). Caught by
  the CI matrix.

## [0.3.0] — 2026-07-10

The three P1 items from the FaceFX feature-gap backlog.

### Added
- **Component-based coarticulation** (#1): per-articulator-class
  (basic/jaw/lips/tongue) lead-in/out timing via `CoartParams`,
  short-silence absorption (0.27 s default), guaranteed ≥0.9 lip closures
  on bilabials/labiodentals, diphthong splitting, onset pre-roll policy.
- **Data-driven weighted mapping** (#2): `Mapping`/`Target` model + JSON
  format (`openfacefx.mapping` v1), per-target articulator class and
  min/max clamps, strict validation, CLI `--mapping`; ships
  `examples/mappings/oculus15.json` and `minimal9.json`. Default path is
  byte-identical to 0.2.0.
- **Batch processing** (#3): `openfacefx batch` over directory trees with
  `--recurse/--modified-only/--jobs`, manifest-based incremental re-runs,
  and a worst-first QA summary (failures, aligner confidence, OOV words).
- `G2P.oov_words()`, optional `PhonemeSegment.confidence`, and
  `FaceTrack.target_set` (exports now report the actual target vocabulary).

### Changed
- Default lip-sync output improves: consonant channels are tighter and lip
  closures complete (PP peak 0.23 → 0.90 on the example track). Tracks are
  not byte-identical to 0.2.0 (that guarantee applied to the 0.2.0 mapping
  change only).

## [0.2.0] — 2026-07-10

Engine-integration release: the wrapper-compatibility work from the FaceFX
ecosystem survey.

### Added
- **Unity `.anim` exporter** (`write_unity_anim`, CLI `-o clip.anim`):
  AnimationClip text YAML driving `blendShape.*` curves on a
  SkinnedMeshRenderer; `oculus` (`viseme_*`, Ready Player Me / Meta rigs) and
  `vrchat` (`vrc.v_*`) naming presets, custom maps via `names=`;
  `--anim-naming` / `--anim-path` CLI options. (#13)
- **Viseme retargeting** (`retarget`, `rename_only`, `PRESETS`; CLI
  `--retarget`): weighted many-to-many remapping onto ARKit-52 (verified
  met4citizen/TalkingHead weights), Rhubarb, Preston-Blair, VRM and CC4
  conventions — provenance in `docs/retargeting.md`.
- **Bethesda tooling** (`openfacefx.bethesda`): verified `.fuz` container
  reader/writer, `.lip` 12-byte header parser, `lip_info` diagnostics,
  Skyrim's 16 speech-target names. A full `.lip` *writer* remains blocked:
  the payload has no public byte-level spec (#12).
- `pipeline.naive_segments()` exposing the phoneme-timing layer.

### Changed
- README compatibility matrix and roadmap updated to reflect shipped
  exporters and the `.lip` payload finding (docs/COMPATIBILITY.md has the
  full analysis).

## [0.1.0] — 2026-07-10

Initial public release.

### Added
- Full lip-sync pipeline: audio + transcript → time-stamped phonemes →
  Oculus/Meta 15-viseme targets → Cohen–Massaro dominance coarticulation →
  Ramer–Douglas–Peucker keyframe reduction → `FaceTrack`.
- Two alignment paths: Montreal Forced Aligner TextGrid parser
  (`load_mfa_textgrid`) and a dependency-free naive aligner with per-phoneme
  duration priors.
- G2P with seed CMU dictionary and rule fallback; `load_cmudict()` for the
  full dictionary.
- CLI: `openfacefx naive` (text + WAV/duration) and `openfacefx mfa`
  (TextGrid), JSON and CSV output.
- Self-contained HTML previewer (`tools/build_preview.py`): schematic
  articulator, per-channel curve plots, scrubbing playhead.
- Versioned JSON interchange format (`openfacefx.track`, version 1).
- Test suite plus CI across Linux/Windows/macOS on Python 3.9/3.12/3.13.
- FaceFX-ecosystem compatibility survey (`docs/COMPATIBILITY.md`).

### Fixed
- Windows: `tools/build_preview.py` now reads the template and track JSON as
  UTF-8 instead of the locale default (cp1252), which failed with
  `UnicodeDecodeError`.

[Unreleased]: https://github.com/OpenFaceFX/OpenFaceFX/compare/v0.11.0...HEAD
[0.11.0]: https://github.com/OpenFaceFX/OpenFaceFX/compare/v0.10.0...v0.11.0
[0.10.0]: https://github.com/OpenFaceFX/OpenFaceFX/compare/v0.9.0...v0.10.0
[0.9.0]: https://github.com/OpenFaceFX/OpenFaceFX/compare/v0.8.0...v0.9.0
[0.8.0]: https://github.com/OpenFaceFX/OpenFaceFX/compare/v0.7.0...v0.8.0
[0.7.0]: https://github.com/OpenFaceFX/OpenFaceFX/compare/v0.6.1...v0.7.0
[0.6.1]: https://github.com/OpenFaceFX/OpenFaceFX/compare/v0.6.0...v0.6.1
[0.6.0]: https://github.com/OpenFaceFX/OpenFaceFX/compare/v0.5.0...v0.6.0
[0.5.0]: https://github.com/OpenFaceFX/OpenFaceFX/compare/v0.4.0...v0.5.0
[0.4.0]: https://github.com/OpenFaceFX/OpenFaceFX/compare/v0.3.1...v0.4.0
[0.3.1]: https://github.com/OpenFaceFX/OpenFaceFX/compare/v0.3.0...v0.3.1
[0.3.0]: https://github.com/OpenFaceFX/OpenFaceFX/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/OpenFaceFX/OpenFaceFX/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/OpenFaceFX/OpenFaceFX/releases/tag/v0.1.0
