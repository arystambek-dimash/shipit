import os

import pytest

from app.deploys.store import SiteStore


@pytest.fixture
def store(tmp_path):
    return SiteStore(tmp_path / "sites", tmp_path / "tmp")


def staged(store, text):
    path = store.new_staging_dir()
    (path / "index.html").write_text(text)
    return path


def test_publish_points_current_at_the_deploy(store):
    store.publish("demo", staged(store, "v1"), 1)
    assert (store.current_dir("demo") / "index.html").read_text() == "v1"
    assert os.readlink(store.current_dir("demo")) == "deploys/1"
    assert store.current_deploy("demo") == 1


def test_publish_again_and_activate_older(store):
    store.publish("demo", staged(store, "v1"), 1)
    store.publish("demo", staged(store, "v2"), 2)
    assert (store.current_dir("demo") / "index.html").read_text() == "v2"
    store.activate("demo", 1)
    assert (store.current_dir("demo") / "index.html").read_text() == "v1"
    with pytest.raises(FileNotFoundError):
        store.activate("demo", 99)


def test_prune_keeps_only_given_ids(store):
    for i in (1, 2, 3):
        store.publish("demo", staged(store, f"v{i}"), i)
    store.prune("demo", {2, 3})
    assert not store.deploy_dir("demo", 1).exists()
    assert store.deploy_dir("demo", 2).exists() and store.deploy_dir("demo", 3).exists()


def test_rename_and_delete(store):
    store.publish("old", staged(store, "v1"), 1)
    store.publish("taken", staged(store, "t"), 2)
    with pytest.raises(FileExistsError):
        store.rename("old", "taken")
    store.rename("old", "new")
    assert (store.current_dir("new") / "index.html").read_text() == "v1"
    assert not store.site_dir("old").exists()
    store.delete("new")
    assert not store.site_dir("new").exists()


def test_cleanup_temp_and_lock(store):
    leftover = store.new_staging_dir()
    (leftover / "x").write_text("x")
    store.cleanup_temp()
    assert not leftover.exists()
    with store.lock("demo"):
        pass
