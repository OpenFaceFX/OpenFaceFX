# Studio end-to-end / UX tests

Drives the **real** Studio in a real Chrome against a running native server.
These catch what the Python suite structurally cannot: markup that never
renders, controls that don't respond, canvases that paint nothing, and
accessibility defects that only exist in the composed page.

```bash
openfacefx studio --port 8801 --no-open &     # or: python -m openfacefx.cli studio …
cd tests/e2e && npm install                   # once — playwright-core only
node tests/e2e/studio.e2e.mjs                 # from the repo root
```

Options: `OFFX_URL=…` to point elsewhere, `--headed` to watch it,
`--json report.json` to save the full report for diffing between runs.

Uses `channel:"chrome"`, so it drives the Chrome you already have — no
browser download. **Not wired into CI**: it needs a browser and a live server.
Run it before releasing anything that touches `studio_web/`.

## What it covers

| Group | Checks |
|---|---|
| Boot | becomes interactive, title, **native runtime detected** |
| Document structure | `lang`, single `<h1>`, `<main>`, viewport, tab/panel/`aria-selected`/`aria-controls` parity |
| Keyboard | WAI-ARIA tabs pattern — Arrow/Home/End, roving tabindex |
| Per tab (×9) | panel renders · every control has an accessible name · every canvas named or marked decorative · no target under 24×24 (WCAG 2.5.8) |
| Primary workflow | type a transcript → Generate → a track with channels and keyframes |
| Canvases | sample pixels to prove Curves / Phonemes / Face Graph actually paint |
| Console | zero errors across the whole session |

## Bugs it has already caught

- **The desktop Studio silently demoted itself to the browser runtime.** Native
  detection raced a `fetch("/api/health")` against a 600 ms timeout; the server
  answers in <1 ms, but that first request queues behind the page's own scripts
  (~700 ms measured), so a *fresh load of the desktop app* fell back to Pyodide
  and disabled Piper. The server now stamps `data-offx-native` on served HTML,
  so detection involves no timing at all.
- 80 unlabelled inputs on the Mapping tab (every weight/target field).
- `role="tablist"` with no `aria-selected`, no `tabpanel`s, and no arrow-key
  navigation — the pattern was claimed but not implemented.
- No `<h1>` anywhere in the document.
- Eleven unnamed `<canvas>` elements — the Studio's entire visual content.
- Six controls below the WCAG 2.2 minimum target size.
