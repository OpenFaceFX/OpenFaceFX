"""RESEARCH TOOL — Bethesda .lip payload codec (issue #12; not a supported API).

Clean-room, derived purely from byte analysis of three mod-author-generated
sample files (July 2026). Status: the structural container is SOLVED — this
codec parses each known sample exactly to EOF and re-serializes it
byte-identically from decoded fields. The SEMANTICS are partially decoded:
values are FaceFX curve floats (weight envelopes in [0,1] plus signed Hermite
tangents stored as doubled values), but the marker bytes' meaning and the
per-key time representation are still unknown, which is what blocks a real
.lip exporter. Confidence tags: [V]=verified across all samples,
[I]=inferred, [U]=unknown.

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
    h["duration"] = struct.unpack_from("<I", d, 4)[0]    # [I] duration; unit [U] (ms?)
    h["num_curves"] = struct.unpack_from("<I", d, 8)[0]  # [I] =13 in all samples
    h["count12"] = struct.unpack_from("<H", d, 12)[0]    # [I] key-count-like
    h["const14"] = struct.unpack_from("<H", d, 14)[0]    # [V] =3
    h["neg16"] = struct.unpack_from("<i", d, 16)[0]      # [U] -8/-9
    h["u20"] = struct.unpack_from("<H", d, 20)[0]        # [U]
    h["u22"] = struct.unpack_from("<H", d, 22)[0]        # [U]
    return h


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


def report(path):
    d = open(path, "rb").read()
    h = parse_header(d)
    toks, end = parse_payload(d)
    rt_raw = serialize(d[:24], toks)
    rt_fld = serialize_from_fields(d[:24], toks)
    vals = [t["value"] for t in toks]
    in01 = sum(1 for v in vals if -1e-4 <= v <= 1.0001)
    neg = sum(1 for v in vals if v < -1e-4)
    print(f"\n{path}:")
    print(f"  header={h}")
    print(f"  ntok={len(toks)}  EOF: end={end}/{len(d)} (slop={len(d) - end})")
    print(f"  roundtrip_raw    = {'EXACT' if rt_raw == d else 'FAIL'}")
    print(f"  roundtrip_fields = {'EXACT' if rt_fld == d else 'FAIL'}")
    print(f"  values in [0,1] = {in01}/{len(vals)}   negatives (slopes) = {neg}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    for p in sys.argv[1:]:
        report(p)
