"""Bethesda ``.lip`` payload writer — Skyrim (EXPERIMENTAL).

    ⚠️  EXPERIMENTAL — NOT YET VERIFIED IN-GAME.  ⚠️

This is the first clean-room writer for the FaceFX facial-animation blob inside a
Skyrim ``.lip`` file. The byte format was reverse-engineered purely from analysis
of four sample files (three mod-author placeholders plus one real vanilla
Creation-Kit asset); see ``tools/lip_codec_research.py`` for the codec and issue
#12 for the full derivation. Our encoder re-serializes all four samples
**byte-identically**, and every track this module writes round-trips through our
own decoder exactly (``tests/test_export_lip.py``).

What is NOT verified, because it needs Skyrim + the Creation Kit and nobody has
run that test yet:

  * **Does the game load it without crashing and animate a face?** Unknown.
  * **Slot → morph mapping — SLOT IS NOT THE TARGET INDEX.** The payload routes
    each curve to a numbered grid slot (0..32 for Skyrim), not a named target,
    and the real asset spreads 13 curves across slots up to 30 (a curve may even
    occupy two slots as a value+tangent pair). Which slot drives which of
    Skyrim's 16 speech morphs is UNRESOLVED. ``SKYRIM_SLOT_MAP`` below is a
    deliberately provisional hypothesis: only its jaw assignment is
    evidence-informed (the vanilla asset's slot 22 is a long-lived jaw-like
    curve), the rest are placeholders. **Until it is calibrated, the mouth may
    move but form the WRONG shapes.** Resolve it empirically — no reverse
    engineering, just eyes on a screen — with the calibration set:
    ``openfacefx lip-calibrate --out DIR`` writes one .lip per slot (a single
    slot swept 0→1→0); play each on a voiced NPC line, note which mouth part
    moves, and fill in ``SKYRIM_SLOT_MAP``. See ``docs/COMPATIBILITY.md``.
  * **Header field ``u22``** (see ``_U22_SKYRIM``): its meaning was never cracked;
    we copy the value the one real vanilla asset uses.

Treat the output as a research artifact whose mouth shapes are uncalibrated. If
you can test it in-game, please report back on issue #12. Fallout 4 is
unsupported for authoring (its 43-target vocabulary is undocumented) —
``game='fallout4'`` raises ``NotImplementedError`` from ``write_lip`` — but the
calibration set works for any game's raw slots.

Input is the phoneme-timing layer (``List[PhonemeSegment]`` from
``pipeline.naive_segments`` / ``alignment.load_mfa_textgrid``); we drive the
existing coarticulation solver through an ARPAbet→Skyrim-16 ``Mapping`` and
sample the resulting weight envelopes on Skyrim's 30 fps frame grid.
"""

from __future__ import annotations

import os
import struct
from typing import Dict, List, Optional

import numpy as np

from .alignment import PhonemeSegment
from .bethesda import SKYRIM_TARGETS
from .coarticulation import CoartParams, build_viseme_curves
from .mapping import Mapping, Target
from .phonemes import SILENCE

# --- Skyrim engine constants (verified across all four samples) --------------
_FPS = 30.0                 # Skyrim lip grid is 30 fps (time_s = frame / 30)
_TICKS_PER_FRAME = 132      # duration@4 = 132 * count12 + 28
_DURATION_BIAS = 28
_STRIDE = 33                # slots per frame R for Skyrim (u20=16); != 16
_U20_SKYRIM = 16            # target-vocabulary field
_VERSION = 1
_CONST14 = 3                # key = (value, slopeIn, slopeOut)
_MAX_PREROLL = 9            # observed neg16 range is -2..-9; clamp anticipation

# u22 was never resolved (values 63/163/199/3 across the four samples fit no
# countable — dup/marker/skip/slot/frame totals were all tested). We emit the
# value from the sole game-authored asset (vanilla Skyrim). This is the writer's
# weakest assumption; if a loader rejects the file, u22 is a prime suspect.
_U22_SKYRIM = 3

# A resting curve can be written with a sentinel float (~9.6e-16, bytes
# f9 e8 8a 26); this writer instead emits real 0.0 weights, which the game reads
# as the same zero opening — simpler and dup-safe (see SKYRIM_SLOT_ORDER).
_EPS = 1e-4  # a target below this is "at rest"; matches the solver's own floor

# --- ARPAbet → Skyrim-16 targets ---------------------------------------------
# The 16 targets are Skyrim's MFG speech morphs (bethesda.SKYRIM_TARGETS). This
# table is the proposal on issue #12 (a synthesis, not a sourced fact). Whole
# diphthong rows (AY/EY/OW/OY/AW) are consulted only if diphthong-splitting is
# disabled; by default the coarticulation solver decomposes them into the
# component vowels above (e.g. AY → AA+IY → BigAah+Eee).
_ARPABET_TO_TARGET: Dict[str, Dict[str, float]] = {
    "B": {"BMP": 1.0}, "P": {"BMP": 1.0}, "M": {"BMP": 1.0},
    "F": {"FV": 1.0}, "V": {"FV": 1.0},
    "TH": {"Th": 1.0}, "DH": {"Th": 1.0},
    "D": {"DST": 1.0}, "T": {"DST": 1.0}, "S": {"DST": 1.0}, "Z": {"DST": 1.0},
    "N": {"N": 1.0}, "L": {"N": 1.0},
    "K": {"k": 1.0}, "G": {"k": 1.0}, "NG": {"k": 1.0}, "HH": {"k": 1.0},
    "CH": {"ChjSh": 1.0}, "JH": {"ChjSh": 1.0},
    "SH": {"ChjSh": 1.0}, "ZH": {"ChjSh": 1.0},
    "R": {"R": 1.0}, "ER": {"R": 1.0},
    "W": {"W": 1.0}, "Y": {"Eee": 1.0},
    "IY": {"Eee": 1.0}, "IH": {"i": 1.0},
    "EH": {"Eh": 1.0}, "AE": {"Eh": 1.0},
    "AH": {"Aah": 1.0}, "AA": {"BigAah": 1.0},
    "AO": {"Oh": 1.0}, "UH": {"OohQ": 1.0}, "UW": {"OohQ": 1.0},
    "AY": {"Aah": 1.0}, "EY": {"Eh": 0.5, "Eee": 0.5},
    "OW": {"Oh": 1.0}, "OY": {"Oh": 1.0}, "AW": {"BigAah": 1.0},
    SILENCE: {},
}

# Articulator class per target, for the coarticulation model's timing/closure.
# Only "lips" targets get closure enforcement (a bilabial seals the mouth), so
# BMP/FV are lips; the rounding glide W is "basic" to avoid a forced full seal.
_TARGET_CLASS: Dict[str, str] = {
    "Aah": "jaw", "BigAah": "jaw", "Eee": "jaw", "Eh": "jaw",
    "i": "jaw", "Oh": "jaw", "OohQ": "jaw",
    "BMP": "lips", "FV": "lips", "W": "basic",
    "ChjSh": "tongue", "DST": "tongue", "Th": "tongue",
    "N": "tongue", "k": "tongue", "R": "tongue",
}

# PROVISIONAL, UNCALIBRATED slot → target map. SLOT IS NOT THE TARGET INDEX: the
# payload numbers curve slots 0..R-1 and the engine reads each as some morph, but
# which morph is UNKNOWN without in-game testing (resolve it with the
# ``lip-calibrate`` set — see the module docstring). Until then, mouth shapes may
# be scrambled. Two design choices constrain this hypothesis:
#   * Targets sit on EVEN slots 0,2,..,30. Even spacing guarantees a resting-skip
#     marker between every two stored values, so adjacent equal weights (e.g. two
#     0.0s) can never be misread as a doubled-value key.
#   * The ONE evidence-informed assignment: the vanilla asset's slot 22 carries a
#     long-lived, mid-amplitude jaw-like curve (active ~43/74 frames), so the
#     open-jaw vowels Aah/BigAah are placed at slots 22/24. Every OTHER row is an
#     arbitrary placeholder filling the remaining even slots in engine order.
# Calibrate, then edit this table — it is the last unknown in the format.
SKYRIM_SLOT_MAP: Dict[str, int] = {
    "Aah": 22, "BigAah": 24,                       # evidence-informed (jaw slot)
    "BMP": 0, "ChjSh": 2, "DST": 4, "Eee": 6,      # placeholders (unverified),
    "Eh": 8, "FV": 10, "i": 12, "k": 14,           # remaining even slots in
    "N": 16, "Oh": 18, "OohQ": 20, "R": 26,        # engine order
    "Th": 28, "W": 30,
}
assert sorted(SKYRIM_SLOT_MAP) == sorted(SKYRIM_TARGETS)
assert sorted(SKYRIM_SLOT_MAP.values()) == list(range(0, 32, 2))  # even 0..30, unique

# The decoder's positional walk starts at pos 0, so a token must exist at grid
# slot 0; the writer forces this slot active (emitted every frame, at rest when
# its target is silent). It is a format anchor, not a claim about slot 0's morph.
_ANCHOR_SLOT = 0


def skyrim_mapping() -> Mapping:
    """The ARPAbet → Skyrim-16 ``Mapping`` the writer drives coarticulation with.
    ``allow_custom_symbols`` is False: rows are ARPAbet, validated on build."""
    targets = [Target(name, _TARGET_CLASS[name]) for name in SKYRIM_TARGETS]
    return Mapping(targets, {ph: dict(row) for ph, row in _ARPABET_TO_TARGET.items()})


def _default_params() -> CoartParams:
    """Coarticulation tunables for lip export: sample up to _MAX_PREROLL frames of
    anticipation before the first onset (the negative-pre-roll neg16 the format
    expects), reproduced deterministically so tests can rebuild the same grid."""
    return CoartParams(preroll=_MAX_PREROLL / _FPS, allow_negative_time=True)


def _pack_header(num_curves: int, count12: int, neg16: int) -> bytes:
    duration = _TICKS_PER_FRAME * count12 + _DURATION_BIAS
    return struct.pack("<IIIHHiHH", _VERSION, duration, num_curves,
                       count12, _CONST14, neg16, _U20_SKYRIM, _U22_SKYRIM)


def _frame_grid(segments: List[PhonemeSegment], duration_s: float,
                mapping: Mapping, params: CoartParams):
    """Sample the coarticulated 16-target envelopes on Skyrim's integer frame
    grid. Returns (grid, neg16, count12) where grid[row, target] in [0,1] and
    row r corresponds to engine frame ``neg16 + r``."""
    times, matrix = build_viseme_curves(segments, fps=_FPS, mapping=mapping,
                                        params=params)
    n_audio = max(int(round(duration_s * _FPS)), 1)   # audio spans frames 0..n_audio-1
    if len(times) == 0:
        matrix = np.zeros((1, len(mapping.targets)))
        times = np.zeros(1)

    # Resample every column onto integer frame times, from any pre-onset frame
    # (times may start below 0) through the end of the audio. np.interp clamps
    # to the endpoint values outside the sampled range (rest at the tail).
    lo = int(np.floor(times[0] * _FPS))
    hi = max(int(np.ceil(times[-1] * _FPS)), n_audio - 1)
    frames = np.arange(lo, hi + 1)
    ft = frames / _FPS
    grid = np.empty((len(frames), matrix.shape[1]))
    for c in range(matrix.shape[1]):
        grid[:, c] = np.interp(ft, times, matrix[:, c])
    np.clip(grid, 0.0, 1.0, out=grid)
    grid[grid < _EPS] = 0.0

    # First frame with any activity → pre-roll (negative), clamped to the
    # observed -9..0 band. count12 covers pre-roll through the end of the audio.
    active_rows = np.where(grid.max(axis=1) > _EPS)[0]
    first_active = int(frames[active_rows[0]]) if len(active_rows) else 0
    preroll = min(max(-first_active, 0), _MAX_PREROLL)
    neg16 = -preroll
    count12 = n_audio + preroll

    # Extract exactly rows for engine frames neg16 .. neg16+count12-1.
    out = np.zeros((count12, matrix.shape[1]))
    for r in range(count12):
        f = neg16 + r
        idx = f - lo
        if 0 <= idx < len(frames):
            out[r] = grid[idx]
    return out, neg16, count12


def lip_bytes(segments: List[PhonemeSegment], duration_s: float,
              game: str = "skyrim", params: Optional[CoartParams] = None) -> bytes:
    """Encode ``segments`` to Skyrim ``.lip`` bytes (header + payload).

    EXPERIMENTAL and unverified in-game — see the module docstring. ``segments``
    is the phoneme-timing layer; ``duration_s`` is the audio duration in seconds.
    ``game`` must be ``'skyrim'`` (``'fallout4'`` raises ``NotImplementedError``).
    Raises ``ValueError`` on empty input or entirely silent speech.
    """
    if game == "fallout4":
        raise NotImplementedError(
            "Fallout 4 .lip is not supported: its 43-target vocabulary (u20=43, "
            "stride R=60) is undocumented, so a slot→morph mapping cannot be "
            "written honestly. Skyrim only (game='skyrim').")
    if game != "skyrim":
        raise ValueError(f"unknown game {game!r}; expected 'skyrim'")
    if not segments:
        raise ValueError("no segments to encode")
    if not (duration_s > 0.0):
        raise ValueError(f"duration_s must be positive, got {duration_s!r}")

    params = params or _default_params()
    mapping = skyrim_mapping()
    grid, neg16, count12 = _frame_grid(segments, duration_s, mapping, params)

    # Which targets ever fire → curves. Force slot 0 (Aah) on so the stream
    # begins at grid origin (0,0), where the decoder's positional walk starts.
    names = mapping.target_names
    active = {SKYRIM_SLOT_ORDER[names[c]] for c in range(grid.shape[1])
              if float(grid[:, c].max()) > _EPS}
    active.add(_ALWAYS_ON_SLOT)
    if len(active) <= 1 and float(grid.max()) <= _EPS:
        raise ValueError("input is entirely silent; nothing to animate")
    slot_to_col = {SKYRIM_SLOT_ORDER[names[c]]: c for c in range(len(names))}
    active_slots = sorted(active)

    # Dense frame-major cells: every active curve at every row (the game
    # interpolates between them). Emitting every row keeps consecutive keys
    # well under the 63-slot marker span, so no key is ever dropped.
    cells = []
    for r in range(count12):
        for slot in active_slots:
            cells.append({"frame": r, "curve": slot,
                          "value": float(grid[r, slot_to_col[slot]])})

    payload = _encode_cells(cells, _STRIDE, total_slots=_STRIDE * count12)
    return _pack_header(len(active_slots), count12, neg16) + payload


def write_lip(segments: List[PhonemeSegment], duration_s: float, path: str,
              game: str = "skyrim", params: Optional[CoartParams] = None) -> None:
    """Write an EXPERIMENTAL Skyrim ``.lip`` file (see module docstring / #12).

    Not verified in-game: the output decodes exactly through our own reader, but
    whether Skyrim loads and animates it is untested. ``game='fallout4'`` raises
    ``NotImplementedError``.
    """
    data = lip_bytes(segments, duration_s, game=game, params=params)
    with open(path, "wb") as fh:
        fh.write(data)


def lip_calibrate(out_dir: str, game: str = "skyrim",
                  seconds: float = 2.0) -> List[str]:
    """Write one EXPERIMENTAL ``.lip`` per Skyrim speech target for in-game
    slot calibration: in each file that target's slot ramps 0→1→0 (triangle,
    peak mid-clip) while everything else rests. Drop each file on any voiced
    line in-game; whichever mouth part moves tells you what that slot really
    drives. ``SKYRIM_SLOT_ORDER`` is the uncalibrated assumption these files
    exist to test — please report findings on issue #12.
    """
    if game == "fallout4":
        raise NotImplementedError("calibration is Skyrim-only for now "
                                  "(FO4's 43-target vocabulary is undocumented)")
    if game != "skyrim":
        raise ValueError(f"unknown game {game!r}; expected 'skyrim'")
    # odd frame count => the triangle's apex lands exactly on a row, so the
    # calibration slot genuinely reaches full-open 1.0
    count12 = max(int(round(seconds * _FPS)), 9) | 1
    os.makedirs(out_dir, exist_ok=True)
    written: List[str] = []
    for name, slot in sorted(SKYRIM_SLOT_ORDER.items(), key=lambda kv: kv[1]):
        cells = []
        for r in range(count12):
            x = r / (count12 - 1)
            v = round(1.0 - abs(2.0 * x - 1.0), 4)
            if slot != _ALWAYS_ON_SLOT:
                # anchor the stream at grid origin (0,0), at rest
                cells.append({"frame": r, "curve": _ALWAYS_ON_SLOT, "value": 0.0})
            cells.append({"frame": r, "curve": slot, "value": v})
        payload = _encode_cells(cells, _STRIDE, total_slots=_STRIDE * count12)
        n_curves = 1 if slot == _ALWAYS_ON_SLOT else 2
        path = os.path.join(out_dir, f"calibrate_slot{slot:02d}_{name}.lip")
        with open(path, "wb") as fh:
            fh.write(_pack_header(n_curves, count12, 0) + payload)
        written.append(path)
    return written


def _encode_cells(cells, R: int, total_slots: int) -> bytes:
    """Serialize ordered frame-major grid cells to payload bytes: value floats
    with the resting-slot skip encoded as the derived gap marker between them.

    Mirror of ``tools/lip_codec_research.encode_curves`` (kept in-package so the
    shipped writer has no dependency on the research script). The final token's
    marker pads the stream to ``total_slots`` (a full frame, R*count12), matching
    the vanilla asset. Assumes gaps ≤ 63 slots, which dense emission guarantees.
    """
    out = bytearray()
    n = len(cells)
    for i, c in enumerate(cells):
        pos = c["frame"] * R + c["curve"]
        out += struct.pack("<f", c["value"])
        if i < n - 1:
            nxt = cells[i + 1]
            skip = (nxt["frame"] * R + nxt["curve"]) - pos - 1
        else:
            skip = total_slots - pos - 1
        if skip < 0:
            raise ValueError(f"cell {i}: negative skip {skip} (cells misordered)")
        if skip > 63:
            raise ValueError(f"cell {i}: skip {skip} > 63 exceeds one marker span")
        if skip:
            out += bytes([0, 4 * skip, 0])
    return bytes(out)
