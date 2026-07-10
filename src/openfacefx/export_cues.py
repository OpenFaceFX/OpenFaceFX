"""Rhubarb-dialect cue exporters: stepped mouth-shape lists for 2D hosts.

The engine-agnostic ``FaceTrack`` is a bundle of smooth, overlapping viseme
curves. A whole ecosystem of indie 2D lip-sync hosts instead wants a *stepped*
cue list -- one mouth shape held per interval. This module flattens a track to
that representation (the single highest-weight channel wins at every sampled
frame) and serialises it in the formats those hosts read:

  * Rhubarb Lip Sync TSV / XML / JSON   -- ``write_rhubarb_{tsv,xml,json}``
  * Moho / OpenToonz switch data (.dat) -- ``write_moho_dat``
  * Papagayo-NG (.pgo)                  -- ``write_pgo``

Shape vocabulary is handled for you: a track in the Oculus-15 viseme set is
retargeted through the built-in ``rhubarb`` / ``preston_blair`` presets; a track
already in Rhubarb A-H/X (or Preston-Blair) shapes is passed through untouched;
anything else is rejected with a clear error. Times and frames are quantised
exactly as the reference tools emit them -- Rhubarb prints seconds as ``%.2f``;
Moho/Papagayo frames are 1-based and truncated (``1 + int(fps * seconds)``).
Pure stdlib serialisation, LF line endings.
"""

from __future__ import annotations

import json
from typing import List, Optional, Set, Tuple
from xml.sax.saxutils import escape as _xml_escape

from .curves import FaceTrack
from .retarget import PRESETS, PRESET_FALLBACKS, retarget, _sampler
from .visemes import VISEMES

Cue = Tuple[float, float, str]

# Rhubarb's nine mouth shapes: six basic (A-F) plus three extended (G, H, X).
_RHUBARB_SHAPES = frozenset("ABCDEFGHX")
# Preston-Blair drawing names (Papagayo / Moho / OpenToonz). A superset of what
# the preset emits (it never needs WQ), listed in full so a hand-authored
# Preston-Blair track passes through.
_PB_SHAPES = frozenset({"AI", "E", "O", "U", "etc", "L", "WQ", "MBP", "FV", "rest"})
_OCULUS_SHAPES = frozenset(VISEMES)

# Rhubarb's documented fallback when a rig lacks an extended shape (README,
# "Extended mouth shapes"): the f/v shape G and the idle X collapse to the
# closed A, the tongue-up H to the open C. The collapse lives once, in
# retarget.PRESET_FALLBACKS (weighted form, shared with retarget's available=);
# here we need the single-shape view a cue label steps to, so flatten each
# rhubarb rule to its lone replacement (they are all single-target renames).
RHUBARB_EXTENDED_FALLBACK = {k: v[0][0] for k, v in PRESET_FALLBACKS["rhubarb"].items()}

# A frame whose loudest channel sits below this weight is treated as silence
# (rest / X). Real tracks carry an explicit sil->X/rest channel that wins on its
# own during pauses, so this floor only rescues hand-authored tracks with dead
# gaps and no rest channel.
_SILENCE_FLOOR = 0.1

_XML_DECLARATION = '<?xml version="1.0" encoding="utf-8"?>'
_DAT_FPS_MIN, _DAT_FPS_MAX = 24, 100
# Nudge before truncating so a frame time that lands microscopically below its
# integer (e.g. 29.9999999 from float error) still floors to the right frame.
_FRAME_EPS = 1e-6


def _frame_at(fps: float, seconds: float) -> int:
    """1-based, truncated frame index for ``seconds`` at ``fps``."""
    return 1 + int(fps * seconds + _FRAME_EPS)


def dominant_cues(track: FaceTrack, rest_name: str = "X",
                  silence_floor: float = _SILENCE_FLOOR) -> List[Cue]:
    """Flatten ``track`` to ``(start, end, shape)`` runs by dominant channel.

    At each frame (sampled at the track's own fps) the single highest-weight
    channel wins; a frame with nothing above ``silence_floor`` becomes
    ``rest_name``. Adjacent equal-shape frames merge into one run, and the runs
    tile ``[0, duration]`` with no gaps.
    """
    fps = track.fps or 1.0
    dur = track.duration
    samplers = [(c.name, _sampler(c)) for c in track.channels]
    n = int(round(dur * fps))
    labels: List[Tuple[float, str]] = []
    for i in range(n + 1):
        t = i / fps
        best_name, best_w = rest_name, silence_floor
        for name, sample in samplers:
            w = sample(t)
            if w > best_w:
                best_name, best_w = name, w
        labels.append((t, best_name))
    if not labels:
        return []
    cues: List[Cue] = []
    start, current = labels[0]
    for t, name in labels[1:]:
        if name != current:
            cues.append((start, min(t, dur), current))
            start, current = t, name
    if start < dur:  # a lone differing frame exactly at the end is negligible
        cues.append((start, dur, current))
    return cues


def _coerce(track: FaceTrack, shapes: frozenset, default_preset: str,
            retarget_preset: Optional[str]) -> FaceTrack:
    """Return ``track`` expressed in ``shapes``, retargeting when it is not."""
    present = {c.name for c in track.channels}
    declared = set(track.target_set) if track.target_set is not None else set(VISEMES)
    vocab = present | declared
    if vocab <= shapes:
        return track
    if retarget_preset is not None:
        return retarget(track, PRESETS[retarget_preset])
    if vocab <= _OCULUS_SHAPES:
        return retarget(track, PRESETS[default_preset])
    raise ValueError(
        f"cannot express track as {default_preset} cue shapes: channels "
        f"{sorted(present)} are neither that shape set nor Oculus-15 visemes; "
        f"retarget the track first or pass retarget_preset=")


def _fallback(name: str, available: Set[str]) -> str:
    seen: Set[str] = set()
    while name not in available and name in RHUBARB_EXTENDED_FALLBACK and name not in seen:
        seen.add(name)
        name = RHUBARB_EXTENDED_FALLBACK[name]
    return name


def _collapse(cues: List[Cue], available: Optional[Set[str]]) -> List[Cue]:
    """Substitute extended shapes the art lacks with their basic fallback."""
    if available is None:
        return cues
    return [(s, e, _fallback(name, available)) for s, e, name in cues]


def _to_frames(cues: List[Cue], fps: float) -> List[Tuple[int, str]]:
    """1-based, truncated start frame per cue; drop a cue that lands on the
    frame just emitted (keep the first, matching Rhubarb's .dat exporter)."""
    frames: List[Tuple[int, str]] = []
    prev = None
    for start, _end, name in cues:
        frame = _frame_at(fps, start)
        if frame == prev:
            continue
        frames.append((frame, name))
        prev = frame
    return frames


def _write_lines(path: str, lines: List[str]) -> None:
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("\n".join(lines) + "\n")


def _rhubarb_cues(track: FaceTrack, retarget_preset: Optional[str],
                  available_shapes: Optional[Set[str]]) -> List[Cue]:
    src = _coerce(track, _RHUBARB_SHAPES, "rhubarb", retarget_preset)
    return _collapse(dominant_cues(src, "X"), available_shapes)


def write_rhubarb_tsv(track: FaceTrack, path: str, *,
                      retarget_preset: Optional[str] = None,
                      available_shapes: Optional[Set[str]] = None) -> None:
    """Rhubarb ``-f tsv``: header-less ``start<TAB>shape`` lines, then a final
    terminal row at the end time bounding the last cue."""
    cues = _rhubarb_cues(track, retarget_preset, available_shapes)
    end = cues[-1][1] if cues else track.duration
    rest = _fallback("X", available_shapes) if available_shapes else "X"
    lines = [f"{s:.2f}\t{name}" for s, _e, name in cues]
    lines.append(f"{end:.2f}\t{rest}")
    _write_lines(path, lines)


def write_rhubarb_xml(track: FaceTrack, path: str, *,
                      sound_file: str = "openfacefx",
                      retarget_preset: Optional[str] = None,
                      available_shapes: Optional[Set[str]] = None) -> None:
    """Rhubarb ``-f xml``: a ``rhubarbResult`` tree with soundFile/duration
    metadata and ``mouthCue`` start/end elements (no terminal sentinel)."""
    cues = _rhubarb_cues(track, retarget_preset, available_shapes)
    end = cues[-1][1] if cues else track.duration
    lines = [_XML_DECLARATION, "<rhubarbResult>", "  <metadata>",
             f"    <soundFile>{_xml_escape(sound_file)}</soundFile>",
             f"    <duration>{end:.2f}</duration>", "  </metadata>",
             "  <mouthCues>"]
    for s, e, name in cues:
        lines.append(f'    <mouthCue start="{s:.2f}" end="{e:.2f}">{name}</mouthCue>')
    lines += ["  </mouthCues>", "</rhubarbResult>"]
    _write_lines(path, lines)


def write_rhubarb_json(track: FaceTrack, path: str, *,
                       sound_file: str = "openfacefx",
                       retarget_preset: Optional[str] = None,
                       available_shapes: Optional[Set[str]] = None) -> None:
    """Rhubarb ``-f json``: hand-formatted (2/4-space indent), a metadata
    object and a ``mouthCues`` array of ``{start, end, value}`` objects."""
    cues = _rhubarb_cues(track, retarget_preset, available_shapes)
    end = cues[-1][1] if cues else track.duration
    lines = ["{", '  "metadata": {',
             f'    "soundFile": {json.dumps(sound_file)},',
             f'    "duration": {end:.2f}', "  },", '  "mouthCues": [']
    last = len(cues) - 1
    for i, (s, e, name) in enumerate(cues):
        comma = "" if i == last else ","
        lines.append(f'    {{ "start": {s:.2f}, "end": {e:.2f}, '
                     f'"value": {json.dumps(name)} }}{comma}')
    lines += ["  ]", "}"]
    _write_lines(path, lines)


def write_moho_dat(track: FaceTrack, path: str, *, fps: float = 24,
                   preston_blair: bool = True,
                   retarget_preset: Optional[str] = None) -> None:
    """Moho / OpenToonz switch data. First line ``MohoSwitch1``; then
    ``<frame> <shape>`` rows on a 1-based truncated timeline; a terminal
    rest/X row at the end frame (bumped one frame on a collision).

    ``preston_blair`` (default) emits Preston-Blair drawing names, which
    OpenToonz's "Apply Lip Sync Data" and Moho switch layers match by name;
    turn it off for Rhubarb's raw A-H/X letters. ``fps`` must be 24..100
    (a float, so NTSC rates like 29.97 are accepted); out of range is a
    clear error, matching Rhubarb (which rejects rather than clamps).
    """
    if not (_DAT_FPS_MIN <= fps <= _DAT_FPS_MAX):
        raise ValueError(
            f"dat frame rate must be {_DAT_FPS_MIN}..{_DAT_FPS_MAX} fps, got {fps}")
    if preston_blair:
        src, rest = _coerce(track, _PB_SHAPES, "preston_blair", retarget_preset), "rest"
    else:
        src, rest = _coerce(track, _RHUBARB_SHAPES, "rhubarb", retarget_preset), "X"
    cues = dominant_cues(src, rest)
    frames = _to_frames(cues, fps)
    lines = ["MohoSwitch1"]
    lines += [f"{frame} {name}" for frame, name in frames]
    end = cues[-1][1] if cues else track.duration
    end_frame = _frame_at(fps, end)
    if frames and end_frame == frames[-1][0]:
        end_frame += 1
    lines.append(f"{end_frame} {rest}")
    _write_lines(path, lines)


def write_pgo(track: FaceTrack, path: str, *, fps: float = 24,
              sound_path: str = "openfacefx", voice_name: str = "Voice 1",
              retarget_preset: Optional[str] = None) -> None:
    """Papagayo-NG ``.pgo`` (version 1): the flattened Preston-Blair cues as a
    single voice / phrase / word phoneme stream, TAB-indented. Frames are
    1-based truncated; ``fps`` is stored as an integer (Papagayo's rate is
    ``%d``); ``sound_path`` defaults to a placeholder rather than a local
    absolute path.
    """
    src = _coerce(track, _PB_SHAPES, "preston_blair", retarget_preset)
    phonemes = _to_frames(dominant_cues(src, "rest"), fps)
    total_frames = int(round(fps * track.duration))
    start_frame = phonemes[0][0] if phonemes else 1
    end_frame = max(total_frames, phonemes[-1][0]) if phonemes else 1
    label = "openfacefx"
    lines = ["lipsync version 1", sound_path, str(int(fps)), str(total_frames), "1",
             f"\t{voice_name}", f"\t{label}", "\t1",
             f"\t\t{label}", f"\t\t{start_frame}", f"\t\t{end_frame}", "\t\t1",
             f"\t\t\t{label} {start_frame} {end_frame} {len(phonemes)}"]
    lines += [f"\t\t\t\t{frame} {name}" for frame, name in phonemes]
    _write_lines(path, lines)
