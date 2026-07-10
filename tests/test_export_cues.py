"""Cue exporters (#16): dominant-target flattening and the Rhubarb-dialect
TSV / XML / JSON, Moho/OpenToonz .dat and Papagayo .pgo writers.

Byte-level fixtures are built from hand-authored tracks already in the target
shape vocabulary, so the expected bytes are fully determined by the writers
(no dependence on the retarget preset tables or the coarticulation solver)."""

import os
import sys

import pytest

try:
    import openfacefx  # noqa: F401
except ImportError:
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from openfacefx.cli import main as cli_main
from openfacefx.curves import Channel, FaceTrack, Keyframe
from openfacefx.export_cues import (
    RHUBARB_EXTENDED_FALLBACK, dominant_cues, write_moho_dat, write_pgo,
    write_rhubarb_json, write_rhubarb_tsv, write_rhubarb_xml,
)


def _track(fps, series):
    """A FaceTrack whose channels carry one key per frame, so the value at
    frame ``i`` is exactly ``series[name][i]`` (clean, predictable argmax)."""
    channels = [Channel(name, [Keyframe(i / fps, v) for i, v in enumerate(vals)])
                for name, vals in series.items()]
    return FaceTrack(fps=fps, channels=channels, target_set=sorted(series))


# A Rhubarb-vocab track (fps=10) exercising a dead frame (index 7: every channel
# zero) that must resolve to the silence shape X via the floor.
TRACK_A = _track(10, {
    "X": [.9, .9, 0, 0, 0, 0, 0, 0, .9, .9, .9],
    "B": [0, 0, .9, .9, .9, 0, 0, 0, 0, 0, 0],
    "F": [0, 0, 0, 0, 0, .9, .9, 0, 0, 0, 0],
})
CUES_A = [(0.0, 0.2, "X"), (0.2, 0.5, "B"), (0.5, 0.7, "F"), (0.7, 1.0, "X")]


def _read(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def test_dominant_flattening_with_silence_floor():
    cues = dominant_cues(TRACK_A, "X")
    assert cues == CUES_A                       # dead frame 7 folds into rest X
    # runs tile [0, duration] with no gaps or overlaps
    assert cues[0][0] == 0.0 and cues[-1][1] == TRACK_A.duration
    assert all(a[1] == b[0] for a, b in zip(cues, cues[1:]))


def test_rhubarb_tsv_terminal_row(tmp_path):
    path = str(tmp_path / "a.tsv")
    write_rhubarb_tsv(TRACK_A, path)
    assert _read(path) == (
        "0.00\tX\n"
        "0.20\tB\n"
        "0.50\tF\n"
        "0.70\tX\n"
        "1.00\tX\n"          # terminal row bounds the last cue at the end time
    )


def test_rhubarb_xml_structure(tmp_path):
    path = str(tmp_path / "a.xml")
    write_rhubarb_xml(TRACK_A, path)
    assert _read(path) == (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        "<rhubarbResult>\n"
        "  <metadata>\n"
        "    <soundFile>openfacefx</soundFile>\n"
        "    <duration>1.00</duration>\n"
        "  </metadata>\n"
        "  <mouthCues>\n"
        '    <mouthCue start="0.00" end="0.20">X</mouthCue>\n'
        '    <mouthCue start="0.20" end="0.50">B</mouthCue>\n'
        '    <mouthCue start="0.50" end="0.70">F</mouthCue>\n'
        '    <mouthCue start="0.70" end="1.00">X</mouthCue>\n'
        "  </mouthCues>\n"
        "</rhubarbResult>\n"
    )


def test_rhubarb_json_exact(tmp_path):
    path = str(tmp_path / "a.json")
    write_rhubarb_json(TRACK_A, path)
    text = _read(path)
    assert text == (
        "{\n"
        '  "metadata": {\n'
        '    "soundFile": "openfacefx",\n'
        '    "duration": 1.00\n'
        "  },\n"
        '  "mouthCues": [\n'
        '    { "start": 0.00, "end": 0.20, "value": "X" },\n'
        '    { "start": 0.20, "end": 0.50, "value": "B" },\n'
        '    { "start": 0.50, "end": 0.70, "value": "F" },\n'
        '    { "start": 0.70, "end": 1.00, "value": "X" }\n'
        "  ]\n"
        "}\n"
    )
    assert ",\n  ]" not in text          # no trailing comma before the ]
    import json
    json.loads(text)                     # and it is valid JSON


def test_moho_dat_truncate_dedup_and_terminal_bump(tmp_path):
    # Flatten at 50 fps, quantize to 24 fps so short runs collide: two cues land
    # on an already-emitted frame (dropped, first kept) and the end frame
    # collides with the last cue (bumped +1).
    dl = _track(50, {
        "X": [.9, .9, 0, 0, 0, 0, 0, 0, .9, 0, 0],
        "B": [0, 0, .9, 0, 0, 0, 0, 0, 0, .9, .9],
        "C": [0, 0, 0, .9, .9, .9, .9, 0, 0, 0, 0],
        "D": [0, 0, 0, 0, 0, 0, 0, .9, 0, 0, 0],
    })
    path = str(tmp_path / "l.dat")
    write_moho_dat(dl, path, fps=24, preston_blair=False)
    assert _read(path) == (
        "MohoSwitch1\n"
        "1 X\n"      # 1-based; frame = 1 + int(24 * start)
        "2 C\n"      # B at 0.04 -> frame 1, collides with X -> dropped (keep first)
        "4 D\n"
        "5 B\n"      # X at 0.16 -> frame 4, collides with D -> dropped
        "6 X\n"      # end frame 5 collides with last cue -> bumped to 6
    )


def test_moho_dat_preston_blair_names(tmp_path):
    pb = _track(10, {"rest": [.9, .9, 0, 0, .9, .9], "AI": [0, 0, .9, .9, 0, 0]})
    path = str(tmp_path / "pb.dat")
    write_moho_dat(pb, path, fps=24, preston_blair=True)
    assert _read(path) == "MohoSwitch1\n1 rest\n5 AI\n10 rest\n13 rest\n"


def test_moho_dat_fps_range_errors(tmp_path):
    path = str(tmp_path / "x.dat")
    for bad in (23, 0, 101, 240):
        with pytest.raises(ValueError, match="24..100"):
            write_moho_dat(TRACK_A, path, fps=bad, preston_blair=False)
    write_moho_dat(TRACK_A, path, fps=24, preston_blair=False)   # boundaries valid
    write_moho_dat(TRACK_A, path, fps=100, preston_blair=False)


def test_pgo_tree_indentation(tmp_path):
    pb = _track(10, {"rest": [.9, .9, 0, 0, .9, .9], "AI": [0, 0, .9, .9, 0, 0]})
    path = str(tmp_path / "v.pgo")
    write_pgo(pb, path, fps=24)
    assert _read(path) == (
        "lipsync version 1\n"
        "openfacefx\n"                 # sound path: placeholder, not a local path
        "24\n"
        "12\n"                         # duration in frames
        "1\n"                          # one voice
        "\tVoice 1\n"
        "\topenfacefx\n"
        "\t1\n"                        # one phrase
        "\t\topenfacefx\n"
        "\t\t1\n"
        "\t\t12\n"
        "\t\t1\n"                      # one word
        "\t\t\topenfacefx 1 12 3\n"    # word: text start end phoneme-count
        "\t\t\t\t1 rest\n"             # phonemes: frame shape
        "\t\t\t\t5 AI\n"
        "\t\t\t\t10 rest\n"
    )


def test_extended_shape_fallback(tmp_path):
    assert RHUBARB_EXTENDED_FALLBACK == {"G": "A", "H": "C", "X": "A"}
    ext = _track(10, {"G": [.9, .9, 0, 0, 0, 0], "H": [0, 0, .9, .9, 0, 0],
                      "X": [0, 0, 0, 0, .9, .9]})
    path = str(tmp_path / "basic.tsv")
    write_rhubarb_tsv(ext, path, available_shapes=set("ABCDEF"))
    # G->A, H->C, X->A, and the terminal rest X also collapses to A
    assert _read(path) == "0.00\tA\n0.20\tC\n0.40\tA\n0.50\tA\n"


def test_vocab_autodetect_and_errors(tmp_path):
    # Oculus visemes are retargeted to Rhubarb shapes automatically.
    oculus = _track(10, {"sil": [.9, .9, 0, 0, .9], "aa": [0, 0, .9, .9, 0]})
    path = str(tmp_path / "o.tsv")
    write_rhubarb_tsv(oculus, path)
    shapes = {line.split("\t")[1] for line in _read(path).splitlines()}
    assert shapes <= set("ABCDEFGHX") and "X" in shapes

    # A vocabulary that is neither Rhubarb nor Oculus is rejected clearly.
    weird = _track(10, {"foo": [.9, .9], "bar": [0, 0]})
    with pytest.raises(ValueError, match="neither"):
        write_rhubarb_tsv(weird, str(tmp_path / "w.tsv"))

    # Preston-Blair mode maps Oculus onto Preston-Blair drawing names.
    dat = str(tmp_path / "o.dat")
    write_moho_dat(oculus, dat, fps=24, preston_blair=True)
    names = {ln.split(" ", 1)[1] for ln in _read(dat).splitlines()[1:]}
    assert names <= {"AI", "E", "O", "U", "etc", "L", "WQ", "MBP", "FV", "rest"}


# --- CLI end-to-end: every format reachable from the generate commands --------

_PHO = "P 90\nAA1 150\nL 120\nP 90\n"
_TEXTGRID = '''File type = "ooTextFile"
Object class = "TextGrid"

xmin = 0
xmax = 0.6
tiers? <exists>
size = 1
item []:
    item [1]:
        class = "IntervalTier"
        name = "phones"
        xmin = 0
        xmax = 0.6
        intervals: size = 2
        intervals [1]:
            xmin = 0.0
            xmax = 0.3
            text = "HH"
        intervals [2]:
            xmin = 0.3
            xmax = 0.6
            text = "AH0"
'''

_SIGNATURES = {
    "o.tsv": lambda t: t.split("\t", 1)[0].replace(".", "").isdigit(),
    "o.xml": lambda t: t.startswith('<?xml version="1.0"') and "<mouthCue " in t,
    "o.dat": lambda t: t.startswith("MohoSwitch1\n"),
    "o.pgo": lambda t: t.startswith("lipsync version 1\n"),
}


def test_cli_all_cue_formats_from_naive(tmp_path):
    for name, ok in _SIGNATURES.items():
        out = str(tmp_path / name)
        rc = cli_main(["naive", "--text", "hello world this is a test",
                       "--duration", "1.6", "--fps", "24", "-o", out])
        assert rc == 0 and ok(_read(out)), name
    # json-cues is reachable only via the explicit flag (.json stays native)
    out = str(tmp_path / "cues.json")
    cli_main(["naive", "--text", "hello world", "--duration", "1.6",
              "-o", out, "--cue-format", "json-cues"])
    assert '"mouthCues"' in _read(out)


def test_cli_json_extension_stays_native(tmp_path):
    out = str(tmp_path / "t.json")
    cli_main(["naive", "--text", "hello world", "--duration", "1.2", "-o", out])
    assert '"format": "openfacefx.track"' in _read(out)


def test_cli_rejects_retarget_with_cue_format(tmp_path):
    with pytest.raises(SystemExit, match="cue formats"):
        cli_main(["naive", "--text", "x", "--duration", "0.5",
                  "-o", str(tmp_path / "x.tsv"), "--retarget", "arkit"])


def test_cli_dat_fps_out_of_range(tmp_path):
    with pytest.raises(ValueError, match="24..100"):
        cli_main(["naive", "--text", "x", "--duration", "0.5",
                  "--cue-fps", "10", "-o", str(tmp_path / "x.dat")])


def test_cli_cue_formats_from_mfa_and_from_timing(tmp_path):
    tg = tmp_path / "line.TextGrid"
    tg.write_text(_TEXTGRID)
    mfa_out = str(tmp_path / "m.pgo")
    assert cli_main(["mfa", "--textgrid", str(tg), "-o", mfa_out]) == 0
    assert _read(mfa_out).startswith("lipsync version 1\n")

    pho = tmp_path / "v.pho"
    pho.write_text(_PHO)
    ft_out = str(tmp_path / "f.dat")
    assert cli_main(["from-timing", "--file", str(pho), "--format", "pho",
                     "--cue-fps", "30", "-o", ft_out]) == 0
    assert _read(ft_out).startswith("MohoSwitch1\n")
