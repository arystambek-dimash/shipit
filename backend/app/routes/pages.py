"""Dashboard page."""
from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends, Request
from starlette.responses import Response

from .. import access
from ..deploys import service
from ..deps import current_user, get_conn
from ..web import redirect, render, settings_of

router = APIRouter()


@router.get("/")
def dashboard(
    request: Request,
    user: sqlite3.Row | None = Depends(current_user),
    conn: sqlite3.Connection = Depends(get_conn),
) -> Response:
    if user is None:
        return redirect("/login")
    if not access.can_deploy(conn, settings_of(request), user["email"]):
        return render(request, "noaccess.html", user=user)
    return render(request, "dashboard.html", user=user, sites=service.list_sites(conn, user["id"]))
