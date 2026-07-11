# VO delivery audit

The reconciliation pair to the [loc-table manifest driver](batch.md) (issue #42):
`audit_delivery` compares a **delivered audio folder** against the loc-table the
way a localization vendor's pre-delivery QA pass does — missing lines, orphan
files, naming-convention violations, empty takes, and script↔audio duration
mismatches. It is deterministic set/arithmetic over file stats + the manifest (no
ML), and **read-only** over the delivered folder: it only walks it and reads WAV
headers/samples, never writing there. It shares the #40
[`read_manifest`][openfacefx.batch_manifest.read_manifest] parser and reuses
[`pipeline.wav_duration`](pipeline.md) for stats.

```bash
python -m openfacefx audit --manifest loc.csv --delivered vo/ \
  --duration-tolerance 0.4 --cps 14 --json
```

The report (a superset of the `batch_summary.json` shape — `format`/`version`
self-describing, every list sorted, keyed by loc-ID) itemizes:

| kind | flagged when |
|---|---|
| `missing` | a manifest row's declared audio is absent from the delivery |
| `orphan` | a delivered `.wav` no manifest row references |
| `duration` | actual length outside `±--duration-tolerance` of the `len(text)/--cps` estimate (a take **inside** tolerance is never flagged) |
| `empty` | a zero-duration or near-silent (~0 RMS) take |
| `naming` | a delivered file whose stem doesn't match the loc-ID convention |
| `unreadable` | a file `wav_duration` cannot parse |

plus a **language-coverage matrix** (`{loc-ID: {locale: present}}`) surfacing
per-locale holes. The `audit` command exits **nonzero** when any issue is found (a
CI QA gate) and prints a human worst-first table, or the full JSON report with
`--json`. Audio paths resolve relative to `--delivered` (the delivery root).
Deterministic; stdlib + numpy (RMS only).

::: openfacefx.vo_audit
