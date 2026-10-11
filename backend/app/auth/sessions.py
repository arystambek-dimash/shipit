"""Server-side sessions: a random token in a cookie, only its SHA-256 stored."""
from __future__ import annotations

import hashlib
import secrets
import sqlite3
from typing import Mapping

from starlette.responses import Response

from .. import db
from ..config import Settings

SESSION_TTL = 30 * 86400
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def create_session(conn: sqlite3.Connection, user_id: int, now: int | None = None) -> str:
    ts = db.now() if now is None else now
    token = secrets.token_urlsafe(32)
    conn.execute(
        "INSERT INTO sessions (token_hash, user_id, created_at, expires_at) VALUES (?, ?, ?, ?)",
        (_hash(token), user_id, ts, ts + SESSION_TTL),
    )
    return token


def user_for_token(conn: sqlite3.Connection, token: str, now: int | None = None) -> sqlite3.Row | None:
    ts = db.now() if now is None else now
    key = _hash(token)
    row = conn.execute(
        "SELECT s.expires_at AS session_expires_at, u.* FROM sessions s"
        " JOIN users u ON u.id = s.user_id WHERE s.token_hash = ?",
        (key,),
    ).fetchone()
    if row is None:
        return None
    if row["session_expires_at"] <= ts:
        conn.execute("DELETE FROM sessions WHERE token_hash = ?", (key,))
        return None
    return row


def delete_session(conn: sqlite3.Connection, token: str) -> None:
    conn.execute("DELETE FROM sessions WHERE token_hash = ?", (_hash(token),))


def set_session_cookie(response: Response, settings: Settings, token: str) -> None:
    response.set_cookie(
        settings.session_cookie, token, max_age=SESSION_TTL, path="/",
        secure=settings.https, httponly=True, samesite="lax",
    )


def clear_session_cookie(response: Response, settings: Settings) -> None:
    response.delete_cookie(
        settings.session_cookie, path="/", secure=settings.https, httponly=True, samesite="lax"
    )


def origin_allowed(method: str, headers: Mapping[str, str], app_origin: str) -> bool:
    if method.upper() in SAFE_METHODS:
        return True
    origin = headers.get("origin")
    if origin:
        return origin == app_origin
    referer = headers.get("referer")
    if referer:
        return referer == app_origin or referer.startswith(app_origin + "/")
    return False
