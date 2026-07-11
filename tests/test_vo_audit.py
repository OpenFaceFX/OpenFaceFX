"""VO delivery QA auditor (issue #42) — reconcile a delivered folder vs the
loc-table manifest.

Pins the acceptance: missing / orphan / duration-outlier / empty / naming checks
each produce a deterministic itemized report keyed by loc-ID; the coverage matrix
reflects per-locale holes; the duration tolerance is configurable and a take
inside it is never flagged; the auditor is read-only over the delivered folder
and reuses the shared `read_manifest` + `wav_duration`.
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

from openfacefx.batch_manifest import read_manifest
from openfacefx.cli import main as cli_main
from openfacefx.pipeline import wav_duration
from openfacefx.vo_audit import audit_delivery, audit_report_text


def _wav(path, seconds=0.8, value=8000, rate=16000):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(struct.pack("<h", value) * int(seconds * rate))


def _manifest(tmp_path, rows, name="loc.csv"):
    p = tmp_path / name
    p.write_text("id,audio,text,language\n" + "\n".join(rows) + "\n")
    return str(p)


def _snapshot(root):
    """Sorted (relpath, size, mtime_ns) of every file under root — to prove the
    auditor writes nothing."""
    out = []
    for dp, _d, files in os.walk(root):
        for f in files:
            fp = os.path.join(dp, f)
            st = os.stat(fp)
            out.append((os.path.relpath(fp, root), st.st_size, st.st_mtime_ns))
    return sorted(out)


# --------------------------------------------------------------------------- #
# each check produces a deterministic itemized issue keyed by loc-ID           #
# --------------------------------------------------------------------------- #

def test_each_check_is_itemized_and_keyed_by_loc_id(tmp_path):
    d = tmp_path / "delivered"
    _wav(str(d / "greeting.wav"))                     # good
    _wav(str(d / "silent.wav"), value=0)              # near-silent -> empty
    _wav(str(d / "toolong.wav"), seconds=9.0)         # duration outlier for "hi"
    _wav(str(d / "misnamed.wav"))                     # stem != loc-ID -> naming
    _wav(str(d / "orphan.wav"))                       # unreferenced -> orphan
    manifest = _manifest(tmp_path, [
        "greeting,greeting.wav,hello there,en",
        "quiet,silent.wav,anyone there,en",
        "shout,toolong.wav,hi,en",
        "named,misnamed.wav,check the name,en",
        "gone,gone.wav,not delivered,en",             # missing (no file)
    ])
    rep = audit_delivery(manifest, str(d), duration_tolerance=0.5, cps=14.0)
    by_kind = {}
    for it in rep["issues"]:
        by_kind.setdefault(it["kind"], []).append(it)
    assert {"missing", "orphan", "duration", "empty", "naming"} <= set(by_kind)
    assert by_kind["missing"][0]["id"] == "gone"      # keyed by loc-ID
    assert by_kind["empty"][0]["id"] == "quiet"
    assert by_kind["duration"][0]["id"] == "shout"
    assert by_kind["naming"][0]["id"] == "named"
    assert by_kind["orphan"][0]["audio"] == "orphan.wav"
    assert rep["format"] == "openfacefx.vo_audit"


def test_report_is_deterministic(tmp_path):
    d = tmp_path / "delivered"
    _wav(str(d / "a.wav"))
    _wav(str(d / "loose.wav"))
    manifest = _manifest(tmp_path, ["a,a.wav,hello,en", "b,gone.wav,bye,en"])
    assert audit_delivery(manifest, str(d)) == audit_delivery(manifest, str(d))
    assert audit_report_text(audit_delivery(manifest, str(d)))  # renders


# --------------------------------------------------------------------------- #
# coverage matrix with per-locale holes                                        #
# --------------------------------------------------------------------------- #

def test_coverage_matrix_reflects_per_locale_holes(tmp_path):
    d = tmp_path / "delivered"
    _wav(str(d / "en" / "hi.wav"))
    _wav(str(d / "fr" / "hi.wav"))
    _wav(str(d / "en" / "bye.wav"))                   # bye delivered only in en
    manifest = _manifest(tmp_path, [
        "hi,en/hi.wav,hello,en",
        "hi,fr/hi.wav,bonjour,fr",
        "bye,en/bye.wav,goodbye,en",
        "bye,fr/bye.wav,au revoir,fr",                # fr take absent -> a hole
    ])
    cov = audit_delivery(manifest, str(d))["coverage"]
    assert cov["hi"] == {"en": True, "fr": True}      # fully covered
    assert cov["bye"] == {"en": True, "fr": False}    # per-locale hole


# --------------------------------------------------------------------------- #
# configurable duration tolerance — inside is never flagged                    #
# --------------------------------------------------------------------------- #

def test_duration_tolerance_is_configurable_and_inside_never_flagged(tmp_path):
    d = tmp_path / "delivered"
    _wav(str(d / "line.wav"), seconds=0.9)            # expected 0.5 s (5 chars/10)
    manifest = _manifest(tmp_path, ["line,line.wav,hello,en"])
    tight = audit_delivery(manifest, str(d), duration_tolerance=0.2, cps=10.0)
    assert any(it["kind"] == "duration" for it in tight["issues"])   # 0.9 > +20%
    loose = audit_delivery(manifest, str(d), duration_tolerance=1.0, cps=10.0)
    assert not any(it["kind"] == "duration" for it in loose["issues"])  # inside
    # and a bang-on take is never flagged at any sane tolerance
    _wav(str(d / "line.wav"), seconds=0.5)
    ok = audit_delivery(manifest, str(d), duration_tolerance=0.2, cps=10.0)
    assert not any(it["kind"] == "duration" for it in ok["issues"])


# --------------------------------------------------------------------------- #
# read-only + shared dependencies                                              #
# --------------------------------------------------------------------------- #

def test_auditor_is_read_only_over_delivered(tmp_path):
    d = tmp_path / "delivered"
    _wav(str(d / "a.wav"))
    _wav(str(d / "b.wav"), value=0)
    manifest = _manifest(tmp_path, ["a,a.wav,hi,en", "b,b.wav,yo,en",
                                    "c,c.wav,gone,en"])
    before = _snapshot(str(d))
    audit_delivery(manifest, str(d))
    audit_delivery(manifest, str(d), duration_tolerance=0.1)
    assert _snapshot(str(d)) == before                # nothing created or touched


def test_reuses_shared_manifest_parser_and_wav_duration():
    import openfacefx.vo_audit as va
    assert va.read_manifest is read_manifest          # #40 parser, shared
    assert va.wav_duration is wav_duration            # pipeline stat, reused


def test_unreadable_file_is_flagged_not_crashed(tmp_path):
    d = tmp_path / "delivered"
    d.mkdir()
    (d / "bad.wav").write_text("this is not a RIFF/WAVE file")
    manifest = _manifest(tmp_path, ["bad,bad.wav,hello,en"])
    rep = audit_delivery(manifest, str(d))
    assert any(it["kind"] == "unreadable" and it["id"] == "bad"
               for it in rep["issues"])


def test_clean_delivery_has_no_issues(tmp_path):
    d = tmp_path / "delivered"
    _wav(str(d / "hello.wav"), seconds=0.8)
    manifest = _manifest(tmp_path, ["hello,hello.wav,hello there,en"])
    rep = audit_delivery(manifest, str(d), duration_tolerance=0.5, cps=14.0)
    assert rep["issues"] == [] and rep["counts"]["issues"] == 0


# --------------------------------------------------------------------------- #
# CLI: the QA gate (nonzero on issues) + --json                                #
# --------------------------------------------------------------------------- #

def test_cli_audit_is_a_qa_gate(tmp_path, capsys):
    d = tmp_path / "delivered"
    _wav(str(d / "hello.wav"), seconds=0.8)
    _wav(str(d / "extra.wav"))                        # orphan -> issue -> exit 1
    manifest = _manifest(tmp_path, ["hello,hello.wav,hello there,en"])
    assert cli_main(["audit", "--manifest", manifest, "--delivered", str(d)]) == 1
    assert "orphan" in capsys.readouterr().out
    os.remove(str(d / "extra.wav"))                   # now clean -> exit 0
    assert cli_main(["audit", "--manifest", manifest, "--delivered", str(d)]) == 0


def test_cli_audit_json_report(tmp_path, capsys):
    d = tmp_path / "delivered"
    _wav(str(d / "hello.wav"))
    manifest = _manifest(tmp_path, ["hello,hello.wav,hi,en", "gone,gone.wav,bye,en"])
    rc = cli_main(["audit", "--manifest", manifest, "--delivered", str(d), "--json"])
    assert rc == 1
    report = json.loads(capsys.readouterr().out)
    assert report["format"] == "openfacefx.vo_audit"
    assert report["counts"]["missing"] == 1
    assert report["coverage"]["gone"] == {"en": False}
