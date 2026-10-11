from app import access
from tests.helpers import ADMIN, new_client, sign_in, upload_zip


def deploy(client, text, url="/deploy"):
    response = upload_zip(client, {"index.html": text.encode()}, url=url)
    assert response.status_code == 201, response.text
    return response.json()


def served(client, name):
    return client.get(f"http://{name}.localhost:8000/")


def test_site_page_lists_versions(client, mailer):
    sign_in(client, mailer, ADMIN)
    name = deploy(client, "v1")["name"]
    deploy(client, "v2", url=f"/sites/{name}/deploy")
    page = client.get(f"/sites/{name}")
    assert page.status_code == 200
    assert page.text.count('class="version"') == 2
    assert f"http://{name}.localhost:8000/" in page.text
    assert 'action="/sites/' + name + '/deploy"' in page.text


def test_restore_previous_version(client, mailer):
    sign_in(client, mailer, ADMIN)
    first = deploy(client, "v1")
    name = first["name"]
    deploy(client, "v2", url=f"/sites/{name}/deploy")
    response = client.post(f"/sites/{name}/restore", data={"deploy_id": first["deploy_id"]}, follow_redirects=False)
    assert response.status_code == 303
    assert served(client, name).text == "v1"
    assert "Version restored." in client.get(f"/sites/{name}").text
    assert client.post(f"/sites/{name}/restore", data={"deploy_id": 999999}).status_code == 404


def test_rename(client, mailer):
    sign_in(client, mailer, ADMIN)
    name = deploy(client, "v1")["name"]
    other = deploy(client, "o")["name"]
    invalid = client.post(f"/sites/{name}/rename", data={"new_name": "x"}, follow_redirects=False)
    assert invalid.headers["location"] == f"/sites/{name}"
    assert "3–40 lowercase" in client.get(invalid.headers["location"]).text
    taken = client.post(f"/sites/{name}/rename", data={"new_name": other}, follow_redirects=False)
    assert "This address is taken." in client.get(taken.headers["location"]).text
    ok = client.post(f"/sites/{name}/rename", data={"new_name": "My-Landing"}, follow_redirects=False)
    assert ok.headers["location"] == "/sites/my-landing"
    assert served(client, "my-landing").text == "v1"
    assert served(client, name).status_code == 404


def test_delete_needs_the_name(client, mailer):
    sign_in(client, mailer, ADMIN)
    name = deploy(client, "v1")["name"]
    wrong = client.post(f"/sites/{name}/delete", data={"confirm": "nope"}, follow_redirects=False)
    assert wrong.headers["location"] == f"/sites/{name}"
    assert served(client, name).status_code == 200
    done = client.post(f"/sites/{name}/delete", data={"confirm": name}, follow_redirects=False)
    assert done.headers["location"] == "/"
    assert served(client, name).status_code == 404
    assert "Site deleted." in client.get("/").text


def test_other_users_sites_are_hidden(client, mailer, app, conn):
    access.add_to_allowlist(conn, "@example.com", ADMIN)
    sign_in(client, mailer, "ann@example.com")
    name = deploy(client, "ann")["name"]
    bob = new_client(app)
    sign_in(bob, mailer, "bob@example.com")
    assert bob.get(f"/sites/{name}").status_code == 404
    assert bob.post(f"/sites/{name}/delete", data={"confirm": name}).status_code == 404
    assert upload_zip(bob, {"index.html": b"x"}, url=f"/sites/{name}/deploy").status_code == 404
    admin = new_client(app)
    sign_in(admin, mailer, ADMIN)
    assert admin.get(f"/sites/{name}").status_code == 200


def test_site_page_in_russian(client, mailer):
    sign_in(client, mailer, ADMIN)
    name = deploy(client, "v1")["name"]
    page = client.get(f"/sites/{name}?lang=ru")
    assert "Версии" in page.text and "Удалить сайт" in page.text
