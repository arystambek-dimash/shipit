import re
import threading

import pytest

from app import db
from app.auth import email_code as ec

SECRET = "test-secret"
EMAIL = "a@b.co"


@pytest.fixture
def db_path(tmp_path):
    return tmp_path / "app.db"


@pytest.fixture
def conn(db_path):
    c = db.connect(db_path)
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


def _race(db_path, n, args_for):
    """Call issue_code from n threads, each on its own connection, released together."""
    barrier = threading.Barrier(n)
    results: list = [None] * n

    def work(i):
        c = db.connect(db_path)
        try:
            barrier.wait(timeout=10)
            email, ip = args_for(i)
            try:
                results[i] = ec.issue_code(c, SECRET, email, ip, now=1000)
            except ec.RateLimited as exc:
                results[i] = exc
        finally:
            c.close()

    threads = [threading.Thread(target=work, args=(i,)) for i in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
    assert not any(t.is_alive() for t in threads)
    # Anything other than a code or RateLimited (e.g. "database is locked") is a failure.
    assert all(isinstance(r, (str, ec.RateLimited)) for r in results), results
    return results


def test_concurrent_requests_for_one_email_issue_exactly_one_code(conn, db_path):
    n = 30
    results = _race(db_path, n, lambda i: (EMAIL, f"10.0.0.{i}"))
    issued = [r for r in results if isinstance(r, str)]
    limited = [r for r in results if isinstance(r, ec.RateLimited)]
    assert len(issued) == 1
    assert len(limited) == n - 1
    assert {r.scope for r in limited} == {"email_interval"}
    assert conn.execute("SELECT COUNT(*) AS n FROM login_codes WHERE email = ?", (EMAIL,)).fetchone()["n"] == 1
    assert ec.verify_code(conn, SECRET, EMAIL, issued[0], now=1001) is True


def test_concurrent_requests_from_one_ip_stop_at_the_hourly_cap(conn, db_path):
    n = ec.IP_PER_HOUR + 10
    results = _race(db_path, n, lambda i: (f"u{i}@b.co", "9.9.9.9"))
    issued = [r for r in results if isinstance(r, str)]
    limited = [r for r in results if isinstance(r, ec.RateLimited)]
    assert len(issued) == ec.IP_PER_HOUR
    assert len(limited) == n - ec.IP_PER_HOUR
    assert {r.scope for r in limited} == {"ip"}
    assert conn.execute("SELECT COUNT(*) AS n FROM login_codes WHERE ip = '9.9.9.9'").fetchone()["n"] == ec.IP_PER_HOUR


def test_rate_limited_issue_leaves_no_trace(conn):
    first = ec.issue_code(conn, SECRET, EMAIL, "1.1.1.1", now=1000)
    with pytest.raises(ec.RateLimited):
        ec.issue_code(conn, SECRET, EMAIL, "1.1.1.1", now=1010)
    assert conn.in_transaction is False
    assert conn.execute("SELECT COUNT(*) AS n FROM login_codes").fetchone()["n"] == 1
    # The rejected request must not have consumed the code that is still valid.
    assert ec.verify_code(conn, SECRET, EMAIL, first, now=1011) is True


def test_seconds_until_resend_covers_every_limit(conn):
    # The resend button counts down to when a code can really be sent, not only the 60 s interval.
    for i in range(ec.EMAIL_PER_HOUR):
        ec.issue_code(conn, SECRET, EMAIL, "1.1.1.1", now=1000 + i * 61)
    now = 1000 + 5 * 61 + 100
    assert ec.seconds_until_resend(conn, EMAIL, now=now) == 1000 + ec.HOUR - now
    for i in range(ec.IP_PER_HOUR):
        ec.issue_code(conn, SECRET, f"u{i}@b.co", "2.2.2.2", now=2000 + i)
    assert ec.seconds_until_resend(conn, "fresh@b.co", now=2100, ip="2.2.2.2") == 2000 + ec.HOUR - 2100
    assert ec.seconds_until_resend(conn, "fresh@b.co", now=2100, ip="3.3.3.3") == 0
    assert ec.seconds_until_resend(conn, "fresh@b.co", now=2100) == 0
