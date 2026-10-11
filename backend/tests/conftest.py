import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app

APP = "http://localhost:8000"


@pytest.fixture
def settings(tmp_path):
    return Settings(data_dir=tmp_path / "data")


@pytest.fixture
def app(settings):
    return create_app(settings)


@pytest.fixture
def client(app):
    with TestClient(app, base_url=APP) as c:
        c.headers["origin"] = APP
        yield c
