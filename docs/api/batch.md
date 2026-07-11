# Batch & CLI

Batch-process a whole directory of voice clips with an OOV/confidence QA report
and incremental re-runs, and the `openfacefx` command-line entry point that wires
every stage and exporter together.

::: openfacefx.batch

## Loc-table manifests (`--manifest`)

The alternative to the directory walk (issue #40): drive the batch from a
**localization string table** — a CSV/TSV keyed by loc-ID, one row per line
(`audio`, `text`, `language`, `character`, optional `mapping`/`style`/`out`) —
the way Unity / Godot / Unreal export String Table Collections and FaceFX keys VO
to an *entrytag*. `read_manifest` parses the table with stdlib `csv`
(header-matched forgivingly against [`COLUMN_ALIASES`][openfacefx.batch_manifest.COLUMN_ALIASES]),
and `manifest_jobs` turns the rows into the same jobs the directory walk feeds
`batch._process_one`, so every row runs through the unchanged pipeline, writers,
summary, NDJSON stream and ledger. A missing / unreadable / malformed row is an
isolated per-row failure; with `--manifest` absent the directory-walk output is
byte-identical.

::: openfacefx.batch_manifest

::: openfacefx.cli
