"""Commercial accounts — companies, travel agents, OTAs.

The customer that is not a person. A corporate client and a travel agent are
the same shape — a non-person with an address, a tax registration and terms —
so they share a table and differ by ``kind``.

**Outstanding is computed, never stored.** What an account owes is the sum of
its folios' balances, taken from ``finance.folio_entries`` the same way every
other screen takes it. A stored balance would be a second copy of the truth
that drifts the first time something posts outside this module.

**The credit limit warns; it does not block.** The agreement is recorded so a
clerk can be told "this would take BlueWave past its limit", not so the system
can refuse a booking on a rule nobody in the building agreed to. Refusing is a
policy decision and belongs to a property, not to a schema.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from chirala_common.gstin import (
    normalise as normalise_gstin, problem as gstin_problem,
)
from chirala_common.audit import record_audit
from chirala_common.india import normalise_state, state_code_problem
from chirala_common.postal import problem as postal_problem
from chirala_common.authz import Caller, build_authz, assert_org_matches_caller, caller_org
from chirala_common.routing import TransactionalRoute
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .database import get_session
from .settings import settings

_get_caller, _require_permission, require_org_permission = build_authz(
    get_session, lambda: settings.environment,
    lambda: settings.service_token,
)

account_router = APIRouter(tags=["commercial-accounts"],
                           route_class=TransactionalRoute)

KINDS = ("company", "travel_agent", "ota", "government", "other")
KIND_LABELS = {
    "company": "Company", "travel_agent": "Travel Agent", "ota": "OTA",
    "government": "Government", "other": "Other",
}


class AccountIn(BaseModel):
    code: str = Field(min_length=1, max_length=30)
    name: str = Field(min_length=1, max_length=200)
    legal_name: str | None = None
    kind: str = "company"
    gstin: str | None = None
    address_line: str | None = None
    city: str | None = None
    state: str | None = None
    state_code: str | None = None
    postal_code: str | None = None
    country: str = "India"
    contact_name: str | None = None
    contact_phone: str | None = None
    contact_email: str | None = None
    credit_limit: Decimal | None = None
    credit_days: int | None = None
    commission_percent: Decimal | None = None
    payment_terms: str | None = None
    status: str = "active"
    notes: str | None = None


class Account(BaseModel):
    id: uuid.UUID
    code: str
    name: str
    legal_name: str | None
    kind: str
    kind_label: str
    gstin: str | None
    address_line: str | None
    city: str | None
    state: str | None
    state_code: str | None
    postal_code: str | None
    country: str | None
    contact_name: str | None
    contact_phone: str | None
    contact_email: str | None
    credit_limit: Decimal | None
    credit_days: int | None
    commission_percent: Decimal | None
    payment_terms: str | None
    status: str
    notes: str | None
    created_at: datetime

    # Derived, never stored.
    bookings: int = 0
    open_folios: int = 0
    outstanding: Decimal = Decimal("0")
    # True when outstanding already exceeds the agreed limit.
    over_limit: bool = False


class AccountList(BaseModel):
    rows: list[Account]
    total: int
    kinds: list[dict]
    can_create: bool


# Outstanding is the folio balance, computed the same way the folio, invoice
# and reservation screens compute it: charges net of adjustments, less what
# was paid net of refunds.
_STATS_SQL = """
    WITH f AS (
        SELECT fo.commercial_account_id AS acct, fo.id, fo.status,
               COALESCE(sum(CASE WHEN e.entry_type = 'debit' THEN e.amount
                                 ELSE -e.amount END), 0) AS balance
        FROM finance.folios fo
        LEFT JOIN finance.folio_entries e ON e.folio_id = fo.id
        WHERE fo.commercial_account_id IS NOT NULL
        GROUP BY fo.commercial_account_id, fo.id, fo.status
    )
    SELECT acct,
           count(*) FILTER (WHERE status = 'open') AS open_folios,
           COALESCE(sum(balance), 0) AS outstanding
    FROM f GROUP BY acct
"""

_BOOKINGS_SQL = """
    SELECT commercial_account_id AS acct, count(*) AS n
    FROM booking.reservations
    WHERE commercial_account_id IS NOT NULL
    GROUP BY commercial_account_id
"""


def _decorate(db: Session, rows: list[dict]) -> list[Account]:
    stats = {r[0]: (int(r[1]), r[2]) for r in db.execute(text(_STATS_SQL))}
    bookings = {r[0]: int(r[1]) for r in db.execute(text(_BOOKINGS_SQL))}
    out = []
    for r in rows:
        open_folios, outstanding = stats.get(r["id"], (0, Decimal("0")))
        limit = r["credit_limit"]
        out.append(Account(
            **{k: r[k] for k in Account.model_fields
               if k in r and k not in
               ("kind_label", "bookings", "open_folios", "outstanding",
                "over_limit")},
            kind_label=KIND_LABELS.get(r["kind"], r["kind"]),
            bookings=bookings.get(r["id"], 0),
            open_folios=open_folios, outstanding=outstanding,
            over_limit=bool(limit is not None and outstanding > limit),
        ))
    return out


@account_router.get("/commercial-accounts", response_model=AccountList)
def list_accounts(
    organization_id: uuid.UUID,
    q: str | None = Query(None),
    kind: str | None = Query(None),
    status: str | None = Query(None),
    caller: Caller = Depends(require_org_permission("guests", "view")),
    db: Session = Depends(get_session),
):
    """Companies and agents, with what each currently owes."""
    assert_org_matches_caller(caller, organization_id)
    from chirala_common.authz import _GRANT_SQL

    rows = db.execute(
        text(
            """
            SELECT * FROM engagement.commercial_accounts
            WHERE organization_id = :org
              AND (CAST(:q AS text) IS NULL
                   OR name ILIKE '%' || CAST(:q AS text) || '%'
                   OR code ILIKE '%' || CAST(:q AS text) || '%'
                   OR COALESCE(gstin, '') ILIKE '%' || CAST(:q AS text) || '%')
              AND (CAST(:kind AS text) IS NULL OR kind = CAST(:kind AS text))
              AND (CAST(:st AS text) IS NULL OR status = CAST(:st AS text))
            ORDER BY name
            """
        ),
        {"org": organization_id, "q": q or None, "kind": kind or None,
         "st": status or None},
    ).mappings().all()

    counts = [
        {"value": r[0], "label": KIND_LABELS.get(r[0], r[0]), "count": r[1]}
        for r in db.execute(
            text("SELECT kind, count(*) FROM engagement.commercial_accounts "
                 "WHERE organization_id = :org GROUP BY 1 ORDER BY 1"),
            {"org": organization_id},
        )
    ]
    may = db.execute(
        text(_GRANT_SQL),
        {"uid": caller.user_id, "res": "guests", "act": "create", "prop": None},
    ).first() is not None

    decorated = _decorate(db, [dict(r) for r in rows])
    return AccountList(rows=decorated, total=len(decorated), kinds=counts,
                       can_create=may)


def _check_gstin(body: "AccountIn") -> None:
    """A customer's GSTIN is checked the same way the property's own is.

    It is not decoration on the account: an invoice raised against a company
    carries this number, and whether its state matches the place of supply is
    what decides CGST+SGST against IGST. The shared rule lives in
    ``chirala_common.gstin`` so the two sides cannot drift apart.
    """
    body.gstin = normalise_gstin(body.gstin)
    body.state = normalise_state(body.state)
    wrong = state_code_problem(body.state, body.state_code)
    if wrong:
        raise HTTPException(status_code=422, detail=wrong)
    wrong = gstin_problem(body.gstin, state_code=body.state_code)
    if wrong:
        raise HTTPException(status_code=422, detail=wrong)
    wrong = postal_problem(body.postal_code, body.country)
    if wrong:
        raise HTTPException(status_code=422, detail=wrong)


@account_router.post("/commercial-accounts", response_model=Account,
                     status_code=201)
def create_account(
    body: AccountIn,
    caller: Caller = Depends(require_org_permission("guests", "create")),
    db: Session = Depends(get_session),
):
    """Open an account for a company or agent."""
    _check_gstin(body)
    if body.kind not in KINDS:
        raise HTTPException(
            status_code=422,
            detail=f"Unknown account type. Use one of: {', '.join(KINDS)}.")

    aid = uuid.uuid4()
    fields = body.model_dump(exclude={"organization_id"})
    cols = ", ".join(fields)
    binds = ", ".join(f":{k}" for k in fields)
    try:
        db.execute(
            text(f"INSERT INTO engagement.commercial_accounts "
                 f"(id, organization_id, {cols}, created_by, updated_by) "
                 f"VALUES (:id, :org, {binds}, :who, :who)"),
            {"id": aid, "org": caller_org(caller), "who": caller.user_id,
             **fields},
        )
        db.flush()
    except IntegrityError as exc:
        # uq_account_code — the desk types the code, so collisions are common
        # and deserve a sentence rather than a constraint name.
        raise HTTPException(
            status_code=409,
            detail=f"An account with the code {body.code} already exists.",
        ) from exc

    record_audit(
        db, action="commercial_account.created", entity_type="commercial_account",
        entity_id=str(aid), organization_id=caller_org(caller),
        property_id=None, actor_subject=caller.subject,
        after={"code": body.code, "name": body.name, "kind": body.kind},
    )
    row = db.execute(
        text("SELECT * FROM engagement.commercial_accounts WHERE id = :i"),
        {"i": aid},
    ).mappings().first()
    return _decorate(db, [dict(row)])[0]


@account_router.get("/commercial-accounts/{account_id}", response_model=Account)
def get_account(
    account_id: uuid.UUID,
    organization_id: uuid.UUID,
    caller: Caller = Depends(require_org_permission("guests", "view")),
    db: Session = Depends(get_session),
):
    assert_org_matches_caller(caller, organization_id)
    row = db.execute(
        text("SELECT * FROM engagement.commercial_accounts "
             "WHERE id = :i AND organization_id = :org"),
        {"i": account_id, "org": organization_id},
    ).mappings().first()
    if row is None:
        raise HTTPException(status_code=404, detail="Account not found")
    return _decorate(db, [dict(row)])[0]


@account_router.patch("/commercial-accounts/{account_id}", response_model=Account)
def update_account(
    account_id: uuid.UUID,
    body: AccountIn,
    caller: Caller = Depends(require_org_permission("guests", "edit")),
    db: Session = Depends(get_session),
):
    """Correct an account. The code can change; collisions are still refused."""
    _check_gstin(body)
    before = db.execute(
        text("SELECT * FROM engagement.commercial_accounts "
             "WHERE id = :i AND organization_id = :org"),
        {"i": account_id, "org": caller_org(caller)},
    ).mappings().first()
    if before is None:
        raise HTTPException(status_code=404, detail="Account not found")
    if body.kind not in KINDS:
        raise HTTPException(
            status_code=422,
            detail=f"Unknown account type. Use one of: {', '.join(KINDS)}.")

    fields = body.model_dump(exclude={"organization_id"})
    sets = ", ".join(f"{k} = :{k}" for k in fields)
    try:
        db.execute(
            text(f"UPDATE engagement.commercial_accounts SET {sets}, "
                 f"updated_by = :who, updated_at = now(), "
                 f"version = version + 1 WHERE id = :id"),
            {"id": account_id, "who": caller.user_id, **fields},
        )
        db.flush()
    except IntegrityError as exc:
        raise HTTPException(
            status_code=409,
            detail=f"An account with the code {body.code} already exists.",
        ) from exc

    changed = {k: v for k, v in fields.items()
               if str(v or "") != str(before[k] or "")}
    record_audit(
        db, action="commercial_account.updated",
        entity_type="commercial_account", entity_id=str(account_id),
        organization_id=caller_org(caller), property_id=None,
        actor_subject=caller.subject,
        before={k: str(before[k]) for k in changed if before[k]},
        after={k: str(v) for k, v in changed.items() if v},
    )
    row = db.execute(
        text("SELECT * FROM engagement.commercial_accounts WHERE id = :i"),
        {"i": account_id},
    ).mappings().first()
    return _decorate(db, [dict(row)])[0]
