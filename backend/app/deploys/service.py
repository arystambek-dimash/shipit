"""Site operations that touch both the database and the file store."""
from __future__ import annotations

import logging
import sqlite3
from contextlib import ExitStack, contextmanager
from typing import Any, Callable, Iterator, Mapping

from .. import db
from . import names
from .ingest import StagedTree
from .store import SiteStore

log = logging.getLogger("zerde.deploys")

KEEP_DEPLOYS = 10
_NAME_ATTEMPTS = 5
_SITE_COLUMNS = (
    "SELECT s.*, u.email AS owner_email, d.total_bytes, d.file_count, d.created_at AS deployed_at"
    " FROM sites s JOIN users u ON u.id = s.owner_id"
    " LEFT JOIN deploys d ON d.id = s.current_deploy_id"
)


class SiteError(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def get_site(conn: sqlite3.Connection, name: str) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM sites WHERE name = ?", (name,)).fetchone()


def list_sites(conn: sqlite3.Connection, owner_id: int | None = None) -> list[sqlite3.Row]:
    order = " ORDER BY s.updated_at DESC, s.id DESC"
    if owner_id is None:
        return conn.execute(_SITE_COLUMNS + order).fetchall()
    return conn.execute(_SITE_COLUMNS + " WHERE s.owner_id = ?" + order, (owner_id,)).fetchall()


def list_deploys(conn: sqlite3.Connection, site_id: int) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT d.*, u.email AS author_email FROM deploys d JOIN users u ON u.id = d.created_by"
        " WHERE d.site_id = ? ORDER BY d.id DESC",
        (site_id,),
    ).fetchall()


def can_manage(site: Mapping[str, Any], user: Mapping[str, Any], admin: bool) -> bool:
    return admin or site["owner_id"] == user["id"]


@contextmanager
def _locked_site(
    conn: sqlite3.Connection, store: SiteStore, site: Mapping[str, Any], *, also: tuple[str, ...] = ()
) -> Iterator[sqlite3.Row]:
    """Hold the per-site lock, then re-read the site: the row we were handed may be stale by now.

    The lock and the directory are keyed on the site's name, and a rename or delete that finished while
    we waited for the lock changes or removes it. A row whose id no longer has that name is rejected as
    not_found rather than acted on. `also` adds locks for other names (rename's target); every lock is
    taken in sorted order so two renames cannot deadlock.

    Lock order everywhere: file locks first, then the SQLite write transaction, never the reverse.
    """
    with ExitStack() as stack:
        for name in sorted({site["name"], *also}):
            stack.enter_context(store.lock(name))
        current = conn.execute("SELECT * FROM sites WHERE id = ?", (site["id"],)).fetchone()
        if current is None or current["name"] != site["name"]:
            raise SiteError("not_found")
        yield current


def _undo(what: str, action: Callable[[], object]) -> None:
    """Run a filesystem compensation without letting its failure mask the error that made it necessary."""
    try:
        action()
    except Exception:
        log.exception("could not undo %s", what)


def publish(
    conn: sqlite3.Connection,
    store: SiteStore,
    *,
    site_name: str | None,
    user_id: int,
    staged: StagedTree,
    now: int | None = None,
) -> tuple[str, int]:
    ts = db.now() if now is None else now
    if site_name is None:
        return _publish_new(conn, store, user_id, staged, ts)
    site = get_site(conn, site_name)
    if site is None:
        raise SiteError("not_found")
    with _locked_site(conn, store, site) as locked:
        return locked["name"], _deploy(conn, store, locked["name"], locked["id"], user_id, staged, ts)


def _publish_new(
    conn: sqlite3.Connection, store: SiteStore, user_id: int, staged: StagedTree, ts: int
) -> tuple[str, int]:
    for _ in range(_NAME_ATTEMPTS):
        name = names.generate_name(lambda n: get_site(conn, n) is not None)
        with store.lock(name):
            try:
                return name, _deploy(conn, store, name, None, user_id, staged, ts)
            except SiteError as error:
                if error.code != "name_taken":
                    raise
                # another request claimed this name between generating it and getting its lock: draw again
    raise RuntimeError("could not find a free site name")


def _deploy(
    conn: sqlite3.Connection,
    store: SiteStore,
    name: str,
    site_id: int | None,
    user_id: int,
    staged: StagedTree,
    ts: int,
) -> int:
    """Record a deploy and put its tree live as one unit; the caller holds the lock for `name`.

    A `site_id` of None creates the site as well. The database writes and the symlink swap share one
    transaction, with the swap last, so a failure anywhere leaves the old deploy live and the database
    untouched. If COMMIT itself fails after the swap, the swap is undone.
    """
    created = site_id is None
    previous = store.current_deploy(name)
    deploy_id: int | None = None
    swapping = False
    try:
        with db.transaction(conn):
            if created:
                if get_site(conn, name) is not None:
                    raise SiteError("name_taken")
                site_id = conn.execute(
                    "INSERT INTO sites (name, owner_id, created_at, updated_at) VALUES (?, ?, ?, ?)",
                    (name, user_id, ts, ts),
                ).lastrowid
            deploy_id = conn.execute(
                "INSERT INTO deploys (site_id, created_by, file_count, total_bytes, created_at)"
                " VALUES (?, ?, ?, ?, ?)",
                (site_id, user_id, staged.file_count, staged.total_bytes, ts),
            ).lastrowid
            conn.execute(
                "UPDATE sites SET current_deploy_id = ?, updated_at = ? WHERE id = ?", (deploy_id, ts, site_id)
            )
            keep = _drop_stale_rows(conn, site_id, deploy_id)
            swapping = True
            store.publish(name, staged.root, deploy_id)
    except BaseException:
        if swapping:
            _undo(
                f"the publish of deploy {deploy_id} to {name}",
                lambda: _revert_publish(store, name, deploy_id, previous, created),
            )
        raise
    # Committed. Collecting old trees is housekeeping: failing at it must not fail the deploy.
    try:
        store.prune(name, keep)
    except OSError:
        log.exception("could not prune old deploys of %s", name)
    return deploy_id


def _drop_stale_rows(conn: sqlite3.Connection, site_id: int, current_id: int) -> set[int]:
    """Delete the rows of deploys beyond the newest KEEP_DEPLOYS; return the ids whose trees stay."""
    rows = conn.execute("SELECT id FROM deploys WHERE site_id = ? ORDER BY id DESC", (site_id,)).fetchall()
    keep = {row["id"] for row in rows[:KEEP_DEPLOYS]} | {current_id}
    stale = [row["id"] for row in rows if row["id"] not in keep]
    if stale:
        conn.execute(f"DELETE FROM deploys WHERE id IN ({','.join('?' * len(stale))})", stale)
    return keep


def _revert_publish(store: SiteStore, name: str, deploy_id: int, previous: int | None, created: bool) -> None:
    if created:
        store.delete(name)  # the site is new: nothing else lives in its directory
        return
    if previous is not None and store.current_deploy(name) != previous:
        store.activate(name, previous)
    store.discard(name, deploy_id)


def restore(conn: sqlite3.Connection, store: SiteStore, site: sqlite3.Row, deploy_id: int, now: int | None = None) -> None:
    ts = db.now() if now is None else now
    with _locked_site(conn, store, site) as current:
        name = current["name"]
        row = conn.execute(
            "SELECT id FROM deploys WHERE id = ? AND site_id = ?", (deploy_id, current["id"])
        ).fetchone()
        if row is None:
            raise SiteError("not_found")
        previous = store.current_deploy(name)
        swapped = False
        try:
            with db.transaction(conn):
                conn.execute(
                    "UPDATE sites SET current_deploy_id = ?, updated_at = ? WHERE id = ?",
                    (deploy_id, ts, current["id"]),
                )
                try:
                    store.activate(name, deploy_id)
                except FileNotFoundError:  # a row whose tree is gone cannot be restored
                    raise SiteError("not_found") from None
                swapped = True
        except BaseException:
            if swapped and previous is not None:
                _undo(f"the restore of deploy {deploy_id} on {name}", lambda: store.activate(name, previous))
            raise


def rename(conn: sqlite3.Connection, store: SiteStore, site: sqlite3.Row, new_name: str, now: int | None = None) -> str:
    ts = db.now() if now is None else now
    new = names.normalize_name(new_name)
    if new == site["name"]:
        return new
    if not names.is_valid_name(new):
        raise SiteError("name_invalid")
    with _locked_site(conn, store, site, also=(new,)) as current:
        old = current["name"]
        renamed = False
        try:
            with db.transaction(conn):
                if get_site(conn, new) is not None:
                    raise SiteError("name_taken")
                conn.execute("UPDATE sites SET name = ?, updated_at = ? WHERE id = ?", (new, ts, current["id"]))
                try:
                    store.rename(old, new)
                except FileExistsError:  # a directory with no site behind it still blocks the name
                    raise SiteError("name_taken") from None
                renamed = True
        except BaseException:
            if renamed:
                _undo(f"the rename of {old} to {new}", lambda: store.rename(new, old))
            raise
    return new


def delete(conn: sqlite3.Connection, store: SiteStore, site: sqlite3.Row) -> None:
    with _locked_site(conn, store, site) as current:
        with db.transaction(conn):
            conn.execute("DELETE FROM deploys WHERE site_id = ?", (current["id"],))
            conn.execute("DELETE FROM sites WHERE id = ?", (current["id"],))
        store.delete(current["name"])
