"""FastAPI dependencies shared by the route modules."""
from __future__ import annotations

import sqlite3
from typing import Iterator

from fastapi import Depends, Request

from . import access, db
from .auth import sessions
from .web import settings_of


class NotSignedIn(Exception):
    """No valid session."""


class Forbidden(Exception):
    """Signed in, but not allowed."""


class NotFound(Exception):
    """Missing, or not visible to this user."""


def get_conn(request: Request) -> Iterator[sqlite3.Connection]:
    conn = db.connect(settings_of(request).db_path)
    try:
        yield conn
    finally:
        conn.close()


def current_user(request: Request, conn: sqlite3.Connection = Depends(get_conn)) -> sqlite3.Row | None:
    token = request.cookies.get(settings_of(request).session_cookie)
    return sessions.user_for_token(conn, token) if token else None


def require_user(user: sqlite3.Row | None = Depends(current_user)) -> sqlite3.Row:
    if user is None:
        raise NotSignedIn()
    return user


def require_deployer(
    request: Request, user: sqlite3.Row = Depends(require_user), conn: sqlite3.Connection = Depends(get_conn)
) -> sqlite3.Row:
    if not access.can_deploy(conn, settings_of(request), user["email"]):
        raise Forbidden()
    return user


def require_admin(request: Request, user: sqlite3.Row = Depends(require_user)) -> sqlite3.Row:
    if not access.is_admin(settings_of(request), user["email"]):
        raise Forbidden()
    return user
