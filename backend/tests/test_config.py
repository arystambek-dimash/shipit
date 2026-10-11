import pytest

from app.config import load_settings, read_dotenv


def test_defaults_are_local_development():
    s = load_settings({})
    assert s.app_origin == "http://localhost:8000"
    assert s.session_cookie == "zd_session"
    assert s.serve_sites_in_app is True
    assert s.site_url("demo") == "http://demo.localhost:8000/"
    assert s.db_path.name == "app.db"


def test_production_values():
    s = load_settings(
        {
            "APP_DOMAIN": "deploy.zerde.ai",
            "SITES_DOMAIN": "deploy.zerde.ai",
            "HTTPS": "true",
            "SECRET_KEY": "x" * 40,
            "ADMIN_EMAILS": " A@Zerde.ai, b@x.com ,",
            "GOOGLE_CLIENT_ID": " abc.apps.googleusercontent.com ",
        }
    )
    assert s.app_origin == "https://deploy.zerde.ai"
    assert s.session_cookie == "__Host-zd_session"
    assert s.serve_sites_in_app is False
    assert s.admin_emails == frozenset({"a@zerde.ai", "b@x.com"})
    assert s.google_client_id == "abc.apps.googleusercontent.com"
    assert s.site_url("demo") == "https://demo.deploy.zerde.ai/"


def test_https_requires_a_real_secret():
    with pytest.raises(ValueError):
        load_settings({"HTTPS": "true"})


def test_read_dotenv(tmp_path):
    path = tmp_path / ".env"
    path.write_text('# comment\nAPP_DOMAIN="x.test"\nEMPTY=\n\nNOEQUALS\n', encoding="utf-8")
    assert read_dotenv(path) == {"APP_DOMAIN": "x.test", "EMPTY": ""}
    assert read_dotenv(tmp_path / "missing") == {}
