"""OpenFaceFX -- an open-source facial/lip-sync animation pipeline.

audio + text  ->  time-stamped phonemes  ->  visemes  ->  coarticulated
animation curves  ->  export for your rig.
"""

from .g2p import G2P
from .alignment import PhonemeSegment, NaiveAligner, load_mfa_textgrid
from .coarticulation import build_viseme_curves, CoartParams
from .curves import FaceTrack, Channel, Keyframe, reduce_to_track
from .pipeline import (generate_from_alignment, generate_naive,
                       naive_segments, wav_duration)
from .energy import energy_envelope, generate_from_energy
from .io_export import to_dict, write_json, write_csv
from .export_unity import write_unity_anim, NAMING_PRESETS
from .export_live2d import write_live2d_motion, lipsync_param_ids
from .export_godot import write_godot_anim
from .export_cues import (
    dominant_cues, write_rhubarb_tsv, write_rhubarb_xml, write_rhubarb_json,
    write_moho_dat, write_pgo,
)
from .mapping import Mapping, Target
from .retarget import retarget, rename_only, PRESETS
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

__version__ = "0.4.0"

__all__ = [
    "G2P", "PhonemeSegment", "NaiveAligner", "load_mfa_textgrid",
    "build_viseme_curves", "CoartParams", "FaceTrack", "Channel", "Keyframe",
    "reduce_to_track", "generate_from_alignment", "generate_naive",
    "naive_segments", "wav_duration", "energy_envelope", "generate_from_energy",
    "to_dict", "write_json", "write_csv",
    "write_unity_anim", "NAMING_PRESETS",
    "write_live2d_motion", "lipsync_param_ids", "write_godot_anim",
    "dominant_cues", "write_rhubarb_tsv", "write_rhubarb_xml",
    "write_rhubarb_json", "write_moho_dat", "write_pgo",
    "Mapping", "Target",
    "retarget", "rename_only", "PRESETS",
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
