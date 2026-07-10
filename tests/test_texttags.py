"""Transcript text tags (`openfacefx.texttags`, issue #7).

Tags are extracted before G2P and mapped onto the timeline the aligner produced.
These tests pin the four families against the FaceFX-modelled acceptance criteria:
a curve tag becomes a channel peaking over its word (the words still lip-sync); an
event tag lands at the *following* word with its payload preserved in JSON; a
chunk timeline splits the utterance with ``sil`` gaps and rejects a non-monotonic
one; ``[emphasis]`` raises the local vowel peak; ``[pause:N]`` inserts ~N seconds;
a preprocessor injecting a tag matches hand-writing it; and — the load-bearing
invariant — a tagless transcript is byte-identical to the plain naive path. All of
it is deterministic, asserted here to reproduce across Python 3.9 and 3.13.
"""

import os
import sys

import pytest

try:
    import openfacefx  # noqa: F401  (installed wheel wins; see test_core)
except ImportError:
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from openfacefx.phonemes import SILENCE
from openfacefx.pipeline import generate_naive
from openfacefx.io_export import to_dict
from openfacefx.g2p import G2P
from openfacefx.events import event_to_dict
from openfacefx.texttags import (
    parse_tagged_transcript, resolve_tagged_segments, curve_channels, tag_events,
    build_curve_channel, has_tags, Tag,
)


# --------------------------------------------------------------------------- #
# Parsing: clean text + tag positions                                          #
# --------------------------------------------------------------------------- #

def test_parse_strips_tags_and_keeps_words():
    clean, tags = parse_tagged_transcript(
        "[brow_raise type=ct v1=1] really [/brow_raise]")
    assert clean == "really"                     # tag stripped, word survives
    assert len(tags) == 1
    t = tags[0]
    assert t.kind == "curve" and t.name == "brow_raise"
    assert t.word_index == 0 and t.end_word_index == 1
    assert t.params == {"type": "ct", "v1": "1"}


def test_parse_extracts_each_tag_type_at_the_right_position():
    clean, tags = parse_tagged_transcript(
        "hello [emphasis]world[/emphasis] then [event:wave] go [pause:0.3] <2> stop")
    assert clean == "hello world then go stop"
    kinds = {t.kind: t for t in tags}
    assert set(kinds) == {"emphasis", "event", "pause", "chunk"}
    # words: 0 hello, 1 world, 2 then, 3 go, 4 stop
    assert kinds["emphasis"].word_index == 1 and kinds["emphasis"].end_word_index == 2
    assert kinds["event"].word_index == 3 and kinds["event"].name == "wave"
    assert kinds["pause"].word_index == 4 and kinds["pause"].value == 0.3
    assert kinds["chunk"].word_index == 4 and kinds["chunk"].value == 2.0


def test_plain_transcript_parses_to_itself():
    text = "the quick brown fox jumps"
    clean, tags = parse_tagged_transcript(text)
    assert clean == text and tags == []
    assert has_tags(text) is False


def test_has_tags_autodetect():
    assert has_tags("say [gesture:blink] hi")
    assert has_tags("[gain type=lt]word[/gain]")
    assert has_tags('a {"grp|anim"} b')
    assert has_tags("chunk <1.5> here")
    # a lone bracketed word is NOT a tag (transcripts stay untouched)
    assert has_tags("this is [inaudible] speech") is False


# --------------------------------------------------------------------------- #
# Curve tags -> channels (issue AC #1)                                         #
# --------------------------------------------------------------------------- #

def test_curve_tag_makes_channel_peaking_over_the_word():
    track = generate_naive("[brow_raise type=ct v1=1] really [/brow_raise]",
                           1.0, parse_tags=True)
    names = [c.name for c in track.channels]
    assert "brow_raise" in names
    # the word 'really' is still lip-synced: mouth (viseme) channels exist too.
    assert any(n not in ("brow_raise", SILENCE, "sil") for n in names)
    ch = next(c for c in track.channels if c.name == "brow_raise")
    peak = max(k.value for k in ch.keys)
    assert peak == pytest.approx(1.0)
    # the curve attains its peak over the word 'really' (ct triplet centre key).
    _, spans, _ = resolve_tagged_segments("really", 1.0,
                                          parse_tagged_transcript(
                                              "[brow_raise type=ct v1=1] really "
                                              "[/brow_raise]")[1], G2P())
    w0, w1 = spans[0]
    in_window = [k.value for k in ch.keys if w0 - 1e-9 <= k.time <= w1 + 1e-9]
    assert in_window and max(in_window) == pytest.approx(peak)


def test_curve_tag_default_ease_is_02s():
    # a lone word centred in a 2s clip; leading ease pushes the first key 0.2s
    # before the word start (clamped at 0 only if it would go negative).
    ch = build_curve_channel("g", {"type": "ct"}, w_start=1.0, w_end=1.4)
    times = [k.time for k in ch.keys]
    assert times[0] == pytest.approx(0.8)       # 1.0 - 0.2 easein
    assert times[-1] == pytest.approx(1.6)      # 1.4 + 0.2 easeout


def test_curve_types_place_the_peak():
    ws, we = 1.0, 2.0
    lt = build_curve_channel("g", {"type": "lt"}, ws, we)
    ct = build_curve_channel("g", {"type": "ct"}, ws, we)
    tt = build_curve_channel("g", {"type": "tt"}, ws, we)
    peak_t = lambda c: next(k.time for k in c.keys if k.value == max(x.value for x in c.keys))
    assert peak_t(lt) == pytest.approx(1.0)     # leading -> word start
    assert peak_t(ct) == pytest.approx(1.5)     # centered -> word centre
    assert peak_t(tt) == pytest.approx(2.0)     # trailing -> word end


# --------------------------------------------------------------------------- #
# Event tags -> events layer (issue AC #2)                                     #
# --------------------------------------------------------------------------- #

def test_event_tag_lands_at_following_word_with_payload():
    text = "knock knock [event:sound payload=\"door\" magnitude=1.5] there now"
    clean, tags = parse_tagged_transcript(text)
    segs, spans, _ = resolve_tagged_segments(clean, 2.0, tags, G2P())
    evs = tag_events(tags, spans, 2.0)
    assert len(evs) == 1
    ev = evs[0]
    # anchored at the start of the following word ('there', index 2)
    assert ev.t == pytest.approx(spans[2][0], abs=1e-4)
    assert ev.type == "sound" and ev.name == "sound"
    # payload survives into JSON
    d = event_to_dict(ev)
    assert d["payload"] == {"data": "door", "magnitude": 1.5}


def test_gesture_tag_types_as_gesture():
    _, tags = parse_tagged_transcript("wave [gesture:blink] now")
    _, spans, _ = resolve_tagged_segments("wave now", 1.0, tags, G2P())
    ev = tag_events(tags, spans, 1.0)[0]
    assert ev.type == "gesture" and ev.name == "blink"


def test_trailing_event_anchors_to_end_of_last_word():
    _, tags = parse_tagged_transcript("all done [event:beat]")
    segs, spans, _ = resolve_tagged_segments("all done", 1.0, tags, G2P())
    ev = tag_events(tags, spans, 1.0)[0]
    assert ev.t == pytest.approx(spans[-1][1], abs=1e-4)


def test_curly_event_syntax_supported():
    # FaceFX native form: {"group|anim" ...}
    _, tags = parse_tagged_transcript('hi {"gesture|nod" start=-0.1} there')
    assert tags[0].kind == "event" and tags[0].name == "gesture|nod"
    _, spans, _ = resolve_tagged_segments("hi there", 1.0, tags, G2P())
    ev = tag_events(tags, spans, 1.0)[0]
    assert ev.type == "gesture" and ev.name == "nod"


# --------------------------------------------------------------------------- #
# Emphasis -> local vowel peak (team-lead requirement, reuses issue #18)       #
# --------------------------------------------------------------------------- #

def _peak_in_window(track, t0, t1):
    m = 0.0
    for c in track.channels:
        if c.name in (SILENCE, "sil"):
            continue
        for k in c.keys:
            if t0 - 1e-6 <= k.time <= t1 + 1e-6:
                m = max(m, k.value)
    return m


def test_emphasis_raises_local_vowel_peak():
    text = "hello world"
    _, tags = parse_tagged_transcript("hello [emphasis]world[/emphasis]")
    _, spans, _ = resolve_tagged_segments(text, 1.2, tags, G2P())
    w0, w1 = spans[1]                            # 'world' span
    base = generate_naive(text, 1.2, parse_tags=True)
    emph = generate_naive("hello [emphasis]world[/emphasis]", 1.2, parse_tags=True)
    assert _peak_in_window(emph, w0, w1) > _peak_in_window(base, w0, w1)


def test_emphasis_does_not_change_the_spoken_segments():
    # emphasis only re-weights the solve; the phoneme timeline is unchanged.
    base = resolve_tagged_segments("hello world", 1.2,
                                   parse_tagged_transcript("hello world")[1], G2P())[0]
    emp = resolve_tagged_segments("hello world", 1.2,
                                  parse_tagged_transcript(
                                      "hello [emphasis]world[/emphasis]")[1], G2P())[0]
    assert [(s.phoneme, round(s.start, 6)) for s in base] == \
           [(s.phoneme, round(s.start, 6)) for s in emp]


# --------------------------------------------------------------------------- #
# Pause -> inserted silence (team-lead requirement)                            #
# --------------------------------------------------------------------------- #

def test_pause_inserts_silence_and_extends_timeline():
    base = generate_naive("hello world", 1.0, parse_tags=True)
    paused = generate_naive("hello [pause:0.5] world", 1.0, parse_tags=True)
    assert paused.duration == pytest.approx(base.duration + 0.5, abs=1e-3)


def test_pause_silence_lands_at_the_boundary():
    _, tags = parse_tagged_transcript("hello [pause:0.4] world")
    segs, spans, _ = resolve_tagged_segments("hello world", 1.0, tags, G2P())
    # a >=0.4s silence run exists starting near the end of 'hello'
    sil_runs = [(s.start, s.end) for s in segs if s.phoneme == SILENCE]
    assert any(e - s >= 0.4 - 1e-6 and s <= spans[1][0] + 1e-6 for s, e in sil_runs)


# --------------------------------------------------------------------------- #
# Chunk markers (issue AC #3)                                                  #
# --------------------------------------------------------------------------- #

def test_chunk_markers_split_with_sil_gaps():
    text = "<1>the quick fox<3>"
    clean, tags = parse_tagged_transcript(text)
    segs, spans, _ = resolve_tagged_segments(clean, 5.0, tags, G2P())
    speech = [s for s in segs if s.phoneme != SILENCE]
    # speech confined to [1, 3]; pre-roll and tail are silence
    assert speech[0].start == pytest.approx(1.0, abs=1e-6)
    assert speech[-1].end == pytest.approx(3.0, abs=1e-6)
    assert segs[0].phoneme == SILENCE and segs[0].start == pytest.approx(0.0)
    assert segs[-1].phoneme == SILENCE and segs[-1].end == pytest.approx(5.0)


def test_chunk_rejects_non_monotonic():
    clean, tags = parse_tagged_transcript("<5>word<3>")
    with pytest.raises(ValueError):
        resolve_tagged_segments(clean, 10.0, tags, G2P())


def test_chunk_rejects_beyond_duration():
    clean, tags = parse_tagged_transcript("<1>word<99>")
    with pytest.raises(ValueError):
        resolve_tagged_segments(clean, 10.0, tags, G2P())


def test_chunk_allows_equal_boundary_between_phrases():
    clean, tags = parse_tagged_transcript("<1>the quick<10> <10>brown fox<12>")
    segs, spans, _ = resolve_tagged_segments(clean, 12.0, tags, G2P())   # no raise
    assert spans[1][1] == pytest.approx(10.0)
    assert spans[2][0] == pytest.approx(10.0)


# --------------------------------------------------------------------------- #
# Byte-identity + preprocessor (issue AC #4)                                   #
# --------------------------------------------------------------------------- #

def test_tagless_is_byte_identical_to_plain_naive():
    text = "the quick brown fox jumps over the lazy dog"
    plain = to_dict(generate_naive(text, 3.0))
    tagged = to_dict(generate_naive(text, 3.0, parse_tags=True))
    assert plain == tagged


def test_preprocessor_injection_matches_handwriting():
    hand = to_dict(generate_naive("really [event:beat]", 1.0, parse_tags=True))
    pre = to_dict(generate_naive("really", 1.0, parse_tags=True,
                                 preprocess=lambda s: s + " [event:beat]"))
    assert hand == pre


def test_preprocessor_runs_even_without_tags():
    # a preprocessor that adds no tag still runs (identity-composes with plain)
    out = to_dict(generate_naive("hello", 1.0, parse_tags=True,
                                 preprocess=lambda s: s + " world"))
    assert out == to_dict(generate_naive("hello world", 1.0, parse_tags=True))


# --------------------------------------------------------------------------- #
# Malformed tags: graceful pass-through (decision)                             #
# --------------------------------------------------------------------------- #

def test_unclosed_curve_tag_passes_through():
    # decision: an unclosed open yields no tag; nothing is added, no crash.
    clean, tags = parse_tagged_transcript("say [brow type=ct] something")
    assert [t for t in tags if t.kind == "curve"] == []
    track = generate_naive("say [brow type=ct] something", 1.0, parse_tags=True)
    assert "brow" not in [c.name for c in track.channels]


def test_stray_close_tag_is_ignored():
    clean, tags = parse_tagged_transcript("word [/nope] more")
    assert tags == []
    assert clean == "word more"


# --------------------------------------------------------------------------- #
# Determinism across runs / Python versions                                    #
# --------------------------------------------------------------------------- #

def test_deterministic_across_runs():
    text = ("[brow_raise type=ct v1=1] really [/brow_raise] "
            "[event:sound payload=\"x\"] and <2> chunk")
    a = to_dict(generate_naive(text, 3.0, parse_tags=True))
    b = to_dict(generate_naive(text, 3.0, parse_tags=True))
    assert a == b


def test_golden_curve_keyframes():
    # hard-coded golden values that MUST reproduce on 3.9 and 3.13.
    ch = build_curve_channel("g", {"type": "quad", "v1": "0", "v2": "1"},
                             w_start=1.0, w_end=2.0)
    assert [(k.time, k.value) for k in ch.keys] == [
        (0.8, 0.0), (1.0, 1.0), (2.0, 1.0), (2.2, 0.0)]
