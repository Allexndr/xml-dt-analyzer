"""E-mail one-time-code login: codes, limits, sessions, ownership migration."""

from __future__ import annotations

import sqlite3

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from xmldt.api import auth
from xmldt.api.store import Store, owner_key


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("XMLDT_VAR", str(tmp_path))
    monkeypatch.setenv("XMLDT_AUTH_DEV_ECHO", "1")
    monkeypatch.delenv("XMLDT_SMTP_HOST", raising=False)
    store = Store(tmp_path / "files")
    auth.configure(store)
    app = FastAPI()
    app.include_router(auth.router)

    @app.get("/whoami")
    def whoami(o: tuple[str, str | None] = Depends(auth.current_owner)):
        return {"owner": o[0], "email": o[1]}

    yield TestClient(app), store, tmp_path
    auth.configure(None)


def login(client, email="User@Example.com"):
    r = client.post("/api/auth/request", json={"email": email})
    assert r.status_code == 202
    return client.post("/api/auth/verify", json={"email": email, "code": r.json()["dev_code"]})


def test_happy_path_and_cookie_flags(env):
    client, _, _ = env
    r = login(client)
    assert r.status_code == 200 and r.json() == {"email": "user@example.com"}
    cookie = r.headers["set-cookie"].lower()
    assert "xmldt_uid=" in cookie and "httponly" in cookie and "samesite=lax" in cookie
    assert "secure" not in cookie
    assert client.get("/api/auth/me").json() == {"email": "user@example.com"}
    w = client.get("/whoami").json()
    assert w["email"] == "user@example.com"
    # same user gets the same owner key on a fresh login
    other = TestClient(client.app)
    login(other, "user@example.com")
    assert other.get("/whoami").json()["owner"] == w["owner"]


def test_secure_flag_on_https(env):
    client, _, _ = env
    c = TestClient(client.app, base_url="https://testserver")
    assert "secure" in login(c).headers["set-cookie"].lower()


def test_no_dev_echo_without_flag(env, monkeypatch, caplog):
    client, _, _ = env
    monkeypatch.delenv("XMLDT_AUTH_DEV_ECHO")
    monkeypatch.setenv("XMLDT_AUTH_DEV", "1")
    with caplog.at_level("WARNING", logger="xmldt.auth"):
        r = client.post("/api/auth/request", json={"email": "a@b.co"})
    assert r.json() == {"status": "sent"}
    assert "DEV MODE" in caplog.text


def test_without_smtp_and_dev_mode_login_is_disabled(env, monkeypatch, caplog):
    client, _, _ = env
    monkeypatch.delenv("XMLDT_AUTH_DEV_ECHO")
    monkeypatch.delenv("XMLDT_AUTH_DEV", raising=False)
    with caplog.at_level("DEBUG"):
        r = client.post("/api/auth/request", json={"email": "a@b.co"})
    assert r.status_code == 503 and "DEV MODE" not in caplog.text


def test_smtp_mode_never_logs_code(env, monkeypatch, caplog):
    client, _, _ = env
    monkeypatch.setenv("XMLDT_SMTP_HOST", "smtp.invalid")
    sent = []
    monkeypatch.setattr(auth, "_send_mail", lambda e, c: sent.append((e, c)))
    with caplog.at_level("DEBUG"):
        r = client.post("/api/auth/request", json={"email": "a@b.co"})
    assert r.json() == {"status": "sent"}
    assert len(sent) == 1 and sent[0][1] not in caplog.text


def test_wrong_code_attempt_limit(env):
    client, _, _ = env
    r = client.post("/api/auth/request", json={"email": "a@b.co"})
    good = r.json()["dev_code"]
    bad = "000000" if good != "000000" else "111111"
    for _ in range(auth.FAIL_PAIR_LIMIT[0]):
        v = client.post("/api/auth/verify", json={"email": "a@b.co", "code": bad})
        assert v.status_code == 400 and v.json()["detail"] == "Неверный или просроченный код"
    # this IP is now blocked for this e-mail, even with the right code
    assert client.post("/api/auth/verify", json={"email": "a@b.co", "code": good}).status_code == 429


def test_attacker_cannot_burn_victims_code(env, monkeypatch):
    client, _, _ = env
    good = client.post("/api/auth/request", json={"email": "victim@b.co"}).json()["dev_code"]
    bad = "000000" if good != "000000" else "111111"
    real_ip = auth._client_ip
    monkeypatch.setattr(auth, "_client_ip", lambda request: "203.0.113.9")  # attacker's IP
    for _ in range(auth.FAIL_PAIR_LIMIT[0]):
        client.post("/api/auth/verify", json={"email": "victim@b.co", "code": bad})
    for _ in range(auth.PAIR_LIMIT[0]):  # attacker also floods code requests from their IP
        client.post("/api/auth/request", json={"email": "victim@b.co"})
    monkeypatch.setattr(auth, "_client_ip", real_ip)
    # the victim's own code still works from the victim's IP
    assert client.post("/api/auth/verify", json={"email": "victim@b.co", "code": good}).status_code == 200


def test_code_single_use_and_older_codes_stay_valid(env):
    client, _, _ = env
    first = client.post("/api/auth/request", json={"email": "a@b.co"}).json()["dev_code"]
    second = client.post("/api/auth/request", json={"email": "a@b.co"}).json()["dev_code"]
    # a new request does not cancel the previous code (someone else may have triggered it)
    assert client.post("/api/auth/verify", json={"email": "a@b.co", "code": first}).status_code == 200
    assert client.post("/api/auth/verify", json={"email": "a@b.co", "code": first}).status_code == 400
    if first != second:
        assert client.post("/api/auth/verify", json={"email": "a@b.co", "code": second}).status_code == 200


def test_expiry(env, monkeypatch):
    client, _, _ = env
    code = client.post("/api/auth/request", json={"email": "a@b.co"}).json()["dev_code"]
    real = auth._now()
    monkeypatch.setattr(auth, "_now", lambda: real + auth.CODE_TTL + 1)
    assert client.post("/api/auth/verify", json={"email": "a@b.co", "code": code}).status_code == 400


def test_session_expiry(env, monkeypatch):
    client, _, _ = env
    login(client)
    real = auth._now()
    monkeypatch.setattr(auth, "_now", lambda: real + auth.SESSION_TTL + 1)
    assert client.get("/api/auth/me").json() == {"email": None}


def test_email_rate_limit(env):
    client, _, _ = env
    for _ in range(3):
        assert client.post("/api/auth/request", json={"email": "a@b.co"}).status_code == 202
    r = client.post("/api/auth/request", json={"email": "A@B.co"})
    assert r.status_code == 429
    assert client.post("/api/auth/request", json={"email": "other@b.co"}).status_code == 202


def test_ip_rate_limit(env):
    client, _, _ = env
    for i in range(20):
        assert client.post("/api/auth/request", json={"email": f"u{i}@b.co"}).status_code == 202
    assert client.post("/api/auth/request", json={"email": "u99@b.co"}).status_code == 429


def test_rate_limit_window_expires(env, monkeypatch):
    client, _, _ = env
    for _ in range(3):
        client.post("/api/auth/request", json={"email": "a@b.co"})
    real = auth._now()
    monkeypatch.setattr(auth, "_now", lambda: real + 601)
    assert client.post("/api/auth/request", json={"email": "a@b.co"}).status_code == 202


def test_no_enumeration_same_shape(env, monkeypatch):
    client, _, _ = env
    login(client, "known@b.co")
    monkeypatch.delenv("XMLDT_AUTH_DEV_ECHO")
    monkeypatch.setenv("XMLDT_AUTH_DEV", "1")
    a = TestClient(client.app).post("/api/auth/request", json={"email": "known@b.co"})
    b = TestClient(client.app).post("/api/auth/request", json={"email": "unknown@b.co"})
    assert (a.status_code, a.json()) == (b.status_code, b.json()) == (202, {"status": "sent"})


@pytest.mark.parametrize("bad", ["", "no-at", "a@b", "a b@c.de", "a@@c.de", "x" * 250 + "@b.co", "a@b.co\nBcc: x@y.z"])
def test_invalid_email(env, bad):
    client, _, _ = env
    assert client.post("/api/auth/request", json={"email": bad}).status_code == 400
    assert client.post("/api/auth/verify", json={"email": bad, "code": "123456"}).status_code == 400


def test_non_numeric_code_rejected(env):
    client, _, _ = env
    client.post("/api/auth/request", json={"email": "a@b.co"})
    assert client.post("/api/auth/verify", json={"email": "a@b.co", "code": "abcdef"}).status_code == 400


def test_logout(env):
    client, _, tmp = env
    login(client)
    assert client.post("/api/auth/logout").status_code == 200
    assert client.get("/api/auth/me").json() == {"email": None}
    assert client.get("/whoami").json()["email"] is None
    n = sqlite3.connect(tmp / "auth.sqlite3").execute("SELECT COUNT(*) FROM sessions").fetchone()[0]
    assert n == 0


def test_secrets_not_stored_in_clear(env):
    client, _, tmp = env
    r = client.post("/api/auth/request", json={"email": "a@b.co"})
    code = r.json()["dev_code"]
    v = client.post("/api/auth/verify", json={"email": "a@b.co", "code": code})
    token = client.cookies.get("xmldt_uid")
    db = sqlite3.connect(tmp / "auth.sqlite3")
    dump = "\n".join(str(r) for t in ("codes", "sessions") for r in db.execute(f"SELECT * FROM {t}"))
    assert code not in dump and token not in dump and v.status_code == 200


def test_anonymous_files_migrated(env):
    client, store, _ = env
    anon_owner = client.get("/whoami").json()["owner"]
    assert client.cookies.get("xmldt_sid")
    meta = store.create(anon_owner, "d.xml", b"<a/>")
    stranger = store.create(owner_key("someone-else"), "x.xml", b"<a/>")
    assert login(client).status_code == 200
    uid_owner = client.get("/whoami").json()["owner"]
    assert uid_owner != anon_owner
    moved = store.get(uid_owner, meta.id)
    from datetime import datetime, timedelta, timezone
    assert datetime.fromisoformat(moved.expires_at) > datetime.now(timezone.utc) + timedelta(days=3000)
    with pytest.raises(KeyError):
        store.get(anon_owner, meta.id)
    assert store.get(owner_key("someone-else"), stranger.id)
    assert client.cookies.get("xmldt_sid") is None


def test_expired_anonymous_files_not_migrated(env):
    client, store, _ = env
    from datetime import datetime, timedelta, timezone
    from xmldt.api.store import migrate_anonymous
    meta = store.create("anon", "d.xml", b"<a/>")
    meta.expires_at = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    store.save(meta, ownership=True)
    assert migrate_anonymous(store, "anon", "user") == 0


def test_late_analysis_save_does_not_revert_migration(env):
    client, store, _ = env
    from xmldt.api.store import migrate_anonymous
    meta = store.create("anon", "d.xml", b"<a/>")      # analysis still holds this stale copy
    assert migrate_anonymous(store, "anon", "user") == 1
    meta.status = "ready"
    store.save(meta)                                    # late write from the upload analysis
    assert store.get("user", meta.id).owner == "user"
