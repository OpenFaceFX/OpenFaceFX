# Transcript text tags

Direct animation from the script (issue #7). Inline tags in the transcript are
extracted before grapheme-to-phoneme conversion, the clean spoken text is
lip-synced as usual, and each tag is mapped onto the timeline the aligner
produced: curve tags become channels, event tags land on the following word,
`[emphasis]` locally raises articulation, and `<T>` / `[pause]` chunk or pad the
timeline with silence. The syntax is modelled on the FaceFX
[text-tagging](https://facefx.github.io/documentation/doc/text-tagging)
documentation and, for `[emphasis]`/`[pause]`, on SSML. Enable it with the
`naive --tags` flag or `generate_naive(..., parse_tags=True)`; a tagless
transcript is byte-identical to the plain path.

::: openfacefx.texttags
