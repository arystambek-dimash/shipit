"""Verify "Sign in with Google" ID tokens. Needs only the public OAuth client ID."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import jwt

JWKS_URL = "https://www.googleapis.com/oauth2/v3/certs"
ISSUERS = frozenset({"accounts.google.com", "https://accounts.google.com"})


class GoogleAuthError(Exception):
    pass


@dataclass(frozen=True)
class GoogleIdentity:
    sub: str
    email: str
    name: str
    picture: str


class GoogleVerifier:
    def __init__(self, client_id: str, jwks_client: Any = None) -> None:
        self.client_id = client_id
        self._jwks = jwks_client or jwt.PyJWKClient(JWKS_URL, lifespan=3600)

    def verify(self, token: str) -> GoogleIdentity:
        if not self.client_id:
            raise GoogleAuthError("Google sign-in is not configured")
        try:
            key = self._jwks.get_signing_key_from_jwt(token).key
            claims = jwt.decode(
                token,
                key,
                algorithms=["RS256"],
                audience=self.client_id,
                options={"require": ["iss", "aud", "sub", "exp", "iat", "email"]},
                leeway=30,
            )
        except jwt.PyJWTError as exc:
            raise GoogleAuthError(str(exc)) from exc
        if claims["iss"] not in ISSUERS:
            raise GoogleAuthError("wrong issuer")
        if claims.get("email_verified") not in (True, "true"):
            raise GoogleAuthError("email is not verified")
        return GoogleIdentity(
            sub=str(claims["sub"]),
            email=str(claims["email"]).strip().lower(),
            name=str(claims.get("name", "")),
            picture=str(claims.get("picture", "")),
        )
