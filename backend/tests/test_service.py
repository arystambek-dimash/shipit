import io

import pytest

from app import db, users
from app.deploys import service
from app.deploys.ingest import Limits, stage_files
from app.deploys.store import SiteStore


@pytest.fixture
def env(tmp_path):
    conn = db.connect(tmp_path / "app.db")
    db.init_db(conn)
    store = SiteStore(tmp_path / "sites", tmp_path / "tmp")
    owner = users.upsert_user(conn, "owner@zerde.ai", now=1)
    yield conn, store, owner
    conn.close()


def tree(store, text):
    return stage_files([("index.html", io.BytesIO(text.encode()))], store.new_staging_dir(), Limits())


def served(store, name):
    return (store.current_dir(name) / "index.html").read_text()


def test_publish_new_site_then_redeploy(env):
    conn, store, owner = env
    name, first = service.publish(conn, store, site_name=None, user_id=owner["id"], staged=tree(store, "v1"), now=10)
    site = service.get_site(conn, name)
    assert site["owner_id"] == owner["id"] and site["current_deploy_id"] == first
    assert served(store, name) == "v1"
    same, second = service.publish(conn, store, site_name=name, user_id=owner["id"], staged=tree(store, "v2"), now=20)
    assert same == name and second > first
    assert served(store, name) == "v2"
    rows = service.list_sites(conn, owner["id"])
    assert [r["name"] for r in rows] == [name]
    assert rows[0]["deployed_at"] == 20 and rows[0]["owner_email"] == "owner@zerde.ai"
    assert [d["id"] for d in service.list_deploys(conn, site["id"])] == [second, first]


def test_publish_to_missing_site_fails(env):
    conn, store, owner = env
    with pytest.raises(service.SiteError) as info:
        service.publish(conn, store, site_name="nope-site", user_id=owner["id"], staged=tree(store, "x"))
    assert info.value.code == "not_found"


def test_only_ten_deploys_are_kept(env):
    conn, store, owner = env
    name, _ = service.publish(conn, store, site_name=None, user_id=owner["id"], staged=tree(store, "v0"))
    for i in range(1, 12):
        service.publish(conn, store, site_name=name, user_id=owner["id"], staged=tree(store, f"v{i}"))
    site = service.get_site(conn, name)
    ids = [d["id"] for d in service.list_deploys(conn, site["id"])]
    assert len(ids) == service.KEEP_DEPLOYS
    kept_dirs = sorted(int(p.name) for p in (store.site_dir(name) / "deploys").iterdir())
    assert kept_dirs == sorted(ids)


def test_restore(env):
    conn, store, owner = env
    name, first = service.publish(conn, store, site_name=None, user_id=owner["id"], staged=tree(store, "v1"))
    service.publish(conn, store, site_name=name, user_id=owner["id"], staged=tree(store, "v2"))
    service.restore(conn, store, service.get_site(conn, name), first)
    assert served(store, name) == "v1"
    assert service.get_site(conn, name)["current_deploy_id"] == first
    with pytest.raises(service.SiteError):
        service.restore(conn, store, service.get_site(conn, name), 9999)


def test_rename(env):
    conn, store, owner = env
    name, _ = service.publish(conn, store, site_name=None, user_id=owner["id"], staged=tree(store, "v1"))
    other, _ = service.publish(conn, store, site_name=None, user_id=owner["id"], staged=tree(store, "o"))
    site = service.get_site(conn, name)
    for bad, code in [("x", "name_invalid"), ("admin", "name_invalid"), (other, "name_taken")]:
        with pytest.raises(service.SiteError) as info:
            service.rename(conn, store, site, bad)
        assert info.value.code == code
    assert service.rename(conn, store, site, " My-Site ") == "my-site"
    assert service.get_site(conn, name) is None
    assert served(store, "my-site") == "v1"


def test_delete(env):
    conn, store, owner = env
    name, _ = service.publish(conn, store, site_name=None, user_id=owner["id"], staged=tree(store, "v1"))
    service.delete(conn, store, service.get_site(conn, name))
    assert service.get_site(conn, name) is None
    assert not store.site_dir(name).exists()
    assert conn.execute("SELECT COUNT(*) FROM deploys").fetchone()[0] == 0


def test_can_manage():
    site = {"owner_id": 1}
    assert service.can_manage(site, {"id": 1}, admin=False)
    assert not service.can_manage(site, {"id": 2}, admin=False)
    assert service.can_manage(site, {"id": 2}, admin=True)
