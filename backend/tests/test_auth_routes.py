import dataclasses
import re

import pytest

from app import access
from app.auth.google import GoogleIdentity
from tests.helpers import ADMIN, APP, last_code, new_client, sign_in

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


# Browsers strip tab/CR/LF before parsing a URL, so "/\t/evil.test" would become "//evil.test".
CONTROL_CHAR_NEXT = ["/\t/evil.test", "/\n/evil.test", "/\r/evil.test", "/ /evil.test", "/\x00/evil.test", "/\x7f/evil.test"]


@pytest.mark.parametrize("bad", CONTROL_CHAR_NEXT)
def test_login_page_drops_control_characters_from_next(client, bad):
    page = client.get("/login", params={"next": bad})
    assert 'data-next="/"' in page.text and "evil.test" not in page.text


@pytest.mark.parametrize("bad", CONTROL_CHAR_NEXT)
def test_email_flow_drops_control_characters_from_next(client, mailer, bad):
    start = client.post("/auth/email/start", data={"email": "a@b.co", "next": bad}, follow_redirects=False)
    assert start.headers["location"] == "/login/code?next=/"
    done = client.post("/auth/email/verify", data={"code": last_code(mailer), "next": bad}, follow_redirects=False)
    assert done.status_code == 303 and done.headers["location"] == "/"


@pytest.mark.parametrize("bad", CONTROL_CHAR_NEXT)
def test_google_sign_in_drops_control_characters_from_next(client, google, bad):
    google.tokens["good"] = GoogleIdentity(sub="g-2", email="bo@zerde.ai", name="Bo", picture="")
    response = client.post("/auth/google", json={"credential": "good", "next": bad}, headers=JSON)
    assert response.status_code == 200 and response.json() == {"redirect": "/"}


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


def test_google_email_change_is_checked_against_the_new_address(client, google, mailer, app, conn, monkeypatch):
    now = [1_000_000]
    monkeypatch.setattr("app.db.now", lambda: now[0])
    # @company.example is allowlisted. Alice signs in with Google, then changes her Google account's
    # address to one outside the allowlist: her next sign-in must be judged on the new address.
    access.add_to_allowlist(conn, "@company.example", ADMIN)
    google.tokens["before"] = GoogleIdentity(sub="s-1", email="alice@company.example", name="Alice", picture="")
    google.tokens["after"] = GoogleIdentity(sub="s-1", email="alice@personal.example", name="Alice", picture="")
    assert client.post("/auth/google", json={"credential": "before"}, headers=JSON).status_code == 200
    assert "No access yet" not in client.get("/").text

    # Whoever holds alice@company.example next signs in by email code and lands on the same row for now.
    successor = new_client(app)
    sign_in(successor, mailer, "alice@company.example")

    assert client.post("/auth/google", json={"credential": "after"}, headers=JSON).status_code == 200
    page = client.get("/")
    assert "No access yet" in page.text and "alice@personal.example" in page.text
    # The row moved to the new address, so the session opened under the old one ended...
    assert successor.get("/", follow_redirects=False).headers["location"] == "/login"
    # ...and the old address now gets an account of its own.
    now[0] += 61  # past the one-code-a-minute limit
    sign_in(successor, mailer, "alice@company.example")
    assert "No access yet" not in successor.get("/").text
    assert "No access yet" in client.get("/").text


def lang_hrefs(page):
    return re.findall(r'<a href="([^"]*)" hreflang="(\w+)"', page.text)


def test_language_links_keep_the_return_path(client):
    page = client.get("/login?next=/sites/x&lang=kk")
    assert lang_hrefs(page) == [
        ("/login?next=%2Fsites%2Fx&amp;lang=en", "en"),
        ("/login?next=%2Fsites%2Fx&amp;lang=ru", "ru"),
        ("/login?next=%2Fsites%2Fx&amp;lang=kk", "kk"),
    ]
    switched = client.get("/login?next=%2Fsites%2Fx&lang=ru")
    assert '<html lang="ru">' in switched.text and 'data-next="/sites/x"' in switched.text


def test_language_links_on_pages_answering_a_post_point_at_the_get_page(client, mailer):
    # These pages are rendered at POST-only URLs; a link relative to them used to end in 405 "not found".
    bad_email = client.post("/auth/email/start", data={"email": "nope", "next": "/sites/x"})
    assert bad_email.status_code == 400
    assert ("/login?next=%2Fsites%2Fx&amp;lang=ru", "ru") in lang_hrefs(bad_email)
    client.post("/auth/email/start", data={"email": "a@b.co", "next": "/sites/x"}, follow_redirects=False)
    wrong_code = client.post("/auth/email/verify", data={"code": "000000", "next": "/sites/x"})
    assert wrong_code.status_code == 400
    href = dict((lang, url) for url, lang in lang_hrefs(wrong_code))["kk"]
    assert href == "/login/code?next=%2Fsites%2Fx&amp;lang=kk"
    switched = client.get(href.replace("&amp;", "&"))
    assert switched.status_code == 200 and '<html lang="kk">' in switched.text
    assert 'name="next" value="/sites/x"' in switched.text and "a@b.co" in switched.text
    assert 'href="?lang=' not in wrong_code.text


def test_language_links_never_leave_the_site(client):
    # The links are built from the request path; "//evil.test/x" must not become a protocol-relative URL.
    cases = [("//evil.test/x", "/evil.test/x"), ("/%2F/evil.test/x", "/evil.test/x"), ("/\\evil.test", "/%5Cevil.test")]
    for raw, path in cases:
        page = client.get(APP + raw)
        assert page.status_code == 404
        assert [url for url, _ in lang_hrefs(page)] == [f"{path}?lang={code}" for code in ("en", "ru", "kk")], raw


def test_hourly_limit_counts_down_on_the_code_and_login_pages(client, mailer, monkeypatch):
    now = [1_000_000]
    monkeypatch.setattr("app.db.now", lambda: now[0])
    client.post("/auth/email/start", data={"email": "a@b.co"}, follow_redirects=False)
    for _ in range(4):  # five codes in all: the hourly limit for one address
        now[0] += 61
        assert client.post("/auth/email/resend", data={"next": "/"}, follow_redirects=False).status_code == 303
    now[0] += 61
    wait = 1_000_000 + 3600 - now[0]  # until the first code is an hour old
    resend = client.post("/auth/email/resend", data={"next": "/"})
    assert resend.status_code == 429 and resend.headers["retry-after"] == str(wait)
    assert "Too many attempts. Try again in 55 min." in resend.text
    assert f'data-wait="{wait}"' in resend.text and "disabled>New code in 55 min</button>" in resend.text
    code_page = client.get("/login/code")
    assert f'data-wait="{wait}"' in code_page.text and "disabled>New code in 55 min</button>" in code_page.text
    login = client.post("/auth/email/start", data={"email": "a@b.co", "next": "/"})
    assert login.status_code == 429 and login.headers["retry-after"] == str(wait)
    assert f'data-wait="{wait}"' in login.text and "disabled>Send code in 55 min</button>" in login.text
    assert 'data-wait-label="Send code in {wait}"' in login.text
    now[0] += wait
    assert "data-wait" not in client.get("/login").text
    assert client.post("/auth/email/resend", data={"next": "/"}, follow_redirects=False).status_code == 303
