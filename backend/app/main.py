"""Application factory. Run with: uvicorn app.main:create_app --factory"""
from __future__ import annotations

import logging
from urllib.parse import quote

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, PlainTextResponse
from starlette.concurrency import run_in_threadpool
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.responses import Response
from starlette.staticfiles import StaticFiles

from . import db, i18n
from .auth import sessions
from .auth.google import GoogleVerifier
from .auth.mailer import make_mailer
from .config import FRONTEND_DIR, Settings, load_settings
from .deploys.store import SiteStore
from .deps import Forbidden, NotFound, NotSignedIn
from .devsites import serve_site_file
from .routes import admin, auth, internal, pages, sites
from .web import redirect, render, wants_json

ERROR_KEYS = {403: "error.forbidden", 404: "error.not_found", 405: "error.not_found"}
# Deployed sites are same-site with the app, so a framed app page would get the session cookie and its
# forms would pass the Origin check: an evil site could overlay one and steal a click. Never be framed.
NO_FRAMING = {"X-Frame-Options": "DENY", "Content-Security-Policy": "frame-ancestors 'none'"}


class CachedStatic(StaticFiles):
    """Assets are linked with ?v=<content hash>, so browsers may keep them for a year."""

    async def get_response(self, path: str, scope) -> Response:
        response = await super().get_response(path, scope)
        if response.status_code == 200:
            response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        return response


def create_app(settings: Settings | None = None, *, mailer=None, google=None) -> FastAPI:
    settings = settings or load_settings()
    logging.basicConfig(level=logging.INFO)
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    app.state.settings = settings
    app.state.mailer = mailer or make_mailer(settings)
    app.state.google = google or GoogleVerifier(settings.google_client_id)
    app.state.store = SiteStore(settings.sites_dir, settings.tmp_dir)
    app.state.store.cleanup_temp()
    conn = db.connect(settings.db_path)
    try:
        db.init_db(conn)
    finally:
        conn.close()

    app.mount("/static", CachedStatic(directory=FRONTEND_DIR / "static"), name="static")
    for module in (pages, auth, sites, admin, internal):
        app.include_router(module.router)

    @app.get("/healthz", include_in_schema=False)
    def healthz() -> PlainTextResponse:
        return PlainTextResponse("ok")

    @app.middleware("http")
    async def request_context(request: Request, call_next) -> Response:
        host = request.headers.get("host", "").lower()
        if (
            settings.serve_sites_in_app
            and host != settings.app_domain
            and host.endswith("." + settings.sites_domain)
        ):
            name = host[: -len(settings.sites_domain) - 1]
            return await run_in_threadpool(serve_site_file, settings, name, request.url.path)
        if not sessions.origin_allowed(request.method, request.headers, settings.app_origin):
            response = PlainTextResponse("Cross-origin request blocked", status_code=403)
        else:
            query_lang = request.query_params.get("lang")
            request.state.lang = i18n.negotiate(
                query_lang, request.cookies.get(i18n.LANG_COOKIE), request.headers.get("accept-language")
            )
            response = await call_next(request)
            if query_lang in i18n.LANGS:
                response.set_cookie(
                    i18n.LANG_COOKIE, query_lang, max_age=365 * 86400, path="/",
                    secure=settings.https, httponly=True, samesite="lax",
                )
        response.headers.update(NO_FRAMING)
        return response

    @app.exception_handler(NotSignedIn)
    async def handle_not_signed_in(request: Request, exc: NotSignedIn) -> Response:
        if wants_json(request):
            return JSONResponse({"error": i18n.t(_lang(request), "error.signed_out")}, status_code=401)
        target = request.url.path if request.method == "GET" else "/"
        return redirect(f"/login?next={quote(target)}")

    @app.exception_handler(Forbidden)
    async def handle_forbidden(request: Request, exc: Forbidden) -> Response:
        return _error(request, 403, "error.forbidden")

    @app.exception_handler(NotFound)
    async def handle_not_found(request: Request, exc: NotFound) -> Response:
        return _error(request, 404, "error.not_found")

    @app.exception_handler(StarletteHTTPException)
    async def handle_http(request: Request, exc: StarletteHTTPException) -> Response:
        return _error(request, exc.status_code, ERROR_KEYS.get(exc.status_code, "error.generic"))

    @app.exception_handler(RequestValidationError)
    async def handle_validation(request: Request, exc: RequestValidationError) -> Response:
        return _error(request, 400, "error.generic")

    return app


def _lang(request: Request) -> str:
    return getattr(request.state, "lang", i18n.DEFAULT_LANG)


def _error(request: Request, status: int, key: str) -> Response:
    request.state.lang = _lang(request)
    message = i18n.t(request.state.lang, key)
    if wants_json(request):
        return JSONResponse({"error": message}, status_code=status)
    return render(request, "error.html", status=status, message=message, code=status)
