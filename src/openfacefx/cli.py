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
from .export_unity import write_unity_anim
from .mapping import Mapping
from .retarget import retarget, PRESETS
from .timing import (
    parse_pho, parse_piper_alignments, parse_cartesia, parse_azure_visemes,
    parse_polly_marks, resolve_ends, to_segments, viseme_events_to_segments,
    build_vendor_mapping, AZURE_VISEME_TO_TARGET, POLLY_VISEME_TO_TARGET,
)

# TTS timing formats: name -> (parser, is_viseme_unit). Phoneme-unit formats
# feed the existing mapping/coarticulation; viseme-unit formats use the vendor
# remap presets. Piper needs the voice sample_rate to turn sample counts into
# seconds; the rest are self-describing.
_TIMING_PARSERS = {
    "pho": (parse_pho, False),
    "piper": (parse_piper_alignments, False),
    "cartesia": (parse_cartesia, False),
    "azure": (parse_azure_visemes, True),
    "polly": (parse_polly_marks, True),
}
_VISEME_TABLES = {"azure": AZURE_VISEME_TO_TARGET, "polly": POLLY_VISEME_TO_TARGET}


def _write(track, out: str, args=None) -> None:
    if args is not None and args.retarget:
        if out.endswith(".anim"):
            raise SystemExit("--retarget applies to JSON/CSV output; "
                             ".anim output has its own --anim-naming presets")
        track = retarget(track, PRESETS[args.retarget])
    if out.endswith(".csv"):
        write_csv(track, out)
    elif out.endswith(".anim"):
        write_unity_anim(track, out,
                         naming=args.anim_naming if args else "oculus",
                         mesh_path=args.anim_path if args else "Body")
    else:
        write_json(track, out)
    print(f"wrote {out}: {len(track.channels)} channels, "
          f"{sum(len(c.keys) for c in track.channels)} keyframes, "
          f"{track.duration:.2f}s")


def _add_output_options(p) -> None:
    p.add_argument("--mapping",
                   help="JSON phoneme->target mapping file (weighted, "
                        "many-to-many; default: built-in Oculus-15 table)")
    p.add_argument("--retarget", choices=sorted(PRESETS),
                   help="remap viseme channels onto another rig's shapes "
                        "(JSON/CSV output; see docs/retargeting.md)")
    p.add_argument("--anim-naming", choices=["oculus", "vrchat"],
                   default="oculus",
                   help="blendshape naming for .anim output (default: oculus)")
    p.add_argument("--anim-path", default="Body",
                   help="Animator-to-SkinnedMeshRenderer transform path for "
                        ".anim output (default: Body)")


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
    _add_output_options(n)

    m = sub.add_parser("mfa", help="MFA TextGrid -> curves (accurate)")
    m.add_argument("--textgrid", required=True)
    m.add_argument("--fps", type=float, default=60.0)
    m.add_argument("-o", "--out", required=True)
    _add_output_options(m)

    t = sub.add_parser("from-timing",
                       help="TTS phoneme/viseme timing -> curves (skip the "
                            "aligner: espeak .pho, Piper, Cartesia, Azure, Polly)")
    t.add_argument("--file", required=True, help="the timing dump to parse")
    t.add_argument("--format", required=True, choices=sorted(_TIMING_PARSERS),
                   help="pho|piper|cartesia (phoneme-unit) or azure|polly "
                        "(viseme-unit, uses the built-in vendor remap preset)")
    t.add_argument("--sample-rate", type=int,
                   help="Piper voice sample rate in Hz (required for "
                        "--format piper; samples -> seconds)")
    t.add_argument("--final-duration", type=float, default=0.08,
                   help="seconds held by the last start-only event "
                        "(azure/polly); default 0.08")
    t.add_argument("--fps", type=float, default=60.0)
    t.add_argument("-o", "--out", required=True)
    _add_output_options(t)

    b = sub.add_parser("batch", help="process a directory tree of voice lines")
    b.add_argument("--dir", required=True, help="input tree of .wav files "
                   "with same-stem .TextGrid or .txt transcripts")
    b.add_argument("--out", required=True, help="mirrored output tree")
    b.add_argument("--recurse", action="store_true")
    b.add_argument("--modified-only", action="store_true",
                   help="skip files unchanged since the last run (manifest)")
    b.add_argument("--jobs", type=int, default=1, help="parallel workers")
    b.add_argument("--ext", choices=["json", "csv"], default="json")
    b.add_argument("--mapping", dest="batch_mapping",
                   help="mapping JSON applied to every file")
    b.add_argument("--cmudict", dest="batch_cmudict",
                   help="CMUdict file for better G2P on naive-path files")
    b.add_argument("--fps", type=float, default=60.0)

    args = p.parse_args(argv)

    if args.cmd == "batch":
        from .batch import run_batch
        return run_batch(args.dir, args.out, recurse=args.recurse,
                         modified_only=args.modified_only, jobs=args.jobs,
                         mapping=args.batch_mapping,
                         cmudict=args.batch_cmudict,
                         fps=args.fps, ext=args.ext)

    mapping = Mapping.from_json(args.mapping) if args.mapping else None

    if args.cmd == "naive":
        dur = args.duration if args.duration else wav_duration(args.wav)
        g2p = G2P()
        if args.cmudict:
            added = g2p.load_cmudict(args.cmudict)
            print(f"loaded {added} CMUdict entries")
        track = generate_naive(args.text, dur, fps=args.fps, g2p=g2p,
                               mapping=mapping)
        _write(track, args.out, args)
    elif args.cmd == "mfa":
        segs = load_mfa_textgrid(args.textgrid)
        track = generate_from_alignment(segs, fps=args.fps, mapping=mapping)
        _write(track, args.out, args)
    elif args.cmd == "from-timing":
        parser_fn, is_viseme = _TIMING_PARSERS[args.format]
        with open(args.file, encoding="utf-8") as fh:
            text = fh.read()
        if args.format == "piper":
            if not args.sample_rate:
                raise SystemExit("--sample-rate (Hz) is required for --format piper")
            events = parser_fn(text, args.sample_rate)
        else:
            events = parser_fn(text)
        events = resolve_ends(events, final_duration=args.final_duration)
        if is_viseme:
            if mapping is not None:
                raise SystemExit(
                    f"--mapping does not apply to --format {args.format}: viseme "
                    "formats use the built-in vendor remap preset")
            table = _VISEME_TABLES[args.format]
            segs, warnings = viseme_events_to_segments(events, table)
            for w in warnings:
                print(f"warning: {w}")
            track = generate_from_alignment(segs, fps=args.fps,
                                            mapping=build_vendor_mapping(table))
        else:
            segs = to_segments(events)
            track = generate_from_alignment(segs, fps=args.fps, mapping=mapping)
        _write(track, args.out, args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
