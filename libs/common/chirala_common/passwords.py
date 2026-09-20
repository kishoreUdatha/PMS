"""Hashing a password, and judging whether one is good enough to accept.

``hashlib.scrypt`` rather than a dependency. It is memory-hard, it is in the
standard library, and shipping it costs no image rebuild — which matters here
because a service that cannot be redeployed cannot be fixed. bcrypt or argon2
would both be defensible; none of the three is the weak link.

The stored form carries its own parameters::

    scrypt$16384$8$1$<salt b64>$<hash b64>

so the cost can be raised later without invalidating every existing hash: an
old record still says how it was made, and can be rehashed on next sign-in.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import secrets

#: Cost. n is the memory/CPU factor; at 16384 a hash takes a few tens of
#: milliseconds, which is slow enough to hurt a guesser and fast enough that a
#: login does not feel stuck.
_N = 16384
_R = 8
_P = 1
_KEYLEN = 32
_SALT_BYTES = 16

MIN_LENGTH = 10


def hash_password(password: str) -> str:
    """The storable form of a password. Never reversible."""
    salt = secrets.token_bytes(_SALT_BYTES)
    digest = hashlib.scrypt(
        password.encode("utf-8"), salt=salt, n=_N, r=_R, p=_P, dklen=_KEYLEN,
    )
    return "$".join((
        "scrypt", str(_N), str(_R), str(_P),
        base64.b64encode(salt).decode(), base64.b64encode(digest).decode(),
    ))


def verify_password(password: str, stored: str) -> bool:
    """Whether the password matches, in constant time.

    A malformed stored value returns False rather than raising: a corrupt row
    should fail the sign-in, not the service.
    """
    try:
        scheme, n, r, p, salt_b64, hash_b64 = stored.split("$")
        if scheme != "scrypt":
            return False
        digest = hashlib.scrypt(
            password.encode("utf-8"),
            salt=base64.b64decode(salt_b64),
            n=int(n), r=int(r), p=int(p),
            dklen=len(base64.b64decode(hash_b64)),
        )
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(digest, base64.b64decode(hash_b64))


def problem(password: str) -> str | None:
    """Why this password is not acceptable, or None.

    Deliberately short on rules. Forced symbols and digits push people towards
    ``Password1!`` and a sticky note; length is what actually costs a guesser,
    so length is what is asked for.
    """
    if len(password) < MIN_LENGTH:
        return f"Use at least {MIN_LENGTH} characters."
    if password.strip() == "":
        return "A password cannot be only spaces."
    lowered = password.lower()
    if lowered in {"password", "12345678901", "qwertyuiop", "letmein123"}:
        return "That is one of the first passwords anyone would try."
    if len(set(password)) < 4:
        return "Use a few more different characters."
    return None


def new_token() -> tuple[str, str]:
    """A single-use link token, and the hash to store for it.

    Only the hash is kept. A database that leaks should not hand over working
    links any more than it hands over working passwords.
    """
    token = secrets.token_urlsafe(32)
    return token, token_hash(token)


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()
