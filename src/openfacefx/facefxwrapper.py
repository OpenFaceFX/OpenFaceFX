"""FaceFXWrapper.exe-compatible drop-in shim (issue #33).

A CLI-compatible stand-in for Nukem9's ``FaceFXWrapper.exe`` — the tool that
Bethesda-modding voice pipelines (xVASynth ``lip_fuz``, Mantella, Pantella) shell
out to in order to turn a voice ``.wav`` + dialogue text into a Skyrim ``.lip``.

Those consumers dispatch FaceFXWrapper purely on ARGUMENT COUNT and gate success
on exactly one thing: a ``.lip`` file existing at the output path. Exit code and
stdout are ignored (the real binary is a GUI-subsystem app whose ``printf`` output
a subprocess usually can't capture), and the temporary resampled ``.wav`` is
``os.remove``-d only if present. So this shim reproduces the positional CLI and
writes a real — EXPERIMENTAL, see issue #12 — Skyrim ``.lip`` through the
openfacefx pipeline, instead of driving Bethesda's Creation Kit code the way the
original does. It still returns 0/1 and prints the wrapper's messages for humans.

Verbatim CLI (from Nukem9/FaceFXWrapper ``FFXW32/FFXW32.cpp`` ``StartCommandLine``,
which switches on ``__argc`` where ``__argv[0]`` is the program name)::

    FaceFXWrapper Type Lang FonixDataPath WavPath ResampledWavPath LipPath Text  # 7 args, resample
    FaceFXWrapper Type Lang FonixDataPath ResampledWavPath LipPath Text          # 6 args, pre-resampled

The input WAV is positional index 3 in BOTH forms; the output ``.lip`` is index 5
(7-arg) or 4 (6-arg); the dialogue text is always last. ``Type`` is ``Skyrim`` or
``Fallout4`` (case-insensitive, like the original's ``_stricmp``). ``Lang``
(always ``USEnglish``) and ``FonixDataPath`` are accepted and ignored — we neither
read a Fonix dictionary nor resample, and we deliberately do NOT write
ResampledWavPath.

HONEST LIMITATIONS (see ``docs/facefxwrapper.md`` and issue #12):
  * Timing is naive: phonemes are spread over the WAV *duration* (the only signal
    this CLI exposes), NOT Fonix acoustic alignment — mouth timing is approximate.
  * The ``.lip`` payload is EXPERIMENTAL and unverified in-game (#12): the
    slot->morph map is a hypothesis, so shapes may be wrong until calibrated.
  * Fallout 4 is unsupported (its 43-target vocabulary is undocumented): a
    ``Fallout4`` request fails honestly (no ``.lip`` written) so the consumer
    falls back to its own placeholder, exactly as a real generation failure would.

The FUZ/xWMA path (``.fuz`` repacking) is out of scope for this default drop-in —
it needs an external xWMA encoder; see the follow-up note in the docs.
"""

from __future__ import annotations

import sys
from typing import List

# Verbatim usage text for a bad argument count, reproduced character-for-character
# from FFXW32.cpp's default switch case (leading blank lines, tab indent, and the
# two Windows-path examples included). Consumers ignore stdout; this is for humans
# who run the shim by hand.
_USAGE = (
    "\n\nUsage:\n"
    "\tFaceFXWrapper [Type] [Lang] [FonixDataPath] [WavPath] [ResampledWavPath] [LipPath] [Text]\n"
    "\tFaceFXWrapper [Type] [Lang] [FonixDataPath] [ResampledWavPath] [LipPath] [Text]\n"
    "\n"
    "Examples:\n"
    '\tFaceFXWrapper "Skyrim" "USEnglish" "C:\\FonixData.cdf" "C:\\input.wav" "C:\\input_resampled.wav" "C:\\output.lip" "Blah Blah Blah"\n'
    '\tFaceFXWrapper "Fallout4" "USEnglish" "C:\\FonixData.cdf" "C:\\input_resampled.wav" "C:\\output.lip" "Blah Blah Blah"\n'
    "\n"
)

# The two accepted layouts, keyed by user-arg count -> positional indices of
# (input WAV, output .lip, dialogue text) within argv. The input WAV is index 3 in
# both; only the .lip / text positions shift. Mirrors the argc==8 (7 user args,
# resample) and argc==7 (6 user args, no resample) cases in FFXW32.cpp, less the
# program name at argv[0].
_LAYOUTS = {
    7: (3, 5, 6),   # Type Lang Fonix WavPath ResampledWavPath LipPath Text
    6: (3, 4, 5),   # Type Lang Fonix ResampledWavPath LipPath Text
}

_KNOWN_TYPES = ("skyrim", "fallout4")


def run(argv: List[str]) -> int:
    """Run the FaceFXWrapper-compatible shim on ``argv`` — the arguments AFTER the
    program name (or the ``facefxwrapper`` subcommand token). Returns a process
    exit code: 0 on success, 1 on any failure. On success a byte-valid Skyrim
    ``.lip`` exists at the output path, which is the only signal real consumers
    check; ResampledWavPath is never written.
    """
    layout = _LAYOUTS.get(len(argv))
    if layout is None:
        # Wrong argument count: usage + failure. The real wrapper validates the
        # Type only inside the two valid-count cases, so an unknown Type with a
        # bad count still lands here (usage), not on the type error below.
        sys.stdout.write(_USAGE)
        return 1

    wav_idx, lip_idx, text_idx = layout
    game = argv[0].lower()
    if game not in _KNOWN_TYPES:
        # Original prints the raw (un-lowercased) argument.
        sys.stdout.write(f'Unknown generator type "{argv[0]}"\n')
        return 1

    in_wav, out_lip, text = argv[wav_idx], argv[lip_idx], argv[text_idx]

    # Fallout 4 is a KNOWN type to the real wrapper, but we can't write its .lip
    # honestly (undocumented 43-target vocabulary, #12). Fail exactly like a real
    # generation failure: print the wrapper's message, write nothing, and let the
    # consumer fall back to its placeholder .lip.
    if game == "fallout4":
        sys.stdout.write("LIP generation failed\n")
        return 1

    # Heavy pipeline imports are deferred to here (not module top) so the
    # bad-count / unknown-type / fallout4 paths stay import-light; kept outside the
    # try below so a genuine install/import error surfaces instead of being
    # swallowed as a generation failure.
    from .pipeline import naive_segments, wav_duration
    from .g2p import G2P
    from .export_lip import write_lip

    try:
        duration = wav_duration(in_wav)
        segments = naive_segments(text, duration, g2p=G2P())
        write_lip(segments, duration, out_lip, game="skyrim")
    except Exception:
        # Mirrors RunLipGeneration returning false for any reason (unreadable WAV,
        # entirely silent line, write error): one message, exit 1, no .lip left.
        sys.stdout.write("LIP generation failed\n")
        return 1
    return 0


def _console() -> int:
    """``facefxwrapper`` console-script entry point: dispatch on ``sys.argv``
    (dropping the program name) and return the process exit code."""
    return run(sys.argv[1:])


if __name__ == "__main__":
    sys.exit(_console())
