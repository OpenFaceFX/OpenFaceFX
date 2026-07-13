"""Transcript-free acoustic phoneme-recognizer adapters (Allosaurus / generic).

Verifies the FaceFX-style "audio -> phonemes -> animation, no transcript" flow:
parse a recognizer's phone+timestamp output into PhonemeSegments, fill silence
gaps, convert the phone alphabet, tolerate unknown phones (-> sil), and feed
generate_from_alignment end-to-end.
"""

import os
import sys

import pytest

try:
    import openfacefx  # noqa: F401
except ImportError:
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from openfacefx.alignment import PhonemeSegment
from openfacefx.phonemes import SILENCE
from openfacefx.pipeline import generate_from_alignment
from openfacefx.visemes import phoneme_to_viseme
from openfacefx.aligners_acoustic import from_allosaurus, from_phone_timestamps

# The literal Allosaurus --timestamp true example (IPA, "start duration phone").
ALLOSAURUS = """\
0.210 0.045 æ
0.390 0.045 l
0.450 0.045 u
0.540 0.045 s
0.630 0.045 ɔ
0.720 0.045 ɹ
0.870 0.045 s
"""


# --- 1. Allosaurus parse: times, IPA→internal, silence gaps ------------------
def test_from_allosaurus_times_and_gaps():
    segs = from_allosaurus(ALLOSAURUS)
    phones = [s for s in segs if s.phoneme != SILENCE]
    assert [round(s.start, 3) for s in phones] == [0.210, 0.390, 0.450, 0.540,
                                                   0.630, 0.720, 0.870]
    assert [round(s.end, 3) for s in phones] == [0.255, 0.435, 0.495, 0.585,
                                                 0.675, 0.765, 0.915]
    # leading silence [0, 0.210] and a silence before every phone (all gapped)
    assert segs[0].phoneme == SILENCE and segs[0].start == 0.0
    assert round(segs[0].end, 3) == 0.210
    assert sum(s.phoneme == SILENCE for s in segs) == 7
    # segments are contiguous and non-overlapping
    for a, b in zip(segs, segs[1:]):
        assert b.start == pytest.approx(a.end, abs=1e-9)


# --- 2. END-TO-END: recognizer output -> track, NO transcript ---------------
def test_allosaurus_feeds_generate_from_alignment():
    segs = from_allosaurus(ALLOSAURUS)
    track = generate_from_alignment(segs, fps=60)
    assert track.channels                                # produced curves
    assert track.duration == pytest.approx(0.915, abs=0.02)   # fps-quantized
    # the recognized phones drive real mouth shapes, not just silence
    assert any(c.name not in (SILENCE, "sil") for c in track.channels)


# --- 3. an unknown phone passes through and falls to sil, never crashes ------
def test_unknown_phone_falls_to_sil():
    segs = from_phone_timestamps("0.0 0.2 ZZZ\n", alphabet="ipa",
                                 timing="start_dur")
    phone = [s for s in segs if s.phoneme != SILENCE][0]
    assert phoneme_to_viseme(phone.phoneme) == "sil"     # documented degrade


# --- 4. Allosaurus without --timestamp is a clear error ----------------------
def test_allosaurus_requires_timestamps():
    with pytest.raises(ValueError, match="timestamp"):
        from_allosaurus("æ p l\n")                       # plain phone string


# --- 5. generic adapter: text / JSON / iterable, alphabet + timing -----------
def test_generic_text_start_end():
    segs = from_phone_timestamps("0.00 0.10 jh\n0.10 0.20 aa\n",
                                 alphabet="arpabet", timing="start_end")
    phones = [s for s in segs if s.phoneme != SILENCE]
    assert [s.phoneme for s in phones] == ["JH", "AA"] or \
           [s.phoneme for s in phones] == ["jh", "aa"]    # arpabet is identity
    assert phones[0].start == 0.0 and phones[1].end == pytest.approx(0.20)


def test_generic_json():
    js = ('[{"start":0.0,"end":0.1,"phone":"h"},'
          ' {"start":0.1,"end":0.25,"phoneme":"æ"}]')
    segs = from_phone_timestamps(js, alphabet="ipa", timing="start_end")
    phones = [s for s in segs if s.phoneme != SILENCE]
    assert len(phones) == 2
    assert phones[1].end == pytest.approx(0.25)


def test_generic_iterable_of_rows():
    rows = [(0.0, 0.1, "p"), (0.1, 0.2, "aa")]
    segs = from_phone_timestamps(rows, alphabet="arpabet", timing="start_end")
    assert [s.start for s in segs if s.phoneme != SILENCE] == [0.0, 0.1]


# --- 6. mild CTC overlap is clamped to contiguous segments ------------------
def test_overlap_is_clamped():
    # second phone starts before the first ends (0.08 < 0.10)
    segs = from_phone_timestamps("0.00 0.10 p\n0.08 0.20 aa\n",
                                 alphabet="arpabet", timing="start_end")
    assert all(b.start >= a.end - 1e-9 for a, b in zip(segs, segs[1:]))


# --- 7. malformed input raises a clear ValueError ---------------------------
@pytest.mark.parametrize("bad", ["0.0 x 1 aa\n", "not a number 0.1 aa\n"])
def test_non_numeric_rejected(bad):
    with pytest.raises(ValueError):
        from_phone_timestamps(bad, timing="start_dur")


def test_negative_start_rejected():
    with pytest.raises(ValueError, match="negative start"):
        from_phone_timestamps("-0.5 0.1 aa\n", timing="start_dur")


def test_end_before_start_rejected():
    with pytest.raises(ValueError, match="before start"):
        from_phone_timestamps("0.5 0.1 aa\n", timing="start_end")   # end<start


def test_empty_recognizer_output_rejected():
    with pytest.raises(ValueError, match="no phones"):
        from_allosaurus("\n   \n")


# --- 8. CLI: the FaceFX-parity flow — no --text, no --duration ---------------
def _read_track(path):
    import json
    return json.loads(open(path).read())


def test_cli_allosaurus_no_text_no_duration(tmp_path):
    from openfacefx.cli import main as cli_main
    allo = tmp_path / "allo.txt"
    allo.write_text(ALLOSAURUS, encoding="utf-8")
    out = tmp_path / "t.json"
    cli_main(["naive", "--anchors", str(allo), "--anchors-format", "allosaurus",
              "-o", str(out)])
    d = _read_track(str(out))
    assert d["channels"]
    assert d["duration"] == pytest.approx(0.917, abs=0.02)   # from the phone file


def test_cli_generic_phones_arpabet(tmp_path):
    from openfacefx.cli import main as cli_main
    ph = tmp_path / "ph.txt"
    ph.write_text("0.0 0.15 hh\n0.15 0.4 ah0\n0.4 0.7 l\n", encoding="utf-8")
    out = tmp_path / "p.json"
    cli_main(["naive", "--anchors", str(ph), "--anchors-format", "phones",
              "--phones-alphabet", "arpabet", "--phones-timing", "start_end",
              "-o", str(out)])
    assert _read_track(str(out))["channels"]


def test_cli_text_path_still_requires_duration(tmp_path):
    from openfacefx.cli import main as cli_main
    with pytest.raises(SystemExit, match="wav / --duration"):
        cli_main(["naive", "--text", "hello", "-o", str(tmp_path / "x.json")])
