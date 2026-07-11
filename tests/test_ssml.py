"""SSML input adapter (issue #52) — a thin front-end over the #7 text tags.

Pins the acceptance: for each supported construct an SSML document yields the
**same** ``(clean_text, tags)`` as the equivalent bracket-tag transcript (so it
inherits #7's downstream behaviour unchanged); ``<speak>hello world</speak>`` is
byte-identical to plain ``naive --text "hello world"``; malformed XML raises a
clear ``ValueError`` and unknown elements pass through as text; and the parse is
deterministic and fully opt-in.
"""

import os
import sys

import pytest

try:
    import openfacefx  # noqa: F401  (installed wheel wins; see test_core)
except ImportError:
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from openfacefx.cli import main as cli_main
from openfacefx.io_export import read_json
from openfacefx.ssml import (BREAK_STRENGTH, EMPHASIS_STRENGTH, looks_like_ssml,
                             parse_ssml)
from openfacefx.texttags import parse_tagged_transcript as ptt


# --------------------------------------------------------------------------- #
# construct-by-construct: SSML == the equivalent bracket-tag transcript        #
# --------------------------------------------------------------------------- #

# (label, ssml document, the bracket transcript that must parse identically)
_EQUIV = [
    ("break-ms",    '<speak>hello <break time="500ms"/> world</speak>',
                    "hello [pause:0.5] world"),
    ("break-s",     '<speak>a <break time="2s"/> b</speak>', "a [pause:2.0] b"),
    ("break-str",   '<speak>a <break strength="strong"/> b</speak>',
                    "a [pause:0.75] b"),
    ("emph-strong", '<speak>say <emphasis level="strong">brave</emphasis> now</speak>',
                    "say [emphasis strength=1.0]brave[/emphasis] now"),
    ("emph-mod",    '<speak><emphasis level="moderate">brave</emphasis></speak>',
                    "[emphasis strength=0.5]brave[/emphasis]"),
    ("emph-none",   '<speak>x <emphasis>brave</emphasis> y</speak>',
                    "x [emphasis]brave[/emphasis] y"),
    ("sub-alias",   '<speak><sub alias="World Health Organization">WHO</sub></speak>',
                    "World Health Organization"),
    ("mark-named",  '<speak>hit <mark name="hit"/> it</speak>',
                    "hit [mark name=hit] it"),
    ("phrase-p-s",  '<speak><s>a</s> <s>b</s></speak>', "[phrase]a [phrase]b"),
    ("say-as",      '<speak><say-as interpret-as="date">2024—2025</say-as></speak>',
                    "2024--2025"),
    ("phoneme-def", '<speak><phoneme alphabet="ipa" ph="x">hello</phoneme></speak>',
                    "hello"),
    ("unknown-tag", '<speak>hello <prosody rate="slow">world</prosody></speak>',
                    "hello world"),
]


@pytest.mark.parametrize("label, ssml, bracket",
                         _EQUIV, ids=[c[0] for c in _EQUIV])
def test_ssml_equals_bracket_transcript(label, ssml, bracket):
    assert parse_ssml(ssml) == ptt(bracket)


def test_speak_hello_world_is_a_noop():
    # no constructs -> plain text + empty tag list (the byte-identity foundation)
    assert parse_ssml("<speak>hello world</speak>") == ("hello world", [])


# --------------------------------------------------------------------------- #
# mapping details                                                             #
# --------------------------------------------------------------------------- #

def test_emphasis_level_strength_table():
    # strong/moderate map to explicit strengths; reduced/none floor at 0 (this
    # pass can only amplify); a level-less emphasis carries NO strength param so
    # the #7 default applies downstream.
    assert EMPHASIS_STRENGTH == {"strong": "1.0", "moderate": "0.5",
                                 "reduced": "0.0", "none": "0.0"}
    _, [tag] = parse_ssml('<speak><emphasis level="reduced">x</emphasis></speak>')
    assert tag.kind == "emphasis" and tag.params == {"strength": "0.0"}
    _, [bare] = parse_ssml("<speak><emphasis>x</emphasis></speak>")
    assert bare.params == {}
    _, [unknown] = parse_ssml('<speak><emphasis level="huge">x</emphasis></speak>')
    assert unknown.params == {}                       # unknown level -> default


def test_break_time_units_and_strength():
    def pause_val(ssml):
        _, [t] = parse_ssml(ssml)
        assert t.kind == "pause"
        return t.value
    assert pause_val('<speak>a<break time="500ms"/>b</speak>') == 0.5
    assert pause_val('<speak>a<break time="2s"/>b</speak>') == 2.0
    assert pause_val('<speak>a<break/>b</speak>') == BREAK_STRENGTH["medium"]
    assert pause_val('<speak>a<break strength="x-strong"/>b</speak>') == 1.0
    # time wins over strength when both are present
    assert pause_val('<speak>a<break time="100ms" strength="strong"/>b</speak>') == 0.1


def test_sub_without_alias_speaks_the_element_text():
    assert parse_ssml("<speak><sub>WHO</sub></speak>") == ("WHO", [])


def test_namespaced_document_matches_bare():
    ns = ('<speak xmlns="http://www.w3.org/2001/10/synthesis">say '
          '<emphasis level="strong">brave</emphasis> now</speak>')
    assert parse_ssml(ns) == parse_ssml(
        '<speak>say <emphasis level="strong">brave</emphasis> now</speak>')


# --------------------------------------------------------------------------- #
# robustness: malformed -> ValueError, determinism, detection                 #
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("bad", ["<speak>hello", "not xml at all", "",
                                 "<speak><!DOCTYPE x>y</speak>"])
def test_malformed_xml_raises_valueerror(bad):
    with pytest.raises(ValueError):
        parse_ssml(bad)


def test_parse_is_deterministic():
    doc = ('<speak>Say <emphasis level="strong">brave</emphasis> '
           '<break time="300ms"/> new <mark name="beat"/> world</speak>')
    assert parse_ssml(doc) == parse_ssml(doc)


def test_looks_like_ssml_detection():
    assert looks_like_ssml("<speak>x</speak>")
    assert looks_like_ssml('  <?xml version="1.0"?>\n<speak>x</speak>')
    assert looks_like_ssml('<speak xmlns="urn:x">x</speak>')
    assert not looks_like_ssml("hello world")
    assert not looks_like_ssml("[emphasis]x[/emphasis]")
    assert not looks_like_ssml("")


# --------------------------------------------------------------------------- #
# CLI: opt-in, byte-identical no-op, and construct == bracket byte-for-byte    #
# --------------------------------------------------------------------------- #

def test_cli_ssml_flag_is_byte_identical_to_plain(tmp_path):
    plain, ssml = str(tmp_path / "p.json"), str(tmp_path / "s.json")
    assert cli_main(["naive", "--text", "hello world",
                     "--duration", "1.5", "-o", plain]) == 0
    assert cli_main(["naive", "--ssml", "--text", "<speak>hello world</speak>",
                     "--duration", "1.5", "-o", ssml]) == 0
    assert open(plain, "rb").read() == open(ssml, "rb").read()


def test_cli_ssml_auto_detected_from_speak_root(tmp_path):
    plain, auto = str(tmp_path / "p.json"), str(tmp_path / "a.json")
    assert cli_main(["naive", "--text", "hello world",
                     "--duration", "1.5", "-o", plain]) == 0
    # no --ssml flag: the <speak> root auto-enables the adapter
    assert cli_main(["naive", "--text", "<speak>hello world</speak>",
                     "--duration", "1.5", "-o", auto]) == 0
    assert open(plain, "rb").read() == open(auto, "rb").read()


def test_cli_ssml_break_byte_identical_to_bracket_pause(tmp_path):
    # SSML with a construct == the equivalent bracket tags, byte-for-byte through
    # the whole pipeline (the pause splices silence, extending duration equally).
    a, b = str(tmp_path / "ssml.json"), str(tmp_path / "brkt.json")
    assert cli_main(["naive", "--ssml", "--duration", "1.0",
                     "--text", '<speak>a <break time="0.5s"/> b</speak>',
                     "-o", a]) == 0
    assert cli_main(["naive", "--tags", "--duration", "1.0",
                     "--text", "a [pause:0.5] b", "-o", b]) == 0
    assert open(a, "rb").read() == open(b, "rb").read()
    assert read_json(a).duration == pytest.approx(1.5)   # 1.0 base + 0.5 pause


def test_cli_ssml_emphasis_byte_identical_to_bracket(tmp_path):
    a, b = str(tmp_path / "ssml.json"), str(tmp_path / "brkt.json")
    assert cli_main(["naive", "--ssml", "--duration", "2.0", "--text",
                     '<speak>say <emphasis level="strong">brave</emphasis> now</speak>',
                     "-o", a]) == 0
    assert cli_main(["naive", "--tags", "--duration", "2.0", "--text",
                     "say [emphasis strength=1.0]brave[/emphasis] now",
                     "-o", b]) == 0
    assert open(a, "rb").read() == open(b, "rb").read()


def test_cli_ssml_rejects_lip_output(tmp_path):
    # SSML routes through the #7 tag path, which the phoneme .lip format can't carry
    with pytest.raises(SystemExit):
        cli_main(["naive", "--ssml", "--duration", "1.0",
                  "--text", '<speak>hello <break time="200ms"/> world</speak>',
                  "-o", str(tmp_path / "out.lip")])
