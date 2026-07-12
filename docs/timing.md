# TTS timing ingest (`openfacefx from-timing`)

Text-to-speech engines already know exactly when every phoneme or viseme
happens, so they can replace the aligner (MFA or the naive aligner) entirely —
the highest-value adapter class from the ecosystem survey (#14).

`from-timing` parses a vendor's timing dump into one **normalized schema** —
`TimingEvent(unit, symbol, start, end)` — and feeds the existing pipeline at two
entry points:

| Unit | Formats | Path |
|---|---|---|
| `phoneme` | `pho`, `piper`, `cartesia` | replaces aligner output → weighted mapping + coarticulation, unchanged |
| `viseme` | `azure`, `polly`, `voicevox` | skips phoneme→target mapping → vendor remap preset → coarticulation |

```bash
openfacefx from-timing --file voice.pho    --format pho      -o track.json
openfacefx from-timing --file align.json   --format piper --sample-rate 22050 -o track.json
openfacefx from-timing --file marks.json   --format cartesia -o track.json
openfacefx from-timing --file visemes.json --format azure    -o track.json
openfacefx from-timing --file voice.marks  --format polly     -o track.json --retarget arkit
openfacefx from-timing --file query.json   --format voicevox  -o track.json   # JP TTS
```

Only start times are guaranteed. Start-only sources (Azure, Polly) get each
event's end from the next event's start; the final event is held for
`--final-duration` seconds (default `0.08`).

## Units and field names, per source

| Format | Unit | Time in the file | Fields |
|---|---|---|---|
| `pho` (MBROLA) | phoneme | per-phoneme duration in **ms**, cumulative | `SYMBOL DURATION_MS [pos% Hz …]`; `;` comments |
| `piper` | phoneme | per-phoneme audio **sample counts** ÷ `--sample-rate` | `phonemes[]` + `phoneme_id_samples[]` (or `alignments:[{phoneme,num_samples}]`) |
| `cartesia` | phoneme | explicit start/end **seconds** | `phoneme_timestamps:{phonemes[],start[],end[]}` |
| `azure` | viseme | audio offset in **100-ns ticks** (÷10000 = ms) | array of `{audio_offset, viseme_id}` |
| `polly` | viseme | `time` in integer **ms** | NDJSON marks; `type=="viseme"`, `value`, `time` |
| `voicevox` | viseme | per-mora consonant/vowel **seconds**, cumulative from `prePhonemeLength`, ÷ `speedScale`; pauses honor the `pauseLength`/`pauseLengthScale` overrides | `/audio_query`: `accent_phrases[].moras[].{consonant,consonant_length,vowel,vowel_length}`, `pause_mora`, `pre`/`postPhonemeLength`, `speedScale`, `pauseLength`, `pauseLengthScale` |

`pho`, `piper` and `cartesia` symbols are the source's own alphabet (IPA for
Piper/Cartesia, a SAMPA variant for MBROLA `.pho`) — **not** ARPABET. So for
those three formats `from-timing` **auto-selects a built-in IPA preset** when no
`--mapping` is given (an explicit `--mapping` still wins); see below. The viseme
formats need no mapping either: `AZURE_VISEME_TO_TARGET` (22 IDs),
`POLLY_VISEME_TO_TARGET` and `VOICEVOX_TO_TARGET` (OpenJTalk phonemes) remap
straight onto the Oculus-15 targets. Symbols outside the active table produce a QA
warning and relax to silence — never a crash. `voicevox` also covers the
API-compatible forks COEIROINK / SHAREVOX / LMROID / AivisSpeech (same schema).

## The built-in IPA preset (pho/piper/cartesia)

`openfacefx.ipa.IPA_MAPPING` keys the Oculus-15 targets by the IPA inventory
Piper, Cartesia and espeak-ng emit, so those sources produce rich mouth shapes
out of the box:

```bash
openfacefx from-timing --file voice.pho --format pho -o track.json   # no --mapping
```

Rather than one row per diacritic-carrying variant, the preset stores base
symbols and **normalizes the lookup key** (`_normalize_ipa`):

| Marks | Rule | Example |
|---|---|---|
| stress `ˈ` `ˌ` (and ASCII `'`) | dropped | `ˈɑ` → `ɑ` |
| length `ː` `ˑ` (and ASCII `:`) | dropped | `ɑː` → `ɑ`, `iː` → `i` |
| affricate **tie bar** `◌͡◌` | dropped → plain digraph | `t͡ʃ` = `tʃ` → CH |
| MFA secondary articulation `ʰ` `ʲ` `ʷ` | dropped | `pʰ` → `p`, `kʷ` → `k` |
| any other **combining** mark | dropped | dental `t̪` → `t`, syllabic `n̩` → `n` |

Both diphthong spellings are covered — the `ɪ`/`ʊ`-offglide (`aɪ aʊ eɪ oʊ ɔɪ`,
espeak/Wikipedia) and the `j`/`w`-offglide (`aj aw ej ow ɔj`, MFA/Cartesia) — as
are `ɜ ɝ ɚ` (NURSE, r-coloured) and a few non-colliding SAMPA fallbacks (`@ { 3`).
The IPA→viseme groupings are an articulatory synthesis (like
`visemes.PHONEME_TO_VISEME`); provenance and the symbol inventory's sources are
documented in `src/openfacefx/ipa.py`. Full per-voice MBROLA SAMPA varies by
voice — for a symbol the preset doesn't know (it warns, once per distinct
symbol, and relaxes to silence) supply your own `--mapping` (rows keyed on those
symbols with top-level `"custom_symbols": true`).

IPA vowels also feed the coarticulation dominance model (`is_ipa_vowel`), so a
Piper/Cartesia vowel gets the same broad, jaw-leading bump an ARPABET vowel does
instead of a consonant-sharp one. The ARPABET default path is unchanged.

## Capturing the SDK-event sources

TTS engines run **externally**, exactly like MFA. The GPL tools (espeak-ng,
piper1-gpl) are invoked as separate processes and are **never vendored** into
this MIT project; the cloud SDKs (Azure, Cartesia, Polly) are likewise the
user's own dependency. These ~10-line scripts show how to dump the timing this
tool ingests.

### Azure Speech — viseme events (Python SDK)

The `VisemeReceived` event carries `audio_offset` (ticks) and `viseme_id` — the
exact field names `--format azure` reads.

```python
import json, azure.cognitiveservices.speech as speechsdk

cfg = speechsdk.SpeechConfig(subscription=KEY, region=REGION)
syn = speechsdk.SpeechSynthesizer(speech_config=cfg, audio_config=None)
events = []
syn.viseme_received.connect(
    lambda e: events.append({"audio_offset": e.audio_offset,   # 100-ns ticks
                             "viseme_id": e.viseme_id}))        # 0–21
syn.speak_ssml_async("<speak …>hello world</speak>").get()
json.dump(events, open("visemes.json", "w"))
# → openfacefx from-timing --file visemes.json --format azure -o track.json
```

### espeak-ng — phonemes

Fully offline, no code: emit an MBROLA `.pho` (per-phoneme durations) and parse
it directly.

```bash
espeak-ng -v mb-en1 -q --pho --phonout=voice.pho "hello world"
openfacefx from-timing --file voice.pho --format pho -o track.json
```

For per-phoneme *events* from the C API, subscribe to `espeakEVENT_PHONEME`
(`audio_position` is the start in ms). These are start-only, so feed them
through the schema and let `resolve_ends` infer durations:

```c
/* link: espeak-ng.  cc capture.c -lespeak-ng */
#include <espeak-ng/speak_lib.h>
#include <stdio.h>
#include <string.h>
static int cb(short *wav, int n, espeak_EVENT *ev) {
    for (; ev->type != espeakEVENT_LIST_TERMINATED; ev++)
        if (ev->type == espeakEVENT_PHONEME)      /* start ms, phoneme name */
            printf("%d\t%s\n", ev->audio_position, ev->id.string);
    return 0;
}
int main(void) {
    espeak_Initialize(AUDIO_OUTPUT_SYNCHRONOUS, 0, NULL, 0);
    espeak_SetSynthCallback(cb);
    const char *t = "hello world";
    espeak_Synth(t, strlen(t) + 1, 0, POS_CHARACTER, 0,
                 espeakCHARS_AUTO | espeakPHONEMES, NULL, NULL);
    return espeak_Synchronize();
}
```

```python
# glue the "start_ms<TAB>phoneme" lines above into a track
from openfacefx import TimingEvent, resolve_ends, to_segments, generate_from_alignment, write_json
ev = [TimingEvent("phoneme", sym, int(ms) / 1000.0)
      for ms, sym in (l.split("\t") for l in open("events.tsv") if l.strip())]
write_json(generate_from_alignment(to_segments(resolve_ends(ev))), "track.json")
```

## Library API

```python
from openfacefx import (parse_azure_visemes, resolve_ends,
                        viseme_events_to_segments, build_vendor_mapping,
                        AZURE_VISEME_TO_TARGET, generate_from_alignment)

events = resolve_ends(parse_azure_visemes(open("visemes.json").read()))
segs, warnings = viseme_events_to_segments(events, AZURE_VISEME_TO_TARGET)
for w in warnings:
    print("QA:", w)
track = generate_from_alignment(segs, mapping=build_vendor_mapping(AZURE_VISEME_TO_TARGET))
```
