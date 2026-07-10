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
from .export_cues import (
    write_rhubarb_tsv, write_rhubarb_xml, write_rhubarb_json,
    write_moho_dat, write_pgo, _RHUBARB_SHAPES,
)
from .mapping import Mapping
from .retarget import retarget, PRESETS
from .timing import (
    parse_pho, parse_piper_alignments, parse_cartesia, parse_azure_visemes,
    parse_polly_marks, resolve_ends, to_segments, viseme_events_to_segments,
    build_vendor_mapping, AZURE_VISEME_TO_TARGET, POLLY_VISEME_TO_TARGET,
)
from .anchors import (
    anchored_segments, anchors_transcript, parse_srt, parse_word_anchors,
    from_azure_word_boundaries, from_elevenlabs_alignment, from_kokoro_tokens,
    from_google_timepoints,
)

# Anchor timing formats: name -> parser(text) -> List[Anchor]. `google` is
# handled separately (its markN timepoints need the transcript to resolve).
_ANCHOR_PARSERS = {
    "srt": parse_srt,
    "words": parse_word_anchors,
    "azure": from_azure_word_boundaries,
    "elevenlabs": from_elevenlabs_alignment,
    "kokoro": from_kokoro_tokens,
}
_ANCHOR_FORMATS = list(_ANCHOR_PARSERS) + ["google"]

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


# Cue-list formats reachable by output extension. `.json` is deliberately
# absent: it stays the native track format, so the Rhubarb JSON cue list is
# reached only via --cue-format json-cues.
_CUE_EXT = {".tsv": "tsv", ".xml": "xml", ".dat": "dat", ".pgo": "pgo"}


def _cue_format(out: str, explicit):
    """Cue format for this output, from --cue-format or the file extension."""
    if explicit:
        return explicit
    for ext, fmt in _CUE_EXT.items():
        if out.endswith(ext):
            return fmt
    return None


def _write_cue(track, out: str, fmt: str, args) -> None:
    sound = getattr(args, "cue_sound", None) or "openfacefx"
    fps = getattr(args, "cue_fps", 24)
    shapes = getattr(args, "rhubarb_shapes", None)
    if shapes:
        shapes = set(shapes.upper())
        if not shapes <= _RHUBARB_SHAPES:
            raise SystemExit(f"--rhubarb-shapes must be Rhubarb letters "
                             f"({''.join(sorted(_RHUBARB_SHAPES))}), got {args.rhubarb_shapes!r}")
    if fmt == "tsv":
        write_rhubarb_tsv(track, out, available_shapes=shapes)
    elif fmt == "xml":
        write_rhubarb_xml(track, out, sound_file=sound, available_shapes=shapes)
    elif fmt == "json-cues":
        write_rhubarb_json(track, out, sound_file=sound, available_shapes=shapes)
    elif fmt == "dat":
        write_moho_dat(track, out, fps=fps,
                       preston_blair=getattr(args, "dat_preston_blair", True))
    elif fmt == "pgo":
        write_pgo(track, out, fps=fps, sound_path=sound)
    else:
        raise SystemExit(f"unknown cue format {fmt!r}")
    print(f"wrote {out}: {fmt} cue list, {track.duration:.2f}s")


def _write(track, out: str, args=None) -> None:
    cue_fmt = _cue_format(out, getattr(args, "cue_format", None))
    if cue_fmt:
        if getattr(args, "retarget", None):
            raise SystemExit(
                "--retarget does not apply to cue formats (tsv/xml/json-cues/"
                "dat/pgo); they map to Rhubarb/Preston-Blair shapes automatically")
        _write_cue(track, out, cue_fmt, args)
        return
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
    p.add_argument("--cue-format",
                   choices=["tsv", "xml", "json-cues", "dat", "pgo"],
                   help="write a stepped cue list instead of curves: Rhubarb "
                        "TSV/XML/JSON, Moho/OpenToonz .dat, Papagayo .pgo. "
                        "Inferred from a .tsv/.xml/.dat/.pgo extension; use "
                        "json-cues explicitly since .json stays the native track")
    p.add_argument("--cue-sound", default="openfacefx",
                   help="soundFile/sound-path string embedded in xml/json/pgo "
                        "cue output (default: 'openfacefx' — not your local path)")
    p.add_argument("--cue-fps", type=float, default=24,
                   help="frame rate for .dat/.pgo cue quantization "
                        "(default: 24; .dat requires 24..100, accepts NTSC "
                        "29.97; .pgo stores an integer rate)")
    p.add_argument("--dat-preston-blair", action=argparse.BooleanOptionalAction,
                   default=True,
                   help="use Preston-Blair drawing names in .dat (default; "
                        "required by OpenToonz/Moho). --no-dat-preston-blair "
                        "emits Rhubarb's raw A-H/X letters instead")
    p.add_argument("--rhubarb-shapes",
                   help="restrict Rhubarb tsv/xml/json to these shape letters "
                        "(e.g. ABCDEF); missing extended shapes G/H/X fall back "
                        "to a basic shape per the documented table")


def _anchored_track(args, dur, g2p, mapping):
    """Build a track from the naive aligner pinned at --anchors. Only `srt` can
    supply its own transcript (concatenated cue text); every other format needs
    --text, and `google` needs it up front to resolve its wN marks to words."""
    with open(args.anchors, encoding="utf-8") as fh:
        text = fh.read()
    fmt = args.anchors_format
    if fmt == "srt":
        anchors = parse_srt(text)
        transcript = args.text if args.text else anchors_transcript(anchors)
    else:
        if not args.text:
            raise SystemExit(
                f"--text is required with --anchors-format {fmt} "
                "(only 'srt' supplies the transcript from its cue text)")
        transcript = args.text
        anchors = (from_google_timepoints(text, transcript) if fmt == "google"
                   else _ANCHOR_PARSERS[fmt](text))
    segs = anchored_segments(transcript, dur, anchors, g2p=g2p)
    return generate_from_alignment(segs, fps=args.fps, mapping=mapping)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="openfacefx")
    sub = p.add_subparsers(dest="cmd", required=True)

    n = sub.add_parser("naive", help="text + duration -> curves (no models)")
    n.add_argument("--text", help="transcript (required unless "
                   "--anchors-format srt supplies it from the cue text)")
    g = n.add_mutually_exclusive_group(required=True)
    g.add_argument("--wav", help="WAV file to read duration from")
    g.add_argument("--duration", type=float, help="duration in seconds")
    n.add_argument("--cmudict", help="optional CMUdict file for better G2P")
    n.add_argument("--anchors", help="word/segment timing file that pins the "
                   "aligner at known boundaries (SRT cues or TTS word timings)")
    n.add_argument("--anchors-format", choices=_ANCHOR_FORMATS,
                   help="format of --anchors: srt|words|azure|elevenlabs|kokoro|"
                        "google (only valid together with --anchors)")
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

    e = sub.add_parser("energy",
                       help="audio loudness -> mouth-open curves (no "
                            "transcript; amplitude fallback, not viseme sync)")
    e.add_argument("--wav", required=True,
                   help="16-bit PCM WAV (mono or stereo; stereo is downmixed). "
                        "Convert other codecs first: ffmpeg -c:a pcm_s16le")
    e.add_argument("--intensity", type=float, default=1.0,
                   help="gain on the mouth opening (1.0 = as-is; >1 opens "
                        "wider on quiet speech, <1 is subtler)")
    e.add_argument("--fps", type=float, default=60.0)
    e.add_argument("-o", "--out", required=True)
    _add_output_options(e)

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
        if bool(args.anchors) != bool(args.anchors_format):
            raise SystemExit(
                "--anchors and --anchors-format are only valid together")
        dur = args.duration if args.duration else wav_duration(args.wav)
        g2p = G2P()
        if args.cmudict:
            added = g2p.load_cmudict(args.cmudict)
            print(f"loaded {added} CMUdict entries")
        if args.anchors:
            track = _anchored_track(args, dur, g2p, mapping)
        else:
            if not args.text:
                raise SystemExit(
                    "--text is required (or pass --anchors with --anchors-format)")
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
    elif args.cmd == "energy":
        from .energy import generate_from_energy
        track = generate_from_energy(args.wav, fps=args.fps,
                                     intensity=args.intensity, mapping=mapping)
        _write(track, args.out, args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
