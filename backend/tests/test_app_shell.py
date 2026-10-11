from tests.helpers import publish_site


def test_unknown_page_renders_translated_404(client):
    response = client.get("/nope?lang=ru")
    assert response.status_code == 404
    assert '<html lang="ru">' in response.text
    assert "Страница не найдена" in response.text


def test_language_choice_is_remembered(client):
    client.get("/nope?lang=kk")
    response = client.get("/nope")
    assert '<html lang="kk">' in response.text
    assert "Бет табылмады" in response.text


def test_accept_language_is_used(client):
    response = client.get("/nope", headers={"accept-language": "ru-RU,ru;q=0.9"})
    assert '<html lang="ru">' in response.text


def test_cross_origin_post_is_blocked(client):
    assert client.post("/healthz", headers={"origin": "http://evil.localhost:8000"}).status_code == 403
    assert client.post("/healthz").status_code == 405


def test_pages_link_versioned_assets_and_strings(client):
    response = client.get("/nope")
    assert "/static/css/app.css?v=" in response.text
    assert 'id="zd-strings"' in response.text


def test_static_files_are_cached(client):
    response = client.get("/static/css/app.css")
    assert response.status_code == 200
    assert "immutable" in response.headers["cache-control"]


def test_json_errors(client):
    response = client.get("/nope", headers={"accept": "application/json"})
    assert response.status_code == 404
    assert response.json() == {"error": "Page not found"}


def test_tls_ask(client, app, conn):
    name = publish_site(app, conn)
    assert client.get(f"/internal/tls-ask?domain={name}.localhost").status_code == 200
    assert client.get("/internal/tls-ask?domain=missing-site.localhost").status_code == 404
    assert client.get(f"/internal/tls-ask?domain={name}.evil.test").status_code == 404
    assert client.get("/internal/tls-ask?domain=localhost").status_code == 404


def test_dev_site_serving(client, app, conn):
    name = publish_site(
        app, conn, {"index.html": b"home", "about.html": b"about", "docs/index.html": b"docs", "404.html": b"custom"}
    )
    base = f"http://{name}.localhost:8000"
    assert client.get(base + "/").text == "home"
    assert client.get(base + "/about").text == "about"
    assert client.get(base + "/docs/").text == "docs"
    assert client.get(base + "/docs").text == "docs"
    missing = client.get(base + "/missing")
    assert missing.status_code == 404 and missing.text == "custom"
    escape = client.get(base + "/%2e%2e/%2e%2e/app.db")
    assert escape.status_code == 404
    unknown = client.get("http://missing-site.localhost:8000/")
    assert unknown.status_code == 404 and "404" in unknown.text
