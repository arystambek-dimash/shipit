"""Application factory. Run with: uvicorn app.main:create_app --factory"""
from __future__ import annotations

from fastapi import FastAPI
from fastapi.responses import PlainTextResponse

from .config import Settings, load_settings


def create_app(settings: Settings | None = None, *, mailer=None, google=None) -> FastAPI:
    settings = settings or load_settings()
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    app.state.settings = settings
    app.state.mailer = mailer
    app.state.google = google

    @app.get("/healthz", include_in_schema=False)
    def healthz() -> PlainTextResponse:
        return PlainTextResponse("ok")

    return app
