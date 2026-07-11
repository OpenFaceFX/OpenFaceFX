# SSML input

Drive lip-sync from the **same SSML you feed your TTS** (issue #52). Game and VO
pipelines already author their TTS input as [SSML](https://www.w3.org/TR/speech-synthesis11/)
— the W3C markup Azure, Google and Amazon Polly consume — and OpenFaceFX's
[text tags](texttags.md) were modelled on it. `parse_ssml` is a **thin front-end
over those #7 tags, not a new animation path**: it parses an SSML document with
the stdlib `xml.etree.ElementTree` and returns the *same* `(clean_text, tags)`
that `parse_tagged_transcript` yields, so the unchanged naive pipeline lip-syncs
it identically — breaks land as pauses, emphasis as stronger articulation, marks
as events.

Enable it with the `naive --ssml` flag, or just pass a document with a `<speak>`
root (auto-detected):

```bash
python -m openfacefx naive --ssml --duration 3 -o out.track.json \
  --text '<speak>Say <emphasis level="strong">brave</emphasis> <break time="300ms"/> new world <mark name="beat"/></speak>'
```

Each construct maps onto an existing text-tag primitive — nothing new is invented:

| SSML | Maps to | Notes |
|---|---|---|
| `<break time="500ms"/>` / `strength` | `[pause:S]` | `time` (ms/s) wins over `strength`; the `strength` table defaults a bare `<break/>` to `medium` (0.5 s) |
| `<emphasis level="strong">…</emphasis>` | `[emphasis strength=..]` | `level` → dominance strength (`strong` 1.0, `moderate` 0.5, `reduced`/`none` 0); a level-less `<emphasis>` uses the #7 default |
| `<sub alias="World Health Organization">WHO</sub>` | substituted text | the spoken `alias` replaces the written form for G2P |
| `<mark name="hit"/>` | `[mark name=hit]` | a named `phrase` marker event |
| `<p>` / `<s>` | `[phrase]` | a phrase boundary at the element start |
| `<say-as interpret-as=…>…</say-as>` | `qa.normalize_transcript` | the enclosed text is folded to ASCII (the #23 pass) |

**Deferred / out of scope:** `<phoneme alphabet="ipa" ph="…">` pronunciation
override belongs to the multi-language framework (#8) + `ipa.py` — its text is
passed through unchanged here, the `ph` ignored. Any **unknown element degrades
to its text content** and never crashes; **malformed XML raises a clear
`ValueError`** at the boundary (a `DOCTYPE` is rejected outright — SSML has none,
and it can smuggle entity-expansion).

Because it produces the same `(clean_text, tags)` pair as the bracket front-end,
an SSML document is **byte-identical** to the equivalent tagged transcript
through the whole pipeline, and a `<speak>` with **no constructs** is
byte-identical to plain `naive --text`. The parse is deterministic and
stdlib-only (`xml.etree` / `re`); numpy is never imported. Library callers get
`parse_ssml`, `looks_like_ssml`, and the extensible `EMPHASIS_STRENGTH` /
`BREAK_STRENGTH` tables.

::: openfacefx.ssml
