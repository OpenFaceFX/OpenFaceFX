# Changelog

All notable changes to the OpenFaceFX Unity runtime package.

## [0.1.0] - 2026-07-24

Initial release.

- `OffxClip` — parsed OpenFaceFX performance (channels of linearly-sampled keyframes, fps, duration).
- `OffxParser` — reads the OpenFaceFX **track JSON** (`openfacefx.track`) and the Apple **ARKit
  Live Link Face** wide CSV. Dependency-free (built-in `MiniJson`); numbers parsed with
  InvariantCulture so comma-decimal locales are safe.
- `OffxFacePlayer` — runtime component that streams weights onto a `SkinnedMeshRenderer`'s
  blendshapes, with case-insensitive / prefix / PascalCase⇄camelCase name resolution, plus
  optional head/eye bone pose from the rotation channels. Play/Pause/Stop/Seek + `ApplyAt(t)`
  for external clocks.
- Editor: `.offxtrack` **ScriptedImporter** and an **Assets ▸ OpenFaceFX ▸ Convert to OffX Clip**
  menu for any track-JSON / ARKit-CSV `TextAsset`.
- **Rocketbox ARKit** sample (a real OpenFaceFX take + wiring notes for Microsoft Rocketbox avatars).
