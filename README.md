<div align="center">

<img src="docs/logo.svg" width="140" alt="OpenFaceFX logo"/>

# OpenFaceFX

**Open-source lip-sync in the spirit of FaceFX: voice recording + transcript → animation curves that drive a character's face.**

[![CI](https://github.com/OpenFaceFX/OpenFaceFX/actions/workflows/ci.yml/badge.svg)](https://github.com/OpenFaceFX/OpenFaceFX/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-f4b942.svg)](LICENSE)
[![Python 3.9+](https://img.shields.io/badge/python-3.9%2B-3776ab.svg?logo=python&logoColor=white)](pyproject.toml)
[![Runtime deps](https://img.shields.io/badge/runtime%20deps-numpy%20only-6e7681.svg)](pyproject.toml)
[![Status](https://img.shields.io/badge/status-0.1.0%20alpha-e06c5b.svg)](#scope--honesty)

<img src="docs/preview.png" width="850" alt="OpenFaceFX previewer: schematic mouth animating next to the viseme channel curves of a generated track"/>

*The built-in previewer playing a track generated from `examples/voice.wav` — schematic articulator on the left, the exported viseme curves with a scrubbing playhead on the right.*

</div>

## What it is

FaceFX-style tools are really four subsystems chained together. Only the first
(acoustic alignment) needs a heavy model — and excellent open-source aligners
already exist. So OpenFaceFX **wraps** the aligner instead of reinventing it,
and fully owns the other three stages:

<img src="docs/pipeline.svg" width="100%" alt="Pipeline: audio + text → alignment → visemes → coarticulation → keyframes → JSON/CSV"/>

1. **Alignment** — time-stamped phonemes from Montreal Forced Aligner (parser
   included), or a dependency-free naive aligner for instant prototyping.
2. **Phoneme → viseme** — the widely-adopted Oculus/Meta 15-viseme convention.
3. **Coarticulation** — Cohen–Massaro dominance blending, so mouth shapes flow
   into each other instead of switching.
4. **Keyframe reduction** — Ramer–Douglas–Peucker thinning into sparse,
   engine-friendly curves.

Every seam is a tiny data contract (`PhonemeSegment` in, `FaceTrack` out), so
any stage can be swapped without touching the rest.

## Install

```bash
pip install -e .          # numpy is the only runtime dependency
```

## Quick start

No models, no downloads — approximate lip-sync from text + a WAV's duration:

```bash
python -m openfacefx naive \
  --text "hello world this is a test" \
  --wav examples/voice.wav \
  -o track.json
```

Accurate lip-sync from a Montreal Forced Aligner result:

```bash
# 1. run MFA (separately) to get voice.TextGrid, then:
python -m openfacefx mfa --textgrid voice.TextGrid -o track.json
```

Library use:

```python
from openfacefx import generate_naive, load_mfa_textgrid, generate_from_alignment, write_json

track = generate_naive("the quick brown fox", duration=1.8)      # quick path
# or, accurate:
segs  = load_mfa_textgrid("voice.TextGrid")
track = generate_from_alignment(segs)
write_json(track, "track.json")
```

## Preview what you generated

`examples/preview.html` is a self-contained page (no server needed) that
animates a schematic mouth from a track and plots every viseme channel with a
scrubbing playhead. Rebuild it for your own track:

```bash
python tools/build_preview.py track.json preview.html
```

## Output format

Deliberately trivial JSON (CSV also available) — sparse `[time, value]` keys
per viseme channel, weights in `[0, 1]`:

```jsonc
{
  "format": "openfacefx.track",
  "version": 1,
  "fps": 60.0,
  "duration": 1.6,
  "viseme_set": ["sil", "PP", "FF", "TH", "DD", "kk", "CH", "SS", "nn", "RR", "aa", "E", "I", "O", "U"],
  "channels": [
    { "name": "PP", "keys": [[0.0, 0.0], [0.9167, 0.0094], [1.0167, 0.0737]] }
  ]
}
```

Channel names are blendshape names your rig exposes; linear interpolation
between keys is the intended playback. A reference reader is ~15 lines — see
[docs/COMPATIBILITY.md](docs/COMPATIBILITY.md).

## Plugging in a real aligner (stage 1)

The naive aligner spaces phonemes by duration priors — fine for prototyping,
not for shipping. For production accuracy, produce a list of
`PhonemeSegment(phoneme, start, end)` from any of these and pass it to
`generate_from_alignment`:

- **Montreal Forced Aligner** — best accuracy; parser included (`load_mfa_textgrid`).
- **Gentle** — Kaldi-based, JSON output; write a ~15-line adapter.
- **wav2vec2 / Whisper** — phoneme or word timings from a neural model; word-level
  needs no transcript.

Better G2P: drop in the full CMU Pronouncing Dictionary with
`G2P().load_cmudict("cmudict.dict")` (the built-in dictionary is a tiny seed).

## The viseme set

15 targets from the Oculus/Meta LipSync convention (`sil, PP, FF, TH, DD, kk,
CH, SS, nn, RR, aa, E, I, O, U`) — a well-documented, IP-free convention most
character rigs already provide blendshapes for. To retarget to a different rig
(Apple ARKit's 52 blendshapes, a Preston-Blair 12-shape set, …), edit
`PHONEME_TO_VISEME` and `VISEMES` in `visemes.py` — nothing else changes.

## FaceFX ecosystem compatibility

We surveyed every public FaceFX wrapper on GitHub. The short version: **all of
them are parallel audio+text generators, not curve consumers** — none accepts
any lip-sync tool's curves as input, so "drop-in" compatibility is impossible
by design, for us and everyone else. What *is* possible is writing the
artifacts their pipelines consume:

| Ecosystem | Route | Status |
|---|---|---|
| Bethesda modding (Nukem9/FaceFXWrapper, xVASynth, Mantella) | OpenFaceFX writes `.LIP` directly — clean-room, no Creation Kit, no `FonixData.cdf` | 🔶 roadmap |
| Unreal (official FaceFX-UE4/UE5 plugins) | Impossible via the plugins (proprietary `.ffxc` compiler); instead drive UE float curves / morph targets from JSON | ✅ JSON today |
| Unity (OVRLipSync, lipstick, uLipSync) | Same 15-viseme set 1:1; AnimationClip / `OVRLipSyncSequence` exporter | 🔶 roadmap |
| Anything else | Trivial JSON/CSV + documented remap | ✅ today |

Full survey with per-tool details: [docs/COMPATIBILITY.md](docs/COMPATIBILITY.md).

## Roadmap

- [ ] Bethesda `.LIP` exporter (replaces FaceFXWrapper for Skyrim/Fallout pipelines)
- [ ] Unity `AnimationClip` / OVRLipSync-sequence exporter
- [ ] Published remap tables: ARKit-52, MetaHuman, Preston-Blair
- [ ] Gentle & Whisper alignment adapters
- [ ] Emotion/expression layering on top of speech tracks

## Scope & honesty

This is a working foundation, not a finished product. It gives you the full
phoneme→viseme→curve→export chain and a preview, with a clean seam where a
real acoustic aligner plugs in. Not yet included: emotion layering, a rig
authoring GUI, audio feature-driven coarticulation (it's timing-driven), and
engine plugins beyond JSON/CSV. All of these fit on top of `FaceTrack` without
changing the solver. It does **not** read or write proprietary FaceFX binary
formats (`.facefx`, `.fxa`, `.fxe`, `.ffxc`).

## Layout

```
src/openfacefx/
  phonemes.py       ARPAbet inventory
  g2p.py            word → phonemes (CMUdict + rule fallback)
  alignment.py      PhonemeSegment, NaiveAligner, MFA TextGrid parser
  visemes.py        viseme set + phoneme→viseme map        ← retarget here
  coarticulation.py dominance-function blending             ← the interesting math
  curves.py         keyframe reduction, FaceTrack
  io_export.py      JSON / CSV writers
  pipeline.py       orchestration
  cli.py            command line
tests/test_core.py  run: pytest
tools/              HTML previewer builder
docs/               logo, images, compatibility survey
```

CI runs the test suite plus CLI and preview-builder smoke tests on every push,
across Linux / Windows / macOS on Python 3.9, 3.12 and 3.13.

## License

MIT — see [LICENSE](LICENSE).

*FaceFX® is a registered trademark of OC3 Entertainment, Inc. OpenFaceFX is an
independent project — not affiliated with, endorsed by, or connected to OC3
Entertainment or Speech Graphics — and contains no code or data from FaceFX
products.*
