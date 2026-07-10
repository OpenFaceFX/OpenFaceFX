"""FaceFXWrapper.exe drop-in shim (#33).

These tests pin the CLI *contract* real consumers (xVASynth lip_fuz, Mantella,
Pantella) depend on — dispatch on argument count, input WAV at positional index 3
in both forms, output ``.lip`` at index 5 (7-arg) / 4 (6-arg), and success gated
solely on a byte-valid ``.lip`` existing at the output path — plus the honest
failure modes (unknown/Fallout4 type, unreadable WAV, bad arg count). They do NOT
prove Skyrim loads the file; the payload is experimental (#12). Byte-validity is
checked two ways: the modern-header reader accepts it, and the independent
research codec (``tools/lip_codec_research``) decodes-then-re-encodes it exactly.
"""

import os
import sys

import pytest

try:
    import openfacefx  # noqa: F401
except ImportError:
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

# The research codec is an independent decoder/re-encoder of the .lip grid, kept
# out of the shipped package (tools/), so a round-trip through it shares no code
# with the writer.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))
import lip_codec_research as codec  # noqa: E402

from openfacefx import cli, facefxwrapper  # noqa: E402
from openfacefx.bethesda import parse_lip_header  # noqa: E402
from openfacefx.export_lip import lip_bytes  # noqa: E402
from openfacefx.pipeline import naive_segments  # noqa: E402

WAV = os.path.join(os.path.dirname(__file__), "..", "examples", "voice.wav")
TEXT = "hello world"

# Bogus stand-ins for the arguments the shim accepts-and-ignores (Lang, Fonix,
# and — in the 7-arg form — ResampledWavPath). Chosen to look like flags / missing
# paths so a test fails loudly if the shim ever tried to open them or an arg
# parser tried to interpret them.
_LANG = "--USEnglish"
_FONIX = "/nonexistent/FonixData.cdf"


def _argv7(wav, lip, text=TEXT, resampled="/nonexistent/resampled.wav"):
    """7-arg resample form: Type Lang Fonix WavPath ResampledWavPath LipPath Text."""
    return ["Skyrim", _LANG, _FONIX, wav, resampled, lip, text]


def _argv6(wav, lip, text=TEXT):
    """6-arg pre-resampled form: Type Lang Fonix ResampledWavPath LipPath Text."""
    return ["Skyrim", _LANG, _FONIX, wav, lip, text]


def _assert_valid_lip(path):
    """A written .lip is byte-valid: modern header reads version 1, and the
    independent research codec decodes-then-re-encodes it byte-identically."""
    data = open(path, "rb").read()
    assert parse_lip_header(data).version == 1
    ok, rebuilt = codec.roundtrip_curves(data)
    assert ok and rebuilt == data


@pytest.mark.parametrize("builder", [_argv7, _argv6], ids=["7-arg", "6-arg"])
def test_form_writes_byte_valid_lip(tmp_path, builder):
    lip = str(tmp_path / "out.lip")
    assert facefxwrapper.run(builder(WAV, lip)) == 0
    assert os.path.exists(lip)
    _assert_valid_lip(lip)


def test_seven_arg_does_not_write_resampled_wav(tmp_path):
    """The shim must NOT create ResampledWavPath (index 4): consumers os.remove it
    only `if exists`, and we never resample."""
    lip = str(tmp_path / "out.lip")
    resampled = str(tmp_path / "should_not_appear.wav")
    assert facefxwrapper.run(_argv7(WAV, lip, resampled=resampled)) == 0
    assert os.path.exists(lip)
    assert not os.path.exists(resampled)


@pytest.mark.parametrize("builder", [_argv7, _argv6], ids=["7-arg", "6-arg"])
def test_input_wav_is_positional_index_3(tmp_path, builder):
    """The input WAV is read from index 3 in BOTH forms: a bogus WAV there fails
    even when a real WAV path sits at another (ignored) index, and the real WAV at
    index 3 succeeds regardless of what the other positions hold."""
    lip = str(tmp_path / "out.lip")
    argv = builder("/nonexistent/input.wav", lip)
    # A real WAV smuggled into an ignored slot must not rescue it.
    argv[1] = WAV  # Lang slot
    assert facefxwrapper.run(argv) == 1
    assert not os.path.exists(lip)
    # Real WAV back at index 3 -> success.
    argv[3] = WAV
    assert facefxwrapper.run(argv) == 0
    _assert_valid_lip(lip)


def test_shim_lip_matches_canonical_writer(tmp_path):
    """The bytes the shim writes are exactly the openfacefx .lip writer's output
    for the same (text, duration) — i.e. it drives the real pipeline, not a
    private encoder."""
    lip = str(tmp_path / "out.lip")
    assert facefxwrapper.run(_argv7(WAV, lip)) == 0
    from openfacefx.pipeline import wav_duration
    dur = wav_duration(WAV)
    expected = lip_bytes(naive_segments(TEXT, dur), dur, game="skyrim")
    assert open(lip, "rb").read() == expected


def test_success_is_silent(tmp_path, capsys):
    """Success returns 0 and prints nothing (the real binary is silent on success;
    consumers ignore stdout anyway, but we match it)."""
    assert facefxwrapper.run(_argv7(WAV, str(tmp_path / "out.lip"))) == 0
    assert capsys.readouterr().out == ""


def test_determinism_identical_bytes(tmp_path):
    a, b = str(tmp_path / "a.lip"), str(tmp_path / "b.lip")
    assert facefxwrapper.run(_argv7(WAV, a, text="determinism matters")) == 0
    assert facefxwrapper.run(_argv7(WAV, b, text="determinism matters")) == 0
    assert open(a, "rb").read() == open(b, "rb").read()


@pytest.mark.parametrize("game_type", ["Morrowind", "", "skyrim4", "Fallout3"])
def test_unknown_type_exits_1_no_file(tmp_path, capsys, game_type):
    lip = str(tmp_path / "out.lip")
    argv = _argv7(WAV, lip)
    argv[0] = game_type
    assert facefxwrapper.run(argv) == 1
    assert not os.path.exists(lip)
    # Prints the raw (un-lowercased) type, verbatim from the original.
    assert capsys.readouterr().out == f'Unknown generator type "{game_type}"\n'


@pytest.mark.parametrize("game_type", ["Fallout4", "fallout4", "FALLOUT4"])
def test_fallout4_exits_1_no_file(tmp_path, capsys, game_type):
    """Fallout 4 is a KNOWN type but unsupported (undocumented 43-target vocab):
    honest failure, no .lip, so the consumer uses its placeholder."""
    lip = str(tmp_path / "out.lip")
    argv = _argv7(WAV, lip)
    argv[0] = game_type
    assert facefxwrapper.run(argv) == 1
    assert not os.path.exists(lip)
    assert capsys.readouterr().out == "LIP generation failed\n"


def test_type_is_case_insensitive(tmp_path):
    lip = str(tmp_path / "out.lip")
    argv = _argv7(WAV, lip)
    argv[0] = "SKYRIM"
    assert facefxwrapper.run(argv) == 0
    _assert_valid_lip(lip)


@pytest.mark.parametrize("argv", [
    [],
    ["Skyrim"],
    ["Skyrim", "USEnglish"],
    ["Skyrim", "USEnglish", "fonix", "wav", "resampled"],            # 5 args
    ["Skyrim", "USEnglish", "f", "w", "r", "lip", "text", "extra"],  # 8 args
])
def test_bad_arg_count_exits_1_with_usage(capsys, argv):
    assert facefxwrapper.run(argv) == 1
    out = capsys.readouterr().out
    assert "Usage:" in out
    # Both accepted forms are shown; the Type is NOT validated on a bad count, so
    # even an unknown type here yields usage, not the type error.
    assert "[WavPath] [ResampledWavPath] [LipPath] [Text]" in out
    assert '"Unknown generator type"' not in out


def test_bad_arg_count_unknown_type_still_shows_usage(capsys):
    """A wrong count wins over the type check (matches the original's switch)."""
    assert facefxwrapper.run(["Morrowind", "USEnglish"]) == 1
    out = capsys.readouterr().out
    assert "Usage:" in out and "Unknown generator type" not in out


def test_unreadable_wav_exits_1_no_file(tmp_path, capsys):
    lip = str(tmp_path / "out.lip")
    assert facefxwrapper.run(_argv7("/nonexistent/missing.wav", lip)) == 1
    assert not os.path.exists(lip)
    assert capsys.readouterr().out == "LIP generation failed\n"


def test_cli_intercept_before_argparse(tmp_path):
    """`openfacefx facefxwrapper ...` is dispatched BEFORE argparse, so raw
    positional args pass through verbatim — including a leading-dash token argparse
    would treat as an option and text with spaces."""
    lip = str(tmp_path / "out.lip")
    rc = cli.main(["facefxwrapper", "Skyrim", "USEnglish", "--Fonix.cdf", WAV,
                   str(tmp_path / "resampled.wav"), lip, "-- a spaced, dashed line --"])
    assert rc == 0
    _assert_valid_lip(lip)


def test_cli_intercept_bad_count_returns_not_raises(capsys):
    """The intercept returns the shim's exit code; it must not raise argparse's
    SystemExit for a bare `facefxwrapper`."""
    assert cli.main(["facefxwrapper"]) == 1
    assert "Usage:" in capsys.readouterr().out


def test_console_entry_point(tmp_path, monkeypatch):
    """The `facefxwrapper` console script reads sys.argv[1:] and returns the code."""
    lip = str(tmp_path / "out.lip")
    monkeypatch.setattr(sys, "argv", ["FaceFXWrapper.exe"] + _argv6(WAV, lip))
    assert facefxwrapper._console() == 0
    _assert_valid_lip(lip)
