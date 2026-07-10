"""Event / take layer (`openfacefx.events`, issue #6).

The layer is additive, opt-in and deterministic. These tests pin: the
byte-identity of an event-free track (both the JSON block and the Unity
``m_Events`` slot, against a baseline built the old way), the JSON round-trip,
SHA-256 take selection (hard-coded golden values that MUST reproduce on Python
3.9 and 3.12 — no ``PYTHONHASHSEED`` salt, no RNG), auto-derivation from a
synthetic stress/pause, and the Unity AnimationEvent / Unreal AnimNotify
structure (verified field names — see docs/COMPATIBILITY.md provenance).
"""

import json
import os
import re
import sys

import numpy as np
import pytest

try:
    import openfacefx  # noqa: F401  (installed wheel wins; see test_core)
except ImportError:
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from openfacefx.cli import main as cli_main
from openfacefx.curves import Channel, FaceTrack, Keyframe
from openfacefx.events import (
    Event, Alternative, VariantGroup, Variants, EVENT_TYPES,
    _unit, choose, resolve, add_event, attach_events, read_events,
    validate_events, event_to_dict, event_from_dict,
)
from openfacefx.io_export import to_dict
from openfacefx.export_unity import write_unity_anim
from openfacefx.export_unreal_notifies import notifies_to_dict, write_unreal_notifies
from openfacefx.pipeline import derive_events, naive_segments, generate_from_alignment
from openfacefx.alignment import PhonemeSegment


def _track():
    return FaceTrack(fps=60, channels=[
        Channel("aa", [Keyframe(0.0, 0.5), Keyframe(1.25, 0.0)])])


def _read(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def _event_times(anim_text):
    """The ordered `- time:` values inside the m_Events block."""
    block = anim_text[anim_text.index("m_Events:"):]
    return [float(x) for x in re.findall(r"^  - time: (\S+)$", block, re.M)]


# --- additive / byte-identity ----------------------------------------------

def test_no_events_json_is_byte_identical():
    # A track with no event layer serialises exactly as before: the original six
    # keys, in order, and NO events/variants keys. This is the backward-compat
    # contract (version stays 1).
    d = to_dict(_track())
    assert list(d) == ["format", "version", "fps", "duration",
                       "viseme_set", "channels"]
    assert d["version"] == 1
    assert "events" not in d and "variants" not in d
    # Empty explicit list / None variants are still absent from the output.
    tr = _track()
    tr.events = []
    tr.variants = None
    assert json.dumps(to_dict(tr)) == json.dumps(to_dict(_track()))


def test_no_events_anim_is_byte_identical():
    # An event-free clip must write the exact `m_Events: []` slot it always did.
    # Baseline = the same track rendered with the event path disabled.
    import tempfile
    a = tempfile.NamedTemporaryFile(suffix=".anim", delete=False).name
    b = tempfile.NamedTemporaryFile(suffix=".anim", delete=False).name
    write_unity_anim(_track(), a)                    # events=True default, but none present
    write_unity_anim(_track(), b, events=False)      # event path fully off
    assert _read(a) == _read(b)
    assert _read(a).endswith("  m_Events: []\n")
    os.unlink(a); os.unlink(b)


def test_events_only_emitted_when_present():
    tr = _track()
    add_event(tr, 1.0, "gesture", "nod")
    d = to_dict(tr)
    assert d["events"] and "variants" not in d          # events yes, variants no
    tr2 = _track()
    tr2.variants = Variants("L1", [VariantGroup("g", [Alternative(1.0, [])])])
    d2 = to_dict(tr2)
    assert "events" not in d2 and d2["variants"]["line_id"] == "L1"


# --- serialisation round-trip ----------------------------------------------

def test_event_roundtrip_preserves_fields():
    e = Event(1.2345, "emphasis", "beat", dur=0.2,
              payload={"strength": 0.8, "src": "auto"},
              blend_in=0.08, blend_out=0.12, channel="jaw", id="e7")
    assert event_from_dict(event_to_dict(e)) == e
    # times are rounded to 4dp like the rest of the format
    assert event_to_dict(Event(1.234567, "marker", "x"))["t"] == 1.2346


def test_track_events_variants_roundtrip():
    tr = _track()
    add_event(tr, 0.5, "sound", "footstep", payload={"foot": "L"})
    tr.variants = Variants("npc_017", [
        VariantGroup("headgest", [
            Alternative(1.0, [Event(0.3, "gesture", "nod_small",
                                    payload={"intensity": 0.6})]),
            Alternative(2.0, [Event(0.3, "gesture", "nod_big")]),
        ], seed_salt="s"),
    ])
    d = json.loads(json.dumps(to_dict(tr)))          # through real JSON
    events, variants = read_events(d)
    assert events == tr.events
    assert variants == tr.variants


def test_reader_ignores_unknown_keys():
    # forward-compat: a future top-level key and future event key are ignored.
    d = {"events": [{"t": 1.0, "type": "marker", "name": "x", "future": 9}],
         "variants": None, "surprise": True}
    events, variants = read_events(d)
    assert events == [Event(1.0, "marker", "x")] and variants is None


# --- deterministic take selection (SHA-256, cross-version golden) -----------

def test_unit_hash_golden_values():
    # Hard-coded: SHA-256 is fixed by FIPS 180-4, so these reproduce bit-for-bit
    # on Python 3.9 and 3.12 and every platform. If this breaks, determinism of
    # every shipped line_id broke with it.
    assert _unit("npc_greet_017", "headgest") == pytest.approx(0.239428208932, abs=1e-12)
    assert _unit("", "headgest") == pytest.approx(0.049469385671, abs=1e-12)
    assert _unit("line_A", "headgest") == pytest.approx(0.122046393917, abs=1e-12)
    assert _unit("line_B", "headgest") == pytest.approx(0.374486437081, abs=1e-12)
    assert 0.0 <= _unit("anything", "g") < 1.0


_ALTS = [Alternative(1.0, [Event(0, "gesture", "nod")]),
         Alternative(1.0, [Event(0, "gesture", "shake")]),
         Alternative(2.0, [Event(0, "gesture", "tilt")])]


def test_choose_golden_sequence():
    picks = [choose(_ALTS, f"npc_{i:03d}", "headgest").events[0].name
             for i in range(12)]
    assert picks == ["shake", "nod", "nod", "tilt", "shake", "nod",
                     "shake", "tilt", "tilt", "tilt", "tilt", "nod"]


def test_choose_same_id_is_stable_different_id_can_differ():
    assert (choose(_ALTS, "L", "g").events[0].name ==
            choose(_ALTS, "L", "g").events[0].name)          # stable forever
    names = {choose(_ALTS, f"id{i}", "g").events[0].name for i in range(20)}
    assert len(names) > 1                                    # not all identical


def test_choose_weights_respected():
    from collections import Counter
    dist = Counter(choose(_ALTS, f"id{i}", "g").events[0].name
                   for i in range(4000))
    # weights 1:1:2 -> tilt ~= nod + shake; generous bands around expectation.
    assert 1800 < dist["tilt"] < 2200
    assert 850 < dist["nod"] < 1150 and 850 < dist["shake"] < 1150


def test_groups_vary_independently():
    # Same line, two groups hash independently (group name is in the key).
    assert choose(_ALTS, "shared", "headgest").events[0].name == "nod"
    assert choose(_ALTS, "shared", "gaze").events[0].name == "tilt"


def test_line_id_none_is_constant_default():
    # No line id -> deterministic constant (u for '' is 0.049 -> first bucket).
    v = Variants(None, [VariantGroup("headgest", _ALTS)])
    tr = _track(); tr.variants = v
    got = resolve(tr)
    assert [e.name for e in got] == ["nod"]


# --- resolve merges + sorts -------------------------------------------------

def test_resolve_merges_explicit_and_variant_events_sorted():
    tr = _track()
    add_event(tr, 2.0, "marker", "late")
    add_event(tr, 0.1, "marker", "early")
    tr.variants = Variants("L1", [
        VariantGroup("g", [Alternative(1.0, [Event(1.0, "gesture", "mid")])]),
    ])
    out = resolve(tr)
    assert [e.name for e in out] == ["early", "mid", "late"]   # ascending t
    assert [round(e.t, 3) for e in out] == [0.1, 1.0, 2.0]


def test_resolve_no_variants_returns_copy_of_events():
    tr = _track()
    add_event(tr, 1.0, "marker", "x")
    out = resolve(tr)
    assert out == tr.events and out is not tr.events           # a fresh list


# --- auto-derivation (reuses gestures_layers detectors) ---------------------

def test_derive_emphasis_on_stress_and_phrase_on_pause():
    segs = [PhonemeSegment("sil", 0.0, 0.3), PhonemeSegment("AA1", 0.3, 0.8),
            PhonemeSegment("sil", 0.8, 1.2)]
    evs = derive_events(segs)
    emph = [e for e in evs if e.type == "emphasis"]
    phrase = [e for e in evs if e.type == "marker"]
    assert len(emph) == 1 and abs(emph[0].t - 0.55) < 1e-6     # AA1 centre
    assert emph[0].payload["strength"] == pytest.approx(1.0)
    assert len(phrase) == 2                                    # both sil spans
    assert all(e.type in EVENT_TYPES for e in evs)


def test_derive_from_energy_only():
    t = np.arange(0.0, 3.0, 1.0 / 60.0)
    env = 0.9 * np.exp(-((t - 1.5) ** 2) / (2 * 0.15 ** 2))
    evs = derive_events(None, t, env)
    emph = [e for e in evs if e.type == "emphasis"]
    assert len(emph) == 1 and abs(emph[0].t - 1.5) < 0.05
    assert "level" in emph[0].payload                          # peak prominence


def test_derive_is_deterministic():
    segs = naive_segments("the quick brown fox jumps", 2.5)
    assert [event_to_dict(e) for e in derive_events(segs)] == \
           [event_to_dict(e) for e in derive_events(segs)]


def test_derive_off_switches():
    segs = [PhonemeSegment("sil", 0.0, 0.3), PhonemeSegment("AA1", 0.3, 0.8),
            PhonemeSegment("sil", 0.8, 1.2)]
    assert all(e.type != "marker" for e in derive_events(segs, phrase=False))
    assert all(e.type != "emphasis" for e in derive_events(segs, emphasis=False))


# --- Unity AnimationEvent structure ----------------------------------------

def _anim_with_events(**kw):
    import tempfile
    tr = _track()
    tr.events = [
        Event(1.234, "gesture", "nod_small", payload={"intensity": 0.6}),
        Event(0.5, "emphasis", "beat", dur=0.2, payload={}),
        Event(0.1, "marker", "phrase"),
    ]
    p = tempfile.NamedTemporaryFile(suffix=".anim", delete=False).name
    write_unity_anim(tr, p, **kw)
    text = _read(p)
    os.unlink(p)
    return text


def test_unity_anim_carries_events_ascending_and_packed():
    text = _anim_with_events()
    assert "m_Events: []" not in text
    times = _event_times(text)
    assert times == sorted(times)                              # Unity requires this
    # point event + ranged expands to Begin/End = 4 entries total
    assert times == [0.1, 0.5, 0.7, 1.234]
    # verified field names (real Unity .anim): functionName + stringParameter
    assert text.count("functionName: OnFaceEvent") == 4
    assert "objectReferenceParameter: {fileID: 0}" in text
    # payload packed into the single stringParameter as name|json, YAML-quoted
    assert 'stringParameter: "nod_small|{\\"intensity\\":0.6}"' in text
    assert "stringParameter: phrase\n" in text                 # no payload -> plain
    # DontRequireReceiver=1 default so a missing handler never errors
    assert text.count("messageOptions: 1") == 4


def test_unity_ranged_event_expands_to_begin_end():
    text = _anim_with_events()
    assert "stringParameter: beat_Begin\n" in text
    assert "stringParameter: beat_End\n" in text


def test_unity_event_func_map_and_message_options():
    text = _anim_with_events(event_func_map={"emphasis": "OnBeat"},
                             event_message_options=0)
    assert "functionName: OnBeat" in text                      # type-specific handler
    assert "functionName: OnFaceEvent" in text                 # others fall back
    assert "messageOptions: 0" in text and "messageOptions: 1" not in text


def test_unity_events_off_restores_empty_slot():
    text = _anim_with_events(events=False)
    assert text.endswith("  m_Events: []\n")


# --- Unreal AnimNotify sidecar ---------------------------------------------

def test_unreal_notifies_shape(tmp_path):
    tr = _track()
    tr.events = [Event(1.0, "gesture", "nod", payload={"a": 1}),
                 Event(0.4, "emphasis", "beat", dur=0.3)]
    doc = notifies_to_dict(tr)
    assert doc["format"] == "openfacefx.unreal_notifies" and doc["version"] == 1
    recs = doc["events"]
    assert [r["trigger_time"] for r in recs] == [0.4, 1.0]     # ascending
    beat = recs[0]
    assert beat["notify_name"] == "beat" and beat["duration"] == 0.3
    assert beat["notify_class"] == "emphasis"                  # UAnimNotifyState
    assert recs[1]["payload"] == {"a": 1}
    # writer produces valid JSON on disk; empty track still valid
    p = str(tmp_path / "line.notifies.json")
    write_unreal_notifies(_track(), p)
    assert json.loads(_read(p))["events"] == []


# --- validation warnings ----------------------------------------------------

def test_validate_flags_nonascending_and_ok_when_sorted():
    assert validate_events([Event(0.0, "marker", "a"), Event(1.0, "marker", "b")]) == []
    warn = validate_events([Event(1.0, "marker", "b"), Event(0.0, "marker", "a")])
    assert any("ascending" in w for w in warn)


def test_validate_flags_too_many_events():
    many = [Event(i * 0.001, "marker", "x") for i in range(4097)]
    assert any("4096" in w for w in validate_events(many))


# --- CLI end-to-end ---------------------------------------------------------

def test_cli_events_off_is_byte_identical(tmp_path):
    base = str(tmp_path / "a.json")
    withoff = str(tmp_path / "b.json")
    argv = ["naive", "--text", "hello world this is a test", "--duration", "2.0"]
    assert cli_main(argv + ["-o", base]) == 0
    assert cli_main(argv + ["-o", withoff]) == 0
    assert _read(base) == _read(withoff)
    assert "events" not in json.loads(_read(base))


def test_cli_events_json_and_anim(tmp_path):
    j = str(tmp_path / "e.json")
    rc = cli_main(["naive", "--text", "the quick brown fox jumps",
                   "--duration", "2.0", "--events", "-o", j])
    assert rc == 0
    d = json.loads(_read(j))
    assert d["version"] == 1 and d["channels"]                 # mouth still there
    types = {e["type"] for e in d["events"]}
    assert types and types <= EVENT_TYPES
    # same run to a .anim carries the AnimationEvents
    a = str(tmp_path / "e.anim")
    assert cli_main(["naive", "--text", "the quick brown fox jumps",
                     "--duration", "2.0", "--events", "-o", a]) == 0
    assert "m_Events: []" not in _read(a)
    assert "functionName: OnFaceEvent" in _read(a)


def test_cli_events_compose_with_gestures(tmp_path):
    out = str(tmp_path / "g.json")
    rc = cli_main(["naive", "--text", "hello world this is a test",
                   "--duration", "30", "--events", "--gestures", "-o", out])
    assert rc == 0
    d = json.loads(_read(out))
    names = {c["name"] for c in d["channels"]}
    assert "blink_L" in names                                  # gesture channels
    assert d["events"]                                         # AND events, independent


def test_cli_events_file_take_bake(tmp_path):
    # An authored variants file + a --line-id resolves the take deterministically
    # and BAKES it into concrete events (no variants block left in the output).
    layer = {
        "events": [{"t": 0.0, "type": "marker", "name": "start"}],
        "variants": {"line_id": None, "groups": [
            {"group": "headgest", "seed_salt": "", "alternatives": [
                {"weight": 1.0, "events": [{"t": 0.3, "type": "gesture", "name": "nod"}]},
                {"weight": 1.0, "events": [{"t": 0.3, "type": "gesture", "name": "shake"}]},
                {"weight": 2.0, "events": [{"t": 0.3, "type": "gesture", "name": "tilt"}]},
            ]},
        ]},
    }
    f = tmp_path / "layer.json"
    f.write_text(json.dumps(layer))

    def run(line_id):
        out = str(tmp_path / f"{line_id}.json")
        assert cli_main(["naive", "--text", "hello world", "--duration", "1.0",
                         "--events-file", str(f), "--line-id", line_id,
                         "-o", out]) == 0
        return json.loads(_read(out))

    d = run("npc_000")
    assert "variants" not in d                                 # baked
    names = {e["name"] for e in d["events"]}
    assert "start" in names                                    # explicit event kept
    # npc_000 -> 'shake' per the golden sequence above; stable across runs.
    assert "shake" in names and "nod" not in names and "tilt" not in names
    # a different id can pick a different take (npc_003 -> 'tilt')
    assert "tilt" in {e["name"] for e in run("npc_003")["events"]}


def test_cli_energy_events(tmp_path):
    voice = os.path.join(os.path.dirname(__file__), "..", "examples", "voice.wav")
    out = str(tmp_path / "en.json")
    rc = cli_main(["energy", "--wav", voice, "--events", "-o", out])
    assert rc == 0
    d = json.loads(_read(out))
    assert "aa" in {c["name"] for c in d["channels"]}          # energy mouth
    assert all(e["type"] in EVENT_TYPES for e in d.get("events", []))
