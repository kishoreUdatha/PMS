"""RFC 6238 time-based one-time passwords, and the recovery codes beside them.

Implemented rather than imported. TOTP is HMAC-SHA1 over a counter and a
truncation rule -- about thirty lines -- and the image carries no OTP library,
so adding one would mean a new dependency in the base image for something the
standard library already does. The algorithm has not changed since 2011 and is
not going to.

Every authenticator app in circulation implements the same defaults: SHA-1, six
digits, a thirty-second step. They are not configurable here on purpose -- a
deployment that picked SHA-256 would produce codes that Google Authenticator
rejects, and the failure would look like the user typing it wrong.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import struct
import time
from urllib.parse import quote

DIGITS = 6
STEP = 30
#: How many steps either side of now to accept. One means a code stays valid
#: for at most ninety seconds, which covers a phone whose clock has drifted a
#: little without widening the window enough to matter.
DRIFT = 1


def new_secret() -> str:
    """A fresh base32 seed, 160 bits, in the shape authenticators expect."""
    return base64.b32encode(secrets.token_bytes(20)).decode("ascii").rstrip("=")


def _code(secret: str, counter: int) -> str:
    key = base64.b32decode(secret + "=" * (-len(secret) % 8), casefold=True)
    digest = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    value = struct.unpack(">I", digest[offset:offset + 4])[0] & 0x7FFFFFFF
    return str(value % (10 ** DIGITS)).zfill(DIGITS)


def verify(secret: str, code: str, *, after: int | None = None,
           now: float | None = None) -> int | None:
    """The counter a code belongs to, or None if it belongs to none.

    Returns the counter rather than a boolean so the caller can store it and
    refuse the same code twice. Without that, a code shouted across a room --
    or read off a proxy log -- stays usable for the rest of its window.

    ``after`` is the last counter already spent; anything at or below it is
    rejected even when the arithmetic is right.

    Compared with ``compare_digest``: a plain ``==`` stops at the first
    differing character, and how long that took narrows the guess.
    """
    code = (code or "").strip().replace(" ", "")
    if not code.isdigit() or len(code) != DIGITS:
        return None
    current = int((now if now is not None else time.time()) // STEP)
    for drift in range(-DRIFT, DRIFT + 1):
        counter = current + drift
        if after is not None and counter <= after:
            continue
        if hmac.compare_digest(_code(secret, counter), code):
            return counter
    return None


def provisioning_uri(secret: str, *, account: str, issuer: str) -> str:
    """The otpauth:// URI an authenticator scans.

    Built here rather than a QR image: the URI is the payload, and rendering
    it to pixels is the browser's job. Sending a PNG from the server would put
    the seed through an extra layer for no benefit.
    """
    label = quote(f"{issuer}:{account}", safe="")
    return (f"otpauth://totp/{label}?secret={secret}"
            f"&issuer={quote(issuer, safe='')}"
            f"&algorithm=SHA1&digits={DIGITS}&period={STEP}")


def new_recovery_codes(count: int = 10) -> list[str]:
    """Single-use codes for the day the phone is lost.

    Grouped with a dash because people read them aloud and type them from
    paper. The alphabet excludes nothing clever -- base32 already has no 0/O
    or 1/I to confuse.
    """
    out = []
    for _ in range(count):
        raw = base64.b32encode(secrets.token_bytes(5)).decode("ascii").rstrip("=")
        out.append(f"{raw[:4]}-{raw[4:8]}")
    return out


def hash_code(code: str) -> str:
    """Match the convention password_tokens already uses: sha256 hex."""
    return hashlib.sha256(
        code.strip().upper().replace(" ", "").encode("utf-8")).hexdigest()
