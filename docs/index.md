# OpenFaceFX

**Open-source lip-sync in the spirit of FaceFX** — turn a voice recording and its
transcript into viseme animation curves that drive any character's face.
MIT-licensed, numpy-only, no proprietary formats.

[Live demo](https://openfacefx.github.io/OpenFaceFX/demo/){ .md-button .md-button--primary }
[Quickstart](#quickstart){ .md-button }
[API reference](api/index.md){ .md-button }

---

## Quickstart

No models, no downloads — approximate lip-sync from text plus a WAV's duration:

```bash
git clone https://github.com/OpenFaceFX/OpenFaceFX && cd OpenFaceFX
pip install -e .        # numpy is the only runtime dependency
python -m openfacefx naive --text "hello world" --wav examples/voice.wav -o track.json
```

```
wrote track.json: 7 channels, 93 keyframes, 1.60s
```

`track.json` is the `openfacefx.track` format: sparse `[time, value]` keyframes
per viseme channel, with weights in `[0, 1]`. Point the
[live previewer](https://openfacefx.github.io/OpenFaceFX/demo/) at it, or feed it
straight into an engine exporter.

From Python, the same thing is two calls:

```python
from openfacefx import generate_naive, write_json

track = generate_naive("the quick brown fox", duration=1.8)
write_json(track, "track.json")
```

For production accuracy, swap the naive aligner for a real one — parse a
Montreal Forced Aligner result with `load_mfa_textgrid` and call
`generate_from_alignment`. See the [API reference](api/index.md).

## How it works

FaceFX-style tooling is really four subsystems chained together. Only the first
(acoustic alignment) needs a heavy model — and excellent open-source aligners
already exist — so OpenFaceFX **wraps** the aligner and fully owns the other
three stages:

1. **Alignment** — time-stamped phonemes from Montreal Forced Aligner (parser
   included), or a dependency-free naive aligner for instant prototyping.
2. **Phoneme → viseme** — the widely-adopted Oculus/Meta 15-viseme convention.
3. **Coarticulation** — Cohen–Massaro dominance blending, so mouth shapes flow
   into each other instead of snapping.
4. **Keyframe reduction** — Ramer–Douglas–Peucker thinning into sparse,
   engine-friendly curves.

Every seam is a tiny data contract (`PhonemeSegment` in, `FaceTrack` out), so any
stage can be swapped without touching the rest.

## Explore

<div class="grid cards" markdown>

-   __Guides__

    Retarget the 15 visemes onto ARKit / VRM / Rhubarb / CC4 rigs, ingest TTS
    timing without an aligner, and check engine & tool compatibility.

    [Retargeting](retargeting.md) · [TTS timing](timing.md) · [Compatibility](COMPATIBILITY.md)

-   __API reference__

    Every public module — the pipeline, viseme model, coarticulation solver,
    retargeting, and all exporters — generated from docstrings.

    [Browse the API →](api/index.md)

-   __Live demo__

    A self-contained previewer that animates a schematic mouth and plots every
    viseme channel, regenerated from the current pipeline on every push.

    [Open the demo →](https://openfacefx.github.io/OpenFaceFX/demo/)

-   __Changelog__

    Release notes and the current backlog.

    [What's changed →](changelog.md)

</div>

!!! note "Scope & honesty"

    This is a working foundation, not a finished product: the full
    phoneme→viseme→curve→export chain with a clean seam where a real acoustic
    aligner plugs in. It does **not** read or write proprietary FaceFX binary
    formats (`.facefx`, `.fxa`, `.fxe`, `.ffxc`). *FaceFX® is a registered
    trademark of OC3 Entertainment, Inc.; OpenFaceFX is an independent project.*
