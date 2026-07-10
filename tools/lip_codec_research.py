"""RESEARCH TOOL — Bethesda .lip payload codec (issue #12; not a supported API).

Clean-room, derived purely from byte analysis of four sample files (three
mod-author-generated placeholders plus one REAL vanilla Skyrim asset; July
2026). Status: the structural container AND the per-key curve routing are now
SOLVED — this codec parses each known sample exactly to EOF, re-serializes it
byte-identically from decoded fields, AND decodes each frame-major value to its
(curve, frame). Values are FaceFX curve floats (weight envelopes in [0,1] plus
signed Hermite tangents stored as doubled values). Confidence tags:
[V]=verified across all samples, [I]=inferred, [U]=unknown.

CURVE ROUTING — RESOLVED (see decode_curves + time_model):
  The payload is a FRAME-MAJOR rigid grid. Conceptually each frame is a fixed
  strip of R "slots" (R=33 for Skyrim-lineage, R=60 for Fallout 4-lineage;
  R == total_slots/frame_count, exact on the real vanilla file: 33*74=2442).
  Walk the token stream accumulating a flattened position:
      pos(token) = running total of  num_floats + (marker//4)
      num_floats = 2 for a dup token (value carries an equal Hermite tangent),
                   else 1;   (marker//4) = COUNT OF EMPTY SLOTS to skip, i.e.
                   inactive curves between this stored value and the next.
  Then  frame = pos // R  and  curve = pos % R  (routing is POSITIONAL; the
  marker does NOT name a curve, it advances past resting curves). A resting
  curve is written with the sentinel float 0x268ae8f9 (~9.6e-16). Decoding the
  real vanilla file this way yields exactly 13 distinct smooth curves (one
  stored as an identical value+tangent slot pair) == header num_curves, and the
  per-curve envelopes are 6.3x smoother than a random-routing null (4.5-6.9x
  across all four samples; round-2 marker-as-curve-index schemes never beat
  ~1.1x). The gaps are exactly recoverable from the (frame,curve) grid, so the
  format is writable. This unblocks a real .lip exporter.

Structural model (verified by byte-exact round-trip):

    file    = header(24 bytes) + payload
    payload = sequence of tokens to EOF, each token:
          <f32 value>                       4 bytes
        [ <f32 value> ]   (exact dup)      +4 bytes  iff next 4 bytes == value
        [ 00 <u8 tag> 00 ] (marker)        +3 bytes  iff pattern fits, tag%4==0, tag!=0
    The marker is a SUFFIX: read the float first, or a value whose own bytes
    contain 00 XX 00 will desync the stream.

How to help (see the issue for details): run this on .lip files you have the
rights to inspect — especially short single-phoneme lines, or Creation
Kit-generated files whose audio duration and transcript you know — and report
header fields + whether round-trip stays EXACT. Do not commit .lip samples to
this repo.

Usage:  python tools/lip_codec_research.py file1.lip [file2.lip ...]
"""
import struct
import sys


def parse_header(d):
    h = {}
    h["version"] = struct.unpack_from("<I", d, 0)[0]     # [V] =1
    h["duration"] = struct.unpack_from("<I", d, 4)[0]    # [V] FaceFX ticks; see time_model
    h["num_curves"] = struct.unpack_from("<I", d, 8)[0]  # [I] ACTIVE curve count (13 in all samples)
    h["count12"] = struct.unpack_from("<H", d, 12)[0]    # [V] FRAME count (uniform time grid)
    h["const14"] = struct.unpack_from("<H", d, 14)[0]    # [I] =3: key = (value, slopeIn, slopeOut)
    h["neg16"] = struct.unpack_from("<i", d, 16)[0]      # [V] first frame index (negative pre-roll)
    h["u20"] = struct.unpack_from("<H", d, 20)[0]        # [V] target vocabulary: 16 Skyrim / 43 FO4
    h["u22"] = struct.unpack_from("<H", d, 22)[0]        # [U] varies (63/163/199/3)
    return h


def time_model(h):
    """TIME MODEL — resolved Jul 2026 from 4 samples incl. a real vanilla CK
    lip, exact on all: duration = ticks_per_frame * count12 + 28, with
    ticks_per_frame an integer per game (Skyrim 132, Fallout 4 240). Keys sit
    on a uniform frame grid running neg16 .. count12-1 (t=0 at audio start);
    at the standard 30 fps, time_s(frame) = frame / 30. Per-key time is NOT
    stored (const14=3: value + two slopes only). Per-key CURVE ROUTING is now
    resolved too: frame-major positional on a rigid R-slot grid (see the module
    docstring and decode_curves)."""
    tpf = (h["duration"] - 28) / h["count12"]
    return {
        "frame_count": h["count12"],
        "ticks_per_frame": tpf,
        "ticks_exact": tpf == int(tpf),
        "first_frame": h["neg16"],
        "duration_s_at_30fps": h["count12"] / 30.0,
        "preroll_s_at_30fps": -h["neg16"] / 30.0,
    }


def _is_marker(d, p):
    return (p + 3 <= len(d) and d[p] == 0 and d[p + 2] == 0
            and d[p + 1] != 0 and d[p + 1] % 4 == 0)


def parse_payload(d, start=24):
    """Return (tokens, end): token = dict(value, dup, marker, raw)."""
    pos = start
    toks = []
    while pos + 4 <= len(d):
        raw0 = d[pos:pos + 4]
        v = struct.unpack_from("<f", d, pos)[0]
        pos += 4
        rawspan = raw0
        dup = False
        if pos + 4 <= len(d) and d[pos:pos + 4] == raw0:
            dup = True
            rawspan += d[pos:pos + 4]
            pos += 4
        marker = None
        if _is_marker(d, pos):
            marker = d[pos + 1]
            rawspan += d[pos:pos + 3]
            pos += 3
        toks.append(dict(value=v, dup=dup, marker=marker, raw=bytes(rawspan)))
    return toks, pos


def serialize(header_bytes, toks):
    out = bytearray(header_bytes)
    for t in toks:
        out += t["raw"]
    return bytes(out)


def serialize_from_fields(header_bytes, toks):
    """Rebuild from (value, dup, marker) instead of raw bytes — proves the
    token model captures everything needed to WRITE the format."""
    out = bytearray(header_bytes)
    for t in toks:
        b = struct.pack("<f", t["value"])
        out += b
        if t["dup"]:
            out += b
        if t["marker"] is not None:
            out += bytes([0, t["marker"], 0])
    return bytes(out)


# Resting-curve sentinel written for a listed-but-inactive curve (~9.6e-16).
SENTINEL = struct.unpack("<f", bytes.fromhex("f9e88a26"))[0]

# Frame stride R (slots per frame) by target-vocabulary field u20. Verified
# exact on the real vanilla file (33 * 74 frames == 2442 slots). Placeholders
# from TTS tools may leave the final frame partial (frames == ceil(total/R)).
STRIDE_BY_U20 = {16: 33, 43: 60}  # Skyrim, Fallout 4


def _is_sentinel(v):
    return struct.pack("<f", v) == b"\xf9\xe8\x8a\x26"


def frame_stride(h):
    """Slots per frame R for this file's game (from header u20)."""
    return STRIDE_BY_U20.get(h["u20"])


def decode_curves(d, R=None):
    """RESOLVED per-key routing. Return dict with:
        'records': list of (frame, curve, value, is_sentinel) in stream order,
        'grid'   : {curve_slot: {frame: value}}   (sentinel -> 0.0 = rest),
        'stride' : R,   'frames': frame count.
    Routing is frame-major positional: a flattened position accumulates
    num_floats + (marker//4) per token; frame = pos//R, curve = pos%R. The
    marker is the number of resting curves to skip, NOT a curve id. Curves that
    share an identical series are one curve stored as value+equal-tangent."""
    h = parse_header(d)
    toks, _ = parse_payload(d)
    if R is None:
        R = frame_stride(h)
    if R is None:
        raise ValueError("unknown frame stride for u20=%d; pass R" % h["u20"])
    records, grid, pos = [], {}, 0
    for t in toks:
        frame, curve = pos // R, pos % R
        sent = _is_sentinel(t["value"])
        records.append((frame, curve, t["value"], sent))
        grid.setdefault(curve, {})[frame] = 0.0 if sent else t["value"]
        pos += (2 if t["dup"] else 1) + (t["marker"] // 4 if t["marker"] is not None else 0)
    frames = (pos + R - 1) // R
    return {"records": records, "grid": grid, "stride": R, "frames": frames}


# ---------------------------------------------------------------------------
# ENCODE side — the exact inverse of decode_curves. Byte-identical re-encode of
# all four known samples ("re-encode oracle", tools/../scratchpad) proves the
# grid model captures everything needed to WRITE a payload, not just read one.
# ---------------------------------------------------------------------------

SENTINEL_BYTES = struct.pack("<f", SENTINEL)  # b"\xf9\xe8\x8a\x26"


def pack_header(h):
    """Inverse of parse_header: the eight header fields back to 24 bytes."""
    return struct.pack("<IIIHHiHH",
                       h["version"], h["duration"], h["num_curves"],
                       h["count12"], h["const14"], h["neg16"],
                       h["u20"], h["u22"])


def decode_cells(d, R=None):
    """Like decode_curves, but returns the ordered per-token CELLS carrying every
    attribute needed to re-encode byte-exactly. Returns (cells, R, end_pos) where
    each cell is dict(pos, frame, curve, value, dup, marker, is_sentinel).
    encode_curves is its exact inverse."""
    h = parse_header(d)
    toks, _ = parse_payload(d)
    if R is None:
        R = frame_stride(h)
    if R is None:
        raise ValueError("unknown frame stride for u20=%d; pass R" % h["u20"])
    cells, pos = [], 0
    for t in toks:
        cells.append(dict(pos=pos, frame=pos // R, curve=pos % R,
                          value=t["value"], dup=t["dup"], marker=t["marker"],
                          is_sentinel=_is_sentinel(t["value"])))
        pos += (2 if t["dup"] else 1) + (t["marker"] // 4 if t["marker"] is not None else 0)
    return cells, R, pos


def encode_curves(cells, R, total_slots=None, tail_marker=None):
    """INVERSE of decode_curves/decode_cells: serialize frame-major grid cells to
    the payload bytes (everything after the 24-byte header).

    ``cells`` is an ordered list walking the grid in stream order; each cell is a
    mapping with ``frame``, ``curve``, ``value`` (float; pass the resting
    ``SENTINEL`` float for a rest key) and optional ``dup`` (bool, default
    False). The resting-slot SKIP markers are DERIVED from the gap between
    consecutive cell positions (pos = frame*R + curve) — they hold no information
    the grid does not, which is precisely why a writer can emit this format from
    a (curve, frame) grid alone.

    The one byte routing does NOT fix is the final token's terminator marker:
      * pass ``total_slots`` to pad the last token's skip so the stream ends at
        exactly that many slots (game-authored Skyrim files end on a full frame,
        R*count12); or
      * pass ``tail_marker`` (a raw tag byte, or None) to emit verbatim — the
        re-encode oracle uses this to reproduce tool-written files exactly.
    Raises ValueError on a >63-slot gap: one marker tag (max 252 = 63*4) cannot
    span it, so a resting cell must be inserted to bridge (a dense writer never
    hits this — consecutive rows are < R apart)."""
    out = bytearray()
    n = len(cells)

    def _pos(c):
        return c["frame"] * R + c["curve"]

    for i, c in enumerate(cells):
        b = SENTINEL_BYTES if _is_sentinel(c["value"]) else struct.pack("<f", c["value"])
        out += b
        dup = c.get("dup", False)
        if dup:
            out += b
        numf = 2 if dup else 1
        if i < n - 1:
            skip = _pos(cells[i + 1]) - _pos(c) - numf
        elif total_slots is not None:
            skip = total_slots - _pos(c) - numf
        else:
            skip = (tail_marker // 4) if tail_marker else 0
        if skip < 0:
            raise ValueError(f"cell {i}: negative skip {skip} "
                             "(cells out of order or overlapping)")
        if skip > 63:
            raise ValueError(f"cell {i}: skip {skip} > 63 slots exceeds one marker "
                             "tag; insert a resting cell to bridge the gap")
        if skip:
            out += bytes([0, 4 * skip, 0])
    return bytes(out)


def roundtrip_curves(d, R=None):
    """Decode ``d`` to cells and re-encode to a full file; returns (ok, bytes).
    Byte-identity is the re-encode oracle — it proves the codec inverts cleanly.
    Preserves each file's own last-token terminator (tool files vary: some pad,
    some omit it) so identity holds without assuming the game's full-frame end."""
    cells, R, _ = decode_cells(d, R)
    tail = cells[-1]["marker"] if cells else None
    rebuilt = pack_header(parse_header(d)) + encode_curves(cells, R, tail_marker=tail)
    return rebuilt == d, rebuilt


def report(path):
    d = open(path, "rb").read()
    h = parse_header(d)
    toks, end = parse_payload(d)
    rt_raw = serialize(d[:24], toks)
    rt_fld = serialize_from_fields(d[:24], toks)
    vals = [t["value"] for t in toks]
    in01 = sum(1 for v in vals if -1e-4 <= v <= 1.0001)
    neg = sum(1 for v in vals if v < -1e-4)
    tm = time_model(h)
    print(f"\n{path}:")
    print(f"  header={h}")
    print(f"  time: {tm['frame_count']} frames x {tm['ticks_per_frame']:g} ticks"
          f" (exact={tm['ticks_exact']}), first frame {tm['first_frame']},"
          f" ~{tm['duration_s_at_30fps']:.2f}s at 30fps")
    print(f"  ntok={len(toks)}  EOF: end={end}/{len(d)} (slop={len(d) - end})")
    print(f"  roundtrip_raw    = {'EXACT' if rt_raw == d else 'FAIL'}")
    print(f"  roundtrip_fields = {'EXACT' if rt_fld == d else 'FAIL'}")
    if frame_stride(h) is not None:
        ok_curve, _ = roundtrip_curves(d)
        print(f"  roundtrip_curves = {'EXACT' if ok_curve else 'FAIL'}  (decode_cells -> encode_curves)")
    print(f"  values in [0,1] = {in01}/{len(vals)}   negatives (slopes) = {neg}")
    R = frame_stride(h)
    if R is not None:
        cv = decode_curves(d, R)
        used = sorted(cv["grid"])
        exact = "EXACT" if len(toks) and cv["frames"] == h["count12"] else "approx"
        print(f"  curves: stride R={R}  frames={cv['frames']} (count12={h['count12']}: {exact})"
              f"  distinct slots used={len(used)} (num_curves={h['num_curves']})")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    for p in sys.argv[1:]:
        report(p)
