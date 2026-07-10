# OpenFaceFX quickstart, recorded as code with VHS (https://github.com/charmbracelet/vhs).
#
# This tape is the single source of truth for the README's quickstart GIF: CI
# re-renders it on every push (.github/workflows/pages.yml) and publishes the
# result to the GitHub Pages site, so the GIF can never drift from the real CLI.
#
# Render it yourself with:  vhs docs/quickstart.tape
# Prerequisite (CI runs this first):  pip install -e .

Output docs/quickstart.gif

Require python

Set Shell "bash"
Set Theme "Catppuccin Frappe"
Set FontSize 20
Set Width 1180
Set Height 720
Set Padding 22
Set TypingSpeed 45ms

Sleep 700ms

# One command: transcript + a WAV's duration -> viseme animation curves. No
# models, no downloads (the naive aligner spaces phonemes by duration priors).
Type "python -m openfacefx naive --text 'hello world' --wav examples/voice.wav -o track.json"
Sleep 500ms
Enter
Sleep 2.5s

# Peek at the track: the format header and the 15 Oculus/Meta viseme channels.
Type "head -22 track.json"
Sleep 500ms
Enter
Sleep 5s
