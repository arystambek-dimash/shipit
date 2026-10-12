"""Shared test helpers (import as `from tests.helpers import ...`)."""
import io
import re
import zipfile

from fastapi.testclient import TestClient

from app import users
from app.auth.google import GoogleAuthError, GoogleIdentity
from app.deploys import service
from app.deploys.ingest import Limits, stage_files

APP = "http://localhost:8000"
ADMIN = "admin@zerde.ai"


class FakeGoogle:
    def __init__(self) -> None:
        self.tokens: dict[str, GoogleIdentity] = {}

    def verify(self, token: str) -> GoogleIdentity:
        try:
            return self.tokens[token]
        except KeyError:
            raise GoogleAuthError("unknown token") from None


def new_client(app) -> TestClient:
    client = TestClient(app, base_url=APP)
    client.headers["origin"] = APP
    return client


def last_code(mailer) -> str:
    return re.search(r"\b(\d{6})\b", mailer.sent[-1].body).group(1)


def sign_in(client, mailer, email: str) -> None:
    response = client.post("/auth/email/start", data={"email": email, "next": "/"}, follow_redirects=False)
    assert response.status_code == 303, response.text
    response = client.post("/auth/email/verify", data={"code": last_code(mailer), "next": "/"}, follow_redirects=False)
    assert response.status_code == 303, response.text


def make_zip(files: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, data in files.items():
            archive.writestr(name, data)
    return buffer.getvalue()


def upload_zip(client, files: dict[str, bytes], url: str = "/deploy", json: bool = True):
    headers = {"accept": "application/json"} if json else {}
    return client.post(
        url, files={"files": ("site.zip", make_zip(files), "application/zip")}, headers=headers, follow_redirects=False
    )


def publish_site(app, conn, files: dict[str, bytes] | None = None) -> str:
    store = app.state.store
    files = files or {"index.html": b"hello"}
    staged = stage_files([(path, io.BytesIO(data)) for path, data in files.items()], store.new_staging_dir(), Limits())
    owner = users.upsert_user(conn, ADMIN)
    name, _ = service.publish(conn, store, site=None, user_id=owner["id"], staged=staged)
    return name
