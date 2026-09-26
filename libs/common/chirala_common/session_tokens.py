"""Session tokens: issued by iam at sign-in, checked by every service.

Until this existed a session token was ``base64("dev:<subject>")`` -- no
signature and no expiry. Anyone who knew or guessed a subject id could write
their own token for it, and one that leaked into a log or a screenshot worked
forever. The password check in front of it was a lock on a door beside an open
window.

**Format.** A compact JWT signed with HMAC-SHA256 (``header.payload.sig``),
built with the standard library. Plain HMAC rather than the OIDC validator in
``chirala_common.auth``: iam is the issuer and every service shares one
cluster-wide secret, so there is no key set to fetch and no second party to
trust. Keeping it to one algorithm, fixed here rather than read from the
token's header, removes the whole family of "alg: none" and key-confusion
tricks that general-purpose JWT libraries have had to patch.

**Claims.** ``sub`` (the user's subject id), ``iat``, ``exp`` and ``jti``. The
``jti`` is what sign-out revokes: a signed token cannot be recalled by the
client throwing it away, because a copy may exist elsewhere.

**Keys.** ``SESSION_SIGNING_KEY``, comma-separated, newest first -- the same
rotation scheme as ``CREDENTIAL_ENCRYPTION_KEYS``. The first key signs; any of
them verifies, so rotating is: put the new key in front, wait out the session
lifetime, drop the old one.

**The old dev tokens** are still accepted, but only when ``ENVIRONMENT`` is
exactly ``local``. Every fixture and developer session in the repository uses
them, and in local mode there is no secret worth protecting. Anywhere else
they are refused whatever else is configured -- a deployment that forgot to
set its environment gets the strict behaviour, not the lenient one, because
the default environment is ``production``.

This module is the one place that decides whether a bearer token names a
person. iam's own caller, the shared caller in ``chirala_common.authz`` and
``/auth/me`` all call :func:`subject_from_bearer`; there is no second copy to
drift.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import time
import uuid
from dataclasses import dataclass
from functools import lru_cache

from sqlalchemy import text
from sqlalchemy.orm import Session

#: Fixed. A token's header is not consulted to choose how to check it.
_HEADER = {"alg": "HS256", "typ": "JWT"}


@dataclass(frozen=True)
class SessionClaims:
    subject: str
    #: None for a local dev token, which has no identity of its own to revoke.
    jti: str | None
    expires_at: int | None


@lru_cache(maxsize=1)
def _settings():
    # Imported late and cached: the services each subclass these settings, but
    # the fields read here are the shared ones and come from the same
    # environment, so one instance serves all three.
    from .config import BaseServiceSettings

    return BaseServiceSettings()


def _keys(raw: str | None) -> list[bytes]:
    if raw is None:
        raw = _settings().session_signing_key
    return [k.strip().encode() for k in raw.split(",") if k.strip()]


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _unb64(data: str) -> bytes:
    return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))


def _sign(signing_input: bytes, key: bytes) -> bytes:
    return hmac.new(key, signing_input, hashlib.sha256).digest()


def issue(
    subject: str, *, key: str | None = None, ttl_seconds: int | None = None,
    environment: str | None = None,
) -> str:
    """A signed session token for ``subject``.

    With no key configured this falls back to a dev token -- but only in
    local mode. Elsewhere it refuses, which the startup check should already
    have made impossible: better a sign-in that fails loudly than one that
    hands out a forgeable token.
    """
    cfg = _settings()
    keys = _keys(key)
    env = environment if environment is not None else cfg.environment
    if not keys:
        if env == "local":
            return base64.urlsafe_b64encode(f"dev:{subject}".encode()).decode()
        raise RuntimeError(
            "SESSION_SIGNING_KEY is not set, so no session can be issued.")
    now = int(time.time())
    ttl = ttl_seconds if ttl_seconds is not None else cfg.session_ttl_minutes * 60
    payload = {"sub": subject, "iat": now, "exp": now + ttl,
               "jti": uuid.uuid4().hex}
    signing_input = (_b64(json.dumps(_HEADER, separators=(",", ":")).encode())
                     + "." + _b64(json.dumps(payload, separators=(",", ":")).encode()))
    sig = _sign(signing_input.encode(), keys[0])
    return signing_input + "." + _b64(sig)


def decode(
    token: str, *, key: str | None = None, environment: str | None = None,
    now: float | None = None,
) -> SessionClaims | None:
    """The claims of a genuine, unexpired token, or None.

    Says nothing about revocation; that needs the database and is
    :func:`is_revoked`'s job. None rather than an exception for every kind of
    failure, because every caller answers all of them with the same 401 and
    telling a forger which part they got wrong helps nobody else.
    """
    token = (token or "").strip()
    env = environment if environment is not None else _settings().environment
    parts = token.split(".")
    if len(parts) != 3:
        # Not a JWT. The only other shape ever issued is the dev token, and
        # that is honoured in local mode alone -- and only while no signing
        # key is configured. With a key, sign-in hands out signed tokens, so
        # a dev token is one somebody wrote by hand; a laptop running the
        # stack with a key should behave like the deployment it rehearses.
        if env != "local" or _keys(key):
            return None
        try:
            raw = base64.urlsafe_b64decode(token.encode()).decode()
        except (binascii.Error, UnicodeDecodeError, ValueError):
            return None
        if raw.startswith("dev:") and len(raw) > 4:
            return SessionClaims(subject=raw[4:], jti=None, expires_at=None)
        return None

    keys = _keys(key)
    if not keys:
        return None
    signing_input = f"{parts[0]}.{parts[1]}".encode()
    try:
        sig = _unb64(parts[2])
        header = json.loads(_unb64(parts[0]))
        payload = json.loads(_unb64(parts[1]))
    except (binascii.Error, UnicodeDecodeError, ValueError):
        return None
    if header != _HEADER or not isinstance(payload, dict):
        return None
    # Every key is tried, and compare_digest so the time taken says nothing
    # about how close a guess came.
    if not any(hmac.compare_digest(sig, _sign(signing_input, k)) for k in keys):
        return None

    sub, exp, jti = payload.get("sub"), payload.get("exp"), payload.get("jti")
    if not isinstance(sub, str) or not sub or not isinstance(jti, str) or not jti:
        return None
    if not isinstance(exp, int) or exp <= (now if now is not None else time.time()):
        return None
    return SessionClaims(subject=sub, jti=jti, expires_at=exp)


def is_revoked(db: Session, jti: str) -> bool:
    """True once the session has been signed out.

    One primary-key lookup per request. The table only ever holds sessions
    that were revoked and have not yet expired on their own, so it stays
    small; a cache in front of it would make sign-out take effect "soon"
    rather than now, which is not what sign-out means.
    """
    return db.execute(
        text("SELECT 1 FROM iam.revoked_sessions WHERE jti = :j"),
        {"j": jti},
    ).first() is not None


def claims_from_bearer(authorization: str | None, db: Session) -> SessionClaims | None:
    """The claims of a live session presented as ``Authorization: Bearer``."""
    if not authorization or not authorization.lower().startswith("bearer "):
        return None
    claims = decode(authorization.split(" ", 1)[1])
    if claims is None:
        return None
    if claims.jti is not None and is_revoked(db, claims.jti):
        return None
    return claims


def subject_from_bearer(authorization: str | None, db: Session) -> str | None:
    """The subject of a live session token, or None."""
    claims = claims_from_bearer(authorization, db)
    return claims.subject if claims else None
