"""Which gateway a tenant uses, and the keys to talk to it.

One place answers that question, because the answer decides whose bank account a
guest's money lands in. Two places would eventually disagree, and the way you
would find out is a tenant asking where last month's takings went.

Resolution, in order:

1. **The tenant's own row**, when it exists and is enabled. This is the answer a
   platform wants: their keys, their account, their money.
2. **The deployment's environment**, when the tenant has no row. A single-hotel
   deployment configured ``RAZORPAY_KEY_ID`` and friends before any of this
   existed and must keep working.
3. **Mock**, otherwise. No money moves and the order ids say so.

Step 2 is a loaded gun on a multi-tenant deployment: a tenant with no row would
quietly transact into the deployment owner's account. It is kept because
removing it would break working installations, and made safe by being *visible* —
:class:`GatewayConfig` carries ``source``, the management API reports it, and
``/payments/credentials`` says in as many words which account a tenant is using.
Silence is what makes a fallback dangerous, not the fallback.
"""

from __future__ import annotations

import logging
import secrets
import uuid
from dataclasses import dataclass

from chirala_common.secretbox import SecretsNotConfigured, open_sealed, seal
from sqlalchemy import text
from sqlalchemy.orm import Session

from .settings import settings

log = logging.getLogger("uvicorn.error").getChild("credentials")


@dataclass(frozen=True)
class GatewayConfig:
    """Everything needed to transact for one tenant."""

    provider: str
    key_id: str
    key_secret: str
    webhook_secret: str
    #: 'tenant' | 'deployment' | 'none' — where these values came from. Carried
    #: so no screen and no log line has to guess whose account is in use.
    source: str

    @property
    def live(self) -> bool:
        return self.provider == "razorpay" and bool(
            self.key_id and self.key_secret)


MOCK = GatewayConfig(provider="mock", key_id="", key_secret="",
                     webhook_secret="", source="none")


def new_webhook_ref() -> str:
    """A fresh callback path segment.

    Not the organisation id: a public URL should not publish an internal id, and
    a value that can be rotated is worth having for the day one turns up in a
    support ticket.
    """
    return secrets.token_hex(16)


def _deployment_config() -> GatewayConfig:
    if settings.payment_provider == "razorpay" and settings.razorpay_key_id:
        return GatewayConfig(
            provider="razorpay",
            key_id=settings.razorpay_key_id,
            key_secret=settings.razorpay_key_secret,
            webhook_secret=settings.razorpay_webhook_secret,
            source="deployment",
        )
    return MOCK


def for_organization(db: Session,
                     organization_id: uuid.UUID) -> GatewayConfig:
    """The gateway this tenant transacts through."""
    row = db.execute(
        text("SELECT provider, key_id, key_secret_sealed, "
             "webhook_secret_sealed, enabled "
             "FROM finance.payment_credentials WHERE organization_id = :o"),
        {"o": organization_id},
    ).mappings().first()

    if row is None or not row["enabled"]:
        return _deployment_config()
    if row["provider"] == "mock":
        # An explicit choice, and it outranks the deployment's keys. A tenant
        # who set themselves to mock is testing and must not take real money.
        return GatewayConfig(provider="mock", key_id="", key_secret="",
                             webhook_secret="", source="tenant")
    try:
        return GatewayConfig(
            provider=row["provider"],
            key_id=row["key_id"] or "",
            key_secret=_unseal(row["key_secret_sealed"]),
            webhook_secret=_unseal(row["webhook_secret_sealed"]),
            source="tenant",
        )
    except Exception as exc:  # noqa: BLE001
        # An unreadable secret is a wrong or missing encryption key. Falling
        # back to the deployment's account here would send this tenant's money
        # somewhere else, which is far worse than refusing to transact, so this
        # returns mock and says so loudly.
        log.error("tenant %s has payment credentials that cannot be "
                  "decrypted (%s); treating as not configured",
                  organization_id, exc)
        return GatewayConfig(provider="mock", key_id="", key_secret="",
                             webhook_secret="", source="tenant")


def by_webhook_ref(db: Session, ref: str):
    """The tenant a callback belongs to, from its URL. ``None`` if unknown.

    Called before the body is parsed, which is the whole point: the signature
    cannot be checked until we know whose secret to check it with.
    """
    row = db.execute(
        text("SELECT organization_id, provider, webhook_secret_sealed, enabled "
             "FROM finance.payment_credentials WHERE webhook_ref = :r"),
        {"r": ref},
    ).mappings().first()
    if row is None or not row["enabled"]:
        return None
    if not row["webhook_secret_sealed"]:
        return None
    try:
        secret = _unseal(row["webhook_secret_sealed"])
    except Exception as exc:  # noqa: BLE001
        log.error("webhook secret for tenant %s cannot be decrypted: %s",
                  row["organization_id"], exc)
        return None
    return row["organization_id"], secret


def _unseal(value: str | None) -> str:
    if not value:
        return ""
    return open_sealed(settings.credential_encryption_keys, value)


def protect(value: str) -> str:
    """Seal a secret for storage, or refuse.

    Refusing is the point. Without a key this raises rather than storing the
    secret as it arrived, because a deployment that wrote gateway secrets in
    plaintext when a variable was unset would be worse than one that would not
    save them at all.
    """
    return seal(settings.credential_encryption_keys, value)


def keys_configured() -> bool:
    """Whether this service can store credentials at all."""
    try:
        seal(settings.credential_encryption_keys, "probe")
    except SecretsNotConfigured:
        return False
    return True
