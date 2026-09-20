"""Second-factor enrolment and verification for platform staff (screen 02).

Sign-in becomes two steps once somebody is enrolled: the password proves the
credential, the code proves the device. The first step no longer returns a
session -- it returns a short-lived challenge that is worth nothing on its own,
so a stolen password gets an attacker a token that can do exactly one thing:
be exchanged for a session by somebody holding the phone.

**Enrolment is two steps too.** A secret is generated and stored ``pending``;
it only becomes ``active`` when the operator produces a code from it. Marking
somebody enrolled the moment a secret exists locks out anybody whose
authenticator did not actually take it, and the only way back would be the
database.

Recovery codes are issued once, shown once, and stored hashed. Spending one
signs you in and burns it.
"""

from __future__ import annotations

import base64
import logging
import secrets
import time
import uuid

from chirala_common.db import system_context
from chirala_common.routing import TransactionalRoute
from chirala_common.secretbox import SecretsNotConfigured, open_sealed, seal
from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.orm import Session

from . import totp
from .audit import record_audit
from .authz import Caller, get_caller
from .database import get_session
from .settings import settings

mfa_router = APIRouter(prefix="/auth/mfa", tags=["mfa"],
                       route_class=TransactionalRoute)

#: How long a half-finished sign-in stays usable. Long enough to open an
#: authenticator, short enough that a challenge left in a log is stale.
CHALLENGE_SECONDS = 300


def _keys() -> str:
    keys = getattr(settings, "credential_encryption_keys", "") or ""
    if not keys:
        raise HTTPException(
            status_code=503,
            detail="Second-factor storage is not configured on this "
                   "deployment (CREDENTIAL_ENCRYPTION_KEYS).")
    return keys


def make_challenge(user_id: uuid.UUID) -> str:
    """A signed, expiring token that stands for "password accepted".

    Signed with the same key material that protects the secrets, so it cannot
    be forged without the key, and carrying its own expiry so a replay of an
    old one is refused on arithmetic rather than on a lookup.
    """
    payload = f"{user_id}:{int(time.time()) + CHALLENGE_SECONDS}"
    return base64.urlsafe_b64encode(seal(_keys(), payload).encode()).decode()


def read_challenge(token: str) -> uuid.UUID:
    try:
        payload = open_sealed(
            _keys(), base64.urlsafe_b64decode(token.encode()).decode())
        raw_id, expires = payload.rsplit(":", 1)
        if int(expires) < int(time.time()):
            raise ValueError("expired")
        return uuid.UUID(raw_id)
    except HTTPException:
        raise
    except Exception:  # noqa: BLE001
        # The caller is told nothing useful -- a challenge that failed for a
        # bad signature and one that failed for age must look identical, or
        # the difference becomes an oracle. The operator, on the other hand,
        # needs to know which: a deployment whose key changed would otherwise
        # present as "everybody's sign-in suddenly expires".
        logging.getLogger("uvicorn.error").getChild("mfa").warning(
            "sign-in challenge rejected", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="That sign-in attempt has expired. Start again.",
        ) from None


def mfa_state(db: Session, user_id: uuid.UUID) -> dict | None:
    row = db.execute(
        text("SELECT user_id, secret, status, last_counter FROM iam.user_mfa "
             "WHERE user_id = :u"),
        {"u": user_id},
    ).mappings().first()
    return dict(row) if row else None


def _spend_recovery(db: Session, user_id: uuid.UUID, code: str) -> bool:
    """Burn a recovery code, if it is one of this user's unused ones."""
    return db.execute(
        text("UPDATE iam.user_mfa_recovery SET used_at = now() "
             "WHERE user_id = :u AND code_hash = :h AND used_at IS NULL"),
        {"u": user_id, "h": totp.hash_code(code)},
    ).rowcount > 0


def check_second_factor(db: Session, user_id: uuid.UUID, code: str) -> bool:
    """Accept a TOTP code, or a recovery code, for this user.

    Tried in that order and both in constant-ish time from the caller's point
    of view: a wrong code is a wrong code, and which kind it failed to be is
    not something the response should reveal.
    """
    state = mfa_state(db, user_id)
    if not state or state["status"] != "active":
        return False
    try:
        secret = open_sealed(_keys(), state["secret"])
    except SecretsNotConfigured:
        raise HTTPException(
            status_code=503, detail="Second-factor storage is unavailable.")
    except Exception:  # noqa: BLE001
        # The key that wrote this secret is gone. Loud, not silent: treating
        # an unreadable second factor as absent would disable MFA by accident.
        raise HTTPException(
            status_code=503,
            detail="Your second factor cannot be read on this deployment. "
                   "Contact another platform administrator.") from None

    counter = totp.verify(secret, code, after=state["last_counter"])
    if counter is not None:
        db.execute(
            text("UPDATE iam.user_mfa SET last_counter = :c, "
                 "last_used_at = now(), updated_at = now() WHERE user_id = :u"),
            {"c": counter, "u": user_id},
        )
        return True
    return _spend_recovery(db, user_id, code)


# ------------------------------------------------------------- enrolment ---

class EnrolOut(BaseModel):
    secret: str
    otpauth_uri: str
    recovery_codes: list[str]


@mfa_router.post("/enrol", response_model=EnrolOut)
def enrol(request: Request,
          db: Session = Depends(get_session),
          caller: Caller = Depends(get_caller)):
    """Start enrolment: a new secret, its QR payload, and recovery codes.

    Re-enrolling replaces whatever was there, which is the honest behaviour
    for somebody who has lost their phone and still has a session. The old
    secret and its unused recovery codes stop working at that moment rather
    than lingering as a second way in.

    Returns the secret in clear **once**. There is no route that reads it back.
    """
    if caller.user_id is None:
        raise HTTPException(status_code=403, detail="No account")

    system_context(db, reason=f"mfa: enrol {caller.subject}")
    secret = totp.new_secret()
    codes = totp.new_recovery_codes()

    db.execute(
        text(
            """
            INSERT INTO iam.user_mfa (user_id, secret, status, confirmed_at,
                                      last_counter)
            VALUES (:u, :s, 'pending', NULL, NULL)
            ON CONFLICT (user_id) DO UPDATE
               SET secret = EXCLUDED.secret, status = 'pending',
                   confirmed_at = NULL, last_counter = NULL,
                   updated_at = now()
            """
        ),
        {"u": caller.user_id, "s": seal(_keys(), secret)},
    )
    db.execute(text("DELETE FROM iam.user_mfa_recovery WHERE user_id = :u"),
               {"u": caller.user_id})
    for c in codes:
        db.execute(
            text("INSERT INTO iam.user_mfa_recovery (user_id, code_hash) "
                 "VALUES (:u, :h)"),
            {"u": caller.user_id, "h": totp.hash_code(c)},
        )

    record_audit(
        db, action="mfa.enrolment.started", entity_type="user",
        entity_id=str(caller.user_id), actor_subject=caller.subject,
        after={"status": "pending"})

    return EnrolOut(
        secret=secret,
        otpauth_uri=totp.provisioning_uri(
            secret, account=caller.subject, issuer=settings.platform_name),
        recovery_codes=codes,
    )


class ConfirmIn(BaseModel):
    code: str = Field(min_length=6, max_length=10)


@mfa_router.post("/confirm")
def confirm(request: Request, body: ConfirmIn,
            db: Session = Depends(get_session),
            caller: Caller = Depends(get_caller)):
    """Prove the authenticator took the secret, and turn it on."""
    if caller.user_id is None:
        raise HTTPException(status_code=403, detail="No account")
    system_context(db, reason=f"mfa: confirm {caller.subject}")

    state = mfa_state(db, caller.user_id)
    if not state:
        raise HTTPException(status_code=409, detail="Start enrolment first.")
    if state["status"] == "active":
        return {"status": "active"}

    counter = totp.verify(open_sealed(_keys(), state["secret"]), body.code)
    if counter is None:
        raise HTTPException(
            status_code=422,
            detail="That code is not right. Check your authenticator's clock "
                   "and try the current code.")

    db.execute(
        text("UPDATE iam.user_mfa SET status = 'active', confirmed_at = now(), "
             "last_counter = :c, updated_at = now() WHERE user_id = :u"),
        {"c": counter, "u": caller.user_id},
    )
    db.execute(
        text("UPDATE iam.users SET mfa_status = 'active', updated_at = now() "
             "WHERE id = :u"),
        {"u": caller.user_id},
    )
    record_audit(
        db, action="mfa.enrolment.completed", entity_type="user",
        entity_id=str(caller.user_id), actor_subject=caller.subject,
        before={"status": "pending"}, after={"status": "active"})
    return {"status": "active"}


@mfa_router.get("/status")
def mfa_status(db: Session = Depends(get_session),
               caller: Caller = Depends(get_caller)):
    """Whether this account has a second factor, and how many codes are left."""
    if caller.user_id is None:
        raise HTTPException(status_code=403, detail="No account")
    system_context(db, reason=f"mfa: status for {caller.subject}")
    state = mfa_state(db, caller.user_id)
    unused = db.execute(
        text("SELECT count(*) FROM iam.user_mfa_recovery "
             "WHERE user_id = :u AND used_at IS NULL"),
        {"u": caller.user_id},
    ).scalar()
    return {
        "status": state["status"] if state else "none",
        "recovery_codes_unused": int(unused or 0),
        "required": bool(getattr(settings, "platform_mfa_required", False)),
    }


class VerifyIn(BaseModel):
    #: The challenge from step one of sign-in.
    challenge: str = Field(min_length=10)
    code: str = Field(min_length=6, max_length=10)
