# Open-source aligner adapters

OpenFaceFX ships timing adapters for every commercial TTS source (Azure,
ElevenLabs, Kokoro, Google, Piper, Cartesia, Polly). Issue #54 closes the
asymmetry with first-class adapters for the **free, open-source** aligners/ASR
that indie and research users actually have — so a user with only a WAV gets
accurate, model-backed timings into the pipeline without MFA and without writing
glue. They are siblings of `anchors.from_azure_word_boundaries`, stdlib `json`
only, and additive (with no `--anchors-format`, output is unchanged).

| adapter | source | returns |
|---|---|---|
| [`from_whisper_json`][openfacefx.aligners.from_whisper_json] | OpenAI Whisper `verbose_json` (`segments[].words[]` or a flat `words[]`) | word `Anchor`s |
| [`from_whisperx`][openfacefx.aligners.from_whisperx] | WhisperX `segments[].words[]` | word `Anchor`s |
| [`from_gentle`][openfacefx.aligners.from_gentle] | Gentle `words[]` (`case == "success"`) | word `Anchor`s |
| [`from_gentle_phones`][openfacefx.aligners.from_gentle_phones] | Gentle per-word `phones[]` | `PhonemeSegment`s (phone path) |

```bash
# word timings — the aligner supplies the words, so no --text needed
python -m openfacefx naive --anchors words.json --anchors-format whisper -o out.json
python -m openfacefx naive --anchors align.json --anchors-format whisperx --wav vo.wav -o out.json
# Gentle's free phoneme timings — the accurate path, straight to generate_from_alignment
python -m openfacefx naive --anchors gentle.json --anchors-format gentle-phones -o out.json
```

## Tolerance + the deterministic drop rule

The adapters tolerate the **key variance** across openai-whisper / faster-whisper /
whisper.cpp (`word` vs `text`, `probability` vs `score`). Aligners leave some words
**unaligned** — Whisper omits the timestamp, Gentle marks `case != "success"` — and
such a word is **dropped deterministically**: its neighbours' anchors still pin the
timeline and the aligner spreads the gap. A phone/word symbol outside the ARPAbet
inventory (Gentle `oov`, a non-English token) passes through and falls to `sil` at
the viseme stage (documented), never a crash.

## The Gentle phone path

Gentle's per-word `phones[]` carry a relative `duration` and an ARPAbet symbol with
a `_B`/`_I`/`_E`/`_S` position suffix. `from_gentle_phones` strips the suffix,
upper-cases, and accumulates the durations from each word's `start` into absolute
`PhonemeSegment`s (the last phone ends at the word span within float tolerance),
with silence filling the gaps between words. This **skips the naive spacer** and
feeds `generate_from_alignment` directly — a genuinely phone-accurate track from a
free tool.

::: openfacefx.aligners
