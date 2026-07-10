"""The README viseme gallery (docs/visemes/*.svg) is generated, not hand-drawn,
so it must never drift from tools/render_viseme_gallery.py — the same guarantee
the quickstart GIF gets from its .tape. These tests re-render in memory and
compare to the committed files, and check the files stay tiny and well-formed."""

from __future__ import annotations

import importlib.util
import pathlib
import xml.dom.minidom as minidom

import pytest

from openfacefx.visemes import VISEMES

ROOT = pathlib.Path(__file__).resolve().parents[1]
VISEME_DIR = ROOT / "docs" / "visemes"


def _gallery():
    spec = importlib.util.spec_from_file_location(
        "render_viseme_gallery", ROOT / "tools" / "render_viseme_gallery.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


gallery = _gallery()


def test_committed_svgs_cover_exactly_the_viseme_set():
    on_disk = {p.stem for p in VISEME_DIR.glob("*.svg")}
    assert on_disk == set(VISEMES), (
        "docs/visemes/*.svg is out of sync with VISEMES; "
        "re-run python tools/render_viseme_gallery.py")


@pytest.mark.parametrize("viseme", VISEMES)
def test_committed_svg_matches_fresh_render(viseme):
    committed = (VISEME_DIR / f"{viseme}.svg").read_text(encoding="utf-8")
    assert committed == gallery.build_svg(viseme), (
        f"docs/visemes/{viseme}.svg is stale; "
        "re-run python tools/render_viseme_gallery.py")


@pytest.mark.parametrize("viseme", VISEMES)
def test_svg_is_small_and_well_formed(viseme):
    path = VISEME_DIR / f"{viseme}.svg"
    data = path.read_bytes()
    assert len(data) < 3072, f"{path.name} should stay under 3KB, got {len(data)}"
    doc = minidom.parseString(data)          # raises if not well-formed XML
    assert doc.documentElement.tagName == "svg"
    # no scripts/styles/classes, so GitHub renders it inline via <img>
    text = data.decode("utf-8")
    assert "<script" not in text and "<style" not in text
    assert "#f4b942" in text                 # the amber lip stroke is present
