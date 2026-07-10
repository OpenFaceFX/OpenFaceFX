# Changelog

All notable changes to OpenFaceFX are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses
[Semantic Versioning](https://semver.org/): while on `0.x`, minor versions may
contain breaking changes; the JSON track format is versioned independently via
its `version` field.

## [Unreleased]

### Added
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

Still open on #23: the `batch` `--machine-readable` NDJSON event stream, the
append-only run ledger, and wiring the new `cue_flags` into the batch summary.

Backlog: [issues](https://github.com/OpenFaceFX/OpenFaceFX/issues) — larger
unspecced features (#8 i18n, #18 style presets/stress, #19 JALI coart rules),
engine-side distribution (#28–#31), the manual PyPI publisher step (#24), and
in-game confirmation of the `.lip` writer + FaceFXWrapper shim (#12, #33).

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

[Unreleased]: https://github.com/OpenFaceFX/OpenFaceFX/compare/v0.10.0...HEAD
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
