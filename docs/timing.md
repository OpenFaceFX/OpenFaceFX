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
| `viseme` | `azure`, `polly` | skips phoneme→target mapping → vendor remap preset → coarticulation |

```bash
openfacefx from-timing --file voice.pho    --format pho      -o track.json
openfacefx from-timing --file align.json   --format piper --sample-rate 22050 -o track.json
openfacefx from-timing --file marks.json   --format cartesia -o track.json
openfacefx from-timing --file visemes.json --format azure    -o track.json
openfacefx from-timing --file voice.marks  --format polly     -o track.json --retarget arkit
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

`pho`, `piper` and `cartesia` symbols are the source's own alphabet (MBROLA
SAMPA, and IPA for Piper/Cartesia) — **not** ARPABET. The default mapping expects
ARPABET, so pass a matching `--mapping` (rows keyed on those symbols, built with
`allow_custom_symbols`) for meaningful visemes from IPA/SAMPA sources. The
viseme formats need no mapping: `AZURE_VISEME_TO_TARGET` (22 IDs) and
`POLLY_VISEME_TO_TARGET` remap straight onto the Oculus-15 targets. Symbols
outside those tables produce a QA warning and relax to silence — never a crash.

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
