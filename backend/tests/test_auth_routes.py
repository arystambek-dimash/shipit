import dataclasses
import re

from app.auth.google import GoogleIdentity
from tests.helpers import ADMIN, last_code, sign_in

JSON = {"accept": "application/json"}


def test_login_page(client):
    response = client.get("/login")
    assert response.status_code == 200
    assert 'id="g-btn"' in response.text and "accounts.google.com/gsi/client" in response.text
    assert 'name="email"' in response.text and "<h1>Sign in</h1>" in response.text


def test_login_page_without_google(client, app):
    app.state.settings = dataclasses.replace(app.state.settings, google_client_id="")
    response = client.get("/login")
    assert 'id="g-btn"' not in response.text and "gsi/client" not in response.text


def test_login_page_in_kazakh(client):
    response = client.get("/login?lang=kk")
    assert '<html lang="kk">' in response.text and "<h1>Кіру</h1>" in response.text


def test_invalid_email(client, mailer):
    response = client.post("/auth/email/start", data={"email": "nope"})
    assert response.status_code == 400
    assert "Enter a valid email address." in response.text
    assert mailer.sent == []


def test_email_sign_in_flow(client, mailer):
    start = client.post("/auth/email/start", data={"email": " Ann@Example.com ", "next": "/sites/x"}, follow_redirects=False)
    assert start.status_code == 303 and start.headers["location"] == "/login/code?next=/sites/x"
    assert mailer.sent[-1].to == "ann@example.com"
    assert re.search(r"\b\d{6}\b", mailer.sent[-1].subject)

    page = client.get("/login/code")
    assert "ann@example.com" in page.text and 'autocomplete="one-time-code"' in page.text

    wrong = client.post("/auth/email/verify", data={"code": "abcdef", "next": "/sites/x"})
    assert wrong.status_code == 400 and "wrong or has expired" in wrong.text

    ok = client.post("/auth/email/verify", data={"code": last_code(mailer), "next": "/sites/x"}, follow_redirects=False)
    assert ok.status_code == 303 and ok.headers["location"] == "/sites/x"
    assert ok.cookies.get("zd_session")
    assert client.get("/login", follow_redirects=False).headers["location"] == "/"


def test_code_email_is_translated(client, mailer):
    client.post("/auth/email/start?lang=ru", data={"email": "a@b.co"}, follow_redirects=False)
    assert "ваш код" in mailer.sent[-1].subject
    assert "Код действует 10 минут" in mailer.sent[-1].body


def test_unsafe_next_is_ignored(client, mailer):
    start = client.post("/auth/email/start", data={"email": "a@b.co", "next": "//evil.test"}, follow_redirects=False)
    assert start.headers["location"] == "/login/code?next=/"
    done = client.post("/auth/email/verify", data={"code": last_code(mailer), "next": "https://evil.test"}, follow_redirects=False)
    assert done.headers["location"] == "/"


def test_code_page_requires_pending_email(client):
    assert client.get("/login/code", follow_redirects=False).headers["location"] == "/login"


def test_repeat_start_within_a_minute_reuses_the_code(client, mailer):
    client.post("/auth/email/start", data={"email": "a@b.co"}, follow_redirects=False)
    again = client.post("/auth/email/start", data={"email": "a@b.co"}, follow_redirects=False)
    assert again.status_code == 303
    assert len(mailer.sent) == 1


def test_resend_is_rate_limited_then_allowed(client, mailer, monkeypatch):
    now = [1_000_000]
    monkeypatch.setattr("app.db.now", lambda: now[0])
    client.post("/auth/email/start", data={"email": "a@b.co"}, follow_redirects=False)
    early = client.post("/auth/email/resend", data={"next": "/"})
    assert early.status_code == 429 and "Too many attempts" in early.text
    now[0] += 61
    later = client.post("/auth/email/resend", data={"next": "/"}, follow_redirects=False)
    assert later.status_code == 303 and len(mailer.sent) == 2
    assert "We sent a new code." in client.get(later.headers["location"]).text


def test_google_sign_in(client, google):
    google.tokens["good"] = GoogleIdentity(sub="g-1", email="ann@zerde.ai", name="Ann", picture="")
    ok = client.post("/auth/google", json={"credential": "good", "next": "/admin"}, headers=JSON)
    assert ok.status_code == 200 and ok.json() == {"redirect": "/admin"}
    assert ok.cookies.get("zd_session")
    bad = client.post("/auth/google", json={"credential": "bad"}, headers=JSON)
    assert bad.status_code == 400 and "Google" in bad.json()["error"]


def test_logout(client, mailer):
    sign_in(client, mailer, ADMIN)
    out = client.post("/auth/logout", follow_redirects=False)
    assert out.headers["location"] == "/login"
    assert client.get("/login", follow_redirects=False).status_code == 200


def test_cross_site_form_post_is_rejected(client):
    response = client.post("/auth/email/start", data={"email": "a@b.co"}, headers={"origin": "http://site.localhost:8000"})
    assert response.status_code == 403
