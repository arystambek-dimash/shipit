"""Who may deploy: admins from settings, plus an allowlist of emails and @domains."""
from __future__ import annotations

import re
import sqlite3

from . import db
from .config import Settings

_LABEL = r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?"
DOMAIN_RE = re.compile(rf"^{_LABEL}(?:\.{_LABEL})+$")
EMAIL_RE = re.compile(rf"^[^@\s]{{1,64}}@{_LABEL}(?:\.{_LABEL})+$")


def normalize_email(raw: str) -> str | None:
    email = raw.strip().lower()
    return email if len(email) <= 254 and EMAIL_RE.match(email) else None


def normalize_pattern(raw: str) -> str | None:
    value = raw.strip().lower()
    if value.startswith("@"):
        return value if DOMAIN_RE.match(value[1:]) else None
    return normalize_email(value)


def is_admin(settings: Settings, email: str) -> bool:
    return email.strip().lower() in settings.admin_emails


def can_deploy(conn: sqlite3.Connection, settings: Settings, email: str) -> bool:
    email = email.strip().lower()
    if is_admin(settings, email):
        return True
    # Single authorisation gate: anything that is not a well-formed email is
    # refused before a domain is derived (no "@" would make rsplit return the
    # whole string and match a "@domain" entry).
    if normalize_email(email) is None:
        return False
    domain = "@" + email.rsplit("@", 1)[-1]
    row = conn.execute(
        "SELECT 1 FROM allowlist WHERE pattern IN (?, ?) LIMIT 1", (email, domain)
    ).fetchone()
    return row is not None


def list_allowlist(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute("SELECT * FROM allowlist ORDER BY pattern").fetchall()


def add_to_allowlist(
    conn: sqlite3.Connection, pattern: str, added_by: str, now: int | None = None
) -> bool:
    try:
        conn.execute(
            "INSERT INTO allowlist (pattern, added_by, created_at) VALUES (?, ?, ?)",
            (pattern, added_by, db.now() if now is None else now),
        )
    except sqlite3.IntegrityError:
        return False
    return True


def remove_from_allowlist(conn: sqlite3.Connection, entry_id: int) -> bool:
    return conn.execute("DELETE FROM allowlist WHERE id = ?", (entry_id,)).rowcount > 0
