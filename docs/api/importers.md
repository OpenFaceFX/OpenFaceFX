# Importing mouth-cue files

OpenFaceFX can *write* stepped mouth-cue files for the indie 2D ecosystem
(Rhubarb, Papagayo, Moho — see [Exporters](exporters.md)); this module reads them
back, so a studio sitting on a Rhubarb/Papagayo library or a hand-timed Moho
mouth track can bring it into OpenFaceFX to coarticulate, retarget, layer
gestures/events, condition and re-export (issue #44). Because the project owns
both halves, each parser is the *verified inverse* of shipping code in
[`export_cues`](exporters.md).

Import a cue file with the `from-cues` command; the format is auto-detected by
extension and first line:

```bash
python -m openfacefx from-cues mouth.tsv  -o track.json      # Rhubarb TSV
python -m openfacefx from-cues mouth.xml  -o track.anim      # Rhubarb XML -> Unity
python -m openfacefx from-cues mouth.dat  --fps 24 -o track.json   # Moho / OpenToonz
python -m openfacefx from-cues mouth.pgo  --coarticulate -o track.json  # Papagayo
```

The result is an ordinary stepped [`FaceTrack`](visemes.md) — one `[0, 1]`
channel per viseme, `sil` in the gaps — that flows unchanged through every track
exporter and `--retarget`. `--coarticulate` re-solves the hard steps through the
existing dominance blend to smooth them.

## Formats & frames

| Format | Extension | Time base | Shape vocabulary |
|--------|-----------|-----------|------------------|
| Rhubarb TSV / XML / JSON | `.tsv` / `.xml` / `.json` | seconds (`%.2f`) | Rhubarb A–H/X |
| Moho / OpenToonz | `.dat` | 1-based frames (default 24 fps) | Preston-Blair, or Rhubarb A–H/X |
| Papagayo-NG | `.pgo` | 1-based frames (fps in file) | Preston-Blair |

Rhubarb files are seconds-based and reconstructed on a 100 fps grid (hundredths
land exactly on frames). The rate-less Moho `.dat` defaults to 24 fps (override
with `--fps`); Papagayo `.pgo` carries its own rate. Frame decoding inverts
`export_cues._frame_at` / `_to_frames`: `seconds = (frame - 1) / fps`.

## Shape → viseme

Shape IDs map back to viseme channels through :data:`RHUBARB_TO_VISEME` and
:data:`PRESTON_BLAIR_TO_VISEME`, which are **derived from the forward retarget
presets** (`retarget.PRESETS`) so they can never drift out of sync. When several
visemes collapse onto one shape (e.g. many consonants → Rhubarb `B`), the inverse
picks the first viseme in canonical `VISEMES` order — a representative that, by
construction, retargets straight back to the same shape, so a stepped track
re-exports to the identical cue file.

The extended shapes `G` (f/v), `H` (tongue-up) and `X` (idle) invert *directly*
to `FF` / `nn` / `sil` for full fidelity. A shape the inverse table does not know
is routed through the documented `RHUBARB_EXTENDED_FALLBACK` (`G→A`, `H→C`,
`X→A`) and **reported**; if it still cannot be resolved it is a clear error —
never silently dropped.

## Round-trip guarantee

Each parser is tested against its writer:

- **Rhubarb TSV / XML / JSON** round-trip **byte-identically**
  (`write → import → write` reproduces the file).
- **Moho `.dat`** and **Papagayo `.pgo`** reach a byte-exact **idempotent fixed
  point** and preserve the **collapsed (shape, frame-boundary) cue sequence**
  exactly. A byte difference on the first pass can only be a *redundant duplicate
  switch* the writer's frame quantisation emits when it drops an intermediate run
  that collides at the lower rate — it holds the same mouth, carries no
  animation, and `dominant_cues` (which merges equal adjacent shapes) structurally
  cannot re-emit it, so the importer collapses it into the canonical run.

The imported track validates through `io_export.from_dict`/`to_dict` and
re-exports through Unity / Godot / Live2D / cues / CSV / JSON unchanged. (`.lip`
is phoneme-based, not track-based, and is outside the cue-import path — cues carry
visemes, not phonemes.)

Deterministic; stdlib + numpy only (`xml.etree`, `json`, `re`, string splitting).
A purely additive command and module: no existing command's output changes.

## Blendshape-weight CSV (ARKit / Live Link Face)

A lot of face animation lives as per-frame **blendshape-weight CSV** — Apple
ARKit's 52 coefficients recorded by Epic's Live Link Face app, or exported by
capture tools and DCCs. `from-csv` reads two layouts, auto-detected from the
header row:

```bash
python -m openfacefx from-csv track.csv -o out.json                 # OpenFaceFX long
python -m openfacefx from-csv capture.csv --fps 60 -o out.anim       # wide, Live Link Face
python -m openfacefx from-csv capture.csv --timecode-col Timecode -o out.json
```

- **OpenFaceFX long CSV** `time,channel,value` — the exact inverse of
  [`io_export.write_csv`](exporters.md); a byte-clean round-trip
  (`read_csv(write_csv(track))` reconstructs the channels and keyframes).
- **Wide per-frame CSV** — one row per frame, one column per blendshape name,
  with an optional leading `Timecode` / `BlendShapeCount` header as Live Link
  Face emits. A `Timecode` column (SMPTE `HH:MM:SS:FF`, sized by `--fps`) or the
  row index (`i / fps`) drives the timeline, and each column is RDP-thinned via
  `reduce_to_track` into sparse keys.

Channel names are kept **verbatim in rig space** (`jawOpen`, `mouthSmileLeft`, …)
and values are clamped to `[0, 1]` — a column carrying out-of-range values (e.g. a
non-blendshape head-rotation angle) is clamped and **reported**. Because the
forward viseme→ARKit map is many-to-one, this deliberately does **not** recover
visemes: it brings the raw rig-space channels in so they can be conditioned
(`--smooth`/`--lag`), layered and re-exported through Unity / Godot / Live2D. The
`from-csv` command is kept separate from `from-cues` — blendshape weights and
mouth-shape cues are different sources. Library callers get `read_csv(path, *,
fps, timecode_col)`. numpy + stdlib (`csv`), deterministic across Python
3.9/3.13.

::: openfacefx.importers

::: openfacefx.importers_csv
