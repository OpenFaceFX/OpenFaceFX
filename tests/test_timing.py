"""TTS timing schema + vendor adapters (issue #14).

Every parser is exercised with an inline fixture (exact symbols and converted
times), malformed input is asserted to raise a clear ValueError, and the
five CLI formats are run end to end through ``from-timing`` with the same track
invariants the MFA path is held to."""

import json
import os
import sys

import pytest

try:
    import openfacefx  # noqa: F401  (installed wheel wins; see test_core)
except ImportError:
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from openfacefx.cli import main as cli_main
from openfacefx.visemes import VISEMES
from openfacefx.mapping import Mapping, Target
from openfacefx import timing as T


def _assert_track_invariants(path, viseme_names=True):
    with open(path, encoding="utf-8") as fh:
        d = json.load(fh)
    assert d["format"] == "openfacefx.track" and d["version"] == 1
    assert d["duration"] > 0 and d["channels"]
    for c in d["channels"]:
        times = [k[0] for k in c["keys"]]
        vals = [k[1] for k in c["keys"]]
        assert times == sorted(times), c["name"]
        assert all(0.0 <= v <= 1.0 for v in vals), c["name"]
        assert all(t <= d["duration"] + 1e-6 for t in times), c["name"]
        assert c["name"] in d["viseme_set"]
        if viseme_names:
            assert c["name"] in VISEMES
    return d


# --------------------------------------------------------------------------- #
# parse_pho                                                                     #
# --------------------------------------------------------------------------- #

PHO = """; espeak-ng --pho output (SAMPA symbols in the wild; ARPABET here just
; so the default mapping yields visemes for the CLI smoke test)
_ 50
P 90
AA1 150 50 120 100 118
P 90
_ 80
"""


def test_parse_pho_cumulative_ms_and_ignores_comments_and_pitch():
    ev = T.parse_pho(PHO)
    assert [e.symbol for e in ev] == ["_", "P", "AA1", "P", "_"]
    assert all(e.unit == "phoneme" for e in ev)
    # 50 / 90 / 150 / 90 / 80 ms, cumulative from 0
    assert [round(e.start, 3) for e in ev] == [0.0, 0.05, 0.14, 0.29, 0.38]
    assert [round(e.end, 3) for e in ev] == [0.05, 0.14, 0.29, 0.38, 0.46]


def test_parse_pho_rejects_malformed():
    with pytest.raises(ValueError, match="DURATION_MS"):
        T.parse_pho("P\n")                       # no duration column
    with pytest.raises(ValueError, match="not a number"):
        T.parse_pho("P ninety\n")
    with pytest.raises(ValueError, match="no phoneme lines"):
        T.parse_pho("; only a comment\n\n")


# --------------------------------------------------------------------------- #
# parse_piper_alignments                                                        #
# --------------------------------------------------------------------------- #

PIPER = json.dumps({"phonemes": ["h", "ə", "l", "oʊ"],
                    "phoneme_id_samples": [2205, 4410, 2205, 6615]})


def test_parse_piper_samples_to_seconds_roundtrip_multiple_rates():
    # 22050 Hz: 2205 samples = 0.1 s exactly; cumulative ends 0.1/0.3/0.4/0.7
    ev = T.parse_piper_alignments(PIPER, 22050)
    assert [e.symbol for e in ev] == ["h", "ə", "l", "oʊ"]
    assert [round(e.end, 4) for e in ev] == [0.1, 0.3, 0.4, 0.7]
    # Same sample counts at 16 kHz stretch proportionally (round-trip check)
    ev16 = T.parse_piper_alignments(PIPER, 16000)
    assert round(ev16[0].end, 6) == round(2205 / 16000, 6)
    assert round(ev16[-1].end, 4) == round(15435 / 16000, 4)


def test_parse_piper_list_of_objects_form():
    txt = json.dumps({"alignments": [{"phoneme": "h", "num_samples": 1600},
                                     {"phoneme": "i", "num_samples": 3200}]})
    ev = T.parse_piper_alignments(txt, 16000)
    assert [e.symbol for e in ev] == ["h", "i"]
    assert [round(e.end, 3) for e in ev] == [0.1, 0.3]


def test_parse_piper_rejects_malformed():
    with pytest.raises(ValueError, match="positive integer"):
        T.parse_piper_alignments(PIPER, 0)
    with pytest.raises(ValueError, match="not valid JSON"):
        T.parse_piper_alignments("{oops", 22050)
    with pytest.raises(ValueError, match="phonemes"):
        T.parse_piper_alignments(json.dumps({"phoneme_id_samples": [1]}), 22050)
    with pytest.raises(ValueError, match="sample counts"):
        T.parse_piper_alignments(
            json.dumps({"phonemes": ["a", "b"], "phoneme_id_samples": [1]}), 22050)


# --------------------------------------------------------------------------- #
# parse_cartesia                                                                #
# --------------------------------------------------------------------------- #

CARTESIA = json.dumps({
    "type": "phoneme_timestamps", "done": False,
    "phoneme_timestamps": {"phonemes": ["h", "ə", "l", "oʊ"],
                           "start": [0.093, 0.174, 0.255, 0.337],
                           "end": [0.174, 0.255, 0.337, 0.418]}})


def test_parse_cartesia_explicit_seconds():
    ev = T.parse_cartesia(CARTESIA)
    assert [e.symbol for e in ev] == ["h", "ə", "l", "oʊ"]
    assert [e.start for e in ev] == [0.093, 0.174, 0.255, 0.337]
    assert [e.end for e in ev] == [0.174, 0.255, 0.337, 0.418]
    # a bare phoneme_timestamps object (no envelope) parses too
    bare = json.dumps({"phonemes": ["a"], "start": [0.0], "end": [0.2]})
    assert T.parse_cartesia(bare)[0].end == 0.2


def test_parse_cartesia_rejects_malformed():
    with pytest.raises(ValueError, match="required"):
        T.parse_cartesia(json.dumps({"phoneme_timestamps": {"phonemes": ["a"]}}))
    with pytest.raises(ValueError, match="differ in length"):
        T.parse_cartesia(json.dumps({"phonemes": ["a", "b"],
                                     "start": [0.0], "end": [0.1]}))


# --------------------------------------------------------------------------- #
# parse_azure_visemes                                                           #
# --------------------------------------------------------------------------- #

AZURE = json.dumps([
    {"audio_offset": 0, "viseme_id": 0},          # ticks; /1e7 = seconds
    {"audio_offset": 2000000, "viseme_id": 21},   # 0.2 s
    {"audio_offset": 4500000, "viseme_id": 2},    # 0.45 s
])


def test_parse_azure_ticks_to_seconds_and_ends_from_next():
    ev = T.resolve_ends(T.parse_azure_visemes(AZURE), final_duration=0.08)
    assert [e.symbol for e in ev] == ["0", "21", "2"]     # str(viseme_id)
    assert all(e.unit == "viseme" for e in ev)
    assert [round(e.start, 3) for e in ev] == [0.0, 0.2, 0.45]
    # end = next start; last held for final_duration
    assert [round(e.end, 3) for e in ev] == [0.2, 0.45, 0.53]


def test_parse_azure_accepts_aliases_and_object_wrapper():
    txt = json.dumps({"visemes": [{"AudioOffset": 1000000, "VisemeId": 6}]})
    ev = T.parse_azure_visemes(txt)
    assert ev[0].symbol == "6" and round(ev[0].start, 3) == 0.1


def test_parse_azure_rejects_malformed():
    with pytest.raises(ValueError, match="not valid JSON"):
        T.parse_azure_visemes("nope")
    with pytest.raises(ValueError, match="integer id"):
        T.parse_azure_visemes(json.dumps([{"audio_offset": 0}]))
    with pytest.raises(ValueError, match="no viseme events"):
        T.parse_azure_visemes("[]")


# --------------------------------------------------------------------------- #
# parse_polly_marks                                                             #
# --------------------------------------------------------------------------- #

POLLY = "\n".join([
    '{"time":0,"type":"viseme","value":"sil"}',
    '{"time":125,"type":"word","start":0,"end":5,"value":"hello"}',   # ignored
    '{"time":125,"type":"viseme","value":"p"}',
    '{"time":300,"type":"viseme","value":"a"}',
    '{"time":480,"type":"viseme","value":"t"}',
])


def test_parse_polly_filters_visemes_and_converts_ms():
    ev = T.resolve_ends(T.parse_polly_marks(POLLY))
    assert [e.symbol for e in ev] == ["sil", "p", "a", "t"]   # word mark dropped
    assert [round(e.start, 3) for e in ev] == [0.0, 0.125, 0.3, 0.48]
    assert [round(e.end, 3) for e in ev] == [0.125, 0.3, 0.48, 0.56]


def test_parse_polly_rejects_malformed():
    with pytest.raises(ValueError, match="not valid JSON"):
        T.parse_polly_marks('{"time":0,"type":"viseme"\n')
    with pytest.raises(ValueError, match="needs 'time' and 'value'"):
        T.parse_polly_marks('{"type":"viseme","value":"p"}\n')
    with pytest.raises(ValueError, match="no viseme marks"):
        T.parse_polly_marks('{"time":0,"type":"word","value":"hi"}\n')


# --------------------------------------------------------------------------- #
# parse_voicevox (VOICEVOX /audio_query, + the API-compatible forks)            #
# --------------------------------------------------------------------------- #

def _voicevox_query(pre=0.1, post=0.1, speed=1.0, pause=None, **top):
    # こんにちは = ko / N / ni / chi / wa, with round lengths for exact math.
    # **top injects top-level fields (e.g. pauseLength/pauseLengthScale); when
    # unused the JSON is byte-identical to the #58 fixture.
    moras = [
        {"text": "コ", "consonant": "k", "consonant_length": 0.05, "vowel": "o", "vowel_length": 0.10},
        {"text": "ン", "consonant": None, "consonant_length": None, "vowel": "N", "vowel_length": 0.08},
        {"text": "ニ", "consonant": "n", "consonant_length": 0.03, "vowel": "i", "vowel_length": 0.07},
        {"text": "チ", "consonant": "ch", "consonant_length": 0.04, "vowel": "i", "vowel_length": 0.06},
        {"text": "ワ", "consonant": "w", "consonant_length": 0.03, "vowel": "a", "vowel_length": 0.12},
    ]
    q = {"accent_phrases": [{"moras": moras, "accent": 5, "pause_mora": pause,
                            "is_interrogative": False}],
         "prePhonemeLength": pre, "postPhonemeLength": post, "speedScale": speed}
    q.update(top)
    return json.dumps(q)


def test_parse_voicevox_timeline_cumulative_starts():
    ev = T.parse_voicevox(_voicevox_query())
    assert [e.symbol for e in ev] == ["pau", "k", "o", "N", "n", "i", "ch",
                                      "i", "w", "a", "pau"]
    # hand-computed cumulative starts: pre 0.1, then consonant+vowel per mora,
    # then post 0.1. Each event's end is the next event's start (contiguous).
    starts = [0.0, 0.1, 0.15, 0.25, 0.33, 0.36, 0.43, 0.47, 0.53, 0.56, 0.68]
    assert [e.start for e in ev] == pytest.approx(starts)
    assert ev[0].end == pytest.approx(0.1) and ev[-1].end == pytest.approx(0.78)


def test_parse_voicevox_speedscale_divides_all_durations():
    ev = T.parse_voicevox(_voicevox_query(speed=2.0))
    assert [e.start for e in ev] == pytest.approx(
        [0.0, 0.05, 0.075, 0.125, 0.165, 0.18, 0.215, 0.235, 0.265, 0.28, 0.34])


def test_parse_voicevox_pause_mora_inserts_a_gap():
    ev = T.parse_voicevox(_voicevox_query(
        pause={"text": "、", "vowel": "pau", "vowel_length": 0.2}))
    # the pause_mora sits after the phrase's last vowel (0.68) and before post
    syms = [e.symbol for e in ev]
    assert syms.count("pau") == 3                      # pre + pause_mora + post
    pau_starts = [e.start for e in ev if e.symbol == "pau"]
    assert pau_starts == pytest.approx([0.0, 0.68, 0.88])


def test_parse_voicevox_maps_openjtalk_phonemes_to_native_targets():
    ev = T.resolve_ends(T.parse_voicevox(_voicevox_query()))
    segs, warnings = T.viseme_events_to_segments(ev, T.VOICEVOX_TO_TARGET)
    m = T.build_vendor_mapping(T.VOICEVOX_TO_TARGET)
    got = [m.targets[max(m.row(s.phoneme), key=m.row(s.phoneme).get)].name
           for s in segs]
    assert got == ["sil", "kk", "O", "nn", "nn", "I", "CH", "I", "RR", "aa", "sil"]
    assert warnings == []                              # every symbol is known
    # unvoiced (uppercase) vowels share their voiced target, and cl -> sil
    assert T.VOICEVOX_TO_TARGET["I"] == T.VOICEVOX_TO_TARGET["i"] == "I"
    assert T.VOICEVOX_TO_TARGET["U"] == T.VOICEVOX_TO_TARGET["u"] == "U"
    assert T.VOICEVOX_TO_TARGET["cl"] == "sil"


def test_parse_voicevox_rejects_malformed():
    with pytest.raises(ValueError, match="not valid JSON"):
        T.parse_voicevox("nope")
    with pytest.raises(ValueError, match="accent_phrases"):
        T.parse_voicevox(json.dumps({"speedScale": 1.0}))
    with pytest.raises(ValueError, match="speedScale must be > 0"):
        T.parse_voicevox(json.dumps({"accent_phrases": [], "speedScale": 0.0}))
    with pytest.raises(ValueError, match="no moras"):
        T.parse_voicevox(json.dumps({"accent_phrases": [], "prePhonemeLength": 0.0}))
    with pytest.raises(ValueError, match="vowel"):
        T.parse_voicevox(json.dumps({"accent_phrases": [
            {"moras": [{"consonant": "k", "consonant_length": 0.05}]}]}))


def test_cli_from_timing_voicevox(tmp_path):
    q = tmp_path / "aq.json"
    q.write_text(_voicevox_query(), encoding="utf-8")
    out = tmp_path / "vv.json"
    assert cli_main(["from-timing", "--format", "voicevox", "--file", str(q),
                     "-o", str(out)]) == 0
    d = json.loads(out.read_text())
    names = {c["name"] for c in d["channels"]}
    assert {"kk", "O", "nn", "I", "CH", "RR", "aa"} & names   # native targets present


# --------------------------------------------------------------------------- #
# parse_voicevox: pauseLength / pauseLengthScale overrides (#59)                #
# --------------------------------------------------------------------------- #

_PAUSE = {"text": "、", "vowel": "pau", "vowel_length": 0.2}


def _pause_span(**top):
    # pre/post 0 so the pause_mora is the only "pau" event; return its duration.
    ev = T.parse_voicevox(_voicevox_query(pre=0.0, post=0.0, pause=_PAUSE, **top))
    pau = [e for e in ev if e.symbol == "pau"]
    assert len(pau) == 1
    return pau[0].end - pau[0].start


def test_voicevox_pauselength_replaces_pause_globally():
    assert _pause_span() == pytest.approx(0.2)                    # pause_mora default
    assert _pause_span(pauseLength=0.5) == pytest.approx(0.5)     # replaced
    assert _pause_span(pauseLength=0.0) == pytest.approx(0.0)     # explicit zero, not None


def test_voicevox_pauselengthscale_multiplies_then_speed():
    assert _pause_span(pauseLengthScale=2.0) == pytest.approx(0.4)   # 0.2 * 2
    assert _pause_span(pauseLengthScale=0.0) == pytest.approx(0.0)   # 0 allowed (engine 0-2)
    # compose replace + scale + speed exactly as the engine: 0.6 * 0.5 / 2.0 = 0.15
    assert _pause_span(pauseLength=0.6, pauseLengthScale=0.5,
                       speedScale=2.0) == pytest.approx(0.15)


def test_voicevox_pause_overrides_absent_is_byte_identical():
    # the pure-superset guarantee: no override (or explicit nulls / a 1.0 scale)
    # yields the exact #58 event stream.
    base = [(e.symbol, e.start, e.end) for e in
            T.parse_voicevox(_voicevox_query(pause=_PAUSE))]
    for variant in ({}, {"pauseLength": None, "pauseLengthScale": None},
                    {"pauseLengthScale": 1.0}):
        got = [(e.symbol, e.start, e.end) for e in
               T.parse_voicevox(_voicevox_query(pause=_PAUSE, **variant))]
        assert got == base, variant


def test_voicevox_pause_override_validation():
    with pytest.raises(ValueError, match="must be >= 0"):
        T.parse_voicevox(_voicevox_query(pause=_PAUSE, pauseLength=-0.1))
    with pytest.raises(ValueError, match="must be >= 0"):
        T.parse_voicevox(_voicevox_query(pause=_PAUSE, pauseLengthScale=-1.0))
    with pytest.raises(ValueError, match="non-numeric"):
        T.parse_voicevox(_voicevox_query(pause=_PAUSE, pauseLength="x"))


# --------------------------------------------------------------------------- #
# resolve_ends / to_segments                                                    #
# --------------------------------------------------------------------------- #

def test_resolve_ends_fills_from_next_and_final_duration_configurable():
    ev = [T.TimingEvent("viseme", "a", 0.0),
          T.TimingEvent("viseme", "b", 0.3),
          T.TimingEvent("viseme", "c", 0.5)]
    r = T.resolve_ends(ev, final_duration=0.1)
    assert [e.end for e in r] == [0.3, 0.5, 0.6]
    assert T.resolve_ends(ev, final_duration=0.25)[-1].end == 0.75
    # events that already carry an end are untouched
    kept = T.resolve_ends([T.TimingEvent("phoneme", "x", 0.0, 0.4)])
    assert kept[0].end == 0.4
    with pytest.raises(ValueError, match="final_duration"):
        T.resolve_ends(ev, final_duration=0.0)


def test_to_segments_requires_resolved_phoneme_events():
    ev = T.resolve_ends(T.parse_pho("P 100\nAA1 200\n"))
    segs = T.to_segments(ev)
    assert [s.phoneme for s in segs] == ["P", "AA1"]
    assert round(segs[1].end, 3) == 0.3
    with pytest.raises(ValueError, match="viseme"):
        T.to_segments([T.TimingEvent("viseme", "0", 0.0, 0.1)])
    with pytest.raises(ValueError, match="unresolved end"):
        T.to_segments([T.TimingEvent("phoneme", "P", 0.0)])


# --------------------------------------------------------------------------- #
# Mapping.allow_custom_symbols                                                   #
# --------------------------------------------------------------------------- #

def test_mapping_custom_symbols_verbatim_and_case_significant():
    m = Mapping([Target("SS"), Target("CH")],
                {"s": {"SS": 1.0}, "S": {"CH": 1.0}, "21": {"SS": 1.0}},
                allow_custom_symbols=True)
    # numeric IDs are not stress-stripped; case is not folded
    assert m.row("s") == {0: 1.0}
    assert m.row("S") == {1: 1.0}
    assert m.row("21") == {0: 1.0}


def test_mapping_default_still_rejects_non_arpabet():
    # the vendor symbols above are only legal with the flag set
    with pytest.raises(ValueError, match="unknown phoneme"):
        Mapping([Target("x")], {"21": {"x": 1.0}})
    with pytest.raises(ValueError, match="unknown phoneme"):
        Mapping([Target("x")], {"@": {"x": 1.0}})


# --------------------------------------------------------------------------- #
# Vendor presets + viseme-unit flow                                             #
# --------------------------------------------------------------------------- #

def test_azure_table_is_complete_and_targets_are_visemes():
    assert sorted(T.AZURE_VISEME_TO_TARGET) == list(range(22))
    assert all(v in VISEMES for v in T.AZURE_VISEME_TO_TARGET.values())
    assert T.AZURE_VISEME_TO_TARGET[0] == "sil"
    assert T.AZURE_VISEME_TO_TARGET[21] == "PP"      # p b m -> lip press


def test_polly_table_targets_are_visemes_and_case_distinct():
    assert all(v in VISEMES for v in T.POLLY_VISEME_TO_TARGET.values())
    # the case-significant pairs land on different targets
    assert T.POLLY_VISEME_TO_TARGET["s"] != T.POLLY_VISEME_TO_TARGET["S"]
    assert T.POLLY_VISEME_TO_TARGET["t"] != T.POLLY_VISEME_TO_TARGET["T"]
    assert T.POLLY_VISEME_TO_TARGET["p"] == "PP"


def test_viseme_flow_unknown_symbol_warns_not_crashes():
    ev = T.resolve_ends(T.parse_azure_visemes(
        json.dumps([{"audio_offset": 0, "viseme_id": 21},
                    {"audio_offset": 1000000, "viseme_id": 99}])))
    segs, warnings = T.viseme_events_to_segments(ev, T.AZURE_VISEME_TO_TARGET)
    assert [s.phoneme for s in segs] == ["21", "sil"]     # 99 routed to silence
    assert warnings and "99" in warnings[0]
    # a fully known stream produces no warnings
    _, none = T.viseme_events_to_segments(
        T.resolve_ends(T.parse_polly_marks(POLLY)), T.POLLY_VISEME_TO_TARGET)
    assert none == []


def test_build_vendor_mapping_drives_oculus_targets():
    m = T.build_vendor_mapping(T.AZURE_VISEME_TO_TARGET)
    assert m.allow_custom_symbols and m.target_names == list(VISEMES)
    # Azure 21 -> PP, and the sil fallback row exists for routed-unknown segments
    assert m.row("21") == {VISEMES.index("PP"): 1.0}
    assert m.row("sil") == {VISEMES.index("sil"): 1.0}


# --------------------------------------------------------------------------- #
# CLI: openfacefx from-timing (every format, MFA-grade invariants)              #
# --------------------------------------------------------------------------- #

def _run(tmp_path, name, text, fmt, extra=()):
    src = tmp_path / name
    src.write_text(text, encoding="utf-8")
    out = str(tmp_path / (fmt + ".json"))
    rc = cli_main(["from-timing", "--file", str(src), "--format", fmt,
                   "-o", out, *extra])
    assert rc == 0
    return out


_OCULUS15 = os.path.join(os.path.dirname(__file__), "..", "examples",
                         "mappings", "oculus15.json")


def test_cli_from_timing_pho_explicit_mapping_overrides_ipa(tmp_path):
    # pho auto-selects the built-in IPA preset (issue #32), but this fixture is
    # written in ARPABET, so an explicit --mapping to the ARPABET table must win
    # and still yield PP/aa. (IPA auto-select coverage lives in test_ipa.py.)
    d = _assert_track_invariants(
        _run(tmp_path, "a.pho", PHO, "pho", ("--mapping", _OCULUS15)))
    names = {c["name"] for c in d["channels"]}
    assert "PP" in names and "aa" in names          # bilabials seal, vowel opens
    pp = [c for c in d["channels"] if c["name"] == "PP"][0]
    assert max(k[1] for k in pp["keys"]) >= 0.5


# --------------------------------------------------------------------------- #
# BH2/BH3: non-finite times and negative durations must raise, not reach solver #
# --------------------------------------------------------------------------- #

def test_bh2_non_finite_time_rejected():
    # json.loads parses NaN/Infinity; a non-finite time would crash int(round(t*fps))
    with pytest.raises(ValueError, match="must be finite"):
        T.parse_cartesia(json.dumps({"phoneme_timestamps": {
            "phonemes": ["a"], "start": [float("nan")], "end": [0.1]}}))
    with pytest.raises(ValueError, match="must be finite"):
        T.parse_azure_visemes('[{"audio_offset": Infinity, "viseme_id": 1}]')


def test_bh3_voicevox_negative_mora_duration_rejected():
    with pytest.raises(ValueError, match="negative duration"):
        T.parse_voicevox(json.dumps({"accent_phrases": [
            {"moras": [{"vowel": "a", "vowel_length": -0.1}]}],
            "prePhonemeLength": 0, "postPhonemeLength": 0, "speedScale": 1}))


def test_cli_from_timing_piper_sample_rate(tmp_path):
    out = _run(tmp_path, "a.json", PIPER, "piper", ("--sample-rate", "22050"))
    d = _assert_track_invariants(out)
    assert abs(d["duration"] - 0.7) < 0.05          # 15435 samples / 22050 Hz
    # a different rate rescales the whole track (round-trip through the CLI)
    out16 = _run(tmp_path, "b.json", PIPER, "piper", ("--sample-rate", "16000"))
    d16 = _assert_track_invariants(out16)
    assert d16["duration"] > d["duration"] + 0.1


def test_cli_from_timing_piper_requires_sample_rate(tmp_path):
    src = tmp_path / "p.json"
    src.write_text(PIPER, encoding="utf-8")
    with pytest.raises(SystemExit):
        cli_main(["from-timing", "--file", str(src), "--format", "piper",
                  "-o", str(tmp_path / "o.json")])


def test_cli_from_timing_cartesia(tmp_path):
    d = _assert_track_invariants(_run(tmp_path, "c.json", CARTESIA, "cartesia"))
    assert abs(d["duration"] - 0.418) < 0.05


def test_cli_from_timing_azure_viseme_path(tmp_path):
    azure = json.dumps([{"audio_offset": 0, "viseme_id": 0},
                        {"audio_offset": 2000000, "viseme_id": 21},
                        {"audio_offset": 4000000, "viseme_id": 2},
                        {"audio_offset": 6000000, "viseme_id": 19},
                        {"audio_offset": 8000000, "viseme_id": 0}])
    d = _assert_track_invariants(_run(tmp_path, "az.json", azure, "azure"))
    names = {c["name"] for c in d["channels"]}
    assert "PP" in names and "aa" in names          # id 21 -> PP, id 2 -> aa


def test_cli_from_timing_polly_viseme_path(tmp_path):
    d = _assert_track_invariants(_run(tmp_path, "po.marks", POLLY, "polly"))
    names = {c["name"] for c in d["channels"]}
    assert "PP" in names and "aa" in names          # p -> PP, a -> aa


def test_cli_from_timing_azure_rejects_mapping_flag(tmp_path):
    src = tmp_path / "az.json"
    src.write_text(AZURE, encoding="utf-8")
    mapping = os.path.join(os.path.dirname(__file__), "..", "examples",
                           "mappings", "oculus15.json")
    with pytest.raises(SystemExit, match="does not apply"):
        cli_main(["from-timing", "--file", str(src), "--format", "azure",
                  "-o", str(tmp_path / "o.json"), "--mapping", mapping])


def test_cli_from_timing_retarget_applies_on_viseme_path(tmp_path):
    # viseme output is Oculus-named, so retarget onto another rig still works
    out = _run(tmp_path, "az.json", AZURE, "azure", ("--retarget", "arkit"))
    d = _assert_track_invariants(out, viseme_names=False)
    assert any(c["name"] == "jawOpen" for c in d["channels"])


def test_custom_symbols_mapping_file_roundtrip(tmp_path):
    """A SAMPA-alphabet mapping file with "custom_symbols": true loads and
    matches verbatim — the documented path for .pho/Piper/Cartesia input."""
    import json
    import pytest
    from openfacefx import generate_from_alignment
    from openfacefx.mapping import Mapping
    from openfacefx.timing import parse_pho, resolve_ends, to_segments
    path = tmp_path / "sampa.json"
    path.write_text(json.dumps({
        "format": "openfacefx.mapping", "version": 1, "custom_symbols": True,
        "targets": [{"name": "kk"}, {"name": "aa"}, {"name": "DD"},
                    {"name": "O"}, {"name": "sil"}],
        "phonemes": {"h": {"kk": 1.0}, "@": {"aa": 1.0}, "l": {"DD": 1.0},
                     "oU": {"O": 1.0}, "_": {"sil": 1.0}, "sil": {"sil": 1.0}},
    }))
    m = Mapping.from_json(str(path))
    assert m.allow_custom_symbols
    events = resolve_ends(parse_pho("h 80\n@ 120\nl 90\noU 210\n_ 100\n"))
    track = generate_from_alignment(to_segments(events), mapping=m)
    names = {c.name for c in track.channels}
    assert {"kk", "aa", "DD", "O"} <= names       # every phone found its target
    # without the flag the same file must fail ARPABET validation
    path2 = tmp_path / "bad.json"
    path2.write_text(path.read_text().replace('"custom_symbols": true, ', ""))
    with pytest.raises(ValueError, match="unknown phoneme"):
        Mapping.from_json(str(path2))
