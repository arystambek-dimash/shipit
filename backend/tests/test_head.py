import asyncio

import pytest

from tests.helpers import APP


@pytest.mark.parametrize("path", ["/login", "/privacy", "/healthz", "/static/css/app.css", "/nope"])
def test_head_answers_like_get_without_a_body(client, path):
    got = client.get(path)
    head = client.head(path)
    assert head.status_code == got.status_code != 405
    assert head.headers == got.headers
    assert head.content == b""


def test_head_of_the_dashboard_redirects_to_login(client):
    head = client.head("/", follow_redirects=False)
    assert head.status_code == 303
    assert head.headers["location"] == "/login"
    assert head.content == b""


def test_head_does_not_open_post_only_routes(client):
    assert client.head("/auth/email/start").status_code == 405


def test_the_asgi_response_to_head_carries_no_body_bytes(app):
    # TestClient and uvicorn both drop a HEAD body themselves, so look at what the app itself sends.
    sent = []

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        sent.append(message)

    scope = {
        "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": "HEAD", "scheme": "http",
        "path": "/login", "raw_path": b"/login", "query_string": b"", "root_path": "",
        "headers": [(b"host", b"localhost:8000"), (b"origin", APP.encode())],
        "client": ("127.0.0.1", 50000), "server": ("localhost", 8000),
    }
    asyncio.run(app(scope, receive, send))

    start = next(m for m in sent if m["type"] == "http.response.start")
    assert start["status"] == 200
    assert dict(start["headers"])[b"content-length"] != b"0"
    bodies = [m for m in sent if m["type"] == "http.response.body"]
    assert bodies and all(m.get("body", b"") == b"" for m in bodies)
    assert not bodies[-1].get("more_body", False)
