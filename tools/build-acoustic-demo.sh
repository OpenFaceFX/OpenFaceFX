#!/usr/bin/env bash
#
# build-acoustic-demo.sh — build the acoustic-adapter demo from a REAL phoneme
# recognizer run, with NO transcript. This is the honest end-to-end proof:
#
#     speech audio ──(Allosaurus, external ML)──▶ phones+timings ──▶ openfacefx ──▶ face
#
# OpenFaceFX never sees a transcript and never does the recognition itself — an
# acoustic recognizer (here Allosaurus) hears the phones straight from the audio,
# and the numpy-only adapter turns them into the animation you see and hear.
#
# Usage:  bash tools/build-acoustic-demo.sh [clip.wav]
#         (defaults to examples/speech.wav — a real 3.27s speech sample)
#
# Requires Python 3.9+. Installs `allosaurus` on first run if it is missing
# (it pulls in torch, ~1 GB — that is the ML that must run outside openfacefx).
set -euo pipefail
cd "$(dirname "$0")/.."

CLIP="${1:-examples/speech.wav}"
PHONES="examples/speech.allosaurus.txt"
OUT="examples/acoustic-demo.html"

[ -f "$CLIP" ] || { echo "error: no such clip: $CLIP" >&2; exit 1; }

# Use python3 if present (bare `python` is absent on many Macs). Allosaurus and
# openfacefx must live in whatever interpreter this resolves to.
PY=python3; command -v python3 >/dev/null 2>&1 || PY=python

# openfacefx is numpy-only; run the installed package, or fall back to ./src.
if "$PY" -c "import openfacefx" 2>/dev/null; then OFX=("$PY" -m openfacefx)
else OFX=(env PYTHONPATH=src "$PY" -m openfacefx); fi

# 1. RECOGNIZE — phones + timings straight from the audio, no transcript.
if ! "$PY" -c "import allosaurus" 2>/dev/null; then
  echo "==> allosaurus not found; installing it (one-time, pulls in torch ~1GB)…"
  "$PY" -m pip install allosaurus
fi
echo "==> recognizing phones from $CLIP with Allosaurus (no transcript)…"
"$PY" -m allosaurus.run --timestamp true -i "$CLIP" > "$PHONES"
# (Python-API equivalent if the CLI differs in your version:
#   $PY -c "from allosaurus.app import read_recognizer as r; \
#           print(r().recognize('$CLIP', timestamp=True))" > "$PHONES" )
lines=$(grep -cve '^[[:space:]]*$' "$PHONES" || true)
[ "$lines" -gt 0 ] || { echo "error: allosaurus produced no phones for $CLIP" >&2; exit 1; }
echo "    -> $PHONES ($lines phones). First few:"; head -4 "$PHONES" | sed 's/^/       /'

# 2. ADAPT — recognizer output -> viseme track (numpy-only, deterministic).
"${OFX[@]}" naive --anchors "$PHONES" --anchors-format allosaurus \
  -o /tmp/acoustic_track.json --emit-segments /tmp/acoustic_segments.json

# 3. RENDER — self-contained previewer: the real audio + the phoneme lane.
"$PY" tools/build_preview.py /tmp/acoustic_track.json "$OUT" \
  --wav "$CLIP" --segments /tmp/acoustic_segments.json

echo
echo "==> built $OUT from a real recognizer run. Open it:"
echo "       open $OUT"
echo "    Commit $PHONES and $OUT to ship it (the site rebuilds the demo"
echo "    from $PHONES on deploy — no ML needed in CI)."
