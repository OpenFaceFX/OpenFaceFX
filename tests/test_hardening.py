"""Boundary-hardening regression tests (F1-F5): malformed external input at the
parser/loader boundaries must raise a clear ``ValueError`` (or ``SystemExit`` at
the CLI) naming the offending field/index — not a bare KeyError/TypeError/
AttributeError that is opaque AND slips past the CLI's ``except (OSError,
ValueError)`` handlers. Valid input is unaffected (the full prior suite is the
byte-identity guard); these lock in that the malformed cases now fail loudly."""

import os
import sys

import pytest

try:
    import openfacefx  # noqa: F401
except ImportError:
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from openfacefx.alignment import PhonemeSegment
from openfacefx.cli import main as cli_main
from openfacefx.events import event_from_dict, variants_from_dict
from openfacefx.importers import (parse_rhubarb_json, parse_rhubarb_tsv,
                                  parse_rhubarb_xml)
from openfacefx.io_export import from_dict
from openfacefx.mapping import Mapping
from openfacefx.pipeline import (generate_from_alignment, generate_naive,
                                 naive_segments)

_TRACK = {"format": "openfacefx.track", "version": 1}


# --------------------------------------------------------------------------- #
# F1 — io_export.from_dict (the .track.json boundary, reached by read_json)     #
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("d, match", [
    ({"version": 1, "fps": 60}, "format 'openfacefx.track'"),
    ({**_TRACK, "channels": []}, "missing required 'fps'"),
    ({**_TRACK, "fps": "fast", "channels": []}, "'fps' must be a number"),
    ({**_TRACK, "fps": 60, "channels": {}}, "'channels' must be a list"),
    ({**_TRACK, "fps": 60, "channels": [1, 2]}, "channel 0 must be an object"),
    ({**_TRACK, "fps": 60, "channels": [{"keys": []}]}, "channel 0 missing required"),
    ({**_TRACK, "fps": 60, "channels": [{"name": "aa"}]}, "channel 0 missing required"),
    ({**_TRACK, "fps": 60, "channels": [{"name": "aa", "keys": [[0.0, 1.0, 9.0]]}]},
     r"channel 0 \('aa'\) key 0"),
    ({**_TRACK, "fps": 60, "channels": [{"name": "aa", "keys": [["x", "y"]]}]},
     r"channel 0 \('aa'\) key 0"),
])
def test_f1_from_dict_malformed_raises_valueerror(d, match):
    with pytest.raises(ValueError, match=match):
        from_dict(d)


def test_f1_from_dict_valid_still_parses():
    t = from_dict({**_TRACK, "fps": 60, "channels": [{"name": "aa", "keys": [[0.0, 1.0]]}]})
    assert t.fps == 60.0 and t.channels[0].name == "aa"


# --------------------------------------------------------------------------- #
# F2 — events.event_from_dict / variants_from_dict (same .track.json boundary)  #
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("ev, match", [
    ({"type": "marker", "name": "x"}, "missing required 't'"),
    ({"t": 0.1, "name": "x"}, "missing required 'type'"),
    ({"t": 0.1, "type": "marker"}, "missing required 'name'"),
    (["not", "an", "object"], "expected an object"),
])
def test_f2_event_from_dict_malformed_raises(ev, match):
    with pytest.raises(ValueError, match=match):
        event_from_dict(ev)


def test_f2_variants_missing_group_raises():
    with pytest.raises(ValueError, match="variant group 0: missing required 'group'"):
        variants_from_dict({"line_id": "a", "groups": [{"alternatives": []}]})


def test_f2_valid_event_roundtrips():
    e = event_from_dict({"t": 0.5, "type": "marker", "name": "beat"})
    assert e.t == 0.5 and e.type == "marker" and e.name == "beat"


# --------------------------------------------------------------------------- #
# F3 — importers rhubarb cue parsers (the from-cues boundary)                   #
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("text, match", [
    ('{"mouthCues": 42}', "'mouthCues' must be a list"),
    ('{"mouthCues": [123]}', "cue 0 must be an object"),
    ('{"mouthCues": [{"end": 1.0, "value": "A"}]}', "cue 0 missing required"),
    ('{"mouthCues": [{"start": 0.0, "end": 1.0}]}', "cue 0 missing required"),
    ('{"mouthCues": [{"start": "x", "end": 1.0, "value": "A"}]}', "cue 0 has non-numeric"),
])
def test_f3_rhubarb_json_malformed_raises(text, match):
    with pytest.raises(ValueError, match=match):
        parse_rhubarb_json(text)


def test_f3_rhubarb_xml_missing_attr_raises():
    with pytest.raises(ValueError, match="missing 'start'/'end' attribute"):
        parse_rhubarb_xml("<rhubarb><mouthCues><mouthCue>A</mouthCue></mouthCues></rhubarb>")


def test_f3_rhubarb_tsv_names_the_line():
    with pytest.raises(ValueError, match="rhubarb tsv line 1: non-numeric start"):
        parse_rhubarb_tsv("abc\tA\n")


# --------------------------------------------------------------------------- #
# F4 — fps/duration validation (silent empty/NaN track today)                   #
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("fps", [0.0, -30.0, float("inf"), float("nan")])
def test_f4_generate_naive_bad_fps_raises(fps):
    with pytest.raises(ValueError, match="fps must be a finite value > 0"):
        generate_naive("hello world", 1.0, fps=fps)


@pytest.mark.parametrize("dur", [0.0, -1.0, float("inf"), float("nan")])
def test_f4_generate_naive_bad_duration_raises(dur):
    with pytest.raises(ValueError, match="duration must be a finite value > 0"):
        generate_naive("hello world", dur, fps=60.0)


@pytest.mark.parametrize("fps", [0.0, -1.0])
def test_f4_generate_from_alignment_bad_fps_raises(fps):
    with pytest.raises(ValueError, match="fps must be a finite value > 0"):
        generate_from_alignment([PhonemeSegment("P", 0.0, 0.1)], fps=fps)


def test_f4_naive_segments_bad_duration_raises():
    with pytest.raises(ValueError, match="duration must be a finite value > 0"):
        naive_segments("hello", 0.0)


@pytest.mark.parametrize("flag,val", [("--fps", "0"), ("--fps", "-5"), ("--duration", "0")])
def test_f4_cli_rejects_bad_fps_duration(tmp_path, flag, val):
    argv = ["naive", "--text", "hi", "--duration", "1", "-o", str(tmp_path / "o.json")]
    # replace the flag under test (keep a valid --duration when testing --fps)
    if flag == "--duration":
        argv[argv.index("--duration") + 1] = val
    else:
        argv += [flag, val]
    with pytest.raises(SystemExit) as exc:
        cli_main(argv)
    assert exc.value.code == 2                          # argparse usage error


def test_f4_valid_fps_duration_unchanged(tmp_path):
    t = generate_naive("hello world", 1.0, fps=60.0)
    assert t.fps == 60.0 and t.duration > 0.0


# --------------------------------------------------------------------------- #
# F5 — mapping.__post_init__ / from_json (the --mapping boundary)               #
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("rows, match", [
    ({"P": 1.0, "sil": {}}, r"phoneme 'P': must map target->weight"),
    ({"P": ["PP"], "sil": {}}, r"phoneme 'P': must map target->weight"),
])
def test_f5_mapping_row_not_a_dict_raises(rows, match):
    from openfacefx.mapping import Target
    with pytest.raises(ValueError, match=match):
        Mapping([Target("PP")], rows)


def test_f5_from_json_non_numeric_target_field_named(tmp_path):
    import json
    p = tmp_path / "m.json"
    p.write_text(json.dumps({"format": "openfacefx.mapping", "version": 2,
                             "targets": [{"name": "PP", "min": "low"}],
                             "phonemes": {"P": {"PP": 1.0}}}))
    with pytest.raises(ValueError, match="malformed targets entry"):
        Mapping.from_json(str(p))


# --------------------------------------------------------------------------- #
# End-to-end: a malformed file is a clean SystemExit (the CLI's                 #
# except (OSError, ValueError) handler), NOT a raw traceback — the point of     #
# wrapping the loaders to raise ValueError.                                     #
# --------------------------------------------------------------------------- #

def test_cli_missing_fps_track_is_clean_systemexit(tmp_path):
    bad = tmp_path / "bad.track.json"
    bad.write_text('{"format": "openfacefx.track", "version": 1, "channels": []}')
    with pytest.raises(SystemExit) as exc:
        cli_main(["convert", str(bad), "-o", str(tmp_path / "o.csv")])
    assert exc.value.code != 0


def test_cli_malformed_cue_is_clean_systemexit(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text('{"mouthCues": [{"end": 1.0, "value": "A"}]}')   # cue missing 'start'
    with pytest.raises(SystemExit) as exc:
        cli_main(["from-cues", str(bad), "--format", "json-cues",
                  "-o", str(tmp_path / "o.json")])
    assert exc.value.code != 0
