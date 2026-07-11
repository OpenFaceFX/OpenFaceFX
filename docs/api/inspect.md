# Inspecting & validating assets

Two deterministic, **read-only** commands (issue #47) answer *"what's in this
track?"* and *"is this asset well-formed?"* without opening the previewer — the
CI-friendly home for the format contract `io_export`, `edits`, and `events`
already imply.

## `inspect`

```bash
python -m openfacefx inspect track.json          # human table
python -m openfacefx inspect track.json --json    # schema-stable JSON stats
```

`inspect` prints duration, fps, channel/keyframe counts, per-channel key count /
min / max / start / end / time-coverage, event & variant counts, and the
weight-vs-pose-vs-gesture channel split. `--json` emits a schema-stable object
(`format: openfacefx.inspect`) — **every documented key is always present**, lists
empty rather than absent — so a CI step can assert on it. It reuses
[`qa.summarize`](batch.md) / `qa.cue_flags` for the shared counters, so the
numbers match the generate commands' `--json` output.

## `validate`

```bash
python -m openfacefx validate track.json          # lint; exits nonzero on a violation
python -m openfacefx validate line.edits.json --json     # deterministic problem list
python -m openfacefx validate events.json --strict       # warnings become errors
```

`validate` is a lint gate. It auto-detects the asset kind — a `.track.json`, an
`*.edits.json` sidecar, or a standalone events file — parses it (via
`io_export.from_dict` / the `edits` / `events` validators), checks the contract,
and **exits nonzero** with a deterministic, sorted, machine-readable problem list
(`{severity, code, where, detail}`) so CI diffs stay clean. Checks:

- per-channel key **times** monotonic non-decreasing and within `[0, duration]`;
- **weight** channels (viseme / blendshape / emotion) values in `[0, 1]`; the
  **signed pose** channels (`headYaw/Pitch/Roll`, `eyeYaw/Pitch` — angles, in
  degrees or `[-1, 1]`) flagged only when *wildly* out of range (`> ±360`), not
  for being outside `[0, 1]` — the same weight-vs-angle distinction the CSV
  importer draws;
- `viseme_set` / `target_set` consistent with the channel names;
- event / variant blocks via `events.validate_events`, plus a check that every
  event `type` is a known `EVENT_TYPES` member;
- `--strict` promotes warnings (empty channels, a zero-length track) to errors.

`validate` exits `0` on **every** track the generators and importers produce and
nonzero on a corrupted one (out-of-order times, out-of-range weight, unknown event
type, `viseme_set` mismatch). Library callers get `inspect_track(track)`,
`validate_asset(data)` / `validate_file(path)`, and `detect_kind(data)`. Read-only
(never writes), stdlib only, deterministic across Python 3.9/3.13.

::: openfacefx.inspect

## `diff`

OpenFaceFX ships a hard determinism guarantee; `diff` is the golden-file / snapshot
gate that leverages it (issue #50) — *"did this solver-param / coarticulation /
retarget change actually move the curves, and by how much?"* It is distinct from
its neighbours: `validate` checks a **single** file against the contract, and
`diff-edits` **writes a sidecar for re-application**; `diff` always takes **two**
tracks and **never writes**. A raw `cmp` is too brittle (4-dp time quantisation,
RDP key placement), so it compares *semantically*.

```bash
python -m openfacefx diff golden.track.json candidate.track.json            # exact-match gate
python -m openfacefx diff golden.track.json candidate.track.json --tolerance 0.002
python -m openfacefx diff a.track.json b.track.json --json > drift.json
```

The report gives the duration delta, an `fps` mismatch, per-channel added/removed,
and for shared channels the **max-abs / RMS / mean-abs** value delta on a shared
dense grid (the same `np.interp` resampling `edits.sample` uses) plus
time-coverage and first/last-key drift, and event add/remove/changed. It **exits
`0` when every delta ≤ `--tolerance`** (default `0.0` → exact match) and nonzero
otherwise, emitting a deterministic, sorted problem list (`{channel, metric,
value}`) so CI diffs stay stable; `--json` prints the full schema-stable report,
human mode a worst-first table. The magnitudes are symmetric — `diff(a, b)` and
`diff(b, a)` agree up to sign, and added/removed swap. Library callers get
`diff_tracks(a, b, *, tolerance)`; pure numpy + stdlib, no solver, no RNG, no
writes.

::: openfacefx.trackdiff
