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
| Colour contrast | every visible text node vs its resolved background, both themes (WCAG 1.4.3) |
| Focus visibility | real Tab presses — every stop shows a visible indicator (WCAG 2.4.7) |
| Status messages | a live region exists and Generate is announced through it (WCAG 4.1.3) |
| Header, rails, transport | the ~30 controls *outside* the tab panels are named and ≥24×24; the playhead slider speaks time |
| Menu button | the ⋯ menu: ARIA menu pattern, Enter opens + focuses, Arrow/End move, Escape closes + restores focus |
| Modal dialog | Account & projects: `role=dialog`/`aria-modal`/labelled, focus moves in, Tab and Shift+Tab trapped, Escape restores focus, password `<label>` |
| Idle rendering | the 3D head draws **zero** frames while idle, never while its canvas is hidden, and one when shown again |
| Reflow | no horizontal scroll at 1024; every control still reachable at 768 and 1024 (200 % zoom on a laptop) |
| Button sweep | every visible, non-destructive button on every tab is clicked — no console error may follow |
| Console | zero errors across the whole session |

## Bugs it has already caught

- **The 3D preview rendered at 60 fps forever** — on every tab, canvas hidden or
  not, nothing moving. Now on-demand (0 idle frames).
- **The header and transport pushed controls off-screen below ~900 px** (tablet,
  or 200 % zoom on a laptop) — and the page is `overflow:hidden`, so account,
  theme and fps were unreachable, not merely scrolled away.
- The playhead slider had no accessible name (it lives in the transport, which
  the per-tab audit never saw) and announced 0–1000 instead of time.
- The ⋯ actor/take menu had no ARIA menu semantics and no keyboard way to close it.
- The Account & projects "modal" was a `<div>`: no `role=dialog`, focus never moved
  in, Tab escaped to the page behind (24 of 30 presses), and closing dropped focus
  on `<body>`. The password field had no `<label>`.
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
