"""Tamper-proof, expiring values for short-lived cookies (pending email, flash messages)."""
from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import time


def _mac(payload: str, secret: str) -> str:
    return hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()[:32]


def sign(value: str, secret: str, now: int | None = None) -> str:
    stamp = str(int(time.time()) if now is None else now)
    payload = f"{value}|{stamp}"
    raw = f"{payload}|{_mac(payload, secret)}".encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def unsign(token: str, secret: str, max_age: int, now: int | None = None) -> str | None:
    try:
        raw = base64.urlsafe_b64decode(token + "=" * (-len(token) % 4)).decode()
        value, stamp, mac = raw.rsplit("|", 2)
        issued = int(stamp)
    except (ValueError, UnicodeDecodeError, binascii.Error):
        return None
    if not hmac.compare_digest(mac.encode(), _mac(f"{value}|{stamp}", secret).encode()):
        return None
    current = int(time.time()) if now is None else now
    if current - issued > max_age:
        return None
    return value
