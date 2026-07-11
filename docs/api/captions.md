# Subtitles & captions (SRT / WebVTT)

Captions and lip motion should come from **one source of truth** so they stay in
sync (issue #41). OpenFaceFX already *ingests* word/segment timings
([`anchors.parse_srt`](timing.md), the Azure / ElevenLabs word boundaries); this
is the matching *output*. It is deterministic string formatting over the timing
arrays the pipeline already produces — **not** a new alignment:
[`word_timings`][openfacefx.export_captions.word_timings] pulls per-word spans
from `texttags.naive_word_segments`, whose phoneme segments are byte-identical to
the `pipeline.naive_segments` the viseme curves are reduced from, so the words the
captions carry are timed by the very segments that drove the mouth.

```bash
# a standalone subtitle file, timed like the lip-sync would be
python -m openfacefx captions --text "Well met, traveler." --wav vo.wav -o vo.srt
python -m openfacefx captions --text "Well met, traveler." --duration 2.0 -o vo.vtt --karaoke

# or co-generate the track and its captions from one run
python -m openfacefx naive --text "Well met." --wav vo.wav -o vo.track.json --emit-captions vo.srt

# and at dialogue scale, a caption sidecar next to every naive-mode track
python -m openfacefx batch --manifest loc.csv --out tracks/ --captions srt
```

The pipeline is three deterministic steps:

- [`word_timings`][openfacefx.export_captions.word_timings] → per-word
  `(token, start, end)` (punctuation kept for sentence detection; a hyphenated
  token spans its parts);
- [`build_cues`][openfacefx.export_captions.build_cues] groups words into cues —
  greedily packed under a **max-line-length × max-lines wrap budget** (no cue
  exceeds it), broken at sentence-ending punctuation and audible gaps, each
  cue's duration extended toward a **reading-speed (characters-per-second)**
  minimum where the timeline allows and clamped so cues stay **monotonic and
  non-overlapping**;
- [`srt_text`][openfacefx.export_captions.srt_text] /
  [`vtt_text`][openfacefx.export_captions.vtt_text] serialise to SubRip
  (`HH:MM:SS,mmm`) and WebVTT (`HH:MM:SS.mmm` under the `WEBVTT` header), with
  optional word-level **karaoke** (`--karaoke`, WebVTT `<c>` spans + inline cue
  timestamps that fall inside their cue span).

`srt_text` is the exact inverse of `anchors.parse_srt`:
`parse_srt(srt_text(cues))` recovers the same cue spans (within millisecond
rounding). Output is LF-terminated UTF-8, pure stdlib.

::: openfacefx.export_captions
