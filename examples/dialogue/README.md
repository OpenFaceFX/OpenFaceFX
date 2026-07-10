# Dialogue-tree batch example

A miniature game-style voice tree showing both alignment paths side by side:

```
dialogue/
  intro/greeting.wav + greeting.txt      naive path (transcript only)
  intro/farewell.wav + farewell.txt      naive path
  quest/oath.wav     + oath.TextGrid     accurate path (MFA alignment)
```

Process the whole tree — the output mirrors the folder structure and a QA
summary reports OOV words, per-file status and worst-first triage:

```bash
python -m openfacefx batch --dir examples/dialogue --out /tmp/tracks --recurse
```

Re-run with `--modified-only` after editing a transcript and only that line
is regenerated. Add `--jobs 8` for parallel processing, `--mapping` /
`--ext csv` to shape the output. (The `.wav` files here are copies of
`examples/voice.wav` — stand-ins for your real recorded lines.)
