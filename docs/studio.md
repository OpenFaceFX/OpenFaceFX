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

**Every API key is entered in one place: the Assistant tab.** LLM keys (for the
director actions) and voice keys (ElevenLabs, OpenAI TTS) share the same vault —
use **＋ key** to add more than one. The Voice engine panel holds only non-secret
preferences (which engine, which voice, what rate) and reads the key back from
the vault at call time, so no secret is ever written to plaintext storage. A
single OpenAI key serves both the director and **Generate voice**.

If the vault is locked, **Generate voice** says so rather than failing obscurely;
unlock it on the Assistant tab.

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

## Voices — three tiers behind one button

**Generate voice** speaks the transcript and drives the take from the result.
Pick the engine under the ⚙ next to it:

| Engine | Quality | Key | Network | Lip-sync timing |
|---|---|---|---|---|
| **Built-in** (`openfacefx.tts`) | robotic (formant synth) | none | none | phonemes from text |
| **Piper** (`openfacefx.piper_tts`) | natural neural | none | none | **real phoneme timing** |
| **ElevenLabs / OpenAI** | natural neural | yours | yes | audio envelope |

Keys for the cloud engines come from the Assistant's vault (see below) — the
Voice panel has no key field. **ElevenLabs keys are scoped**: if yours lacks the
`text_to_speech` permission you get a 401 that looks like a bad key. Fix it in the
ElevenLabs dashboard under Profile → API Keys → edit the key → enable Text to
Speech; the Studio names this case explicitly when it happens.

Piper is the only one that is natural *and* offline *and* keyless. It is also the
**most accurate**: Piper reports how many audio samples each phoneme occupies, so
the viseme curves are solved from actual phoneme boundaries. The cloud voices
return audio only, which leaves the energy engine inferring mouth open/close from
loudness — good, but never as sharp as knowing where the `m` ends.

### Setting Piper up

```bash
pip install "piper-tts[alignment]"        # the [alignment] extra pulls in onnx
python -m piper.download_voices --data-dir VOICES en_US-amy-medium
```

Then set **Voice engine → Piper** and put `VOICES` (the folder, or a specific
`.onnx`) in the **Voice** box. **Rate** is Piper's `length_scale`: `1.0` normal,
`1.6` slower, `0.8` faster — handy for fitting a take to a timing budget.

Desktop only: Piper runs *your* local install, and the browser (Pyodide) build
can't spawn processes. Use `openfacefx studio`, the Docker image, or the SaaS
backend.

Scriptable too:

```python
from openfacefx.piper_tts import synthesize
from openfacefx.timing import parse_piper_alignments, resolve_ends, to_segments
from openfacefx.ipa import IPA_MAPPING
from openfacefx import generate_from_alignment

res = synthesize("Hello world.", "VOICES", length_scale=1.2)
open("out.wav", "wb").write(res.wav)
if res.has_timing:                        # ground-truth phoneme boundaries
    segs = to_segments(resolve_ends(parse_piper_alignments(res.alignments, res.sample_rate)))
    track = generate_from_alignment(segs, fps=30, mapping=IPA_MAPPING)
```

Piper times its phonemes in **IPA**, so the mapping is `IPA_MAPPING` — the same
preset `openfacefx from-timing --format piper` uses. Spaces and punctuation come
back as real durations and map to silence, which is what they are.

### Licensing, and why it's a subprocess

Piper (`piper1-gpl`) is **GPL-3.0**; OpenFaceFX is MIT. So Piper is never
imported and never vendored — it is an optional program we *run*, the same
arrangement as the espeak-ng and MFA aligners the CLI already shells out to.
Nothing breaks if it isn't installed; the Studio just falls back to the built-in
synth. Two environment variables tune the arrangement:

- `OPENFACEFX_PIPER_PYTHON` — the interpreter that can `import piper`, if it
  isn't the one running OpenFaceFX.
- `OPENFACEFX_PIPER_VOICE` — the default voice `.onnx` (or a folder of them).

### If Piper can't find its phonemizer

Some `piper-tts` wheels bake their build machine's espeak-ng data path into the
native bridge, so synthesis fails with
`Error processing file '/Users/runner/work/piper1-gpl/…/espeak-ng-data/phontab'`
(seen on the macOS arm64 wheel of `piper-tts` 1.6.0). The wheel *does* ship the
data inside the package; espeak just has to be pointed at it. What worked in
testing was a clean directory holding a symlink named `espeak-ng-data`:

```bash
mkdir -p ~/.openfacefx/espeak
ln -sfn "$(python -c 'import piper,os;print(os.path.join(os.path.dirname(piper.__file__),"espeak-ng-data"))')" \
        ~/.openfacefx/espeak/espeak-ng-data
export ESPEAK_DATA_PATH=~/.openfacefx/espeak
```

Setting `ESPEAK_DATA_PATH` to the `piper` package directory itself did **not**
work — espeak then looked for `<piper>/phontab` and gave up. The indirection
above is the reliable form. This is an upstream packaging bug, so OpenFaceFX
doesn't try to repair it silently: it surfaces Piper's own error with this hint.

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
