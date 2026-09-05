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

The account flow (register → save project → reload → open → delete → sign out)
writes a user to the server's database, so it is opt-in: start the server on a
throwaway DB and set `OFFX_E2E_ACCOUNTS=1`:

```bash
OFFX_STUDIO_DB=/tmp/offx-e2e.db openfacefx studio --port 8801 --no-open &
OFFX_E2E_ACCOUNTS=1 node tests/e2e/studio.e2e.mjs
```

Uses `channel:"chrome"`, so it drives the Chrome you already have — no
browser download. **Not wired into CI**: it needs a browser and a live server.
Run it before releasing anything that touches `studio_web/`.

### The browser (Pyodide) runtime, and the live site

`openfacefx studio` always serves the **native** runtime. The live site runs the
pipeline **in the browser** (Pyodide + the published wheel), so drive that too —
serve the folder statically and point the suite at it, or at the deploy itself:

```bash
(cd src/openfacefx/studio_web && python3 -m http.server 8803 --bind 127.0.0.1 &)
OFFX_URL=http://127.0.0.1:8803/ node tests/e2e/studio.e2e.mjs      # boots Pyodide
OFFX_URL=https://openfacefx.com/studio/ node tests/e2e/studio.e2e.mjs   # post-deploy smoke
```

The suite reports which runtime it found and adapts: the boot wait allows the
wheel download, and on a static host that isn't the Pages deploy the two backend
probes (`/api/health`, `/api/auth/me`) are allowed to 404.

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
| Keyboard operability | channel list + Workspace rail are roving listboxes; on the Curves canvas ← → select, Shift+← → and ↑ ↓ nudge, Enter adds, Delete removes, Ctrl+Z restores, edits are announced; phoneme boundary moves a frame and re-solves; Face Graph node, Event jump and pose pad from the arrows (WCAG 2.1.1) |
| Workflows | every exporter yields a real, non-empty download (`.fuz` refuses without audio — as a notice); a real WAV → spectrogram, energy engine, mute button, playback that advances; Import track… and Align from… words add takes; Batch makes one take per line; Run QA flags an OOV word and the pronunciation editor clears it; duplicate / inline-rename / delete-with-confirmation; a broken import is a notice and Generate still works; an empty transcript explains its silent take; Generate voice drives the spectrogram; a 720-word generate shows its busy state and keeps the page painting (longest frame gap under 250 ms — Pyodide runs in a worker) |
| Reload | the theme choice and the whole session survive a reload; Start a fresh workspace… clears it |
| Accounts (opt-in) | register → Save new → reload keeps the cookie session → Open restores the take → Delete → Sign out |
| Console | zero errors across the whole session (headless Chrome's "no audio device" renderer error is filtered) |

## Bugs it has already caught

- **A reload lost every unsaved take**; the theme choice was forgotten too. Now
  `session.js` autosaves/restores the workspace and remembers the theme.
- **Delete take / Delete actor asked nothing** — one click, gone, no undo.
- **The playhead froze whenever the audio clock did** (suspended or erroring
  `AudioContext`): audio is the transport's clock and nothing fell back.
- **Thirteen `alert()` dialogs** froze the app for results and errors; they are
  notices now.
- **Every canvas editor was pointer-only** (WCAG 2.1.1): no key could select or move a
  curve key, re-time a phoneme, pick a graph node or turn the head. Now `keyboard.js`.
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
