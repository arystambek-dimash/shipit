from app import access
from tests.helpers import ADMIN, sign_in, upload_zip

JSON = {"accept": "application/json"}


def test_dashboard_requires_sign_in(client):
    response = client.get("/", follow_redirects=False)
    assert response.status_code == 303 and response.headers["location"] == "/login"


def test_user_without_access_sees_explanation(client, mailer):
    sign_in(client, mailer, "guest@example.com")
    page = client.get("/")
    assert page.status_code == 200
    assert "guest@example.com" in page.text and "No access yet" in page.text
    assert 'id="drop"' not in page.text
    assert upload_zip(client, {"index.html": b"hi"}).status_code == 403


def test_allowlisted_user_sees_drop_zone(client, mailer, conn):
    access.add_to_allowlist(conn, "@example.com", ADMIN)
    sign_in(client, mailer, "ann@example.com")
    page = client.get("/")
    assert 'id="drop"' in page.text and 'action="/deploy"' in page.text
    assert "No sites yet" in page.text


def test_deploy_zip_and_visit(client, mailer):
    sign_in(client, mailer, ADMIN)
    response = upload_zip(client, {"index.html": b"<h1>Hello</h1>", "about.html": b"about", "docs/index.html": b"docs"})
    assert response.status_code == 201, response.text
    body = response.json()
    name = body["name"]
    assert body["url"] == f"http://{name}.localhost:8000/"
    assert body["manage_url"] == f"/sites/{name}"
    site = f"http://{name}.localhost:8000"
    assert client.get(site + "/").text == "<h1>Hello</h1>"
    assert client.get(site + "/about").text == "about"
    assert client.get(site + "/docs/").text == "docs"
    assert client.get(site + "/nope").status_code == 404
    dashboard = client.get("/")
    assert f"{name}.localhost:8000" in dashboard.text
    assert "Published." in dashboard.text


def test_deploy_files_with_relative_paths(client, mailer):
    sign_in(client, mailer, ADMIN)
    response = client.post(
        "/deploy",
        files=[("files", ("index.html", b"root", "text/html")), ("files", ("a.css", b"body{}", "text/css"))],
        data={"paths": ["proj/index.html", "proj/css/a.css"]},
        headers=JSON,
    )
    assert response.status_code == 201, response.text
    name = response.json()["name"]
    assert client.get(f"http://{name}.localhost:8000/css/a.css").text == "body{}"


def test_upload_errors_are_translated(client, mailer):
    sign_in(client, mailer, ADMIN)
    english = upload_zip(client, {"readme.txt": b"x"})
    assert english.status_code == 400 and english.json() == {"error": "There is no index.html at the top level."}
    client.get("/?lang=ru")
    russian = upload_zip(client, {"readme.txt": b"x"})
    assert russian.json() == {"error": "В корне нет index.html."}
    empty = client.post("/deploy", data={"paths": []}, headers=JSON)
    assert empty.status_code == 400


def test_form_upload_without_javascript_redirects(client, mailer):
    sign_in(client, mailer, ADMIN)
    response = upload_zip(client, {"index.html": b"hi"}, json=False)
    assert response.status_code == 303 and response.headers["location"].startswith("/sites/")
    failed = upload_zip(client, {"readme.txt": b"x"}, json=False)
    assert failed.status_code == 303 and failed.headers["location"] == "/"
    assert "no index.html" in client.get("/").text


def test_redeploy_endpoint_and_ownership(client, mailer, app, conn):
    sign_in(client, mailer, ADMIN)
    name = upload_zip(client, {"index.html": b"v1"}).json()["name"]
    again = upload_zip(client, {"index.html": b"v2"}, url=f"/sites/{name}/deploy")
    assert again.status_code == 201 and again.json()["name"] == name
    assert client.get(f"http://{name}.localhost:8000/").text == "v2"
    assert upload_zip(client, {"index.html": b"x"}, url="/sites/missing-site/deploy").status_code == 404
