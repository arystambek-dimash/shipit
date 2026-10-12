import sqlite3

import pytest

from app import db, users
from app.auth import sessions


@pytest.fixture
def conn(tmp_path):
    c = db.connect(tmp_path / "x" / "app.db")
    db.init_db(c)
    yield c
    c.close()


def test_connect_uses_wal_and_foreign_keys(conn):
    assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1


def test_init_db_is_idempotent(conn):
    db.init_db(conn)
    tables = {r["name"] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"users", "allowlist", "sessions", "login_codes", "sites", "deploys"} <= tables


def test_transaction_rolls_back_on_error(conn):
    with pytest.raises(RuntimeError):
        with db.transaction(conn):
            conn.execute("INSERT INTO allowlist (pattern, added_by, created_at) VALUES ('a@b.co', 'x', 1)")
            raise RuntimeError("boom")
    assert conn.execute("SELECT COUNT(*) FROM allowlist").fetchone()[0] == 0


def test_upsert_creates_then_updates(conn):
    user = users.upsert_user(conn, "  Ann@Example.COM ", now=100)
    assert user["email"] == "ann@example.com"
    assert user["created_at"] == 100 and user["last_login_at"] == 100
    again = users.upsert_user(conn, "ann@example.com", name="Ann", now=200)
    assert again["id"] == user["id"]
    assert again["name"] == "Ann" and again["last_login_at"] == 200 and again["created_at"] == 100


def test_upsert_links_google_account_to_email_user(conn):
    user = users.upsert_user(conn, "ann@example.com", now=100)
    linked = users.upsert_user(conn, "ann@example.com", google_sub="g-1", now=150)
    assert linked["id"] == user["id"] and linked["google_sub"] == "g-1"
    # Same Google account, email changed at Google: still the same user, now under the new address.
    same = users.upsert_user(conn, "ann.new@example.com", google_sub="g-1", now=160)
    assert same["id"] == user["id"]
    assert same["email"] == "ann.new@example.com"
    assert conn.execute("SELECT COUNT(*) FROM users WHERE email = 'ann@example.com'").fetchone()[0] == 0


def test_email_change_at_google_ends_the_rows_sessions(conn):
    user = users.upsert_user(conn, "ann@example.com", google_sub="g-1", now=100)
    sessions.create_session(conn, user["id"], now=100)
    users.upsert_user(conn, "ann@example.com", google_sub="g-1", now=110)  # same address: sessions stay
    assert conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 1
    users.upsert_user(conn, "ann@elsewhere.example", google_sub="g-1", now=120)
    assert conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 0


def test_google_account_moves_to_the_row_that_owns_its_new_email(conn):
    old = users.upsert_user(conn, "ann@example.com", google_sub="g-1", now=100)
    sessions.create_session(conn, old["id"], now=100)  # opened when the account proved ann@example.com
    new = users.upsert_user(conn, "ann.new@example.com", now=110)  # signed in by email code
    sessions.create_session(conn, new["id"], now=110)
    signed_in = users.upsert_user(conn, "ann.new@example.com", google_sub="g-1", now=120)
    assert signed_in["id"] == new["id"] and signed_in["google_sub"] == "g-1"
    left = users.get_user(conn, old["id"])
    assert left["email"] == "ann@example.com" and left["google_sub"] is None
    remaining = [row["user_id"] for row in conn.execute("SELECT user_id FROM sessions")]
    assert remaining == [new["id"]]


def test_the_latest_google_account_to_prove_an_email_holds_the_link(conn):
    user = users.upsert_user(conn, "ann@example.com", google_sub="g-old", now=100)
    again = users.upsert_user(conn, "ann@example.com", google_sub="g-new", now=110)
    assert again["id"] == user["id"] and again["google_sub"] == "g-new"
    # The old Google account now has another address; it must not carry this row off with it.
    other = users.upsert_user(conn, "someone@else.example", google_sub="g-old", now=120)
    assert other["id"] != user["id"]
    assert users.get_user(conn, user["id"])["email"] == "ann@example.com"


def test_emails_are_unique(conn):
    users.upsert_user(conn, "a@b.co", now=1)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO users (email, created_at, last_login_at) VALUES ('a@b.co', 1, 1)")
