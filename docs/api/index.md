# API reference

OpenFaceFX is a small, numpy-only library. Everything the CLI does is available
as plain functions and dataclasses you can call directly — the top-level package
re-exports the public surface:

```python
from openfacefx import (
    generate_naive,            # text + duration       -> FaceTrack  (no models)
    generate_from_alignment,   # time-stamped phonemes -> FaceTrack  (accurate)
    load_mfa_textgrid,         # Montreal Forced Aligner .TextGrid -> segments
    write_json, write_csv,     # serialise a FaceTrack
    write_unity_anim,          # engine exporters (Unity / Live2D / Godot / ...)
    retarget,                  # remap the 15 visemes onto another rig
)
```

Every stage is a tiny data contract — `PhonemeSegment` in, `FaceTrack` out — so
any module can be swapped without touching the rest.

## Modules

| Area | Modules |
|------|---------|
| [Pipeline & generation](pipeline.md) | `pipeline`, `alignment`, `g2p`, `phonemes`, `energy` |
| [Visemes, coarticulation & curves](visemes.md) | `visemes`, `coarticulation`, `curves`, `mapping`, `ipa` |
| [Timing & anchors](timing.md) | `timing`, `anchors` |
| [Retargeting](retarget.md) | `retarget` |
| [Exporters](exporters.md) | `io_export`, `export_unity`, `export_live2d`, `export_godot`, `export_cues`, `export_lip`, `bethesda` |
| [Batch & CLI](batch.md) | `batch`, `cli` |

Pages are generated from the source docstrings with
[mkdocstrings](https://mkdocstrings.github.io/), so they never drift from the
code.
