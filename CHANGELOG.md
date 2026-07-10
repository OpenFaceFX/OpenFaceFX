# Changelog

All notable changes to OpenFaceFX are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses
[Semantic Versioning](https://semver.org/): while on `0.x`, minor versions may
contain breaking changes; the JSON track format is versioned independently via
its `version` field.

## [Unreleased]

### Added
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

Remaining backlog: prosody/gestures/events/text-tags/i18n (#4–#9), preview
upgrades (#10–#11), and the Bethesda `.LIP` writer (#12, blocked on payload
reverse-engineering — research codec in `tools/lip_codec_research.py`).

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

[Unreleased]: https://github.com/OpenFaceFX/OpenFaceFX/compare/v0.3.1...HEAD
[0.3.1]: https://github.com/OpenFaceFX/OpenFaceFX/compare/v0.3.0...v0.3.1
[0.3.0]: https://github.com/OpenFaceFX/OpenFaceFX/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/OpenFaceFX/OpenFaceFX/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/OpenFaceFX/OpenFaceFX/releases/tag/v0.1.0
