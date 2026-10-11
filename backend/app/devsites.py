"""Development only: serve deployed sites from the app. Production uses Caddy (deploys/sites.caddy)."""
from __future__ import annotations

from pathlib import Path
from urllib.parse import unquote

from starlette.responses import FileResponse, HTMLResponse, Response

from .config import FRONTEND_DIR, Settings
from .deploys import names

SITE_404 = FRONTEND_DIR / "static" / "site-404.html"
HEADERS = {"Cache-Control": "public, max-age=0, must-revalidate", "X-Content-Type-Options": "nosniff"}


def resolve_site_file(current: Path, url_path: str) -> Path | None:
    root = current.resolve()
    rel = unquote(url_path).lstrip("/")
    candidates = [rel, f"{rel}/index.html", f"{rel}.html"] if rel else ["index.html"]
    for candidate in candidates:
        path = (root / candidate).resolve()
        if path != root and root not in path.parents:
            return None
        if path.is_file():
            return path
    return None


def serve_site_file(settings: Settings, name: str, url_path: str) -> Response:
    current = settings.sites_dir / name / "current"
    if not names.is_valid_name(name) or not current.is_dir():
        return _not_found(None)
    path = resolve_site_file(current, url_path)
    return FileResponse(path, headers=HEADERS) if path else _not_found(current)


def _not_found(current: Path | None) -> Response:
    if current is not None and (current / "404.html").is_file():
        return FileResponse(current / "404.html", status_code=404, headers=HEADERS)
    return HTMLResponse(SITE_404.read_text(encoding="utf-8"), status_code=404)
