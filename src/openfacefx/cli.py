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
import json
import math
import sys

from .g2p import G2P
from .alignment import load_mfa_textgrid, dump_segments
from .pipeline import generate_from_alignment, wav_duration
from .io_export import write_json, write_csv
from .export_unity import write_unity_anim
from .export_live2d import write_live2d_motion, lipsync_param_ids
from .export_godot import write_godot_anim
from .export_lip import write_lip, lip_calibrate
from .export_cues import (
    write_rhubarb_tsv, write_rhubarb_xml, write_rhubarb_json,
    write_moho_dat, write_pgo, _RHUBARB_SHAPES,
)
from .mapping import Mapping, ARTICULATOR_CLASSES
from .coarticulation import CoartParams
from .retarget import retarget, PRESETS
from .timing import (
    parse_pho, parse_piper_alignments, parse_cartesia, parse_azure_visemes,
    parse_polly_marks, resolve_ends, to_segments, viseme_events_to_segments,
    build_vendor_mapping, AZURE_VISEME_TO_TARGET, POLLY_VISEME_TO_TARGET,
)
from .ipa import IPA_MAPPING, ipa_unknown_symbols
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


def _load_str_map(path: str, flag: str) -> dict:
    """Load a JSON object of ``str -> str`` for a mapping flag, validated at
    this CLI boundary (a clear SystemExit rather than a stray JSON/type error)."""
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    if not isinstance(data, dict) or not all(
            isinstance(k, str) and isinstance(v, str) for k, v in data.items()):
        raise SystemExit(f"{flag} must be a JSON object mapping strings to strings")
    return data


def _write_live2d(track, out: str, args) -> None:
    if getattr(args, "retarget", None):
        raise SystemExit(
            "--retarget does not apply to Live2D .motion3.json; use "
            "--live2d-params to map visemes onto parameter Ids")
    params_file = getattr(args, "live2d_params", None)
    model3 = getattr(args, "live2d_model3", None)
    if params_file and model3:
        raise SystemExit("--live2d-params and --live2d-model3 are mutually exclusive")
    params = None
    mouth = getattr(args, "live2d_param", None) or "ParamMouthOpenY"
    if params_file:
        params = _load_str_map(params_file, "--live2d-params")
    elif model3:
        ids = lipsync_param_ids(model3)
        if not ids:
            raise SystemExit(f"no Groups:LipSync entry with Ids found in {model3}")
        if len(ids) > 1:
            raise SystemExit(
                f"{model3} LipSync group declares {len(ids)} parameters {ids}; "
                "supply --live2d-params to assign visemes to them")
        mouth = ids[0]
    write_live2d_motion(track, out, params=params, mouth_param=mouth)
    print(f"wrote {out}: Live2D motion3.json, {track.duration:.2f}s")


def _write_godot(track, out: str, args) -> None:
    if getattr(args, "retarget", None):
        raise SystemExit(
            "--retarget does not apply to Godot .tres; it has its own "
            "--godot-naming presets (or pass --godot-names)")
    names_file = getattr(args, "godot_names", None)
    names = _load_str_map(names_file, "--godot-names") if names_file else None
    write_godot_anim(track, out,
                     naming=getattr(args, "godot_naming", "oculus"),
                     node=getattr(args, "godot_node", "Head"), names=names)
    print(f"wrote {out}: Godot .tres Animation, {track.duration:.2f}s")


def _write(track, out: str, args=None) -> None:
    if out.endswith(".lip"):
        raise SystemExit(
            "-o .lip is only supported by the 'naive' and 'mfa' commands: it "
            "writes a Bethesda FaceFX payload from phoneme segments, not from "
            "reduced viseme curves")
    if out.endswith(".motion3.json"):
        _write_live2d(track, out, args)
        return
    if out.endswith(".tres"):
        _write_godot(track, out, args)
        return
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
    p.add_argument("--live2d-param", default="ParamMouthOpenY",
                   help="Cubism parameter Id the default single-curve "
                        ".motion3.json collapses onto (default: ParamMouthOpenY)")
    p.add_argument("--live2d-params",
                   help="JSON viseme->ParamId map for .motion3.json per-"
                        "parameter output (one curve per Id; e.g. per-vowel "
                        "ParamA/I/U/E/O rigs). Overrides --live2d-param")
    p.add_argument("--live2d-model3",
                   help="read the mouth parameter Id from a Cubism model3.json's "
                        "Groups:LipSync entry (.motion3.json single-curve target)")
    p.add_argument("--godot-node", default="Head",
                   help="animated node name for .tres blendshape track paths, "
                        "relative to the AnimationPlayer root (default: Head)")
    p.add_argument("--godot-naming", choices=["oculus", "vrchat"],
                   default="oculus",
                   help="blendshape naming for .tres output (default: oculus)")
    p.add_argument("--godot-names",
                   help="JSON viseme->shape map for .tres output, overriding "
                        "--godot-naming with an explicit blendshape naming")
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
    p.add_argument("--lip-game", choices=["skyrim", "fallout4"], default="skyrim",
                   help="target game for -o out.lip output (naive/mfa only): "
                        "skyrim (EXPERIMENTAL, unverified in-game — see #12); "
                        "fallout4 is not supported (undocumented 43-target "
                        "vocabulary) and raises an error")


def _add_coart_options(p) -> None:
    """Artistic intensity dials, shared by the coarticulation-driven commands
    (naive/mfa/from-timing). The energy command keeps its own --intensity: it
    never builds coarticulated curves (no articulator-class channels — it
    synthesizes an aa/E/O/sil partition straight from an RMS envelope), so the
    CoartParams master does not apply there; its --intensity is an envelope gain
    with different (multiply-then-clamp) semantics, kept as-is."""
    p.add_argument("--intensity", type=float, default=1.0,
                   help="master articulation gain (JALI-style): 1.0 = as-is "
                        "(byte-identical no-op), <1 mumbles, >1 hyper-"
                        "articulates, 0 closes the mouth. Scales every channel's "
                        "opening; 'sil' absorbs the slack; lip closures still win")
    p.add_argument("--gain", action="append", metavar="CLASS=VALUE",
                   help="per-articulator-class gain, repeatable (e.g. --gain "
                        "tongue=0.6 --gain jaw=1.2). CLASS is one of "
                        f"{'/'.join(ARTICULATOR_CLASSES)}; VALUE >= 0 (0 mutes "
                        "the class). Multiplies with --intensity")


def _parse_gains(items):
    """``['class=value', ...]`` -> ``{class: float}``, validated at this CLI
    boundary: a clear SystemExit on an unknown class or non-finite/negative
    value (argparse never sees the value, so we own the errors)."""
    gains = {}
    for item in items or []:
        cls, sep, val = item.partition("=")
        cls = cls.strip()
        if not sep or not cls:
            raise SystemExit(f"--gain expects CLASS=VALUE, got {item!r}")
        if cls not in ARTICULATOR_CLASSES:
            raise SystemExit(f"--gain: unknown articulator class {cls!r} "
                             f"(use one of {', '.join(ARTICULATOR_CLASSES)})")
        try:
            g = float(val)
        except ValueError:
            raise SystemExit(f"--gain {cls}=: {val!r} is not a number")
        if not math.isfinite(g) or g < 0.0:
            raise SystemExit(f"--gain {cls}=: value must be finite and >= 0, "
                             f"got {val!r}")
        gains[cls] = g
    return gains


def _coart_params(args):
    """CoartParams from --intensity/--gain, or None when both are neutral so the
    default (byte-identical) code path is taken unchanged."""
    intensity = getattr(args, "intensity", 1.0)
    if not math.isfinite(intensity) or intensity < 0.0:
        raise SystemExit(f"--intensity must be finite and >= 0, got {intensity}")
    gains = _parse_gains(getattr(args, "gain", None))
    if intensity == 1.0 and not gains:
        return None
    p = CoartParams()
    p.intensity = intensity
    p.gains = {**p.gains, **gains}
    return p


def _anchored_segments(args, dur, g2p):
    """Phoneme segments from the naive aligner pinned at --anchors. Only `srt`
    can supply its own transcript (concatenated cue text); every other format
    needs --text, and `google` needs it up front to resolve its wN marks."""
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
    return anchored_segments(transcript, dur, anchors, g2p=g2p)


def _naive_input_segments(args, dur, g2p):
    """Phoneme segments for the ``naive`` command: anchored if --anchors, else
    the transcript spread over the duration. The shared entry point for both the
    curve exporters and the .lip writer (which needs segments, not a track)."""
    if args.anchors:
        return _anchored_segments(args, dur, g2p)
    if not args.text:
        raise SystemExit(
            "--text is required (or pass --anchors with --anchors-format)")
    from .pipeline import naive_segments
    return naive_segments(args.text, dur, g2p=g2p)


def _emit_segments(segs, args) -> None:
    """Dump the phoneme segments as JSON for the HTML previewer's --segments
    lane, when --emit-segments PATH was given (naive/mfa). Independent of the
    track output, so it works alongside any -o format including .lip."""
    path = getattr(args, "emit_segments", None)
    if not path:
        return
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(dump_segments(segs), fh, indent=2)
    print(f"wrote {path}: {len(segs)} phoneme segments "
          "(preview with build_preview.py --segments)")


def _write_lip(segs, dur, args) -> None:
    """Dispatch ``-o out.lip`` (naive/mfa). EXPERIMENTAL, unverified in-game."""
    try:
        write_lip(segs, dur, args.out, game=getattr(args, "lip_game", "skyrim"))
    except (NotImplementedError, ValueError) as e:
        raise SystemExit(str(e))
    print(f"wrote {args.out}: EXPERIMENTAL Bethesda .lip "
          f"({getattr(args, 'lip_game', 'skyrim')}), {dur:.2f}s — "
          "UNVERIFIED in-game, please report on issue #12")


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
    n.add_argument("--emit-segments", metavar="PATH",
                   help="also write phoneme segments as JSON for the HTML "
                        "previewer's --segments lane (see tools/build_preview.py)")
    _add_output_options(n)
    _add_coart_options(n)

    m = sub.add_parser("mfa", help="MFA TextGrid -> curves (accurate)")
    m.add_argument("--textgrid", required=True)
    m.add_argument("--fps", type=float, default=60.0)
    m.add_argument("-o", "--out", required=True)
    m.add_argument("--emit-segments", metavar="PATH",
                   help="also write phoneme segments as JSON for the HTML "
                        "previewer's --segments lane (see tools/build_preview.py)")
    _add_output_options(m)
    _add_coart_options(m)

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
    _add_coart_options(t)

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

    lc = sub.add_parser("lip-calibrate",
                        help="EXPERIMENTAL: write one .lip per grid slot "
                             "(slot_NN.lip, single slot swept 0->1->0) to map "
                             "slots to mouth targets in-game (issue #12)")
    lc.add_argument("--out", required=True,
                    help="output directory for slot_NN.lip + README.txt")
    lc.add_argument("--seconds", type=float, default=2.0,
                    help="sweep length per slot (default 2.0)")
    lc.add_argument("--lip-game", default="skyrim", choices=["skyrim"],
                    help="target game (Skyrim only; #12)")

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

    if args.cmd == "lip-calibrate":
        written = lip_calibrate(args.out, game=args.lip_game,
                                seconds=args.seconds)
        print(f"wrote {len(written)} calibration lips to {args.out} — "
              f"EXPERIMENTAL: load each on a voiced line in-game and report "
              f"which mouth part moves on issue #12")
        return 0

    if args.cmd == "batch":
        from .batch import run_batch
        return run_batch(args.dir, args.out, recurse=args.recurse,
                         modified_only=args.modified_only, jobs=args.jobs,
                         mapping=args.batch_mapping,
                         cmudict=args.batch_cmudict,
                         fps=args.fps, ext=args.ext)

    mapping = Mapping.from_json(args.mapping) if args.mapping else None
    params = (_coart_params(args)
              if args.cmd in ("naive", "mfa", "from-timing") else None)

    if args.cmd == "naive":
        if bool(args.anchors) != bool(args.anchors_format):
            raise SystemExit(
                "--anchors and --anchors-format are only valid together")
        dur = args.duration if args.duration else wav_duration(args.wav)
        g2p = G2P()
        if args.cmudict:
            added = g2p.load_cmudict(args.cmudict)
            print(f"loaded {added} CMUdict entries")
        segs = _naive_input_segments(args, dur, g2p)
        _emit_segments(segs, args)
        if args.out.endswith(".lip"):
            _write_lip(segs, dur, args)
        else:
            track = generate_from_alignment(segs, fps=args.fps, mapping=mapping,
                                            params=params)
            _write(track, args.out, args)
    elif args.cmd == "mfa":
        segs = load_mfa_textgrid(args.textgrid)
        _emit_segments(segs, args)
        if args.out.endswith(".lip"):
            _write_lip(segs, segs[-1].end if segs else 0.0, args)
        else:
            track = generate_from_alignment(segs, fps=args.fps, mapping=mapping,
                                            params=params)
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
                                            mapping=build_vendor_mapping(table),
                                            params=params)
        else:
            segs = to_segments(events)
            active = mapping
            if active is None:
                # pho (MBROLA SAMPA) and piper/cartesia (IPA) don't speak
                # ARPABET, so default them to the built-in IPA preset; an
                # explicit --mapping still wins. Unknown symbols route to
                # silence with a QA warning, once per distinct symbol.
                active = IPA_MAPPING
                for w in ipa_unknown_symbols(e.symbol for e in events):
                    print(f"warning: {w}")
            track = generate_from_alignment(segs, fps=args.fps, mapping=active,
                                            params=params)
        _write(track, args.out, args)
    elif args.cmd == "energy":
        from .energy import generate_from_energy
        track = generate_from_energy(args.wav, fps=args.fps,
                                     intensity=args.intensity, mapping=mapping)
        _write(track, args.out, args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
