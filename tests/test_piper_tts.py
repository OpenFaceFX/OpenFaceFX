"""Piper neural-TTS backend (openfacefx.piper_tts).

Piper is GPL-3.0 and optional, so it is never imported — we run it. These tests
therefore put a **stub ``piper`` module** on the subprocess's ``PYTHONPATH`` and
run the *real* helper script under ``sys.executable``. That exercises everything
we actually own (the helper's version fallbacks, int16 conversion, alignment
collection, WAV writing, and our error diagnosis) with no Piper installed.

The stub mirrors the shapes verified against the real ``piper-tts`` 1.6.0:
``PiperVoice.load(model, config_path=, espeak_data_dir=, include_alignments=)``,
``voice.synthesize(text, syn_config=, include_alignments=)`` yielding chunks with
``sample_rate`` / ``audio_int16_bytes`` / ``phoneme_alignments`` of
``PhonemeAlignment(phoneme, num_samples)``, and ``SynthesisConfig(length_scale=,
speaker_id=)``. Real end-to-end behaviour (``sum(num_samples) == len(audio)``)
was confirmed against a patched ``en_US-amy-low`` voice.
"""

from __future__ import annotations

import io
import json
import sys
import wave

import pytest

from openfacefx import piper_tts
from openfacefx.piper_tts import PiperError, find_voice, synthesize

# --------------------------------------------------------------------------- #
# a stub `piper` module, written to a dir we put on the subprocess PYTHONPATH  #
# --------------------------------------------------------------------------- #

_STUB = '''
"""Stand-in for the real (GPL) piper package."""
import json, math, os
from dataclasses import dataclass, field
from typing import List, Optional

# Behaviour switches, so one stub covers every scenario under test.
MODE = os.environ.get("STUB_PIPER_MODE", "aligned")   # aligned | unpatched | legacy | floatonly
_REPORT = os.environ.get("STUB_PIPER_REPORT", "")

PHONEMES = ["^", "h", "\\u0259", "l", "\\u02c8", "o", "\\u028a", " ", "w", "\\u025c", "l", "d", "."]
_BASE_SAMPLES = 512


@dataclass
class PhonemeAlignment:
    phoneme: str
    num_samples: int
    phoneme_ids: Optional[list] = None


@dataclass
class SynthesisConfig:
    speaker_id: Optional[int] = None
    length_scale: Optional[float] = None
    noise_scale: Optional[float] = None
    noise_w_scale: Optional[float] = None
    normalize_audio: bool = True
    volume: float = 1.0


if MODE == "legacy":            # a Piper old enough to lack SynthesisConfig
    del SynthesisConfig


class _Cfg:
    sample_rate = 16000


@dataclass
class AudioChunk:
    sample_rate: int
    alignments: Optional[List[PhonemeAlignment]]
    n: int

    @property
    def phoneme_alignments(self):
        return self.alignments

    @property
    def audio_float_array(self):
        return [math.sin(i * 0.01) * 0.5 for i in range(self.n)]

    @property
    def audio_int16_bytes(self):
        if MODE == "floatonly":              # older Piper: only the float array
            raise AttributeError("audio_int16_bytes")
        import array
        return array.array("h", [max(-32768, min(32767, int(s * 32767)))
                                 for s in self.audio_float_array]).tobytes()


class PiperVoice:
    def __init__(self, model):
        self.config = _Cfg()
        self.model = model

    @classmethod
    def load(cls, model_path, config_path=None, use_cuda=False,
             espeak_data_dir=None, download_dir=None, include_alignments=False):
        if not os.path.isfile(str(model_path)):
            raise FileNotFoundError(str(model_path))
        if _REPORT:
            with open(_REPORT, "w", encoding="utf-8") as fh:
                json.dump({"config_path": str(config_path or ""),
                           "espeak_data_dir": str(espeak_data_dir or "")}, fh)
        return cls(model_path)

    def synthesize(self, text, syn_config=None, include_alignments=False):
        scale = 1.0
        if syn_config is not None and getattr(syn_config, "length_scale", None):
            scale = float(syn_config.length_scale)
        if _REPORT:                          # record what actually arrived
            try:
                cur = json.load(open(_REPORT, encoding="utf-8"))
            except Exception:
                cur = {}
            cur.update({"text": text, "length_scale": scale,
                        "include_alignments": bool(include_alignments),
                        "speaker_id": getattr(syn_config, "speaker_id", None)})
            with open(_REPORT, "w", encoding="utf-8") as fh:
                json.dump(cur, fh)
        per = int(_BASE_SAMPLES * scale)
        aligns = None
        if MODE != "unpatched" and include_alignments:
            aligns = [PhonemeAlignment(p, per) for p in PHONEMES]
        total = per * len(PHONEMES)
        return [AudioChunk(sample_rate=16000, alignments=aligns, n=total)]
'''


@pytest.fixture()
def stub(tmp_path, monkeypatch):
    """Install the stub `piper` for the subprocess and return a voice model path."""
    lib = tmp_path / "stublib"
    lib.mkdir()
    (lib / "piper.py").write_text(_STUB, encoding="utf-8")
    monkeypatch.setenv("PYTHONPATH", str(lib))
    monkeypatch.setenv("STUB_PIPER_MODE", "aligned")
    monkeypatch.delenv("STUB_PIPER_REPORT", raising=False)
    monkeypatch.delenv(piper_tts._VOICE_ENV, raising=False)
    monkeypatch.delenv("ESPEAK_DATA_PATH", raising=False)
    model = tmp_path / "voice.onnx"
    model.write_bytes(b"not a real onnx")
    return model


def _speak(model, **kw):
    return synthesize("Hello world.", str(model), python=sys.executable, **kw)


# --------------------------------------------------------------------------- #
# find_voice                                                                  #
# --------------------------------------------------------------------------- #

def test_find_voice_explicit_file(tmp_path):
    m = tmp_path / "a.onnx"
    m.write_bytes(b"x")
    assert find_voice(str(m)) == m


def test_find_voice_env_var(tmp_path, monkeypatch):
    m = tmp_path / "a.onnx"
    m.write_bytes(b"x")
    monkeypatch.setenv(piper_tts._VOICE_ENV, str(m))
    assert find_voice() == m


def test_find_voice_directory_prefers_the_alignment_patched_copy(tmp_path):
    (tmp_path / "en_US-amy-medium.onnx").write_bytes(b"x")
    (tmp_path / "amy-aligned.onnx").write_bytes(b"x")
    # a patched voice carries real phoneme timing, so it must win
    assert find_voice(str(tmp_path)).name == "amy-aligned.onnx"


def test_find_voice_directory_single_model(tmp_path):
    (tmp_path / "only.onnx").write_bytes(b"x")
    assert find_voice(str(tmp_path)).name == "only.onnx"


def test_find_voice_errors(tmp_path, monkeypatch):
    monkeypatch.delenv(piper_tts._VOICE_ENV, raising=False)
    with pytest.raises(PiperError, match="no Piper voice model"):
        find_voice()
    with pytest.raises(PiperError, match="not found"):
        find_voice(str(tmp_path / "nope.onnx"))
    with pytest.raises(PiperError, match="no \\*.onnx"):
        find_voice(str(tmp_path))


# --------------------------------------------------------------------------- #
# synthesize — the happy path                                                 #
# --------------------------------------------------------------------------- #

def test_synthesize_returns_a_valid_wav_with_alignments(stub):
    r = _speak(stub)
    assert r.wav[:4] == b"RIFF" and r.wav[8:12] == b"WAVE"
    with wave.open(io.BytesIO(r.wav)) as w:
        assert (w.getnchannels(), w.getsampwidth(), w.getframerate()) == (1, 2, 16000)
        frames = w.getnframes()
    assert r.sample_rate == 16000
    assert r.has_timing and r.voice == "voice.onnx"
    assert r.duration == pytest.approx(frames / 16000, abs=1e-4)


def test_alignments_tile_the_audio_exactly(stub):
    """The property that makes Piper worth wiring: no drift against the wave."""
    r = _speak(stub)
    total = sum(a["num_samples"] for a in json.loads(r.alignments)["alignments"])
    with wave.open(io.BytesIO(r.wav)) as w:
        assert total == w.getnframes()


def test_alignments_feed_our_existing_piper_parser_into_a_track(stub):
    from openfacefx import generate_from_alignment
    from openfacefx.ipa import IPA_MAPPING
    from openfacefx.timing import parse_piper_alignments, resolve_ends, to_segments
    r = _speak(stub)
    events = resolve_ends(parse_piper_alignments(r.alignments, r.sample_rate))
    segs = to_segments(events)
    assert len(segs) == 13
    assert segs[-1].end == pytest.approx(r.duration, abs=1e-3)
    track = generate_from_alignment(segs, fps=30, mapping=IPA_MAPPING)
    assert track.channels and track.duration > 0


def test_length_scale_and_text_reach_piper(stub, monkeypatch, tmp_path):
    report = tmp_path / "report.json"
    monkeypatch.setenv("STUB_PIPER_REPORT", str(report))
    r = _speak(stub, length_scale=1.6)
    got = json.loads(report.read_text(encoding="utf-8"))
    assert got["text"] == "Hello world."
    assert got["length_scale"] == pytest.approx(1.6)
    assert got["include_alignments"] is True
    assert r.duration > _speak(stub).duration       # slower ⇒ longer


def test_speaker_id_and_sidecar_config_are_passed(stub, monkeypatch, tmp_path):
    report = tmp_path / "report.json"
    monkeypatch.setenv("STUB_PIPER_REPORT", str(report))
    side = stub.with_name(stub.name + ".json")      # voice.onnx.json beside the model
    side.write_text("{}", encoding="utf-8")
    _speak(stub, speaker_id=3)
    got = json.loads(report.read_text(encoding="utf-8"))
    assert got["speaker_id"] == 3
    assert got["config_path"] == str(side)


def test_espeak_data_dir_is_forwarded(stub, monkeypatch, tmp_path):
    report = tmp_path / "report.json"
    monkeypatch.setenv("STUB_PIPER_REPORT", str(report))
    _speak(stub, espeak_data_dir=str(tmp_path))
    got = json.loads(report.read_text(encoding="utf-8"))
    assert got["espeak_data_dir"] == str(tmp_path)


# --------------------------------------------------------------------------- #
# synthesize — degraded but usable                                            #
# --------------------------------------------------------------------------- #

def test_unpatched_voice_still_yields_audio_without_timing(stub, monkeypatch):
    """A stock .onnx has no alignment output — audio only, caller falls back."""
    monkeypatch.setenv("STUB_PIPER_MODE", "unpatched")
    r = _speak(stub)
    assert r.wav[:4] == b"RIFF"
    assert r.alignments is None and not r.has_timing


def test_legacy_piper_without_synthesisconfig(stub, monkeypatch):
    monkeypatch.setenv("STUB_PIPER_MODE", "legacy")
    r = _speak(stub)
    assert r.wav[:4] == b"RIFF" and r.duration > 0


def test_piper_exposing_only_the_float_array(stub, monkeypatch):
    monkeypatch.setenv("STUB_PIPER_MODE", "floatonly")
    r = _speak(stub)
    with wave.open(io.BytesIO(r.wav)) as w:
        assert w.getsampwidth() == 2 and w.getnframes() > 0


# --------------------------------------------------------------------------- #
# errors are actionable                                                       #
# --------------------------------------------------------------------------- #

def test_missing_piper_says_how_to_install_it(tmp_path, monkeypatch):
    monkeypatch.setenv("PYTHONPATH", str(tmp_path))     # no piper module here
    m = tmp_path / "v.onnx"
    m.write_bytes(b"x")
    with pytest.raises(PiperError, match="pip install piper-tts"):
        synthesize("hi", str(m), python=sys.executable)


def test_missing_interpreter(tmp_path):
    m = tmp_path / "v.onnx"
    m.write_bytes(b"x")
    with pytest.raises(PiperError, match="interpreter not found"):
        synthesize("hi", str(m), python=str(tmp_path / "no-such-python"))


def test_espeak_data_failure_is_diagnosed(tmp_path, monkeypatch):
    """Some Piper wheels bake in their build machine's espeak path."""
    lib = tmp_path / "lib"
    lib.mkdir()
    (lib / "piper.py").write_text(
        "raise RuntimeError(\"Error processing file '/build/espeak-ng-data/phontab'\")",
        encoding="utf-8")
    monkeypatch.setenv("PYTHONPATH", str(lib))
    m = tmp_path / "v.onnx"
    m.write_bytes(b"x")
    with pytest.raises(PiperError, match="ESPEAK_DATA_PATH"):
        synthesize("hi", str(m), python=sys.executable)


def test_empty_text_is_rejected_before_spawning(stub):
    with pytest.raises(PiperError, match="no text"):
        synthesize("   ", str(stub), python=sys.executable)


def test_timeout(stub, monkeypatch):
    lib = stub.parent / "stublib"
    (lib / "piper.py").write_text("import time; time.sleep(30)", encoding="utf-8")
    with pytest.raises(PiperError, match="timed out"):
        _speak(stub, timeout=1.0)


def test_piper_python_env_override(monkeypatch):
    monkeypatch.setenv(piper_tts._PYTHON_ENV, "/opt/py")
    assert piper_tts.piper_python() == "/opt/py"
    monkeypatch.delenv(piper_tts._PYTHON_ENV)
    assert piper_tts.piper_python() == sys.executable


# --------------------------------------------------------------------------- #
# the Studio handler                                                          #
# --------------------------------------------------------------------------- #

def test_studio_tts_piper_returns_a_phoneme_timed_track(stub, monkeypatch):
    from openfacefx import studio
    monkeypatch.setenv(piper_tts._PYTHON_ENV, sys.executable)
    out = studio._tts_piper({"text": "Hello world.", "voice": str(stub), "fps": 30})
    assert "error" not in out
    assert out["has_timing"] is True and out["sr"] == 16000
    assert out["track"]["channels"] and out["segments"]
    assert out["fps"] == 30
    assert out["wav_b64"]
    # spaces/punctuation carry real duration and route to silence, with a note
    assert any("routed to silence" in w for w in out["warnings"])


def test_studio_tts_piper_audio_only_when_unpatched(stub, monkeypatch):
    from openfacefx import studio
    monkeypatch.setenv(piper_tts._PYTHON_ENV, sys.executable)
    monkeypatch.setenv("STUB_PIPER_MODE", "unpatched")
    out = studio._tts_piper({"text": "Hello world.", "voice": str(stub)})
    assert out["has_timing"] is False
    assert "track" not in out and out["wav_b64"]


def test_studio_tts_piper_threads_length_scale(stub, monkeypatch, tmp_path):
    from openfacefx import studio
    report = tmp_path / "r.json"
    monkeypatch.setenv(piper_tts._PYTHON_ENV, sys.executable)
    monkeypatch.setenv("STUB_PIPER_REPORT", str(report))
    studio._tts_piper({"text": "hi", "voice": str(stub), "length_scale": "1.4"})
    assert json.loads(report.read_text(encoding="utf-8"))["length_scale"] == pytest.approx(1.4)


# --------------------------------------------------------------------------- #
# the Studio frontend contract                                                #
# --------------------------------------------------------------------------- #

def _web(name):
    from pathlib import Path
    # encoding is explicit: these files carry non-ASCII UI glyphs and Windows
    # defaults to cp1252, which used to break CI on exactly this kind of read
    return (Path(__file__).resolve().parents[1] / "src" / "openfacefx" /
            "studio_web" / name).read_text(encoding="utf-8")


def test_voice_panel_ids_referenced_by_studio_js_exist_in_the_html():
    """A typo'd element id fails silently in the browser — catch it here."""
    import re
    js, html = _web("studio.js"), _web("index.html")
    ids = set(re.findall(r'id="([^"]+)"', html))
    section = re.search(r"/\* Voice engine settings.*?\$\(\"#ttsBtn\"\) && ", js, re.S)
    assert section, "voice-engine section not found in studio.js"
    used = set(re.findall(r'\$\("#([A-Za-z0-9_]+)"\)', section.group(0)))
    assert {"voiceProvider", "voiceRate", "voiceRateRow", "voicePiperNote"} <= used
    assert not (used - ids), f"studio.js references missing ids: {sorted(used - ids)}"


def test_piper_is_offered_in_the_voice_dropdown_and_routed_in_js():
    import re
    js, html = _web("studio.js"), _web("index.html")
    assert re.search(r'<option value="piper">', html)
    assert 'ttsPiper' in js and '/api/tts_piper' in js
    assert re.search(r'piperConfigured\(\)\s*\{\s*return\s+voiceProvider\(\)==="piper"', js)
    # Piper must NOT be treated as a key-bearing cloud provider
    neural = js.split("function neuralConfigured()", 1)[1].split("\n/*", 1)[0]
    assert 'p!=="piper"' in neural and 'p!=="builtin"' in neural
    # the phoneme-timed branch commits Piper's own track instead of an energy re-solve
    assert re.search(r'if\(piper&&piper\.track\)\{', js)
    assert 'commitTake(piper.track' in js


def test_piper_is_gated_to_the_native_runtime_in_the_ui():
    """Piper spawns a local program, so the browser runtime can't run it. The
    option must be *disabled* there rather than selectable-then-failing, and a
    setting carried over from a desktop session must fall back with a reason."""
    js, html = _web("studio.js"), _web("index.html")
    assert "function gateVoiceProviders()" in js
    # called both at boot and whenever a later probe discovers the native backend
    assert js.count("gateVoiceProviders()") >= 3
    gate = js.split("function gateVoiceProviders()", 1)[1].split("\nfunction ", 1)[0]
    assert "opt.disabled=true" in gate and "opt.disabled=false" in gate
    assert 'prov.value="builtin"' in gate            # stale piper choice falls back
    assert "voicePiperUnavail" in gate and 'id="voicePiperUnavail"' in html
    # the fallback must not discard the user's other voice settings (e.g. an API key)
    assert "...voiceCfg()" in gate


def test_native_probe_is_retried_with_a_real_timeout():
    """bootstrap()'s health check gives up after 600ms so a static host doesn't
    stall; without a patient re-probe, a desktop Studio whose server was still
    starting would report Piper as browser-only."""
    js = _web("studio.js")
    assert "async function probeNative()" in js
    assert "await probeNative()" in js               # ttsPiper defers to it
    probe = js.split("async function probeNative()", 1)[1].split("\nasync function ", 1)[0]
    assert "AbortSignal.timeout(5000)" in probe
    assert "AbortSignal.timeout(600)" in js          # the fast boot check still exists


def test_api_keys_live_only_in_the_assistant_vault():
    """Keys are entered in ONE place — the Assistant's encrypted vault. The voice
    panel must not have a key input, and must never persist a key itself."""
    js, html = _web("studio.js"), _web("index.html")
    assert 'id="voiceKey"' not in html            # no key input in the voice panel
    assert 'id="voiceKeyStatus"' in html          # status + a jump to the Assistant
    assert 'id="voiceOpenAssistant"' in html
    # saveVoiceCfg strips any key before writing to localStorage
    save = js.split("function saveVoiceCfg(c){", 1)[1].split("\n}", 1)[0]
    assert "const {key, ...safe}" in save and "JSON.stringify(safe)" in save
    # readiness and the request itself both source the key from the vault
    assert "function vaultKey(provider)" in js and "a.getKey" in js
    assert "vaultKey(p)" in js
    neural = js.split("function neuralConfigured()", 1)[1].split("\n/*", 1)[0]
    assert "vaultKey(p)" in neural and "c.key" not in neural
    # a plaintext key from the older build is actively removed, not just ignored
    assert "purgeLegacyVoiceKey" in js and "delete c.key" in js


def test_assistant_exposes_a_read_api_and_holds_tts_providers():
    a = _web("assistant.js")
    for fn in ("vaultState()", "hasKey(provider)", "getKey(provider)", "ttsProviders()", "open()"):
        assert fn in a, fn
    # ElevenLabs is storable in the vault but is NOT an LLM the director can call
    assert 'elevenlabs:' in a and 'kind:"tts"' in a
    assert "const isLLM" in a and "const llmEntry" in a
    assert "llmEntry()" in a and "ITEMS[0]" not in a   # director picks an LLM, not item 0
    # one OpenAI key serves both the director and Generate voice
    assert "tts:true" in a
    # multi-key support: adding a second provider to an unlocked vault
    assert "function renderAddKey()" in a and "addKeyBtn" in a
    # the voice panel is told when the vault changes
    assert 'CustomEvent("offx-keys")' in a and '"offx-keys"' in _web("studio.js")


def test_elevenlabs_permission_error_is_explained_not_dumped():
    """A key lacking the text_to_speech scope returns 401, which reads like a bad
    key and sends people hunting for the wrong problem."""
    js = _web("studio.js")
    assert "async function elevenErr(res,vid,key)" in js
    err = js.split("async function elevenErr(res,vid,key){", 1)[1].split("\n}", 1)[0]
    assert "text_to_speech" in err and "Text to Speech" in err
    assert "settings/api-keys" in err                      # names the dashboard page to visit
    for case in ("voice_not_found", "quota_exceeded", "missing_permissions"):
        assert case in err, case
    assert "elevenErr(res,vid,key)" in js         # actually wired into the request


def test_key_fingerprint_identifies_the_stored_key_without_exposing_it():
    """Editing the permissions of a *different* key than the one stored looks
    identical to the edit not working, so both the panel and the error name which
    key is being sent — as a prefix/suffix only, never the secret."""
    js = _web("studio.js")
    assert "function keyHint(k)" in js
    hint = js.split("function keyHint(k){", 1)[1].split("\n}", 1)[0]
    assert "k.slice(0,6)" in hint and "k.slice(-4)" in hint   # matchable, not usable
    assert "k.length" in hint                                 # length disambiguates further
    assert "keyHint(k)" in js.split("function showKeyStatus(p)", 1)[1][:400]
    assert "keyHint(key)" in js                               # and in the failure message


def test_studio_js_and_python_agree_on_the_endpoint_and_field_names():
    js, py = _web("studio.js"), _web("../studio.py")
    assert '"/api/tts_piper"' in py and '/api/tts_piper' in js
    # fields that actually cross the boundary — the UI reads each of these
    for field in ("wav_b64", "length_scale", "voice", "track", "segments", "duration"):
        assert field in js and field in py, field
    # `has_timing` is an API-level summary; the UI deliberately guards on `track`
    # instead, because timing can arrive while building the track fails
    # (`timing_error`) and the label must not then claim "phoneme-timed".
    assert '"has_timing"' in py and "timing_error" in py and "timing_error" in js


def test_studio_tts_piper_input_errors(stub, monkeypatch):
    from openfacefx import studio
    monkeypatch.setenv(piper_tts._PYTHON_ENV, sys.executable)
    assert "no text" in studio._tts_piper({"text": "  "})["error"]
    assert "length_scale" in studio._tts_piper(
        {"text": "hi", "voice": str(stub), "length_scale": "fast"})["error"]
    # a missing voice surfaces Piper's own guidance rather than a traceback
    assert "voice model" in studio._tts_piper({"text": "hi", "voice": "/no/such.onnx"})["error"]


def test_espeak_data_path_env_is_not_forwarded_as_espeak_data_dir(stub, monkeypatch, tmp_path):
    """$ESPEAK_DATA_PATH is the *parent* of espeak-ng-data; Piper's
    ``espeak_data_dir`` is the directory itself. Forwarding one as the other
    would point a correctly-packaged Piper at a folder with no phontab."""
    report = tmp_path / "r.json"
    monkeypatch.setenv("STUB_PIPER_REPORT", str(report))
    monkeypatch.setenv("ESPEAK_DATA_PATH", str(tmp_path / "parent"))
    _speak(stub)
    assert json.loads(report.read_text(encoding="utf-8"))["espeak_data_dir"] == ""
