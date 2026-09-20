"""OIDC token validation for FastAPI resource servers.

Validates a bearer JWT against Keycloak's JWKS and returns the caller principal.
Authorization (permissions, scope, action limits per §2) is enforced separately
in each service; this module only establishes *authenticated identity*.
"""

from __future__ import annotations

from dataclasses import dataclass

import httpx
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import jwt

_bearer = HTTPBearer(auto_error=True)


@dataclass
class Principal:
    """Authenticated caller. ``subject_id`` maps to schema ``users.subject_id``."""

    subject_id: str
    issuer: str
    claims: dict


class TokenValidator:
    """Caches JWKS and validates tokens. One instance per service."""

    def __init__(self, issuer: str, audience: str, jwks_url: str) -> None:
        self._issuer = issuer
        self._audience = audience
        self._jwks_url = jwks_url
        self._jwks: dict | None = None

    def _get_jwks(self) -> dict:
        if self._jwks is None:
            resp = httpx.get(self._jwks_url, timeout=5.0)
            resp.raise_for_status()
            self._jwks = resp.json()
        return self._jwks

    def validate(self, token: str) -> Principal:
        try:
            claims = jwt.decode(
                token,
                self._get_jwks(),
                audience=self._audience,
                issuer=self._issuer,
                options={"verify_at_hash": False},
            )
        except Exception as exc:  # noqa: BLE001 - surface as 401
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=f"Invalid token: {exc}",
            ) from exc
        return Principal(
            subject_id=claims.get("sub", ""),
            issuer=claims.get("iss", ""),
            claims=claims,
        )


def make_auth_dependency(validator: TokenValidator):
    """Build a FastAPI dependency that yields the authenticated ``Principal``."""

    def _dep(
        creds: HTTPAuthorizationCredentials = Depends(_bearer),
    ) -> Principal:
        return validator.validate(creds.credentials)

    return _dep
