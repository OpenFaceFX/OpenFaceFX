"""System voice (Web Speech API) — the browser's own voices as a timed take.

The browser can't run Piper (no process to spawn) and the cloud voices need a
key, so ``speechSynthesis`` is the only natural voice a purely in-browser Studio
can reach. It fires ``boundary`` events, so we learn where every **word** lands;
those become word anchors and go through the same ``anchored_segments`` path
"Align from… words" uses, giving real phoneme timing.

Browsers do not expose the synthesized audio, so such a take has timing but no
clip. That limit is asserted here too — it must stay visible in the UI rather
than being discovered at export time.

The JS half is exercised directly under node (see the session harness); these
tests pin the Python side and the JS/HTML contract.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from openfacefx.studio import _align, _anchors_duration

WEB = Path(__file__).resolve().parents[1] / "src" / "openfacefx" / "studio_web"

TEXT = "Hello world, this is the system voice."
# exactly what webspeech.js emits for TEXT (verified against a simulated browser)
WORDS = [{"text": "Hello", "start": 0.0}, {"text": "world,", "start": 0.42},
         {"text": "this", "start": 0.9}, {"text": "is", "start": 1.06},
         {"text": "the", "start": 1.2}, {"text": "system", "start": 1.36},
         {"text": "voice.", "start": 1.82, "end": 2.4}]


def _web(name):
    # explicit encoding: these files carry non-ASCII UI glyphs and Windows CI
    # defaults to cp1252
    return (WEB / name).read_text(encoding="utf-8")


# --------------------------------------------------------------------------- #
# _anchors_duration — the bug that blocked this path                          #
# --------------------------------------------------------------------------- #

class _A:
    def __init__(self, start, end=None):
        self.start, self.end = start, end


def test_anchors_duration_uses_the_largest_real_end():
    assert _anchors_duration([_A(0.0, 0.5), _A(0.5, 2.4), _A(2.4)]) == 2.4


def test_anchors_duration_survives_ends_being_omitted():
    """`end` is optional in the word-anchor schema and word-boundary sources
    report starts — a plain max() over .end raised TypeError on the first None,
    which broke 'Align from… words' for its own documented input."""
    assert _anchors_duration([_A(0.0), _A(0.42), _A(1.82)]) == 1.82


def test_anchors_duration_empty():
    assert _anchors_duration([]) == 0.0


def test_align_accepts_word_anchors_without_ends():
    starts_only = [{"text": w["text"], "start": w["start"]} for w in WORDS]
    out = _align({"transcript": TEXT, "format": "words",
                  "text": json.dumps({"words": starts_only})})
    assert "error" not in out, out.get("error")
    # falls back to the last start (1.82) — nothing invented past the last word.
    # The reported duration is the generated track's, which ends a frame inside.
    assert out["duration"] == pytest.approx(1.82, abs=0.02)
    assert out["segments"]


# --------------------------------------------------------------------------- #
# the browser's word boundaries become real phoneme timing                    #
# --------------------------------------------------------------------------- #

def test_word_boundaries_become_a_phoneme_timed_track():
    out = _align({"transcript": TEXT, "format": "words",
                  "text": json.dumps({"words": WORDS})})
    assert "error" not in out, out.get("error")
    assert out["duration"] == pytest.approx(2.4)
    assert out["channels"] >= 8 and len(out["segments"]) > 20
    assert out["track"]["channels"]


def test_every_reported_word_boundary_lands_on_a_phoneme_start():
    """The point of the feature: timing comes from the browser, not a guess."""
    out = _align({"transcript": TEXT, "format": "words",
                  "text": json.dumps({"words": WORDS})})
    starts = [s["start"] for s in out["segments"]]
    for w in WORDS:
        assert min(abs(s - w["start"]) for s in starts) < 1e-3, w


def test_track_spans_exactly_the_measured_speech():
    out = _align({"transcript": TEXT, "format": "words",
                  "text": json.dumps({"words": WORDS})})
    assert out["segments"][-1]["end"] == pytest.approx(2.4)


# --------------------------------------------------------------------------- #
# frontend contract                                                           #
# --------------------------------------------------------------------------- #

def test_webspeech_module_ships_and_is_loaded():
    assert (WEB / "webspeech.js").is_file()
    html = _web("index.html")
    # must load BEFORE studio.js, which calls window.WebSpeechTTS
    assert html.index('src="webspeech.js"') < html.index('src="studio.js"')
    # plain text, not tomllib — this suite also runs on Python 3.9
    root = WEB.parents[2]
    pyproject = (root / "pyproject.toml").read_text(encoding="utf-8")
    assert '"studio_web/*"' in pyproject               # so the wheel carries it
    # …and the Pages deploy must cache-bust it like every other script, or an
    # edit ships behind a stale browser cache
    import re
    loaded = re.findall(r'<script[^>]*src="([A-Za-z0-9_]+)\.js"', html)
    sed = (root / ".github" / "workflows" / "pages.yml").read_text(encoding="utf-8")
    busted = re.search(r'src=\\"\(([A-Za-z0-9|_]+)\)\\\.js', sed).group(1).split("|")
    assert not [s for s in loaded if s not in busted], sorted(set(loaded) - set(busted))


def test_system_voice_is_offered_and_wired():
    js, html, ws = _web("studio.js"), _web("index.html"), _web("webspeech.js")
    assert '<option value="webspeech">' in html
    assert 'id="voiceSysVoice"' in html and 'id="voiceSysRow"' in html
    assert "function webspeechConfigured()" in js
    assert "generateSystemVoice(text)" in js
    # goes through the existing word-anchor align path, not a new backend
    gen = js.split("async function generateSystemVoice(text){", 1)[1].split("\n}", 1)[0]
    assert 'Pipe.align(text,"words"' in gen
    assert "commitTake(al.track" in gen
    for fn in ("speakAndTime", "marksToWords", "available", "onVoices"):
        assert fn in ws and fn in js, fn


def test_the_no_audio_limitation_is_stated_and_enforced():
    """A take made this way has no clip. That must be said up front and the
    stale clip from a previous engine must be cleared, or export/spectrogram
    would silently use the wrong audio."""
    js, html = _web("studio.js"), _web("index.html")
    assert 'id="voiceSysNote"' in html
    note = html.split('id="voiceSysNote"', 1)[1].split("</p>", 1)[0]
    assert "no audio clip" in note and "export" in note
    gen = js.split("async function generateSystemVoice(text){", 1)[1].split("\n}", 1)[0]
    assert "S.wavBytes=null" in gen and "S.wavSpec=null" in gen
    assert "no clip" in gen                       # and the take label says so
    assert '$("#engine").value="naive"' in gen    # energy would be meaningless


def test_voices_without_boundary_events_fall_back_to_text_timing():
    """Some voices never fire `boundary`; keep the measured duration rather
    than inventing word positions."""
    js = _web("studio.js")
    gen = js.split("async function generateSystemVoice(text){", 1)[1].split("\n}", 1)[0]
    assert "if(!words){" in gen and "runGenerate()" in gen
    ws = _web("webspeech.js")
    assert "return null" in ws                    # marksToWords signals "no timing"


def test_wall_clock_timing_not_elapsedtime():
    """The spec says elapsedTime is seconds; shipped browsers have used
    milliseconds. A performance.now() delta is unambiguous everywhere."""
    ws = _web("webspeech.js")
    assert "performance.now()" in ws
    assert "elapsedTime" not in ws.split("function speakAndTime", 1)[1]
