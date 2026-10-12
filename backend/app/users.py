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
    """Find or create the user for an email the caller has just verified, and record the sign-in.

    Access is decided on the stored email, so after this returns that email is the one verified now:
    - A row that already has this email wins. A Google account signing in takes over the google_sub
      link from any other row, because the link must point at whatever the account last proved it owns;
      the row it leaves loses its sessions, since the account no longer proves that row's address.
    - Otherwise a row linked to this Google account (google_sub) gets the new email: the address
      changed at Google. Its sessions end, since they were opened under the old address.
    - Otherwise a new row is created.
    """
    ts = db.now() if now is None else now
    email = email.strip().lower()
    with db.transaction(conn):
        by_email = conn.execute("SELECT id, google_sub FROM users WHERE email = ?", (email,)).fetchone()
        by_sub = None
        if google_sub:
            by_sub = conn.execute("SELECT id FROM users WHERE google_sub = ?", (google_sub,)).fetchone()
        if by_email is not None:
            user_id = by_email["id"]
            if google_sub and by_email["google_sub"] != google_sub:
                if by_sub is not None:
                    conn.execute("UPDATE users SET google_sub = NULL WHERE id = ?", (by_sub["id"],))
                    conn.execute("DELETE FROM sessions WHERE user_id = ?", (by_sub["id"],))
                conn.execute("UPDATE users SET google_sub = ? WHERE id = ?", (google_sub, user_id))
        elif by_sub is not None:
            user_id = by_sub["id"]
            conn.execute("UPDATE users SET email = ? WHERE id = ?", (email, user_id))
            conn.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))
        else:
            user_id = conn.execute(
                "INSERT INTO users (email, name, avatar_url, google_sub, created_at, last_login_at)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (email, name, avatar_url, google_sub, ts, ts),
            ).lastrowid
        conn.execute(
            "UPDATE users SET"
            " name = CASE WHEN ? != '' THEN ? ELSE name END,"
            " avatar_url = CASE WHEN ? != '' THEN ? ELSE avatar_url END,"
            " last_login_at = ?"
            " WHERE id = ?",
            (name, name, avatar_url, avatar_url, ts, user_id),
        )
    user = get_user(conn, user_id)
    assert user is not None
    return user
