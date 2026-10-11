"""User records. A user is identified by email; a Google account links via google_sub."""
from __future__ import annotations

import sqlite3

from . import db


def get_user(conn: sqlite3.Connection, user_id: int) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()


def upsert_user(
    conn: sqlite3.Connection,
    email: str,
    *,
    name: str = "",
    avatar_url: str = "",
    google_sub: str | None = None,
    now: int | None = None,
) -> sqlite3.Row:
    ts = db.now() if now is None else now
    email = email.strip().lower()
    with db.transaction(conn):
        row = None
        if google_sub:
            row = conn.execute("SELECT id FROM users WHERE google_sub = ?", (google_sub,)).fetchone()
        if row is None:
            row = conn.execute("SELECT id FROM users WHERE email = ?", (email,)).fetchone()
        if row is None:
            user_id = conn.execute(
                "INSERT INTO users (email, name, avatar_url, google_sub, created_at, last_login_at)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (email, name, avatar_url, google_sub, ts, ts),
            ).lastrowid
        else:
            user_id = row["id"]
            conn.execute(
                "UPDATE users SET"
                " name = CASE WHEN ? != '' THEN ? ELSE name END,"
                " avatar_url = CASE WHEN ? != '' THEN ? ELSE avatar_url END,"
                " google_sub = COALESCE(google_sub, ?),"
                " last_login_at = ?"
                " WHERE id = ?",
                (name, name, avatar_url, avatar_url, google_sub, ts, user_id),
            )
    user = get_user(conn, user_id)
    assert user is not None
    return user
