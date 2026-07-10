<div align="center">

<img src="docs/logo.svg" width="140" alt="OpenFaceFX logo"/>

# OpenFaceFX

**Open-source lip-sync in the spirit of FaceFX: voice recording + transcript → animation curves that drive a character's face.**

[![CI](https://github.com/OpenFaceFX/OpenFaceFX/actions/workflows/ci.yml/badge.svg)](https://github.com/OpenFaceFX/OpenFaceFX/actions/workflows/ci.yml)
[![Docs](https://img.shields.io/badge/docs-openfacefx.github.io-f4b942.svg)](https://openfacefx.github.io/OpenFaceFX/docs/)
[![License: MIT](https://img.shields.io/badge/license-MIT-f4b942.svg)](LICENSE)
[![Python 3.9+](https://img.shields.io/badge/python-3.9%2B-3776ab.svg?logo=python&logoColor=white)](pyproject.toml)
[![Runtime deps](https://img.shields.io/badge/runtime%20deps-numpy%20only-6e7681.svg)](pyproject.toml)
[![Status](https://img.shields.io/badge/status-alpha-e06c5b.svg)](#scope--honesty)
[![Release](https://img.shields.io/github/v/release/OpenFaceFX/OpenFaceFX?color=f4b942)](https://github.com/OpenFaceFX/OpenFaceFX/releases)

**[▶ Live demo](https://openfacefx.github.io/OpenFaceFX/demo/)** — no install, regenerated from the current pipeline on every push. **[Read the docs →](https://openfacefx.github.io/OpenFaceFX/docs/)**

<a href="https://openfacefx.github.io/OpenFaceFX/demo/"><img src="https://openfacefx.github.io/OpenFaceFX/quickstart.gif" width="850" alt="Quickstart: one naive command turns 'hello world' plus a WAV into a viseme track JSON"/></a>

*The one-command quickstart, rendered from [`docs/quickstart.tape`](docs/quickstart.tape) by [VHS](https://github.com/charmbracelet/vhs) in CI on every push — recorded as code, so it can't drift from the real CLI. [Open the live previewer →](https://openfacefx.github.io/OpenFaceFX/demo/)*

</div>

## Install

```bash
git clone https://github.com/OpenFaceFX/OpenFaceFX && cd OpenFaceFX
pip install -e .              # numpy is the only runtime dependency
```

(`pip install openfacefx` from PyPI is coming — the release automation is in
place pending the registry setup, [#24](https://github.com/OpenFaceFX/OpenFaceFX/issues/24).)

## Quick start

No models, no downloads — approximate lip-sync from text + a WAV's duration:

```bash
python -m openfacefx naive --text "hello world" --wav examples/voice.wav -o track.json
```

```
wrote track.json: 7 channels, 93 keyframes, 1.60s
```

`track.json` is the `openfacefx.track` format: sparse `[time, value]` keyframes
per viseme channel, weights in `[0, 1]`. The real output begins:

```json
{
  "format": "openfacefx.track",
  "version": 1,
  "fps": 60.0,
  "duration": 1.6,
  "viseme_set": [
    "sil",
    "PP",
    "FF",
    "TH",
    "DD",
    "kk",
    "CH",
    "SS",
    "nn",
    "RR",
    "aa",
    "E",
    "I",
    "O",
    "U"
  ],
  "channels": [
    {
      "name": "sil",
      "keys": [
        [
          0.0,
          0.6196
        ],
```

*The first 30 lines of the actual file (7 channels, 93 keyframes in full). A
reference reader is ~15 lines — see [docs/COMPATIBILITY.md](docs/COMPATIBILITY.md).*

## The 15 visemes

15 targets from the Oculus/Meta LipSync convention — a well-documented, IP-free
set most character rigs already expose blendshapes for. Each mouth shape below
is drawn by the same schematic articulator the [live previewer](https://openfacefx.github.io/OpenFaceFX/demo/)
animates, rendered at full weight (regenerate with `python tools/render_viseme_gallery.py`):

| Viseme | Shape | Phonemes | Mouth |
|:------:|:-----:|:----------|:------|
| **sil** | <img src="docs/visemes/sil.svg" width="72" alt="sil mouth shape"> | — | neutral / mouth at rest |
| **PP** | <img src="docs/visemes/PP.svg" width="72" alt="PP mouth shape"> | `B`, `M`, `P` | lips pressed shut |
| **FF** | <img src="docs/visemes/FF.svg" width="72" alt="FF mouth shape"> | `F`, `V` | lower lip to upper teeth |
| **TH** | <img src="docs/visemes/TH.svg" width="72" alt="TH mouth shape"> | `DH`, `TH` | tongue between the teeth |
| **DD** | <img src="docs/visemes/DD.svg" width="72" alt="DD mouth shape"> | `D`, `L`, `T` | tongue to the alveolar ridge |
| **kk** | <img src="docs/visemes/kk.svg" width="72" alt="kk mouth shape"> | `G`, `HH`, `K` | back of tongue raised |
| **CH** | <img src="docs/visemes/CH.svg" width="72" alt="CH mouth shape"> | `CH`, `JH`, `SH`, `ZH` | rounded, protruded |
| **SS** | <img src="docs/visemes/SS.svg" width="72" alt="SS mouth shape"> | `S`, `Z` | narrow, teeth close |
| **nn** | <img src="docs/visemes/nn.svg" width="72" alt="nn mouth shape"> | `N`, `NG` | nasal, tongue up |
| **RR** | <img src="docs/visemes/RR.svg" width="72" alt="RR mouth shape"> | `ER`, `R` | retroflex / lightly rounded |
| **aa** | <img src="docs/visemes/aa.svg" width="72" alt="aa mouth shape"> | `AA`, `AE`, `AH`, `AY` | open jaw |
| **E** | <img src="docs/visemes/E.svg" width="72" alt="E mouth shape"> | `EH`, `EY`, `IH` | mid-front spread |
| **I** | <img src="docs/visemes/I.svg" width="72" alt="I mouth shape"> | `IY`, `Y` | wide spread |
| **O** | <img src="docs/visemes/O.svg" width="72" alt="O mouth shape"> | `AO`, `AW`, `OW`, `OY` | rounded and open |
| **U** | <img src="docs/visemes/U.svg" width="72" alt="U mouth shape"> | `UH`, `UW`, `W` | tight lip rounding |

To retarget to a different rig (Apple ARKit's 52 blendshapes, a Preston-Blair
12-shape set, …), edit `PHONEME_TO_VISEME` and `VISEMES` in `visemes.py` —
nothing else changes.

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

## More ways to generate

Accurate lip-sync from a Montreal Forced Aligner result:

```bash
# 1. run MFA (separately) to get voice.TextGrid, then:
python -m openfacefx mfa --textgrid voice.TextGrid -o track.json
```

Straight from a TTS engine's own timing — skip the aligner (espeak/MBROLA
`.pho`, Piper, or Cartesia phonemes; Azure or Polly visemes; details and
capture scripts in [docs/timing.md](docs/timing.md)):

```bash
python -m openfacefx from-timing --file visemes.json --format azure -o track.json
```

Or pin the naive aligner at known word/segment boundaries — subtitle cue times or
TTS word timestamps (SRT, Azure/ElevenLabs/Kokoro/Google) — for much better sync
with no models (SRT supplies its own transcript; the rest take `--text`):

```bash
python -m openfacefx naive --anchors cues.srt --anchors-format srt --wav voice.wav -o track.json
```

No transcript at all? Drive the mouth straight from audio loudness — an
amplitude fallback in the spirit of SALSA/Moho/Live2D (**energy, not viseme
detection**; good for barks, crowds, or a quick pass when all you have is a
WAV):

```bash
python -m openfacefx energy --wav examples/voice.wav -o track.json
```

Straight to a Unity AnimationClip, or remapped onto another rig:

```bash
python -m openfacefx naive --text "..." --wav voice.wav -o clip.anim   # viseme_* curves
python -m openfacefx naive --text "..." --wav voice.wav -o clip.anim --anim-naming vrchat
python -m openfacefx mfa --textgrid voice.TextGrid -o track.json --retarget arkit
```

Or a stepped cue list for the indie 2D ecosystem — Rhubarb TSV/XML/JSON,
Moho/OpenToonz `.dat` (Preston-Blair drawing names), Papagayo `.pgo` — flattened
to the dominant mouth shape per interval (extension picks the format; `.json`
stays the native track, so ask for the Rhubarb JSON explicitly):

```bash
python -m openfacefx naive --text "..." --wav voice.wav -o cues.tsv          # Rhubarb TSV
python -m openfacefx mfa --textgrid voice.TextGrid -o mouth.dat              # Moho/OpenToonz
python -m openfacefx mfa --textgrid voice.TextGrid -o cues.json --cue-format json-cues
```

Or bake into a VTuber/game engine's own animation asset — a Live2D Cubism
`motion3.json` (a single mouth-open parameter curve by default, or per-vowel
`ParamA/I/U/E/O` via `--live2d-params`, or read the target from a model's
`model3.json` LipSync group) and a Godot 4 `AnimationPlayer` resource (`.tres`,
one blendshape value track per viseme, `--godot-node`/`--godot-naming`):

```bash
python -m openfacefx naive --text "..." --wav voice.wav -o mouth.motion3.json  # Live2D Cubism
python -m openfacefx mfa --textgrid voice.TextGrid -o lipsync.tres            # Godot 4
```

Whole dialogue trees at once, with an OOV/confidence QA report and
incremental re-runs:

```bash
python -m openfacefx batch --dir voice/ --out tracks/ --recurse --modified-only --jobs 8
```

Weighted many-to-many phoneme mapping and coarticulation timing are
data/parameters, not code — see `examples/mappings/` and `CoartParams`.
JALI-style artistic dials tune articulation strength without retiming: `--intensity`
(master, `<1` mumbles, `>1` hyper-articulates) and repeatable `--gain class=value`
(e.g. `--gain tongue=0.6 --gain jaw=1.2`); all `1.0` is a byte-identical no-op.

Library use:

```python
from openfacefx import generate_naive, load_mfa_textgrid, generate_from_alignment, write_json

track = generate_naive("the quick brown fox", duration=1.8)      # quick path
# or, accurate:
segs  = load_mfa_textgrid("voice.TextGrid")
track = generate_from_alignment(segs)
write_json(track, "track.json")
```

## Non-verbal gestures (blinks, brows, head & eyes)

A mouth that moves but a face that's otherwise frozen reads as a mask. Pass
`--gestures` to layer the *other* channels a believable performance needs — eye
blinks, eyebrow raises, head nods and idle sway, and gaze saccades — on top of
any generated track (issue [#5](https://github.com/OpenFaceFX/OpenFaceFX/issues/5)):

```bash
python -m openfacefx naive --text "..." --wav voice.wav --gestures -o track.json
python -m openfacefx mfa   --textgrid voice.TextGrid       --gestures -o track.json
python -m openfacefx energy --wav voice.wav                --gestures -o track.json
```

The timing is coupled to the speech the way FaceFX/JALI/SmartBody do it, not
sprinkled at random: blinks follow a Poisson process (~15/min) but **snap onto
pauses and stressed syllables**, with a biphasic fast-close/slow-open lid;
eyebrow flashes and head nods fire on loudness peaks (the same `energy.py`
envelope the audio fallback uses) and primary-stress vowels; a slow sum-of-sines
keeps the head alive between nods. Everything is **deterministic** — seeded from
`--gesture-seed` (default 0), identical keyframes every run and across Python
versions — and fully **opt-in**: without `--gestures`, output is byte-identical
to before. Tune it with `--blink-rate` (blinks/min) and `--no-brows`, or the
`GestureParams` dataclass in the library.

Blink and brow channels are `[0,1]` blendshape weights (like the visemes);
`headPitch/Yaw/Roll` and `eyePitch/Yaw` are **signed pose channels in degrees**
(positive `headPitch` = down, positive `eyeYaw` = the subject's left), or a
signed `[-1,1]` range with `GestureParams(head_eye_in_degrees=False)`. They are
not visemes: `--retarget` passes them through unchanged, and the mouth-only cue
(`.tsv`/`.dat`/…) and Bethesda `.lip` exporters ignore them.

```python
from openfacefx import generate_from_alignment, GestureParams, load_mfa_textgrid

segs  = load_mfa_textgrid("voice.TextGrid")
track = generate_from_alignment(segs, gestures=GestureParams(seed=0), wav="voice.wav")
# or add gestures to an existing track: add_gestures_to_track(track, dur, times, env, segs)
```

## Events & takes (game-engine notifies)

A track says *how the face moves*; an **event** says *what happened and when* — a
named, timed, typed record with a freeform JSON payload that a game runtime turns
into gameplay (play a sound, shake the camera, fire a Blueprint node). It is the
same payload-only model as FaceFX events, Unreal's `AnimNotify` and Unity's
`AnimationEvent` (issue [#6](https://github.com/OpenFaceFX/OpenFaceFX/issues/6)).
The layer is **additive**: without it, every track is byte-identical to before.

Pass `--events` to auto-author a typed layer from the speech itself — `emphasis`
events on stressed syllables / loudness peaks and `phrase` boundary markers at
pauses (reusing the same accent detection as `--gestures`, but independent of it):

```bash
python -m openfacefx naive --text "..." --wav voice.wav --events -o track.json
python -m openfacefx mfa   --textgrid voice.TextGrid       --events -o track.anim
```

**Takes** are deterministic variation. Author weighted alternative event-sets per
group; a **line id** picks one, forever, by hashing the id with SHA-256 (no RNG,
no wall-clock — the same id resolves to the same take on every machine and Python
version; the builtin `hash()` is deliberately *not* used because it is salted):

```python
from openfacefx import (generate_from_alignment, load_mfa_textgrid,
                        Variants, VariantGroup, Alternative, Event, resolve)

track = generate_from_alignment(load_mfa_textgrid("voice.TextGrid"))
track.variants = Variants("npc_greet_017", [
    VariantGroup("headgest", [
        Alternative(1.0, [Event(0.4, "gesture", "nod_small", payload={"intensity": 0.6})]),
        Alternative(2.0, [Event(0.4, "gesture", "nod_big")]),   # twice as likely
    ]),
])
events = resolve(track)     # same line id -> same pick, every run
```

Each group hashes independently, so a head-gesture choice and a gaze choice vary
independently for one line. On the command line, `--events-file layer.json`
attaches an authored events/variants block and `--line-id ID` bakes the chosen
take into concrete events on write.

Events serialize into the track JSON (an optional top-level `events` / `variants`
array — see [Output format](#output-format)) and into **Unity `.anim`
AnimationEvents**: each event becomes an `AnimationEvent` Unity SendMessage-invokes
on the Animator's GameObject (`OnFaceEvent` by default), with the event name and
payload packed into its single `stringParameter` as `name|{json}`; ranged events
(`dur > 0`) expand to a `_Begin`/`_End` pair. For **Unreal**, `write_unreal_notifies`
emits an `AnimNotify` sidecar JSON that a short editor-Python snippet stamps onto a
`UAnimSequence` (point events → `UAnimNotify`, ranged → `UAnimNotifyState`); the
snippet ships in that module's docstring. The mouth-only cue/`.lip` exporters
ignore events.

## Prosody events from the audio (pitch & loudness)

`--events` reads accents from the *timing* (stress digits, loudness peaks).
`--prosody` reads them from the **pitch** of the voice as well — a numpy
autocorrelation pitch tracker follows the fundamental frequency (F0), and where
pitch **and** loudness spike together you get an `emphasis`; a silent pause or the
end of the line is a `phrase_boundary`; a rising terminal F0 (the yes/no-question
cue) is a `question_rise` (issue [#4](https://github.com/OpenFaceFX/OpenFaceFX/issues/4)).
It needs the audio, so pass `--wav`:

```bash
python -m openfacefx naive  --text "are you going" --wav voice.wav --prosody -o track.json
python -m openfacefx mfa    --textgrid voice.TextGrid --wav voice.wav --prosody -o track.anim
python -m openfacefx energy --wav voice.wav                          --prosody -o track.json
```

The events are ordinary [`Event`s](#events--takes-game-engine-notifies), so they
ride the same JSON / Unity `.anim` / Unreal notify path and **compose** with
`--events` and `--gestures` (the audio-derived `phrase_boundary` sits happily
beside a timing-derived `phrase`). Library callers get `prosody_events(wav, fps)`,
the `prosody_features(wav)` bundle (F0, voicing, loudness, speaking rate) and the
raw `pitch_track(wav)`. It is fully deterministic (no RNG — identical events on
Python 3.9/3.13) and **opt-in**: without `--prosody`, output is byte-identical.

**This is DSP, not an ML prosody model.** Autocorrelation F0 is within a few
percent on clean voiced speech but makes octave errors and mislabels voicing on
whispered/breathy/creaky voice or low SNR, and it will misbehave on music,
background noise and overlapping speakers; prominence and question detection are
rule-based cue layers, not phonological labelling. That is fine here — the events
only need *relative* pitch movement to land in the right place, not calibrated Hz.
Input is 16-bit PCM WAV (convert first with `ffmpeg -c:a pcm_s16le`).

## Preview what you generated

`examples/preview.html` is a self-contained page (no server needed) that
animates a schematic mouth from a track and plots every viseme channel with a
scrubbing playhead. Rebuild it for your own track:

```bash
python tools/build_preview.py track.json preview.html
```

To answer the usual QA question — *is the timing right against the audio?* —
embed the voice line and a phoneme lane. `--wav` bakes the audio in as a data
URI (decoded client-side, no network) so playback stays in sync with the
playhead and draws a waveform; `--segments` adds a clickable phoneme/word lane
above the transport — click a segment to seek there, or to hear just that slice
when audio is embedded, and low-`confidence` blocks are tinted red so alignment
errors stand out. The `naive`/`mfa` commands dump the lane data with
`--emit-segments`:

```bash
openfacefx naive --text "hello world" --wav voice.wav \
  -o track.json --emit-segments segs.json
python tools/build_preview.py track.json preview.html \
  --wav voice.wav --segments segs.json
```

`--segments` accepts that JSON — a list of `{"phoneme", "start", "end"}`
objects (optional `confidence` in `[0, 1]`), optionally wrapped as
`{"segments": [...], "words": [...]}` to draw a word lane too — or a Praat
`.TextGrid` straight from the Montreal Forced Aligner. Output is byte-identical
to before when neither flag is given, and the page stays a single file with no
network requests.

<img src="docs/preview.png" width="850" alt="OpenFaceFX previewer: schematic mouth animating next to the viseme channel curves of a generated track"/>

*The built-in previewer playing a track generated from `examples/voice.wav` —
schematic articulator on the left, the exported viseme curves with a scrubbing
playhead on the right; building with `--wav`/`--segments` adds synced audio, a
waveform, and the phoneme lane. [Try it live.](https://openfacefx.github.io/OpenFaceFX/demo/)*

## Output format

Deliberately trivial JSON (CSV also available) — sparse `[time, value]` keys
per viseme channel, weights in `[0, 1]`. The full shape, abbreviated:

```jsonc
{
  "format": "openfacefx.track", "version": 1, "fps": 60.0, "duration": 1.6,
  "viseme_set": ["sil", "PP", "FF", "TH", "DD", "kk", "CH", "SS", "nn", "RR", "aa", "E", "I", "O", "U"],
  "channels": [
    { "name": "sil", "keys": [[0.0, 0.6196], [0.0833, 0.6644], /* … */] }
    // one object per active viseme channel
  ]
}
```

Channel names are blendshape names your rig exposes; linear interpolation
between keys is the intended playback. See
[docs/COMPATIBILITY.md](docs/COMPATIBILITY.md) for a ~15-line reference reader.

Two optional top-level keys, `events` and `variants`, carry the
[event/take layer](#events--takes-game-engine-notifies) — emitted **only when
present**, so a track without them is byte-identical to the above and `version`
stays `1`. Readers ignore unknown top-level keys, so this is forward-compatible:

```jsonc
{ /* … format/version/fps/duration/viseme_set/channels as above … */
  "events": [
    { "t": 0.55, "type": "emphasis", "name": "beat", "dur": 0.0,
      "payload": {"strength": 1.0}, "blend_in": 0.0, "blend_out": 0.0 }
  ]
  // "variants": { "line_id": "npc_greet_017", "groups": [ … ] }  // authored takes
}
```

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

## FaceFX ecosystem compatibility

We surveyed every public FaceFX wrapper on GitHub. The short version: **all of
them are parallel audio+text generators, not curve consumers** — none accepts
any lip-sync tool's curves as input, so feeding one our curves is impossible by
design, for us and everyone else. What *is* possible is writing the artifacts
their pipelines consume — and, because the de-facto-standard
[`FaceFXWrapper.exe`](https://github.com/Nukem9/FaceFXWrapper) is itself an
audio+text generator, *replacing* it outright with a
[drop-in shim](docs/facefxwrapper.md) (issue [#33](https://github.com/OpenFaceFX/OpenFaceFX/issues/33)):

| Ecosystem | Route | Status |
|---|---|---|
| Unity / VRChat / Ready Player Me | `-o clip.anim` — AnimationClip with `viseme_*` or `vrc.v_*` blendshape curves, plus optional `m_Events` AnimationEvents from the [event layer](#events--takes-game-engine-notifies) (`--events`) | ✅ shipped |
| Live2D Cubism (VTuber 2D) | `-o mouth.motion3.json` — parameter curves; mouth-open by default, per-vowel via `--live2d-params`, or auto-targeted from a `model3.json` LipSync group | ✅ shipped |
| Godot 4 | `-o lipsync.tres` — `AnimationPlayer` resource, one `blend_shapes/*` value track per viseme (`--godot-node`/`--godot-naming`) | ✅ shipped |
| ARKit / Rhubarb / VRM / CC4 rigs | `--retarget arkit\|rhubarb\|vrm\|cc4` weighted remaps ([docs](docs/retargeting.md)) | ✅ shipped |
| Unreal (official FaceFX-UE4/UE5 plugins) | Impossible via the plugins (proprietary `.ffxc` compiler); instead drive UE float curves / morph targets from JSON — the `arkit` remap feeds MetaHuman's ARKit route — plus an `AnimNotify` sidecar JSON (`write_unreal_notifies`) an editor-Python snippet stamps onto a `UAnimSequence` | ✅ JSON + AnimNotify sidecar |
| Bethesda modding (Nukem9/FaceFXWrapper, xVASynth, Mantella, Pantella) | `.fuz` container + `.lip` header tools (`openfacefx.bethesda`), an **experimental** clean-room Skyrim `.lip` **writer** (`-o out.lip` from `naive`/`mfa`; the payload was reverse-engineered and our codec re-encodes the real samples byte-exact, **not yet verified in-game** [#12](https://github.com/OpenFaceFX/OpenFaceFX/issues/12)), plus a **`FaceFXWrapper.exe`-compatible drop-in shim** those pipelines can call in place of Nukem9's tool ([docs](docs/facefxwrapper.md), [#33](https://github.com/OpenFaceFX/OpenFaceFX/issues/33)) | 🧪 experimental writer + drop-in shim shipped — needs in-game confirmation |
| Anything else | Trivial JSON/CSV + documented remap | ✅ today |

Full survey with per-tool details: [docs/COMPATIBILITY.md](docs/COMPATIBILITY.md).

## Roadmap

The full backlog lives in the [issues](https://github.com/OpenFaceFX/OpenFaceFX/issues)
(milestone v0.2.0), distilled from a feature-gap survey against FaceFX.

- [x] Unity `AnimationClip` exporter (`-o clip.anim`, oculus/vrchat naming)
- [x] Live2D `motion3.json` ([#20](https://github.com/OpenFaceFX/OpenFaceFX/issues/20)) and Godot `.tres` ([#21](https://github.com/OpenFaceFX/OpenFaceFX/issues/21)) exporters
- [x] Published remap tables: ARKit-52, Rhubarb, Preston-Blair, VRM, CC4
- [x] Component-based coarticulation with tunable articulator timing ([#1](https://github.com/OpenFaceFX/OpenFaceFX/issues/1))
- [x] Data-driven weighted phoneme→target mapping ([#2](https://github.com/OpenFaceFX/OpenFaceFX/issues/2))
- [x] Batch directory processing with QA reports ([#3](https://github.com/OpenFaceFX/OpenFaceFX/issues/3))
- [~] Bethesda `.LIP` exporter — **experimental Skyrim writer shipped** (`-o out.lip`; re-encodes the real samples byte-exact, in-game verification pending) ([#12](https://github.com/OpenFaceFX/OpenFaceFX/issues/12))
- [~] Prosody, gestures, events, text tags, i18n ([#4](https://github.com/OpenFaceFX/OpenFaceFX/issues/4)–[#8](https://github.com/OpenFaceFX/OpenFaceFX/issues/8)) — **shipped**: procedural gestures (`--gestures`, [#5](https://github.com/OpenFaceFX/OpenFaceFX/issues/5)), the event/take layer (`--events`, [#6](https://github.com/OpenFaceFX/OpenFaceFX/issues/6)), and audio prosody events from a numpy pitch tracker (`--prosody`: emphasis / phrase-boundary / question-rise, [#4](https://github.com/OpenFaceFX/OpenFaceFX/issues/4))

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
  timing.py         TTS phoneme/viseme timing adapters (from-timing) ← skip the aligner
  anchors.py        word/segment-anchored naive alignment (SRT + TTS word timings)
  visemes.py        viseme set + phoneme→viseme map
  mapping.py        weighted phoneme→target mapping (JSON)  ← remap phonemes here
  coarticulation.py component dominance blending, CoartParams ← the interesting math
  curves.py         keyframe reduction, FaceTrack
  io_export.py      JSON / CSV writers
  export_unity.py   Unity .anim AnimationClip writer
  export_live2d.py  Live2D Cubism motion3.json parameter-curve writer
  export_godot.py   Godot 4 .tres AnimationPlayer resource writer
  export_cues.py    Rhubarb TSV/XML/JSON, Moho/OpenToonz .dat, Papagayo .pgo cues
  retarget.py       viseme→rig remapping + presets          ← retarget rigs here
  bethesda.py       .fuz container / .lip header tools
  export_lip.py     Bethesda Skyrim .lip writer (EXPERIMENTAL, #12) ← unverified in-game
  batch.py          directory batch runner + QA summary
  energy.py         audio-loudness fallback lip-sync (no transcript) ← amplitude-driven
  prosody.py        numpy autocorrelation pitch tracker → emphasis/boundary/question events (#4) ← --prosody
  events.py         timed/typed events + deterministic takes (#6) ← --events, game-engine notifies
  gestures.py       procedural blinks/brows/head/eyes, GestureParams (#5) ← opt-in, deterministic
  gestures_layers.py  gesture event-extraction + per-layer curve synthesis (gestures.py's engine)
  pipeline.py       orchestration
  cli.py            command line
tests/test_core.py  run: pytest
tools/              HTML previewer builder + viseme-gallery SVG renderer
docs/               logo, images, viseme gallery, quickstart tape, compatibility survey
```

CI runs the test suite plus CLI and preview-builder smoke tests on every push,
across Linux / Windows / macOS on Python 3.9, 3.12 and 3.13.

## License

MIT — see [LICENSE](LICENSE).

*FaceFX® is a registered trademark of OC3 Entertainment, Inc. OpenFaceFX is an
independent project — not affiliated with, endorsed by, or connected to OC3
Entertainment or Speech Graphics — and contains no code or data from FaceFX
products.*
