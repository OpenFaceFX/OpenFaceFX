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
from .io_export import to_dict, write_json, write_csv
from .export_unity import write_unity_anim, NAMING_PRESETS
from .mapping import Mapping, Target
from .retarget import retarget, rename_only, PRESETS
from .visemes import VISEMES, phoneme_to_viseme

__version__ = "0.2.0"

__all__ = [
    "G2P", "PhonemeSegment", "NaiveAligner", "load_mfa_textgrid",
    "build_viseme_curves", "CoartParams", "FaceTrack", "Channel", "Keyframe",
    "reduce_to_track", "generate_from_alignment", "generate_naive",
    "naive_segments", "wav_duration", "to_dict", "write_json", "write_csv",
    "write_unity_anim", "NAMING_PRESETS",
    "Mapping", "Target",
    "retarget", "rename_only", "PRESETS",
    "VISEMES", "phoneme_to_viseme",
]
