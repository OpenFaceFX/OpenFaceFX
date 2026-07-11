"""Loc-table / dialogue-database manifest batch driver (issue #40).

Pins the acceptance: a CSV/TSV manifest of N rows produces N tracks at the
manifest-specified output paths; a missing-audio / unreadable / bad row is a
per-row failure that does not abort the batch and shows up in the summary +
NDJSON + ledger; the language / character / mapping / style columns thread into
each row's solve; and the directory-walk mode is byte-identical when
``--manifest`` is absent (no manifest key leaks into a directory row, its
fingerprint or the ledger). Stdlib-only parsing.
"""

import json
import os
import struct
import sys
import wave

import pytest

try:
    import openfacefx  # noqa: F401
except ImportError:
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from openfacefx.batch import run_batch
from openfacefx.batch_manifest import manifest_jobs, read_manifest
from openfacefx.cli import main as cli_main
from openfacefx.io_export import read_json


def _wav(path, seconds=0.4, rate=16000):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(struct.pack("<h", 0) * int(seconds * rate))


def _rename_mapping(path, prefix="X_"):
    """A valid mapping that prefixes every default target name, so a track built
    with it has all-``X_``-prefixed channels -- an observable per-row solve."""
    from openfacefx.mapping import Mapping, Target
    m = Mapping.default()
    targets = [Target(prefix + t.name, t.articulator, t.lo, t.hi)
               for t in m.targets]
    rows = {ph: {prefix + k: v for k, v in w.items()} for ph, w in m.rows.items()}
    Mapping(targets, rows).to_json(path)


def _manifest(tmp_path, text, extra_rows=(), name="loc.csv"):
    """Two good rows (hello/greet) + any extra rows; audio under ``audio/``."""
    for stem in ("hello", "greet"):
        _wav(str(tmp_path / "audio" / (stem + ".wav")))
    lines = ["id,audio,text,language,character,mapping,style,out",
             "greet_01,audio/hello.wav,%s,en,Guard,,," % text,
             "greet_02,audio/greet.wav,good morning,en,Guard,,,"]
    lines.extend(extra_rows)
    p = tmp_path / name
    p.write_text("\n".join(lines) + "\n")
    return str(p)


# --------------------------------------------------------------------------- #
# N rows -> N tracks; explicit + derived output paths                          #
# --------------------------------------------------------------------------- #

def test_n_rows_produce_n_tracks_at_specified_paths(tmp_path):
    _wav(str(tmp_path / "audio" / "third.wav"))
    manifest = _manifest(tmp_path, "hello world", extra_rows=[
        "quest_intro,audio/third.wav,farewell friend,en,Mage,,,quests/intro.json"])
    out = tmp_path / "out"
    assert cli_main(["batch", "--manifest", manifest, "--out", str(out),
                     "--quiet"]) == 0
    # derived <id>.json for the first two, the explicit out= path for the third
    assert read_json(str(out / "greet_01.json")).duration > 0
    assert read_json(str(out / "greet_02.json")).duration > 0
    assert read_json(str(out / "quests" / "intro.json")).duration > 0   # subdir
    summary = json.loads((out / "batch_summary.json").read_text())
    assert summary["processed"] == 3 and summary["failed"] == 0


# --------------------------------------------------------------------------- #
# per-row failure isolation                                                    #
# --------------------------------------------------------------------------- #

def test_missing_audio_row_is_isolated_failure(capsys, tmp_path):
    manifest = _manifest(tmp_path, "hello world", extra_rows=[
        "broken_01,audio/GONE.wav,no audio here,en,Guard,,,"])
    out = tmp_path / "out"
    ledger = tmp_path / "runs.ndjson"
    rc = cli_main(["batch", "--manifest", manifest, "--out", str(out),
                   "--machine-readable", "--ledger", str(ledger)])
    err = capsys.readouterr().err
    assert rc == 1                                    # one bad row -> exit 1
    # the two good rows still produced tracks
    assert read_json(str(out / "greet_01.json")).duration > 0
    assert read_json(str(out / "greet_02.json")).duration > 0
    summary = json.loads((out / "batch_summary.json").read_text())
    assert summary["processed"] == 3 and summary["failed"] == 1
    bad = next(r for r in summary["rows"] if r["file"] == "broken_01")
    assert bad["status"] == "failed" and bad["channels"] == 0
    # surfaced in the NDJSON stream and the ledger outcome
    events = [json.loads(ln) for ln in err.splitlines() if ln.strip()]
    assert any(e["event"] == "failure" and e["file"] == "broken_01"
               for e in events)
    rec = json.loads(ledger.read_text().splitlines()[0])
    assert rec["outcome"] == {"processed": 3, "failed": 1, "skipped": 0,
                              "exit": 1}


def test_bad_mapping_and_style_rows_fail_in_isolation(tmp_path):
    _wav(str(tmp_path / "audio" / "a.wav"))
    _wav(str(tmp_path / "audio" / "b.wav"))
    manifest = _manifest(tmp_path, "hello world", extra_rows=[
        "bad_map,audio/a.wav,hi there,en,Guard,does_not_exist.json,,",
        "bad_style,audio/b.wav,hi there,en,Guard,,no_such_style,"])
    out = tmp_path / "out"
    assert cli_main(["batch", "--manifest", manifest, "--out", str(out),
                     "--quiet"]) == 1
    summary = json.loads((out / "batch_summary.json").read_text())
    rows = {r["file"]: r for r in summary["rows"]}
    assert rows["bad_map"]["status"] == "failed"      # mapping reached the solve
    assert rows["bad_style"]["status"] == "failed"    # style reached the solve
    assert rows["greet_01"]["status"] == "ok"         # good rows unaffected


# --------------------------------------------------------------------------- #
# columns thread into the per-row solve                                        #
# --------------------------------------------------------------------------- #

def test_language_and_character_threaded_onto_rows(tmp_path):
    manifest = _manifest(tmp_path, "hello world")
    out = tmp_path / "out"
    assert cli_main(["batch", "--manifest", manifest, "--out", str(out),
                     "--quiet"]) == 0
    rows = {r["file"]: r
            for r in json.loads((out / "batch_summary.json").read_text())["rows"]}
    assert rows["greet_01"]["language"] == "en"
    assert rows["greet_01"]["character"] == "Guard"
    assert rows["greet_01"]["id"] == "greet_01"


def test_per_row_mapping_is_applied(tmp_path):
    _wav(str(tmp_path / "audio" / "m.wav"))
    _rename_mapping(str(tmp_path / "rig.json"))
    manifest = _manifest(tmp_path, "hello world", extra_rows=[
        "mapped_01,audio/m.wav,hello world,en,Guard,rig.json,,"])
    out = tmp_path / "out"
    assert cli_main(["batch", "--manifest", manifest, "--out", str(out),
                     "--quiet"]) == 0
    mapped = read_json(str(out / "mapped_01.json"))
    plain = read_json(str(out / "greet_01.json"))
    # the per-row mapping renamed every channel; the default-rig row did not
    assert mapped.channels and all(c.name.startswith("X_") for c in mapped.channels)
    assert not any(c.name.startswith("X_") for c in plain.channels)


def test_per_row_style_changes_the_solve(tmp_path):
    _wav(str(tmp_path / "audio" / "s.wav"))
    manifest = _manifest(tmp_path, "hello world", extra_rows=[
        "styled_01,audio/s.wav,hello world,en,Mage,,whisper,"])
    out = tmp_path / "out"
    assert cli_main(["batch", "--manifest", manifest, "--out", str(out),
                     "--quiet"]) == 0
    # same words + duration as greet_01, but the whisper style alters the curves
    styled = (out / "styled_01.json").read_bytes()
    plain = (out / "greet_01.json").read_bytes()
    assert styled != plain


# --------------------------------------------------------------------------- #
# directory mode is byte-identical when --manifest is absent                   #
# --------------------------------------------------------------------------- #

def _dir_tree(tmp_path):
    src = tmp_path / "src"
    (src).mkdir()
    for stem, text in (("hello", "hello world"), ("bye", "farewell friend")):
        _wav(str(src / (stem + ".wav")))
        (src / (stem + ".txt")).write_text(text)
    return src


def test_directory_mode_has_no_manifest_leakage(tmp_path):
    """The #40 additions are all gated on manifest jobs: a directory-walk row,
    its modified-only fingerprint and the ledger snapshot carry none of the new
    keys, so the directory output bytes are unchanged."""
    src = _dir_tree(tmp_path)
    out = tmp_path / "out"
    ledger = tmp_path / "l.ndjson"
    assert cli_main(["batch", "--dir", str(src), "--out", str(out),
                     "--ledger", str(ledger), "--quiet"]) == 0
    summary = json.loads((out / "batch_summary.json").read_text())
    for r in summary["rows"]:                         # no id/language/character
        assert "id" not in r and "language" not in r and "character" not in r
    fp = json.loads((out / ".openfacefx-manifest.json").read_text())
    for entry in fp.values():                         # exactly the pre-#40 keys
        assert set(entry) == {"wav", "transcript", "mapping", "out"}
    rec = json.loads(ledger.read_text().splitlines()[0])
    assert "manifest" not in rec["args"]              # snapshot unchanged


def test_directory_mode_is_deterministic_across_runs(tmp_path):
    src = _dir_tree(tmp_path)
    out = tmp_path / "out"
    assert cli_main(["batch", "--dir", str(src), "--out", str(out),
                     "--quiet"]) == 0
    first = (out / "batch_summary.json").read_bytes()
    first_track = (out / "hello.json").read_bytes()
    assert cli_main(["batch", "--dir", str(src), "--out", str(out),
                     "--quiet"]) == 0
    assert (out / "batch_summary.json").read_bytes() == first
    assert (out / "hello.json").read_bytes() == first_track


# --------------------------------------------------------------------------- #
# reader: aliases / TSV / stdlib, malformed handling, CLI validation           #
# --------------------------------------------------------------------------- #

def test_read_manifest_aliases_tsv_and_blank_cells(tmp_path):
    # forgiving headers (Key/Voice/Line/Locale/Speaker), tab-delimited
    p = tmp_path / "table.tsv"
    p.write_text("Key\tVoice\tLine\tLocale\tSpeaker\n"
                 "q1\tsnd/a.wav\thello\tfr\tNPC\n"
                 "q2\tsnd/b.wav\t\ten\t\n")
    rows = read_manifest(str(p))
    assert rows[0] == {"id": "q1", "audio": "snd/a.wav", "text": "hello",
                       "language": "fr", "character": "NPC", "mapping": None,
                       "style": None, "textgrid": None, "out": None}
    assert rows[1]["text"] is None and rows[1]["character"] is None   # blank->None


def test_manifest_jobs_derive_and_resolve_paths():
    rows = [{"id": "a/b:c", "audio": "v.wav", "text": "hi", "language": None,
             "character": None, "mapping": None, "style": None,
             "textgrid": None, "out": None}]
    jobs = manifest_jobs(rows, out_dir="OUT", ext="json", base_dir="BASE")
    assert jobs[0]["wav"] == os.path.join("BASE", "v.wav")
    assert jobs[0]["out_rel"] == "a_b_c.json"          # sanitized flat stem
    assert jobs[0]["out"] == os.path.join("OUT", "a_b_c.json")


def test_stdlib_only_manifest_parsing():
    import inspect
    from openfacefx import batch_manifest
    src = inspect.getsource(batch_manifest)
    assert "numpy" not in src and "import numpy" not in src


def test_malformed_manifest_is_a_clean_failure(capsys, tmp_path):
    bad = tmp_path / "bad.csv"
    bad.write_text("")                                # no header row
    with pytest.raises(ValueError):
        read_manifest(str(bad))
    # via run_batch it degrades to exit 1 + a message, not a crash
    rc = run_batch(None, str(tmp_path / "out"), manifest_file=str(bad))
    assert rc == 1
    assert "cannot read manifest" in capsys.readouterr().out


def test_cli_requires_exactly_one_of_dir_or_manifest(tmp_path):
    with pytest.raises(SystemExit):                   # neither
        cli_main(["batch", "--out", str(tmp_path / "o")])
    with pytest.raises(SystemExit):                   # both
        cli_main(["batch", "--dir", str(tmp_path), "--manifest",
                  str(tmp_path / "m.csv"), "--out", str(tmp_path / "o")])


def test_run_batch_manifest_library_call(tmp_path):
    manifest = _manifest(tmp_path, "hello world")
    rc = run_batch(None, str(tmp_path / "out"), manifest_file=manifest,
                   quiet=True)
    assert rc == 0
    summary = json.loads((tmp_path / "out" / "batch_summary.json").read_text())
    assert summary["processed"] == 2 and summary["failed"] == 0
