"""OpenFaceFX Studio — the SaaS / multi-user backend.

This is the "whole SaaS aspect": real accounts, per-user project storage, and
encrypted-vault sync — the pieces that turn the single-page studio into a hosted,
multi-tenant service. It is **stdlib only** (``sqlite3``, ``hashlib``,
``secrets``) so it ships in the numpy-only wheel and runs anywhere Python does:

  * ``openfacefx studio`` — a single-tenant server on your PC (accounts live in a
    local SQLite file; sign in to save projects across sessions).
  * the Docker image / a hosted deploy — the same code behind TLS is a genuine
    multi-tenant SaaS: each user's projects and (ciphertext-only) key vault are
    isolated by session.

Zero-knowledge stays intact: the vault blob is encrypted in the browser and this
server only ever stores/returns the ciphertext (see ``assistant.js``). Passwords
are salted + PBKDF2-SHA256 hashed (never stored in the clear); sessions are
random opaque tokens carried in an httpOnly cookie.

The store is a small class so tests (and future backends) can point it at any
path; ``studio.py`` owns one instance and maps HTTP to it.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import secrets
import sqlite3
import time

_PBKDF2_ROUNDS = 200_000
_SESSION_TTL = 30 * 24 * 3600            # 30 days
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_MAX_BLOB = 8 * 1024 * 1024              # 8 MB cap on any stored blob


class AuthError(ValueError):
    """A user-facing validation / auth failure (maps to HTTP 400/401)."""


def default_db_path() -> str:
    """Where the SQLite file lives; override with ``OFFX_STUDIO_DB``."""
    env = os.environ.get("OFFX_STUDIO_DB")
    if env:
        return env
    root = os.environ.get("OFFX_STUDIO_DATA") or os.path.join(
        os.path.expanduser("~"), ".openfacefx")
    return os.path.join(root, "studio.db")


def _now() -> float:
    return time.time()


class Store:
    """SQLite-backed users / sessions / projects / vaults."""

    def __init__(self, path: str | None = None):
        self.path = path or default_db_path()
        if self.path != ":memory:":
            os.makedirs(os.path.dirname(os.path.abspath(self.path)), exist_ok=True)
        # one shared connection (ThreadingHTTPServer → many threads); serialize
        # with check_same_thread=False + SQLite's own locking. Low volume.
        self._db = sqlite3.connect(self.path, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA journal_mode=WAL")
        self._init()

    def _init(self) -> None:
        c = self._db
        c.executescript(
            """
            CREATE TABLE IF NOT EXISTS users(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email TEXT UNIQUE NOT NULL,
                salt TEXT NOT NULL, pw_hash TEXT NOT NULL,
                created REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS sessions(
                token TEXT PRIMARY KEY, user_id INTEGER NOT NULL,
                created REAL NOT NULL, expires REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS projects(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL, name TEXT NOT NULL,
                data TEXT NOT NULL, created REAL NOT NULL, updated REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS vaults(
                user_id INTEGER PRIMARY KEY, data TEXT NOT NULL, updated REAL NOT NULL);
            """)
        c.commit()

    # -- passwords ------------------------------------------------------- #
    @staticmethod
    def _hash(password: str, salt: bytes) -> str:
        return hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"), salt, _PBKDF2_ROUNDS).hex()

    # -- accounts -------------------------------------------------------- #
    def register(self, email: str, password: str) -> dict:
        email = (email or "").strip().lower()
        if not _EMAIL_RE.match(email):
            raise AuthError("Enter a valid email address.")
        if not password or len(password) < 8:
            raise AuthError("Password must be at least 8 characters.")
        salt = os.urandom(16)
        try:
            cur = self._db.execute(
                "INSERT INTO users(email,salt,pw_hash,created) VALUES(?,?,?,?)",
                (email, salt.hex(), self._hash(password, salt), _now()))
            self._db.commit()
        except sqlite3.IntegrityError:
            raise AuthError("That email is already registered.")
        return self._new_session(cur.lastrowid, email)

    def login(self, email: str, password: str) -> dict:
        email = (email or "").strip().lower()
        row = self._db.execute(
            "SELECT id,salt,pw_hash FROM users WHERE email=?", (email,)).fetchone()
        # compute a hash even when the user is unknown (constant-ish time)
        salt = bytes.fromhex(row["salt"]) if row else os.urandom(16)
        candidate = self._hash(password or "", salt)
        if not row or not hmac.compare_digest(candidate, row["pw_hash"]):
            raise AuthError("Wrong email or password.")
        return self._new_session(row["id"], email)

    def _new_session(self, user_id: int, email: str) -> dict:
        token = secrets.token_urlsafe(32)
        now = _now()
        self._db.execute(
            "INSERT INTO sessions(token,user_id,created,expires) VALUES(?,?,?,?)",
            (token, user_id, now, now + _SESSION_TTL))
        self._db.commit()
        return {"token": token, "user": {"id": user_id, "email": email}}

    def logout(self, token: str) -> None:
        if not token:
            return
        self._db.execute("DELETE FROM sessions WHERE token=?", (token,))
        self._db.commit()

    def user_for(self, token: str) -> dict | None:
        if not token:
            return None
        row = self._db.execute(
            "SELECT s.expires, u.id, u.email FROM sessions s "
            "JOIN users u ON u.id=s.user_id WHERE s.token=?", (token,)).fetchone()
        if not row:
            return None
        if row["expires"] < _now():
            self.logout(token)
            return None
        return {"id": row["id"], "email": row["email"]}

    # -- projects -------------------------------------------------------- #
    @staticmethod
    def _dump(data) -> str:
        blob = json.dumps(data, separators=(",", ":"))
        if len(blob) > _MAX_BLOB:
            raise AuthError("Project is too large to save.")
        return blob

    def list_projects(self, user_id: int) -> list:
        rows = self._db.execute(
            "SELECT id,name,created,updated FROM projects WHERE user_id=? "
            "ORDER BY updated DESC", (user_id,)).fetchall()
        return [dict(r) for r in rows]

    def get_project(self, user_id: int, pid: int) -> dict | None:
        r = self._db.execute(
            "SELECT id,name,data,created,updated FROM projects WHERE id=? AND user_id=?",
            (pid, user_id)).fetchone()
        if not r:
            return None
        out = dict(r)
        out["data"] = json.loads(out["data"])
        return out

    def save_project(self, user_id: int, pid, name: str, data) -> dict:
        name = (name or "Untitled").strip()[:200] or "Untitled"
        blob = self._dump(data)
        now = _now()
        if pid:
            n = self._db.execute(
                "UPDATE projects SET name=?,data=?,updated=? WHERE id=? AND user_id=?",
                (name, blob, now, pid, user_id)).rowcount
            if not n:
                raise AuthError("Project not found.")
            self._db.commit()
            return {"id": int(pid), "name": name, "updated": now}
        cur = self._db.execute(
            "INSERT INTO projects(user_id,name,data,created,updated) VALUES(?,?,?,?,?)",
            (user_id, name, blob, now, now))
        self._db.commit()
        return {"id": cur.lastrowid, "name": name, "updated": now}

    def delete_project(self, user_id: int, pid: int) -> bool:
        n = self._db.execute(
            "DELETE FROM projects WHERE id=? AND user_id=?", (pid, user_id)).rowcount
        self._db.commit()
        return bool(n)

    # -- zero-knowledge key vault (ciphertext only) ---------------------- #
    def get_vault(self, user_id: int) -> dict | None:
        r = self._db.execute(
            "SELECT data,updated FROM vaults WHERE user_id=?", (user_id,)).fetchone()
        return {"data": json.loads(r["data"]), "updated": r["updated"]} if r else None

    def set_vault(self, user_id: int, data) -> dict:
        blob = self._dump(data)
        now = _now()
        self._db.execute(
            "INSERT INTO vaults(user_id,data,updated) VALUES(?,?,?) "
            "ON CONFLICT(user_id) DO UPDATE SET data=excluded.data,updated=excluded.updated",
            (user_id, blob, now))
        self._db.commit()
        return {"updated": now}

    def close(self) -> None:
        self._db.close()
