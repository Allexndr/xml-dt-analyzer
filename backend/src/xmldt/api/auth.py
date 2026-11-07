"""Optional passwordless e-mail login (one-time code).

Anonymous use keeps working. A signed-in user gets an owner key derived from the
user id, so files and history stay with the account. Codes and session tokens are
stored only as salted / plain SHA-256 hashes.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import os
import re
import secrets
import smtplib
import sqlite3
import time
import uuid
from email.message import EmailMessage
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request, Response
from pydantic import BaseModel

from xmldt.api.store import Store, migrate_anonymous, owner_key

log = logging.getLogger("xmldt.auth")

ANON_COOKIE = "xmldt_sid"
USER_COOKIE = "xmldt_uid"
CODE_TTL = 10 * 60
MAX_ATTEMPTS = 5
PAIR_LIMIT = (3, 10 * 60)       # code requests per (email, IP) per window (seconds)
EMAIL_LIMIT = (10, 60 * 60)     # code requests per email from all IPs: high, so others cannot lock a user out cheaply
IP_LIMIT = (20, 60 * 60)        # code requests per IP per window
FAIL_PAIR_LIMIT = (5, 10 * 60)  # wrong codes per (email, IP)
FAIL_EMAIL_LIMIT = (30, 60 * 60)  # wrong codes per email from all IPs (brute-force ceiling)
ACTIVE_CODES = 5                # latest unexpired codes stay valid; a new request does not cancel the user's code
SESSION_TTL = 30 * 24 * 3600
ANON_MAX_AGE = 7 * 24 * 3600
BAD_CODE = "Неверный или просроченный код"

_EMAIL_RE = re.compile(
    r"^[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]{1,64}@"
    r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)+$"
)
_CODE_RE = re.compile(r"^\d{6}$")

_store: Store | None = None
on_login: list = []  # callbacks (anon_owner, user_owner) run after a successful login


def configure(store: Store | None) -> None:
    """Give the router the file store so anonymous files can move to the account on login."""
    global _store
    _store = store


def _now() -> float:  # patched in tests
    return time.time()


def normalize_email(raw: str) -> str | None:
    e = (raw or "").strip().lower()
    if len(e) > 254 or not _EMAIL_RE.match(e):
        return None
    return e


def user_owner(user_id: str) -> str:
    return owner_key("user:" + user_id)


# ---- database ---------------------------------------------------------------

_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (id TEXT PRIMARY KEY, email TEXT UNIQUE NOT NULL, created_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS codes (
  id INTEGER PRIMARY KEY AUTOINCREMENT, email TEXT NOT NULL, salt TEXT NOT NULL, hash TEXT NOT NULL,
  expires_at REAL NOT NULL, attempts INTEGER NOT NULL DEFAULT 0, used INTEGER NOT NULL DEFAULT 0);
CREATE INDEX IF NOT EXISTS codes_email ON codes(email);
CREATE TABLE IF NOT EXISTS requests (id INTEGER PRIMARY KEY AUTOINCREMENT, email TEXT NOT NULL, ip TEXT NOT NULL, at REAL NOT NULL);
CREATE INDEX IF NOT EXISTS requests_email ON requests(email, at);
CREATE INDEX IF NOT EXISTS requests_ip ON requests(ip, at);
CREATE TABLE IF NOT EXISTS sessions (token_hash TEXT PRIMARY KEY, user_id TEXT NOT NULL, expires_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS failures (id INTEGER PRIMARY KEY AUTOINCREMENT, email TEXT NOT NULL, ip TEXT NOT NULL, at REAL NOT NULL);
CREATE INDEX IF NOT EXISTS failures_email ON failures(email, at);
"""


def _db_path() -> Path:
    var = os.environ.get("XMLDT_VAR") or str(Path(__file__).resolve().parents[3] / "var")
    return Path(var) / "auth.sqlite3"


_ready: set[str] = set()


def _conn():
    from xmldt.api import db

    return db.connect(_db_path(), _SCHEMA)


def _h(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()


def _code_hash(salt: str, code: str) -> str:
    return _h(f"xmldt-code:{salt}:{code}")


def _sweep(c: sqlite3.Connection, t: float) -> None:
    c.execute("DELETE FROM codes WHERE expires_at < ?", (t - 3600,))
    c.execute("DELETE FROM requests WHERE at < ?", (t - IP_LIMIT[1],))
    c.execute("DELETE FROM sessions WHERE expires_at < ?", (t,))
    c.execute("DELETE FROM failures WHERE at < ?", (t - FAIL_EMAIL_LIMIT[1],))


# ---- delivery ---------------------------------------------------------------

def _smtp_configured() -> bool:
    return bool(os.environ.get("XMLDT_SMTP_HOST"))


def _client_ip(request) -> str:
    return request.client.host if request.client else "unknown"


def dev_mode() -> bool:
    """Codes are shown in the server log only in an explicit development mode."""
    return os.environ.get("XMLDT_AUTH_DEV") == "1" or os.environ.get("XMLDT_AUTH_DEV_ECHO") == "1"


def cookie_secure(request) -> bool:
    return os.environ.get("XMLDT_COOKIE_SECURE") == "1" or request.url.scheme == "https"


def _send_mail(email: str, code: str) -> None:
    msg = EmailMessage()
    msg["Subject"] = "Код для входа"
    msg["From"] = os.environ.get("XMLDT_SMTP_FROM") or os.environ.get("XMLDT_SMTP_USER") or "noreply@localhost"
    msg["To"] = email
    msg.set_content(
        f"Здравствуйте!\n\nВаш код для входа: {code}\n\n"
        "Код действует 10 минут. Если вы не запрашивали вход, просто проигнорируйте это письмо.\n"
    )
    try:
        with smtplib.SMTP(os.environ["XMLDT_SMTP_HOST"], int(os.environ.get("XMLDT_SMTP_PORT", "587")), timeout=15) as s:
            s.starttls()
            user = os.environ.get("XMLDT_SMTP_USER")
            if user:
                s.login(user, os.environ.get("XMLDT_SMTP_PASSWORD", ""))
            s.send_message(msg)
    except Exception:  # never reveal delivery problems to the caller; never log the code
        log.exception("Не удалось отправить код входа")


# ---- sessions / owners ------------------------------------------------------

def user_from_request(request: Request) -> tuple[str, str] | None:
    """Return (user_id, email) for a valid login cookie, else None."""
    token = request.cookies.get(USER_COOKIE)
    if not token or len(token) > 128:
        return None
    c = _conn()
    try:
        row = c.execute(
            "SELECT u.id, u.email FROM sessions s JOIN users u ON u.id = s.user_id "
            "WHERE s.token_hash = ? AND s.expires_at > ?", (_h(token), _now())).fetchone()
    finally:
        c.close()
    return (row["id"], row["email"]) if row else None


def current_owner(request: Request, response: Response) -> tuple[str, str | None]:
    """FastAPI dependency: (owner key, e-mail or None). Anonymous behaviour is unchanged."""
    user = user_from_request(request)
    if user:
        return user_owner(user[0]), user[1]
    token = request.cookies.get(ANON_COOKIE)
    if not token or len(token) > 64:
        token = secrets.token_urlsafe(32)
        response.set_cookie(ANON_COOKIE, token, httponly=True, samesite="lax", max_age=ANON_MAX_AGE,
                            secure=cookie_secure(request))
    return owner_key(token), None


# ---- API --------------------------------------------------------------------

router = APIRouter(prefix="/api/auth", tags=["auth"])


class RequestIn(BaseModel):
    email: str


class VerifyIn(BaseModel):
    email: str
    code: str


@router.post("/request", status_code=202)
def request_code(body: RequestIn, request: Request, tasks: BackgroundTasks) -> dict:
    email = normalize_email(body.email)
    if email is None:
        raise HTTPException(400, "Укажите корректный адрес электронной почты")
    if not _smtp_configured() and not dev_mode():
        raise HTTPException(503, "Вход по e-mail не настроен на сервере (нет SMTP)")
    ip = _client_ip(request)
    t = _now()
    code = f"{secrets.randbelow(1_000_000):06d}"
    salt = secrets.token_hex(16)
    c = _conn()
    try:
        c.execute("BEGIN IMMEDIATE")
        _sweep(c, t)
        count = lambda where, args, win: c.execute(  # noqa: E731
            f"SELECT COUNT(*) FROM requests WHERE {where} AND at>?", (*args, t - win)).fetchone()[0]
        if (count("email=? AND ip=?", (email, ip), PAIR_LIMIT[1]) >= PAIR_LIMIT[0]
                or count("email=?", (email,), EMAIL_LIMIT[1]) >= EMAIL_LIMIT[0]
                or count("ip=?", (ip,), IP_LIMIT[1]) >= IP_LIMIT[0]):
            c.execute("ROLLBACK")
            raise HTTPException(429, "Слишком много запросов кода. Попробуйте позже.")
        c.execute("INSERT INTO requests(email, ip, at) VALUES (?,?,?)", (email, ip, t))
        # keep only the latest ACTIVE_CODES codes valid (older ones are retired, not the user's fresh one)
        c.execute("UPDATE codes SET used=1 WHERE email=? AND used=0 AND id NOT IN "
                  "(SELECT id FROM codes WHERE email=? AND used=0 ORDER BY id DESC LIMIT ?)",
                  (email, email, ACTIVE_CODES - 1))
        c.execute("INSERT INTO codes(email, salt, hash, expires_at) VALUES (?,?,?,?)",
                  (email, salt, _code_hash(salt, code), t + CODE_TTL))
        c.execute("COMMIT")
    finally:
        c.close()

    out: dict = {"status": "sent"}
    if _smtp_configured():
        tasks.add_task(_send_mail, email, code)
    else:  # dev_mode() is guaranteed here
        log.warning("DEV MODE (XMLDT_AUTH_DEV): SMTP не настроен, код входа для %s: %s", email, code)
        if os.environ.get("XMLDT_AUTH_DEV_ECHO") == "1":
            out["dev_code"] = code
    return out


@router.post("/verify")
def verify(body: VerifyIn, request: Request, response: Response) -> dict:
    email = normalize_email(body.email)
    code = (body.code or "").strip()
    if email is None or not _CODE_RE.match(code):
        raise HTTPException(400, BAD_CODE)
    ip = _client_ip(request)
    t = _now()
    c = _conn()
    try:
        c.execute("BEGIN IMMEDIATE")
        fails_pair = c.execute("SELECT COUNT(*) FROM failures WHERE email=? AND ip=? AND at>?",
                               (email, ip, t - FAIL_PAIR_LIMIT[1])).fetchone()[0]
        fails_all = c.execute("SELECT COUNT(*) FROM failures WHERE email=? AND at>?",
                              (email, t - FAIL_EMAIL_LIMIT[1])).fetchone()[0]
        if fails_pair >= FAIL_PAIR_LIMIT[0] or fails_all >= FAIL_EMAIL_LIMIT[0]:
            c.execute("COMMIT")
            raise HTTPException(429, "Слишком много неверных попыток. Запросите новый код позже.")
        rows = c.execute("SELECT * FROM codes WHERE email=? AND used=0 AND expires_at>=? ORDER BY id DESC LIMIT ?",
                         (email, t, ACTIVE_CODES)).fetchall()
        # wrong guesses are counted per (email, IP); they do not destroy the user's code
        row = next((r for r in rows if hmac.compare_digest(r["hash"], _code_hash(r["salt"], code))), None)
        if row is None:
            c.execute("INSERT INTO failures(email, ip, at) VALUES (?,?,?)", (email, ip, t))
            c.execute("COMMIT")
            raise HTTPException(400, BAD_CODE)
        c.execute("UPDATE codes SET used=1 WHERE id=?", (row["id"],))
        u = c.execute("SELECT id FROM users WHERE email=?", (email,)).fetchone()
        if u is None:
            uid = uuid.uuid4().hex
            c.execute("INSERT INTO users(id, email, created_at) VALUES (?,?,?)", (uid, email, t))
        else:
            uid = u["id"]
        token = secrets.token_urlsafe(32)
        c.execute("INSERT INTO sessions(token_hash, user_id, expires_at) VALUES (?,?,?)", (_h(token), uid, t + SESSION_TTL))
        c.execute("COMMIT")
    finally:
        c.close()

    anon = request.cookies.get(ANON_COOKIE)
    if anon and len(anon) <= 64 and _store is not None:
        migrate_anonymous(_store, owner_key(anon), user_owner(uid))
        for cb in on_login:
            cb(owner_key(anon), user_owner(uid))
        response.delete_cookie(ANON_COOKIE, path="/")
    response.set_cookie(USER_COOKIE, token, httponly=True, samesite="lax", max_age=SESSION_TTL,
                        secure=cookie_secure(request), path="/")
    return {"email": email}


@router.get("/me")
def me(request: Request) -> dict:
    user = user_from_request(request)
    return {"email": user[1] if user else None}


@router.post("/logout")
def logout(request: Request, response: Response) -> dict:
    token = request.cookies.get(USER_COOKIE)
    if token and len(token) <= 128:
        c = _conn()
        try:
            c.execute("DELETE FROM sessions WHERE token_hash=?", (_h(token),))
        finally:
            c.close()
    response.delete_cookie(USER_COOKIE, path="/")
    return {"email": None}
