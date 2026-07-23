<div align="center">

<img src="https://raw.githubusercontent.com/OpenFaceFX/OpenFaceFX/main/docs/logo.svg" width="140" alt="OpenFaceFX logo"/>

# OpenFaceFX

**Open-source lip-sync in the spirit of FaceFX: voice recording + transcript → animation curves that drive a character's face.**

[![CI](https://github.com/OpenFaceFX/OpenFaceFX/actions/workflows/ci.yml/badge.svg)](https://github.com/OpenFaceFX/OpenFaceFX/actions/workflows/ci.yml)
[![Docs](https://img.shields.io/badge/docs-openfacefx.github.io-f4b942.svg)](https://openfacefx.github.io/OpenFaceFX/docs/)
[![License: MIT](https://img.shields.io/badge/license-MIT-f4b942.svg)](LICENSE)
[![Python 3.9+](https://img.shields.io/badge/python-3.9%2B-3776ab.svg?logo=python&logoColor=white)](pyproject.toml)
[![Runtime deps](https://img.shields.io/badge/runtime%20deps-numpy%20only-6e7681.svg)](pyproject.toml)
[![Status](https://img.shields.io/badge/status-alpha-e06c5b.svg)](#scope--honesty)
[![Release](https://img.shields.io/github/v/release/OpenFaceFX/OpenFaceFX?color=f4b942)](https://github.com/OpenFaceFX/OpenFaceFX/releases)
[![Buy Me a Coffee](https://img.shields.io/badge/support-buy%20me%20a%20coffee-ffdd00.svg?logo=buymeacoffee&logoColor=black)](https://buymeacoffee.com/openfacefx)

<img src="https://raw.githubusercontent.com/OpenFaceFX/OpenFaceFX/main/docs/talking-head.svg" width="460" alt="OpenFaceFX animating a face's mouth from the phrase 'open source lip sync from audio + text' — every frame is the dominant viseme of the curves the pipeline generated"/>

*Each mouth frame above is the dominant viseme of a real `track.json` the pipeline generated from that sentence — no ML, just deterministic phoneme→viseme→curve math.*

**[▶ Live demo](https://openfacefx.github.io/OpenFaceFX/demo/)** · **[🔊 Hear it](https://openfacefx.github.io/OpenFaceFX/face.html)** (real speech, mouth synced to audio) · **[Read the docs →](https://openfacefx.github.io/OpenFaceFX/docs/)**

<a href="https://openfacefx.github.io/OpenFaceFX/demo/"><img src="https://openfacefx.github.io/OpenFaceFX/quickstart.gif" width="850" alt="Quickstart: one naive command turns 'hello world' plus a WAV into a viseme track JSON"/></a>

*The one-command quickstart, rendered from [`docs/quickstart.tape`](docs/quickstart.tape) by [VHS](https://github.com/charmbracelet/vhs) in CI on every push — recorded as code, so it can't drift from the real CLI. [Open the live previewer →](https://openfacefx.github.io/OpenFaceFX/demo/)*

</div>

## Install

```bash
pip install openfacefx        # numpy is the only runtime dependency
```

Or from source, to contribute:

```bash
git clone https://github.com/OpenFaceFX/OpenFaceFX && cd OpenFaceFX
pip install -e .
```

## Studio — the visual workspace

Prefer a GUI? **OpenFaceFX Studio** is a web-based facial-animation studio (the
[FaceFX Studio](https://facefx.github.io/documentation/) workflow, open) with
Preview, a phoneme timeline, a curve editor, a Face Graph retarget view, every
exporter, and an AI assistant (bring-your-own-key LLMs, encrypted client-side):

```bash
openfacefx studio            # serves it locally against the native pipeline, opens your browser
```

It's one dependency-free web app that runs three ways from the same code: **in the
browser** via Pyodide (zero-install, pipeline runs client-side), **standalone on a
PC** (`openfacefx studio`, Tauri/Electron-wrappable), and as the basis for a
**SaaS**. See [`docs/studio.md`](docs/studio.md).

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
| **sil** | <img src="https://raw.githubusercontent.com/OpenFaceFX/OpenFaceFX/main/docs/visemes/sil.svg" width="72" alt="sil mouth shape"> | — | neutral / mouth at rest |
| **PP** | <img src="https://raw.githubusercontent.com/OpenFaceFX/OpenFaceFX/main/docs/visemes/PP.svg" width="72" alt="PP mouth shape"> | `B`, `M`, `P` | lips pressed shut |
| **FF** | <img src="https://raw.githubusercontent.com/OpenFaceFX/OpenFaceFX/main/docs/visemes/FF.svg" width="72" alt="FF mouth shape"> | `F`, `V` | lower lip to upper teeth |
| **TH** | <img src="https://raw.githubusercontent.com/OpenFaceFX/OpenFaceFX/main/docs/visemes/TH.svg" width="72" alt="TH mouth shape"> | `DH`, `TH` | tongue between the teeth |
| **DD** | <img src="https://raw.githubusercontent.com/OpenFaceFX/OpenFaceFX/main/docs/visemes/DD.svg" width="72" alt="DD mouth shape"> | `D`, `L`, `T` | tongue to the alveolar ridge |
| **kk** | <img src="https://raw.githubusercontent.com/OpenFaceFX/OpenFaceFX/main/docs/visemes/kk.svg" width="72" alt="kk mouth shape"> | `G`, `HH`, `K` | back of tongue raised |
| **CH** | <img src="https://raw.githubusercontent.com/OpenFaceFX/OpenFaceFX/main/docs/visemes/CH.svg" width="72" alt="CH mouth shape"> | `CH`, `JH`, `SH`, `ZH` | rounded, protruded |
| **SS** | <img src="https://raw.githubusercontent.com/OpenFaceFX/OpenFaceFX/main/docs/visemes/SS.svg" width="72" alt="SS mouth shape"> | `S`, `Z` | narrow, teeth close |
| **nn** | <img src="https://raw.githubusercontent.com/OpenFaceFX/OpenFaceFX/main/docs/visemes/nn.svg" width="72" alt="nn mouth shape"> | `N`, `NG` | nasal, tongue up |
| **RR** | <img src="https://raw.githubusercontent.com/OpenFaceFX/OpenFaceFX/main/docs/visemes/RR.svg" width="72" alt="RR mouth shape"> | `ER`, `R` | retroflex / lightly rounded |
| **aa** | <img src="https://raw.githubusercontent.com/OpenFaceFX/OpenFaceFX/main/docs/visemes/aa.svg" width="72" alt="aa mouth shape"> | `AA`, `AE`, `AH`, `AY` | open jaw |
| **E** | <img src="https://raw.githubusercontent.com/OpenFaceFX/OpenFaceFX/main/docs/visemes/E.svg" width="72" alt="E mouth shape"> | `EH`, `EY`, `IH` | mid-front spread |
| **I** | <img src="https://raw.githubusercontent.com/OpenFaceFX/OpenFaceFX/main/docs/visemes/I.svg" width="72" alt="I mouth shape"> | `IY`, `Y` | wide spread |
| **O** | <img src="https://raw.githubusercontent.com/OpenFaceFX/OpenFaceFX/main/docs/visemes/O.svg" width="72" alt="O mouth shape"> | `AO`, `AW`, `OW`, `OY` | rounded and open |
| **U** | <img src="https://raw.githubusercontent.com/OpenFaceFX/OpenFaceFX/main/docs/visemes/U.svg" width="72" alt="U mouth shape"> | `UH`, `UW`, `W` | tight lip rounding |

To retarget to a different rig (Apple ARKit's 52 blendshapes, a Preston-Blair
12-shape set, …), edit `PHONEME_TO_VISEME` and `VISEMES` in `visemes.py` —
nothing else changes.

## What it is

FaceFX-style tools are really four subsystems chained together. Only the first
(acoustic alignment) needs a heavy model — and excellent open-source aligners
already exist. So OpenFaceFX **wraps** the aligner instead of reinventing it,
and fully owns the other three stages:

<img src="https://raw.githubusercontent.com/OpenFaceFX/OpenFaceFX/main/docs/pipeline.svg" width="100%" alt="Pipeline: audio + text → alignment → visemes → coarticulation → keyframes → JSON/CSV"/>

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
`model3.json` LipSync group), a Godot 4 `AnimationPlayer` resource (`.tres`,
one blendshape value track per viseme, `--godot-node`/`--godot-naming`), or a
**VRM Animation** (`.vrma`) expression clip a VRM 1.0 avatar will lip-sync to in
UniVRM / three-vrm / VMagicMirror (`VRMC_vrm_animation`, the vowel expressions
`aa/ih/ou/ee/oh`, `--vrma-head-node` for head pose; a track with a baked emotion
layer also fills the `happy/angry/sad/surprised` expression slots):

```bash
python -m openfacefx naive --text "..." --wav voice.wav -o mouth.motion3.json  # Live2D Cubism
python -m openfacefx mfa --textgrid voice.TextGrid -o lipsync.tres            # Godot 4
python -m openfacefx naive --text "..." --wav voice.wav -o avatar.vrma        # VRM 1.0 avatar
python -m openfacefx naive --text "..." --spine-base rig.json -o rig.spine.json  # Spine (2D)
python -m openfacefx naive --text "..." --wav voice.wav -o smile.exp3.json    # Live2D expression pose
```

For 2D games on **Spine** (Esoteric Software), `--spine-base rig.json` splices a
mouth-slot attachment timeline into your existing Spine project (bones, skins and
other animations untouched); omit it for a standalone stub skeleton. A Live2D
`.exp3.json` freezes one pose (default: the peak-activity frame, or `--exp3-at`)
as a VTube-Studio-bindable expression — the static companion to `motion3.json`.

Whole dialogue trees at once, with an OOV/confidence QA report and
incremental re-runs:

```bash
python -m openfacefx batch --dir voice/ --out tracks/ --recurse --modified-only --jobs 8
```

For dialogue-scale runs, `--machine-readable` streams a live NDJSON progress log,
`--ledger` keeps an append-only run trail, and `--cue-warnings` ranks cue
outliers — see [Batch runs](#batch-runs-live-progress-a-run-ledger-and-cue-qa).
Or drive the batch from a **localization string table** with
`--manifest loc.csv` (one row per line, keyed by loc-ID) instead of a file tree —
see [Loc-table manifests](#loc-table-manifests---manifest).

Weighted many-to-many phoneme mapping and coarticulation timing are
data/parameters, not code — see `examples/mappings/` and `CoartParams`.
JALI-style artistic dials tune articulation strength without retiming: `--intensity`
(master, `<1` mumbles, `>1` hyper-articulates) and repeatable `--gain class=value`
(e.g. `--gain tongue=0.6 --gain jaw=1.2`); all `1.0` is a byte-identical no-op.
Named **`--style` presets** bundle those dials into a delivery style — `neutral`
(the defaults, byte-identical), `whisper`, `mumble`, `tense`, `exaggerated`,
`broad` — and explicit `--intensity`/`--gain` still compose on top. **`--stress-emphasis`**
`[AMOUNT]` articulates lexically stressed syllables more strongly: it biases
ARPABET primary/secondary-stressed vowels up and unstressed ones down (via the
dominance blend, so each frame still sums to ~1 and lip closures still seal). Off
by default; a no-op on inputs without stress digits (`STYLE_PRESETS`,
`style_params`, `CoartParams.stress_emphasis` for library callers).

**JALI coarticulation rules** ([SIGGRAPH 2016](https://www.dgp.toronto.edu/~elf/JALISIG16.pdf))
extend the component model with a **data-driven rule table** — opt-in behind
`CoartParams(jali=True)`, and **byte-identical to the legacy path when off**. It
adds JALI's hard constraints (bilabial/labiodental closure, sibilants narrow the
jaw, non-nasals open the lips), its habits (duplicated-viseme merge across word
boundaries — "po_p m_an"; lip-heavy visemes UW/OW/OY/w/S/Z/J/C start early and
hold longer; a tongue articulation never pulls the lips; a short obstruent/nasal
leaves the jaw untouched; a word-final lip shape anticipates), and an **empirical
per-phoneme onset/decay lookup** (post-pause vs post-vowel onsets, ~150 ms
lip-protrusion extension) in place of the per-class timing constants. The rules,
category phoneme sets and timing constants live in `data/jali_rules.json` (plain
data, so new measurements drop in) and each is individually toggleable via
`jali_rules` (`JALI_RULE_IDS` lists them). A custom mapping can also give a target
NVIDIA-A2F-style `gain`/`offset` to scale/bias a channel (chiefly the tongue) —
mapping **schema v2** (#53); version-1 files still load, the absent fields reading
as the no-op defaults.

FaceFX-style post-solve curve conditioning smooths and retimes the curves
without re-solving: `--smooth SECONDS` runs a temporal Gaussian (sigma in
seconds) over the dense curves before keyframe reduction to soften jitter — lip
closures are re-sealed *after* the filter, so `/p/ /b/ /m/ /f/ /v/` stay sharp
— and `--lag MS` slides every viseme curve to trail (`>0`) or lead (`<0`) the
audio, clamped into the clip. Both default off (byte-identical) and apply to
`naive`/`mfa`/`from-timing`/`energy`.

When retargeting, trim individual rig shapes without forking a preset table:
`--adjust adjust.json` applies a per-target `clamp(gain*v + offset, 0, 1)` (JSON
`{"jawOpen": {"gain": 0.8}, "mouthSmileLeft": {"offset": 0.15}}` — soften the
jaw, hold a smile slightly on), and `--retarget-shapes shapes.json` restricts a
preset to a rig's real shapes (a JSON array), rerouting any it lacks through the
preset's fallback table (e.g. a tongue-less ARKit rig). Both leave the weighted
tables untouched; details in [docs/retargeting.md](docs/retargeting.md).

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
blinks, eyebrow raises, head nods and idle sway, gaze saccades, and (with
`--breath`) an idle chest rise/fall — on top of
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

## Text tags: directing animation from the script

Steer the generated animation with inline tags in the transcript — expression
curves, event notifies, local emphasis and audio chunking — the way FaceFX's
[text-tagging](https://facefx.github.io/documentation/doc/text-tagging) stage
does. Tags are stripped **before** grapheme-to-phoneme conversion, the clean
words are lip-synced as usual, and each tag is mapped onto the timeline the
aligner produced. Turn it on with `--tags` (or just include a tag — clear tags
auto-enable it):

```bash
python -m openfacefx naive --tags --duration 3 -o out.track.json \
  --text 'I said [brow_raise type=ct v1=1]really[/brow_raise] loud [event:sound payload="clap"] now.'
```

The syntax is modelled on the FaceFX text-tag docs (and, for `[emphasis]` /
`[pause]`, on SSML `<emphasis>` / `<break>`):

| Family | Syntax | Effect |
|---|---|---|
| **Curve** | `[Name type=quad\|lt\|ct\|tt v1=.. v2=.. v3=.. v4=.. easein=.. easeout=.. timeshift=.. duration=..]word(s)[/Name]` | adds an animation channel `Name` keyframed over the tagged word span (leading/centered/trailing triplet or quadruplet, 0.2 s ease default) |
| **Event** | `[event:NAME k=v ...]`, `[gesture:NAME ...]`, or FaceFX `{"group\|anim" start=.. payload=".." ...}` | injects an [event](#events--takes-game-engine-notifies) at the **start of the following word** (end of the last word if trailing); `start`/`duration`/`blendin`/`blendout` map to event fields, everything else is kept in the payload |
| **Emphasis** | `[emphasis]word[/emphasis]` (optional `strength=`) | raises the local vowel peak over the span (reuses the `--stress-emphasis` dominance pass from #18) |
| **Chunk** | `<T>` angle-bracket marker(s), e.g. `<5>Yes I'm here<7.5>` | pins text to audio time `T`; the naive utterance is split into phrases with `sil` in the gaps. Times must be non-negative, `<= duration` and non-decreasing, else a `ValueError` |
| **Pause / phrase** | `[pause:SECONDS]` / `[break time=..]`, `[phrase]` | inserts that much silence at the word boundary; `[phrase]` drops a `marker/phrase` event |

Curve tags are still lip-synced (the word survives), event payloads round-trip
through the track JSON, and the tag layer composes with `--gestures` / `--events`
/ `--prosody` / `--edits`. A **tagless transcript is byte-identical** to a run
without `--tags`, so switching it on is safe.

Programmatically, `parse_tagged_transcript(text) -> (clean_text, tags)` exposes
the parse, and `generate_naive(text, duration, parse_tags=True,
preprocess=fn)` runs an optional `callable(text) -> text` first — a registered
auto-tagger (regex head-shakes on *no/not*, phonetic respelling of proper nouns)
that injects a tag is identical to hand-writing it. Deterministic, stdlib-only
parsing (`re` / `shlex`). `--tags` is rejected with `-o .lip` (no curve/event
slot) and with `--anchors`.

### SSML input: the same markup you feed your TTS

Author already carrying [SSML](https://www.w3.org/TR/speech-synthesis11/) for
Azure / Google / Polly? Feed the *same document* in with `--ssml` (or just pass a
`<speak>` root — it auto-detects) and it drives lip-sync through a **thin
front-end over the tags above** — `<break>`→`[pause]`, `<emphasis level=..>`→
`[emphasis]`, `<mark>`/`<p>`/`<s>`→`[phrase]`, `<sub alias=..>` substitutes the
spoken form, `<say-as>` normalizes its text:

```bash
python -m openfacefx naive --ssml --duration 3 -o out.track.json \
  --text '<speak>Say <emphasis level="strong">brave</emphasis> <break time="300ms"/> new world <mark name="beat"/></speak>'
```

It parses with the stdlib `xml.etree`, produces the **same `(clean_text, tags)`**
as the equivalent bracket transcript (so an SSML document is byte-identical to
the tagged one through the whole pipeline, and a construct-free `<speak>` is
byte-identical to plain `naive --text`), degrades unknown elements to their text,
and raises a clear `ValueError` on malformed XML. `<phoneme ph=..>` pronunciation
override is deferred to the i18n framework (#8). See the
[SSML input](https://openfacefx.github.io/OpenFaceFX/api/ssml/) reference;
`parse_ssml(text) -> (clean_text, tags)` is the library entry.

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

## Preserving hand-edits across a re-run

The pipeline is a pure function, so re-running it — to re-tune `--intensity`, a
`--gain`, the coarticulation, or a new alignment — throws away any manual tweak an
animator made to the curves. OpenFaceFX solves this the way FaceFX does, with a
two-layer ownership model (issue [#9](https://github.com/OpenFaceFX/OpenFaceFX/issues/9)):
analysis **owns** the generated curves, and a user keeps their edits in a small,
separate **sidecar** `*.edits.json` — never inline, so the `.track` stays clean,
versioned interchange and `version` stays `1`.

Capture what you changed by diffing a hand-edited track against the baseline it
came from, then apply the sidecar on any later run with `--edits`:

```bash
python -m openfacefx naive --text "..." --wav voice.wav -o base.json      # generate
#   ...an animator hand-edits base.json -> edited.json in a curve editor...
python -m openfacefx diff-edits base.json edited.json -o line.edits.json   # capture
python -m openfacefx naive --text "..." --wav voice.wav \
       --intensity 1.2 --edits line.edits.json -o final.json               # re-run, edits kept
```

Two per-channel modes mirror FaceFX's *offset curve* and *owned-off* editing:

- **`offset`** (default) stores the delta from the baseline. Being *relative*, it
  rides on top of whatever the solver now produces — so an offset survives an
  intensity / gain / coarticulation change, which is the common case. The result
  is `clamp(analysis + offset)`, exactly FaceFX's "virtual curve".
- **`replace`** stores absolute values (full manual ownership). Add `--span T0 T1`
  to lock only a **time region**: that window is user-owned and the freshly
  generated curve shows through everywhere else.

Conflicts are handled conservatively. An edit whose channel the regeneration
dropped (a renamed shape, or a word removed on re-alignment) is **preserved and
reported** by default (`--on-conflict keep-edit` — a hand-edit is never silently
lost); `take-generated` discards it for the fresh output instead. A locked region
always wins inside its span. Library callers get `diff_edits(base, edited)`,
`apply_edits(regenerated, edits)` and `load_edits`/`save_edits`; the merge is
deterministic (numpy `interp`/`clip` + the same RDP thinner, no RNG — identical on
Python 3.9/3.13) and **opt-in**: without `--edits`, output is byte-identical.

The sidecar is plain JSON — a stable `base_hash` of the baseline for provenance,
`source_id` (optionally the audio's sha1, via `diff-edits --source`), and one
record per edited channel:

```jsonc
{
  "format": "openfacefx.edits", "version": 1,
  "base_hash": "sha1:…", "fps": 60.0,
  "channels": {
    "aa": { "mode": "offset",  "keys": [[0.0, 0.15], [0.8, 0.15]], "clamp": [0.0, 1.0] },
    "PP": { "mode": "replace", "span": [1.20, 1.80], "keys": [[1.20, 0.9], [1.80, 0.9]] }
  }
}
```

**Out of scope** (issue #9 keeps it numpy + stdlib, deterministic, non-ML): no
Bezier/tangent handles (curves are linear by design), no phoneme-anchored *rebase*
of edit times onto a changed transcript (offsets on the *same* audio are the
supported robustness story; a transcript rewrite that drops a channel is flagged,
not auto-migrated), and no 3-way / multi-user merge beyond keep / take.

## Emotion & expression over speech

Production rigs keep expression on a **separate additive layer** over lip-sync —
SALSA's EmoteR blends emphasis-timed emotes over speech, and Unreal additive
animation is the *difference between a pose and a reference pose* added onto the
base. OpenFaceFX does the same (issue [#38](https://github.com/OpenFaceFX/OpenFaceFX/issues/38)):
an authored emotion envelope becomes a true additive delta `channel_value -
reference_value`, baked onto the speech-solved channels with a global intensity
dial and per-channel clamps. Bake it over a solved track with the `emotion`
command:

```bash
python -m openfacefx naive --text "we did it" --duration 1.5 -o base.json
python -m openfacefx emotion base.json happy.emotion.json --intensity 1.0 -o baked.json
```

An envelope carries either **direct emotion-channel keyframes** (`smile` /
`frown` / `brow_raise` …) or a compact **valence/arousal** keyframe track (both in
`[-1, 1]`) mapped through a **fixed, hand-authored table** by bilinear
interpolation — high valence → smile + cheek raise, low valence + high arousal →
brow lower. It is a table lookup and interpolation, **not ML**; the neutral point
`valence = arousal = 0` maps to an all-zero pose. The baked result is a normal
track that exports through every exporter (the mouth-only cue/`.lip` writers
ignore the expression channels, and `--retarget` passes them through). Library
callers get `bake_emotion(track, envelope)`, `va_to_pose(valence, arousal)` and
`load_envelope`/`save_envelope`; the bake is deterministic (numpy `interp`/`clip`
+ the same RDP thinner, no RNG — identical on Python 3.9/3.13) and **opt-in**:
with `--intensity 0`, a neutral envelope, or a zero delta, output is
byte-identical to the plain speech track. See
[docs/api/emotion.md](docs/api/emotion.md) for the full valence/arousal table.

## Import mouth-cue files (Rhubarb, Papagayo, Moho)

OpenFaceFX writes stepped mouth-cue files for the indie 2D ecosystem — and now
reads them back (issue [#44](https://github.com/OpenFaceFX/OpenFaceFX/issues/44)),
so a studio sitting on a Rhubarb/Papagayo library or a hand-timed Moho mouth
track has a migration path **into** the tool: import, then coarticulate,
retarget, layer gestures/events, condition and re-export to Unity/Godot/Live2D.
The `from-cues` command auto-detects the format by extension and first line:

```bash
python -m openfacefx from-cues mouth.tsv -o track.json           # Rhubarb TSV/XML/JSON
python -m openfacefx from-cues mouth.dat --fps 24 -o track.anim  # Moho/OpenToonz -> Unity
python -m openfacefx from-cues mouth.pgo --coarticulate -o track.json  # Papagayo, smoothed
```

Each parser is the **verified inverse** of the matching cue *exporter*: the shape
tables are derived from the same retarget presets the writers use, so
`write → from-cues → write` round-trips **byte-identically** for Rhubarb and to a
byte-exact fixed point (preserving the exact shape/frame sequence) for the
frame-based Moho `.dat` / Papagayo `.pgo`. The result is an ordinary stepped
`FaceTrack` (one `[0,1]` viseme channel, `sil` in the gaps) that flows through
every exporter and `--retarget`; `--coarticulate` re-solves the steps through the
dominance blend. Extended/unknown shapes route through the documented
`RHUBARB_EXTENDED_FALLBACK` or raise a clear error — never silently dropped.
Library callers get `import_cues(path)`, `detect_format`, `build_cue_track` and
the `RHUBARB_TO_VISEME` / `PRESTON_BLAIR_TO_VISEME` tables; stdlib + numpy,
deterministic, and purely additive (no existing output changes). See
[docs/api/importers.md](docs/api/importers.md).

The sibling `from-csv` command imports **blendshape-weight CSV** (issue
[#45](https://github.com/OpenFaceFX/OpenFaceFX/issues/45)) — the OpenFaceFX long
`time,channel,value` format (exact inverse of `write_csv`) or a wide per-frame
Apple ARKit / Epic Live Link Face export (row = frame, columns = blendshape
names, optional `Timecode`):

```bash
python -m openfacefx from-csv capture.csv --fps 60 -o track.anim   # ARKit / Live Link Face
```

Channel names land in **rig space** verbatim (`jawOpen`, `mouthSmileLeft`, …),
values clamped `[0,1]`, timecode/frame → seconds, and each column RDP-thinned via
`reduce_to_track`. It deliberately does not recover visemes (the viseme→ARKit map
is many-to-one) — it brings the raw channels in to condition and re-export.
`read_csv(path)` is the library entry; numpy + stdlib, deterministic.

**NVIDIA Audio2Face** interop (`.a2f.json`, the non-USD path) reads and writes A2F's
dense blendshape JSON (`facsNames` + `weightMat`) in both directions — export a
synthetic performance to an A2F-targeted rig, or bring A2F output in to retarget,
condition and re-export:

```bash
python -m openfacefx naive --text "..." --wav voice.wav --retarget arkit -o perf.a2f.json  # → A2F
python -m openfacefx from-a2f a2f_output.json -o track.json                                 # A2F →
```

## Subtitles & captions (SRT / WebVTT)

Captions and lip motion should come from **one source of truth** so they stay in
sync. OpenFaceFX already *ingests* word timings (`parse_srt`, Azure / ElevenLabs
word boundaries); the `captions` command is the matching *output* — SRT and
WebVTT timed by the **same** word alignment the lip curves use (it pulls word
spans from `naive_word_segments`, whose phoneme segments are byte-identical to
the `naive_segments` the visemes are reduced from):

```bash
python -m openfacefx captions --text "Well met, traveler." --wav vo.wav -o vo.srt
python -m openfacefx captions --text "Well met." --duration 2 -o vo.vtt --karaoke
```

Cues are packed under a max-line-length × max-lines **wrap budget** (`--max-line`
/ `--max-lines`, no cue exceeds it), split at sentence ends and pauses (`--gap`),
and each is held long enough to read at a configurable **reading speed**
(`--cps`, characters/sec) — monotonic and non-overlapping, `HH:MM:SS,mmm` (SRT) /
`HH:MM:SS.mmm` (WebVTT). `--karaoke` adds WebVTT `<c>` word spans with inline cue
timestamps for word-level highlighting. Co-generate a track and its captions in
one run with `naive … --emit-captions vo.srt`, or write a caption sidecar next to
every naive-mode track in a batch with `batch … --captions srt`. `srt_text` is
the exact inverse of `parse_srt` — a round-trip recovers the cue spans.
`write_captions(text, duration, path)` is the library entry; pure stdlib,
deterministic.

Captions also read **back in**: `parse_vtt` turns WebVTT (plain **or** the
karaoke `<c>` spans above) into timing anchors — `parse_vtt(vtt_text(cues))`
round-trips within millisecond rounding, karaoke recovering word-level anchors —
so an existing subtitle file drives lip-sync via `naive --anchors captions.vtt
--anchors-format vtt` (self-transcribing like `srt`, no `--text` needed).

## Re-export or retarget an existing track (`convert`)

Every exporter used to be reachable only as the `-o` sink of a generate command.
`convert` (issue [#46](https://github.com/OpenFaceFX/OpenFaceFX/issues/46))
decouples **generation** from **delivery** — load an existing `track.json` and
emit any other format, or retarget it, **without re-running the solver** (no audio
or TextGrid needed). It's the natural partner to the importers: `from-cues` /
`from-csv` → `convert` → Unity/Godot/Live2D.

```bash
python -m openfacefx convert track.json -o clip.anim                 # to Unity
python -m openfacefx convert track.json --retarget arkit -o rig.json  # retarget
python -m openfacefx convert track.json --edits line.edits.json -o final.tres
```

It routes the loaded track through the **exact same** `--edits` → exporter
dispatch the generate commands use, so the output is **byte-identical to
generating that track** by construction — the same `--retarget`/`--adjust`/
`--retarget-shapes`/`--edits` and format flags apply. (The `openfacefx.track` JSON
stores keyframe *times* at 4 dp, so an exporter that renders finer time precision
reflects that quantisation; it's byte-identical for every exporter when the
track's frame times are 4-dp-representable, and for CSV/cues/JSON always.) Pure
re-serialisation plus the existing transforms — no solver, no RNG. `.lip` stays
guarded exactly as in the generate path (it needs phonemes a viseme track lacks).

## Inspect & validate (CI lint)

Two deterministic, **read-only** commands (issue
[#47](https://github.com/OpenFaceFX/OpenFaceFX/issues/47)) answer *"what's in this
track?"* and *"is it well-formed?"* without opening the previewer:

```bash
python -m openfacefx inspect track.json            # human table (or --json)
python -m openfacefx validate track.json           # lint gate; exits nonzero on a violation
python -m openfacefx validate line.edits.json --json --strict
```

`inspect` reports duration, fps, channel/keyframe counts, per-channel coverage and
the weight/pose/gesture split, with a schema-stable `--json` (every key always
present). `validate` auto-detects a `.track.json`, an `*.edits.json` sidecar, or a
standalone events file and checks the contract — monotonic in-bounds key times,
weight channels in `[0,1]` (signed head/eye **pose** angles flagged only when
wildly out of range), `viseme_set` consistency, and event/variant blocks — exiting
nonzero with a deterministic, sorted problem list so a CI job fails cleanly on a
malformed asset. It exits `0` on every track the generators and importers produce.
Library callers get `inspect_track`, `validate_asset`/`validate_file`,
`detect_kind`; stdlib only, deterministic. See
[docs/api/inspect.md](docs/api/inspect.md).

## Transform a track (retime / mirror / trim)

Deterministic post-production edits (issue
[#48](https://github.com/OpenFaceFX/OpenFaceFX/issues/48)) that `postprocess.time_shift`
can't do — it only *slides*, never stretches. They compose with `convert` and the
importers (bring a capture in, retime it to the new VO, re-export):

```bash
python -m openfacefx transform track.json --duration 3.2 -o fit.json    # retime to 3.2 s
python -m openfacefx transform track.json --wav newvo.wav -o redub.json  # ...or a WAV length
python -m openfacefx transform track.json --mirror -o flipped.json       # L/R mirror
python -m openfacefx transform track.json --trim 0.5 2.0 -o slice.json    # keep [0.5, 2.0]
```

- **retime** scales every keyframe **and** event time (by `--retime FACTOR`, to a
  `--duration`, or to a `--wav` length, about an optional `--anchor`); channel
  **values** are unchanged and every key is preserved (a uniform scale adds no
  redundancy). 2× exactly doubles every time and the duration.
- **mirror** swaps `*Left`/`*Right` channel pairs (an extensible pair table) and
  negates the signed lateral pose channels (`headYaw`/`headRoll`/`eyeYaw`), leaving
  centered channels (visemes, `jawOpen`, `headPitch`) untouched. It's a pure
  relabel + sign flip, so **`mirror ∘ mirror` is byte-identical** to the original.
- **trim** keeps `[t0, t1]`, rebases to `0`, and drops/reclamps events to the
  window; an empty window yields an empty track, not a crash.

And the sequential complement, **`sequence`** (issue
[#51](https://github.com/OpenFaceFX/OpenFaceFX/issues/51)) — splice finished tracks
end-to-end into one timeline (stitch per-line VO into a conversation, build a barks
reel, insert beats):

```bash
python -m openfacefx sequence line1.json line2.json --gap 0.5 -o scene.json
```

`concat(tracks, gaps=…, crossfade=…)` offsets every keyframe + event time by the
cumulative start (`duration = Σ durations + Σ gaps`), unions channels (an absent
channel rests at `0` across its span — no cross-seam bleed), and is the seam
inverse of `trim`. A single-track `concat([a])` is byte-identical to `a`; the
default hard cut is a pure relabel/offset (no re-thin), with an optional
`--crossfade S` linear seam blend.

Library callers get `retime`, `retime_to_duration`, `mirror`, `trim`, `concat` and
the `MIRROR_PAIRS`/`MIRROR_NEGATE` tables; numpy + stdlib, deterministic, additive.
See [docs/api/transforms.md](docs/api/transforms.md).

## Export LOD variants for distance thinning

Game runtimes thin facial animation with distance. `lod` (issue
[#36](https://github.com/OpenFaceFX/OpenFaceFX/issues/36)) produces **K detail
levels from one solve** — a pure re-run of the `_rdp` / `edits.sample` machinery
we already ship, at a tiered tolerance table:

```bash
python -m openfacefx lod clip.track.json -o out/clip                       # default 3 tiers
python -m openfacefx lod clip.track.json --rdp 0.002,0.01,0.04 --fps 60,30,15 -o out/clip
```

writes `out/clip_lod0.json` … plus an `out/clip_lod.json` metadata sidecar. Two
tiers: an **RDP tier** re-thins each channel at a rising epsilon (LOD0 dense,
higher tiers only major inflections — and it never invents a key, so LOD0 at the
source epsilon is **byte-identical** to the input); an **fps tier** resamples each
channel onto a coarser grid (60/30/15 fps) before thinning so a distant LOD
updates less often. Higher tiers carry a monotonically non-increasing keyframe
count. The sidecar names each variant's epsilon+fps and ships an *advisory*
screen-coverage → LOD-index switching table (the engine owns the switch — there's
no camera at export). It does **not** overload `FaceTrack.variants` (that's the
event-take layer); variants are separate files. Library callers get
`generate_lods(track)`, `make_lod`, `lod_metadata`; numpy + stdlib, deterministic,
additive. See [docs/api/lod.md](docs/api/lod.md).

## Fit a channel budget (morph cap / per-LOD)

Rigs have fixed morph-target budgets and drop secondary detail at distance. The
budget pass (issue [#37](https://github.com/OpenFaceFX/OpenFaceFX/issues/37)) ranks
channels by **total energy** (summed abs key-to-key delta — how much a channel
moves) and keeps the top N, dropping the low-energy secondary micro-channels
**entirely**:

```bash
python -m openfacefx transform clip.track.json --max-channels 20 -o rig.json   # hard cap
python -m openfacefx lod clip.track.json --max-channels 15,8,4 -o out/clip      # per-LOD
```

In speech the jaw + primary lip visemes are highest-energy, so the ranking keeps
them naturally (no protect-set). The cap applies to the `[0,1]` morph channels
only — the signed head/eye **pose** channels pass through unchanged and aren't
counted toward N (they drive bones, not morph targets, and their degree-scale
deltas aren't comparable to `[0,1]` weights). Dropped channels are removed, not
zeroed; the cap never yields more than N morph channels; and the per-channel energy
ranking is written as sidecar metadata either way (`transform` →
`<out>.budget.json`, `lod` → the `*_lod.json`). Absent the flag, output is
byte-identical. Library callers get
`rank_channels`, `budget_channels(track, N)`, `channel_energy`; stdlib,
deterministic, additive. See [docs/api/budget.md](docs/api/budget.md).

## Export separate animation layers

Engines often re-blend or toggle facial layers at runtime rather than take one
flattened curve set (Unreal additive tracks, SALSA priority blending). `export-layers`
(issue [#39](https://github.com/OpenFaceFX/OpenFaceFX/issues/39)) decomposes a
merged track into named **speech / emotion / gesture** sub-tracks with a per-layer
blend-weight curve + integer priority:

```bash
python -m openfacefx export-layers merged.track.json -o layered.track.json
```

It writes the **same flat track** plus an optional top-level `layers` block, so the
default output is byte-identical and a reader that ignores the block still gets the
merged track. Every channel lands in exactly one layer, so summing the layers at
weight 1 reproduces the flat track exactly — a faithful, lossless decomposition; the
runtime mix stays the engine's job. Library callers get `build_layers`,
`flatten_layers`, `layers_to_dict`/`layers_from_dict` and the `Layer` type;
`to_dict(track, layers=…)`/`from_dict` round-trip the block. numpy + stdlib,
deterministic, additive. See [docs/api/layers.md](docs/api/layers.md).

## Golden-file drift check (`diff`)

OpenFaceFX guarantees deterministic bytes — `diff` (issue
[#50](https://github.com/OpenFaceFX/OpenFaceFX/issues/50)) is the golden-file /
snapshot gate that leverages it: *did a solver-param / coarticulation / retarget
change actually move the curves, and by how much?*

```bash
python -m openfacefx diff golden.track.json candidate.track.json                 # exit 0 iff exact
python -m openfacefx diff golden.track.json candidate.track.json --tolerance 0.002 --json
```

A read-only structured drift report — duration/fps delta, per-channel
added/removed, and for shared channels **max-abs / RMS / mean-abs** value delta on
a shared dense grid, plus coverage/key drift and event changes. It **exits nonzero
when any delta exceeds `--tolerance`** (default `0.0` → exact match) with a
deterministic, sorted `{channel, metric, value}` problem list, so CI diffs stay
stable. Unlike `validate` (single-file contract) and `diff-edits` (writes a
sidecar), `diff` takes two tracks and never writes. Library callers get
`diff_tracks(a, b, tolerance=…)`; pure numpy + stdlib. See
[docs/api/inspect.md](docs/api/inspect.md).

## Export vendor-neutral glTF 2.0

Every other 3D exporter here is engine-specific; **glTF 2.0** (issue
[#49](https://github.com/OpenFaceFX/OpenFaceFX/issues/49)) is the ISO/IEC 12113
interchange standard imported by Blender, Three.js, Babylon, Godot, Unity, Unreal
— and the base of **VRM**. Its animation natively drives **morph-target weights**,
exactly OpenFaceFX's `[0,1]` viseme/blendshape model, so one portable file plays
anywhere:

```bash
python -m openfacefx naive --text "..." --wav v.wav -o face.gltf   # JSON + base64 buffer
python -m openfacefx convert track.json -o face.glb               # binary container
python -m openfacefx convert track.json --gltf-head-node -o face.glb   # + head rotation
```

A stub mesh declares N morph targets named after the weight channels
(`mesh.extras.targetNames`), a node references them, and one LINEAR animation
drives the `weights` path; accessors are packed as little-endian FLOAT via numpy,
`.glb` as a `struct` header + JSON + BIN chunk. Only `[0,1]` weight channels become
morphs — signed head/eye pose channels are excluded by default (opt-in
`--gltf-head-node` adds a separate `rotation` sampler). The Khronos glTF Validator
is the external gate; the in-repo proof is a full accessor **round-trip**
(reconstructs every channel within `1e-6`). Deterministic, numpy + stdlib. See
[docs/api/gltf.md](docs/api/gltf.md).

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

<img src="https://raw.githubusercontent.com/OpenFaceFX/OpenFaceFX/main/docs/preview.png" width="850" alt="OpenFaceFX previewer: schematic mouth animating next to the viseme channel curves of a generated track"/>

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

## Scripting / CI (machine-readable output & embedding)

The four generate commands (`naive`, `mfa`, `from-timing`, `energy`) take
`--json`, which prints a **single-line JSON QA summary** to stdout instead of the
human `wrote …` line, so a wrapping tool or CI job parses one object rather than
scraping console text. The written track file is **byte-identical** with or
without the flag; `--report FILE` writes the same JSON (indented) to a file while
keeping the human line.

```console
$ openfacefx naive --text "it's a teszt" --wav vo.wav -o vo.json --json
{"format": "openfacefx.qa", "version": 1, "command": "naive", "output": "vo.json",
 "fps": 60.0, "duration": 1.6, "channels": 10, "keyframes": 148, "gestures": 0,
 "events": 0, "oov_words": ["teszt"], "substitutions": [{"from": "\u2019", "to": "'", "count": 1}],
 "cue_warnings": [{"phoneme": "T", "start": 0.94, "duration": 0.02, "kind": "short"}],
 "warnings": ["1 word(s) fell back to G2P rules (add to a pronunciation dict): teszt"]}
```

Turn that OOV list into an editable asset with `emit-oov-dict`: it writes a
reviewable CMUdict of rule-G2P *guesses* for the words that fell through, which you
fix and load back with `--cmudict` (the MFA validate→g2p loop, offline):

```bash
python -m openfacefx emit-oov-dict --transcript script.txt -o guesses.dict  # review, then:
python -m openfacefx naive --text "…" --cmudict guesses.dict -o mouth.json
```

Every key is always present (lists empty rather than absent), so the schema is
stable to assert on. `oov_words` are words that fell through to the crude G2P
rule fallback — worth adding to a CMUdict; `cue_warnings` are phoneme cues below
`--min-cue` (default 0.03 s) or above `--max-cue` (default 0.5 s), each with its
clip, `start` and `duration`; `confidence_warnings` are phonemes whose aligner
confidence is below `--min-confidence` (default 0.5) — populated only when your
aligner supplies per-phone confidence; `substitutions` reports the transcript
normalization pass (below). The process exit code is nonzero on a real error
(`batch` returns nonzero if any file failed), so `set -e` scripts stop as
expected.

**Transcript normalization.** Before G2P, `naive` folds the Unicode punctuation a
TTS engine or a pasted script tends to carry — ellipsis `…`, en/em dashes,
curly quotes `‘’“”`, non-breaking space — down to ASCII, and reports each fold in
`substitutions`. The load-bearing case is the curly apostrophe: `it’s` typed with
U+2019 otherwise splits into two tokens. ASCII transcripts are unaffected;
`--no-normalize` opts out.

**Embedding without the CLI.** The core is a plain library — `generate_naive`,
`generate_from_alignment`, `generate_from_energy` return a `FaceTrack`. The same
QA signals are public functions, so an embedding app or notebook gets the summary
without shelling out:

```python
import openfacefx as ofx

text, subs = ofx.normalize_transcript("it’s a teszt…")   # ("it's a teszt...", [...])
track = ofx.generate_naive(text, duration=1.6)
oov = ofx.G2P().oov_words(text)                            # ["teszt"]
summary = ofx.summarize(track, segments=None, oov_words=oov)   # the dict above
short_long = ofx.cue_flags(segments, min_dur=0.03, max_dur=0.5)
```

`summarize(track)` is deterministic and JSON-ready (same inputs, same bytes).

## Batch runs: live progress, a run ledger, and cue QA

`batch` turns a whole dialogue tree into tracks in one command. Three opt-in
flags make a large run observable and auditable **without changing its default
output** — with none of them the printed table and `batch_summary.json` are
byte-identical to before.

**`--machine-readable`** streams an NDJSON event log to **stderr** (one JSON
object per line), so a supervising process can follow the run live while stdout
keeps the human table — or add **`--quiet`** to drop the table and keep only the
machine output:

```console
$ openfacefx batch --dir voice/ --out tracks/ --recurse --machine-readable --quiet
{"event": "start", "total": 1200, "todo": 1200, "skipped": 0, "jobs": 8, "ext": "json", "recurse": true}
{"event": "progress", "index": 0, "file": "mq01/l01.wav", "out": "mq01/l01.json", "status": "ok", "mode": "mfa", "channels": 12, "keyframes": 210, "oov": [], "cue_warnings": 0, "min_confidence": 0.62, "warnings": []}
{"event": "warning", "index": 3, "file": "mq01/l04.wav", "message": "2 word(s) fell back to G2P rules: zorblat, awakens"}
{"event": "failure", "index": 7, "file": "mq01/l08.wav", "error": "FileNotFoundError: no transcript: expected same-stem .TextGrid or .txt"}
{"event": "done", "processed": 1200, "failed": 1, "skipped": 0, "exit": 1}
```

| event | when | fields |
|-------|------|--------|
| `start` | once, first | `total`, `todo`, `skipped`, `jobs`, `ext`, `recurse` |
| `progress` | once per processed file, in processing order | `index`, `file`, `out`, `status` (`ok`/`failed`), `mode`, `channels`, `keyframes`, `oov`, `cue_warnings`, `min_confidence`, `warnings` |
| `warning` | per per-file warning | `index`, `file`, `message` |
| `failure` | per failed file | `index`, `file`, `error` |
| `done` | once, last | `processed`, `failed`, `skipped`, `exit` |

Events stream in **processing order** (`os.walk` + sorted), while the summary
table stays worst-first sorted. The field set is fixed and `ensure_ascii`, so the
stream is pure ASCII and safe to parse a line at a time.

**`--ledger FILE`** appends one NDJSON record per run — it never rewrites the
file, so a `--modified-only` re-run simply adds another line: the args snapshot,
every discovered input's size/mtime, and the outcome counts, i.e. a
reproducibility/audit trail for dialogue-scale runs.

```json
{"format": "openfacefx.batch.ledger", "version": 1, "run": "9f9731688453cc8f",
 "args": {"dir": "voice/", "out": "tracks/", "recurse": true, "modified_only": false,
          "jobs": 8, "ext": "json", "mapping": null, "cmudict": null, "fps": 60.0,
          "cue_warnings": false, "min_cue": 0.03, "max_cue": 0.5},
 "inputs": {"count": 1200, "files": [
   {"file": "mq01/l01.wav", "mtime": 1783711018.36, "size": 512044, "transcript": "mfa"}]},
 "outcome": {"processed": 1200, "failed": 1, "skipped": 0, "exit": 1}, "ext": "json"}
```

The `run` id is a SHA-256 over the run's identity (the args plus each input's
path/size/mtime) — **deterministic and wall-clock-free**, so two identical
re-runs hash the same and a changed input or arg hashes differently. `mtime` is
file metadata for audit, never `Date.now`, so the ledger stays reproducible.

**`--cue-warnings`** folds the phoneme-cue check (`qa.cue_flags`, the same one
behind the generate commands' `cue_warnings`) into the summary: each row gains an
integer count of cues shorter than `--min-cue` (default 0.03 s) or longer than
`--max-cue` (default 0.5 s), and the worst-first ranking gains it as a final
tiebreaker so cue-heavy files surface alongside failures, low confidence and OOV.
It is opt-in because adding the count would otherwise change `batch_summary.json`;
without the flag the summary is byte-identical.

### Loc-table manifests (`--manifest`)

Real game VO is driven by a **localization string table**, not a directory of
same-stem files: Unity / Godot / Unreal export String Table Collections keyed by
a loc-ID, and FaceFX keys VO to an *entrytag*. `--manifest FILE` reads a CSV/TSV
table and emits one track per row through the **same** pipeline, summary table,
NDJSON stream and ledger — it just swaps the directory walk for a table read
(the two modes are mutually exclusive):

```bash
python -m openfacefx batch --manifest loc.csv --out tracks/ --ledger runs.ndjson
```

```csv
id,audio,text,language,character,mapping,style,out
greeting_01,vo/en/guard_hello.wav,"Well met, traveler.",en,Guard,,,
quest_intro,vo/en/mage_intro.wav,"The Zorblat awakens…",en,Mage,rigs/mage.json,whisper,quests/intro.json
```

Columns are matched **by header, forgivingly** (case / spacing / punctuation are
ignored): `id`/`key`/`entrytag`, `audio`/`wav`/`voice`, `text`/`transcript`/`line`,
`language`/`locale`, `character`/`speaker`, `mapping`/`rig`, `style`, and an
optional explicit `out` (else `<id>.<ext>` under the tree). Paths resolve relative
to the manifest. The `mapping` and `style` columns thread into that row's solve (a
per-line rig or coarticulation preset); `language`/`character` ride along on the
summary row. A **missing-audio, unreadable or malformed row is an isolated
per-row failure** — the batch continues and it shows up as a failure in the
summary, NDJSON and ledger, exactly like a bad file in directory mode. Parsing is
stdlib `csv` only; CSV/TSV today, PO/XLIFF and pivoted one-column-per-locale
tables are future follow-ups. With `--manifest` absent the directory-walk output
is byte-identical.

### VO delivery audit (`audit`)

The reconciliation pair to the manifest driver: `audit` compares a **delivered
audio folder** against the loc-table the way a localization vendor's pre-delivery
QA pass does — **read-only**, a deterministic QA gate:

```bash
python -m openfacefx audit --manifest loc.csv --delivered vo/ --json
```

It reports, itemized and keyed by loc-ID: **missing** lines (a row whose declared
audio isn't in the delivery), **orphan** files (delivered audio no row
references), **duration** outliers (actual `wav_duration` outside a configurable
`--duration-tolerance` of the `len(text)/--cps` estimate — a take inside
tolerance is never flagged), **empty/near-silent** takes (~0 duration or ~0 RMS),
**naming** violations (a file stem that doesn't match the loc-ID), plus a
**language-coverage matrix** that surfaces per-locale holes. It exits nonzero when
issues are found (a CI gate) — human worst-first table, or `--json` for the full
report (the `batch_summary.json` schema style). It **writes nothing** under the
delivered folder, reuses `pipeline.wav_duration` for stats, and shares the #40
`read_manifest` parser. `audit_delivery(manifest, delivered)` is the library entry.

## Plugging in a real aligner (stage 1)

The naive aligner spaces phonemes by duration priors — fine for prototyping,
not for shipping. For production accuracy, produce a list of
`PhonemeSegment(phoneme, start, end)` from any of these and pass it to
`generate_from_alignment`:

- **Montreal Forced Aligner** — best accuracy; parser included (`load_mfa_textgrid`).
- **Whisper / WhisperX** — word timings from the most common audio→timing tool;
  **built-in adapters** (`from_whisper_json` / `from_whisperx`, or `naive
  --anchors words.json --anchors-format whisper|whisperx`). Word-level needs no
  transcript — the aligner supplies the words.
- **Gentle** — Kaldi-based forced aligner with free **phoneme**-level timings;
  **built-in adapters** (`from_gentle` for word anchors, `from_gentle_phones` for
  the accurate phone path, or `--anchors-format gentle|gentle-phones`).
- **Vosk** — offline Kaldi ASR with word timings + per-word confidence;
  **built-in adapter** (`from_vosk`, or `--anchors-format vosk`, with an optional
  `--vosk-min-conf` gate). Self-transcribing, so `--text` is optional.
- **wav2vec2 / any other source** — produce a list of `PhonemeSegment` and pass it
  to `generate_from_alignment`.

Better G2P: drop in the full CMU Pronouncing Dictionary with
`G2P().load_cmudict("cmudict.dict")` (the built-in dictionary is a tiny seed).

### Other languages (dictionaries, pronouncers, IPA/SAMPA)

The grapheme-to-phoneme stage is a **protocol** (`Pronouncer`: a tokenizer + a
word→phoneme map) with the English `G2P` as one implementation, and its default
path is **byte-identical** — the i18n hooks are all opt-in:

```python
g = G2P()
g.load_dictionary("ja.dict")          # a .dict declaring locale + ipa/sampa/arpabet
g.pronouncer = lambda w, prev, nxt: ... # callable(word, prev, next) -> phones | None
g.tokenizer  = lambda text: text.split()  # keep non-Latin script (default drops it)
```

- **Dictionaries** declare a `locale` and phoneme `alphabet`; `read_dictionary`
  maps IPA/SAMPA/ARPAbet entries into the internal inventory via the alias tables
  (`IPA_ALIASES` / `SAMPA_ALIASES` cover all 39 phonemes and round-trip exactly —
  also handy for display).
- A **pronouncer hook** is consulted **between** dictionary lookup and the rule
  fallback (FaceFX's lookup→pronouncer→rules), receiving previous/next word
  context — the way Czech/Polish are done in code.
- A **pluggable tokenizer** per language keeps non-Latin tokens the default
  `[A-Za-z']+` split would drop.

A phoneme with no internal equivalent passes through and falls to `sil` at the
viseme stage (map it to give it a mouth shape). See the
[Multi-language pronunciation](https://openfacefx.github.io/OpenFaceFX/api/i18n/)
reference.

## Streaming / real-time generation

For a live pipeline — a TTS engine emitting phonemes as it speaks —
`StreamingGenerator` carries coarticulation state across pushed chunks in
**constant memory** and emits keyframes incrementally:

```python
from openfacefx import StreamingGenerator, frames_to_track

gen = StreamingGenerator(fps=60.0, look_ahead=0.5)   # look_ahead = latency dial
frames = []
for chunk in phoneme_chunks:      # each a list of PhonemeSegment
    frames += gen.push(chunk)
frames += gen.flush()
track = frames_to_track(frames, 60.0)
```

It reuses the **exact** offline component math over a bounded segment window.
**Honestly**: because the coarticulation dominance is exponential/infinite-support
(`exp(-theta·|t−c|)`, normalized over every segment), streaming reproduces
`generate_from_alignment` **within tolerance, not bit-exactly** — pruning old
segments and a finite look-ahead both omit exponentially small tails. `look_ahead`
is the **single latency ↔ fidelity dial** with an `O(exp(-theta·W))` error bound
(W≈1.5 s → ~1e-2, W≈3 s → ~1e-4, W≈4.5 s → ~1e-6); `0` is zero-latency causal-only
(no anticipation). One case is **exact**: when the window covers the whole clip
(`look_ahead`/`back_span` ≥ clip length) the per-frame blend is bit-identical to
offline. Chunk boundaries never matter (1 chunk == K chunks, bit-exact), the
buffer is `O(window)`, and a later chunk can never alter an already-emitted frame
(causal; the optional `causal_smooth` is a past-only filter, distinct from the
offline symmetric smoother). In-process only — network transport is out of scope.

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
| MikuMikuDance / MMD (VTuber, blender_mmd_tools, three.js `MMDLoader`, babylon-mmd) | `-o motion.vmd` — Vocaloid Motion Data morph animation; native visemes → Japanese kana lip morphs (あいうえお/ん), map overridable (`--vmd-model`/`--vmd-fps`) | ✅ shipped |
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
  export_gltf.py    glTF 2.0 morph-target animation (.gltf/.glb), vendor-neutral (#49) ← -o .gltf/.glb
  export_cues.py    Rhubarb TSV/XML/JSON, Moho/OpenToonz .dat, Papagayo .pgo cues
  retarget.py       viseme→rig remapping + presets          ← retarget rigs here
  bethesda.py       .fuz container / .lip header tools
  export_lip.py     Bethesda Skyrim .lip writer (EXPERIMENTAL, #12) ← unverified in-game
  batch.py          directory batch runner + QA summary
  energy.py         audio-loudness fallback lip-sync (no transcript) ← amplitude-driven
  prosody.py        numpy autocorrelation pitch tracker → emphasis/boundary/question events (#4) ← --prosody
  events.py         timed/typed events + deterministic takes (#6) ← --events, game-engine notifies
  gestures.py       procedural blinks/brows/head/eyes, GestureParams (#5) ← opt-in, deterministic
  edits.py          edit-preservation sidecar: diff/apply hand-edits (#9) ← --edits, diff-edits
  emotion.py        additive emotion/expression layer, valence/arousal table (#38) ← emotion command
  importers.py      read Rhubarb/Moho/Papagayo cue files back into a track (#44) ← from-cues command
  importers_csv.py  read ARKit/Live Link Face blendshape-weight CSV into a track (#45) ← from-csv command
  inspect.py        read-only track stats + a CI format/contract linter (#47) ← inspect, validate commands
  trackdiff.py      read-only A/B drift report, tolerance-gated exit (#50) ← diff command
  transforms.py     retime/mirror/trim (#48) + concat/sequence splice (#51) ← transform, sequence
  lod.py            offline LOD variant export (RDP-eps + fps-resample tiers) (#36) ← lod command
  budget.py         energy-ranked channel-budget reduction / morph cap (#37) ← --max-channels
  layers.py         layered speech/emotion/gesture export + blend/priority (#39) ← export-layers
  gestures_layers.py  gesture event-extraction + per-layer curve synthesis (gestures.py's engine)
  pipeline.py       orchestration
  cli.py            command line
tests/test_core.py  run: pytest
tools/              HTML previewer builder + viseme-gallery SVG renderer
docs/               logo, images, viseme gallery, quickstart tape, compatibility survey
```

CI runs the test suite plus CLI and preview-builder smoke tests on every push,
across Linux / Windows / macOS on Python 3.9, 3.12 and 3.13.

## Support

OpenFaceFX is free and MIT-licensed. If it saves you time, you can support
development on **[Buy Me a Coffee](https://buymeacoffee.com/openfacefx)** — it
funds new features, testing, and keeping the project free for everyone. Starring
the repo, filing issues, and (once you've tried it in an engine) reporting how
the `.lip` / FaceFXWrapper path works in-game help just as much.

## License

MIT — see [LICENSE](LICENSE).

*FaceFX® is a registered trademark of OC3 Entertainment, Inc. OpenFaceFX is an
independent project — not affiliated with, endorsed by, or connected to OC3
Entertainment or Speech Graphics — and contains no code or data from FaceFX
products.*
