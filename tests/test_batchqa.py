"""Batch-layer QA follow-up (issue #35): the ``--machine-readable`` NDJSON event
stream, the append-only ``--ledger``, and ``--cue-warnings`` folded into the
worst-first summary.

The load-bearing invariant, exactly as for #23's generate-side flags, is
*additive*: without the new flags the printed table and ``batch_summary.json``
are byte-identical to before, and the NDJSON is an opt-in stderr overlay a
supervising process parses instead of scraping the human table."""

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

from openfacefx.cli import main as cli_main
from openfacefx.batch import run_batch

# One MFA TextGrid with a deliberately too-short (20 ms) and too-long (680 ms)
# phoneme, plus one in-bounds -- deterministic cue_flags: 1 short + 1 long = 2.
TEXTGRID_CUES = '''File type = "ooTextFile"
Object class = "TextGrid"
xmin = 0
xmax = 1.0
tiers? <exists>
size = 1
item []:
    item [1]:
        class = "IntervalTier"
        name = "phones"
        xmin = 0
        xmax = 1.0
        intervals: size = 3
        intervals [1]:
            xmin = 0.0
            xmax = 0.02
            text = "HH"
        intervals [2]:
            xmin = 0.02
            xmax = 0.7
            text = "OW1"
        intervals [3]:
            xmin = 0.7
            xmax = 1.0
            text = "P"
'''


def _write_wav(path, seconds=0.4, rate=16000):
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(struct.pack("<h", 0) * int(seconds * rate))


def _tree(tmp_path):
    """A mixed fixture tree: naive .txt (one with OOV words), an MFA TextGrid
    with out-of-bounds cues, and a transcript-less .wav that must fail."""
    src = tmp_path / "src"
    (src / "quests").mkdir(parents=True)
    for rel, text in {"hello.wav": "hello world",
                      "quests/zorblat.wav": "the zorblat awakens"}.items():
        p = src / rel
        _write_wav(str(p))
        p.with_suffix(".txt").write_text(text)
    _write_wav(str(src / "aligned.wav"))
    (src / "aligned.TextGrid").write_text(TEXTGRID_CUES)
    _write_wav(str(src / "broken.wav"))          # no transcript -> failure
    return src, tmp_path / "out"


def _ndjson(err):
    """Parse a captured stderr blob as NDJSON, asserting every line is a JSON
    object (raises on the first malformed line)."""
    objs = [json.loads(ln) for ln in err.splitlines() if ln.strip()]
    assert objs, "expected at least one NDJSON event on stderr"
    return objs


# --------------------------------------------------------------------------- #
# --machine-readable NDJSON event stream                                       #
# --------------------------------------------------------------------------- #

PROGRESS_KEYS = {"event", "index", "file", "out", "status", "mode", "channels",
                 "keyframes", "oov", "cue_warnings", "min_confidence",
                 "warnings"}


def test_machine_readable_streams_valid_ndjson_to_stderr(capsys, tmp_path):
    src, out = _tree(tmp_path)
    rc = cli_main(["batch", "--dir", str(src), "--out", str(out), "--recurse",
                   "--machine-readable"])
    cap = capsys.readouterr()
    assert rc == 1                                   # broken.wav fails
    events = _ndjson(cap.err)
    kinds = [e["event"] for e in events]
    # a well-formed stream: exactly one start (first), one done (last)
    assert kinds[0] == "start" and kinds[-1] == "done"
    assert kinds.count("start") == 1 and kinds.count("done") == 1

    start = events[0]
    assert start["total"] == 4 and start["todo"] == 4 and start["skipped"] == 0
    done = events[-1]
    assert done["processed"] == 4 and done["failed"] == 1 and done["exit"] == 1

    progress = [e for e in events if e["event"] == "progress"]
    assert len(progress) == 4                        # one per processed file
    for pe in progress:
        assert set(pe) == PROGRESS_KEYS              # documented, fixed field set
        assert pe["status"] in ("ok", "failed")
        assert isinstance(pe["oov"], list)
        assert isinstance(pe["warnings"], list)
    # progress is emitted in processing order (os.walk/sorted), 0-based index
    assert [pe["index"] for pe in progress] == [0, 1, 2, 3]

    # the transcript-less file yields a dedicated failure event
    failures = [e for e in events if e["event"] == "failure"]
    assert len(failures) == 1 and failures[0]["file"].startswith("broken")
    assert "no transcript" in failures[0]["error"]

    # the OOV file yields a warning event mirroring its progress.warnings
    warns = [e for e in events if e["event"] == "warning"]
    assert any("zorblat" in w["message"] and "G2P" in w["message"]
               for w in warns)


def test_machine_readable_leaves_stdout_table_and_summary_identical(capsys,
                                                                     tmp_path):
    """--machine-readable only *adds* the stderr stream: the human table on
    stdout and batch_summary.json are byte-for-byte what the plain run emits."""
    src, out = _tree(tmp_path)
    assert cli_main(["batch", "--dir", str(src), "--out", str(out),
                     "--recurse"]) == 1
    plain_out = capsys.readouterr().out
    plain_summary = (out / "batch_summary.json").read_bytes()

    assert cli_main(["batch", "--dir", str(src), "--out", str(out),
                     "--recurse", "--machine-readable"]) == 1
    cap = capsys.readouterr()
    assert cap.out == plain_out                      # stdout table unchanged
    assert (out / "batch_summary.json").read_bytes() == plain_summary
    assert cap.err and _ndjson(cap.err)              # NDJSON went to stderr only


def test_quiet_suppresses_table_but_keeps_summary_and_ndjson(capsys, tmp_path):
    src, out = _tree(tmp_path)
    assert cli_main(["batch", "--dir", str(src), "--out", str(out),
                     "--recurse"]) == 1
    plain_summary = (out / "batch_summary.json").read_bytes()
    capsys.readouterr()

    assert cli_main(["batch", "--dir", str(src), "--out", str(out), "--recurse",
                     "--quiet", "--machine-readable"]) == 1
    cap = capsys.readouterr()
    assert cap.out == ""                             # human table suppressed
    assert (out / "batch_summary.json").read_bytes() == plain_summary
    assert [e["event"] for e in _ndjson(cap.err)][-1] == "done"


# --------------------------------------------------------------------------- #
# Append-only run ledger                                                       #
# --------------------------------------------------------------------------- #

LEDGER_KEYS = {"format", "version", "run", "args", "inputs", "outcome", "ext"}


def test_ledger_appends_one_line_per_run_and_survives_modified_only(tmp_path):
    src, out = _tree(tmp_path)
    ledger = tmp_path / "runs.ndjson"
    assert cli_main(["batch", "--dir", str(src), "--out", str(out), "--recurse",
                     "--ledger", str(ledger), "--quiet"]) == 1
    lines = ledger.read_text().splitlines()
    assert len(lines) == 1
    rec = json.loads(lines[0])
    assert set(rec) == LEDGER_KEYS
    assert rec["format"] == "openfacefx.batch.ledger" and rec["version"] == 1
    assert rec["inputs"]["count"] == 4              # all discovered inputs
    assert rec["outcome"] == {"processed": 4, "failed": 1, "skipped": 0,
                              "exit": 1}
    fp = rec["inputs"]["files"][0]
    assert set(fp) == {"file", "mtime", "size", "transcript"}
    assert {f["transcript"] for f in rec["inputs"]["files"]} == {"mfa", "naive",
                                                                 "none"}

    # a --modified-only re-run appends a *second* line (does not overwrite)
    assert cli_main(["batch", "--dir", str(src), "--out", str(out), "--recurse",
                     "--modified-only", "--ledger", str(ledger), "--quiet"]) == 1
    lines = ledger.read_text().splitlines()
    assert len(lines) == 2
    rerun = json.loads(lines[1])
    assert rerun["args"]["modified_only"] is True
    assert rerun["outcome"]["skipped"] == 3         # the three ok files skipped


def test_ledger_run_id_is_deterministic_and_wall_clock_free(tmp_path):
    """Two identical runs over the same inputs produce a byte-identical ledger
    line (same ``run`` hash) -- no Date.now in the identity."""
    src, out = _tree(tmp_path)
    ledger = tmp_path / "runs.ndjson"
    for _ in range(2):
        assert cli_main(["batch", "--dir", str(src), "--out", str(out),
                         "--recurse", "--ledger", str(ledger), "--quiet"]) == 1
    a, b = ledger.read_text().splitlines()
    assert a == b                                    # identical inputs+args
    assert json.loads(a)["run"] == json.loads(b)["run"]


# --------------------------------------------------------------------------- #
# cue_warnings folded into the summary + ranking                              #
# --------------------------------------------------------------------------- #

def test_cue_warnings_surface_for_short_and_long_cues(capsys, tmp_path):
    src, out = _tree(tmp_path)
    assert cli_main(["batch", "--dir", str(src), "--out", str(out), "--recurse",
                     "--cue-warnings"]) == 1
    summary = json.loads((out / "batch_summary.json").read_text())
    rows = {r["file"]: r for r in summary["rows"]}
    # the MFA fixture has exactly one 20 ms (short) and one 680 ms (long) cue
    assert rows["aligned.wav"]["cue_warnings"] == 2
    assert all("cue_warnings" in r for r in summary["rows"])
    # the printed table gains a 'cue' column
    assert "cue" in capsys.readouterr().out


def test_cue_warnings_absent_by_default_protects_byte_identity(tmp_path):
    """Without --cue-warnings no row carries the key, so the summary bytes are
    exactly the pre-#35 shape; the flag is the only thing that adds it."""
    src, out = _tree(tmp_path)
    assert cli_main(["batch", "--dir", str(src), "--out", str(out),
                     "--recurse", "--quiet"]) == 1
    summary = json.loads((out / "batch_summary.json").read_text())
    assert all("cue_warnings" not in r for r in summary["rows"])


def test_cue_warnings_custom_thresholds_change_the_count(tmp_path):
    src, out = _tree(tmp_path)
    # widen the window past both outliers (10 ms .. 900 ms) -> aligned clean
    assert cli_main(["batch", "--dir", str(src), "--out", str(out), "--recurse",
                     "--cue-warnings", "--min-cue", "0.01", "--max-cue", "0.9",
                     "--quiet"]) == 1
    rows = {r["file"]: r
            for r in json.loads((out / "batch_summary.json").read_text())["rows"]}
    assert rows["aligned.wav"]["cue_warnings"] == 0


# --------------------------------------------------------------------------- #
# Byte-identity, determinism, exit code                                        #
# --------------------------------------------------------------------------- #

def test_plain_run_is_deterministic(capsys, tmp_path):
    """Same inputs, same output path -> byte-identical table and summary across
    two runs (the #23 determinism guarantee, at the batch layer)."""
    src, out = _tree(tmp_path)
    assert cli_main(["batch", "--dir", str(src), "--out", str(out),
                     "--recurse"]) == 1
    first_out = capsys.readouterr().out
    first_summary = (out / "batch_summary.json").read_bytes()
    assert cli_main(["batch", "--dir", str(src), "--out", str(out),
                     "--recurse"]) == 1
    assert capsys.readouterr().out == first_out
    assert (out / "batch_summary.json").read_bytes() == first_summary


def test_ndjson_stream_is_deterministic(capsys, tmp_path):
    src, out = _tree(tmp_path)
    cli_main(["batch", "--dir", str(src), "--out", str(out), "--recurse",
              "--quiet", "--machine-readable"])
    first = capsys.readouterr().err
    cli_main(["batch", "--dir", str(src), "--out", str(out), "--recurse",
              "--quiet", "--machine-readable"])
    assert capsys.readouterr().err == first          # same events, same bytes
    assert first == first.encode("ascii", "replace").decode()   # ensure_ascii


def test_failures_still_exit_nonzero_with_new_flags(tmp_path):
    src, out = _tree(tmp_path)                        # contains broken.wav
    ledger = tmp_path / "l.ndjson"
    rc = cli_main(["batch", "--dir", str(src), "--out", str(out), "--recurse",
                   "--machine-readable", "--ledger", str(ledger),
                   "--cue-warnings", "--quiet"])
    assert rc == 1                                    # a per-file failure -> 1


def test_empty_tree_emits_wellformed_stream_and_ledger(capsys, tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    out = tmp_path / "out"
    ledger = tmp_path / "l.ndjson"
    rc = cli_main(["batch", "--dir", str(empty), "--out", str(out),
                   "--machine-readable", "--ledger", str(ledger)])
    assert rc == 1
    kinds = [e["event"] for e in _ndjson(capsys.readouterr().err)]
    assert kinds == ["start", "done"]
    rec = json.loads(ledger.read_text().splitlines()[0])
    assert rec["inputs"]["count"] == 0 and rec["outcome"]["processed"] == 0


# --------------------------------------------------------------------------- #
# Library entry point (embedding without the CLI)                              #
# --------------------------------------------------------------------------- #

def test_run_batch_library_call_with_new_kwargs(tmp_path):
    src, out = _tree(tmp_path)
    ledger = tmp_path / "l.ndjson"
    rc = run_batch(str(src), str(out), recurse=True, cue_warnings=True,
                   ledger=str(ledger), quiet=True)
    assert rc == 1
    summary = json.loads((out / "batch_summary.json").read_text())
    assert all("cue_warnings" in r for r in summary["rows"])
    assert ledger.read_text().count("\n") == 1


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    import inspect
    for fn in fns:
        sig = inspect.signature(fn)
        if "capsys" in sig.parameters or "tmp_path" in sig.parameters:
            continue                                  # pytest-only fixtures
        fn()
        print("PASS", fn.__name__)
