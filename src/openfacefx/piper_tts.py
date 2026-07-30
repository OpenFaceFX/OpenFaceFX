"""Piper neural TTS — a real, offline, keyless voice (issue: Studio voice tier 3).

`Piper <https://github.com/OHF-Voice/piper1-gpl>`_ synthesizes natural speech
from a small ONNX voice model on CPU. It fills the gap between the two voices
the Studio already had: the built-in formant synth in :mod:`openfacefx.tts`
(offline and keyless, but robotic by nature) and the BYO-key cloud relay in
:mod:`openfacefx.studio` (natural, but needs an API key, a network round-trip
and a third party). Piper is natural **and** offline **and** keyless.

**Why a subprocess.** The reference implementation (``piper-tts`` on PyPI, the
``piper1-gpl`` project) is **GPL-3.0**; OpenFaceFX is MIT. So Piper is never
imported into this process and never vendored — it is an optional,
user-installed program that we *run*, exactly like the espeak-ng and MFA
aligners the CLI already shells out to. Nothing in this module requires Piper to
be installed; without it :func:`synthesize` raises :class:`PiperError` and the
Studio falls back to the built-in synth.

**Ground-truth phoneme timing.** Piper can report the number of audio samples
each phoneme occupies (``PhonemeAlignment(phoneme, num_samples)``), which
:func:`openfacefx.timing.parse_piper_alignments` already parses. Those counts
tile the waveform *exactly* — verified against ``piper-tts`` 1.6.0:
``sum(num_samples) == len(audio)`` at every ``length_scale``. A Piper take
therefore carries real phoneme timing, so the viseme curves come from the
phoneme pipeline rather than being estimated from loudness. That is strictly
better lip-sync than any cloud voice can give us, since those return audio only.

Alignments are **opt-in in Piper**, because the sample-count tensor is not a
graph output in a stock ``.onnx``. We always ask for them; Piper then patches the
model **in memory** provided the ``onnx`` package is present::

    pip install "piper-tts[alignment]"          # piper-tts + onnx
    python -m piper.download_voices --data-dir VOICES en_US-amy-medium

That is the whole setup — any stock voice then yields real phoneme timing, with
nothing written to disk. Two fallbacks, both verified:

* without ``onnx``, Piper logs a warning and returns audio only, so
  :attr:`PiperResult.alignments` is ``None`` and the caller drives lip-sync from
  the audio envelope instead;
* a voice patched **on disk** (``python -m piper.patch_voice_with_alignment``)
  keeps its timing even with no ``onnx`` installed — which is why
  :func:`find_voice` prefers a model whose name contains ``align`` when it is
  handed a directory.

The phoneme symbols Piper emits are **IPA**, NFD-decomposed, and include
non-phoneme entries — the ``^``/``$`` sentence markers, spaces and punctuation,
and the stress/length marks ``ˈ ˌ ː`` as separate entries.
:data:`openfacefx.ipa.IPA_MAPPING` is the right mapping for them: it folds the
diacritics onto their base symbol and routes the rest to silence (which is what
a space or a comma *is*), so the timing stays contiguous.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Sequence

__all__ = ["PiperError", "PiperResult", "synthesize", "find_voice", "piper_python"]

#: Environment overrides. ``_PYTHON_ENV`` names the interpreter that can
#: ``import piper`` (default: the one running us, i.e. ``pip install piper-tts``
#: into the same environment); ``_VOICE_ENV`` names the default ``.onnx`` voice.
_PYTHON_ENV = "OPENFACEFX_PIPER_PYTHON"
_VOICE_ENV = "OPENFACEFX_PIPER_VOICE"

_TIMEOUT = 180.0


class PiperError(RuntimeError):
    """Piper is missing, misconfigured, or failed to synthesize."""


@dataclass
class PiperResult:
    """One Piper synthesis.

    ``wav`` is a complete 16-bit mono WAV file. ``alignments`` is JSON ready for
    :func:`openfacefx.timing.parse_piper_alignments` (with this
    ``sample_rate``), or ``None`` when the voice model is unpatched.
    """

    wav: bytes
    sample_rate: int
    duration: float
    alignments: Optional[str] = None
    voice: str = ""

    @property
    def has_timing(self) -> bool:
        """True when real phoneme timing came back (a patched voice model)."""
        return bool(self.alignments)


def piper_python() -> str:
    """The interpreter used to run Piper (``$OPENFACEFX_PIPER_PYTHON``, else ours)."""
    return os.environ.get(_PYTHON_ENV) or sys.executable


def find_voice(voice: Optional[str] = None) -> Path:
    """Resolve a Piper voice ``.onnx`` path.

    Order: the explicit argument, then ``$OPENFACEFX_PIPER_VOICE``. A directory
    is searched for ``*.onnx`` (a name containing ``align`` wins, so a patched
    copy is preferred over the stock one beside it).
    """
    raw = voice or os.environ.get(_VOICE_ENV) or ""
    if not raw.strip():
        raise PiperError(
            "no Piper voice model — pass one, or set " + _VOICE_ENV + ". Download "
            "one with: python -m piper.download_voices --data-dir VOICES "
            "en_US-amy-medium")
    p = Path(raw).expanduser()
    if p.is_dir():
        found = sorted(p.glob("*.onnx"))
        if not found:
            raise PiperError(f"no *.onnx voice model in {p}")
        # prefer an alignment-patched copy — it carries real phoneme timing
        return next((f for f in found if "align" in f.name.lower()), found[0])
    if not p.is_file():
        raise PiperError(f"Piper voice model not found: {p}")
    return p


# Runs inside the *Piper* interpreter, so it may import piper. Reads a JSON
# config from argv[1], writes the WAV to argv[2], prints alignment JSON on
# stdout. Kept tolerant of Piper versions: `SynthesisConfig` and the
# `include_alignments` flag are both newer additions.
_HELPER = r'''
import json, sys, wave

cfg = json.load(open(sys.argv[1], encoding="utf-8"))
out_wav = sys.argv[2]
from piper import PiperVoice

kw = {}
if cfg.get("config"):
    kw["config_path"] = cfg["config"]
if cfg.get("espeak_data_dir"):
    kw["espeak_data_dir"] = cfg["espeak_data_dir"]
try:
    voice = PiperVoice.load(cfg["model"], include_alignments=True, **kw)
except TypeError:                      # older Piper: no include_alignments on load
    voice = PiperVoice.load(cfg["model"], **kw)

length_scale = cfg.get("length_scale")
speaker_id = cfg.get("speaker_id")
text = cfg["text"]

def _chunks():
    """Newest API first (SynthesisConfig + opt-in alignments), then fall back."""
    try:
        from piper import SynthesisConfig
    except ImportError:
        SynthesisConfig = None
    if SynthesisConfig is not None:
        sc_kw = {}
        if length_scale is not None:
            sc_kw["length_scale"] = float(length_scale)
        if speaker_id is not None:
            sc_kw["speaker_id"] = int(speaker_id)
        sc = SynthesisConfig(**sc_kw)
        try:
            return list(voice.synthesize(text, syn_config=sc, include_alignments=True))
        except TypeError:
            return list(voice.synthesize(text, syn_config=sc))
    legacy = {}
    if length_scale is not None:
        legacy["length_scale"] = float(length_scale)
    if speaker_id is not None:
        legacy["speaker_id"] = int(speaker_id)
    try:
        return list(voice.synthesize(text, **legacy))
    except TypeError:
        return list(voice.synthesize(text))

chunks = _chunks()
if not chunks:
    raise SystemExit("piper produced no audio")

sample_rate = getattr(chunks[0], "sample_rate", None) or voice.config.sample_rate
frames, aligns = bytearray(), []
for c in chunks:
    raw = getattr(c, "audio_int16_bytes", None)
    if raw is None:                    # older Piper: only the float array
        import array
        fa = c.audio_float_array
        raw = array.array("h", [max(-32768, min(32767, int(s * 32767))) for s in fa]).tobytes()
    frames += raw
    for a in (getattr(c, "phoneme_alignments", None) or []):
        aligns.append({"phoneme": str(a.phoneme), "num_samples": int(a.num_samples)})

with wave.open(out_wav, "wb") as w:
    w.setnchannels(1)
    w.setsampwidth(2)
    w.setframerate(int(sample_rate))
    w.writeframes(bytes(frames))

json.dump({"sample_rate": int(sample_rate),
           "alignments": aligns,
           "n_frames": len(frames) // 2}, sys.stdout)
'''


def synthesize(text: str, voice: Optional[str] = None, *,
               length_scale: Optional[float] = None,
               speaker_id: Optional[int] = None,
               config: Optional[str] = None,
               espeak_data_dir: Optional[str] = None,
               python: Optional[str] = None,
               timeout: float = _TIMEOUT) -> PiperResult:
    """Speak ``text`` with a Piper voice, in a subprocess.

    ``length_scale`` is Piper's speech-rate control: >1 slower, <1 faster
    (1.6 ≈ 3.0s for what takes 2.1s at 1.0). Use it to fit a take to a timing
    budget. ``espeak_data_dir`` overrides Piper's phonemizer data directory —
    needed on installs whose bundled path is wrong (see ``docs``).

    Raises :class:`PiperError` if Piper is unavailable or fails; the message
    carries Piper's own stderr so the cause is actionable.
    """
    if not (text or "").strip():
        raise PiperError("no text to speak")
    model = find_voice(voice)
    exe = python or piper_python()
    # NB: ``espeak_data_dir`` is the espeak-ng-data directory *itself*, whereas
    # ``$ESPEAK_DATA_PATH`` is its parent — so the env var must NOT be forwarded
    # here. It reaches espeak on its own, through the subprocess environment.
    cfg = {"model": str(model), "text": text,
           "config": config or _sidecar(model),
           "espeak_data_dir": espeak_data_dir or "",
           "length_scale": length_scale, "speaker_id": speaker_id}

    with tempfile.TemporaryDirectory(prefix="offx_piper_") as tmp:
        cfg_path = os.path.join(tmp, "cfg.json")
        wav_path = os.path.join(tmp, "out.wav")
        with open(cfg_path, "w", encoding="utf-8") as fh:
            json.dump(cfg, fh)
        meta = _run(exe, cfg_path, wav_path, timeout)
        try:
            wav = Path(wav_path).read_bytes()
        except OSError as e:
            raise PiperError(f"piper wrote no audio ({e})") from None

    sr = int(meta.get("sample_rate") or 0) or 22050
    aligns = meta.get("alignments") or []
    frames = int(meta.get("n_frames") or 0)
    return PiperResult(
        wav=wav, sample_rate=sr,
        duration=round(frames / sr, 4) if sr else 0.0,
        alignments=json.dumps({"alignments": aligns}) if aligns else None,
        voice=model.name)


def _sidecar(model: Path) -> str:
    """Piper's ``<model>.onnx.json`` config, when it sits beside the model."""
    side = model.with_name(model.name + ".json")
    return str(side) if side.is_file() else ""


def _run(exe: str, cfg_path: str, wav_path: str, timeout: float) -> dict:
    """Run the helper in ``exe`` and return its JSON metadata."""
    cmd: Sequence[str] = [exe, "-", cfg_path, wav_path]
    try:
        proc = subprocess.run(cmd, input=_HELPER, capture_output=True,
                              text=True, timeout=timeout)
    except FileNotFoundError:
        raise PiperError(
            f"interpreter not found: {exe} — set {_PYTHON_ENV} to a Python that "
            "can 'import piper'") from None
    except subprocess.TimeoutExpired:
        raise PiperError(f"piper timed out after {timeout:.0f}s") from None
    if proc.returncode != 0:
        raise PiperError(_diagnose(proc.stderr, exe))
    try:
        return json.loads(proc.stdout or "{}")
    except json.JSONDecodeError:
        raise PiperError("piper: unreadable output — " +
                         (proc.stderr or proc.stdout or "")[-300:].strip()) from None


def _diagnose(stderr: str, exe: str) -> str:
    """Turn Piper's stderr into an actionable message."""
    tail = (stderr or "").strip()[-500:]
    if "No module named 'piper'" in tail or "No module named piper" in tail:
        return ("Piper is not installed in " + exe + " — install it with "
                "'pip install piper-tts' (GPL-3.0, kept as a separate program), "
                "or point " + _PYTHON_ENV + " at an environment that has it.")
    if "phontab" in tail or "espeak" in tail.lower():
        return ("Piper's espeak-ng data directory is wrong — some wheels bake in "
                "their build machine's path. Point ESPEAK_DATA_PATH at a "
                "directory *containing* an 'espeak-ng-data' folder. Piper said: "
                + tail)
    return "piper failed: " + (tail or "no error output")
