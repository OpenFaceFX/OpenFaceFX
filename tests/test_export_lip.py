"""Bethesda .lip writer (#12) — EXPERIMENTAL, unverified in-game.

These oracles do NOT prove Skyrim loads the file (nobody has run that test).
They prove the writer is internally exact:

  * ORACLE B — every track we write decodes back, through an INDEPENDENT walker
    of the documented token grammar, to exactly the curves we put in (byte-exact
    float32 on the frame-major R=33 grid), with a self-consistent header.
  * ORACLE C — our own tooling tolerates the output: the modern-header reader
    doesn't choke on it, and it embeds into / extracts from a .fuz round-trip.

Real .lip samples are never committed, so tracks are synthesized in-test. The
byte-identical re-encode of the four real samples lives in the scratchpad Oracle
A script (see the PR notes), not here.
"""

import os
import struct
import sys

import pytest

try:
    import openfacefx  # noqa: F401
except ImportError:
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from openfacefx import export_lip as EL
from openfacefx.bethesda import (SKYRIM_TARGETS, parse_lip_header, read_fuz,
                                  write_fuz)
from openfacefx.pipeline import naive_segments

_SENTINEL = b"\xf9\xe8\x8a\x26"
_STRIDE = {16: 33, 43: 60}


def _decode(data):
    """INDEPENDENT decoder for the .lip payload — a from-scratch walk of the
    token grammar (value [dup] [00 tag 00 marker]) on the frame-major grid, so
    it shares no code with the writer. Returns header + grid {slot:{row:val}}."""
    version, duration, num_curves = struct.unpack_from("<III", data, 0)
    count12, const14 = struct.unpack_from("<HH", data, 12)
    (neg16,) = struct.unpack_from("<i", data, 16)
    u20, u22 = struct.unpack_from("<HH", data, 20)
    R = _STRIDE[u20]
    header = dict(version=version, duration=duration, num_curves=num_curves,
                  count12=count12, const14=const14, neg16=neg16, u20=u20, u22=u22)

    p, pos, grid, ncells, first_pos = 24, 0, {}, 0, None
    while p + 4 <= len(data):
        raw = data[p:p + 4]
        v = struct.unpack_from("<f", data, p)[0]
        p += 4
        dup = p + 4 <= len(data) and data[p:p + 4] == raw
        if dup:
            p += 4
        marker = None
        if (p + 3 <= len(data) and data[p] == 0 and data[p + 2] == 0
                and data[p + 1] != 0 and data[p + 1] % 4 == 0):
            marker = data[p + 1]
            p += 3
        if first_pos is None:
            first_pos = pos
        frame, curve = pos // R, pos % R
        grid.setdefault(curve, {})[frame] = 0.0 if raw == _SENTINEL else v
        ncells += 1
        pos += (2 if dup else 1) + (marker // 4 if marker is not None else 0)
    return dict(header=header, R=R, grid=grid, endpos=pos, ncells=ncells,
                first_pos=first_pos, tail=p)


# Phrases spanning bilabials, fricatives, rounded/open vowels, plosives and a
# single phoneme — enough variety that a routing bug shows as a grid mismatch.
_CASES = [
    ("hello world", 2.0),
    ("the quick brown fox jumps over", 3.5),
    ("mmm papa bob", 1.8),
    ("she sells sea shells", 2.6),
    ("a", 0.6),
    ("wow you were far away", 2.2),
]


def _intended_grid(text, dur):
    """The float32 curves the writer intends to store: coarticulation sampled on
    the same frame grid, via the writer's own (deterministic) helpers."""
    segs = naive_segments(text, dur)
    mapping = EL.skyrim_mapping()
    grid, neg16, count12 = EL._frame_grid(segs, dur, mapping, EL._default_params())
    names = mapping.target_names
    active, col_of = {}, {}
    for c, name in enumerate(names):
        slot = EL.SKYRIM_SLOT_ORDER[name]
        col_of[slot] = c
        if float(grid[:, c].max()) > EL._EPS:
            active[slot] = c
    active[EL.SKYRIM_SLOT_ORDER["Aah"]] = col_of[EL.SKYRIM_SLOT_ORDER["Aah"]]
    return grid, neg16, count12, active


@pytest.mark.parametrize("text,dur", _CASES)
def test_oracle_b_decoded_grid_matches_intended(text, dur):
    segs = naive_segments(text, dur)
    data = EL.lip_bytes(segs, dur, game="skyrim")
    dec = _decode(data)
    grid, neg16, count12, active = _intended_grid(text, dur)

    # header self-consistency
    h = dec["header"]
    assert h["version"] == 1 and h["const14"] == 3 and h["u20"] == 16
    assert h["count12"] == count12 and h["neg16"] == neg16
    assert h["duration"] == 132 * count12 + 28
    assert h["num_curves"] == len(active) == len(dec["grid"])

    # the decoder starts at grid origin, so the stream must too; and it must
    # cover exactly count12 full R=33 frames (game-authored files end that way)
    assert dec["first_pos"] == 0
    assert dec["endpos"] == 33 * count12
    assert dec["tail"] == len(data)  # no trailing slop

    # every stored value round-trips to the exact float32 we intended
    for slot, col in active.items():
        assert slot in dec["grid"], f"slot {slot} dropped"
        for row in range(count12):
            want = float(struct.unpack("<f", struct.pack("<f", grid[row, col]))[0])
            assert dec["grid"][slot][row] == want, (slot, row)


@pytest.mark.parametrize("text,dur", _CASES)
def test_writer_output_is_a_decode_encode_fixed_point(text, dur):
    """Decoding then re-encoding the writer's bytes reproduces them exactly:
    proves the emitted markers/values are a self-consistent grid with no
    accidental doubled-value merges or desync."""
    data = EL.lip_bytes(naive_segments(text, dur), dur, game="skyrim")
    dec = _decode(data)
    cells = []
    for slot, frames in dec["grid"].items():
        for row, val in frames.items():
            cells.append({"frame": row, "curve": slot, "value": val})
    cells.sort(key=lambda c: (c["frame"], c["curve"]))
    payload = EL._encode_cells(cells, 33, total_slots=33 * dec["header"]["count12"])
    assert data[24:] == payload


@pytest.mark.parametrize("text,dur", _CASES)
def test_all_values_in_unit_range(text, dur):
    dec = _decode(EL.lip_bytes(naive_segments(text, dur), dur, game="skyrim"))
    for frames in dec["grid"].values():
        for v in frames.values():
            assert -1e-6 <= v <= 1.0 + 1e-6


def test_determinism():
    segs = naive_segments("consistency matters", 2.4)
    a = EL.lip_bytes(segs, 2.4, game="skyrim")
    b = EL.lip_bytes(segs, 2.4, game="skyrim")
    assert a == b


# --- ORACLE C: our own reader / container tolerate the output ---------------

def test_oracle_c_modern_header_reader_tolerates_output():
    data = EL.lip_bytes(naive_segments("hello world", 2.0), 2.0, game="skyrim")
    # NOTE: these payloads carry the 24-byte FaceFX animation header, NOT the
    # 12-byte version/size/flags header parse_lip_header models (that layout is
    # FaceFXWrapper's interface view; the real .fuz-embedded samples have none).
    # The one field both layouts share is version@0 — it must read 1 and the
    # reader must not choke on the longer buffer.
    hdr = parse_lip_header(data)
    assert hdr.version == 1


def test_oracle_c_fuz_embedding_roundtrip(tmp_path):
    data = EL.lip_bytes(naive_segments("into a fuz we go", 2.1), 2.1, game="skyrim")
    audio = b"RIFF\x24\x00\x00\x00WAVEfmt " + b"\x00" * 16  # stand-in xWMA bytes
    path = str(tmp_path / "voice.fuz")
    write_fuz(path, audio, lip=data)
    lip_out, audio_out = read_fuz(path)
    assert lip_out == data
    assert audio_out == audio


def test_write_lip_file(tmp_path):
    path = str(tmp_path / "line.lip")
    EL.write_lip(naive_segments("write me to disk", 1.9), 1.9, path)
    with open(path, "rb") as fh:
        on_disk = fh.read()
    assert on_disk == EL.lip_bytes(naive_segments("write me to disk", 1.9), 1.9)
    assert parse_lip_header(on_disk).version == 1


# --- guards / assumptions ---------------------------------------------------

def test_fallout4_raises_not_implemented():
    segs = naive_segments("fallout", 1.0)
    with pytest.raises(NotImplementedError, match="Fallout 4"):
        EL.lip_bytes(segs, 1.0, game="fallout4")
    with pytest.raises(NotImplementedError):
        EL.write_lip(segs, 1.0, "/dev/null", game="fallout4")


def test_unknown_game_raises_value_error():
    with pytest.raises(ValueError, match="unknown game"):
        EL.lip_bytes(naive_segments("x", 0.5), 0.5, game="morrowind")


def test_empty_and_bad_duration_raise():
    with pytest.raises(ValueError):
        EL.lip_bytes([], 1.0)
    with pytest.raises(ValueError):
        EL.lip_bytes(naive_segments("hi", 1.0), 0.0)


def test_slot_order_is_even_spaced_unique_and_in_range():
    slots = EL.SKYRIM_SLOT_ORDER
    assert set(slots) == set(SKYRIM_TARGETS)
    vals = list(slots.values())
    assert vals == [2 * i for i in range(16)]          # even slots 0,2,..,30
    assert len(set(vals)) == 16 and max(vals) < 33      # unique, within stride
    assert slots["Aah"] == 0                            # base curve at grid origin


def test_mapping_covers_all_arpabet():
    from openfacefx.phonemes import ARPABET
    m = EL.skyrim_mapping()
    assert [t.name for t in m.targets] == SKYRIM_TARGETS
    # every ARPAbet phoneme resolves to a subset of the 16 Skyrim targets
    for ph in ARPABET:
        row = m.row(ph)
        assert all(0 <= idx < 16 for idx in row)


def test_lip_calibrate_sweep(tmp_path):
    """Each calibration file animates exactly its named slot 0->1->0 and
    decodes exactly through the research codec's grammar."""
    from openfacefx.export_lip import lip_calibrate, SKYRIM_SLOT_ORDER
    files = lip_calibrate(str(tmp_path), seconds=1.0)
    assert len(files) == len(SKYRIM_SLOT_ORDER) == 16
    for path in files:
        name = os.path.basename(path)
        slot = int(name.split("slot")[1][:2])
        d = open(path, "rb").read()
        hdr = struct.unpack_from("<IIIHHiHH", d)
        version, duration, ncur, count12, c14, neg16, u20, u22 = hdr
        assert version == 1 and u20 == 16 and c14 == 3 and neg16 == 0
        assert duration == 132 * count12 + 28
        dec = _decode(d)                 # the independent test decoder
        animated = {c for c, rows in dec["grid"].items()
                    if max(rows.values()) > 1e-4}
        assert animated == {slot}, name
        peak = max(dec["grid"][slot].values())
        assert abs(peak - 1.0) < 1e-6
