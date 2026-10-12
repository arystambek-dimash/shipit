from app import access
from app.auth.mailer import ConsoleMailer
from app.deploys import ingest
from app.main import create_app
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


def multipart_body(boundary: str, files: dict[str, bytes]) -> bytes:
    parts = b"".join(
        (
            f'--{boundary}\r\nContent-Disposition: form-data; name="files"; filename="{name}"\r\n'
            "Content-Type: text/html\r\n\r\n"
        ).encode()
        + data
        + b"\r\n"
        for name, data in files.items()
    )
    return parts + f"--{boundary}--\r\n".encode()


def chunked(body: bytes, size: int = 1024):
    for start in range(0, len(body), size):
        yield body[start : start + size]


def test_chunked_upload_without_content_length_is_rejected(client, mailer):
    # A well-formed, tiny upload: it must be refused only because the size cannot be checked up front.
    sign_in(client, mailer, ADMIN)
    boundary = "zerde-boundary"
    body = multipart_body(boundary, {"index.html": b"hi"})
    headers = {**JSON, "content-type": f"multipart/form-data; boundary={boundary}"}
    request = client.build_request("POST", "/deploy", content=chunked(body), headers=headers)
    assert "content-length" not in request.headers and request.headers["transfer-encoding"] == "chunked"
    response = client.send(request)
    assert response.status_code == 400
    assert response.json() == {"error": "The upload is larger than 5 MB."}
    # The same body with a Content-Length is accepted, so the guard is what rejected the chunked one.
    plain = client.post("/deploy", content=body, headers=headers)
    assert plain.status_code == 201, plain.text


def test_chunked_oversized_upload_never_reaches_form_parsing(client, mailer, monkeypatch):
    from starlette.requests import Request

    async def fail_form(self, *args, **kwargs):
        raise AssertionError("the body must not be spooled when its size is unknown")

    sign_in(client, mailer, ADMIN)
    monkeypatch.setattr(Request, "form", fail_form)
    boundary = "zerde-boundary"
    body = multipart_body(boundary, {"big.bin": b"x" * (6 * 1024 * 1024)})
    headers = {**JSON, "content-type": f"multipart/form-data; boundary={boundary}"}
    response = client.post("/deploy", content=chunked(body, 64 * 1024), headers=headers)
    assert response.status_code == 400
    assert response.json() == {"error": "The upload is larger than 5 MB."}


def test_unusable_or_oversized_content_length_is_rejected(client, mailer):
    sign_in(client, mailer, ADMIN)
    for bad in (b"\xb2", b"abc", b"-5", b"1.5", b"99999999"):
        response = client.post(
            "/deploy",
            content=b"",
            headers={**JSON, "content-type": "multipart/form-data; boundary=x", "content-length": bad},
        )
        assert response.status_code == 400, bad
        assert response.json() == {"error": "The upload is larger than 5 MB."}, bad


def test_a_worker_starting_mid_upload_leaves_the_upload_alone(client, mailer, settings, monkeypatch):
    # uvicorn respawns a worker that dies; the new worker's create_app cleans tmp while another worker is
    # extracting a zip there. That used to delete the files written so far and publish the rest as live.
    sign_in(client, mailer, ADMIN)
    real_write, writes = ingest._write, []

    def write_then_respawn(stream, dest, rel, budget):
        real_write(stream, dest, rel, budget)
        writes.append(rel)
        if len(writes) == 2:
            create_app(settings, mailer=ConsoleMailer())

    monkeypatch.setattr(ingest, "_write", write_then_respawn)
    files = {"assets/app.js": b"js", "assets/style.css": b"css", "assets/logo.png": b"png", "index.html": b"home"}
    response = upload_zip(client, files)
    assert response.status_code == 201, response.text
    site = f"http://{response.json()['name']}.localhost:8000"
    for path, data in files.items():
        assert client.get(f"{site}/{path}").content == data, path
