from contextlib import contextmanager

from app import access
from app.deploys import service
from app.routes import sites as sites_routes
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


def run_first(monkeypatch, store, action):
    """Run `action` once, just before the next per-site lock is taken: another request wins the race."""
    real_lock = store.lock

    @contextmanager
    def lock(name):
        monkeypatch.setattr(store, "lock", real_lock)
        action()
        with real_lock(name):
            yield

    monkeypatch.setattr(store, "lock", lock)


def test_delete_that_loses_a_race_is_not_found(client, mailer, app, conn, monkeypatch):
    # Two deletes (a double click) both pass the ownership check; the second must not answer 500.
    sign_in(client, mailer, ADMIN)
    name = deploy(client, "v1")["name"]
    store = app.state.store
    run_first(monkeypatch, store, lambda: service.delete(conn, store, service.get_site(conn, name)))
    response = client.post(f"/sites/{name}/delete", data={"confirm": name}, follow_redirects=False)
    assert response.status_code == 404
    assert served(client, name).status_code == 404


def test_redeploy_whose_site_changed_hands_during_the_upload(client, mailer, app, conn, monkeypatch):
    access.add_to_allowlist(conn, "@example.com", ADMIN)
    sign_in(client, mailer, "ann@example.com")
    name = deploy(client, "ann")["name"]
    bob = new_client(app)
    sign_in(bob, mailer, "bob@example.com")
    bobs = deploy(bob, "bob")["name"]
    store, real_stage = app.state.store, sites_routes.stage_upload

    def stage_then_swap(*args):
        staged = real_stage(*args)
        # Meanwhile Ann renames her site in another tab and Bob renames his onto the freed name.
        service.rename(conn, store, service.get_site(conn, name), "ann-moved")
        service.rename(conn, store, service.get_site(conn, bobs), name)
        return staged

    monkeypatch.setattr(sites_routes, "stage_upload", stage_then_swap)
    response = upload_zip(client, {"index.html": b"ann v2"}, url=f"/sites/{name}/deploy")
    assert response.status_code == 404
    assert response.json()["error"] == (
        "The site's address changed or the site was deleted during the upload. Nothing was published."
    )
    assert served(client, name).text == "bob"
    assert served(client, "ann-moved").text == "ann"
    taken = service.get_site(conn, name)
    assert [d["author_email"] for d in service.list_deploys(conn, taken["id"])] == ["bob@example.com"]


def test_form_redeploy_of_a_deleted_site_returns_to_the_dashboard(client, mailer, app, conn, monkeypatch):
    sign_in(client, mailer, ADMIN)
    name = deploy(client, "v1")["name"]
    store, real_stage = app.state.store, sites_routes.stage_upload

    def stage_then_delete(*args):
        staged = real_stage(*args)
        service.delete(conn, store, service.get_site(conn, name))
        return staged

    monkeypatch.setattr(sites_routes, "stage_upload", stage_then_delete)
    response = upload_zip(client, {"index.html": b"v2"}, url=f"/sites/{name}/deploy", json=False)
    assert response.status_code == 303 and response.headers["location"] == "/"
    assert "Nothing was published." in client.get("/").text
