import base64

from app import signing


def test_round_trip_with_unicode_and_pipes():
    token = signing.sign("ok:Опубликовано | done", "secret", now=1000)
    assert token.isascii()
    assert signing.unsign(token, "secret", max_age=60, now=1030) == "ok:Опубликовано | done"


def test_rejects_wrong_secret_expired_and_garbage():
    token = signing.sign("a@b.co", "secret", now=1000)
    assert signing.unsign(token, "other", max_age=60, now=1001) is None
    assert signing.unsign(token, "secret", max_age=60, now=1061) is None
    assert signing.unsign("not-a-token", "secret", max_age=60) is None
    assert signing.unsign(token[:-2] + "xx", "secret", max_age=60, now=1001) is None


def test_non_ascii_mac_is_rejected_not_raised():
    forged = base64.urlsafe_b64encode("a|1000|é".encode()).decode().rstrip("=")
    assert signing.unsign(forged, "secret", max_age=60, now=1001) is None
