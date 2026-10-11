import pytest
from fastapi.testclient import TestClient

from app import db
from app.auth.mailer import ConsoleMailer
from app.config import Settings
from app.main import create_app
from tests.helpers import ADMIN, APP, FakeGoogle


@pytest.fixture
def settings(tmp_path):
    return Settings(
        data_dir=tmp_path / "data",
        admin_emails=frozenset({ADMIN}),
        google_client_id="test-client.apps.googleusercontent.com",
        max_upload_mb=5,
        max_files=50,
        max_file_mb=2,
    )


@pytest.fixture
def mailer():
    return ConsoleMailer()


@pytest.fixture
def google():
    return FakeGoogle()


@pytest.fixture
def app(settings, mailer, google):
    return create_app(settings, mailer=mailer, google=google)


@pytest.fixture
def client(app):
    with TestClient(app, base_url=APP) as c:
        c.headers["origin"] = APP
        yield c


@pytest.fixture
def conn(app, settings):
    c = db.connect(settings.db_path)
    yield c
    c.close()
