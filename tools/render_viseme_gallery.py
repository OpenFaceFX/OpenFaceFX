"""Render one small mouth-shape SVG per viseme for the README gallery.

The mouth geometry is the *same* schematic articulator the HTML previewer draws
(`tools/preview_template.html`, function ``drawFace``): a pair of quadratic-Bezier
lips whose width/openness/rounding are driven by the viseme weights, plus an
optional teeth bar and tongue tip. Here each viseme is rendered as a *static*
pose — that viseme at full weight (1.0), every other channel at 0 — so the table
in the README shows what each blendshape looks like on its own.

No new dependencies: this emits plain SVG with presentation attributes only (no
``<style>``/classes/scripts), so GitHub renders the files inline via ``<img>``.

Usage:
    python tools/render_viseme_gallery.py                 # -> docs/visemes/*.svg
    python tools/render_viseme_gallery.py --out some/dir  # custom directory
    python tools/render_viseme_gallery.py --table         # also print MD table

Output is deterministic (fixed 1-decimal coordinate formatting), so the committed
SVGs can be regression-checked against a fresh render (see tests).
"""

from __future__ import annotations

import argparse
import os

from openfacefx.phonemes import SILENCE
from openfacefx.visemes import VISEMES, PHONEME_TO_VISEME

# --- palette (matches the previewer's articulator scope) --------------------
INK = "#0d1119"      # tile background
LINE = "#1e2635"     # tile border
CAVITY = "#05070b"   # mouth interior
TEETH = "#c9d1d9"
TONGUE = "#8a4a52"
LIP = "#f4b942"      # amber lip stroke

# --- geometry (ported verbatim from preview_template.html drawFace) ---------
CX, CY = 150.0, 120.0
# A tight crop around the mouth; every static pose stays inside it with margin
# (widest lips reach x 84..216, most-open jaw reaches y ~148, top of lip ~112).
VIEWBOX = (72, 84, 156, 82)

# Short human descriptions for the README table (the phoneme lists are derived
# from the real PHONEME_TO_VISEME map below, not hand-copied).
DESCRIPTIONS = {
    "sil": "neutral / mouth at rest",
    "PP": "lips pressed shut",
    "FF": "lower lip to upper teeth",
    "TH": "tongue between the teeth",
    "DD": "tongue to the alveolar ridge",
    "kk": "back of tongue raised",
    "CH": "rounded, protruded",
    "SS": "narrow, teeth close",
    "nn": "nasal, tongue up",
    "RR": "retroflex / lightly rounded",
    "aa": "open jaw",
    "E": "mid-front spread",
    "I": "wide spread",
    "O": "rounded and open",
    "U": "tight lip rounding",
}


def _clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def _f(x: float) -> str:
    """Deterministic 1-decimal formatting (avoids -0.0 and platform drift)."""
    x = round(x, 1)
    if x == 0:
        x = 0.0
    return f"{x:.1f}"


def build_svg(viseme: str) -> str:
    """SVG string for one viseme rendered at full weight."""
    w = {name: (1.0 if name == viseme else 0.0) for name in VISEMES}
    aa, O, E, I, U = w["aa"], w["O"], w["E"], w["I"], w["U"]
    RR, PP, FF, SS, DD = w["RR"], w["PP"], w["FF"], w["SS"], w["DD"]
    TH, CH, kk, sil, nn = w["TH"], w["CH"], w["kk"], w["sil"], w["nn"]

    open_ = _clamp(aa * 1.0 + O * 0.82 + E * 0.42 + I * 0.22
                   + RR * 0.32 + U * 0.28 + kk * 0.3, 0, 1)
    press = _clamp(PP * 1.0 + sil * 0.5 + nn * 0.25 + FF * 0.35, 0, 1)
    open_ *= (1 - press * 0.85)
    spread = _clamp(I * 1.0 + E * 0.55 + aa * 0.3 + SS * 0.4
                    - U * 0.95 - O * 0.75, -1, 1)
    round_ = _clamp(U * 1.0 + O * 0.85 + CH * 0.55, 0, 1)

    half_w = 46 + spread * 20 - round_ * 16
    jaw = 4 + open_ * 44
    up_ctrl_y = CY - 8 - round_ * 3 + press * 2
    lo_ctrl_y = CY + jaw + 8
    lx, rx = CX - half_w, CX + half_w

    upper = f"M {_f(lx)} {_f(CY)} Q {_f(CX)} {_f(up_ctrl_y)} {_f(rx)} {_f(CY)}"
    lower = f"M {_f(lx)} {_f(CY)} Q {_f(CX)} {_f(lo_ctrl_y)} {_f(rx)} {_f(CY)}"
    fill = (f"{upper} L {_f(rx)} {_f(CY)} "
            f"Q {_f(CX)} {_f(lo_ctrl_y)} {_f(lx)} {_f(CY)} Z")

    # teeth bar (FF/SS/TH/DD) and tongue tip (DD/TH), same formulas as drawFace.
    teeth = _clamp(FF * 1.0 + SS * 0.7 + TH * 0.6 + DD * 0.4, 0, 1)
    teeth_op = teeth * min(open_ + 0.25, 1)
    tng = _clamp(DD * 0.8 + TH * 0.7, 0, 1)
    tng_op = tng * open_

    vb = " ".join(str(v) for v in VIEWBOX)
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="{vb}" '
        f'width="{VIEWBOX[2]}" height="{VIEWBOX[3]}" role="img" '
        f'aria-label="{viseme} viseme mouth shape">',
        f'<rect x="{VIEWBOX[0]}" y="{VIEWBOX[1]}" width="{VIEWBOX[2]}" '
        f'height="{VIEWBOX[3]}" rx="8" fill="{INK}" stroke="{LINE}" '
        f'stroke-width="1.5"/>',
        f'<path d="{fill}" fill="{CAVITY}"/>',
    ]
    # teeth/tongue painted on top of the cavity so the dental/lingual cues show.
    if teeth > 0.02:
        parts.append(
            f'<rect x="{_f(lx + 6)}" y="{_f(CY - 4)}" '
            f'width="{_f((half_w - 6) * 2)}" height="6" rx="1" '
            f'fill="{TEETH}" opacity="{teeth_op:.2f}"/>')
    if tng > 0.05:
        parts.append(
            f'<ellipse cx="{_f(CX)}" cy="{_f(CY + 4)}" rx="18" '
            f'ry="{_f(5 + open_ * 4)}" fill="{TONGUE}" '
            f'opacity="{tng_op:.2f}"/>')
    parts.append(
        f'<path d="{upper}" fill="none" stroke="{LIP}" stroke-width="2.5" '
        f'stroke-linecap="round"/>')
    parts.append(
        f'<path d="{lower}" fill="none" stroke="{LIP}" stroke-width="2.5" '
        f'stroke-linecap="round"/>')
    parts.append("</svg>")
    return "\n".join(parts) + "\n"


def _phonemes_by_viseme():
    """Invert PHONEME_TO_VISEME so the table's example phonemes are grounded in
    the real mapping rather than hand-copied comments."""
    out = {v: [] for v in VISEMES}
    for phon, vis in PHONEME_TO_VISEME.items():
        if phon == SILENCE:           # the silence sentinel isn't a real phoneme
            continue
        out.setdefault(vis, []).append(phon)
    for v in out:
        out[v].sort()
    return out


def markdown_table(rel_dir: str = "docs/visemes") -> str:
    phons = _phonemes_by_viseme()
    rows = ["| Viseme | Shape | Phonemes | Mouth |",
            "|:------:|:-----:|:----------|:------|"]
    for v in VISEMES:
        img = (f'<img src="{rel_dir}/{v}.svg" width="72" '
               f'alt="{v} mouth shape">')
        ex = ", ".join(f"`{p}`" for p in phons.get(v, [])) or "—"
        rows.append(f"| **{v}** | {img} | {ex} | {DESCRIPTIONS[v]} |")
    return "\n".join(rows) + "\n"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", default="docs/visemes",
                    help="output directory (default: docs/visemes)")
    ap.add_argument("--table", action="store_true",
                    help="also print the README markdown table to stdout")
    args = ap.parse_args()

    os.makedirs(args.out, exist_ok=True)
    for v in VISEMES:
        path = os.path.join(args.out, f"{v}.svg")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(build_svg(v))
    print(f"wrote {len(VISEMES)} viseme SVGs to {args.out}/")
    if args.table:
        print()
        print(markdown_table())


if __name__ == "__main__":
    main()
