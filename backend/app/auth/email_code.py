"""Six-digit email confirmation codes with expiry, attempt limits and rate limits."""
from __future__ import annotations

import hashlib
import hmac
import secrets
import sqlite3
from typing import Iterator

from .. import db

CODE_TTL = 600
MAX_ATTEMPTS = 5
EMAIL_INTERVAL = 60
EMAIL_PER_HOUR = 5
IP_PER_HOUR = 20
HOUR = 3600
DIGITS = "0123456789"


class RateLimited(Exception):
    def __init__(self, scope: str, retry_after: int) -> None:
        super().__init__(f"{scope}: retry after {retry_after}s")
        self.scope = scope
        self.retry_after = max(1, retry_after)


def _digest(secret: str, email: str, code: str) -> str:
    return hmac.new(secret.encode(), f"{email}:{code}".encode(), hashlib.sha256).hexdigest()


def _limits(conn: sqlite3.Connection, email: str, ip: str | None, ts: int) -> Iterator[tuple[str, int]]:
    """(scope, seconds to wait) for each limit that a new code for `email` from `ip` would break at `ts`."""
    since = ts - HOUR
    last = conn.execute(
        "SELECT MAX(created_at) AS last FROM login_codes WHERE email = ?", (email,)
    ).fetchone()["last"]
    if last is not None and ts - last < EMAIL_INTERVAL:
        yield "email_interval", EMAIL_INTERVAL - (ts - last)
    recent = conn.execute(
        "SELECT created_at FROM login_codes WHERE email = ? AND created_at > ? ORDER BY created_at",
        (email, since),
    ).fetchall()
    if len(recent) >= EMAIL_PER_HOUR:
        yield "email_hour", recent[0]["created_at"] + HOUR - ts
    if ip is None:
        return
    recent = conn.execute(
        "SELECT created_at FROM login_codes WHERE ip = ? AND created_at > ? ORDER BY created_at",
        (ip, since),
    ).fetchall()
    if len(recent) >= IP_PER_HOUR:
        yield "ip", recent[0]["created_at"] + HOUR - ts


def seconds_until_resend(conn: sqlite3.Connection, email: str, now: int | None = None, *, ip: str | None = None) -> int:
    """How long until issue_code would send `email` (from `ip`, when given) another code; 0 means now."""
    ts = db.now() if now is None else now
    return max((wait for _, wait in _limits(conn, email, ip, ts)), default=0)


def issue_code(conn: sqlite3.Connection, secret: str, email: str, ip: str, now: int | None = None) -> str:
    ts = db.now() if now is None else now
    code = f"{secrets.randbelow(1_000_000):06d}"
    # The limit checks and the insert share one BEGIN IMMEDIATE transaction. SQLite then admits one
    # writer at a time, so concurrent requests (one connection each) cannot all pass the checks
    # before any of them inserts. RateLimited raised in here rolls the transaction back untouched.
    with db.transaction(conn):
        for scope, wait in _limits(conn, email, ip, ts):
            raise RateLimited(scope, wait)
        conn.execute("DELETE FROM login_codes WHERE created_at < ?", (ts - 86400,))
        conn.execute(
            "UPDATE login_codes SET consumed_at = ? WHERE email = ? AND consumed_at IS NULL", (ts, email)
        )
        conn.execute(
            "INSERT INTO login_codes (email, code_hash, ip, created_at, expires_at) VALUES (?, ?, ?, ?, ?)",
            (email, _digest(secret, email, code), ip, ts, ts + CODE_TTL),
        )
    return code


def verify_code(conn: sqlite3.Connection, secret: str, email: str, code: str, now: int | None = None) -> bool:
    ts = db.now() if now is None else now
    digits = "".join(ch for ch in code if ch in DIGITS)
    with db.transaction(conn):
        row = conn.execute(
            "SELECT id, code_hash, attempts FROM login_codes"
            " WHERE email = ? AND consumed_at IS NULL AND expires_at > ?"
            " ORDER BY created_at DESC LIMIT 1",
            (email, ts),
        ).fetchone()
        if row is None:
            return False
        if len(digits) == 6 and hmac.compare_digest(row["code_hash"], _digest(secret, email, digits)):
            conn.execute("UPDATE login_codes SET consumed_at = ? WHERE id = ?", (ts, row["id"]))
            return True
        attempts = row["attempts"] + 1
        conn.execute(
            "UPDATE login_codes SET attempts = ?,"
            " consumed_at = CASE WHEN ? >= ? THEN ? ELSE NULL END WHERE id = ?",
            (attempts, attempts, MAX_ATTEMPTS, ts, row["id"]),
        )
        return False
