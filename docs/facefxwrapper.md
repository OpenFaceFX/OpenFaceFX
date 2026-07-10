# FaceFXWrapper.exe drop-in shim

OpenFaceFX ships a **CLI-compatible stand-in for Nukem9's
[`FaceFXWrapper.exe`](https://github.com/Nukem9/FaceFXWrapper)** — the tool that
Bethesda-modding voice pipelines shell out to in order to turn a voice `.wav` +
dialogue text into a Skyrim `.lip` file. Point a consumer at our build and it
generates the `.lip` through the OpenFaceFX pipeline instead of Bethesda's
Creation Kit code.

!!! warning "Experimental — the `.lip` payload is not yet verified in-game (#12)"
    The shim produces a **byte-valid** `.lip` (it re-encodes the real vanilla
    sample byte-for-byte and round-trips through an independent decoder), but
    whether Skyrim loads and animates it is **untested**, and the timing is
    approximate (see [Honest limitations](#honest-limitations)). This is a
    research artifact, not a finished FaceFX replacement. In-game testers wanted:
    [issue #12](https://github.com/OpenFaceFX/OpenFaceFX/issues/12).

## Why a drop-in is even possible

The three known consumers — xVASynth's `lip_fuz` plugin and the
Mantella / Pantella AI-NPC pipelines — invoke FaceFXWrapper the same way, and we
verified from their source that:

- **Dispatch is purely on argument count.** There are two forms (below); the tool
  switches on how many arguments it got, not on named flags.
- **Success is gated on one thing: a `.lip` file existing at the output path.**
  The real binary is a GUI-subsystem app whose `printf` output a subprocess
  usually can't capture, so consumers **ignore its exit code and stdout** and just
  check whether the `.lip` appeared. (We still return `0`/`1` and print the
  wrapper's messages, for humans.)
- **The temporary resampled `.wav` is deleted only `if exists`.** So the shim
  simply **never writes it** — nothing to clean up.

That means a faithful drop-in only has to reproduce the positional CLI and drop a
real `.lip` at the right path. It does **not** need Fonix, resampling, or the
Creation Kit.

## The command line

Verbatim from `FFXW32/FFXW32.cpp`'s `StartCommandLine` (it switches on `__argc`,
where `__argv[0]` is the program name):

```
FaceFXWrapper  Type  Lang  FonixDataPath  WavPath  ResampledWavPath  LipPath  Text   # 7 args — resample
FaceFXWrapper  Type  Lang  FonixDataPath  ResampledWavPath  LipPath  Text            # 6 args — pre-resampled
```

| Field | 7-arg index | 6-arg index | Shim behaviour |
|---|:---:|:---:|---|
| `Type` | 0 | 0 | `Skyrim` or `Fallout4`, case-insensitive. Unknown → `Unknown generator type "…"`, exit 1. |
| `Lang` | 1 | 1 | Accepted and **ignored** (consumers always pass `USEnglish`). |
| `FonixDataPath` | 2 | 2 | Accepted and **ignored** — we read no Fonix dictionary. (A stub file must still *exist*; see below.) |
| **Input WAV** | **3** | **3** | Read for its **duration** — index 3 in **both** forms. |
| `ResampledWavPath` | 4 | — | Ignored, and **never written**. |
| **Output `.lip`** | 5 | 4 | Where the generated `.lip` is written. |
| **Text** | 6 | 5 | Dialogue transcript — always last. |

Both forms produce the same result here (we don't resample, so the extra 7-arg
`WavPath`/`ResampledWavPath` split collapses to "read index 3"). `Fallout4` is a
*known* type but unsupported — it fails honestly with no `.lip` (see below), so
the consumer falls back to its own placeholder.

## Drop-in recipe

Each consumer expects a folder containing **`FaceFXWrapper.exe`** and a sibling
**`FonixData.cdf`**, and pre-flight-checks that *both files exist* before running.
Replace the exe with our build and satisfy the `FonixData.cdf` check with a stub:

1. **Build `FaceFXWrapper.exe`** (see [Building the exe](#building-the-exe)) — or,
   for a pipeline that can call a native command, use the `facefxwrapper` console
   script this package installs (no Wine needed).
2. **Provide a `FonixData.cdf` stub.** Consumers only check that the file *exists*
   (it is the real wrapper's Bethesda-owned pronunciation dictionary); **we ignore
   its contents entirely**. Any non-empty file named `FonixData.cdf` next to the
   exe passes the guard:
   ```bash
   printf 'stub' > FonixData.cdf     # contents irrelevant; must merely exist and be non-empty
   ```
   Do **not** ship Bethesda's real `FonixData.cdf` — it's their asset and we don't
   need it.
3. **Place both files where the consumer looks:**

    | Consumer | Where it looks | Form used |
    |---|---|---|
    | **xVASynth** `lip_fuz` plugin | the plugin's own folder (`resources/app/plugins/lip_fuz/…`), which bundles `FaceFXWrapper.exe` + `FonixData.cdf` — replace them in place | 7-arg resample |
    | **Mantella** | the `FaceFXWrapper` folder configured in its settings (it raises on startup if `FaceFXWrapper.exe` **or** `FonixData.cdf` is missing) | 7-arg resample |
    | **Pantella** | its bundled `FaceFXWrapper/` folder (ships the Haurrus fork + a `FonixData.cdf`) — replace the exe, keep/replace the `.cdf` | 7-arg resample |

That's the whole integration surface: same filename, same folder, a `.cdf` that
exists, and a `.lip` at the output path.

## Building the exe

The consumers hardcode the name **`FaceFXWrapper.exe`** and (on Linux/macOS) run
it under their bundled **Wine** prefix, so ship a Windows PE built with
[PyInstaller](https://pyinstaller.org/). Because the shim uses package-relative
imports, freeze a tiny runner that imports it as part of the package (not the
module file directly):

```python
# facefxwrapper_main.py  (PyInstaller entry — not committed to this repo)
import sys
from openfacefx.facefxwrapper import _console
sys.exit(_console())
```

```bash
pip install -e .            # or: pip install openfacefx
pip install pyinstaller
pyinstaller --onefile --console --name FaceFXWrapper facefxwrapper_main.py
# -> dist/FaceFXWrapper.exe  (build on Windows, or cross-build for the Wine target)
```

Then copy `dist/FaceFXWrapper.exe` and a `FonixData.cdf` stub into the consumer
folder from the recipe above. This repository intentionally **does not** commit a
prebuilt binary; the command above is the whole build.

!!! note "FUZ / xWMA repacking is out of scope"
    Some pipelines repack the `.wav` + `.lip` into a `.fuz` with xWMA audio.
    `openfacefx.bethesda.write_fuz` writes the container, but encoding xWMA needs
    an external tool (e.g. `xWMAEncode`), so the shim stops at the `.lip` — which
    is what every consumer above actually calls FaceFXWrapper *for*. A `--fuz`
    convenience mode is a possible follow-up.

## Honest limitations

- **Timing is naive, not acoustic.** The CLI only exposes the WAV's *duration*, so
  phonemes are spread across that duration by the dependency-free
  [naive aligner](index.md) — this is **not** Fonix (or MFA) acoustic alignment,
  and mouth timing is only approximate. For accurate sync, generate offline from a
  real aligner with `openfacefx mfa` instead.
- **The `.lip` payload is experimental (#12).** The slot→morph map and the header
  `u22` field are documented assumptions; the file is byte-valid and self-consistent
  but **unverified in-game**, so mouth *shapes* may be wrong until calibrated (see
  the calibration procedure in [Compatibility](COMPATIBILITY.md)).
- **Fallout 4 is unsupported.** Its 43-target vocabulary is undocumented, so a
  `Fallout4` request prints `LIP generation failed` and writes nothing — the
  consumer then uses its placeholder `.lip`, exactly as it would on a real failure.

## Trying it without a consumer

The installed `facefxwrapper` console script (and `python -m openfacefx
facefxwrapper …`) take the exact same arguments, so you can exercise the contract
directly:

```bash
# 7-arg resample form (Lang / FonixData / ResampledWav are accepted and ignored)
facefxwrapper Skyrim USEnglish FonixData.cdf voice.wav resampled.wav out.lip "hello world"
test -f out.lip && echo "success is: the .lip exists"
```
