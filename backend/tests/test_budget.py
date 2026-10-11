import gzip
import re

import pytest

from app.config import FRONTEND_DIR
from tests.helpers import ADMIN, sign_in, upload_zip

STATIC = FRONTEND_DIR / "static"


def gz(data: bytes) -> int:
    return len(gzip.compress(data, 9))


def test_static_asset_budget():
    assert gz((STATIC / "css/app.css").read_bytes()) <= 12_000
    assert gz((STATIC / "js/app.js").read_bytes()) <= 8_000
    assert sum(p.stat().st_size for p in (STATIC / "fonts").glob("*.woff2")) <= 100_000


def test_dashboard_html_budget(client, mailer):
    sign_in(client, mailer, ADMIN)
    for _ in range(5):
        upload_zip(client, {"index.html": b"hi"})
    response = client.get("/")
    assert gz(response.content) <= 15_000


@pytest.mark.parametrize("lang", ["en", "ru", "kk"])
def test_pages_declare_language_and_label_fields(client, lang):
    page = client.get(f"/login?lang={lang}").text
    assert f'<html lang="{lang}">' in page
    assert '<label for="email">' in page
    assert 'name="viewport"' in page


def test_no_third_party_assets_outside_login(client, mailer):
    sign_in(client, mailer, ADMIN)
    page = client.get("/").text
    external = re.findall(r'<(?:script|link)[^>]+(?:src|href)="https?://', page)
    assert external == []


def test_no_emoji_in_templates_or_translations():
    emoji = re.compile("[\U0001F300-\U0001FAFF☀-➿]")
    for path in list((FRONTEND_DIR / "templates").glob("*.html")) + list((FRONTEND_DIR / "i18n").glob("*.json")):
        assert not emoji.search(path.read_text(encoding="utf-8")), path.name
