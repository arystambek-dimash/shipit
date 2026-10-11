"""Template rendering, redirects and one-shot flash messages."""
from __future__ import annotations

import hashlib
from functools import partial

from fastapi import Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from starlette.responses import Response

from . import access, db, i18n, signing
from .config import FRONTEND_DIR, MB, Settings

FLASH_COOKIE = "zd_flash"
FLASH_TTL = 60
templates = Jinja2Templates(directory=str(FRONTEND_DIR / "templates"))


def _asset_version() -> str:
    digest = hashlib.sha256()
    for path in sorted((FRONTEND_DIR / "static").rglob("*")):
        if path.is_file():
            digest.update(path.read_bytes())
    return digest.hexdigest()[:10]


ASSET_VERSION = _asset_version()


def settings_of(request: Request) -> Settings:
    return request.app.state.settings


def wants_json(request: Request) -> bool:
    return "application/json" in request.headers.get("accept", "")


def safe_next(value: str | None) -> str:
    """Return `value` only if it is a same-site absolute path, else "/".

    Control characters and whitespace are rejected outright: browsers strip tab, CR and LF
    from a URL before parsing it, so "/\\t/evil.test" would resolve to "//evil.test" when a
    script hands it to location.assign().
    """
    if (
        value
        and value.startswith("/")
        and not value.startswith("//")
        and "\\" not in value
        and all(ord(ch) > 0x20 and ord(ch) != 0x7F for ch in value)
    ):
        return value
    return "/"


def set_flash(response: Response, settings: Settings, kind: str, text: str) -> None:
    response.set_cookie(
        FLASH_COOKIE, signing.sign(f"{kind}:{text}", settings.secret_key), max_age=FLASH_TTL, path="/",
        secure=settings.https, httponly=True, samesite="lax",
    )


def pop_flash(request: Request) -> dict[str, str] | None:
    raw = request.cookies.get(FLASH_COOKIE)
    if not raw:
        return None
    value = signing.unsign(raw, settings_of(request).secret_key, max_age=FLASH_TTL)
    if not value or ":" not in value:
        return None
    kind, text = value.split(":", 1)
    return {"kind": kind, "text": text}


def redirect(url: str, *, settings: Settings | None = None, flash: tuple[str, str] | None = None) -> RedirectResponse:
    response = RedirectResponse(url, status_code=303)
    if flash is not None and settings is not None:
        set_flash(response, settings, *flash)
    return response


def js_strings(lang: str, settings: Settings) -> dict[str, object]:
    t = partial(i18n.t, lang)
    return {
        "copied": t("js.copied"),
        "reading": t("js.reading"),
        "uploading": t("js.uploading"),
        "processing": t("js.processing"),
        "failed": t("js.failed"),
        "empty": t("upload.error.empty"),
        "tooBig": t("upload.error.too_big", mb=settings.max_upload_mb),
        "tooMany": t("upload.error.too_many_files", n=settings.max_files),
        "maxBytes": settings.max_upload_mb * MB,
        "maxFiles": settings.max_files,
        "units": [t("unit.b"), t("unit.kb"), t("unit.mb"), t("unit.gb")],
        "dec": "." if lang == "en" else ",",
    }


def render(request: Request, template: str, *, status: int = 200, user=None, **context: object) -> HTMLResponse:
    settings = settings_of(request)
    lang = getattr(request.state, "lang", i18n.DEFAULT_LANG)
    flash = pop_flash(request)
    base = {
        "lang": lang,
        "langs": i18n.LANGS,
        "lang_labels": i18n.LANG_LABELS,
        "t": partial(i18n.t, lang),
        "size": partial(i18n.format_size, lang),
        "ago": lambda ts: i18n.format_ago(lang, max(0, db.now() - int(ts or 0))),
        "settings": settings,
        "asset_v": ASSET_VERSION,
        "flash": flash,
        "user": user,
        "is_admin": bool(user) and access.is_admin(settings, user["email"]),
        "js_strings": js_strings(lang, settings),
        "path": request.url.path,
    }
    response = templates.TemplateResponse(request, template, {**base, **context}, status_code=status)
    if flash:
        response.delete_cookie(FLASH_COOKIE, path="/")
    return response
