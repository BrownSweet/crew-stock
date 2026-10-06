"""Single administrator with hashed passwords and revocable MySQL sessions."""

import hashlib
import hmac
import os
import secrets
import threading
import time

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, Field, SecretStr

from . import config
from .store import now

COOKIE = "crew_session"
SESSION_SECONDS = 7 * 24 * 3600
ATTEMPTS = {}
ATTEMPTS_LOCK = threading.Lock()


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def password_hash(password):
    salt = secrets.token_bytes(16)
    value = hashlib.scrypt(
        password.encode(), salt=salt, n=32768, r=8, p=1, maxmem=64 * 1024 * 1024
    )
    return f"scrypt${salt.hex()}${value.hex()}"


def password_matches(password, encoded):
    try:
        algorithm, salt, expected = encoded.split("$")
        if algorithm != "scrypt":
            return False
        actual = hashlib.scrypt(
            password.encode(),
            salt=bytes.fromhex(salt),
            n=32768,
            r=8,
            p=1,
            maxmem=64 * 1024 * 1024,
        )
        return hmac.compare_digest(actual.hex(), expected)
    except ValueError:
        return False


def authenticate(store, token):
    if not token:
        return None
    with store.connection() as con:
        row = con.execute(
            "SELECT a.username FROM accounts a JOIN sessions s ON a.id=s.account_id WHERE s.token_hash=%s AND s.expires_at>%s",
            (digest(token), int(time.time())),
        ).fetchone()
    return row[0] if row else None


def create_session(con, response):
    token = secrets.token_urlsafe(32)
    con.execute("DELETE FROM sessions WHERE expires_at<=%s", (int(time.time()),))
    con.execute(
        "INSERT INTO sessions VALUES (%s,1,%s)",
        (digest(token), int(time.time()) + SESSION_SECONDS),
    )
    response.set_cookie(
        COOKIE,
        token,
        max_age=SESSION_SECONDS,
        httponly=True,
        samesite="strict",
        secure=os.getenv("COOKIE_SECURE", "false").lower() in {"1", "true"},
        path=config.BASE_PATH,
    )


class Credentials(BaseModel):
    username: str = Field(min_length=3, max_length=64, pattern=r"^[A-Za-z0-9_.@-]+$")
    password: SecretStr = Field(min_length=12, max_length=256)


class Login(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: SecretStr = Field(min_length=1, max_length=256)


def router(get_store):
    api = APIRouter(prefix="/api/auth")

    @api.get("/status")
    def status():
        with get_store().connection() as con:
            return {
                "initialized": con.execute(
                    "SELECT id FROM accounts WHERE id=1"
                ).fetchone()
                is not None
            }

    @api.post("/setup", status_code=201)
    def setup(p: Credentials, response: Response):
        encoded = password_hash(p.password.get_secret_value())
        with get_store().transaction() as con:
            if con.execute("SELECT id FROM accounts WHERE id=1").fetchone():
                raise HTTPException(409, "管理员已创建，请登录")
            con.execute(
                "INSERT INTO accounts VALUES (1,%s,%s,%s)", (p.username, encoded, now())
            )
            create_session(con, response)
        return {"username": p.username}

    @api.post("/login")
    def login(p: Login, request: Request, response: Response):
        host = request.client.host if request.client else "unknown"
        stamp = time.monotonic()
        # Reserve an attempt before hashing so concurrent requests cannot bypass the limit.
        with ATTEMPTS_LOCK:
            for key in list(ATTEMPTS):
                if ATTEMPTS[key][-1] < stamp - 600:
                    del ATTEMPTS[key]
            attempts = [t for t in ATTEMPTS.get(host, []) if t > stamp - 600]
            if len(attempts) >= 10 or (host not in ATTEMPTS and len(ATTEMPTS) >= 10000):
                raise HTTPException(429, "尝试过于频繁，请 10 分钟后重试")
            ATTEMPTS[host] = attempts + [stamp]
        with get_store().transaction() as con:
            row = con.execute(
                "SELECT username,password_hash FROM accounts WHERE id=1"
            ).fetchone()
            if (
                not row
                or not password_matches(p.password.get_secret_value(), row[1])
                or row[0] != p.username
            ):
                raise HTTPException(401, "账号或密码不正确")
            create_session(con, response)
        with ATTEMPTS_LOCK:
            ATTEMPTS.pop(host, None)
        return {"username": row[0]}

    @api.get("/session")
    def session(request: Request):
        return {"username": request.state.account_name}

    @api.post("/logout")
    def logout(request: Request, response: Response):
        with get_store().connection() as con:
            con.execute(
                "DELETE FROM sessions WHERE token_hash=%s",
                (digest(request.cookies.get(COOKIE, "")),),
            )
        response.delete_cookie(COOKIE, path=config.BASE_PATH)
        return {"ok": True}

    return api
