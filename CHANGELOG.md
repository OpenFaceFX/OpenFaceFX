# Changelog

All notable changes to OpenFaceFX are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses
[Semantic Versioning](https://semver.org/): while on `0.x`, minor versions may
contain breaking changes; the JSON track format is versioned independently via
its `version` field.

## [Unreleased]

Planned work is tracked in the
[issue backlog](https://github.com/OpenFaceFX/OpenFaceFX/issues): the
FaceFX feature-gap backlog (#1–#11) and the Bethesda `.LIP` writer (#12,
blocked on payload reverse-engineering).

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

[Unreleased]: https://github.com/OpenFaceFX/OpenFaceFX/compare/v0.2.0...HEAD
[0.2.0]: https://github.com/OpenFaceFX/OpenFaceFX/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/OpenFaceFX/OpenFaceFX/releases/tag/v0.1.0
