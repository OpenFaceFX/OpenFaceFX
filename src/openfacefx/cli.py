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
from .qa import normalize_transcript, summarize
from .export_unity import write_unity_anim
from .export_live2d import (write_live2d_motion, write_live2d_expression,
                            lipsync_param_ids)
from .export_godot import write_godot_anim
from .export_vmd import write_vmd
from .export_lip import write_lip, lip_calibrate
from .export_cues import (
    write_rhubarb_tsv, write_rhubarb_xml, write_rhubarb_json,
    write_moho_dat, write_pgo, _RHUBARB_SHAPES,
)
from .mapping import Mapping, ARTICULATOR_CLASSES
from .coarticulation import CoartParams, STYLE_PRESETS, style_params
from .retarget import retarget, apply_adjust, PRESETS, PRESET_FALLBACKS
from .timing import (
    parse_pho, parse_piper_alignments, parse_cartesia, parse_azure_visemes,
    parse_polly_marks, parse_voicevox, resolve_ends, to_segments,
    viseme_events_to_segments, build_vendor_mapping, AZURE_VISEME_TO_TARGET,
    POLLY_VISEME_TO_TARGET, VOICEVOX_TO_TARGET,
)
from .ipa import IPA_MAPPING, ipa_unknown_symbols
from .anchors import (
    anchored_segments, anchors_transcript, parse_srt, parse_word_anchors,
    from_azure_word_boundaries, from_elevenlabs_alignment, from_kokoro_tokens,
    from_google_timepoints, from_vosk,
)
from .aligners import (from_whisper_json, from_whisperx, from_gentle,
                      from_gentle_phones)
from .aligners_acoustic import from_allosaurus, from_phone_timestamps
from .export_captions import parse_vtt
from . import facefxwrapper

# Anchor timing formats: name -> parser(text) -> List[Anchor]. `google` and
# `gentle-phones` are handled separately (google's markN timepoints need the
# transcript; gentle-phones produces PhonemeSegments, not anchors).
_ANCHOR_PARSERS = {
    "srt": parse_srt,
    "vtt": parse_vtt,
    "words": parse_word_anchors,
    "azure": from_azure_word_boundaries,
    "elevenlabs": from_elevenlabs_alignment,
    "kokoro": from_kokoro_tokens,
    "whisper": from_whisper_json,
    "whisperx": from_whisperx,
    "gentle": from_gentle,
    "vosk": from_vosk,
}
# Formats whose anchors carry the words, so --text is optional (like srt) — SRT/
# WebVTT cues and the open-source aligners all supply their own transcript.
_SELF_TRANSCRIBING = ("srt", "vtt", "whisper", "whisperx", "gentle", "vosk")
# `gentle-phones`, `allosaurus` and `phones` produce PhonemeSegments directly from
# phone timings (no transcript at all — the acoustic-recognizer path).
_PHONE_SEGMENT_FORMATS = ("gentle-phones", "allosaurus", "phones")
_ANCHOR_FORMATS = list(_ANCHOR_PARSERS) + ["google"] + list(_PHONE_SEGMENT_FORMATS)

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
    "voicevox": (parse_voicevox, True),
}
_VISEME_TABLES = {"azure": AZURE_VISEME_TO_TARGET, "polly": POLLY_VISEME_TO_TARGET,
                  "voicevox": VOICEVOX_TO_TARGET}


# Cue-list formats reachable by output extension. `.json` is deliberately
# absent: it stays the native track format, so the Rhubarb JSON cue list is
# reached only via --cue-format json-cues.
_CUE_EXT = {".tsv": "tsv", ".xml": "xml", ".dat": "dat", ".pgo": "pgo"}


def _say(args, msg: str) -> None:
    """Print a human status line, unless --json has taken over stdout for the
    machine-readable QA summary (which must stay a single JSON object)."""
    if not getattr(args, "json", False):
        print(msg)


def _warn(args, msg: str) -> None:
    """Collect a warning for the QA summary and print it (``warning: <msg>``)
    unless --json owns stdout, where it surfaces only in the summary's
    ``warnings`` list. Byte-identical to the old inline prints without --json."""
    getattr(args, "_warnings", []).append(msg)
    if not getattr(args, "json", False):
        print(f"warning: {msg}")


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
    _say(args, f"wrote {out}: {fmt} cue list, {track.duration:.2f}s")


def _load_str_map(path: str, flag: str) -> dict:
    """Load a JSON object of ``str -> str`` for a mapping flag, validated at
    this CLI boundary (a clear SystemExit rather than a stray JSON/type error)."""
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    if not isinstance(data, dict) or not all(
            isinstance(k, str) and isinstance(v, str) for k, v in data.items()):
        raise SystemExit(f"{flag} must be a JSON object mapping strings to strings")
    return data


def _load_adjust(path: str) -> dict:
    """Load a ``--adjust`` file into a validated ``{target: (gain, offset) | link}``.

    Schema: a JSON object mapping each rig target name to either a linear trim with
    optional numeric ``gain`` (default 1.0) / ``offset`` (default 0.0), e.g.
    ``{"jawOpen": {"gain": 0.8}, "mouthSmileLeft": {"offset": 0.1}}``, or a
    nonlinear **link function** ``{"function": name, ...params}`` (#68), e.g.
    ``{"tongueOut": {"function": "quadratic", "m": 1.4}}`` — see
    :mod:`openfacefx.links`. Validated at this CLI boundary so a malformed file is a
    clear SystemExit, not a stray error deeper in ``apply_adjust``."""
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError) as e:
        raise SystemExit(f"--adjust: cannot read {path!r}: {e}")
    if not isinstance(data, dict):
        raise SystemExit('--adjust must be a JSON object mapping target names to '
                         '{"gain":G,"offset":O} or {"function":name,...} objects')
    adjust = {}
    for target, spec in data.items():
        if not isinstance(spec, dict):
            raise SystemExit(f"--adjust[{target!r}] must be an object with "
                             f"gain/offset or a 'function' link spec, got {spec!r}")
        if "function" in spec:                     # nonlinear link function (#68)
            from .links import normalize_link
            try:
                name, params = normalize_link(spec)
            except ValueError as e:
                raise SystemExit(f"--adjust[{target!r}]: {e}")
            adjust[target] = {"function": name, **params}
            continue
        extra = set(spec) - {"gain", "offset"}
        if extra:
            raise SystemExit(f"--adjust[{target!r}] has unknown key(s) "
                             f"{sorted(extra)}; only 'gain'/'offset' or a "
                             f"'function' link spec allowed")
        pair = []
        for key, default in (("gain", 1.0), ("offset", 0.0)):
            val = spec.get(key, default)
            # JSON booleans are ints in Python; reject them explicitly.
            if isinstance(val, bool) or not isinstance(val, (int, float)) \
                    or not math.isfinite(val):
                raise SystemExit(f"--adjust[{target!r}].{key} must be a finite "
                                 f"number, got {val!r}")
            pair.append(float(val))
        adjust[target] = (pair[0], pair[1])
    return adjust


def _load_shapes(path: str) -> set:
    """Load a ``--retarget-shapes`` file: a JSON array of the rig's real shape
    names, validated at this CLI boundary."""
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError) as e:
        raise SystemExit(f"--retarget-shapes: cannot read {path!r}: {e}")
    if not isinstance(data, list) or not all(isinstance(s, str) for s in data):
        raise SystemExit("--retarget-shapes must be a JSON array of shape-name "
                         "strings the rig actually has")
    if not data:
        raise SystemExit("--retarget-shapes is empty: list at least one shape the "
                         "rig has (an empty set would drop every channel)")
    return set(data)


def _reject_trim_flags(args, what: str) -> None:
    """--adjust/--retarget-shapes only condition the retargeted curve outputs
    (json/csv/anim); reject them on ``what`` rather than silently dropping them."""
    for flag, dash in (("adjust", "--adjust"),
                       ("retarget_shapes", "--retarget-shapes")):
        if getattr(args, flag, None):
            raise SystemExit(f"{dash} does not apply to {what}")


def _live2d_target(args, fmt: str):
    """Resolve ``(params, mouth_param)`` from --live2d-params/--live2d-model3/
    --live2d-param, shared by the motion3.json and exp3.json writers."""
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
    return params, mouth


def _write_live2d(track, out: str, args) -> None:
    if getattr(args, "retarget", None):
        raise SystemExit(
            "--retarget does not apply to Live2D .motion3.json; use "
            "--live2d-params to map visemes onto parameter Ids")
    _reject_trim_flags(args, "Live2D .motion3.json")
    params, mouth = _live2d_target(args, "Live2D .motion3.json")
    write_live2d_motion(track, out, params=params, mouth_param=mouth)
    _say(args, f"wrote {out}: Live2D motion3.json, {track.duration:.2f}s")


def _write_live2d_expression(track, out: str, args) -> None:
    if getattr(args, "retarget", None):
        raise SystemExit(
            "--retarget does not apply to Live2D .exp3.json; use "
            "--live2d-params to map visemes onto parameter Ids")
    _reject_trim_flags(args, "Live2D .exp3.json")
    params, mouth = _live2d_target(args, "Live2D .exp3.json")
    write_live2d_expression(track, out, at=getattr(args, "exp3_at", None),
                            params=params, mouth_param=mouth)
    when = ("peak" if getattr(args, "exp3_at", None) is None
            else f"{args.exp3_at:.2f}s")
    _say(args, f"wrote {out}: Live2D exp3.json expression (pose @ {when})")


def _write_godot(track, out: str, args) -> None:
    if getattr(args, "retarget", None):
        raise SystemExit(
            "--retarget does not apply to Godot .tres; it has its own "
            "--godot-naming presets (or pass --godot-names)")
    _reject_trim_flags(args, "Godot .tres")
    names_file = getattr(args, "godot_names", None)
    names = _load_str_map(names_file, "--godot-names") if names_file else None
    write_godot_anim(track, out,
                     naming=getattr(args, "godot_naming", "oculus"),
                     node=getattr(args, "godot_node", "Head"), names=names)
    _say(args, f"wrote {out}: Godot .tres Animation, {track.duration:.2f}s")


def _write_vmd(track, out: str, args) -> None:
    if getattr(args, "retarget", None):
        raise SystemExit(
            "--retarget does not apply to MMD .vmd; it has its own viseme->morph "
            "map (override in the library via write_vmd(morph_map=...))")
    _reject_trim_flags(args, "MMD .vmd")
    write_vmd(track, out, model_name=getattr(args, "vmd_model", "OpenFaceFX"),
              fps=getattr(args, "vmd_fps", None))
    _say(args, f"wrote {out}: MMD .vmd morph animation, {track.duration:.2f}s")


def _write_vrma(track, out: str, args) -> None:
    if getattr(args, "retarget", None):
        raise SystemExit(
            "--retarget does not apply to VRM .vrma; it maps onto the VRM 1.0 "
            "vowel expressions (aa/ih/ou/ee/oh) internally")
    _reject_trim_flags(args, "VRM .vrma")
    from .export_vrma import write_vrma
    write_vrma(track, out, head_node=getattr(args, "vrma_head_node", False))
    _say(args, f"wrote {out}: VRM Animation (VRMC_vrm_animation) expression clip, "
         f"{track.duration:.2f}s")


def _write_spine(track, out: str, args) -> None:
    if getattr(args, "retarget", None):
        raise SystemExit(
            "--retarget does not apply to Spine .spine.json; it flattens the "
            "track to mouth-slot attachments internally (override the shape->"
            "attachment names with --spine-attachments)")
    _reject_trim_flags(args, "Spine .spine.json")
    from .export_spine import write_spine
    amap = None
    if getattr(args, "spine_attachments", None):
        amap = _load_str_map(args.spine_attachments, "--spine-attachments")
    base = getattr(args, "spine_base", None)
    write_spine(track, out, base=base,
                anim_name=getattr(args, "spine_anim", None) or "lipsync",
                slot=getattr(args, "spine_slot", None) or "mouth",
                attachment_map=amap)
    mode = f"spliced into {base}" if base else "standalone skeleton"
    _say(args, f"wrote {out}: Spine slot-attachment lip-sync ({mode}), "
         f"{track.duration:.2f}s")


def _write_livelink(track, out: str, args) -> None:
    from .export_livelink import write_livelink_csv
    matched = write_livelink_csv(track, out,
                                 fps=getattr(args, "livelink_fps", None))
    if matched == 0:
        _warn(args, "livelink: no ARKit blendshape channels matched — the track is "
                    "likely in viseme space; pass --retarget arkit to map it first")
    _say(args, f"wrote {out}: ARKit / Live Link Face CSV, {track.duration:.2f}s")


def _write_a2f(track, out: str, args) -> None:
    from .export_a2f import write_a2f
    matched = write_a2f(track, out, fps=getattr(args, "a2f_fps", None))
    if matched == 0:
        _warn(args, "a2f: no ARKit blendshape channels matched — the track is "
                    "likely in viseme space; pass --retarget arkit to map it first")
    _say(args, f"wrote {out}: Audio2Face blendshape JSON, {track.duration:.2f}s")


def _write(track, out: str, args=None) -> None:
    if out.endswith(".lip"):
        raise SystemExit(
            "-o .lip is only supported by the 'naive' and 'mfa' commands: it "
            "writes a Bethesda FaceFX payload from phoneme segments, not from "
            "reduced viseme curves")
    if out.endswith(".motion3.json"):
        _write_live2d(track, out, args)
        return
    if out.endswith(".exp3.json"):
        _write_live2d_expression(track, out, args)
        return
    if out.endswith(".tres"):
        _write_godot(track, out, args)
        return
    if out.endswith(".vmd"):
        _write_vmd(track, out, args)
        return
    if out.endswith(".vrma"):
        _write_vrma(track, out, args)
        return
    if out.endswith(".spine.json"):
        _write_spine(track, out, args)
        return
    if out.endswith((".gltf", ".glb")):
        from .export_gltf import write_gltf
        write_gltf(track, out, head_node=getattr(args, "gltf_head_node", False))
        _say(args, f"wrote {out}: glTF 2.0 morph-target animation, "
             f"{len(track.channels)} channels, {track.duration:.2f}s")
        return
    cue_fmt = _cue_format(out, getattr(args, "cue_format", None))
    if cue_fmt:
        if getattr(args, "retarget", None):
            raise SystemExit(
                "--retarget does not apply to cue formats (tsv/xml/json-cues/"
                "dat/pgo); they map to Rhubarb/Preston-Blair shapes automatically")
        _reject_trim_flags(args, "cue formats (tsv/xml/json-cues/dat/pgo)")
        _write_cue(track, out, cue_fmt, args)
        return
    if args is not None and args.retarget:
        if out.endswith(".anim"):
            raise SystemExit("--retarget applies to JSON/CSV output; "
                             ".anim output has its own --anim-naming presets")
        available, fallbacks = None, None
        if getattr(args, "retarget_shapes", None):
            available = _load_shapes(args.retarget_shapes)
            fallbacks = PRESET_FALLBACKS.get(args.retarget)
        track = _apply_retarget(track, PRESETS[args.retarget], available, fallbacks)
    elif args is not None and getattr(args, "retarget_shapes", None):
        raise SystemExit("--retarget-shapes needs --retarget (it filters the "
                         "chosen rig preset's shapes); pass --retarget too")
    if args is not None and getattr(args, "adjust", None):
        track = apply_adjust(track, _load_adjust(args.adjust))
    if out.endswith(".a2f.json"):
        _write_a2f(track, out, args)
    elif out.endswith(".livelink.csv"):
        _write_livelink(track, out, args)
    elif out.endswith(".csv"):
        write_csv(track, out)
    elif out.endswith(".anim"):
        write_unity_anim(track, out,
                         naming=args.anim_naming if args else "oculus",
                         mesh_path=args.anim_path if args else "Body")
    else:
        write_json(track, out)
    _say(args, f"wrote {out}: {len(track.channels)} channels, "
         f"{sum(len(c.keys) for c in track.channels)} keyframes, "
         f"{track.duration:.2f}s")


def _add_output_options(p) -> None:
    p.add_argument("--mapping",
                   help="JSON phoneme->target mapping file (weighted, "
                        "many-to-many; default: built-in Oculus-15 table)")
    p.add_argument("--retarget", choices=sorted(PRESETS),
                   help="remap viseme channels onto another rig's shapes "
                        "(JSON/CSV output; see docs/retargeting.md)")
    p.add_argument("--retarget-shapes", metavar="JSON",
                   help="restrict --retarget to the rig's real shapes: a JSON "
                        "array of shape names the rig has. A mapped target it "
                        "lacks reroutes through the preset's fallback table "
                        "(e.g. a tongue-less ARKit rig sends tongueOut to a "
                        "small jawOpen) — the library available=/fallbacks= path")
    p.add_argument("--adjust", metavar="JSON",
                   help="per-target gain/offset trim applied after --retarget "
                        "(curve output: json/csv/anim): a JSON object "
                        '{"target":{"gain":G,"offset":O}} remapping each shape as '
                        "clamp(gain*v+offset,0,1). Leaves the preset weight tables "
                        "untouched — e.g. soften jawOpen, hold mouthSmile slightly "
                        "on. See docs/retargeting.md")
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
    p.add_argument("--exp3-at", type=float, default=None,
                   help="for .exp3.json output, the time (seconds) to freeze the "
                        "pose at; default is the peak-activity frame. Reuses the "
                        "--live2d-param/-params/-model3 targeting")
    p.add_argument("--godot-node", default="Head",
                   help="animated node name for .tres blendshape track paths, "
                        "relative to the AnimationPlayer root (default: Head)")
    p.add_argument("--godot-naming", choices=["oculus", "vrchat"],
                   default="oculus",
                   help="blendshape naming for .tres output (default: oculus)")
    p.add_argument("--godot-names",
                   help="JSON viseme->shape map for .tres output, overriding "
                        "--godot-naming with an explicit blendshape naming")
    p.add_argument("--gltf-head-node", action="store_true",
                   help="for .gltf/.glb output, also encode the signed head pose "
                        "channels (headPitch/Yaw/Roll) as a separate node "
                        "'rotation' (Euler->quaternion) animation, kept distinct "
                        "from the [0,1] morph-weight targets")
    p.add_argument("--vrma-head-node", action="store_true",
                   help="for .vrma output, also map the signed head pose "
                        "(headPitch/Yaw/Roll) onto VRM humanoid.humanBones.head "
                        "as a quaternion 'rotation' animation")
    p.add_argument("--vmd-model", default="OpenFaceFX",
                   help="model name embedded in .vmd output (ShiftJIS, <=20 "
                        "bytes; MMD shows it but a morph-only motion ignores it; "
                        "default: OpenFaceFX)")
    p.add_argument("--vmd-fps", type=_positive_float, default=30.0,
                   help="frame rate for .vmd frame numbers (MMD-native default "
                        "30; frame# = round(time*fps), independent of the solver "
                        "sampling fps)")
    p.add_argument("--livelink-fps", type=_positive_float, default=60.0,
                   help="frame rate for .livelink.csv rows (ARKit / Live Link Face "
                        "wide CSV; default 60, Live Link's rate). Retarget viseme "
                        "tracks with --retarget arkit first")
    p.add_argument("--a2f-fps", type=_positive_float, default=None,
                   help="exportFps for .a2f.json (NVIDIA Audio2Face blendshape "
                        "JSON); default is the track's own fps. Retarget viseme "
                        "tracks with --retarget arkit first")
    p.add_argument("--spine-base", metavar="JSON",
                   help="for .spine.json output, splice the mouth timeline into "
                        "this existing Spine skeleton JSON (leaves bones/skins/"
                        "other animations untouched); omit for a standalone stub")
    p.add_argument("--spine-anim", default="lipsync",
                   help="animation name for .spine.json output (default: lipsync)")
    p.add_argument("--spine-slot", default="mouth",
                   help="mouth slot name for .spine.json output (default: mouth)")
    p.add_argument("--spine-attachments", metavar="JSON",
                   help="JSON map of Rhubarb A-H/X mouth shapes to Spine "
                        "attachment names for .spine.json (default: mouth_a.."
                        "mouth_x)")
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
    with different (multiply-then-clamp) semantics, kept as-is. --style and
    --stress-emphasis are scoped here for the same reason: energy has no
    articulator-class channels for a style's gains to act on, nor phoneme stress
    digits for the emphasis pass to read."""
    p.add_argument("--style", choices=sorted(STYLE_PRESETS),
                   help="named delivery-style preset (issue #18) loading JALI-"
                        f"style intensity/gain dials ({'/'.join(sorted(STYLE_PRESETS))}). "
                        "'neutral' is byte-identical to no --style; 'mumble'/"
                        "'whisper' soften and close, 'exaggerated'/'broad' open "
                        "and hyper-articulate. Explicit --intensity/--gain compose "
                        "on top and win; lip closures still seal")
    p.add_argument("--intensity", type=float, default=None,
                   help="master articulation gain (JALI-style): 1.0 = as-is "
                        "(byte-identical no-op), <1 mumbles, >1 hyper-"
                        "articulates, 0 closes the mouth. Scales every channel's "
                        "opening; 'sil' absorbs the slack; lip closures still win. "
                        "Overrides a --style preset's master intensity")
    p.add_argument("--gain", action="append", metavar="CLASS=VALUE",
                   help="per-articulator-class gain, repeatable (e.g. --gain "
                        "tongue=0.6 --gain jaw=1.2). CLASS is one of "
                        f"{'/'.join(ARTICULATOR_CLASSES)}; VALUE >= 0 (0 mutes "
                        "the class). Multiplies with --intensity; overrides a "
                        "--style preset's gain for that class")
    p.add_argument("--stress-emphasis", type=float, nargs="?", const=0.5,
                   default=0.0, metavar="AMOUNT",
                   help="lexical-stress amplitude pass (issue #18): bias the "
                        "dominance of ARPABET primary/secondary-stressed vowels "
                        "up and unstressed ones down so stressed syllables "
                        "articulate more strongly. Bare flag = 0.5; range 0..2; "
                        "0 = off (byte-identical). Preserves the per-frame "
                        "partition and lip closures; a graceful no-op on inputs "
                        "without stress digits (vendor/IPA timing)")


def _add_smoothing_options(p) -> None:
    """Opt-in post-solve curve conditioning (issue #10), shared by naive/mfa/
    from-timing/energy. Both default to a no-op so existing output stays byte-
    identical. Unlike --intensity/--gain these also apply in energy mode (they
    condition the reduced curves, not the coarticulation model)."""
    p.add_argument("--smooth", type=float, default=0.0, metavar="SECONDS",
                   help="temporal Gaussian smoothing of the dense curves before "
                        "keyframe reduction (sigma in seconds, e.g. 0.02). Softens "
                        "jitter; lip closures are re-sealed afterwards so /p/ /b/ "
                        "/m/ /f/ /v/ stay sharp. 0 = off (byte-identical)")
    p.add_argument("--lag", type=float, default=0.0, metavar="MS",
                   help="slide every viseme curve in time by +/- milliseconds so "
                        "the mouth trails (positive, lag) or leads (negative) the "
                        "audio; keys are clamped into the clip so its length is "
                        "unchanged. 0 = off (byte-identical)")


def _smooth_seconds(args) -> float:
    """Validated --smooth (sigma seconds) at the CLI boundary."""
    s = getattr(args, "smooth", 0.0)
    if not math.isfinite(s) or s < 0.0:
        raise SystemExit(f"--smooth must be a finite, non-negative number of "
                         f"seconds, got {s}")
    return s


def _lag_seconds(args) -> float:
    """Validated --lag, converting the CLI's milliseconds to seconds."""
    ms = getattr(args, "lag", 0.0)
    if not math.isfinite(ms):
        raise SystemExit(f"--lag must be a finite number of milliseconds, got {ms}")
    return ms / 1000.0


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


def _stress_emphasis(args) -> float:
    """Validated --stress-emphasis amount at the CLI boundary (0 = off). The bare
    flag is 0.5 (argparse const); an explicit value must be finite in [0, 2] (the
    cap keeps the unstressed-vowel dominance cut positive in ``_stress_gains``)."""
    amt = getattr(args, "stress_emphasis", 0.0)
    if amt is None:                    # defensive; argparse supplies const/default
        return 0.0
    if not math.isfinite(amt) or not (0.0 <= amt <= 2.0):
        raise SystemExit(f"--stress-emphasis must be a finite number in [0, 2] "
                         f"(0 = off), got {amt}")
    return amt


def _coart_params(args):
    """CoartParams from a --style preset plus the explicit --intensity/--gain/
    --stress-emphasis/--smooth/--lag dials, or None when they net to the defaults
    so the byte-identical code path is taken unchanged.

    A --style preset seeds the dials; explicit flags compose on top and win — an
    omitted --intensity keeps the preset's master, --gain merges per class over
    it. --style neutral with no other dial collapses to None, exactly like
    passing no params at all (that is the byte-identity guarantee)."""
    p = style_params(args.style) if getattr(args, "style", None) else CoartParams()
    intensity = getattr(args, "intensity", None)
    if intensity is not None:
        if not math.isfinite(intensity) or intensity < 0.0:
            raise SystemExit(f"--intensity must be finite and >= 0, got {intensity}")
        p.intensity = intensity
    p.gains = {**p.gains, **_parse_gains(getattr(args, "gain", None))}
    p.stress_emphasis = _stress_emphasis(args)
    p.smooth = _smooth_seconds(args)
    p.lag = _lag_seconds(args)
    # Nothing actually moved off the defaults -> take the untouched path, so
    # --style neutral / neutral dials stay byte-identical to no params at all.
    return None if p == CoartParams() else p


def _add_gesture_options(p) -> None:
    """Opt-in non-verbal gesture layer (issue #5), shared by naive/mfa/energy.
    OFF by default so existing output stays byte-identical."""
    p.add_argument("--gestures", action="store_true",
                   help="append procedural blink/brow/head/eye channels (issue "
                        "#5): Poisson blinks snapped to pauses/stress, eyebrow "
                        "flashes and head nods on energy/stress, ambient sway and "
                        "gaze saccades. Deterministic; OFF by default. Applies to "
                        "curve outputs (json/csv/anim/godot/live2d), not the "
                        "mouth-only cue/.lip formats")
    p.add_argument("--gesture-seed", type=int, default=0,
                   help="RNG seed for --gestures (default 0); same seed + audio "
                        "+ params gives identical keyframes")
    p.add_argument("--blink-rate", type=float,
                   help="blinks per minute for --gestures (default ~15)")
    p.add_argument("--no-brows", action="store_true",
                   help="disable the eyebrow-flash channel in --gestures")
    p.add_argument("--breath", action="store_true",
                   help="add an idle procedural breathing channel ('breath', [0,1]) "
                        "to --gestures: a slow chest rise/fall (~15/min) for a rig "
                        "with a breath target (e.g. Live2D ParamBreath)")


def _gesture_params(args):
    """GestureParams from the --gestures flags, or None when --gestures is absent
    (the default, byte-identical path)."""
    if not getattr(args, "gestures", False):
        return None
    from .gestures import GestureParams
    p = GestureParams()
    p.seed = getattr(args, "gesture_seed", 0)
    rate = getattr(args, "blink_rate", None)
    if rate is not None:
        if not math.isfinite(rate) or rate <= 0.0:
            raise SystemExit(f"--blink-rate must be a positive blinks-per-minute "
                             f"value, got {rate}")
        p.blink_mean_interval = 60.0 / rate
    if getattr(args, "no_brows", False):
        p.brow_enable = False
    if getattr(args, "breath", False):
        p.breath_enable = True
    return p


def _add_event_options(p) -> None:
    """Opt-in event/take layer (issue #6), shared by naive/mfa/energy. OFF by
    default so existing output stays byte-identical."""
    p.add_argument("--events", action="store_true",
                   help="auto-author a typed event layer (issue #6): 'emphasis' "
                        "events on stressed syllables / loudness peaks and "
                        "'phrase' boundary markers at pauses. Carried into JSON "
                        "output and Unity .anim AnimationEvents; ignored by the "
                        "mouth-only cue/.lip formats. Deterministic; composes "
                        "with --gestures")
    p.add_argument("--events-file", metavar="JSON",
                   help="attach an authored events/variants ('takes') layer from "
                        "a JSON file — same {\"events\":[...],\"variants\":{...}} "
                        "schema as the track block; merged with --events if both "
                        "are given")
    p.add_argument("--line-id", metavar="ID",
                   help="line id that deterministically resolves variant 'takes' "
                        "by hashing the id with SHA-256 (same id -> same take, "
                        "across runs/OSes); bakes variants into concrete events "
                        "on write")


def _add_prosody_options(p) -> None:
    """Opt-in audio-derived prosody events (issue #4), shared by naive/mfa/energy.
    OFF by default so existing output stays byte-identical."""
    p.add_argument("--prosody", action="store_true",
                   help="auto-author prosody events (issue #4) from the audio's "
                        "pitch and loudness: 'emphasis' beats (coincident pitch+"
                        "energy peaks), 'phrase_boundary' markers (pauses / end) "
                        "and 'question_rise' markers (rising terminal F0). Needs "
                        "--wav (naive/mfa/energy). DSP heuristics, not an ML "
                        "prosody model; deterministic. Composes with --events/"
                        "--gestures; ignored by the mouth-only cue/.lip formats")


def _add_edits_options(p) -> None:
    """Opt-in edit-preservation layer (issue #9), shared by naive/mfa/from-timing/
    energy. Overlays a hand-edit sidecar onto the freshly generated curves so
    manual tweaks survive a re-run. OFF by default (no --edits) => byte-identical."""
    p.add_argument("--edits", metavar="FILE",
                   help="apply an openfacefx.edits sidecar (author one with the "
                        "'diff-edits' command): user-owned offset/replace channels "
                        "and locked regions overlay the generated curves so hand-"
                        "edits survive regeneration. Deterministic; composes with "
                        "--gestures/--events. OFF by default (byte-identical)")
    p.add_argument("--on-conflict", choices=["keep-edit", "take-generated"],
                   default="keep-edit",
                   help="when an edit targets a channel the regeneration dropped "
                        "(renamed / word removed): keep-edit re-injects it "
                        "(default, a hand-edit is never lost), take-generated "
                        "discards it for the fresh output. Either way it is warned")


def _add_qa_options(p) -> None:
    """Machine-readable QA output for scripting/CI (issue #23), shared by naive/
    mfa/from-timing/energy. All opt-in: without --json/--report the console and
    the written track are byte-identical to before."""
    p.add_argument("--json", action="store_true",
                   help="emit a single-line machine-readable JSON QA summary "
                        "(format openfacefx.qa) to stdout INSTEAD of the human "
                        "'wrote ...' line: output, fps, duration, channel/keyframe"
                        "/gesture/event counts, oov_words, cue_warnings, "
                        "normalization substitutions and warnings[]. The written "
                        "track file itself is unchanged")
    p.add_argument("--report", metavar="FILE",
                   help="also write the JSON QA summary (see --json) to FILE, "
                        "indented, keeping the human console output as-is")
    p.add_argument("--min-cue", type=float, default=None, metavar="SECONDS",
                   help="flag phoneme cues shorter than this in the QA summary's "
                        "cue_warnings (default 0.03; a too-short viseme clicks)")
    p.add_argument("--max-cue", type=float, default=None, metavar="SECONDS",
                   help="flag phoneme cues longer than this in the QA summary's "
                        "cue_warnings (default 0.5; a too-long one sticks)")
    p.add_argument("--min-confidence", type=float, default=None, metavar="SCORE",
                   help="flag phonemes whose aligner confidence is below this in "
                        "the QA summary's confidence_warnings (default 0.5; only "
                        "populates when your aligner supplies per-phone confidence)")


def _min_confidence(args) -> float:
    """Validated low-confidence threshold in [0, 1]; --min-confidence overrides
    qa's default, at this CLI boundary."""
    from .qa import MIN_CONFIDENCE
    v = getattr(args, "min_confidence", None)
    if v is None:
        return MIN_CONFIDENCE
    if not math.isfinite(v) or not (0.0 <= v <= 1.0):
        raise SystemExit(f"--min-confidence must be a number in [0, 1], got {v}")
    return v


def _want_summary(args) -> bool:
    return bool(getattr(args, "json", False) or getattr(args, "report", None))


def _cue_thresholds(args):
    """Validated (min, max) cue-duration thresholds in seconds; --min-cue/
    --max-cue override qa's defaults, at this CLI boundary."""
    from .qa import MIN_CUE, MAX_CUE
    lo, hi = getattr(args, "min_cue", None), getattr(args, "max_cue", None)
    for name, v in (("--min-cue", lo), ("--max-cue", hi)):
        if v is not None and (not math.isfinite(v) or v < 0.0):
            raise SystemExit(f"{name} must be a finite, non-negative number of "
                             f"seconds, got {v}")
    lo = MIN_CUE if lo is None else lo
    hi = MAX_CUE if hi is None else hi
    if lo > hi:
        raise SystemExit(f"--min-cue ({lo}) must not exceed --max-cue ({hi})")
    return lo, hi


def _emit_summary(args, track, *, segments=None, oov_words=None,
                  substitutions=None) -> None:
    """Emit the machine-readable QA summary for a generate command: --json prints
    a single-line JSON object to stdout, --report FILE writes it indented. A
    no-op without either flag, so default runs stay byte-identical."""
    if not _want_summary(args):
        return
    lo, hi = _cue_thresholds(args)
    doc = summarize(track, output=args.out, command=args.cmd, segments=segments,
                    oov_words=oov_words, substitutions=substitutions,
                    warnings=args._warnings, min_cue=lo, max_cue=hi,
                    min_confidence=_min_confidence(args))
    report = getattr(args, "report", None)
    if report:
        try:
            with open(report, "w", encoding="utf-8") as fh:
                json.dump(doc, fh, indent=2)
        except OSError as e:
            raise SystemExit(f"--report: cannot write {report!r}: {e}")
    if getattr(args, "json", False):
        print(json.dumps(doc))


def _apply_edits_layer(track, args):
    """Overlay an --edits sidecar (issue #9) onto ``track`` and return the merged
    track. A no-op returning ``track`` unchanged without --edits, so output stays
    byte-identical. Conflicts (edits on now-absent channels) are printed."""
    path = getattr(args, "edits", None)
    if not path:
        return track
    from .edits import load_edits, apply_edits
    try:
        doc = load_edits(path)
    except (OSError, ValueError) as e:
        raise SystemExit(f"--edits: cannot load {path!r}: {e}")
    merged, conflicts = apply_edits(
        track, doc, on_conflict=getattr(args, "on_conflict", "keep-edit"))
    for c in conflicts:
        _warn(args, f"edit conflict on {c['channel']}: {c['detail']}")
    return merged


def _attach_prosody(track, args, segments) -> None:
    """Run the issue-#4 pitch/loudness event detector on --wav and append the
    typed events onto the track. Errors clearly if the command has no audio (a
    naive run given --duration, or an mfa run without --wav): prosody is audio-
    derived and cannot fall back to timing alone."""
    wav = getattr(args, "wav", None)
    if not wav:
        raise SystemExit(
            "--prosody needs audio to analyse (pitch + loudness): pass --wav. "
            "For 'naive' use --wav rather than --duration; 'mfa' takes an "
            "optional --wav alongside the TextGrid.")
    from .prosody import prosody_events
    from .events import attach_events
    # Analysed at the module's own ~100 Hz rate (event times are absolute
    # seconds), so the events are independent of the render --fps.
    attach_events(track, events=prosody_events(wav, segments=segments))


def _event_layer(track, args, segments=None, env_times=None, env=None) -> None:
    """Attach / auto-derive / bake the issue-#6 event layer per --events /
    --events-file / --line-id, plus issue-#4 audio prosody events per --prosody.
    A no-op (byte-identical output) when none are set. Variant 'takes' are baked
    into concrete events once a line id is known (from --line-id or embedded in
    the file); otherwise the authoring form is kept."""
    want = getattr(args, "events", False)
    efile = getattr(args, "events_file", None)
    line_id = getattr(args, "line_id", None)
    prosody = getattr(args, "prosody", False)
    if not (want or efile or line_id or prosody):
        return
    from .events import attach_events, read_events, resolve, validate_events
    if want:
        from .pipeline import derive_events
        attach_events(track, events=derive_events(segments, env_times, env))
    if prosody:
        _attach_prosody(track, args, segments)
    if efile:
        try:
            with open(efile, encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, ValueError) as e:
            raise SystemExit(f"--events-file: cannot read {efile!r}: {e}")
        events, variants = read_events(data)
        attach_events(track, events=events, variants=variants)
    if line_id is not None and track.variants is not None:
        track.variants.line_id = line_id
    if track.variants is not None and track.variants.line_id is not None:
        track.events = resolve(track)          # bake the chosen take into events
        track.variants = None
    for w in validate_events(track.events):
        _warn(args, w)


def _apply_retarget(track, preset, available=None, fallbacks=None):
    """Retarget the viseme channels onto a rig while passing gesture/pose
    (issue #5) and emotion/expression (issue #38) channels through unchanged --
    ``retarget`` drops channels absent from the viseme map by design, which would
    otherwise delete those layers.

    ``available``/``fallbacks`` (from --retarget-shapes) restrict the rig's real
    shapes and reroute the rest, exactly as the library ``retarget`` args do."""
    from .gestures import GESTURE_CHANNELS
    from .emotion import VA_EMOTION_CHANNELS
    from .curves import FaceTrack
    passthru = GESTURE_CHANNELS | frozenset(VA_EMOTION_CHANNELS)
    kept = [c for c in track.channels if c.name in passthru]
    if not kept:
        return retarget(track, preset, available=available, fallbacks=fallbacks)
    mouth = FaceTrack(track.fps,
                      [c for c in track.channels if c.name not in passthru],
                      track.target_set)
    out = retarget(mouth, preset, available=available, fallbacks=fallbacks)
    out.channels.extend(kept)
    if out.target_set is not None:
        out.target_set = list(out.target_set) + [c.name for c in kept]
    return out


def _anchored_segments(args, dur, g2p):
    """Phoneme segments from the naive aligner pinned at --anchors. Only `srt`
    can supply its own transcript (concatenated cue text); every other format
    needs --text, and `google` needs it up front to resolve its wN marks."""
    with open(args.anchors, encoding="utf-8") as fh:
        text = fh.read()
    fmt = args.anchors_format
    if fmt == "gentle-phones":                 # phone timings -> segments directly
        return from_gentle_phones(text)        # (the accurate phone path, no naive spacer)
    if fmt == "allosaurus":                    # acoustic recognizer, NO transcript
        return from_allosaurus(text)
    if fmt == "phones":                        # generic acoustic phone timings
        return from_phone_timestamps(
            text, alphabet=getattr(args, "phones_alphabet", "ipa"),
            timing=getattr(args, "phones_timing", "start_end"))
    if fmt in _SELF_TRANSCRIBING:              # srt + the aligners carry the words
        anchors = (from_vosk(text, min_conf=getattr(args, "vosk_min_conf", 0.0) or 0.0)
                   if fmt == "vosk" else _ANCHOR_PARSERS[fmt](text))
        transcript = args.text if args.text else anchors_transcript(anchors)
    else:
        if not args.text:
            raise SystemExit(
                f"--text is required with --anchors-format {fmt} "
                "(srt/whisper/whisperx/gentle supply the transcript themselves)")
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


def _naive_tagged(args, dur, g2p, mapping, params, clean=None, tags=None):
    """The ``naive`` command's transcript-text-tag path (issue #7): parse the tags
    out of ``--text``, lip-sync the clean words as usual, and fold the tags back in
    — curve tags as channels, event tags into the event layer, ``[emphasis]`` as a
    local articulation boost, ``<T>``/``[pause]`` as timeline chunking/silence.
    Returns ``(track, segments, clean_text)``. A transcript that parses to no tags
    takes the plain naive path, so it is byte-identical to a run without --tags.
    ``clean``/``tags`` may be supplied pre-parsed (the ``--ssml`` front-end, issue
    #52, hands in the same pair from :func:`openfacefx.ssml.parse_ssml`)."""
    from .pipeline import naive_segments
    from .texttags import (parse_tagged_transcript, resolve_tagged_segments,
                           curve_channels, tag_events, emphasis_params)
    if tags is None:
        clean, tags = parse_tagged_transcript(args.text or "")
    if not tags:
        segs = naive_segments(clean, dur, g2p=g2p)
        track = generate_from_alignment(segs, fps=args.fps, mapping=mapping,
                                        params=params,
                                        gestures=_gesture_params(args),
                                        wav=args.wav)
        return track, segs, clean
    segs, spans, windows = resolve_tagged_segments(clean, dur, tags, g2p)
    track = generate_from_alignment(segs, fps=args.fps, mapping=mapping,
                                    params=emphasis_params(params, windows),
                                    gestures=_gesture_params(args), wav=args.wav)
    track.channels.extend(curve_channels(tags, spans, dur))
    track.events.extend(tag_events(tags, spans, dur))
    return track, segs, clean


def _emit_segments(segs, args) -> None:
    """Dump the phoneme segments as JSON for the HTML previewer's --segments
    lane, when --emit-segments PATH was given (naive/mfa). Independent of the
    track output, so it works alongside any -o format including .lip."""
    path = getattr(args, "emit_segments", None)
    if not path:
        return
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(dump_segments(segs), fh, indent=2)
    _say(args, f"wrote {path}: {len(segs)} phoneme segments "
         "(preview with build_preview.py --segments)")


def _add_caption_options(p) -> None:
    """Shared SRT/WebVTT caption tuning (issue #41), used by the ``captions``
    command and ``naive --emit-captions``."""
    from .export_captions import (DEFAULT_CPS, DEFAULT_GAP, DEFAULT_MAX_LINE,
                                  DEFAULT_MAX_LINES)
    p.add_argument("--cps", type=float, default=DEFAULT_CPS, metavar="CHARS",
                   help="reading speed: a cue is held at least len(text)/CPS "
                        f"seconds where the timeline allows (default {DEFAULT_CPS})")
    p.add_argument("--max-line", type=int, default=DEFAULT_MAX_LINE,
                   metavar="CHARS", help="max characters per caption line "
                        f"(default {DEFAULT_MAX_LINE})")
    p.add_argument("--max-lines", type=int, default=DEFAULT_MAX_LINES,
                   metavar="N", help=f"max lines per cue (default {DEFAULT_MAX_LINES})")
    p.add_argument("--gap", type=float, default=DEFAULT_GAP, dest="caption_gap",
                   metavar="SECONDS", help="a silence at least this long between "
                        f"words breaks the cue (default {DEFAULT_GAP})")
    p.add_argument("--karaoke", action="store_true",
                   help="WebVTT only: emit per-word <c> spans with inline cue "
                        "timestamps for word-level highlighting")


def _emit_captions(args, dur, g2p, text) -> None:
    """Write SRT/WebVTT captions from the SAME naive word alignment the curves
    use, when --emit-captions PATH was given (naive) — the co-generated caption
    side output (issue #41). ``.vtt`` -> WebVTT, any other extension -> SubRip."""
    path = getattr(args, "emit_captions", None)
    if not path or not text:
        return
    from .export_captions import write_captions
    cues = write_captions(text, dur, path, g2p=g2p,
                          karaoke=getattr(args, "karaoke", False),
                          cps=args.cps, max_line=args.max_line,
                          max_lines=args.max_lines, gap=args.caption_gap)
    _say(args, f"wrote {path}: {len(cues)} caption cue(s)")


def _write_lip(segs, dur, args) -> None:
    """Dispatch ``-o out.lip`` (naive/mfa). EXPERIMENTAL, unverified in-game."""
    try:
        write_lip(segs, dur, args.out, game=getattr(args, "lip_game", "skyrim"))
    except (NotImplementedError, ValueError) as e:
        raise SystemExit(str(e))
    _say(args, f"wrote {args.out}: EXPERIMENTAL Bethesda .lip "
         f"({getattr(args, 'lip_game', 'skyrim')}), {dur:.2f}s — "
         "UNVERIFIED in-game, please report on issue #12")


def _parse_floats(s, name):
    """Parse a comma-separated float list (e.g. ``0.002,0.01,0.04``) for the lod
    tiers; ``None`` passes through so the library default applies."""
    if s is None:
        return None
    try:
        out = [float(x) for x in s.split(",") if x.strip()]
    except ValueError:
        raise SystemExit(f"lod: {name} must be comma-separated numbers, got {s!r}")
    if not out:
        raise SystemExit(f"lod: {name} is empty")
    return out


def _parse_ints(s, name):
    """Parse a comma-separated integer list (the per-tier lod channel budget);
    ``None`` passes through (no budgeting)."""
    if s is None:
        return None
    try:
        out = [int(x) for x in s.split(",") if x.strip()]
    except ValueError:
        raise SystemExit(f"lod: {name} must be comma-separated integers, got {s!r}")
    if not out:
        raise SystemExit(f"lod: {name} is empty")
    return out


def _write_budget_sidecar(out, ranking, max_channels, args):
    """Write the ``<out>.budget.json`` energy-ranking sidecar for a transform
    ``--max-channels`` run (issue #37)."""
    import os
    from .budget import budget_metadata
    path = os.path.splitext(out)[0] + ".budget.json"
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(budget_metadata(ranking, max_channels), fh, indent=2)
        fh.write("\n")
    _say(args, f"wrote {path}: energy ranking of {len(ranking)} channels")


def _emotion_clamps(args):
    """Parse repeated ``--clamp CHANNEL LO HI`` triples into a
    ``{channel: (lo, hi)}`` dict for ``bake_emotion`` (None when unset). The
    range validation (0<=lo<=hi<=1) is left to the library so the message matches
    the envelope-JSON path."""
    raw = getattr(args, "clamp", None)
    if not raw:
        return None
    out = {}
    for name, lo, hi in raw:
        try:
            out[name] = (float(lo), float(hi))
        except ValueError:
            raise SystemExit(f"emotion: --clamp {name}: LO/HI must be numbers, "
                             f"got {lo!r} {hi!r}")
    return out


def _positive_float(s: str) -> float:
    """argparse ``type`` for a scalar --fps/--duration on the generate commands: a
    finite value > 0 (``fps=0`` divides-by-zero into an empty/NaN track; a
    non-positive duration is degenerate). A clear ``error:`` at the CLI boundary."""
    try:
        v = float(s)
    except (TypeError, ValueError):
        raise argparse.ArgumentTypeError(f"expected a number, got {s!r}")
    if not (0.0 < v < float("inf")):
        raise argparse.ArgumentTypeError(f"must be a finite value > 0, got {s!r}")
    return v


def main(argv=None) -> int:
    # FaceFXWrapper.exe drop-in shim (issue #33): intercept BEFORE argparse so the
    # raw positional args pass through verbatim. A real consumer command carries
    # values argparse would choke on — a leading-dash token read as an option, or
    # a resampled-WAV/text path — so the whole tail goes straight to the shim,
    # dispatched on the literal 'facefxwrapper' token.
    raw = sys.argv[1:] if argv is None else list(argv)
    if raw and raw[0] == "facefxwrapper":
        return facefxwrapper.run(raw[1:])

    p = argparse.ArgumentParser(prog="openfacefx")
    sub = p.add_subparsers(dest="cmd", required=True)

    n = sub.add_parser("naive", help="text + duration -> curves (no models)")
    n.add_argument("--text", help="transcript (required unless "
                   "--anchors-format srt supplies it from the cue text)")
    # Not argparse-`required`: the phone-timing formats (gentle-phones/allosaurus/
    # phones) carry their own timeline, so they need neither --wav nor --duration.
    # Every other path still requires one — enforced in the handler.
    g = n.add_mutually_exclusive_group(required=False)
    g.add_argument("--wav", help="WAV file to read duration from")
    g.add_argument("--duration", type=_positive_float, help="duration in seconds")
    n.add_argument("--cmudict", help="optional CMUdict file for better G2P")
    n.add_argument("--anchors", help="word/segment timing file that pins the "
                   "aligner at known boundaries (SRT cues or TTS word timings)")
    n.add_argument("--anchors-format", choices=_ANCHOR_FORMATS,
                   help="format of --anchors: srt|words|azure|elevenlabs|kokoro|"
                        "google, the aligners whisper|whisperx|gentle|gentle-phones|"
                        "vosk, or the transcript-free acoustic recognizers "
                        "allosaurus|phones (only valid together with --anchors)")
    n.add_argument("--phones-alphabet", choices=["ipa", "arpabet", "sampa"],
                   default="ipa",
                   help="phone alphabet for --anchors-format phones (default ipa)")
    n.add_argument("--phones-timing", choices=["start_end", "start_dur"],
                   default="start_end",
                   help="second column of --anchors-format phones rows: an absolute "
                        "end time or a duration (default start_end)")
    n.add_argument("--vosk-min-conf", type=float, default=0.0,
                   help="for --anchors-format vosk, drop words whose per-word "
                        "confidence is below this (0.0 = keep all; default)")
    n.add_argument("--fps", type=_positive_float, default=60.0)
    n.add_argument("-o", "--out", required=True)
    n.add_argument("--emit-segments", metavar="PATH",
                   help="also write phoneme segments as JSON for the HTML "
                        "previewer's --segments lane (see tools/build_preview.py)")
    n.add_argument("--emit-captions", metavar="PATH",
                   help="also write SRT/WebVTT captions (issue #41) from the same "
                        "word alignment the curves use — .vtt for WebVTT, any "
                        "other extension for SubRip; tune with --cps/--max-line/"
                        "--max-lines/--gap/--karaoke")
    _add_caption_options(n)
    _add_output_options(n)
    _add_coart_options(n)
    _add_smoothing_options(n)
    _add_gesture_options(n)
    _add_event_options(n)
    _add_prosody_options(n)
    _add_edits_options(n)
    _add_qa_options(n)
    n.add_argument("--no-normalize", action="store_true",
                   help="disable the default transcript normalization pass "
                        "(Unicode ellipsis/dashes/curly-quotes/NBSP -> ASCII "
                        "before G2P). Substitutions are reported in --json/"
                        "--report; ASCII transcripts are unaffected either way")
    n.add_argument("--tags", action="store_true",
                   help="parse transcript text tags in --text (issue #7): curve "
                        "tags '[Name type=ct v1=1]word[/Name]' -> channels, event "
                        "tags '[event:NAME ...]'/'[gesture:NAME]' -> the event "
                        "layer, '[emphasis]word[/emphasis]' -> local articulation "
                        "boost, '<T>' chunk markers / '[pause:SEC]' -> timeline "
                        "silence. Auto-enabled when a clear tag is present. Tags "
                        "are stripped before G2P, so the words are still lip-synced; "
                        "a tagless transcript is byte-identical to no --tags")
    n.add_argument("--ssml", action="store_true",
                   help="parse --text as SSML (issue #52): the same W3C markup you "
                        "feed Azure/Google/Polly TTS drives lip-sync via a thin "
                        "front-end over #7 — <break>/<emphasis>/<mark>/<sub>/<p>/"
                        "<s>/<say-as> map onto the text-tag primitives. "
                        "Auto-enabled when --text opens with a <speak> root; a "
                        "<speak> with no constructs is byte-identical to plain naive")

    m = sub.add_parser("mfa", help="MFA TextGrid -> curves (accurate)")
    m.add_argument("--textgrid", required=True)
    m.add_argument("--wav", help="optional 16-bit PCM WAV the audio-driven layers "
                   "read: energy-scaled --gestures and --prosody events (the "
                   "TextGrid alone has no audio). Without it those layers degrade "
                   "to timing-only / are unavailable")
    m.add_argument("--fps", type=_positive_float, default=60.0)
    m.add_argument("-o", "--out", required=True)
    m.add_argument("--emit-segments", metavar="PATH",
                   help="also write phoneme segments as JSON for the HTML "
                        "previewer's --segments lane (see tools/build_preview.py)")
    _add_output_options(m)
    _add_coart_options(m)
    _add_smoothing_options(m)
    _add_gesture_options(m)
    _add_event_options(m)
    _add_prosody_options(m)
    _add_edits_options(m)
    _add_qa_options(m)

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
    t.add_argument("--fps", type=_positive_float, default=60.0)
    t.add_argument("-o", "--out", required=True)
    _add_output_options(t)
    _add_coart_options(t)
    _add_smoothing_options(t)
    _add_edits_options(t)
    _add_qa_options(t)

    fc = sub.add_parser("from-cues",
                        help="import a stepped mouth-cue file back into a track "
                             "(Rhubarb tsv/xml/json, Moho .dat, Papagayo .pgo) — "
                             "the inverse of the cue exporters (issue #44)")
    fc.add_argument("file", help="the mouth-cue file to import")
    fc.add_argument("--format", choices=["tsv", "xml", "json-cues", "dat", "pgo"],
                    help="override the format (else auto-detected by extension + "
                         "first line); json-cues reads a Rhubarb JSON, not a track")
    fc.add_argument("--fps", type=_positive_float,
                    help="frame rate for the rate-less Moho .dat (default 24); "
                         "Papagayo .pgo carries its own and Rhubarb is seconds-"
                         "based (reconstructed at 100 fps), so this is ignored there")
    fc.add_argument("--coarticulate", action="store_true",
                    help="re-solve the stepped cues through the coarticulation "
                         "dominance blend to smooth the hard steps (default: keep "
                         "the stepped [0,1] switches)")
    fc.add_argument("-o", "--out", required=True)
    _add_output_options(fc)
    _add_qa_options(fc)

    cap = sub.add_parser("captions",
                         help="write SRT/WebVTT subtitles from a transcript + "
                              "duration, timed by the SAME alignment the lip "
                              "curves use — captions and lip-sync from one source "
                              "(issue #41)")
    cap.add_argument("--text", required=True, help="the transcript to caption")
    capg = cap.add_mutually_exclusive_group(required=True)
    capg.add_argument("--wav", help="WAV file to read the duration from")
    capg.add_argument("--duration", type=float, help="duration in seconds")
    cap.add_argument("--cmudict", help="optional CMUdict file for better G2P")
    cap.add_argument("-o", "--out", required=True,
                     help="output path: .vtt -> WebVTT, any other extension -> "
                          "SubRip .srt")
    _add_caption_options(cap)

    au = sub.add_parser("audit",
                        help="reconcile a delivered VO folder against a loc-table "
                             "manifest — missing / orphan / duration / empty / "
                             "naming + a language-coverage matrix (read-only QA "
                             "gate, issue #42)")
    au.add_argument("--manifest", required=True, help="the loc-table manifest "
                    "(the #40 CSV/TSV; audio paths resolve under --delivered)")
    au.add_argument("--delivered", required=True,
                    help="the delivered audio folder (read-only)")
    au.add_argument("--duration-tolerance", type=float, default=0.5,
                    dest="duration_tolerance", metavar="FRACTION",
                    help="allowed +/- fraction of the len(text)/CPS length "
                         "estimate before a take is a duration outlier (0.5)")
    au.add_argument("--cps", type=float, default=14.0, metavar="CHARS",
                    help="speaking rate (characters/sec) for the length estimate "
                         "(default 14)")
    au.add_argument("--json", action="store_true",
                    help="emit the full itemized report as JSON instead of the "
                         "human worst-first table")

    cs = sub.add_parser("from-csv",
                        help="import a blendshape-weight CSV into a track: the "
                             "OpenFaceFX long time,channel,value format or a wide "
                             "per-frame ARKit / Live Link Face CSV (issue #45)")
    cs.add_argument("file", help="the blendshape-weight CSV to import")
    cs.add_argument("--fps", type=float, default=60.0,
                    help="frame rate timing the wide per-frame rows "
                         "(frame/timecode -> seconds; default 60). The long "
                         "time,channel,value format carries its own times")
    cs.add_argument("--timecode-col", metavar="NAME",
                    help="column holding a SMPTE 'HH:MM:SS:FF' timecode in a wide "
                         "CSV (else a literal 'Timecode' column, else the row "
                         "index drives the timeline)")
    cs.add_argument("-o", "--out", required=True)
    _add_output_options(cs)
    _add_qa_options(cs)

    fv = sub.add_parser("from-vmd",
                        help="import a MikuMikuDance .vmd morph animation into a "
                             "track — the read side of the .vmd exporter (issue "
                             "#60); unknown morphs pass through, 頭/首 bones become "
                             "head-pose channels")
    fv.add_argument("file", help="the .vmd motion file to import")
    fv.add_argument("--fps", type=float, default=30.0,
                    help="frame rate timing the .vmd frames (frame -> seconds; "
                         "default 30, MMD-native)")
    fv.add_argument("--no-head-pose", action="store_true",
                    help="skip harvesting the 頭/首 head bones into "
                         "headPitch/headYaw/headRoll channels")
    fv.add_argument("-o", "--out", required=True)
    _add_output_options(fv)
    _add_qa_options(fv)

    fa = sub.add_parser("from-a2f",
                        help="import a NVIDIA Audio2Face blendshape JSON "
                             "(facsNames/weightMat) into a track — the read side "
                             "of the .a2f.json exporter (issue #64)")
    fa.add_argument("file", help="the Audio2Face blendshape .json to import")
    fa.add_argument("--fps", type=float, default=None,
                    help="frame rate timing the frames (frame -> seconds); "
                         "overrides the file's exportFps, and is the fallback when "
                         "the file omits it (default 30, A2F-native)")
    fa.add_argument("-o", "--out", required=True)
    _add_output_options(fa)
    _add_qa_options(fa)

    eo = sub.add_parser("emit-oov-dict",
                        help="emit a reviewable CMUdict of rule-G2P guesses for the "
                             "out-of-vocabulary words in a transcript (issue #66); "
                             "fix the guesses and load them with --cmudict")
    eo.add_argument("--text", help="inline transcript text")
    eo.add_argument("--transcript", metavar="FILE",
                    help="a UTF-8 transcript file to scan (alternative to --text)")
    eo.add_argument("--cmudict",
                    help="optional CMUdict to pre-load so words it already defines "
                         "are not emitted as OOV")
    eo.add_argument("-o", "--out", required=True, help="output .dict path")

    cv = sub.add_parser("convert",
                        help="re-export or retarget an existing track.json to any "
                             "format (Unity/Godot/Live2D/cues/.lip/CSV/JSON) "
                             "WITHOUT re-running the solver (issue #46)")
    cv.add_argument("infile", help="the source .track.json to load")
    cv.add_argument("-o", "--out", required=True,
                    help="output; the exporter is chosen by extension, exactly as "
                         "the generate commands' -o (so behaviour cannot drift)")
    cv.add_argument("--fps", type=float,
                    help="re-stamp the track's frame rate before export "
                         "(default: keep the loaded track's fps)")
    _add_output_options(cv)
    _add_edits_options(cv)

    ins = sub.add_parser("inspect",
                         help="read-only stats for a track.json: duration, "
                              "channel/keyframe counts, per-channel coverage, "
                              "event/variant counts (issue #47)")
    ins.add_argument("file", help="the .track.json to inspect")
    ins.add_argument("--json", action="store_true",
                     help="emit the schema-stable JSON stats instead of the "
                          "human table")

    val = sub.add_parser("validate",
                         help="lint a track / *.edits.json / events file against "
                              "the format contract; exits nonzero on a violation "
                              "(a CI gate, issue #47)")
    val.add_argument("file", help="the .track.json, *.edits.json, or events JSON")
    val.add_argument("--strict", action="store_true",
                     help="promote warnings (empty channels, zero-length track) "
                          "to errors")
    val.add_argument("--json", action="store_true",
                     help="emit the deterministic JSON problem list instead of "
                          "the human summary")

    tf = sub.add_parser("transform",
                        help="retime / mirror / trim an existing track.json "
                             "without re-running the solver (issue #48)")
    tf.add_argument("infile", help="the source .track.json to load")
    tf.add_argument("-o", "--out", required=True)
    rt = tf.add_mutually_exclusive_group()
    rt.add_argument("--retime", type=float, metavar="FACTOR",
                    help="scale all keyframe and event times by FACTOR")
    rt.add_argument("--duration", type=float, metavar="D",
                    help="retime so the clip lasts D seconds")
    rt.add_argument("--wav", metavar="WAV",
                    help="retime so the clip matches the WAV's duration")
    tf.add_argument("--anchor", type=float, default=0.0,
                    help="time pinned fixed by --retime/--duration/--wav "
                         "(default 0, i.e. scale from the clip start)")
    tf.add_argument("--mirror", action="store_true",
                    help="swap *Left/*Right channels and negate the signed "
                         "lateral pose channels (headYaw/headRoll/eyeYaw)")
    tf.add_argument("--trim", type=float, nargs=2, metavar=("T0", "T1"),
                    help="keep [T0, T1] seconds and rebase to 0")
    tf.add_argument("--max-channels", type=int, metavar="N",
                    help="keep only the N highest-energy channels (drop the "
                         "low-energy secondary ones); writes a <out>.budget.json "
                         "energy-ranking sidecar (issue #37)")
    _add_output_options(tf)

    sq = sub.add_parser("sequence",
                        help="splice several track.json files end-to-end into one "
                             "timeline, with optional gaps/crossfade (issue #51)")
    sq.add_argument("infiles", nargs="+",
                    help="the .track.json files to concatenate, in order")
    sq.add_argument("--gap", type=float, default=0.0,
                    help="silence in seconds inserted between each segment "
                         "(default 0 = abutting)")
    sq.add_argument("--crossfade", type=float, default=0.0,
                    help="linear crossfade in seconds at each abutting seam "
                         "(default 0 = hard cut)")
    sq.add_argument("-o", "--out", required=True)
    _add_output_options(sq)

    lo = sub.add_parser("lod",
                        help="offline LOD export: K thinned/resampled variants "
                             "of one track, finest first (issue #36)")
    lo.add_argument("infile", help="the source .track.json to derive LODs from")
    lo.add_argument("-o", "--out", required=True, metavar="DIR/PREFIX",
                    help="output path prefix; writes PREFIX_lod0.EXT ... and a "
                         "PREFIX_lod.json metadata sidecar")
    lo.add_argument("--rdp", metavar="E1,E2,..",
                    help="RDP epsilons per tier, finest first "
                         "(default 0.002,0.01,0.04)")
    lo.add_argument("--fps", metavar="F1,F2,..",
                    help="update rate per tier (default 60,30,15); each is capped "
                         "at the source fps (a tier at the source rate is pure RDP)")
    lo.add_argument("--format", choices=["json", "csv"], default="json",
                    help="variant file format (default json); the metadata "
                         "sidecar is always json")
    lo.add_argument("--max-channels", metavar="N1,N2,..",
                    help="per-tier channel budget: keep the N highest-energy "
                         "channels at each LOD (same length as the tiers, higher "
                         "LODs fewer); nested by the source ranking (issue #37)")

    el = sub.add_parser("export-layers",
                        help="write a track.json carrying an additive speech / "
                             "emotion / gesture layer decomposition + blend-weight "
                             "and priority metadata (issue #39)")
    el.add_argument("infile", help="the merged .track.json to decompose")
    el.add_argument("-o", "--out", required=True,
                    help="output .track.json: the flat channels (unchanged) plus a "
                         "top-level 'layers' block")

    df = sub.add_parser("diff",
                        help="read-only A/B drift report between two track.json "
                             "files; exits nonzero on drift over --tolerance "
                             "(a golden-file CI gate, issue #50)")
    df.add_argument("a", help="the first (baseline / golden) .track.json")
    df.add_argument("b", help="the second .track.json to compare against it")
    df.add_argument("--tolerance", type=float, default=0.0,
                    help="max allowed per-delta drift before the exit is nonzero "
                         "(default 0.0 => exact match required)")
    df.add_argument("--json", action="store_true",
                    help="emit the full deterministic JSON report instead of the "
                         "worst-first human table")

    e = sub.add_parser("energy",
                       help="audio loudness -> mouth-open curves (no "
                            "transcript; amplitude fallback, not viseme sync)")
    e.add_argument("--wav", required=True,
                   help="16-bit PCM WAV (mono or stereo; stereo is downmixed). "
                        "Convert other codecs first: ffmpeg -c:a pcm_s16le")
    e.add_argument("--intensity", type=float, default=1.0,
                   help="gain on the mouth opening (1.0 = as-is; >1 opens "
                        "wider on quiet speech, <1 is subtler)")
    e.add_argument("--fps", type=_positive_float, default=60.0)
    e.add_argument("-o", "--out", required=True)
    _add_output_options(e)
    _add_smoothing_options(e)
    _add_gesture_options(e)
    _add_event_options(e)
    _add_prosody_options(e)
    _add_edits_options(e)
    _add_qa_options(e)

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
    b.add_argument("--dir", help="input tree of .wav files with same-stem "
                   ".TextGrid or .txt transcripts (directory-walk mode; mutually "
                   "exclusive with --manifest)")
    b.add_argument("--manifest", help="a CSV/TSV loc-table driving one track per "
                   "row (issue #40): header-mapped id/audio/text/language/"
                   "character/mapping/style/out columns. Selects manifest mode; "
                   "the directory-walk mode is untouched")
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
    b.add_argument("--captions", choices=["srt", "vtt"],
                   help="also write an SRT/WebVTT caption sidecar next to each "
                        "naive-mode track, from the same word alignment (issue "
                        "#41). Opt-in: without it the output tree is unchanged")
    b.add_argument("--fps", type=float, default=60.0)
    b.add_argument("--machine-readable", action="store_true",
                   help="stream an NDJSON event log to stderr (one JSON object "
                        "per line: start/progress/warning/failure/done) so a "
                        "supervising process can track a large run live. Events "
                        "are in processing order; stdout and batch_summary.json "
                        "are unchanged")
    b.add_argument("--quiet", action="store_true",
                   help="suppress the human progress table on stdout; the "
                        "batch_summary.json and any --machine-readable/--ledger "
                        "output are still written")
    b.add_argument("--ledger", metavar="FILE",
                   help="append one NDJSON record per run to FILE (args snapshot, "
                        "per-input size/mtime, outcome counts) for reproducibility"
                        "/audit. Survives --modified-only; the run id is a "
                        "deterministic, wall-clock-free hash of the inputs")
    b.add_argument("--cue-warnings", action="store_true",
                   help="add a too-short/too-long phoneme-cue count (qa.cue_flags) "
                        "to each summary row and fold it into the worst-first "
                        "ranking. Opt-in: without it the table and summary JSON "
                        "are byte-identical to before")
    b.add_argument("--min-cue", type=float, default=None, metavar="SECONDS",
                   help="cue shorter than this counts toward --cue-warnings "
                        "(default 0.03; needs --cue-warnings)")
    b.add_argument("--max-cue", type=float, default=None, metavar="SECONDS",
                   help="cue longer than this counts toward --cue-warnings "
                        "(default 0.5; needs --cue-warnings)")

    de = sub.add_parser("diff-edits",
                        help="capture hand-edits: diff a BASE track vs an EDITED "
                             "track into an openfacefx.edits sidecar (issue #9)")
    de.add_argument("base", help="the generated baseline .track.json")
    de.add_argument("edited", help="the hand-edited .track.json")
    de.add_argument("-o", "--out", required=True, help="output .edits.json sidecar")
    de.add_argument("--mode", choices=["offset", "replace"], default="offset",
                    help="offset (default) stores deltas relative to the baseline "
                         "-- survives later intensity/coart/energy changes; "
                         "replace stores absolute edited values (full ownership)")
    de.add_argument("--span", type=float, nargs=2, metavar=("T0", "T1"),
                    help="restrict capture to a time window (a locked region); "
                         "the rest of each channel stays analysis-owned")
    de.add_argument("--source", metavar="WAV",
                    help="WAV whose sha1 keys the sidecar to its audio (source_id)")

    em = sub.add_parser("emotion",
                        help="bake an additive emotion/expression envelope over a "
                             "solved track (reference-pose delta, issue #38)")
    em.add_argument("track", help="the solved .track.json to bake onto")
    em.add_argument("envelope", help="an openfacefx.emotion envelope JSON: direct "
                    "emotion-channel keyframes, or a valence/arousal track")
    em.add_argument("-o", "--out", required=True,
                    help="baked output track (.json/.csv/.anim/.tres/"
                         ".motion3.json/cue formats, like the generate commands)")
    em.add_argument("--intensity", type=float, default=1.0,
                    help="global dial scaling the emotion delta (linear); "
                         "0 => output byte-identical to the input track")
    em.add_argument("--eps", type=float, default=0.015,
                    help="RDP thinning epsilon for the re-thinned baked channels "
                         "(default 0.015, matching the solver)")
    em.add_argument("--clamp", action="append", nargs=3,
                    metavar=("CHANNEL", "LO", "HI"),
                    help="per-channel output clamp 0<=LO<=HI<=1, repeatable; "
                         "overrides the envelope's own clamps")
    _add_output_options(em)

    args = p.parse_args(argv)
    args._warnings = []          # QA summary sink; see _warn / _emit_summary

    if args.cmd == "diff-edits":
        from .io_export import read_json
        from .edits import diff_edits, save_edits, _sha1_source
        try:
            base = read_json(args.base)
            edited = read_json(args.edited)
        except (OSError, ValueError) as ex:
            raise SystemExit(f"diff-edits: {ex}")
        source_id = _sha1_source(args.source) if args.source else None
        doc = diff_edits(base, edited, mode=args.mode,
                         span=tuple(args.span) if args.span else None,
                         source_id=source_id)
        save_edits(doc, args.out)
        print(f"wrote {args.out}: {len(doc.channels)} edited channel(s), "
              f"mode={args.mode}" + (f", span={args.span}" if args.span else ""))
        return 0

    if args.cmd == "lip-calibrate":
        written = lip_calibrate(args.out, game=args.lip_game,
                                seconds=args.seconds)
        print(f"wrote {len(written)} calibration lips to {args.out} — "
              f"EXPERIMENTAL: load each on a voiced line in-game and report "
              f"which mouth part moves on issue #12")
        return 0

    if args.cmd == "batch":
        from .batch import run_batch
        if bool(args.dir) == bool(args.manifest):
            raise SystemExit("batch needs exactly one of --dir or --manifest")
        lo, hi = _cue_thresholds(args) if args.cue_warnings else (None, None)
        return run_batch(args.dir, args.out, recurse=args.recurse,
                         modified_only=args.modified_only, jobs=args.jobs,
                         mapping=args.batch_mapping,
                         cmudict=args.batch_cmudict,
                         fps=args.fps, ext=args.ext,
                         machine_readable=args.machine_readable,
                         quiet=args.quiet, ledger=args.ledger,
                         cue_warnings=args.cue_warnings,
                         min_cue=lo, max_cue=hi,
                         manifest_file=args.manifest,
                         captions=args.captions)

    if args.cmd == "emotion":
        from .io_export import read_json
        from .emotion import bake_emotion, load_envelope
        try:
            track = read_json(args.track)
            env = load_envelope(args.envelope)
        except (OSError, ValueError) as ex:
            raise SystemExit(f"emotion: {ex}")
        try:
            baked = bake_emotion(track, env, intensity=args.intensity,
                                 clamps=_emotion_clamps(args), eps=args.eps)
        except ValueError as ex:
            raise SystemExit(f"emotion: {ex}")
        _write(baked, args.out, args)
        return 0

    if args.cmd == "from-cues":
        from .importers import import_cues
        try:
            track, warnings = import_cues(args.file, fmt=args.format,
                                          fps=args.fps,
                                          coarticulate=args.coarticulate)
        except (OSError, ValueError) as ex:
            raise SystemExit(f"from-cues: {ex}")
        for w in warnings:
            _warn(args, w)
        _write(track, args.out, args)
        _emit_summary(args, track)
        return 0

    if args.cmd == "captions":
        from .export_captions import write_captions
        dur = args.duration if args.duration else wav_duration(args.wav)
        text, _ = normalize_transcript(args.text)   # same fold the naive path uses
        g2p = G2P()
        if args.cmudict:
            g2p.load_cmudict(args.cmudict)
        cues = write_captions(text, dur, args.out, g2p=g2p, karaoke=args.karaoke,
                              cps=args.cps, max_line=args.max_line,
                              max_lines=args.max_lines, gap=args.caption_gap)
        _say(args, f"wrote {args.out}: {len(cues)} caption cue(s)")
        return 0

    if args.cmd == "audit":
        from .vo_audit import audit_delivery, audit_report_text
        try:
            report = audit_delivery(args.manifest, args.delivered,
                                    duration_tolerance=args.duration_tolerance,
                                    cps=args.cps)
        except (OSError, ValueError) as ex:
            raise SystemExit(f"audit: {ex}")
        print(json.dumps(report, indent=2) if args.json
              else audit_report_text(report))
        return 1 if report["counts"]["issues"] else 0    # nonzero = QA gate

    if args.cmd == "from-csv":
        from .importers_csv import read_csv
        try:
            track, warnings = read_csv(args.file, fps=args.fps,
                                       timecode_col=args.timecode_col)
        except (OSError, ValueError) as ex:
            raise SystemExit(f"from-csv: {ex}")
        for w in warnings:
            _warn(args, w)
        _write(track, args.out, args)
        _emit_summary(args, track)
        return 0

    if args.cmd == "from-vmd":
        from .importers_vmd import read_vmd
        try:
            track = read_vmd(args.file, fps=args.fps,
                             head_pose=not args.no_head_pose)
        except (OSError, ValueError) as ex:
            raise SystemExit(f"from-vmd: {ex}")
        _write(track, args.out, args)
        _emit_summary(args, track)
        return 0

    if args.cmd == "from-a2f":
        from .export_a2f import read_a2f
        try:
            track, warnings = read_a2f(args.file, fps=args.fps)
        except (OSError, ValueError) as ex:
            raise SystemExit(f"from-a2f: {ex}")
        for w in warnings:
            _warn(args, w)
        _write(track, args.out, args)
        _emit_summary(args, track)
        return 0

    if args.cmd == "emit-oov-dict":
        if bool(args.text) == bool(args.transcript):
            raise SystemExit("emit-oov-dict: pass exactly one of --text / --transcript")
        text = args.text
        if args.transcript:
            try:
                with open(args.transcript, encoding="utf-8") as fh:
                    text = fh.read()
            except OSError as ex:
                raise SystemExit(f"emit-oov-dict: {ex}")
        g2p = G2P()
        if args.cmudict:
            try:
                g2p.load_cmudict(args.cmudict)
            except OSError as ex:
                raise SystemExit(f"emit-oov-dict: {ex}")
        dict_text = g2p.emit_oov_dict(text)
        try:
            with open(args.out, "w", encoding="utf-8", newline="\n") as fh:
                fh.write(dict_text)
        except OSError as ex:
            raise SystemExit(f"emit-oov-dict: {ex}")
        n = sum(1 for ln in dict_text.splitlines() if ln and not ln.startswith(";"))
        _say(args, f"wrote {args.out}: {n} OOV pronunciation guess(es) — "
             f"review the phonemes before loading with --cmudict")
        return 0

    if args.cmd == "convert":
        # Re-serialise an existing track through the SAME edits->_write path the
        # generate commands use (see naive/mfa/from-timing/energy above), so the
        # output is byte-identical to generating that track — by construction, no
        # solver, audio or RNG. Events on the loaded track are preserved as-is.
        from .io_export import read_json
        try:
            track = read_json(args.infile)
        except (OSError, ValueError) as ex:
            raise SystemExit(f"convert: {ex}")
        if args.fps is not None:
            track.fps = args.fps
        track = _apply_edits_layer(track, args)
        _write(track, args.out, args)
        return 0

    if args.cmd == "inspect":
        from .io_export import read_json
        from .inspect import inspect_track, render_inspect
        try:
            track = read_json(args.file)
        except (OSError, ValueError) as ex:
            raise SystemExit(f"inspect: {ex}")
        doc = inspect_track(track)
        print(json.dumps(doc) if args.json else render_inspect(doc))
        return 0

    if args.cmd == "validate":
        from .inspect import validate_file, render_problems
        kind, problems = validate_file(args.file, strict=args.strict)
        ok = not any(p["severity"] == "error" for p in problems)
        if args.json:
            print(json.dumps({"format": "openfacefx.validate", "version": 1,
                              "kind": kind, "ok": ok, "problems": problems}))
        else:
            print(render_problems(kind, problems))
        return 0 if ok else 1

    if args.cmd == "transform":
        from .io_export import read_json
        from . import transforms as _tf
        try:
            track = read_json(args.infile)
        except (OSError, ValueError) as ex:
            raise SystemExit(f"transform: {ex}")
        applied = False
        try:
            if args.retime is not None:
                track = _tf.retime(track, args.retime, anchor=args.anchor)
                applied = True
            elif args.duration is not None:
                track = _tf.retime_to_duration(track, args.duration,
                                               anchor=args.anchor)
                applied = True
            elif args.wav is not None:
                track = _tf.retime_to_duration(track, wav_duration(args.wav),
                                               anchor=args.anchor)
                applied = True
            if args.mirror:
                track = _tf.mirror(track)
                applied = True
            if args.trim is not None:
                track = _tf.trim(track, args.trim[0], args.trim[1])
                applied = True
            ranking = None
            if args.max_channels is not None:      # energy-ranked cap (issue #37)
                from .budget import budget_channels
                track, ranking = budget_channels(track, args.max_channels)
                applied = True
        except (ValueError, OSError) as ex:
            raise SystemExit(f"transform: {ex}")
        if not applied:
            raise SystemExit("transform: choose at least one of "
                             "--retime/--duration/--wav, --mirror, --trim, "
                             "--max-channels")
        _write(track, args.out, args)
        if ranking is not None:
            _write_budget_sidecar(args.out, ranking, args.max_channels, args)
        return 0

    if args.cmd == "sequence":
        from .io_export import read_json
        from .transforms import concat
        try:
            tracks = [read_json(f) for f in args.infiles]
            gaps = [args.gap] * (len(tracks) - 1) if args.gap else None
            out = concat(tracks, gaps=gaps, crossfade=args.crossfade)
        except (OSError, ValueError) as ex:
            raise SystemExit(f"sequence: {ex}")
        _write(out, args.out, args)
        return 0

    if args.cmd == "lod":
        import os
        from .io_export import read_json, write_csv, write_json
        from .lod import generate_lods, lod_metadata
        source_ranking = None
        try:
            track = read_json(args.infile)
            variants, levels = generate_lods(
                track, rdp=_parse_floats(args.rdp, "--rdp"),
                fps=_parse_floats(args.fps, "--fps"))
            budgets = _parse_ints(args.max_channels, "--max-channels")
            if budgets is not None:
                if len(budgets) != len(variants):
                    raise ValueError(
                        f"--max-channels must list one budget per tier "
                        f"({len(variants)}), got {len(budgets)}")
                # rank the SOURCE once so the kept channel sets nest across LODs
                # (pose channels pass through every tier; issue #37)
                from .budget import keep_top_weight, rank_channels
                source_ranking = rank_channels(track)
                variants = [keep_top_weight(v, source_ranking, n)
                            for v, n in zip(variants, budgets)]
        except (OSError, ValueError) as ex:
            raise SystemExit(f"lod: {ex}")
        prefix, ext = args.out, args.format
        outdir = os.path.dirname(prefix)
        if outdir:
            os.makedirs(outdir, exist_ok=True)
        writer = write_csv if ext == "csv" else write_json
        files = []
        for i, v in enumerate(variants):
            path = f"{prefix}_lod{i}.{ext}"
            writer(v, path)
            files.append(os.path.basename(path))
        meta = lod_metadata(track, levels, variants, files)
        if source_ranking is not None:                # issue #37: per-tier budget
            meta["ranking"] = source_ranking
            for i, entry in enumerate(meta["levels"]):
                entry["max_channels"] = budgets[i]
        meta_path = f"{prefix}_lod.json"
        with open(meta_path, "w", encoding="utf-8") as fh:
            json.dump(meta, fh, indent=2)
            fh.write("\n")
        _say(args, f"wrote {len(variants)} LOD variants + {meta_path}: " +
             ", ".join(f"lod{m['index']}={m['keyframes']}kf@{m['fps']}fps"
                       for m in meta["levels"]))
        return 0

    if args.cmd == "export-layers":
        from .io_export import read_json, to_dict
        from .layers import build_layers
        try:
            track = read_json(args.infile)
        except (OSError, ValueError) as ex:
            raise SystemExit(f"export-layers: {ex}")
        layers = build_layers(track)
        with open(args.out, "w", encoding="utf-8") as fh:
            json.dump(to_dict(track, layers=layers), fh, indent=2)
        _say(args, f"wrote {args.out}: flat track + {len(layers)} layer(s) "
             f"({', '.join(l.name for l in layers)})")
        return 0

    if args.cmd == "diff":
        from .io_export import read_json
        from .trackdiff import diff_tracks, render_diff
        try:
            a = read_json(args.a)
            b = read_json(args.b)
        except (OSError, ValueError) as ex:
            raise SystemExit(f"diff: {ex}")
        report = diff_tracks(a, b, tolerance=args.tolerance)
        print(json.dumps(report) if args.json else render_diff(report))
        return 0 if report["ok"] else 1

    mapping = Mapping.from_json(args.mapping) if args.mapping else None
    params = (_coart_params(args)
              if args.cmd in ("naive", "mfa", "from-timing") else None)

    if args.cmd == "naive":
        if bool(args.anchors) != bool(args.anchors_format):
            raise SystemExit(
                "--anchors and --anchors-format are only valid together")
        dur = (args.duration if args.duration
               else wav_duration(args.wav) if args.wav else None)
        if dur is None and args.anchors_format not in _PHONE_SEGMENT_FORMATS:
            raise SystemExit(
                "naive: one of --wav / --duration is required (only the phone-timing "
                f"formats {'/'.join(_PHONE_SEGMENT_FORMATS)} carry their own timeline)")
        subs = []
        if args.text and not args.no_normalize:
            args.text, subs = normalize_transcript(args.text)
        g2p = G2P()
        if args.cmudict:
            added = g2p.load_cmudict(args.cmudict)
            _say(args, f"loaded {added} CMUdict entries")
        from .texttags import has_tags
        from .ssml import looks_like_ssml, parse_ssml
        # SSML (issue #52) is a thin front-end over the #7 tag path: parse it to
        # the same (clean_text, tags) pair and route it through _naive_tagged.
        # Explicit --ssml or an auto-detected <speak> root; empty tags still land
        # on the byte-identical plain path.
        ssml_clean = ssml_tags = None
        if getattr(args, "ssml", False) or (bool(args.text)
                                            and looks_like_ssml(args.text)):
            ssml_clean, ssml_tags = parse_ssml(args.text or "")
        use_tags = ssml_tags is not None or getattr(args, "tags", False) or (
            bool(args.text) and has_tags(args.text))
        if use_tags:
            if args.anchors:
                raise SystemExit("--tags cannot be combined with --anchors")
            if args.out.endswith(".lip"):
                raise SystemExit(
                    "--tags is not supported for -o .lip output: tags drive "
                    "curves/events, which the phoneme .lip format cannot carry")
            track, segs, clean = _naive_tagged(args, dur, g2p, mapping, params,
                                               clean=ssml_clean, tags=ssml_tags)
            oov = g2p.oov_words(clean) if (clean and _want_summary(args)) else []
            _emit_segments(segs, args)
            _emit_captions(args, dur, g2p, clean)
            track = _apply_edits_layer(track, args)
            _event_layer(track, args, segments=segs)
            _write(track, args.out, args)
            _emit_summary(args, track, segments=segs, oov_words=oov,
                          substitutions=subs)
            return 0
        segs = _naive_input_segments(args, dur, g2p)
        if dur is None:                    # phone-timing format: timeline is the segments'
            dur = segs[-1].end if segs else 0.0
        oov = g2p.oov_words(args.text) if (args.text and _want_summary(args)) else []
        _emit_segments(segs, args)
        _emit_captions(args, dur, g2p, args.text)
        if args.out.endswith(".lip"):
            _write_lip(segs, dur, args)
            _emit_summary(args, None, segments=segs, oov_words=oov,
                          substitutions=subs)
        else:
            track = generate_from_alignment(segs, fps=args.fps, mapping=mapping,
                                            params=params,
                                            gestures=_gesture_params(args),
                                            wav=args.wav)
            track = _apply_edits_layer(track, args)
            _event_layer(track, args, segments=segs)
            _write(track, args.out, args)
            _emit_summary(args, track, segments=segs, oov_words=oov,
                          substitutions=subs)
    elif args.cmd == "mfa":
        segs = load_mfa_textgrid(args.textgrid)
        _emit_segments(segs, args)
        if args.out.endswith(".lip"):
            _write_lip(segs, segs[-1].end if segs else 0.0, args)
            _emit_summary(args, None, segments=segs)
        else:
            track = generate_from_alignment(segs, fps=args.fps, mapping=mapping,
                                            params=params,
                                            gestures=_gesture_params(args),
                                            wav=getattr(args, "wav", None))
            track = _apply_edits_layer(track, args)
            _event_layer(track, args, segments=segs)
            _write(track, args.out, args)
            _emit_summary(args, track, segments=segs)
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
                _warn(args, w)
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
                    _warn(args, w)
            track = generate_from_alignment(segs, fps=args.fps, mapping=active,
                                            params=params)
        track = _apply_edits_layer(track, args)
        _write(track, args.out, args)
        _emit_summary(args, track, segments=segs)
    elif args.cmd == "energy":
        from .energy import generate_from_energy
        track = generate_from_energy(args.wav, fps=args.fps,
                                     intensity=args.intensity, mapping=mapping,
                                     gestures=_gesture_params(args),
                                     smooth=_smooth_seconds(args),
                                     lag=_lag_seconds(args))
        track = _apply_edits_layer(track, args)
        et = ev = None
        if getattr(args, "events", False):
            from .energy import energy_envelope
            et, ev = energy_envelope(args.wav, fps=args.fps)
        _event_layer(track, args, env_times=et, env=ev)
        _write(track, args.out, args)
        _emit_summary(args, track)
    return 0


if __name__ == "__main__":
    sys.exit(main())
