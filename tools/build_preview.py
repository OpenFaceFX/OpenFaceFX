"""Build a self-contained HTML previewer with a track embedded inline.

Usage: python tools/build_preview.py path/to/track.json out.html [--autoplay]
Browsers block file:// fetch, so the track is baked into the page.
``--autoplay`` starts playback (looping) on load — used by the hosted demo.
"""
import json
import sys

TEMPLATE = open(__file__.replace("build_preview.py", "preview_template.html"),
                encoding="utf-8").read()


def main(track_path: str, out_path: str, autoplay: bool = False) -> None:
    track = json.load(open(track_path, encoding="utf-8"))
    html = TEMPLATE.replace("/*__TRACK__*/null", json.dumps(track))
    if autoplay:
        html = html.replace("render(0);", "render(0);btn.click();")
    open(out_path, "w", encoding="utf-8").write(html)
    print("wrote", out_path)


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if a != "--autoplay"]
    main(args[0], args[1], autoplay="--autoplay" in sys.argv)
