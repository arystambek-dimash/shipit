from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from tests.helpers import ADMIN, APP, new_client, sign_in

TITLES = {"en": "Privacy", "ru": "Конфиденциальность", "kk": "Құпиялылық"}
UPDATED = {
    "en": "Last updated: 12 October 2026",
    "ru": "Обновлено: 12 октября 2026",
    "kk": "Соңғы жаңартылған күні: 2026 жылғы 12 қазан",
}


def client_for(settings, mailer, **changes):
    client = TestClient(create_app(replace(settings, **changes), mailer=mailer), base_url=APP)
    client.headers["origin"] = APP
    return client


@pytest.mark.parametrize("lang", ["en", "ru", "kk"])
def test_privacy_page_is_public_and_localised(client, lang):
    response = client.get(f"/privacy?lang={lang}", follow_redirects=False)
    assert response.status_code == 200
    assert f'<html lang="{lang}">' in response.text
    assert f"<h1>{TITLES[lang]}</h1>" in response.text
    assert f"<title>{TITLES[lang]} — zerde/deploy</title>" in response.text
    assert UPDATED[lang] in response.text


def test_privacy_page_says_what_is_stored(client):
    text = client.get("/privacy").text
    for fact in ("email address", "Google account ID", "profile picture", "IP address", "session cookie", "anyone who has the link"):
        assert fact in text


def test_privacy_page_names_the_first_admin_as_the_contact(settings, mailer):
    client = client_for(settings, mailer, admin_emails=frozenset({"zoe@zerde.ai", "ann@zerde.ai"}))
    text = client.get("/privacy").text
    assert "Contact: ann@zerde.ai" in text
    assert "zoe@zerde.ai" not in text


def test_privacy_page_without_admins_has_no_contact_line(settings, mailer):
    text = client_for(settings, mailer, admin_emails=frozenset()).get("/privacy").text
    assert "<h1>Privacy</h1>" in text
    assert "Contact:" not in text


def test_privacy_page_shows_the_signed_in_user_in_the_header(app, mailer):
    client = new_client(app)
    sign_in(client, mailer, "ann@example.com")
    response = client.get("/privacy")
    assert response.status_code == 200
    assert "ann@example.com" in response.text


def test_sign_in_pages_link_to_the_privacy_page(client):
    login = client.get("/login")
    assert 'href="/privacy"' in login.text
    client.post("/auth/email/start", data={"email": ADMIN, "next": "/"})
    code = client.get("/login/code")
    assert code.status_code == 200
    assert 'href="/privacy"' in code.text
