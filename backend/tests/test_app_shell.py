import os

from app.devsites import resolve_site_file
from tests.helpers import ADMIN, publish_site, sign_in, upload_zip


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


def test_dev_site_serving(client, app, conn, settings):
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
    # The site root is data/sites/<name>/deploys/<id>. A sentinel two levels up (sites/<name>/secret.txt) and the
    # real data/app.db four levels up are both reachable if, and only if, the containment guard fails.
    sentinel = settings.sites_dir / name / "secret.txt"
    sentinel.write_text("top secret")
    for escape_path in ("/%2e%2e/%2e%2e/secret.txt", "/%2e%2e/%2e%2e/%2e%2e/%2e%2e/app.db"):
        escape = client.get(base + escape_path)
        assert escape.status_code == 404, escape_path
        assert "top secret" not in escape.text and b"SQLite format" not in escape.content, escape_path
    unknown = client.get("http://missing-site.localhost:8000/")
    assert unknown.status_code == 404 and "404" in unknown.text


def test_resolve_site_file_stays_inside_the_site(tmp_path):
    root = tmp_path / "site"
    (root / "docs").mkdir(parents=True)
    (root / "index.html").write_text("home")
    (root / "about.html").write_text("about")
    (root / "docs" / "index.html").write_text("docs")
    (tmp_path / "secret.txt").write_text("outside")
    (tmp_path / "site-evil").mkdir()  # shares the root's string prefix, so a startswith() check would let it in
    (tmp_path / "site-evil" / "index.html").write_text("evil")
    (root / "link.txt").symlink_to(tmp_path / "secret.txt")
    (root / "linkdir").symlink_to(tmp_path / "site-evil", target_is_directory=True)

    assert resolve_site_file(root, "/") == root.resolve() / "index.html"
    assert resolve_site_file(root, "/about") == root.resolve() / "about.html"
    assert resolve_site_file(root, "/docs") == root.resolve() / "docs" / "index.html"
    assert resolve_site_file(root, "/docs/") == root.resolve() / "docs" / "index.html"
    assert resolve_site_file(root, "/missing") is None

    for url_path in (
        "/../secret.txt",
        "/%2e%2e/secret.txt",
        "/docs/../../secret.txt",
        "/docs/%2e%2e/%2e%2e/secret.txt",
        "/../site-evil/index.html",
        "/..",
        "/%2e%2e/",
        "/link.txt",  # a symlink that resolves outside the root
        "/linkdir",
        "/linkdir/",
    ):
        assert resolve_site_file(root, url_path) is None, url_path


def test_app_pages_refuse_to_be_framed(client, mailer, app, conn):
    # A deployed site is same-site with the app: framed, an app form would carry the session cookie and
    # the app's own Origin, so only these headers stop a click being stolen.
    sign_in(client, mailer, ADMIN)
    name = publish_site(app, conn)
    expected = {"/": 200, "/admin": 200, f"/sites/{name}": 200, "/login": 303, "/nope": 404, "/healthz": 200,
                "/static/css/app.css": 200}
    for path, status in expected.items():
        response = client.get(path, follow_redirects=False)
        assert response.status_code == status, path
        assert response.headers["x-frame-options"] == "DENY", path
        assert response.headers["content-security-policy"] == "frame-ancestors 'none'", path
    blocked = client.post("/auth/logout", headers={"origin": "http://evil.localhost:8000"})
    assert blocked.status_code == 403 and blocked.headers["x-frame-options"] == "DENY"


def test_deployed_sites_may_be_framed(client, app, conn):
    name = publish_site(app, conn)
    response = client.get(f"http://{name}.localhost:8000/")
    assert response.status_code == 200
    assert "x-frame-options" not in response.headers and "content-security-policy" not in response.headers


def test_large_bodies_are_refused_outside_the_upload_routes(client, google):
    # FastAPI reads and parses these bodies whole; an anonymous 60 MB JSON array cost ~400 MB per request.
    padding = "0," * 40_000
    body = '{"credential": "x", "next": "/", "pad": [' + padding + "0]}"
    google_login = client.post(
        "/auth/google", content=body, headers={"content-type": "application/json", "accept": "application/json"}
    )
    assert google_login.status_code == 413
    assert google_login.json() == {"error": "Something went wrong. Try again."}
    form = client.post("/auth/email/start", data={"email": "a@b.co", "pad": "x" * 70_000})
    assert form.status_code == 413
    small = client.post("/auth/google", json={"credential": "x"}, headers={"accept": "application/json"})
    assert small.status_code == 400  # parsed and rejected by the handler, as before


def test_uploads_may_be_larger_than_the_form_cap(client, mailer):
    sign_in(client, mailer, ADMIN)
    noise = os.urandom(200_000)  # incompressible, so the zip stays well over 64 KB
    response = upload_zip(client, {"index.html": b"hi", "noise.bin": noise})
    assert response.status_code == 201, response.text
