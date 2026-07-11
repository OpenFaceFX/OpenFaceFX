#!/usr/bin/env python3
"""Render docs/talking-head.svg — a self-contained animated lip-sync demo.

Generates a track with the real pipeline, picks the dominant viseme per frame,
and stacks the project's viseme mouth SVGs (docs/visemes/*.svg) into one SMIL
flip-book. SMIL animates in an <img> context, so the file plays in the GitHub
README and the docs site with no GIF encoder, no JS, and no external assets.
Recorded as code so it can't drift from the real pipeline. Run from the repo
root:  python tools/build_talking_head.py
"""
from __future__ import annotations

import re
from pathlib import Path

import numpy as np

from openfacefx.pipeline import generate_naive
from openfacefx.io_export import to_dict
from openfacefx.visemes import VISEMES

PHRASE = "open source lip sync from audio + text"
CAPTION = "“open source lip sync from audio + text”"
DURATION = 3.2
FPS = 15
OUT = Path("docs/talking-head.svg")
VISEME_DIR = Path("docs/visemes")


def _channels(track):
    return {c.name: c for c in track.channels}


def _sample(ch, t: float) -> float:
    if ch is None or not ch.keys:
        return 0.0
    ts = np.array([k.time for k in ch.keys])
    vs = np.array([k.value for k in ch.keys])
    return float(np.interp(t, ts, vs))


def _dominant_frames(track, n_frames: int):
    chans = _channels(track)
    frames = []
    for i in range(n_frames):
        t = i / FPS
        best, best_v = "sil", 0.0
        for v in VISEMES:
            val = _sample(chans.get(v), t)
            if val > best_v:
                best_v, best = val, v
        frames.append(best if best_v >= 0.12 else "sil")
    return frames


def _mouth_paths(v: str) -> str:
    # the viseme SVGs are a background <rect> plus three mouth <path>s; keep the paths.
    svg = (VISEME_DIR / f"{v}.svg").read_text()
    return "\n      ".join(re.findall(r"<path [^>]*/>", svg))


def build() -> str:
    out = generate_naive(PHRASE, DURATION)
    track = out[0] if isinstance(out, tuple) else out
    n = max(2, round(DURATION * FPS))
    frames = _dominant_frames(track, n)
    key_times = ";".join(f"{i / n:.4f}" for i in range(n)) + ";1"
    loop = f"{DURATION:.2f}s"

    def animate(values):
        vals = ";".join(values) + f";{values[0]}"
        return (f'<animate attributeName="opacity" calcMode="discrete" dur="{loop}" '
                f'repeatCount="indefinite" keyTimes="{key_times}" values="{vals}"/>')

    scale, cx, cy = 1.15, 320, 232
    tx, ty = cx - 150 * scale, cy - 125 * scale
    layers = []
    for v in VISEMES:
        if v not in frames:                              # skip never-shown visemes
            continue
        vals = ["1" if frames[i] == v else "0" for i in range(n)]
        layers.append(f'    <g opacity="0">\n      {_mouth_paths(v)}\n      {animate(vals)}\n    </g>')
    mouth = "\n".join(layers)

    blink = (f'<animate attributeName="ry" dur="{loop}" repeatCount="indefinite" '
             f'keyTimes="0;0.90;0.93;0.96;1" values="9;9;1;9;9" calcMode="linear"/>')
    sweep = (f'<animate attributeName="width" dur="{loop}" repeatCount="indefinite" '
             f'keyTimes="0;1" values="0;404" calcMode="linear"/>')

    return f'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 640 400" width="640" height="400" role="img" aria-label="OpenFaceFX talking-head lip-sync demo">
  <defs>
    <linearGradient id="bg" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0" stop-color="#0f1420"/><stop offset="1" stop-color="#0a0d14"/>
    </linearGradient>
    <radialGradient id="face" cx="0.5" cy="0.42" r="0.7">
      <stop offset="0" stop-color="#1b2333"/><stop offset="1" stop-color="#111725"/>
    </radialGradient>
  </defs>
  <rect x="0" y="0" width="640" height="400" rx="18" fill="url(#bg)"/>
  <rect x="1" y="1" width="638" height="398" rx="17" fill="none" stroke="#1e2635" stroke-width="1.5"/>
  <ellipse cx="320" cy="185" rx="150" ry="150" fill="url(#face)" stroke="#2a3448" stroke-width="2"/>
  <path d="M 252 128 Q 270 122 288 128" fill="none" stroke="#3a4458" stroke-width="4" stroke-linecap="round" opacity="0.55"/>
  <path d="M 352 128 Q 370 122 388 128" fill="none" stroke="#3a4458" stroke-width="4" stroke-linecap="round" opacity="0.55"/>
  <g fill="#f4b942">
    <ellipse cx="270" cy="150" rx="13" ry="9">{blink}</ellipse>
    <ellipse cx="370" cy="150" rx="13" ry="9">{blink}</ellipse>
  </g>
  <circle cx="270" cy="150" r="3.5" fill="#0a0d14"/>
  <circle cx="370" cy="150" r="3.5" fill="#0a0d14"/>
  <g transform="translate({tx:.1f},{ty:.1f}) scale({scale})">
{mouth}
  </g>
  <text x="320" y="360" text-anchor="middle" font-family="ui-sans-serif,Segoe UI,Helvetica,Arial,sans-serif" font-size="19" fill="#e6edf6" font-weight="600">{CAPTION}</text>
  <text x="320" y="384" text-anchor="middle" font-family="ui-monospace,SFMono-Regular,Menlo,monospace" font-size="12" fill="#7d8aa0">OpenFaceFX &#183; audio + text &#8594; viseme curves &#183; openfacefx.com</text>
  <rect x="118" y="372" width="404" height="2" rx="1" fill="#1e2635"/>
  <rect x="118" y="372" width="0" height="2" rx="1" fill="#f4b942">{sweep}</rect>
</svg>
'''


if __name__ == "__main__":
    OUT.write_text(build())
    print(f"wrote {OUT}")
