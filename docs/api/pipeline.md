# Pipeline & generation

The end-to-end orchestration plus the input stages that feed it: the naive
aligner and Montreal Forced Aligner parser, grapheme-to-phoneme conversion, the
ARPAbet phoneme inventory, the transcript-free energy fallback, the prosody
tracker that turns pitch and loudness into typed events, and the
edit-preservation layer that carries hand-tweaks across a regeneration.

::: openfacefx.pipeline

::: openfacefx.edits

::: openfacefx.alignment

::: openfacefx.g2p

::: openfacefx.phonemes

::: openfacefx.energy

::: openfacefx.prosody
