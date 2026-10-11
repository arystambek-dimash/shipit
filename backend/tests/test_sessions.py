import pytest
from starlette.responses import Response

from app import db, users
from app.auth import sessions
from app.config import Settings


@pytest.fixture
def conn(tmp_path):
    c = db.connect(tmp_path / "app.db")
    db.init_db(c)
    yield c
    c.close()


def test_session_lifecycle(conn):
    user = users.upsert_user(conn, "a@b.co", now=1)
    token = sessions.create_session(conn, user["id"], now=100)
    stored = conn.execute("SELECT token_hash FROM sessions").fetchone()["token_hash"]
    assert stored != token and len(stored) == 64
    found = sessions.user_for_token(conn, token, now=200)
    assert found["id"] == user["id"] and found["email"] == "a@b.co"
    assert sessions.user_for_token(conn, "wrong", now=200) is None
    sessions.delete_session(conn, token)
    assert sessions.user_for_token(conn, token, now=200) is None


def test_expired_session_is_removed(conn):
    user = users.upsert_user(conn, "a@b.co", now=1)
    token = sessions.create_session(conn, user["id"], now=100)
    assert sessions.user_for_token(conn, token, now=100 + sessions.SESSION_TTL) is None
    assert conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 0


@pytest.mark.parametrize("https,name,secure", [(False, "zd_session", False), (True, "__Host-zd_session", True)])
def test_cookie_flags(https, name, secure):
    settings = Settings(https=https, secret_key="s" * 40)
    response = Response()
    sessions.set_session_cookie(response, settings, "tok")
    header = response.headers["set-cookie"]
    assert header.startswith(f"{name}=tok;")
    assert "HttpOnly" in header and "SameSite=lax" in header and "Path=/" in header
    assert ("Secure" in header) is secure
    assert "Domain" not in header


ORIGIN = "https://deploy.zerde.ai"


@pytest.mark.parametrize(
    "method,headers,allowed",
    [
        ("GET", {}, True),
        ("HEAD", {"origin": "https://evil.test"}, True),
        ("POST", {"origin": ORIGIN}, True),
        ("POST", {"origin": "https://site.deploy.zerde.ai"}, False),
        ("POST", {"origin": "null"}, False),
        ("POST", {"referer": ORIGIN + "/sites/x"}, True),
        ("POST", {"referer": ORIGIN + ".evil.test/"}, False),
        ("DELETE", {}, False),
    ],
)
def test_origin_allowed(method, headers, allowed):
    assert sessions.origin_allowed(method, headers, ORIGIN) is allowed
