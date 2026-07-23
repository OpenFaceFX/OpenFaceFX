"""Tests for the OpenFaceFX Studio SaaS backend (accounts / projects / vault)."""

import pytest

from openfacefx.studio_saas import Store, AuthError


@pytest.fixture
def store():
    s = Store(":memory:")
    yield s
    s.close()


def test_register_and_login_roundtrip(store):
    reg = store.register("Alice@Example.com", "hunter2pw")
    assert reg["user"]["email"] == "alice@example.com"   # normalized lower-case
    assert reg["token"]
    # the session resolves back to the user
    assert store.user_for(reg["token"])["email"] == "alice@example.com"
    # logging in mints a *new*, independent session
    log = store.login("alice@example.com", "hunter2pw")
    assert log["token"] and log["token"] != reg["token"]
    assert store.user_for(log["token"])["id"] == reg["user"]["id"]


def test_password_is_hashed_not_stored_plain(store):
    store.register("bob@example.com", "s3cretpass")
    row = store._db.execute("SELECT pw_hash,salt FROM users WHERE email=?",
                            ("bob@example.com",)).fetchone()
    assert "s3cretpass" not in row["pw_hash"]
    assert len(row["pw_hash"]) == 64          # sha256 hex digest
    assert row["salt"] and row["salt"] != ""


def test_wrong_password_and_unknown_user_rejected(store):
    store.register("carol@example.com", "goodpassword")
    with pytest.raises(AuthError):
        store.login("carol@example.com", "wrongpassword")
    with pytest.raises(AuthError):
        store.login("nobody@example.com", "whatever12")


def test_duplicate_email_rejected(store):
    store.register("dave@example.com", "password1")
    with pytest.raises(AuthError):
        store.register("dave@example.com", "password2")


@pytest.mark.parametrize("email,pw", [
    ("not-an-email", "longenough1"),
    ("ok@example.com", "short"),
    ("", "longenough1"),
])
def test_registration_validation(store, email, pw):
    with pytest.raises(AuthError):
        store.register(email, pw)


def test_logout_and_expired_session_invalidate(store):
    reg = store.register("erin@example.com", "password12")
    store.logout(reg["token"])
    assert store.user_for(reg["token"]) is None
    # a manually-expired session is rejected (and cleaned up)
    reg2 = store.register("frank@example.com", "password12")
    store._db.execute("UPDATE sessions SET expires=1 WHERE token=?", (reg2["token"],))
    store._db.commit()
    assert store.user_for(reg2["token"]) is None


def test_project_crud_and_user_isolation(store):
    a = store.register("a@example.com", "password12")["user"]
    b = store.register("b@example.com", "password12")["user"]
    p = store.save_project(a["id"], None, "My scene", {"actors": [{"name": "N"}]})
    assert p["id"] and p["name"] == "My scene"
    # owner reads it back
    got = store.get_project(a["id"], p["id"])
    assert got["data"]["actors"][0]["name"] == "N"
    # a different user cannot see or delete it
    assert store.get_project(b["id"], p["id"]) is None
    assert store.delete_project(b["id"], p["id"]) is False
    assert store.list_projects(b["id"]) == []
    # update in place (same id)
    p2 = store.save_project(a["id"], p["id"], "Renamed", {"actors": []})
    assert p2["id"] == p["id"]
    assert store.get_project(a["id"], p["id"])["name"] == "Renamed"
    assert len(store.list_projects(a["id"])) == 1
    # owner deletes it
    assert store.delete_project(a["id"], p["id"]) is True
    assert store.list_projects(a["id"]) == []


def test_saving_over_another_users_project_is_rejected(store):
    a = store.register("a@example.com", "password12")["user"]
    b = store.register("b@example.com", "password12")["user"]
    p = store.save_project(a["id"], None, "A's", {"x": 1})
    with pytest.raises(AuthError):
        store.save_project(b["id"], p["id"], "hijack", {"x": 2})


def test_vault_stores_and_returns_ciphertext_per_user(store):
    a = store.register("a@example.com", "password12")["user"]
    assert store.get_vault(a["id"]) is None
    blob = {"v": 1, "kdf": "PBKDF2-SHA256", "items": [{"provider": "openai", "ct": "deadbeef"}]}
    store.set_vault(a["id"], blob)
    got = store.get_vault(a["id"])
    assert got["data"]["items"][0]["ct"] == "deadbeef"
    # overwrite (upsert)
    store.set_vault(a["id"], {"v": 2, "items": []})
    assert store.get_vault(a["id"])["data"]["v"] == 2
    # isolation
    b = store.register("b@example.com", "password12")["user"]
    assert store.get_vault(b["id"]) is None
