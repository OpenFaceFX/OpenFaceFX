"""OpenFaceFX Studio — the local server behind ``openfacefx studio``.

Serves the bundled single-page web studio at ``http://127.0.0.1:PORT`` plus a
small JSON API backed by the **native** openfacefx pipeline (faster than the
in-browser Pyodide path, and fully offline). The same SPA runs three ways:

  * **Web / SaaS** — host ``studio_web/`` statically; the pipeline runs
    client-side via Pyodide (this server is optional).
  * **Standalone PC** — ``openfacefx studio`` (this module): native pipeline, no
    download, opens your browser. Wrappable in Tauri/Electron for a desktop app.
  * **SaaS backend** — the same endpoints behind auth + storage; the
    ``/api/llm`` relay is a **stateless** pass-through so browser-blocked
    providers (OpenAI/Gemini) work with the user's own key without a cloud
    service ever storing it.

stdlib only — no extra dependencies, so it ships in the numpy-only wheel.
"""

from __future__ import annotations

import base64
import json
import tempfile
import os
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import resources
from urllib import request as _urlrequest
from urllib.error import HTTPError, URLError

from . import __version__

# --- SaaS backend (accounts / projects / vault) — lazily opened on first use - #
_COOKIE = "offx_sess"
_SESSION_TTL = 30 * 24 * 3600
_STORE = None


def _store():
    """Open the SQLite-backed SaaS store on first use (accounts/projects/vault)."""
    global _STORE
    if _STORE is None:
        from .studio_saas import Store
        _STORE = Store()
    return _STORE

_CTYPE = {".html": "text/html; charset=utf-8", ".css": "text/css",
          ".js": "text/javascript", ".json": "application/json",
          ".svg": "image/svg+xml", ".png": "image/png",
          ".glb": "model/gltf-binary", ".wasm": "application/wasm"}


# --------------------------------------------------------------------------- #
# Native pipeline (mirrors the in-browser Pyodide bridge)                      #
# --------------------------------------------------------------------------- #
def _generate(p: dict) -> dict:
    import openfacefx as offx
    from openfacefx import (naive_segments, generate_from_alignment, generate_naive,
                            GestureParams, add_gestures_to_track, to_dict)
    from openfacefx.alignment import dump_segments
    text = p.get("text", "hello"); dur = float(p.get("dur", 4) or 4)
    fps = float(p.get("fps", 60) or 60); engine = p.get("engine", "naive")
    wav_path = None
    if p.get("wav_b64"):
        fd, wav_path = tempfile.mkstemp(suffix=".wav"); os.close(fd)
        with open(wav_path, "wb") as f:
            f.write(base64.b64decode(p["wav_b64"]))
        try: dur = offx.wav_duration(wav_path)
        except Exception: pass
    try:
        segs = naive_segments(text, dur)
        if wav_path and engine == "energy":
            try: track = generate_naive(text, dur, wav=wav_path, fps=fps)
            except TypeError: track = generate_naive(text, dur, wav=wav_path)
        else:
            track = generate_from_alignment(segs, fps=fps)
        if p.get("gestures") or p.get("breath"):
            gp = GestureParams(seed=1, breath_enable=bool(p.get("breath")))
            if p.get("breath") and not p.get("gestures"):
                for a in ("blink_enable", "brow_enable", "gaze_enable",
                          "head_ambient", "head_nod_on_stress"):
                    if hasattr(gp, a): setattr(gp, a, False)
            track = add_gestures_to_track(track, track.duration, params=gp)
        return {"track": to_dict(track), "segments": dump_segments(segs),
                "duration": round(track.duration, 4), "fps": track.fps}
    finally:
        if wav_path and os.path.exists(wav_path): os.remove(wav_path)


def _export(fmt: str, track: dict) -> dict:
    import openfacefx as offx
    from openfacefx import from_dict, retarget, PRESETS
    tk = from_dict(track)
    tmp = tempfile.mkdtemp(); p = os.path.join(tmp, "out")

    def dump(fn, path, name):
        fn();
        with open(path, "rb") as f: data = f.read()
        return {"filename": name, "b64": base64.b64encode(data).decode()}

    def ark():
        try: return retarget(tk, PRESETS["arkit"])
        except Exception: return tk
    try:
        if fmt == "json":
            return {"filename": "take.track.json",
                    "b64": base64.b64encode(json.dumps(track, indent=2).encode()).decode()}
        table = {
            "csv":     (offx.write_csv, tk, ".csv", "take.csv"),
            "glb":     (offx.write_gltf, tk, ".glb", "take.glb"),
            "vrma":    (offx.write_vrma, tk, ".vrma", "take.vrma"),
            "spine":   (offx.write_spine, tk, ".spine.json", "take.spine.json"),
            "live2d":  (offx.write_live2d_motion, tk, ".motion3.json", "take.motion3.json"),
            "exp3":    (offx.write_live2d_expression, tk, ".exp3.json", "pose.exp3.json"),
            "unity":   (offx.write_unity_anim, tk, ".anim", "take.anim"),
            "godot":   (offx.write_godot_anim, tk, ".tres", "take.tres"),
            "vmd":     (offx.write_vmd, tk, ".vmd", "take.vmd"),
            "livelink":(offx.write_livelink_csv, ark(), ".livelink.csv", "take.livelink.csv"),
            "a2f":     (offx.write_a2f, ark(), ".a2f.json", "take.a2f.json"),
            "rhubarb": (offx.write_rhubarb_tsv, tk, ".tsv", "take.tsv"),
            "moho":    (offx.write_moho_dat, tk, ".dat", "take.dat"),
        }
        if fmt not in table:
            return {"error": "unknown format " + fmt}
        writer, obj, ext, name = table[fmt]
        return dump(lambda: writer(obj, p + ext), p + ext, name)
    except Exception as e:
        return {"error": str(e)}


def _presets() -> list:
    from openfacefx import PRESETS
    return sorted(PRESETS)


def _preset(name: str) -> dict:
    from openfacefx import PRESETS
    m = PRESETS.get(name, {})
    return {v: [[t, round(float(w), 3)] for (t, w) in tgts] for v, tgts in m.items()}


def _llm(p: dict) -> dict:
    """Stateless relay for browser-CORS-blocked providers (OpenAI / Gemini).
    Forwards the caller's own key on this one request and stores NOTHING."""
    url = p.get("url"); key = p.get("key", "")
    if not url:
        return {"error": "no provider url"}
    body = {"model": p.get("model"),
            "messages": [m for m in (
                {"role": "system", "content": p["system"]} if p.get("system") else None,
                {"role": "user", "content": p.get("user", "")}) if m]}
    if p.get("json"):
        body["response_format"] = {"type": "json_object"}
    req = _urlrequest.Request(url, data=json.dumps(body).encode(),
                              headers={"content-type": "application/json",
                                       **({"authorization": "Bearer " + key} if key else {})},
                              method="POST")
    try:
        with _urlrequest.urlopen(req, timeout=60) as r:
            j = json.loads(r.read().decode())
        return {"text": (j.get("choices") or [{}])[0].get("message", {}).get("content", "")}
    except HTTPError as e:
        try: msg = json.loads(e.read().decode()).get("error", {}).get("message", str(e))
        except Exception: msg = f"{e.code} {e.reason}"
        return {"error": msg}
    except (URLError, Exception) as e:
        return {"error": str(e)}


# --------------------------------------------------------------------------- #
# HTTP handler                                                                 #
# --------------------------------------------------------------------------- #
class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):  # quiet by default
        if os.environ.get("OFFX_STUDIO_VERBOSE"): super().log_message(*a)

    def _send(self, code, body: bytes, ctype="application/json", extra=None):
        self.send_response(code); self.send_header("content-type", ctype)
        self.send_header("content-length", str(len(body)))
        self.send_header("cache-control", "no-store")
        for k, v in (extra or []):
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD": self.wfile.write(body)

    def _json(self, obj, code=200, extra=None):
        self._send(code, json.dumps(obj).encode(), "application/json", extra)

    def _body(self):
        n = int(self.headers.get("content-length", 0) or 0)
        return json.loads(self.rfile.read(n) or b"{}")

    # -- session cookie helpers ----------------------------------------- #
    def _token(self):
        raw = self.headers.get("Cookie")
        if not raw:
            return ""
        try:
            m = SimpleCookie(raw).get(_COOKIE)
            return m.value if m else ""
        except Exception:
            return ""

    def _set_cookie(self, token, ttl):
        attrs = f"{_COOKIE}={token}; HttpOnly; SameSite=Lax; Path=/; Max-Age={ttl}"
        if os.environ.get("OFFX_STUDIO_SECURE_COOKIE"):
            attrs += "; Secure"
        return [("Set-Cookie", attrs)]

    def _user(self):
        return _store().user_for(self._token())

    def _asset(self, name):
        name = name.lstrip("/") or "index.html"
        parts = name.split("/")
        # allow the flat web root plus a single "assets/" subdir; nothing else
        if ".." in name or len(parts) > 2 or (len(parts) == 2 and parts[0] != "assets"):
            return self._send(404, b"not found", "text/plain")
        ref = resources.files("openfacefx") / "studio_web"
        for p in parts:
            ref = ref / p
        try:
            data = ref.read_bytes()
        except (FileNotFoundError, ModuleNotFoundError, OSError, IsADirectoryError):
            return self._send(404, b"not found", "text/plain")
        ext = os.path.splitext(name)[1]
        self._send(200, data, _CTYPE.get(ext, "application/octet-stream"))

    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if path == "/api/health":
            return self._json({"ok": True, "version": __version__,
                               "runtime": "native", "saas": True})
        if path == "/api/presets":
            return self._json(_presets())
        if path.startswith("/api/preset/"):
            return self._json(_preset(path.rsplit("/", 1)[-1]))
        if path == "/api/auth/me":
            return self._json({"user": self._user()})
        if path == "/api/projects":
            u = self._user()
            if not u: return self._json({"error": "sign in required"}, 401)
            return self._json({"projects": _store().list_projects(u["id"])})
        if path.startswith("/api/projects/"):
            u = self._user()
            if not u: return self._json({"error": "sign in required"}, 401)
            try: pid = int(path.rsplit("/", 1)[-1])
            except ValueError: return self._json({"error": "bad id"}, 400)
            proj = _store().get_project(u["id"], pid)
            return self._json(proj) if proj else self._json({"error": "not found"}, 404)
        if path == "/api/vault":
            u = self._user()
            if not u: return self._json({"error": "sign in required"}, 401)
            return self._json(_store().get_vault(u["id"]) or {"data": None})
        return self._asset(path if path != "/" else "index.html")

    do_HEAD = do_GET

    def do_POST(self):
        path = self.path.split("?", 1)[0]
        try:
            body = self._body()
        except Exception as e:
            return self._json({"error": "bad request: " + str(e)}, 400)
        try:
            if path == "/api/generate":
                return self._json(_generate(body))
            if path.startswith("/api/export/"):
                return self._json(_export(path.rsplit("/", 1)[-1], body.get("track", {})))
            if path == "/api/llm":
                return self._json(_llm(body))
            if path in ("/api/auth/register", "/api/auth/login"):
                from .studio_saas import AuthError
                fn = _store().register if path.endswith("register") else _store().login
                try:
                    res = fn(body.get("email", ""), body.get("password", ""))
                except AuthError as e:
                    return self._json({"error": str(e)}, 400)
                return self._json({"user": res["user"]}, 200,
                                  self._set_cookie(res["token"], _SESSION_TTL))
            if path == "/api/auth/logout":
                _store().logout(self._token())
                return self._json({"ok": True}, 200, self._set_cookie("", 0))
            if path == "/api/projects":
                u = self._user()
                if not u: return self._json({"error": "sign in required"}, 401)
                from .studio_saas import AuthError
                try:
                    return self._json(_store().save_project(
                        u["id"], body.get("id"), body.get("name", "Untitled"),
                        body.get("data", {})))
                except AuthError as e:
                    return self._json({"error": str(e)}, 400)
            if path == "/api/vault":
                u = self._user()
                if not u: return self._json({"error": "sign in required"}, 401)
                from .studio_saas import AuthError
                try:
                    return self._json(_store().set_vault(u["id"], body.get("data")))
                except AuthError as e:
                    return self._json({"error": str(e)}, 400)
        except Exception as e:
            return self._json({"error": str(e)}, 500)
        return self._json({"error": "not found"}, 404)

    def do_DELETE(self):
        path = self.path.split("?", 1)[0]
        if path.startswith("/api/projects/"):
            u = self._user()
            if not u: return self._json({"error": "sign in required"}, 401)
            try: pid = int(path.rsplit("/", 1)[-1])
            except ValueError: return self._json({"error": "bad id"}, 400)
            return self._json({"ok": _store().delete_project(u["id"], pid)})
        return self._json({"error": "not found"}, 404)


def serve(port: int = 8765, host: str = "127.0.0.1", open_browser: bool = True) -> int:
    """Run the studio server until Ctrl-C. Returns a process exit code."""
    httpd = ThreadingHTTPServer((host, port), _Handler)
    url = f"http://{host}:{port}/"
    print(f"OpenFaceFX Studio {__version__} — serving at {url}")
    print("  native pipeline · press Ctrl-C to stop")
    if open_browser:
        try:
            import webbrowser, threading
            threading.Timer(0.6, lambda: webbrowser.open(url)).start()
        except Exception:
            pass
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        httpd.server_close()
    return 0
