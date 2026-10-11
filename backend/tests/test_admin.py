from tests.helpers import ADMIN, new_client, sign_in, upload_zip


def test_non_admins_are_forbidden(client, mailer):
    sign_in(client, mailer, "guest@example.com")
    assert client.get("/admin").status_code == 403
    assert client.post("/admin/allowlist", data={"pattern": "@example.com"}).status_code == 403


def test_admin_manages_the_allowlist(client, mailer, app):
    sign_in(client, mailer, ADMIN)
    page = client.get("/admin")
    assert page.status_code == 200 and ADMIN in page.text and "Nobody besides administrators yet." in page.text

    added = client.post("/admin/allowlist", data={"pattern": " @Example.com "}, follow_redirects=False)
    assert added.headers["location"] == "/admin"
    page = client.get("/admin")
    assert "Added." in page.text and "@example.com" in page.text

    duplicate = client.post("/admin/allowlist", data={"pattern": "@example.com"}, follow_redirects=False)
    assert "Already on the list." in client.get(duplicate.headers["location"]).text
    invalid = client.post("/admin/allowlist", data={"pattern": "example"}, follow_redirects=False)
    assert "Enter an email or a domain" in client.get(invalid.headers["location"]).text

    ann = new_client(app)
    sign_in(ann, mailer, "ann@example.com")
    assert 'id="drop"' in ann.get("/").text

    entry_id = page.text.split('action="/admin/allowlist/')[1].split("/delete")[0]
    removed = client.post(f"/admin/allowlist/{entry_id}/delete", follow_redirects=False)
    assert "Removed." in client.get(removed.headers["location"]).text
    assert "No access yet" in ann.get("/").text


def test_admin_sees_all_sites(client, mailer, app, conn):
    from app import access

    access.add_to_allowlist(conn, "ann@example.com", ADMIN)
    ann = new_client(app)
    sign_in(ann, mailer, "ann@example.com")
    name = upload_zip(ann, {"index.html": b"hi"}).json()["name"]
    sign_in(client, mailer, ADMIN)
    page = client.get("/admin?lang=kk")
    assert name in page.text and "ann@example.com" in page.text and "Барлық сайттар" in page.text
