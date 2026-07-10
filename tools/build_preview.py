"""Build a self-contained HTML previewer with a track embedded inline.

Usage: python tools/build_preview.py path/to/track.json out.html
Browsers block file:// fetch, so the track is baked into the page.
"""
import json
import sys

TEMPLATE = open(__file__.replace("build_preview.py", "preview_template.html")).read()


def main(track_path: str, out_path: str) -> None:
    track = json.load(open(track_path))
    html = TEMPLATE.replace("/*__TRACK__*/null", json.dumps(track))
    open(out_path, "w", encoding="utf-8").write(html)
    print("wrote", out_path)


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
