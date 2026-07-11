# JALI coarticulation rules

[JALI](https://www.dgp.toronto.edu/~elf/JALISIG16.pdf) (Edwards et al., SIGGRAPH
2016) publishes a concrete rule set and measured onset/decay constants that
extend exactly the machinery OpenFaceFX already ships (Cohen–Massaro blending,
per-class timing, bilabial closure). Issue #19 adopts them as a **data-driven
rule table** ([`data/jali_rules.json`][openfacefx.coart_jali.load_rules]) over the
component stage.

**It is entirely opt-in.** With the default (JALI off)
`build_viseme_curves` is **byte-identical** to before — the whole existing suite
stays green and a diff against the released wheel is identical. Turn it on, and
select rules, through `CoartParams`:

```python
from openfacefx import CoartParams, build_viseme_curves, JALI_RULE_IDS

# all JALI rules + empirical timing
params = CoartParams(jali=True)
# or just a couple, individually toggled (JALI_RULE_IDS lists them all)
params = CoartParams(jali=True, jali_rules=("sibilant_jaw", "lip_heavy"))
times, matrix = build_viseme_curves(segments, mapping=mapping, params=params)
```

## What it adds (the prioritised set)

- **Hard constraints** (post-blend forcings over articulator classes, each
  toggleable): `bilabial_close` (lips seal), `labiodental_teeth` (bottom lip to
  teeth), `sibilant_jaw` (sibilants narrow the jaw), `nonnasal_lip_open` (a
  non-nasal open segment opens the lips, so a neighbour's closure can't bleed
  across). When JALI is on these **replace** the legacy lips-only closure pass.
- **Habits**: `duplicated_merge` collapses adjacent same-viseme segments across a
  word boundary into one hold ("po_p m_an"); `lip_heavy` gives the rounded/
  protruded visemes (UW OW OY w S Z J C) an earlier onset and longer hold;
  `tongue_no_lip` guarantees a tongue articulation (l n t d g k ng) never pulls a
  lip channel.
- **Empirical timing** (`jali_timing`, on by default when JALI is on): a
  per-phoneme, context-dependent onset/decay lookup — onset ~120 ms before the
  apex, tighter after a vowel (~60 ms) than after a pause (~120 ms), with a
  ~150 ms lip-protrusion extension — replacing the per-class `lead` constants.

## Data, not code

The categories (phoneme sets), constraint floors/caps and timing constants live
in `data/jali_rules.json` so new measurements drop in without touching code. The
tongue articulator **class** was already in the mapping schema, so no schema
version bump was needed.

## Deferred (flagged for a follow-up)

Two lower-value habits — short-obstruent / nasal "leave-the-jaw-untouched" and
word-final anticipatory lip shape — and the NVIDIA-A2F-style tongue-channel
**gain/offset** mapping fields (with the ARKit tongue targets) are **not** in this
pass. Adding the gain/offset fields *would* bump the mapping schema version; it is
left out here so the schema and shipped ARKit preset stay byte-identical.

::: openfacefx.coart_jali
