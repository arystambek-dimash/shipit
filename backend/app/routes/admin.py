"""Allowlist administration."""
from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends, Form, Request
from starlette.responses import Response

from .. import access, i18n
from ..deploys import service
from ..deps import get_conn, require_admin
from ..web import redirect, render, settings_of

router = APIRouter()


@router.get("/admin")
def admin_page(
    request: Request, user: sqlite3.Row = Depends(require_admin), conn: sqlite3.Connection = Depends(get_conn)
) -> Response:
    return render(
        request, "admin.html", user=user,
        entries=access.list_allowlist(conn),
        admins=sorted(settings_of(request).admin_emails),
        sites=service.list_sites(conn),
    )


@router.post("/admin/allowlist")
def add_entry(
    request: Request,
    pattern: str = Form(""),
    user: sqlite3.Row = Depends(require_admin),
    conn: sqlite3.Connection = Depends(get_conn),
) -> Response:
    lang = request.state.lang
    normalized = access.normalize_pattern(pattern)
    if normalized is None:
        flash = ("error", i18n.t(lang, "error.pattern_invalid"))
    elif not access.add_to_allowlist(conn, normalized, user["email"]):
        flash = ("error", i18n.t(lang, "error.pattern_exists"))
    else:
        flash = ("ok", i18n.t(lang, "flash.added"))
    return redirect("/admin", settings=settings_of(request), flash=flash)


@router.post("/admin/allowlist/{entry_id}/delete")
def remove_entry(
    entry_id: int,
    request: Request,
    user: sqlite3.Row = Depends(require_admin),
    conn: sqlite3.Connection = Depends(get_conn),
) -> Response:
    access.remove_from_allowlist(conn, entry_id)
    message = i18n.t(request.state.lang, "flash.removed")
    return redirect("/admin", settings=settings_of(request), flash=("ok", message))
