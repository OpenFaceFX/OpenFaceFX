"""Style presets + lexical-stress amplitude pass (issue #18).

Two additive, opt-in layers on top of the JALI-style intensity/gain dials, held
to the same contract as those dials: OFF by default, byte-identical output when
neutral/off, deterministic, numpy + stdlib only. The load-bearing invariants:

  1. ``style_params("neutral")`` is a default ``CoartParams()`` and rendering
     with it is byte-identical to passing no params (and so is ``--style
     neutral`` / ``--stress-emphasis 0`` at the CLI).
  2. A style only biases amplitude: ``mumble`` opens the jaw less than
     ``exaggerated`` (aa peak ordering), yet each frame still partitions unit
     energy and enforced lip closures still seal (PP >= 0.89).
  3. ``--stress-emphasis`` raises a stressed vowel's viseme peak and lowers an
     unstressed one's, again preserving the ~1 row sum and the closures; it is a
     graceful no-op on inputs without ARPABET stress digits.
"""

import json
import os
import sys

import numpy as np
import pytest

try:
    import openfacefx  # noqa: F401  (installed wheel wins; see test_core)
except ImportError:
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from openfacefx import (
    generate_from_alignment, generate_naive, to_dict,
    CoartParams, STYLE_PRESETS, style_params,
)
from openfacefx.alignment import PhonemeSegment
from openfacefx.coarticulation import build_viseme_curves, _stress_gains
from openfacefx.pipeline import naive_segments
from openfacefx.visemes import VISEMES

PHRASE = "the quick brown fox jumps over the lazy dog"


def _center(times, a, b):
    return int(np.argmin(np.abs(times - (a + b) / 2.0)))


# Two identical AE vowels — one primary-stressed, one unstressed — each flanked
# by B/T so coarticulation keeps them below saturation, leaving headroom for the
# stress bias to move the aa (jaw) channel measurably at each vowel's centre.
def _stress_probe_segments():
    return [
        PhonemeSegment("sil", 0.00, 0.08),
        PhonemeSegment("B", 0.08, 0.16), PhonemeSegment("AE1", 0.16, 0.30),
        PhonemeSegment("T", 0.30, 0.38), PhonemeSegment("sil", 0.38, 0.46),
        PhonemeSegment("B", 0.46, 0.54), PhonemeSegment("AE0", 0.54, 0.68),
        PhonemeSegment("T", 0.68, 0.76), PhonemeSegment("sil", 0.76, 0.84),
    ]


# --------------------------------------------------------------------------- #
# Style presets                                                               #
# --------------------------------------------------------------------------- #

def test_all_style_presets_load_render_and_partition():
    # Every named preset loads to a fresh CoartParams, renders a real track, and
    # keeps the per-frame partition-energy invariant (rows sum to ~1).
    segs = naive_segments(PHRASE, duration=3.0)
    for name in STYLE_PRESETS:
        p = style_params(name)
        assert isinstance(p, CoartParams)
        assert p is not style_params(name)          # a new instance each call...
        assert style_params(name) == style_params(name)  # ...but value-equal
        _, m = build_viseme_curves(segs, fps=60, params=p)
        assert m.shape[0] > 0
        assert m.min() >= 0.0 and m.max() <= 1.0
        assert np.allclose(m.sum(axis=1), 1.0, atol=2e-3)
        track = generate_naive("hello world", duration=1.5, params=p)
        assert track.channels                       # a non-empty, valid track


def test_studio_style_dropdown_options_are_real_presets():
    # Regression: the Studio's Talking-style dropdown once offered broadcast/shout
    # which were NOT in STYLE_PRESETS, so selecting them raised a swallowed KeyError
    # and did nothing. Every non-empty option must resolve to a real preset.
    import re, pathlib
    html = (pathlib.Path(__file__).resolve().parent.parent
            / "src" / "openfacefx" / "studio_web" / "index.html").read_text(encoding="utf-8")
    sel = re.search(r'<select id="style">(.*?)</select>', html, re.S)
    assert sel, "no #style dropdown found in the Studio index.html"
    values = re.findall(r'<option value="([^"]*)"', sel.group(1))
    assert values, "no style options found"
    for v in values:
        if v == "":                       # "" == default (no style) — always valid
            continue
        assert v in STYLE_PRESETS, f"style option {v!r} is not a real preset"
        assert isinstance(style_params(v), CoartParams)


def test_neutral_style_is_default_and_byte_identical():
    # 'neutral' == the defaults, so rendering with it equals passing no params.
    assert style_params("neutral") == CoartParams()
    segs = naive_segments(PHRASE, duration=3.0)
    assert to_dict(generate_from_alignment(segs)) == to_dict(
        generate_from_alignment(segs, params=style_params("neutral")))


def test_mumble_opens_less_than_exaggerated():
    # A style only biases amplitude: mumble's low intensity + tucked jaw gain
    # keeps the jaw (aa) channel below exaggerated's opened one, and broad opens
    # at least as wide as exaggerated. The partition still holds for each.
    segs = naive_segments(PHRASE, duration=3.0)
    aa = VISEMES.index("aa")
    peaks = {}
    for name in ("mumble", "neutral", "exaggerated", "broad"):
        _, m = build_viseme_curves(segs, fps=60, params=style_params(name))
        peaks[name] = m[:, aa].max()
        assert np.allclose(m.sum(axis=1), 1.0, atol=2e-3)
    assert peaks["mumble"] < peaks["neutral"] < peaks["exaggerated"]
    assert peaks["broad"] >= peaks["exaggerated"]


def test_style_closures_seal_even_when_softened():
    # A whispered/mumbled bilabial still fully closes: closure enforcement runs
    # after the style dials and wins, so PP reaches its floor under low intensity.
    segs = [PhonemeSegment("AA", 0.0, 0.25), PhonemeSegment("P", 0.25, 0.33),
            PhonemeSegment("AA", 0.33, 0.6)]
    pp = VISEMES.index("PP")
    for name in ("whisper", "mumble"):
        times, m = build_viseme_curves(segs, fps=120, params=style_params(name))
        mid = int(np.argmin(np.abs(times - 0.29)))
        assert m[mid, pp] >= 0.89
        assert np.allclose(m.sum(axis=1), 1.0, atol=2e-3)


# --------------------------------------------------------------------------- #
# Lexical-stress amplitude pass                                               #
# --------------------------------------------------------------------------- #

def test_stress_emphasis_raises_stressed_lowers_unstressed():
    # Same AE vowel: the primary-stressed one's aa peak rises with the emphasis
    # amount, the unstressed one's falls, and every frame still sums to ~1.
    segs = _stress_probe_segments()
    aa = VISEMES.index("aa")
    times, base = build_viseme_curves(segs, fps=120)
    cs, cu = _center(times, 0.16, 0.30), _center(times, 0.54, 0.68)
    prev_stressed, prev_unstressed = base[cs, aa], base[cu, aa]
    for amt in (0.5, 1.0, 1.5):
        p = CoartParams(); p.stress_emphasis = amt
        t, m = build_viseme_curves(segs, fps=120, params=p)
        s, u = m[_center(t, 0.16, 0.30), aa], m[_center(t, 0.54, 0.68), aa]
        assert s > prev_stressed                    # stressed rises monotonically
        assert u < prev_unstressed                  # unstressed falls monotonically
        assert s > u                                # and stressed clears unstressed
        assert np.allclose(m.sum(axis=1), 1.0, atol=2e-3)
        prev_stressed, prev_unstressed = s, u


def test_stress_emphasis_preserves_closures_and_partition():
    # The pass biases dominance before closure enforcement, so a stressed word
    # with an interior bilabial still seals (PP >= 0.89) and rows still sum ~1.
    segs = [PhonemeSegment("sil", 0.0, 0.1), PhonemeSegment("AA1", 0.1, 0.3),
            PhonemeSegment("P", 0.3, 0.38), PhonemeSegment("AA1", 0.38, 0.6),
            PhonemeSegment("sil", 0.6, 0.7)]
    pp = VISEMES.index("PP")
    p = CoartParams(); p.stress_emphasis = 1.5
    times, m = build_viseme_curves(segs, fps=120, params=p)
    mid = int(np.argmin(np.abs(times - 0.34)))
    assert m[mid, pp] >= 0.89
    assert np.allclose(m.sum(axis=1), 1.0, atol=2e-3)


def test_stress_emphasis_off_is_byte_identical():
    # The default (0.0) is a behavioural no-op: an explicit CoartParams with
    # stress_emphasis 0 renders the same track as passing no params at all.
    segs = naive_segments(PHRASE, duration=3.0)
    p = CoartParams(); p.stress_emphasis = 0.0
    assert to_dict(generate_from_alignment(segs)) == to_dict(
        generate_from_alignment(segs, params=p))


def test_stress_emphasis_no_digits_is_graceful_noop():
    # Inputs without ARPABET stress digits (vendor/IPA timing) have nothing to
    # bias: _stress_gains is all-ones and the render is byte-identical to off.
    segs = [PhonemeSegment("sil", 0.0, 0.1), PhonemeSegment("AA", 0.1, 0.4),
            PhonemeSegment("T", 0.4, 0.5), PhonemeSegment("AA", 0.5, 0.8),
            PhonemeSegment("sil", 0.8, 0.9)]
    assert np.array_equal(_stress_gains(segs, 1.0), np.ones(len(segs)))
    _, off = build_viseme_curves(segs, fps=120)
    p = CoartParams(); p.stress_emphasis = 1.0
    _, on = build_viseme_curves(segs, fps=120, params=p)
    assert np.array_equal(off, on)


def test_stress_gains_formula():
    # Primary up by amount, secondary half, unstressed down by 0.35*amount, and
    # consonants/digit-less vowels untouched -- the documented modulation.
    segs = [PhonemeSegment("AA1", 0, 0.1), PhonemeSegment("AA2", 0.1, 0.2),
            PhonemeSegment("AA0", 0.2, 0.3), PhonemeSegment("T", 0.3, 0.4),
            PhonemeSegment("AA", 0.4, 0.5)]
    g = _stress_gains(segs, 1.0)
    assert g[0] == pytest.approx(2.0)      # primary: 1 + 1.0
    assert g[1] == pytest.approx(1.5)      # secondary: 1 + 0.5
    assert g[2] == pytest.approx(0.65)     # unstressed: 1 - 0.35
    assert g[3] == 1.0 and g[4] == 1.0     # consonant + digit-less vowel


def test_determinism_repeated_renders_identical():
    # Same inputs -> identical output across repeated runs (no hidden RNG state).
    segs = _stress_probe_segments()
    p = CoartParams(); p.stress_emphasis = 1.0
    a = to_dict(generate_from_alignment(segs, params=p))
    b = to_dict(generate_from_alignment(segs, params=p))
    assert a == b
    s = to_dict(generate_naive(PHRASE, 3.0, params=style_params("exaggerated")))
    s2 = to_dict(generate_naive(PHRASE, 3.0, params=style_params("exaggerated")))
    assert s == s2


# --------------------------------------------------------------------------- #
# CLI end-to-end                                                              #
# --------------------------------------------------------------------------- #

def _run(tmp_path, name, *extra):
    from openfacefx.cli import main as cli_main
    out = str(tmp_path / name)
    argv = ["naive", "--text", PHRASE, "--duration", "3.0", "-o", out, *extra]
    assert cli_main(argv) == 0
    return out


def test_cli_style_neutral_byte_identical(tmp_path):
    # --style neutral and --stress-emphasis 0 write byte-for-byte the same file
    # as a plain run (the byte-identity guarantee at the CLI boundary).
    plain = _run(tmp_path, "plain.json")
    neutral = _run(tmp_path, "neutral.json", "--style", "neutral")
    off = _run(tmp_path, "off.json", "--stress-emphasis", "0")
    base = open(plain, "rb").read()
    assert open(neutral, "rb").read() == base
    assert open(off, "rb").read() == base


def test_cli_style_ordering_and_compose(tmp_path):
    # mumble opens the jaw less than exaggerated end-to-end; an explicit
    # --intensity composes on top of a preset and overrides its master.
    def aa_peak(path):
        d = json.load(open(path))
        ch = next(c for c in d["channels"] if c["name"] == "aa")
        return max(v for _, v in ch["keys"])
    mumble = _run(tmp_path, "mumble.json", "--style", "mumble")
    exag = _run(tmp_path, "exag.json", "--style", "exaggerated")
    assert aa_peak(mumble) < aa_peak(exag)
    # mumble + a strong explicit intensity override opens wider than mumble alone
    boosted = _run(tmp_path, "boosted.json", "--style", "mumble",
                   "--intensity", "1.6")
    assert aa_peak(boosted) > aa_peak(mumble)


def test_cli_stress_emphasis_end_to_end(tmp_path):
    # The bare flag renders (const 0.5), an explicit amount raises stressed-vowel
    # channels vs off, and an out-of-range amount is rejected at the boundary.
    from openfacefx.cli import main as cli_main
    off = _run(tmp_path, "se_off.json")
    bare = _run(tmp_path, "se_bare.json", "--stress-emphasis")
    strong = _run(tmp_path, "se_strong.json", "--stress-emphasis", "1.5")

    def total_open(path):
        d = json.load(open(path))
        return sum(max((v for _, v in c["keys"]), default=0.0)
                   for c in d["channels"] if c["name"] != "sil")
    # emphasis reshapes the track (some vowels up, some down) -- it is not a
    # no-op, and the strong pass differs from off.
    assert json.load(open(strong)) != json.load(open(off))
    assert os.path.getsize(bare) > 0
    with pytest.raises(SystemExit):
        cli_main(["naive", "--text", "hi", "--duration", "0.5",
                  "--stress-emphasis", "3", "-o", str(tmp_path / "bad.json")])
