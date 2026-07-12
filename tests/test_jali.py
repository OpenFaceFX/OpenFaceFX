"""JALI coarticulation rules over the component stage (issue #19).

Pins the acceptance: the rule table is JSON data with the 4 hard constraints +
habits, each individually toggleable; the duplicated-viseme merge collapses "pop
man" into one bilabial hold; lip-heavy visemes anticipate/hold longer; tongue-
class targets never reach lip channels; the empirical timing lookup gives
context-dependent onsets (post-pause vs post-vowel) for /m p b f/; and — the
overriding invariant — with JALI **off** (the default) the output is
byte-identical to the legacy path.
"""

import os
import sys

import numpy as np
import pytest

try:
    import openfacefx  # noqa: F401
except ImportError:
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from openfacefx import coart_jali
from openfacefx.alignment import PhonemeSegment
from openfacefx.coarticulation import CoartParams, _preprocess, build_viseme_curves
from openfacefx.g2p import G2P
from openfacefx.mapping import Mapping, Target, _DEFAULT_CLASSES
from openfacefx.pipeline import naive_segments
from openfacefx.visemes import VISEMES, VISEME_INDEX, phoneme_to_viseme

_G2P = G2P()


def _segs(text, duration):
    return naive_segments(text, duration, g2p=_G2P)


# --------------------------------------------------------------------------- #
# the overriding invariant: JALI OFF is byte-identical to the legacy path      #
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("text", ["hello brave new world", "pop man sees a ship",
                                  "she sells sea shells", "mama papa fifty five"])
def test_jali_off_is_byte_identical_to_the_legacy_path(text):
    segs = _segs(text, max(1.0, len(text) * 0.06))
    legacy_t, legacy_m = build_viseme_curves(segs, fps=60.0)          # no params
    # the explicit default carries jali=False -> must be the same bytes
    off_t, off_m = build_viseme_curves(segs, fps=60.0, params=CoartParams())
    assert np.array_equal(legacy_t, off_t) and np.array_equal(legacy_m, off_m)
    # and a JALI CoartParams with the master flag off is likewise a no-op
    guard = build_viseme_curves(segs, fps=60.0,
                                params=CoartParams(jali=False, jali_timing=True))[1]
    assert np.array_equal(guard, legacy_m)


# --------------------------------------------------------------------------- #
# rule table is data; ids cover the constraints + habits                       #
# --------------------------------------------------------------------------- #

def test_rule_table_is_json_data_and_toggleable():
    rules = coart_jali.load_rules()
    assert rules["format"] == "openfacefx.jali"
    assert {"bilabial", "labiodental", "sibilant", "nasal", "tongue",
            "lip_heavy", "obstruent"} <= set(rules["categories"])
    assert {"bilabial_close", "labiodental_teeth", "sibilant_jaw",
            "nonnasal_lip_open"} <= set(rules["constraints"])
    # the two #53 habits carry their tunables as data too
    assert {"short_no_jaw", "wordfinal_lip"} <= set(rules["habits"])
    assert rules["habits"]["short_no_jaw"]["max_dur"] > 0.0
    assert rules["habits"]["wordfinal_lip"]["onset_ext"] > 0.0
    # every constraint + the prioritised habits are individually addressable
    assert {"bilabial_close", "labiodental_teeth", "sibilant_jaw",
            "nonnasal_lip_open", "duplicated_merge", "lip_heavy",
            "tongue_no_lip", "short_no_jaw", "wordfinal_lip"} == set(
        coart_jali.RULE_IDS)
    # rule_enabled respects the master flag and the selected set (incl. the new
    # habits — off by default, individually selectable)
    assert not coart_jali.rule_enabled(CoartParams(jali=False), "sibilant_jaw")
    assert coart_jali.rule_enabled(CoartParams(jali=True), "sibilant_jaw")
    assert not coart_jali.rule_enabled(
        CoartParams(jali=True, jali_rules=("lip_heavy",)), "sibilant_jaw")
    for hid in ("short_no_jaw", "wordfinal_lip"):
        assert not coart_jali.rule_enabled(CoartParams(jali=False), hid)  # off
        assert coart_jali.rule_enabled(
            CoartParams(jali=True, jali_rules=(hid,)), hid)               # on
        assert not coart_jali.rule_enabled(
            CoartParams(jali=True, jali_rules=("lip_heavy",)), hid)       # isolable


# --------------------------------------------------------------------------- #
# habit: duplicated-viseme merge ("pop man" -> one bilabial hold)              #
# --------------------------------------------------------------------------- #

def test_pop_man_duplicated_viseme_merge():
    segs = _preprocess(_segs("pop man", 1.0), CoartParams())
    merged = coart_jali.merge_duplicates(segs, None)
    assert len(merged) < len(segs)                        # the p+m collapsed
    # the merged segment is one long PP spanning the /p/ end and the /m/ start
    before = [phoneme_to_viseme(s.phoneme) for s in segs]
    after = [phoneme_to_viseme(s.phoneme) for s in merged]
    assert before.count("PP") == after.count("PP") + 1    # two PPs became one
    assert "PP" in after


# --------------------------------------------------------------------------- #
# habit: tongue-only visemes never contribute to lip channels                  #
# --------------------------------------------------------------------------- #

def test_tongue_class_never_contributes_to_lip_channels():
    # a rig where /l/ (a tongue articulation) is mostly tongue but leaks 0.3 lip
    m = Mapping([Target("LIPS", "lips"), Target("TONG", "tongue"),
                 Target("sil", "basic")],
                {"L": {"TONG": 0.7, "LIPS": 0.3}, "AA": {"TONG": 0.0}, "sil": {}})
    segs = _segs("la la la", 1.2)
    without = build_viseme_curves(segs, 60.0, mapping=m,
                                  params=CoartParams(jali=True, jali_rules=()))[1]
    with_rule = build_viseme_curves(
        segs, 60.0, mapping=m,
        params=CoartParams(jali=True, jali_rules=("tongue_no_lip",)))[1]
    assert without[:, 0].max() > 0.1                      # the leak is real ...
    assert with_rule[:, 0].max() < 1e-9                   # ... and fully removed


# --------------------------------------------------------------------------- #
# habit (#53): a short obstruent/nasal leaves the jaw untouched                 #
# --------------------------------------------------------------------------- #

def test_short_obstruent_leaves_the_jaw_untouched():
    # a JALI/A2F-style rig with a dedicated jaw channel: the vowels hold the jaw
    # open and /d/ drives only the tongue. A *short* /d/ must not dip the jaw.
    m = Mapping([Target("JAW", "jaw"), Target("TNG", "tongue"),
                 Target("sil", "basic")],
                {"D": {"TNG": 1.0}, "AA": {"JAW": 1.0}, "sil": {}})
    short = [PhonemeSegment("AA", 0.0, 0.30), PhonemeSegment("D", 0.30, 0.35),
             PhonemeSegment("AA", 0.35, 0.65)]                # 50 ms /d/
    times, off = build_viseme_curves(short, 120.0, mapping=m,
                                     params=CoartParams(jali=True, jali_rules=()))
    _, on = build_viseme_curves(short, 120.0, mapping=m,
                                params=CoartParams(jali=True,
                                                   jali_rules=("short_no_jaw",)))
    span = (times >= 0.30) & (times <= 0.35)
    assert off[span, 0].min() < 0.5                          # the jaw dips ...
    assert on[span, 0].min() > off[span, 0].min() + 0.1      # ... the habit holds it
    # the habit is individually toggleable and off by default
    default = build_viseme_curves(short, 120.0, mapping=m,
                                  params=CoartParams(jali=True))[1]  # all rules
    assert on[span, 0].min() == pytest.approx(default[span, 0].min())
    # a LONG /d/ (beyond the short threshold) is left alone
    long_d = [PhonemeSegment("AA", 0.0, 0.30), PhonemeSegment("D", 0.30, 0.60),
              PhonemeSegment("AA", 0.60, 0.90)]               # 300 ms /d/
    off2 = build_viseme_curves(long_d, 120.0, mapping=m,
                               params=CoartParams(jali=True, jali_rules=()))[1]
    on2 = build_viseme_curves(long_d, 120.0, mapping=m,
                              params=CoartParams(jali=True,
                                                 jali_rules=("short_no_jaw",)))[1]
    assert np.array_equal(off2, on2)                         # untouched


# --------------------------------------------------------------------------- #
# habit (#53): word-final anticipatory lip shape                                #
# --------------------------------------------------------------------------- #

def test_wordfinal_lip_anticipates():
    on = CoartParams(jali=True, jali_rules=("wordfinal_lip",))
    off = CoartParams(jali=True, jali_rules=())
    lead = lambda segs, p: coart_jali.timing_leads(segs, p)[0][1]
    # UW is a lip-heavy shape; word-final (next is silence) it forms early
    final = [PhonemeSegment("sil", 0.0, 0.2), PhonemeSegment("UW", 0.2, 0.4),
             PhonemeSegment("sil", 0.4, 0.6)]
    assert lead(final, on) > lead(final, off)                # onset extended
    # a mid-word UW (followed by a vowel, not silence) is not anticipated
    mid = [PhonemeSegment("sil", 0.0, 0.2), PhonemeSegment("UW", 0.2, 0.4),
           PhonemeSegment("AA", 0.4, 0.6)]
    assert lead(mid, on) == lead(mid, off)
    # it needs a lip shape: a word-final /t/ (viseme DD, not lip-heavy) is untouched
    final_t = [PhonemeSegment("sil", 0.0, 0.2), PhonemeSegment("T", 0.2, 0.4),
               PhonemeSegment("sil", 0.4, 0.6)]
    assert lead(final_t, on) == lead(final_t, off)
    # end-to-end: the earlier onset gives the word-final U viseme more area
    ui = VISEME_INDEX["U"]
    curve_off = build_viseme_curves(final, 120.0, params=off)[1][:, ui]
    curve_on = build_viseme_curves(final, 120.0, params=on)[1][:, ui]
    _onset = lambda col: int(np.argmax(col > 0.1))
    assert _onset(curve_on) <= _onset(curve_off)             # earlier (or equal)
    assert curve_on.sum() > curve_off.sum()                  # more anticipation area


# --------------------------------------------------------------------------- #
# empirical timing: context-dependent onsets for /m p b f/                     #
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("ph", ["M", "P", "B", "F"])
def test_empirical_onset_post_pause_longer_than_post_vowel(ph):
    p = CoartParams(jali=True)
    post_pause = [PhonemeSegment("sil", 0.0, 0.2), PhonemeSegment(ph, 0.2, 0.35)]
    post_vowel = [PhonemeSegment("AA", 0.0, 0.2), PhonemeSegment(ph, 0.2, 0.35)]
    onset_pause = coart_jali.timing_leads(post_pause, p)[0][1]
    onset_vowel = coart_jali.timing_leads(post_vowel, p)[0][1]
    assert onset_pause > onset_vowel                      # anticipation from rest
    assert onset_pause == pytest.approx(0.12)
    assert onset_vowel == pytest.approx(0.06)


# --------------------------------------------------------------------------- #
# hard constraints (each toggleable, measurable)                               #
# --------------------------------------------------------------------------- #

def _jaw_cols():
    return [VISEME_INDEX[v] for v in VISEMES
            if _DEFAULT_CLASSES.get(v, "basic") == "jaw"]


def test_sibilant_narrows_the_jaw():
    times, base = build_viseme_curves(_segs("see saw", 1.0), 60.0)
    _, capped = build_viseme_curves(
        _segs("see saw", 1.0), 60.0,
        params=CoartParams(jali=True, jali_timing=False,
                           jali_rules=("sibilant_jaw",)))
    seg = next(s for s in _preprocess(_segs("see saw", 1.0), CoartParams())
               if s.phoneme.upper() == "S")
    span = (times >= seg.start) & (times <= seg.end)
    jc = _jaw_cols()
    assert base[np.ix_(span, jc)].max() > 0.35            # jaw was open ...
    assert capped[np.ix_(span, jc)].max() <= 0.351        # ... now narrowed


def test_lip_heavy_visemes_anticipate_and_hold_longer():
    # /sh/ (viseme CH) is lip-heavy; with the habit it starts earlier and holds
    # longer than its vowel neighbours (anticipation + hysteresis).
    segs = [PhonemeSegment("AA", 0.0, 0.3), PhonemeSegment("SH", 0.3, 0.45),
            PhonemeSegment("AA", 0.45, 0.75)]
    ch = VISEME_INDEX["CH"]
    plain = build_viseme_curves(segs, 60.0,
                                params=CoartParams(jali=True, jali_rules=()))[1]
    heavy = build_viseme_curves(segs, 60.0,
                                params=CoartParams(jali=True,
                                                   jali_rules=("lip_heavy",)))[1]
    assert heavy[:, ch].sum() > plain[:, ch].sum()        # wider (more area)
    onset = lambda col: int(np.argmax(col > 0.1))         # noqa: E731
    assert onset(heavy[:, ch]) < onset(plain[:, ch])      # earlier onset


def test_nonnasal_lip_open_caps_closed_lip_bleed():
    # /aa/ around a /b/ closure: the vowel should not inherit the closed lips
    segs = _segs("aba", 1.0)
    pp = VISEME_INDEX["PP"]
    only_close = build_viseme_curves(
        segs, 60.0, params=CoartParams(jali=True, jali_timing=False,
                                       jali_rules=("bilabial_close",)))[1]
    opened = build_viseme_curves(
        segs, 60.0, params=CoartParams(jali=True, jali_timing=False,
                                       jali_rules=("bilabial_close",
                                                   "nonnasal_lip_open")))[1]
    # the lip-open constraint reduces the peak closed-lip weight over the clip
    assert opened[:, pp].max() <= only_close[:, pp].max()
    assert opened[:, pp].max() <= 0.90                    # not a full closure
