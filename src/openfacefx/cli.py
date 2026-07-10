"""Command-line interface.

Examples
--------
Naive (text + audio duration, no models needed):
    python -m openfacefx naive --text "hello world" --wav voice.wav -o out.json

From an MFA alignment (accurate):
    python -m openfacefx mfa --textgrid voice.TextGrid -o out.json
"""

from __future__ import annotations

import argparse
import sys

from .g2p import G2P
from .alignment import load_mfa_textgrid
from .pipeline import generate_naive, generate_from_alignment, wav_duration
from .io_export import write_json, write_csv


def _write(track, out: str) -> None:
    if out.endswith(".csv"):
        write_csv(track, out)
    else:
        write_json(track, out)
    print(f"wrote {out}: {len(track.channels)} channels, "
          f"{sum(len(c.keys) for c in track.channels)} keyframes, "
          f"{track.duration:.2f}s")


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="openfacefx")
    sub = p.add_subparsers(dest="cmd", required=True)

    n = sub.add_parser("naive", help="text + duration -> curves (no models)")
    n.add_argument("--text", required=True)
    g = n.add_mutually_exclusive_group(required=True)
    g.add_argument("--wav", help="WAV file to read duration from")
    g.add_argument("--duration", type=float, help="duration in seconds")
    n.add_argument("--cmudict", help="optional CMUdict file for better G2P")
    n.add_argument("--fps", type=float, default=60.0)
    n.add_argument("-o", "--out", required=True)

    m = sub.add_parser("mfa", help="MFA TextGrid -> curves (accurate)")
    m.add_argument("--textgrid", required=True)
    m.add_argument("--fps", type=float, default=60.0)
    m.add_argument("-o", "--out", required=True)

    args = p.parse_args(argv)

    if args.cmd == "naive":
        dur = args.duration if args.duration else wav_duration(args.wav)
        g2p = G2P()
        if args.cmudict:
            added = g2p.load_cmudict(args.cmudict)
            print(f"loaded {added} CMUdict entries")
        track = generate_naive(args.text, dur, fps=args.fps, g2p=g2p)
        _write(track, args.out)
    elif args.cmd == "mfa":
        segs = load_mfa_textgrid(args.textgrid)
        track = generate_from_alignment(segs, fps=args.fps)
        _write(track, args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
