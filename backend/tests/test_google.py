import time
import types

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from app.auth.google import GoogleAuthError, GoogleVerifier

KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
OTHER_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
CLIENT_ID = "cid.apps.googleusercontent.com"


class FakeJwks:
    def get_signing_key_from_jwt(self, token):
        return types.SimpleNamespace(key=KEY.public_key())


def make_token(key=KEY, **overrides):
    now = int(time.time())
    claims = {
        "iss": "https://accounts.google.com",
        "aud": CLIENT_ID,
        "sub": "1234567890",
        "email": "Ann@Zerde.ai",
        "email_verified": True,
        "name": "Ann",
        "picture": "https://example.test/a.png",
        "iat": now,
        "exp": now + 600,
    }
    claims.update(overrides)
    return jwt.encode(claims, key, algorithm="RS256", headers={"kid": "k1"})


@pytest.fixture
def verifier():
    return GoogleVerifier(CLIENT_ID, jwks_client=FakeJwks())


def test_valid_token(verifier):
    identity = verifier.verify(make_token())
    assert identity.email == "ann@zerde.ai"
    assert identity.sub == "1234567890" and identity.name == "Ann"


def test_short_issuer_is_accepted(verifier):
    assert verifier.verify(make_token(iss="accounts.google.com")).sub == "1234567890"


@pytest.mark.parametrize(
    "overrides",
    [
        {"aud": "someone-else"},
        {"iss": "https://evil.test"},
        {"exp": int(time.time()) - 120},
        {"email_verified": False},
    ],
)
def test_rejected_claims(verifier, overrides):
    with pytest.raises(GoogleAuthError):
        verifier.verify(make_token(**overrides))


def test_wrong_signature(verifier):
    with pytest.raises(GoogleAuthError):
        verifier.verify(make_token(key=OTHER_KEY))


def test_garbage_and_disabled():
    with pytest.raises(GoogleAuthError):
        GoogleVerifier(CLIENT_ID, jwks_client=FakeJwks()).verify("not.a.jwt")
    with pytest.raises(GoogleAuthError):
        GoogleVerifier("", jwks_client=FakeJwks()).verify(make_token())
