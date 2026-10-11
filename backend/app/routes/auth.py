"""Sign-in pages and actions: Google ID token, or email + 6-digit code."""
from __future__ import annotations

import logging
import sqlite3
from urllib.parse import quote

from fastapi import APIRouter, BackgroundTasks, Depends, Form, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from starlette.responses import Response

from .. import access, i18n, signing, users
from ..auth import email_code, sessions
from ..auth.google import GoogleAuthError
from ..auth.mailer import Mailer, Message
from ..deps import current_user, get_conn
from ..web import redirect, render, safe_next, settings_of

log = logging.getLogger("zerde.auth")
router = APIRouter()
PENDING_COOKIE = "zd_pending"
PENDING_TTL = 15 * 60


class GoogleLogin(BaseModel):
    credential: str
    next: str = "/"


def client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def pending_email(request: Request) -> str | None:
    raw = request.cookies.get(PENDING_COOKIE)
    return signing.unsign(raw, settings_of(request).secret_key, PENDING_TTL) if raw else None


def send_code(mailer: Mailer, email: str, code: str, lang: str, domain: str) -> None:
    message = Message(
        to=email,
        subject=i18n.t(lang, "email.subject", code=code),
        body=i18n.t(lang, "email.body", code=code, domain=domain),
    )
    try:
        mailer.send(message)
    except Exception:
        log.exception("Could not send a sign-in code to %s", email)


def start_session(request: Request, conn: sqlite3.Connection, user: sqlite3.Row, response: Response) -> Response:
    sessions.set_session_cookie(response, settings_of(request), sessions.create_session(conn, user["id"]))
    return response


@router.get("/login")
def login_page(
    request: Request, next_url: str = Query("/", alias="next"), user: sqlite3.Row | None = Depends(current_user)
) -> Response:
    target = safe_next(next_url)
    if user is not None:
        return redirect(target)
    return render(request, "login.html", next=target, email="", error=None)


@router.get("/login/code")
def code_page(
    request: Request, next_url: str = Query("/", alias="next"), conn: sqlite3.Connection = Depends(get_conn)
) -> Response:
    email = pending_email(request)
    if email is None:
        return redirect("/login")
    return render(
        request, "code.html", email=email, next=safe_next(next_url), error=None,
        resend_wait=email_code.seconds_until_resend(conn, email),
    )


@router.post("/auth/email/start")
def email_start(
    request: Request,
    background: BackgroundTasks,
    email: str = Form(""),
    next_url: str = Form("/", alias="next"),
    conn: sqlite3.Connection = Depends(get_conn),
) -> Response:
    settings = settings_of(request)
    lang = request.state.lang
    target = safe_next(next_url)
    address = access.normalize_email(email)
    if address is None:
        return render(request, "login.html", status=400, next=target, email=email, error=i18n.t(lang, "error.email_invalid"))
    try:
        code = email_code.issue_code(conn, settings.secret_key, address, client_ip(request))
    except email_code.RateLimited as exc:
        if exc.scope != "email_interval":
            error = i18n.t(lang, "error.rate_limited", s=exc.retry_after)
            return render(request, "login.html", status=429, next=target, email=email, error=error)
    else:
        background.add_task(send_code, request.app.state.mailer, address, code, lang, settings.app_domain)
    response = redirect(f"/login/code?next={quote(target)}")
    response.set_cookie(
        PENDING_COOKIE, signing.sign(address, settings.secret_key), max_age=PENDING_TTL, path="/",
        secure=settings.https, httponly=True, samesite="lax",
    )
    return response


@router.post("/auth/email/verify")
def email_verify(
    request: Request,
    code: str = Form(""),
    next_url: str = Form("/", alias="next"),
    conn: sqlite3.Connection = Depends(get_conn),
) -> Response:
    settings = settings_of(request)
    target = safe_next(next_url)
    email = pending_email(request)
    if email is None:
        return redirect("/login")
    if not email_code.verify_code(conn, settings.secret_key, email, code):
        return render(
            request, "code.html", status=400, email=email, next=target,
            error=i18n.t(request.state.lang, "error.code_invalid"),
            resend_wait=email_code.seconds_until_resend(conn, email),
        )
    response = redirect(target)
    response.delete_cookie(PENDING_COOKIE, path="/")
    return start_session(request, conn, users.upsert_user(conn, email), response)


@router.post("/auth/email/resend")
def email_resend(
    request: Request,
    background: BackgroundTasks,
    next_url: str = Form("/", alias="next"),
    conn: sqlite3.Connection = Depends(get_conn),
) -> Response:
    settings = settings_of(request)
    lang = request.state.lang
    target = safe_next(next_url)
    email = pending_email(request)
    if email is None:
        return redirect("/login")
    try:
        code = email_code.issue_code(conn, settings.secret_key, email, client_ip(request))
    except email_code.RateLimited as exc:
        return render(
            request, "code.html", status=429, email=email, next=target,
            error=i18n.t(lang, "error.rate_limited", s=exc.retry_after),
            resend_wait=email_code.seconds_until_resend(conn, email),
        )
    background.add_task(send_code, request.app.state.mailer, email, code, lang, settings.app_domain)
    return redirect(f"/login/code?next={quote(target)}", settings=settings, flash=("ok", i18n.t(lang, "code.sent")))


@router.post("/auth/google")
def google_login(request: Request, body: GoogleLogin, conn: sqlite3.Connection = Depends(get_conn)) -> Response:
    try:
        identity = request.app.state.google.verify(body.credential)
    except GoogleAuthError:
        return JSONResponse({"error": i18n.t(request.state.lang, "error.google_failed")}, status_code=400)
    user = users.upsert_user(
        conn, identity.email, name=identity.name, avatar_url=identity.picture, google_sub=identity.sub
    )
    return start_session(request, conn, user, JSONResponse({"redirect": safe_next(body.next)}))


@router.post("/auth/logout")
def logout(request: Request, conn: sqlite3.Connection = Depends(get_conn)) -> Response:
    settings = settings_of(request)
    token = request.cookies.get(settings.session_cookie)
    if token:
        sessions.delete_session(conn, token)
    response = redirect("/login")
    sessions.clear_session_cookie(response, settings)
    return response
