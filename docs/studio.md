# OpenFaceFX Studio

An open, web-based facial-animation & lip-sync studio — the FaceFX Studio workflow,
rebuilt on the OpenFaceFX pipeline, running in your browser. Preview, Curves,
Phonemes, a Face Graph, every exporter, and an AI assistant with bring-your-own-key
LLMs behind zero-knowledge encryption.

```
openfacefx studio        # launch it locally (native pipeline, opens your browser)
```

---

## Why this design

[FaceFX Studio](https://facefx.github.io/documentation/) (OC3 Entertainment →
acquired by Speech Graphics, 2025) is the tool behind facial animation in 150+ AAA
titles (Halo, Fallout, GTA V, Baldur's Gate 3). Its workflow is a set of tabbed
views over one data model: **audio + text → phonemes → coarticulated curves →
a face-graph retarget → export**. OpenFaceFX already implements that entire engine
as a pure-Python, numpy-only library. **The Studio is the missing GUI** — and
because the engine is small and dependency-light, it runs *in the browser* via
Pyodide, so the whole studio is a static web app that also packages as a desktop
tool and a SaaS.

Each FaceFX view maps onto code that already exists:

| FaceFX Studio view | OpenFaceFX Studio | Backed by |
|---|---|---|
| Preview (3D + sliders) | **Preview** — an ARKit-blendshape **3D head** (three.js) driven by the take; schematic 2D fallback when offline | `retarget` (arkit), `gestures` |
| Phoneme editor (waveform + phoneme/word bar) | **Phonemes** — waveform + aligned phoneme strip | `alignment`, `pipeline`, `energy` |
| Curve editor (offset curves) | **Curves** — coarticulated viseme & gesture curves | `coarticulation`, `curves` |
| Face Graph (nodes + link functions) | **Face Graph** — viseme inputs → rig outputs via links | `mapping`, `retarget`, `links` (#68) |
| Mapping (phoneme→weighted targets, Basic/Tongue/Jaw) | **Mapping** — editable phoneme→viseme weight table; apply-on-Generate + download a canonical `openfacefx.mapping` | `mapping` (`Mapping.default`/`from_json`/`to_json`) |
| Events / curve-attached notifies | **Events** — auto-authored emphasis/phrase event layer on a timeline; rides in the track JSON → exports as engine notifies | `events`, `pipeline.derive_events` |
| Analysis Actor (blinks, brows, head) | Generate options (gestures, breath) | `gestures`, `prosody` |
| Export / Publish | **Export** — every engine/DCC target | `export_*`, `importers_*` |
| Python console / commands | (roadmap: scripting console) | `cli`, the Python API |
| — new — | **Assistant** — LLM help, BYO-key | see below |

---

## Architecture — one frontend, three runtimes

The frontend (`src/openfacefx/studio_web/`: `index.html`, `studio.css`, `studio.js`,
`assistant.js`) is a dependency-free SPA. It talks to a **Pipeline** abstraction
that resolves to whichever runtime is present:

```
                     ┌────────────────────────── studio_web/ (one SPA) ──────────────────────────┐
                     │  Preview · Phonemes · Curves · Events · Face Graph · Mapping · Export · Assistant │
                     └───────────────┬───────────────────────────────────────────┬───────────────┘
                                     │  Pipe.generate / export / presets           │  callLLM
             ┌───────────────────────┴───────────────┐                 ┌───────────┴───────────┐
   (A) WEB / SaaS client          (B) STANDALONE / SaaS backend        BYO-key LLM providers
   Pyodide: the openfacefx        `openfacefx studio` (studio.py):     Anthropic (direct) · Ollama
   wheel + numpy run in the       stdlib http server, NATIVE           (direct) · OpenAI/Gemini
   browser (zero install)         pipeline + /api/llm relay            (via the /api/llm relay)
```

- **(A) Web / SaaS front-of-house** — host `studio_web/` on any static host. The
  pipeline runs **entirely client-side** via Pyodide (CPython+numpy→WASM), which
  `micropip install openfacefx`s the real wheel. Nothing is uploaded. This is the
  zero-install "try it" surface and the SaaS client.
- **(B) Standalone desktop** — `openfacefx studio` (module `openfacefx.studio`)
  serves the same SPA against the **native** pipeline over a tiny stdlib HTTP
  API (`/api/health|generate|export|presets|preset|llm`, plus
  `/api/auth|projects|vault` for accounts & storage). Faster, offline, no Pyodide
  download. Wrap in [Tauri](https://tauri.app/)/Electron for a signed desktop
  binary (the web root is already self-contained).
- **(SaaS)** — the same server **is** the multi-tenant backend: `studio_saas.py`
  adds accounts, per-user project storage, and ciphertext-only vault sync (see
  "Accounts, projects & multi-tenant SaaS" below); `/api/llm` is the stateless
  provider relay.

The SPA auto-detects: on load it probes `/api/health`; present → **native**,
absent → **browser/Pyodide**. Same UI, same results.

---

## LLM integration — where it helps, and how

LLMs are wired into the **Assistant** tab and target the specific places a
lip-sync tool benefits (structured JSON output, ranges clamped client-side, and a
deterministic fallback kept behind the model):

| Assist action | What the LLM does | Feeds |
|---|---|---|
| **Clean transcript** | Normalize casing/punctuation, expand numbers & abbreviations for TTS/G2P | the transcript → pipeline |
| **Pronounce OOV** | Grapheme→ARPAbet for names/brands CMUdict misses | `emit-oov-dict` / `--cmudict` (#66) |
| **Direct emotion** | Script line → valence/arousal/emotion/intensity | the emotion layer (#38/#67) |
| **Direct the performance** | Free-form notes → talking style, gestures, emphasis | generate options |

Further points identified for the roadmap: co-speech **gesture/blink direction**
from text (→ event layer #6), **natural-language curve editing** ("less exaggerated
on 'hello'" → validated edit ops), **QA** (flag implausible viseme runs), and
**dialogue generation**.

**Providers.** Two client adapters cover everything:

- **Anthropic-shaped** — Claude Messages API. Works **direct from the browser**
  with `anthropic-dangerous-direct-browser-access: true`.
- **OpenAI-shaped** — covers OpenAI, Google Gemini (OpenAI-compat endpoint), and
  local **Ollama / vLLM / LM Studio** (swap base URL). Local + Gemini-simple call
  direct; **OpenAI/Gemini are browser-CORS-blocked, so they route through the
  stateless `/api/llm` relay** when the studio runs under `openfacefx studio`.

Open-source models are first-class: point the "OpenAI-compatible" provider at
`http://localhost:11434/v1` (Ollama) or a vLLM/LM Studio endpoint and bring any
Llama/Mistral/Qwen — no key, nothing leaves your machine.

---

## Bring-your-own-key — zero-knowledge encryption

Provider API keys are encrypted **in the browser** with a master password, using
the same model as LastPass/Bitwarden (client-side KDF; server sees only
ciphertext). Implemented in `assistant.js` with the Web Crypto API:

```
master password ─PBKDF2-SHA256(600,000 iters, random 16-byte salt)─▶ 256-bit AES-GCM
                                                                       vault key
                                                          (non-extractable, in memory only)
each API key ──AES-256-GCM(fresh random 96-bit IV)──▶ { iv, ciphertext(+128-bit tag) }

stored (localStorage now; SaaS syncs the same blob):
  { v, kdf:"PBKDF2-SHA256", iterations:600000, salt, items:[{provider, iv, ciphertext}] }
```

- The **master password and vault key never leave the browser.** The server (when
  there is one) stores only `{salt, iv, ciphertext, kdf-params}` — useless without
  the password. KDF name + iteration count are stored **with** the ciphertext so
  the work factor is upgradeable (the lesson from the 2022 LastPass breach, where a
  low iteration count was the crux).
- Params follow the current **OWASP Password Storage** guidance (PBKDF2-SHA256 ≥
  600k; Argon2id is a future option via a WASM build). The AES-GCM key is
  **non-extractable** and decrypted keys live only for the duration of a request.
- **Threat model:** zero-knowledge protects data *at rest*; the dominant risk is
  XSS reading in-memory keys. Mitigations shipped/planned: no `innerHTML` of
  untrusted data, a strict CSP with an allowlisted `connect-src` (only the
  configured providers), Subresource Integrity, and short in-memory key lifetime.

---

## Running it

```bash
pip install openfacefx
openfacefx studio                     # → http://127.0.0.1:8765 , opens your browser
openfacefx studio --port 9000 --no-open
```

Static web host: serve `src/openfacefx/studio_web/` (the GitHub Pages build copies
it to `/studio`). It runs fully client-side via Pyodide there.

Container / self-host (a runnable SaaS today — accounts, projects, vault sync):

```bash
docker build -t openfacefx-studio .
docker run --rm -p 8080:8080 -v offx-data:/data \
  -e OFFX_STUDIO_DB=/data/studio.db -e OFFX_STUDIO_SECURE_COOKIE=1 \
  openfacefx-studio                              # live at http://<host>:8080
```

The image runs `openfacefx studio --host 0.0.0.0` — the native pipeline, accounts
+ project storage (`studio_saas.py`), and the stateless `/api/llm` relay. Sign in
from the **Account** chip to save projects; provider keys stay client-side (only
ciphertext ever reaches the server).

## Accounts, projects & multi-tenant SaaS

The container above **is** the SaaS backend. Accounts, per-user project storage,
and vault sync are implemented in `studio_saas.py` (stdlib `sqlite3` + `hashlib`
+ `secrets`), wired into the server in `studio.py`:

1. **Auth** — register / sign-in / sign-out. Passwords are salted +
   PBKDF2-SHA256 hashed (200k rounds); the session is a random opaque token in an
   **httpOnly, SameSite=Lax** cookie. Set `OFFX_STUDIO_SECURE_COOKIE=1` behind TLS.
2. **Projects** — each account owns named projects (the whole actor/take
   workspace: params + tracks). `GET`/`POST /api/projects`, `GET`/`DELETE
   /api/projects/<id>`; strictly isolated per user. The SPA's **Account** menu
   saves / opens / deletes them; with no backend it falls back to a browser-local
   workspace (localStorage).
3. **Key vault sync** — `GET`/`POST /api/vault` persists the client's encrypted
   vault blob (**ciphertext only** — the server never decrypts; zero-knowledge).
4. **LLM relay** — `/api/llm` is a stateless pass-through; put it behind the
   session + rate limits for a hosted deploy.

Storage is a single SQLite file (`~/.openfacefx/studio.db`, override with
`OFFX_STUDIO_DB`); mount it as a volume for a container deploy.

## Roadmap

Built today: Preview (3D head), Curves (**editable — drag keyframes**), Phonemes,
Face Graph (**selectable nodes**), full Export, the Assistant (BYO-key vault +
clean/pronounce/emotion/direct), **actors & takes**, **accounts + project
save/load + vault sync** (native / SaaS backend), native + browser runtimes.

Next: editable Face Graph link functions, emotion **bake** from the LLM's
valence/arousal into the `emotion` layer, a scripting console over the Python API,
and multi-tenant hardening (email verification, OAuth, rate limits, billing).

---

*FaceFX is a trademark of its owners (OC3 Entertainment / Speech Graphics).
OpenFaceFX is an independent, clean-room open-source project and is not affiliated
with or endorsed by them. Studio design informed by the public FaceFX documentation
at [facefx.github.io/documentation](https://facefx.github.io/documentation/).*
