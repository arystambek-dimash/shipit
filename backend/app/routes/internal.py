"""Endpoints for Caddy on the private Docker network. Caddy answers 404 for /internal/* publicly."""
from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends, Request
from fastapi.responses import PlainTextResponse

from ..deploys import names, service
from ..deps import get_conn
from ..web import settings_of

router = APIRouter()


@router.get("/internal/tls-ask", include_in_schema=False)
def tls_ask(request: Request, domain: str = "", conn: sqlite3.Connection = Depends(get_conn)) -> PlainTextResponse:
    """Caddy on-demand TLS: issue a certificate only for sites that exist."""
    suffix = "." + settings_of(request).sites_domain.split(":")[0]
    host = domain.strip().lower().rstrip(".")
    if host.endswith(suffix):
        name = host[: -len(suffix)]
        if names.is_valid_name(name) and service.get_site(conn, name) is not None:
            return PlainTextResponse("ok")
    return PlainTextResponse("unknown", status_code=404)
