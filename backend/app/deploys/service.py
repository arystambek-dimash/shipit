"""Site operations that touch both the database and the file store."""
from __future__ import annotations

import sqlite3
from typing import Any, Mapping

from .. import db
from . import names
from .ingest import StagedTree
from .store import SiteStore

KEEP_DEPLOYS = 10
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
    created = site_name is None
    if created:
        with db.transaction(conn):
            name = names.generate_name(lambda n: get_site(conn, n) is not None)
            site_id = conn.execute(
                "INSERT INTO sites (name, owner_id, created_at, updated_at) VALUES (?, ?, ?, ?)",
                (name, user_id, ts, ts),
            ).lastrowid
    else:
        site = get_site(conn, site_name)
        if site is None:
            raise SiteError("not_found")
        name, site_id = site["name"], site["id"]
    with store.lock(name):
        deploy_id = conn.execute(
            "INSERT INTO deploys (site_id, created_by, file_count, total_bytes, created_at) VALUES (?, ?, ?, ?, ?)",
            (site_id, user_id, staged.file_count, staged.total_bytes, ts),
        ).lastrowid
        try:
            store.publish(name, staged.root, deploy_id)
        except BaseException:
            conn.execute("DELETE FROM deploys WHERE id = ?", (deploy_id,))
            if created:
                conn.execute("DELETE FROM sites WHERE id = ?", (site_id,))
                store.delete(name)
            raise
        conn.execute(
            "UPDATE sites SET current_deploy_id = ?, updated_at = ? WHERE id = ?", (deploy_id, ts, site_id)
        )
        _prune(conn, store, name, site_id, deploy_id)
    return name, deploy_id


def _prune(conn: sqlite3.Connection, store: SiteStore, name: str, site_id: int, current_id: int) -> None:
    rows = conn.execute("SELECT id FROM deploys WHERE site_id = ? ORDER BY id DESC", (site_id,)).fetchall()
    keep = {row["id"] for row in rows[:KEEP_DEPLOYS]} | {current_id}
    stale = [row["id"] for row in rows if row["id"] not in keep]
    if stale:
        conn.execute(f"DELETE FROM deploys WHERE id IN ({','.join('?' * len(stale))})", stale)
    store.prune(name, keep)


def restore(conn: sqlite3.Connection, store: SiteStore, site: sqlite3.Row, deploy_id: int, now: int | None = None) -> None:
    ts = db.now() if now is None else now
    row = conn.execute("SELECT id FROM deploys WHERE id = ? AND site_id = ?", (deploy_id, site["id"])).fetchone()
    if row is None:
        raise SiteError("not_found")
    with store.lock(site["name"]):
        store.activate(site["name"], deploy_id)
        conn.execute(
            "UPDATE sites SET current_deploy_id = ?, updated_at = ? WHERE id = ?", (deploy_id, ts, site["id"])
        )


def rename(conn: sqlite3.Connection, store: SiteStore, site: sqlite3.Row, new_name: str, now: int | None = None) -> str:
    ts = db.now() if now is None else now
    new = names.normalize_name(new_name)
    if new == site["name"]:
        return new
    if not names.is_valid_name(new):
        raise SiteError("name_invalid")
    if get_site(conn, new) is not None:
        raise SiteError("name_taken")
    with store.lock(site["name"]):
        try:
            store.rename(site["name"], new)
        except FileExistsError:
            raise SiteError("name_taken") from None
        try:
            conn.execute("UPDATE sites SET name = ?, updated_at = ? WHERE id = ?", (new, ts, site["id"]))
        except sqlite3.IntegrityError:
            store.rename(new, site["name"])
            raise SiteError("name_taken") from None
    return new


def delete(conn: sqlite3.Connection, store: SiteStore, site: sqlite3.Row) -> None:
    with store.lock(site["name"]):
        with db.transaction(conn):
            conn.execute("DELETE FROM deploys WHERE site_id = ?", (site["id"],))
            conn.execute("DELETE FROM sites WHERE id = ?", (site["id"],))
        store.delete(site["name"])
