"""A tenant's own payment gateway, configured by the tenant.

Everything here is organisation-scoped, because a gateway account belongs to a
business with a bank account and that is what an organisation is.

Two rules this module keeps:

**A secret that comes in never goes back out.** The API reports whether a secret
is *set*, never what it is. There is no read-back, no "reveal", and no
round-tripping a value through a form. If a tenant loses their key secret,
Razorpay reissues it; we are not a second copy of it for an attacker to fetch.

**Whose account is in use is always stated.** A tenant with no row falls back to
the deployment's environment keys, which on a platform means their guests' money
lands in the operator's account. That fallback exists so single-hotel
installations keep working, and the only thing that makes it safe is saying so --
so every response carries ``source``, and ``using_deployment_account`` spells out
the consequence.
"""

from __future__ import annotations

import uuid

from chirala_common.audit import record_audit
from chirala_common.authz import Caller
from chirala_common.routing import TransactionalRoute
from chirala_common.secretbox import SecretsNotConfigured
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.orm import Session

from .credentials import for_organization, keys_configured, new_webhook_ref, protect
from .database import get_session
from .routes import require_org_permission
from .settings import settings

credentials_router = APIRouter(prefix="/payments", tags=["payments"],
                               route_class=TransactionalRoute)

PROVIDERS = ("mock", "razorpay")


class CredentialsOut(BaseModel):
    """What a tenant may see about their own gateway. No secrets."""

    provider: str
    #: 'tenant' | 'deployment' | 'none' -- which keys are actually in effect.
    source: str
    enabled: bool
    #: The publishable half. Safe to show: it ships to the guest's browser.
    key_id: str = ""
    key_secret_set: bool = False
    webhook_secret_set: bool = False
    #: The URL to paste into the tenant's own Razorpay dashboard. Absent until
    #: credentials are first saved, because the ref is created with the row.
    webhook_url: str | None = None
    #: False when this service has no encryption key, in which case saving a
    #: secret is refused rather than stored in the clear.
    can_store_secrets: bool = True
    #: True when this tenant is transacting through the deployment's account
    #: rather than their own -- money is not reaching them.
    using_deployment_account: bool = False


class CredentialsIn(BaseModel):
    provider: str = Field(pattern="^(mock|razorpay)$")
    key_id: str | None = Field(None, max_length=160)
    #: Omit to keep whatever is stored. A tenant toggling ``enabled`` should not
    #: have to retype a secret they cannot read back.
    key_secret: str | None = Field(None, max_length=400)
    webhook_secret: str | None = Field(None, max_length=400)
    enabled: bool = False


def _row(db: Session, organization_id: uuid.UUID):
    return db.execute(
        text("SELECT provider, webhook_ref, key_id, key_secret_sealed, "
             "webhook_secret_sealed, enabled "
             "FROM finance.payment_credentials WHERE organization_id = :o"),
        {"o": organization_id},
    ).mappings().first()


def _webhook_url(ref: str | None) -> str | None:
    if not ref:
        return None
    base = (settings.app_base_url or "").rstrip("/")
    return f"{base}/api/finance/webhooks/razorpay/{ref}" if base else None


def _present(db: Session, organization_id: uuid.UUID) -> CredentialsOut:
    row = _row(db, organization_id)
    effective = for_organization(db, organization_id)
    return CredentialsOut(
        provider=(row["provider"] if row else "mock"),
        source=effective.source,
        enabled=bool(row["enabled"]) if row else False,
        key_id=(row["key_id"] or "") if row else "",
        key_secret_set=bool(row and row["key_secret_sealed"]),
        webhook_secret_set=bool(row and row["webhook_secret_sealed"]),
        webhook_url=_webhook_url(row["webhook_ref"] if row else None),
        can_store_secrets=keys_configured(),
        using_deployment_account=(effective.source == "deployment"
                                  and effective.provider == "razorpay"),
    )


def _caller_org(caller: Caller) -> uuid.UUID:
    if caller.organization_id is None:
        raise HTTPException(status_code=403, detail="No active membership")
    return caller.organization_id


@credentials_router.get("/credentials", response_model=CredentialsOut)
def get_credentials(
    caller: Caller = Depends(require_org_permission("payments", "view")),
    db: Session = Depends(get_session),
):
    """This tenant's gateway configuration, without the secrets."""
    return _present(db, _caller_org(caller))


@credentials_router.put("/credentials", response_model=CredentialsOut)
def put_credentials(
    body: CredentialsIn,
    caller: Caller = Depends(require_org_permission("payments", "configure")),
    db: Session = Depends(get_session),
):
    """Set this tenant's gateway.

    Gated on ``payments:configure``, which only Administrator and Property IT
    Administrator hold -- these keys are the tenant's bank account by proxy, and
    a permission that a duty manager carries would be the wrong one.

    A secret left out is kept. Enabling Razorpay without a complete set of
    credentials is refused by the table's own CHECK, and refused here first with
    something a person can act on: a tenant half-way through entering keys must
    not be live, because the failure mode is a guest paying into nothing.
    """
    organization_id = _caller_org(caller)
    existing = _row(db, organization_id)

    key_id = body.key_id if body.key_id is not None else (
        existing["key_id"] if existing else None)
    secret_sealed = existing["key_secret_sealed"] if existing else None
    hook_sealed = existing["webhook_secret_sealed"] if existing else None

    try:
        if body.key_secret:
            secret_sealed = protect(body.key_secret.strip())
        if body.webhook_secret:
            hook_sealed = protect(body.webhook_secret.strip())
    except SecretsNotConfigured as exc:
        # 503, not 400: the tenant did nothing wrong, the deployment is missing
        # its encryption key. Storing the secret unsealed instead is not on the
        # table.
        raise HTTPException(status_code=503, detail=str(exc)) from None

    if body.enabled and body.provider == "razorpay" and not (
            key_id and secret_sealed and hook_sealed):
        missing = [name for name, value in (
            ("key id", key_id), ("key secret", secret_sealed),
            ("webhook secret", hook_sealed)) if not value]
        raise HTTPException(
            status_code=422,
            detail=("Razorpay cannot be switched on until every credential is "
                    f"saved. Still missing: {', '.join(missing)}."))

    ref = (existing["webhook_ref"] if existing and existing["webhook_ref"]
           else new_webhook_ref())

    db.execute(
        text(
            """
            INSERT INTO finance.payment_credentials
                (organization_id, provider, webhook_ref, key_id,
                 key_secret_sealed, webhook_secret_sealed, enabled, updated_by)
            VALUES (:o, :prov, :ref, :kid, :sec, :hook, :on, :by)
            ON CONFLICT (organization_id) DO UPDATE
               SET provider = EXCLUDED.provider,
                   key_id = EXCLUDED.key_id,
                   key_secret_sealed = EXCLUDED.key_secret_sealed,
                   webhook_secret_sealed = EXCLUDED.webhook_secret_sealed,
                   enabled = EXCLUDED.enabled,
                   updated_by = EXCLUDED.updated_by,
                   updated_at = now()
            """
        ),
        {"o": organization_id, "prov": body.provider, "ref": ref,
         "kid": key_id, "sec": secret_sealed, "hook": hook_sealed,
         "on": body.enabled, "by": caller.user_id},
    )

    # Audited without a single secret in it -- only which knobs moved. An audit
    # trail that recorded the values would be the plaintext copy this module
    # exists to avoid.
    record_audit(
        db,
        action="payment_credentials_set",
        entity_type="organization",
        entity_id=str(organization_id),
        organization_id=organization_id,
        actor_subject=caller.subject,
        before=({"provider": existing["provider"],
                 "enabled": bool(existing["enabled"]),
                 "key_id": existing["key_id"]} if existing else None),
        after={"provider": body.provider, "enabled": body.enabled,
               "key_id": key_id,
               "key_secret_changed": bool(body.key_secret),
               "webhook_secret_changed": bool(body.webhook_secret)},
        reason=("Gateway switched on." if body.enabled
                else "Gateway switched off."),
    )
    return _present(db, organization_id)


@credentials_router.post("/credentials/rotate-callback",
                         response_model=CredentialsOut)
def rotate_callback(
    caller: Caller = Depends(require_org_permission("payments", "configure")),
    db: Session = Depends(get_session),
):
    """Issue a new callback URL for this tenant.

    For when the old one has been somewhere it should not -- a support ticket, a
    screenshot, a shared document. **Payments break until the new URL is saved in
    the Razorpay dashboard**, because callbacks to the old path stop being
    recognised the moment this returns. Deliberate: a leaked URL that keeps
    working is not rotated.
    """
    organization_id = _caller_org(caller)
    if _row(db, organization_id) is None:
        raise HTTPException(
            status_code=404,
            detail="No gateway is configured for this tenant yet.")
    db.execute(
        text("UPDATE finance.payment_credentials SET webhook_ref = :r, "
             "updated_at = now(), updated_by = :by WHERE organization_id = :o"),
        {"r": new_webhook_ref(), "o": organization_id, "by": caller.user_id},
    )
    record_audit(
        db,
        action="payment_callback_rotated",
        entity_type="organization",
        entity_id=str(organization_id),
        organization_id=organization_id,
        actor_subject=caller.subject,
        reason="Callback URL rotated; the previous one no longer works.",
    )
    return _present(db, organization_id)
