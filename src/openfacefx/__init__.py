"""OpenFaceFX -- an open-source facial/lip-sync animation pipeline.

audio + text  ->  time-stamped phonemes  ->  visemes  ->  coarticulated
animation curves  ->  export for your rig.
"""

from .g2p import G2P
from .alignment import PhonemeSegment, NaiveAligner, load_mfa_textgrid
from .coarticulation import (build_viseme_curves, CoartParams, STYLE_PRESETS,
                             style_params)
from .curves import FaceTrack, Channel, Keyframe, reduce_to_track
from .postprocess import smooth_matrix, time_shift
from .pipeline import (generate_from_alignment, generate_naive,
                       naive_segments, wav_duration, derive_events)
from .energy import energy_envelope, generate_from_energy
from .prosody import (ProsodyParams, ProsodyTrack, pitch_track,
                      prosody_features, prosody_events, detect_events)
from .gestures import (GestureParams, generate_gestures, gestures_from_wav,
                       add_gestures_to_track, GESTURE_CHANNELS)
from .events import (Event, Alternative, VariantGroup, Variants, EVENT_TYPES,
                     resolve, choose, add_event, attach_events, read_events,
                     validate_events)
from .io_export import to_dict, write_json, write_csv, from_dict, read_json
from .qa import summarize, normalize_transcript, cue_flags
from .texttags import (parse_tagged_transcript, Tag, has_tags,
                       resolve_tagged_segments, curve_channels, tag_events,
                       build_curve_channel)
from .ssml import (parse_ssml, looks_like_ssml, EMPHASIS_STRENGTH,
                   BREAK_STRENGTH)
from .edits import (EditsDoc, diff_edits, apply_edits, load_edits, save_edits,
                    sample)
from .emotion import (EmotionEnvelope, bake_emotion, load_envelope,
                     save_envelope, va_to_pose, VA_TABLE, VA_EMOTION_CHANNELS)
from .importers import (import_cues, detect_format, build_cue_track,
                       RHUBARB_TO_VISEME, PRESTON_BLAIR_TO_VISEME)
from .importers_csv import read_csv
from .inspect import (inspect_track, validate_asset, validate_file,
                     detect_kind)
from .transforms import (retime, retime_to_duration, mirror, trim, concat,
                        MIRROR_PAIRS, MIRROR_NEGATE)
from .lod import (generate_lods, make_lod, lod_metadata, switching_table,
                 LOD_DEFAULT_RDP, LOD_DEFAULT_FPS)
from .budget import (channel_energy, rank_channels, keep_channels,
                    keep_top_weight, budget_channels, budget_metadata)
from .layers import (Layer, build_layers, flatten_layers, layers_to_dict,
                    layers_from_dict)
from .batch_manifest import read_manifest, manifest_jobs, COLUMN_ALIASES
from .trackdiff import diff_tracks, render_diff
from .export_unity import write_unity_anim, NAMING_PRESETS
from .export_unreal_notifies import write_unreal_notifies, notifies_to_dict
from .export_live2d import write_live2d_motion, lipsync_param_ids
from .export_godot import write_godot_anim
from .export_gltf import write_gltf, build_gltf
from .export_lip import (write_lip, lip_bytes, skyrim_mapping,
                        SKYRIM_SLOT_MAP)
from .export_cues import (
    dominant_cues, write_rhubarb_tsv, write_rhubarb_xml, write_rhubarb_json,
    write_moho_dat, write_pgo,
)
from .export_captions import (
    write_captions, write_srt, write_vtt, srt_text, vtt_text, build_cues,
    word_timings, format_timestamp, CaptionCue,
)
from .mapping import Mapping, Target
from .ipa import IPA_MAPPING, is_ipa_vowel, ipa_unknown_symbols
from .retarget import (retarget, apply_adjust, rename_only, PRESETS,
                       PRESET_FALLBACKS)
from .visemes import VISEMES, phoneme_to_viseme
from .timing import (
    TimingEvent, resolve_ends, to_segments, parse_pho, parse_piper_alignments,
    parse_cartesia, parse_azure_visemes, parse_polly_marks,
    viseme_events_to_segments, build_vendor_mapping,
    AZURE_VISEME_TO_TARGET, POLLY_VISEME_TO_TARGET,
)
from .anchors import (
    Anchor, anchored_segments, anchors_transcript, parse_srt,
    parse_word_anchors, from_azure_word_boundaries, from_elevenlabs_alignment,
    from_kokoro_tokens, google_ssml_with_marks, from_google_timepoints,
)

__version__ = "0.15.0"

__all__ = [
    "G2P", "PhonemeSegment", "NaiveAligner", "load_mfa_textgrid",
    "build_viseme_curves", "CoartParams", "STYLE_PRESETS", "style_params",
    "FaceTrack", "Channel", "Keyframe",
    "reduce_to_track", "smooth_matrix", "time_shift",
    "generate_from_alignment", "generate_naive",
    "naive_segments", "wav_duration", "derive_events",
    "energy_envelope", "generate_from_energy",
    "ProsodyParams", "ProsodyTrack", "pitch_track", "prosody_features",
    "prosody_events", "detect_events",
    "GestureParams", "generate_gestures", "gestures_from_wav",
    "add_gestures_to_track", "GESTURE_CHANNELS",
    "Event", "Alternative", "VariantGroup", "Variants", "EVENT_TYPES",
    "resolve", "choose", "add_event", "attach_events", "read_events",
    "validate_events",
    "to_dict", "write_json", "write_csv", "from_dict", "read_json",
    "summarize", "normalize_transcript", "cue_flags",
    "parse_tagged_transcript", "Tag", "has_tags", "resolve_tagged_segments",
    "curve_channels", "tag_events", "build_curve_channel",
    "parse_ssml", "looks_like_ssml", "EMPHASIS_STRENGTH", "BREAK_STRENGTH",
    "EditsDoc", "diff_edits", "apply_edits", "load_edits", "save_edits", "sample",
    "EmotionEnvelope", "bake_emotion", "load_envelope", "save_envelope",
    "va_to_pose", "VA_TABLE", "VA_EMOTION_CHANNELS",
    "import_cues", "detect_format", "build_cue_track",
    "RHUBARB_TO_VISEME", "PRESTON_BLAIR_TO_VISEME", "read_csv",
    "inspect_track", "validate_asset", "validate_file", "detect_kind",
    "retime", "retime_to_duration", "mirror", "trim", "concat",
    "MIRROR_PAIRS", "MIRROR_NEGATE",
    "generate_lods", "make_lod", "lod_metadata", "switching_table",
    "LOD_DEFAULT_RDP", "LOD_DEFAULT_FPS",
    "channel_energy", "rank_channels", "keep_channels", "keep_top_weight",
    "budget_channels", "budget_metadata",
    "Layer", "build_layers", "flatten_layers", "layers_to_dict",
    "layers_from_dict",
    "read_manifest", "manifest_jobs", "COLUMN_ALIASES",
    "diff_tracks", "render_diff",
    "write_unity_anim", "NAMING_PRESETS",
    "write_unreal_notifies", "notifies_to_dict",
    "write_live2d_motion", "lipsync_param_ids", "write_godot_anim",
    "write_gltf", "build_gltf",
    "write_lip", "lip_bytes", "skyrim_mapping", "SKYRIM_SLOT_MAP",
    "dominant_cues", "write_rhubarb_tsv", "write_rhubarb_xml",
    "write_rhubarb_json", "write_moho_dat", "write_pgo",
    "write_captions", "write_srt", "write_vtt", "srt_text", "vtt_text",
    "build_cues", "word_timings", "format_timestamp", "CaptionCue",
    "Mapping", "Target",
    "IPA_MAPPING", "is_ipa_vowel", "ipa_unknown_symbols",
    "retarget", "apply_adjust", "rename_only", "PRESETS", "PRESET_FALLBACKS",
    "VISEMES", "phoneme_to_viseme",
    "TimingEvent", "resolve_ends", "to_segments", "parse_pho",
    "parse_piper_alignments", "parse_cartesia", "parse_azure_visemes",
    "parse_polly_marks", "viseme_events_to_segments", "build_vendor_mapping",
    "AZURE_VISEME_TO_TARGET", "POLLY_VISEME_TO_TARGET",
    "Anchor", "anchored_segments", "anchors_transcript", "parse_srt",
    "parse_word_anchors", "from_azure_word_boundaries",
    "from_elevenlabs_alignment", "from_kokoro_tokens", "google_ssml_with_marks",
    "from_google_timepoints",
]
