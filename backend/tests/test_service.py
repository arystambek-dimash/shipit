import io
import shutil
import sqlite3
from contextlib import contextmanager

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


def redeploy(conn, store, name, owner, text, **kwargs):
    """Publish a new version of the site that is called `name` right now."""
    site = service.get_site(conn, name)
    return service.publish(conn, store, site=site, user_id=owner["id"], staged=tree(store, text), **kwargs)


def test_publish_new_site_then_redeploy(env):
    conn, store, owner = env
    name, first = service.publish(conn, store, site=None, user_id=owner["id"], staged=tree(store, "v1"), now=10)
    site = service.get_site(conn, name)
    assert site["owner_id"] == owner["id"] and site["current_deploy_id"] == first
    assert served(store, name) == "v1"
    same, second = redeploy(conn, store, name, owner, "v2", now=20)
    assert same == name and second > first
    assert served(store, name) == "v2"
    rows = service.list_sites(conn, owner["id"])
    assert [r["name"] for r in rows] == [name]
    assert rows[0]["deployed_at"] == 20 and rows[0]["owner_email"] == "owner@zerde.ai"
    assert [d["id"] for d in service.list_deploys(conn, site["id"])] == [second, first]


def test_publish_to_missing_site_fails(env):
    conn, store, owner = env
    with pytest.raises(service.SiteError) as info:
        gone = {"id": 999, "name": "nope-site"}
        service.publish(conn, store, site=gone, user_id=owner["id"], staged=tree(store, "x"))
    assert info.value.code == "not_found"


def test_only_ten_deploys_are_kept(env):
    conn, store, owner = env
    name, _ = service.publish(conn, store, site=None, user_id=owner["id"], staged=tree(store, "v0"))
    for i in range(1, 12):
        redeploy(conn, store, name, owner, f"v{i}")
    site = service.get_site(conn, name)
    ids = [d["id"] for d in service.list_deploys(conn, site["id"])]
    assert len(ids) == service.KEEP_DEPLOYS
    kept_dirs = sorted(int(p.name) for p in (store.site_dir(name) / "deploys").iterdir())
    assert kept_dirs == sorted(ids)


def test_restore(env):
    conn, store, owner = env
    name, first = service.publish(conn, store, site=None, user_id=owner["id"], staged=tree(store, "v1"))
    redeploy(conn, store, name, owner, "v2")
    service.restore(conn, store, service.get_site(conn, name), first)
    assert served(store, name) == "v1"
    assert service.get_site(conn, name)["current_deploy_id"] == first
    with pytest.raises(service.SiteError):
        service.restore(conn, store, service.get_site(conn, name), 9999)


def test_rename(env):
    conn, store, owner = env
    name, _ = service.publish(conn, store, site=None, user_id=owner["id"], staged=tree(store, "v1"))
    other, _ = service.publish(conn, store, site=None, user_id=owner["id"], staged=tree(store, "o"))
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
    name, _ = service.publish(conn, store, site=None, user_id=owner["id"], staged=tree(store, "v1"))
    service.delete(conn, store, service.get_site(conn, name))
    assert service.get_site(conn, name) is None
    assert not store.site_dir(name).exists()
    assert conn.execute("SELECT COUNT(*) FROM deploys").fetchone()[0] == 0


def test_can_manage():
    site = {"owner_id": 1}
    assert service.can_manage(site, {"id": 1}, admin=False)
    assert not service.can_manage(site, {"id": 2}, admin=False)
    assert service.can_manage(site, {"id": 2}, admin=True)


def deploy_dirs(store, name):
    return sorted(int(p.name) for p in (store.site_dir(name) / "deploys").iterdir())


class CommitFails:
    """Connection proxy whose COMMIT fails once armed; the test rolls the open transaction back itself."""

    def __init__(self, conn):
        self._conn = conn
        self.armed = False

    def execute(self, sql, *args):
        if self.armed and sql == "COMMIT":
            raise sqlite3.OperationalError("disk I/O error")
        return self._conn.execute(sql, *args)


def lock_after(monkeypatch, store, action):
    """Run `action` once, between the caller's site lookup and its lock: the other request wins the race."""
    real_lock = store.lock

    @contextmanager
    def lock(name):
        monkeypatch.setattr(store, "lock", real_lock)
        action()
        with real_lock(name):
            yield

    monkeypatch.setattr(store, "lock", lock)


# --- publish is one transaction: the database and the live symlink never disagree ---


@pytest.mark.parametrize(
    "trigger",
    [
        "BEFORE INSERT ON deploys",  # fails before anything touches the disk
        "BEFORE UPDATE ON sites",
        "BEFORE DELETE ON deploys",  # fails while dropping the stale rows
    ],
)
def test_failed_redeploy_changes_nothing(env, trigger):
    conn, store, owner = env
    name, first = service.publish(conn, store, site=None, user_id=owner["id"], staged=tree(store, "v0"))
    site_id = service.get_site(conn, name)["id"]
    for i in range(1, service.KEEP_DEPLOYS):
        _, last = redeploy(conn, store, name, owner, f"v{i}")
    before = deploy_dirs(store, name)
    assert len(before) == service.KEEP_DEPLOYS  # the next publish has a stale deploy to drop
    conn.execute(f"CREATE TRIGGER boom {trigger} BEGIN SELECT RAISE(ABORT, 'boom'); END")
    with pytest.raises(sqlite3.IntegrityError):
        redeploy(conn, store, name, owner, "new")
    assert served(store, name) == f"v{service.KEEP_DEPLOYS - 1}"
    assert store.current_deploy(name) == last == service.get_site(conn, name)["current_deploy_id"]
    assert deploy_dirs(store, name) == before == sorted(d["id"] for d in service.list_deploys(conn, site_id))
    conn.execute("DROP TRIGGER boom")
    _, newest = redeploy(conn, store, name, owner, "new")
    assert served(store, name) == "new" and service.get_site(conn, name)["current_deploy_id"] == newest
    assert first not in deploy_dirs(store, name)


@pytest.mark.parametrize("trigger", ["BEFORE INSERT ON deploys", "BEFORE UPDATE ON sites"])
def test_failed_first_publish_leaves_no_site_behind(env, trigger):
    conn, store, owner = env
    conn.execute(f"CREATE TRIGGER boom {trigger} BEGIN SELECT RAISE(ABORT, 'boom'); END")
    with pytest.raises(sqlite3.IntegrityError):
        service.publish(conn, store, site=None, user_id=owner["id"], staged=tree(store, "v1"))
    assert conn.execute("SELECT COUNT(*) FROM sites").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM deploys").fetchone()[0] == 0
    assert [p.name for p in store.sites_dir.iterdir()] == [".locks"]


def test_failed_first_publish_on_disk_leaves_no_site_behind(env, monkeypatch):
    conn, store, owner = env

    def broken(name, deploy_id):
        raise OSError("injected")

    monkeypatch.setattr(store, "activate", broken)
    with pytest.raises(OSError, match="injected"):
        service.publish(conn, store, site=None, user_id=owner["id"], staged=tree(store, "v1"))
    assert conn.execute("SELECT COUNT(*) FROM sites").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM deploys").fetchone()[0] == 0
    assert [p.name for p in store.sites_dir.iterdir()] == [".locks"]


def test_first_publish_draws_again_when_the_name_was_just_taken(env, monkeypatch):
    conn, store, owner = env
    taken, _ = service.publish(conn, store, site=None, user_id=owner["id"], staged=tree(store, "mine"))
    draws = iter([taken, "fresh-name-22"])  # the first draw is a name another request has claimed meanwhile
    monkeypatch.setattr(service.names, "generate_name", lambda exists: next(draws))
    name, _ = service.publish(conn, store, site=None, user_id=owner["id"], staged=tree(store, "new"))
    assert name == "fresh-name-22" and served(store, name) == "new"
    assert served(store, taken) == "mine"


def test_publish_whose_commit_fails_restores_the_live_site(env):
    conn, store, owner = env
    name, first = service.publish(conn, store, site=None, user_id=owner["id"], staged=tree(store, "v1"))
    proxy = CommitFails(conn)
    proxy.armed = True
    with pytest.raises(sqlite3.OperationalError):
        service.publish(proxy, store, site=service.get_site(conn, name), user_id=owner["id"], staged=tree(store, "v2"))
    conn.execute("ROLLBACK")  # what SQLite does with a transaction whose COMMIT failed
    assert served(store, name) == "v1"
    assert store.current_deploy(name) == first == service.get_site(conn, name)["current_deploy_id"]
    assert deploy_dirs(store, name) == [first]


def test_first_publish_whose_commit_fails_removes_the_new_site_dir(env):
    conn, store, owner = env
    proxy = CommitFails(conn)
    proxy.armed = True
    with pytest.raises(sqlite3.OperationalError):
        service.publish(proxy, store, site=None, user_id=owner["id"], staged=tree(store, "v1"))
    conn.execute("ROLLBACK")
    assert conn.execute("SELECT COUNT(*) FROM sites").fetchone()[0] == 0
    assert [p.name for p in store.sites_dir.iterdir()] == [".locks"]


def test_publish_survives_failed_activates(env, monkeypatch):
    conn, store, owner = env
    name, first = service.publish(conn, store, site=None, user_id=owner["id"], staged=tree(store, "v1"))
    real_activate = store.activate

    def broken(site, deploy_id):
        raise OSError("injected")

    monkeypatch.setattr(store, "activate", broken)
    for _ in range(2):  # the id is reused each time; a half-published tree used to wedge the site
        with pytest.raises(OSError, match="injected"):
            redeploy(conn, store, name, owner, "v2")
        assert deploy_dirs(store, name) == [first]
        assert served(store, name) == "v1"
        assert [d["id"] for d in service.list_deploys(conn, service.get_site(conn, name)["id"])] == [first]
    monkeypatch.setattr(store, "activate", real_activate)
    _, second = redeploy(conn, store, name, owner, "v2")
    assert second > first and served(store, name) == "v2"
    assert service.get_site(conn, name)["current_deploy_id"] == second


# --- the per-site lock protects against a rename or delete that finished while we waited for it ---


def test_publish_racing_a_rename_is_rejected(env, monkeypatch):
    conn, store, owner = env
    name, _ = service.publish(conn, store, site=None, user_id=owner["id"], staged=tree(store, "v1"))
    lock_after(monkeypatch, store, lambda: service.rename(conn, store, service.get_site(conn, name), "renamed-site"))
    with pytest.raises(service.SiteError) as info:
        redeploy(conn, store, name, owner, "v2")
    assert info.value.code == "not_found"
    assert not store.site_dir(name).exists()
    renamed = service.get_site(conn, "renamed-site")
    assert served(store, "renamed-site") == "v1"
    assert len(service.list_deploys(conn, renamed["id"])) == 1


def test_publish_goes_to_the_authorised_site_or_nowhere(env):
    # The route authorises a site row, then reads the whole upload. If the owner renames that site meanwhile
    # and another user's site takes the freed name, the deploy must not land on the other user's site.
    conn, store, owner = env
    other = users.upsert_user(conn, "other@zerde.ai", now=1)
    mine, _ = service.publish(conn, store, site=None, user_id=owner["id"], staged=tree(store, "mine"))
    theirs, _ = service.publish(conn, store, site=None, user_id=other["id"], staged=tree(store, "theirs"))
    authorised = service.get_site(conn, mine)
    service.rename(conn, store, service.get_site(conn, mine), "moved-away")
    service.rename(conn, store, service.get_site(conn, theirs), mine)
    with pytest.raises(service.SiteError) as info:
        service.publish(conn, store, site=authorised, user_id=owner["id"], staged=tree(store, "intruder"))
    assert info.value.code == "not_found"
    taken = service.get_site(conn, mine)
    assert taken["owner_id"] == other["id"] and served(store, mine) == "theirs"
    assert [d["created_by"] for d in service.list_deploys(conn, taken["id"])] == [other["id"]]
    assert served(store, "moved-away") == "mine"


def test_restore_with_a_stale_row_is_rejected(env):
    conn, store, owner = env
    name, first = service.publish(conn, store, site=None, user_id=owner["id"], staged=tree(store, "v1"))
    _, second = redeploy(conn, store, name, owner, "v2")
    stale = service.get_site(conn, name)
    service.rename(conn, store, stale, "renamed-site")
    with pytest.raises(service.SiteError) as info:
        service.restore(conn, store, stale, first)
    assert info.value.code == "not_found"
    assert served(store, "renamed-site") == "v2"
    assert service.get_site(conn, "renamed-site")["current_deploy_id"] == second


def test_rename_with_a_stale_row_is_rejected(env):
    conn, store, owner = env
    name, _ = service.publish(conn, store, site=None, user_id=owner["id"], staged=tree(store, "v1"))
    stale = service.get_site(conn, name)
    service.rename(conn, store, stale, "first-name")
    with pytest.raises(service.SiteError) as info:
        service.rename(conn, store, stale, "second-name")
    assert info.value.code == "not_found"
    assert service.get_site(conn, "first-name") is not None and service.get_site(conn, "second-name") is None
    assert served(store, "first-name") == "v1" and not store.site_dir("second-name").exists()


def test_delete_with_a_stale_row_is_rejected(env):
    conn, store, owner = env
    name, _ = service.publish(conn, store, site=None, user_id=owner["id"], staged=tree(store, "v1"))
    stale = service.get_site(conn, name)
    service.rename(conn, store, stale, "renamed-site")
    with pytest.raises(service.SiteError) as info:
        service.delete(conn, store, stale)
    assert info.value.code == "not_found"
    assert service.get_site(conn, "renamed-site") is not None
    assert served(store, "renamed-site") == "v1"
    assert conn.execute("SELECT COUNT(*) FROM deploys").fetchone()[0] == 1


def test_delete_with_a_stale_row_spares_a_site_that_took_the_name(env):
    conn, store, owner = env
    gone, _ = service.publish(conn, store, site=None, user_id=owner["id"], staged=tree(store, "old"))
    other, _ = service.publish(conn, store, site=None, user_id=owner["id"], staged=tree(store, "new"))
    stale = service.get_site(conn, gone)
    service.delete(conn, store, stale)
    service.rename(conn, store, service.get_site(conn, other), gone)
    with pytest.raises(service.SiteError) as info:
        service.delete(conn, store, stale)
    assert info.value.code == "not_found"
    assert served(store, gone) == "new" and service.get_site(conn, gone) is not None


def test_rename_holds_the_lock_of_both_names(env, monkeypatch):
    conn, store, owner = env
    name, _ = service.publish(conn, store, site=None, user_id=owner["id"], staged=tree(store, "v1"))
    held, real_lock = [], store.lock

    @contextmanager
    def spy(lock_name):
        held.append(lock_name)
        with real_lock(lock_name):
            yield

    monkeypatch.setattr(store, "lock", spy)
    service.rename(conn, store, service.get_site(conn, name), "target-name")
    assert sorted(held) == sorted([name, "target-name"])


# --- restore and rename are atomic too ---


def test_restore_of_a_deploy_whose_tree_is_gone_is_not_found(env):
    conn, store, owner = env
    name, first = service.publish(conn, store, site=None, user_id=owner["id"], staged=tree(store, "v1"))
    _, second = redeploy(conn, store, name, owner, "v2")
    shutil.rmtree(store.deploy_dir(name, first))
    with pytest.raises(service.SiteError) as info:
        service.restore(conn, store, service.get_site(conn, name), first)
    assert info.value.code == "not_found"
    assert served(store, name) == "v2" and service.get_site(conn, name)["current_deploy_id"] == second


def test_restore_whose_commit_fails_keeps_serving_the_current_deploy(env):
    conn, store, owner = env
    name, first = service.publish(conn, store, site=None, user_id=owner["id"], staged=tree(store, "v1"))
    _, second = redeploy(conn, store, name, owner, "v2")
    proxy = CommitFails(conn)
    proxy.armed = True
    with pytest.raises(sqlite3.OperationalError):
        service.restore(proxy, store, service.get_site(conn, name), first)
    conn.execute("ROLLBACK")
    assert served(store, name) == "v2"
    assert service.get_site(conn, name)["current_deploy_id"] == second


def test_rename_onto_a_leftover_directory_changes_nothing(env):
    conn, store, owner = env
    name, _ = service.publish(conn, store, site=None, user_id=owner["id"], staged=tree(store, "v1"))
    store.site_dir("leftover-dir").mkdir()
    with pytest.raises(service.SiteError) as info:
        service.rename(conn, store, service.get_site(conn, name), "leftover-dir")
    assert info.value.code == "name_taken"
    assert service.get_site(conn, name) is not None and served(store, name) == "v1"


def test_rename_whose_commit_fails_moves_the_directory_back(env):
    conn, store, owner = env
    name, _ = service.publish(conn, store, site=None, user_id=owner["id"], staged=tree(store, "v1"))
    proxy = CommitFails(conn)
    proxy.armed = True
    with pytest.raises(sqlite3.OperationalError):
        service.rename(proxy, store, service.get_site(conn, name), "other-name")
    conn.execute("ROLLBACK")
    assert service.get_site(conn, name) is not None
    assert served(store, name) == "v1" and not store.site_dir("other-name").exists()
