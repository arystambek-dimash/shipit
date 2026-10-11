import pytest

from app import access, db
from app.config import Settings


@pytest.fixture
def conn(tmp_path):
    c = db.connect(tmp_path / "app.db")
    db.init_db(c)
    yield c
    c.close()


SETTINGS = Settings(admin_emails=frozenset({"boss@zerde.ai"}))


@pytest.mark.parametrize(
    "raw,expected",
    [
        (" Ann@Example.com ", "ann@example.com"),
        ("@Zerde.AI", "@zerde.ai"),
        ("@sub.zerde.ai", "@sub.zerde.ai"),
        ("not-an-email", None),
        ("@nodot", None),
        ("a@b", None),
        ("@", None),
        ("", None),
    ],
)
def test_normalize_pattern(raw, expected):
    assert access.normalize_pattern(raw) == expected


def test_normalize_email_rejects_domains():
    assert access.normalize_email("@zerde.ai") is None
    assert access.normalize_email("x" * 250 + "@a.co") is None


def test_admin_can_always_deploy(conn):
    assert access.is_admin(SETTINGS, "Boss@Zerde.ai")
    assert access.can_deploy(conn, SETTINGS, "boss@zerde.ai")


def test_allowlist_by_email_and_domain(conn):
    assert not access.can_deploy(conn, SETTINGS, "ann@example.com")
    assert access.add_to_allowlist(conn, "ann@example.com", "boss@zerde.ai")
    assert access.can_deploy(conn, SETTINGS, "Ann@Example.com")
    assert not access.can_deploy(conn, SETTINGS, "bob@example.com")
    assert access.add_to_allowlist(conn, "@example.com", "boss@zerde.ai")
    assert access.can_deploy(conn, SETTINGS, "bob@example.com")
    assert not access.can_deploy(conn, SETTINGS, "bob@sub.example.com")


def test_duplicates_and_removal(conn):
    assert access.add_to_allowlist(conn, "@example.com", "boss@zerde.ai")
    assert not access.add_to_allowlist(conn, "@example.com", "boss@zerde.ai")
    entries = access.list_allowlist(conn)
    assert [e["pattern"] for e in entries] == ["@example.com"]
    assert access.remove_from_allowlist(conn, entries[0]["id"])
    assert not access.remove_from_allowlist(conn, entries[0]["id"])
    assert access.list_allowlist(conn) == []


def test_can_deploy_rejects_non_email_input_even_with_domain_entry(conn):
    # Without "@" the domain derivation used to return the whole string, so
    # "example.com" and "@example.com" both mapped to the "@example.com" entry.
    assert access.add_to_allowlist(conn, "@example.com", "boss@zerde.ai")
    assert not access.can_deploy(conn, SETTINGS, "example.com")
    assert not access.can_deploy(conn, SETTINGS, "@example.com")
    assert access.can_deploy(conn, SETTINGS, "bob@example.com")
