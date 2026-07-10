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

For dialogue-scale runs, `--machine-readable` streams a live NDJSON progress log,
`--ledger` keeps an append-only run trail, and `--cue-warnings` ranks cue
outliers — see [Batch runs](#batch-runs-live-progress-a-run-ledger-and-cue-qa).

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

Every key is always present (lists empty rather than absent), so the schema is
stable to assert on. `oov_words` are words that fell through to the crude G2P
rule fallback — worth adding to a CMUdict; `cue_warnings` are phoneme cues below
`--min-cue` (default 0.03 s) or above `--max-cue` (default 0.5 s), each with its
clip, `start` and `duration`; `substitutions` reports the transcript
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
  edits.py          edit-preservation sidecar: diff/apply hand-edits (#9) ← --edits, diff-edits
  emotion.py        additive emotion/expression layer, valence/arousal table (#38) ← emotion command
  importers.py      read Rhubarb/Moho/Papagayo cue files back into a track (#44) ← from-cues command
  importers_csv.py  read ARKit/Live Link Face blendshape-weight CSV into a track (#45) ← from-csv command
  inspect.py        read-only track stats + a CI format/contract linter (#47) ← inspect, validate commands
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
