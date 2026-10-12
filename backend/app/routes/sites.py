"""Upload, site page and site actions."""
from __future__ import annotations

import logging
import sqlite3
from pathlib import Path

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import UploadFile
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.responses import Response

from .. import access, i18n
from ..config import MB, Settings
from ..deploys import ingest, service
from ..deploys.ingest import Limits, StagedTree, UploadError
from ..deploys.store import SiteStore
from ..deps import NotFound, get_conn, require_deployer
from ..web import redirect, render, set_flash, settings_of, wants_json

log = logging.getLogger("zerde.deploys")
router = APIRouter()


def limits_for(settings: Settings) -> Limits:
    return Limits(
        max_total_bytes=settings.max_upload_mb * MB,
        max_files=settings.max_files,
        max_file_bytes=settings.max_file_mb * MB,
    )


def managed_site(request: Request, conn: sqlite3.Connection, user: sqlite3.Row, name: str) -> sqlite3.Row:
    site = service.get_site(conn, name)
    admin = access.is_admin(settings_of(request), user["email"])
    if site is None or not service.can_manage(site, user, admin):
        raise NotFound()
    return site


def stage_upload(pairs: list[tuple[UploadFile, str]], dest: Path, limits: Limits) -> StagedTree:
    if not pairs:
        raise UploadError("empty")
    if len(pairs) > limits.max_files:
        raise UploadError("too_many_files", n=limits.max_files)
    first = pairs[0][0]
    if len(pairs) == 1 and (first.filename or "").lower().endswith(".zip"):
        return ingest.stage_zip(first.file, dest, limits)
    return ingest.stage_files([(path or upload.filename or "", upload.file) for upload, path in pairs], dest, limits)


def stage_and_publish(
    store: SiteStore, conn: sqlite3.Connection, pairs: list[tuple[UploadFile, str]], limits: Limits,
    site: sqlite3.Row | None, user_id: int,
) -> tuple[str, int]:
    """Runs in a worker thread: staging, publishing and removing a failed upload's staged tree all hit the disk."""
    with store.staging() as staging:
        staged = stage_upload(pairs, staging, limits)
        return service.publish(conn, store, site=site, user_id=user_id, staged=staged)


def upload_failed(request: Request, error: UploadError, back: str, status: int = 400) -> Response:
    message = i18n.t(request.state.lang, f"upload.error.{error.code}", **error.params)
    if wants_json(request):
        return JSONResponse({"error": message}, status_code=status)
    return redirect(back, settings=settings_of(request), flash=("error", message))


async def handle_upload(
    request: Request, conn: sqlite3.Connection, user: sqlite3.Row, site: sqlite3.Row | None
) -> Response:
    settings = settings_of(request)
    store = request.app.state.store
    limits = limits_for(settings)
    back = f"/sites/{site['name']}" if site else "/"
    # request.form() spools the whole multipart body to temp files before ingest can count a byte of it, so the
    # size has to be known and within the cap up front. Browsers always send Content-Length for FormData; a
    # chunked or malformed request is refused instead of being read without bound.
    declared = request.headers.get("content-length", "")
    if not (declared.isascii() and declared.isdigit()) or int(declared) > limits.max_total_bytes + MB:
        return upload_failed(request, UploadError("too_big", mb=settings.max_upload_mb), back)
    try:
        form = await request.form(max_files=limits.max_files + 1, max_fields=limits.max_files + 32)
    except StarletteHTTPException:
        return upload_failed(request, UploadError("too_many_files", n=limits.max_files), back)
    try:
        uploads = form.getlist("files")
        paths = [str(path) for path in form.getlist("paths")]
        pairs = [
            (upload, paths[index] if index < len(paths) else "")
            for index, upload in enumerate(uploads)
            if isinstance(upload, UploadFile) and upload.filename
        ]
        name, deploy_id = await run_in_threadpool(stage_and_publish, store, conn, pairs, limits, site, user["id"])
    except UploadError as error:
        return upload_failed(request, error, back)
    except service.SiteError:  # renamed or deleted while the upload ran: nothing was published
        return upload_failed(request, UploadError("site_gone"), "/", status=404)
    except OSError:  # a full disk, permissions: the operator needs the traceback, the user a plain answer
        log.exception("could not stage or publish an upload by %s", user["email"])
        return upload_failed(request, UploadError("server_error"), back, status=500)
    finally:
        await form.close()
    message = i18n.t(request.state.lang, "flash.deployed")
    if wants_json(request):
        response = JSONResponse(
            {"name": name, "url": settings.site_url(name), "deploy_id": deploy_id, "manage_url": f"/sites/{name}"},
            status_code=201,
        )
        set_flash(response, settings, "ok", message)
        return response
    return redirect(f"/sites/{name}", settings=settings, flash=("ok", message))


@router.post("/deploy")
async def deploy_new(
    request: Request,
    user: sqlite3.Row = Depends(require_deployer),
    conn: sqlite3.Connection = Depends(get_conn),
) -> Response:
    return await handle_upload(request, conn, user, None)


@router.post("/sites/{name}/deploy")
async def deploy_version(
    name: str,
    request: Request,
    user: sqlite3.Row = Depends(require_deployer),
    conn: sqlite3.Connection = Depends(get_conn),
) -> Response:
    site = await run_in_threadpool(managed_site, request, conn, user, name)
    return await handle_upload(request, conn, user, site)


@router.get("/sites/{name}")
def site_page(
    name: str,
    request: Request,
    user: sqlite3.Row = Depends(require_deployer),
    conn: sqlite3.Connection = Depends(get_conn),
) -> Response:
    site = managed_site(request, conn, user, name)
    return render(
        request, "site.html", user=user, site=site,
        deploys=service.list_deploys(conn, site["id"]), url=settings_of(request).site_url(site["name"]),
    )


@router.post("/sites/{name}/restore")
def restore_version(
    name: str,
    request: Request,
    deploy_id: int = Form(...),
    user: sqlite3.Row = Depends(require_deployer),
    conn: sqlite3.Connection = Depends(get_conn),
) -> Response:
    site = managed_site(request, conn, user, name)
    try:
        service.restore(conn, request.app.state.store, site, deploy_id)
    except service.SiteError:
        raise NotFound() from None
    message = i18n.t(request.state.lang, "flash.restored")
    return redirect(f"/sites/{name}", settings=settings_of(request), flash=("ok", message))


@router.post("/sites/{name}/rename")
def rename_site(
    name: str,
    request: Request,
    new_name: str = Form(""),
    user: sqlite3.Row = Depends(require_deployer),
    conn: sqlite3.Connection = Depends(get_conn),
) -> Response:
    site = managed_site(request, conn, user, name)
    settings = settings_of(request)
    lang = request.state.lang
    try:
        renamed = service.rename(conn, request.app.state.store, site, new_name)
    except service.SiteError as error:
        return redirect(f"/sites/{name}", settings=settings, flash=("error", i18n.t(lang, f"error.{error.code}")))
    return redirect(f"/sites/{renamed}", settings=settings, flash=("ok", i18n.t(lang, "flash.renamed")))


@router.post("/sites/{name}/delete")
def delete_site(
    name: str,
    request: Request,
    confirm: str = Form(""),
    user: sqlite3.Row = Depends(require_deployer),
    conn: sqlite3.Connection = Depends(get_conn),
) -> Response:
    site = managed_site(request, conn, user, name)
    settings = settings_of(request)
    lang = request.state.lang
    if confirm.strip().lower() != site["name"]:
        return redirect(f"/sites/{name}", settings=settings, flash=("error", i18n.t(lang, "error.confirm_mismatch")))
    try:
        service.delete(conn, request.app.state.store, site)
    except service.SiteError:  # a concurrent delete or rename got there first
        raise NotFound() from None
    return redirect("/", settings=settings, flash=("ok", i18n.t(lang, "flash.deleted")))
