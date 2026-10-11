import re

import pytest

from app import db
from app.auth import email_code as ec

SECRET = "test-secret"
EMAIL = "a@b.co"


@pytest.fixture
def conn(tmp_path):
    c = db.connect(tmp_path / "app.db")
    db.init_db(c)
    yield c
    c.close()


def test_issue_and_verify_once(conn):
    code = ec.issue_code(conn, SECRET, EMAIL, "1.1.1.1", now=1000)
    assert re.fullmatch(r"\d{6}", code)
    stored = conn.execute("SELECT code_hash FROM login_codes").fetchone()["code_hash"]
    assert code not in stored
    assert ec.verify_code(conn, SECRET, EMAIL, code, now=1010) is True
    assert ec.verify_code(conn, SECRET, EMAIL, code, now=1011) is False


def test_spaces_are_ignored(conn):
    code = ec.issue_code(conn, SECRET, EMAIL, "1.1.1.1", now=1000)
    assert ec.verify_code(conn, SECRET, EMAIL, f" {code[:3]} {code[3:]} ", now=1001)


def test_code_expires(conn):
    code = ec.issue_code(conn, SECRET, EMAIL, "1.1.1.1", now=1000)
    assert ec.verify_code(conn, SECRET, EMAIL, code, now=1000 + ec.CODE_TTL) is False


def test_wrong_attempts_burn_the_code(conn):
    code = ec.issue_code(conn, SECRET, EMAIL, "1.1.1.1", now=1000)
    wrong = "000000" if code != "000000" else "111111"
    for _ in range(ec.MAX_ATTEMPTS):
        assert ec.verify_code(conn, SECRET, EMAIL, wrong, now=1001) is False
    assert ec.verify_code(conn, SECRET, EMAIL, code, now=1002) is False


def test_new_code_replaces_old(conn):
    old = ec.issue_code(conn, SECRET, EMAIL, "1.1.1.1", now=1000)
    new = ec.issue_code(conn, SECRET, EMAIL, "1.1.1.1", now=1000 + ec.EMAIL_INTERVAL)
    if old != new:
        assert ec.verify_code(conn, SECRET, EMAIL, old, now=1061) is False
    assert ec.verify_code(conn, SECRET, EMAIL, new, now=1062) is True


def test_resend_interval(conn):
    ec.issue_code(conn, SECRET, EMAIL, "1.1.1.1", now=1000)
    assert ec.seconds_until_resend(conn, EMAIL, now=1030) == 30
    with pytest.raises(ec.RateLimited) as info:
        ec.issue_code(conn, SECRET, EMAIL, "1.1.1.1", now=1030)
    assert info.value.scope == "email_interval" and info.value.retry_after == 30
    assert ec.seconds_until_resend(conn, "new@b.co", now=1030) == 0


def test_hourly_limit_per_email(conn):
    for i in range(ec.EMAIL_PER_HOUR):
        ec.issue_code(conn, SECRET, EMAIL, "1.1.1.1", now=1000 + i * 61)
    with pytest.raises(ec.RateLimited) as info:
        ec.issue_code(conn, SECRET, EMAIL, "1.1.1.1", now=1000 + 5 * 61)
    assert info.value.scope == "email_hour"
    assert info.value.retry_after == 1000 + 3600 - (1000 + 5 * 61)


def test_hourly_limit_per_ip(conn):
    for i in range(ec.IP_PER_HOUR):
        ec.issue_code(conn, SECRET, f"u{i}@b.co", "9.9.9.9", now=1000 + i)
    with pytest.raises(ec.RateLimited) as info:
        ec.issue_code(conn, SECRET, "late@b.co", "9.9.9.9", now=1100)
    assert info.value.scope == "ip"
