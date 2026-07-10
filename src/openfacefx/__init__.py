"""OpenFaceFX -- an open-source facial/lip-sync animation pipeline.

audio + text  ->  time-stamped phonemes  ->  visemes  ->  coarticulated
animation curves  ->  export for your rig.
"""

from .g2p import G2P
from .alignment import PhonemeSegment, NaiveAligner, load_mfa_textgrid
from .coarticulation import build_viseme_curves
from .curves import FaceTrack, Channel, Keyframe, reduce_to_track
from .pipeline import (generate_from_alignment, generate_naive,
                       naive_segments, wav_duration)
from .io_export import to_dict, write_json, write_csv
from .retarget import retarget, rename_only, PRESETS
from .visemes import VISEMES, phoneme_to_viseme

__version__ = "0.1.0"

__all__ = [
    "G2P", "PhonemeSegment", "NaiveAligner", "load_mfa_textgrid",
    "build_viseme_curves", "FaceTrack", "Channel", "Keyframe",
    "reduce_to_track", "generate_from_alignment", "generate_naive",
    "naive_segments", "wav_duration", "to_dict", "write_json", "write_csv",
    "retarget", "rename_only", "PRESETS",
    "VISEMES", "phoneme_to_viseme",
]
