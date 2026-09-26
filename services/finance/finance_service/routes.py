"""Finance API routes."""

from __future__ import annotations

import uuid
from datetime import date, timedelta
from decimal import Decimal
from typing import NoReturn

from chirala_common.db import bind_tenant_context
from chirala_common import charge_types, no_show
from chirala_common.audit import record_audit
from chirala_common.authz import Caller, assert_property_in_org, build_authz, assert_entity_in_org, assert_org_matches_caller, caller_org, require_property_permission
from chirala_common.routing import TransactionalRoute
from fastapi import (
    APIRouter, BackgroundTasks, Depends, HTTPException, Query, status,
)
from sqlalchemy import text
from sqlalchemy.orm import Session

from . import schemas
from .database import SessionFactory, get_session
from .scheduler import AUDIT_HOUR, jitter_minute
from .settings import settings
from .ledger import (
    Allocation,
    LedgerError,
    folio_balance,
    post_charge,
    post_payment,
    post_refund,
)
from .mail import send_night_audit_report
from .night_audit import (
    NightAuditError, NightlyCharge, derive_nightly_charges, find_no_shows,
    find_open_shifts, find_overstays, future_day_reason, local_today,
    open_shift_block, run_night_audit,
)
from .tax_engine import tax_breakdown

_get_caller, require_permission, require_org_permission = build_authz(
    get_session, lambda: settings.environment,
    lambda: settings.service_token,
)

router = APIRouter(route_class=TransactionalRoute)


def _raise_ledger(exc: Exception, conflict: bool) -> NoReturn:
    raise HTTPException(status_code=409 if conflict else 422, detail=str(exc)) from exc


@router.post(
    "/folios",
    response_model=schemas.FolioOut,
    status_code=status.HTTP_201_CREATED,
    tags=["folios"],
)
def create_folio(body: schemas.FolioCreate, caller: Caller = Depends(require_org_permission("payments", "create")), db: Session = Depends(get_session)):
    # The organisation is the caller's own; the body no longer
    # carries one to disagree with.
    require_property_permission(db, caller, body.property_id,
                                "payments", "create")
    folio_id = uuid.uuid4()
    db.execute(
        text(
            """
            INSERT INTO finance.folios
                (id, organization_id, property_id, reservation_id, type, currency,
                 status)
            VALUES (:id, :org, :prop, :res, :type, :cur, 'open')
            """
        ),
        {
            "id": folio_id,
            "org": caller_org(caller),
            "prop": body.property_id,
            "res": body.reservation_id,
            "type": body.type,
            "cur": body.currency,
        },
    )
    return schemas.FolioOut(
        id=folio_id, type=body.type, currency=body.currency, status="open"
    )


@router.get(
    "/folios",
    response_model=list[schemas.FolioOut],
    tags=["folios"],
)
def list_folios(
    reservation_id: uuid.UUID | None = None, caller: Caller = Depends(require_org_permission("payments", "view")), db: Session = Depends(get_session)
):
    """List folios, optionally filtered by reservation_id (Option A link, §6).

    Both branches are scoped to the caller's organisation. Neither was: the
    filtered one trusted a reservation id, and the unfiltered one listed the
    first 200 folios *in the deployment* -- every tenant's, in creation order.
    Scoping in the same query as the filter is what makes another tenant's
    folio indistinguishable from one that does not exist.
    """
    if caller.organization_id is None:
        return []
    if reservation_id is not None:
        rows = db.execute(
            text(
                """
                SELECT id, type, currency, status FROM finance.folios
                WHERE reservation_id = :rid AND organization_id = :org
                ORDER BY created_at
                """
            ),
            {"rid": reservation_id, "org": caller.organization_id},
        ).all()
    else:
        rows = db.execute(
            text(
                "SELECT id, type, currency, status FROM finance.folios "
                "WHERE organization_id = :org ORDER BY created_at LIMIT 200"
            ),
            {"org": caller.organization_id},
        ).all()
    return [
        schemas.FolioOut(id=r.id, type=r.type, currency=r.currency, status=r.status)
        for r in rows
    ]


@router.get(
    "/folios/{folio_id}/balance",
    response_model=schemas.BalanceOut,
    tags=["folios"],
)
def get_balance(folio_id: uuid.UUID, caller: Caller = Depends(require_org_permission("payments", "view")), db: Session = Depends(get_session)):
    assert_entity_in_org(db, caller, table="finance.folios", entity_id=folio_id)
    return schemas.BalanceOut(folio_id=folio_id, balance=folio_balance(db, folio_id))


# Map source_type -> a human description + department for the folio table.
_SOURCE_META = {
    "room_stay": ("Room Charge", "Rooms"),
    "room_night": ("Room Charge (Night)", "Rooms"),
    "room": ("Room Charge", "Rooms"),
    "payment": ("Payment Received", "Payments"),
    "refund": ("Refund", "Payments"),
    "minibar": ("Minibar", "Rooms"),
    "restaurant": ("Restaurant", "Restaurant"),
    "spa": ("Spa Treatment", "Spa"),
    "laundry": ("Laundry", "Laundry"),
    "transport": ("Transport", "Transportation"),
}

# post_charge posts exclusive tax as "<source>_tax". Give those a readable name
# rather than letting the raw source key reach the bill.
_SOURCE_META.update({
    f"{src}_tax": (f"Taxes & Charges — {label}", dept)
    for src, (label, dept) in list(_SOURCE_META.items())
    if dept != "Payments"
})


@router.get(
    "/folios/{folio_id}/entries",
    response_model=list[schemas.FolioEntryOut],
    tags=["folios"],
)
def folio_entries(folio_id: uuid.UUID, caller: Caller = Depends(require_org_permission("payments", "view")), db: Session = Depends(get_session)):
    """Charge/payment lines for a folio (US-007 Guest Folio)."""
    assert_entity_in_org(db, caller, table="finance.folios", entity_id=folio_id)
    rows = db.execute(
        text(
            """
            SELECT e.id, e.business_date, e.entry_type, e.amount, e.currency,
                   e.source_type, e.source_id,
                   -- What the guest actually ordered, where something
                   -- recorded it. Without this the bill says "Restaurant" for
                   -- a line the guest remembers as two dosas, and nobody at
                   -- the desk can answer "what is this 240 for?". Joined here
                   -- rather than on the checkout screen so the folio PDF and
                   -- the invoice say the same thing.
                   l.item_name, l.quantity
            FROM finance.folio_entries e
            LEFT JOIN finance.service_order_lines l ON l.folio_entry_id = e.id
            WHERE e.folio_id = :fid
            ORDER BY e.business_date, e.posted_at
            """
        ),
        {"fid": folio_id},
    ).mappings().all()
    out = []
    for r in rows:
        meta = _SOURCE_META.get(r["source_type"], (r["source_type"].title(), "General"))
        desc, dept = meta
        if r["item_name"]:
            qty = Decimal(r["quantity"]).normalize()
            desc = f"{r['item_name']}" + (f" x{qty}" if qty != 1 else "")
        out.append(
            schemas.FolioEntryOut(
                id=r["id"], business_date=r["business_date"], entry_type=r["entry_type"],
                amount=r["amount"], currency=r["currency"], source_type=r["source_type"],
                description=desc, department=dept,
            )
        )
    return out


@router.get(
    "/folios/{folio_id}/summary",
    response_model=schemas.FolioSummaryOut,
    tags=["folios"],
)
def folio_summary(folio_id: uuid.UUID, caller: Caller = Depends(require_org_permission("payments", "view")), db: Session = Depends(get_session)):
    """Subtotal, tax, total, advance and balance for one folio.

    Tax comes from what was actually posted (``folio_entry_taxes``), not from a
    assumed percentage: the rules on screen 118 decide it, and each line records
    the rate it was charged at.
    """
    assert_entity_in_org(db, caller, table="finance.folios", entity_id=folio_id)
    row = db.execute(
        text(
            """
            SELECT
              -- What the guest was charged. A refund is a debit too, and
              -- counting it here made handing money back look like billing
              -- for something.
              coalesce(sum(amount) FILTER (
                  WHERE entry_type = 'debit'
                    AND coalesce(source_type, '') NOT IN ('refund', 'deposit_refund')
              ), 0) AS charges,
              -- What the resort is holding: taken, less given back.
              coalesce(sum(amount) FILTER (WHERE entry_type = 'credit'), 0)
              - coalesce(sum(amount) FILTER (
                  WHERE entry_type = 'debit'
                    AND coalesce(source_type, '') IN ('refund', 'deposit_refund')
              ), 0) AS paid,
              coalesce(max(currency), 'INR') AS currency
            FROM finance.folio_entries WHERE folio_id = :fid
            """
        ),
        {"fid": folio_id},
    ).mappings().first()
    charges = row["charges"]
    paid = row["paid"]

    lines = tax_breakdown(db, folio_id)
    taxes = sum((line["tax_amount"] for line in lines), Decimal("0"))
    # An exclusive tax was posted as its own debit, so it is already inside
    # `debits`; an inclusive one never was. Either way the subtotal is what the
    # guest is charged less the tax within it.
    subtotal = charges - taxes
    return schemas.FolioSummaryOut(
        folio_id=folio_id,
        subtotal=subtotal,
        taxes=taxes,
        grand_total=charges,
        advance_paid=paid,
        # Unchanged in value -- charges less what is still held is the same
        # number as debits less credits -- but now it is the difference
        # between two figures that mean what their labels say.
        balance_due=charges - paid,
        currency=row["currency"],
        tax_lines=[schemas.TaxLineOut(**line) for line in lines],
    )


def _folios_on_property(db: Session, property_id, folio_ids) -> None:
    """Refuse a posting to a folio that is not on the property named.

    The permission check is made against the property in the body, and the
    money lands on the folio in the body. Nothing tied the two together, so a
    caller permitted at one hotel could name it and post to a sister hotel's
    folio. 404, as for any other row that is not the caller's to address.
    """
    ids = list({f for f in folio_ids})
    found = db.execute(
        text("SELECT count(*) FROM finance.folios "
             "WHERE id = ANY(:ids) AND property_id = :p"),
        {"ids": ids, "p": property_id},
    ).scalar()
    if found != len(ids):
        raise HTTPException(status_code=404, detail="Folio not found.")


def _trading_day(db: Session, property_id) -> date:
    """The day this property is trading, for a caller that named none.

    The OLDEST unclosed day: the night audit closes them oldest first, so a
    property cannot be selling the 18th while the 17th is still open. Falls
    back to the property's own local date, never the server's -- a resort in
    Kolkata and one in Lisbon do not change date together.
    """
    day = db.execute(
        text("SELECT business_date FROM finance.business_days "
             "WHERE property_id = :p AND status = 'open' "
             "ORDER BY business_date ASC LIMIT 1"),
        {"p": property_id},
    ).scalar()
    return day if day is not None else local_today(db, property_id)


@router.post(
    "/charges",
    response_model=schemas.EntryOut,
    status_code=status.HTTP_201_CREATED,
    tags=["charges"],
)
def create_charge(body: schemas.ChargeCreate, caller: Caller = Depends(require_org_permission("payments", "create")), db: Session = Depends(get_session)):
    # The organisation is the caller's own; the body no longer
    # carries one to disagree with.
    require_property_permission(db, caller, body.property_id,
                                "payments", "create")
    _folios_on_property(db, body.property_id, [body.folio_id])
    try:
        res = post_charge(
            db,
            organization_id=caller_org(caller),
            property_id=body.property_id,
            folio_id=body.folio_id,
            amount=body.amount,
            business_date=body.business_date or _trading_day(db, body.property_id),
            source_type=body.source_type,
            source_line_key=body.source_line_key,
            charge_code_id=body.charge_code_id,
            source_id=body.source_id,
            currency=body.currency,
            note=body.note,
            quantity=body.quantity,
            unit_amount=body.unit_amount,
            discount_amount=body.discount_amount,
            posted_by=caller.subject,
        )
    except LedgerError as exc:
        _raise_ledger(exc, exc.conflict)
    return schemas.EntryOut(entry_id=res.entry_id, created=res.created)


@router.get("/charge-types", tags=["charges"])
def charge_types_list(
    caller: Caller = Depends(require_org_permission("payments", "view")),
):
    """What a desk may bill a guest for, and what each is for tax.

    Served rather than written into the client, for the reason every other
    list on this system is: the dropdown, the folio's description of a line
    and the tax engine's category all read one definition, so a charge type
    cannot exist in one and be missing from another. It was possible to bill
    "breakfast", see it described, and have it silently post untaxed.
    """
    return [
        {"value": t.value, "label": t.label, "tax_category": t.category}
        for t in charge_types.BILLABLE
    ]


@router.post(
    "/payments",
    response_model=schemas.PaymentOut,
    status_code=status.HTTP_201_CREATED,
    tags=["payments"],
)
def create_payment(body: schemas.PaymentCreate, caller: Caller = Depends(require_org_permission("payments", "create")), db: Session = Depends(get_session)):
    # The organisation is the caller's own; the body no longer
    # carries one to disagree with.
    require_property_permission(db, caller, body.property_id,
                                "payments", "create")
    _folios_on_property(db, body.property_id,
                        [a.folio_id for a in body.allocations])
    try:
        res = post_payment(
            db,
            organization_id=caller_org(caller),
            property_id=body.property_id,
            method=body.method,
            business_date=body.business_date or _trading_day(db, body.property_id),
            # Cash taken at a folio goes into the same drawer as cash taken at
            # the Cashiering Centre. Only the Centre used to say so, so the
            # till held money its own shift did not expect.
            cashier_shift_id=_open_shift_of(db, caller, body.property_id),
            allocations=[
                Allocation(folio_id=a.folio_id, amount=a.amount)
                for a in body.allocations
            ],
            reference=body.reference,
            note=body.note,
            currency=body.currency,
            posted_by=caller.subject,
        )
    except LedgerError as exc:
        _raise_ledger(exc, exc.conflict)
    return schemas.PaymentOut(
        payment_id=res.payment_id, credit_entry_ids=res.credit_entry_ids
    )


def _open_shift_of(db: Session, caller: Caller, property_id) -> uuid.UUID | None:
    """The drawer this user currently has open at this property, if any.

    Resolved from the person taking or handing back the money rather than
    passed in by the caller: it crosses the counter in front of them, and a
    client that can name any shift can charge a shortage to somebody else's
    drawer.

    Used by both sides of the till. It was refunds only for a while, so cash
    taken from a folio never reached a drawer count while the refund of it
    did.
    """
    return db.execute(
        text("SELECT id FROM finance.cashier_shifts "
             "WHERE cashier_id = :u AND property_id = :p AND status = 'open'"),
        {"u": caller.user_id, "p": property_id},
    ).scalar()


@router.post(
    "/refunds",
    response_model=schemas.RefundOut,
    status_code=status.HTTP_201_CREATED,
    tags=["refunds"],
)
def create_refund(body: schemas.RefundCreate, caller: Caller = Depends(require_org_permission("payments", "cancel")), db: Session = Depends(get_session)):
    # The organisation is the caller's own; the body no longer
    # carries one to disagree with.
    require_property_permission(db, caller, body.property_id,
                                "payments", "cancel")
    try:
        res = post_refund(
            db,
            organization_id=caller_org(caller),
            property_id=body.property_id,
            payment_id=body.payment_id,
            amount=body.amount,
            business_date=body.business_date,
            reason=body.reason,
            currency=body.currency,
            cashier_shift_id=_open_shift_of(db, caller, body.property_id),
        )
    except LedgerError as exc:
        _raise_ledger(exc, exc.conflict)
    return schemas.RefundOut(
        refund_id=res.refund_id, debit_entry_ids=res.debit_entry_ids
    )


@router.get("/dashboard/revenue", tags=["dashboard"])
def dashboard_revenue(
    property_id: uuid.UUID,
    business_date: date | None = None,
    caller: Caller = Depends(require_permission("dashboard", "view")), db: Session = Depends(get_session),
):
    """Today's revenue and a 7-day revenue series for a property (§6).

    Revenue = posted DEBIT charge entries (room/services) minus DEBIT reversals
    are excluded; payments (credits) are collections, not revenue. This is a
    subledger measure, not accounting profit (per schema §6).
    """
    assert_property_in_org(db, caller, property_id)
    from datetime import timedelta

    bd = business_date or date.today()
    start = bd - timedelta(days=6)

    today_revenue = db.execute(
        text(
            """
            SELECT coalesce(sum(amount), 0)
            FROM finance.folio_entries
            WHERE property_id = :prop
              AND entry_type = 'debit'
              -- Refunds are debits and are not charges. Both kinds: a deposit
              -- refund counted here would inflate the day's takings by money
              -- that went back to the guest.
              AND COALESCE(source_type, '') <> ALL(:refunds)
              AND business_date = :bd
            """
        ),
        {"prop": property_id, "bd": bd,
         "refunds": list(charge_types.REFUND_SOURCES)},
    ).scalar_one()

    rows = db.execute(
        text(
            """
            SELECT business_date AS d, coalesce(sum(amount), 0) AS amt
            FROM finance.folio_entries
            WHERE property_id = :prop
              AND entry_type = 'debit'
              AND COALESCE(source_type, '') <> ALL(:refunds)
              AND business_date >= :start AND business_date <= :bd
            GROUP BY business_date
            """
        ),
        {"prop": property_id, "start": start, "bd": bd,
         "refunds": list(charge_types.REFUND_SOURCES)},
    ).all()
    by_date = {r.d: float(r.amt) for r in rows}
    series = [
        {
            "date": (start + timedelta(days=i)).isoformat(),
            "amount": by_date.get(start + timedelta(days=i), 0.0),
        }
        for i in range(7)
    ]
    return {"today_revenue": float(today_revenue), "series": series}


@router.post(
    "/night-audit/run",
    response_model=schemas.NightAuditOut,
    tags=["night-audit"],
)
def night_audit(body: schemas.NightAuditRun, background: BackgroundTasks, caller: Caller = Depends(require_org_permission("payments", "configure")), db: Session = Depends(get_session)):
    """Close a business day.

    Charges are derived from who is in the house. A caller *may* pass its own
    list -- tests and manual repairs do -- but the default is to let the audit
    work it out, because an audit handed an empty list closes the day with
    every guest un-charged and reports success.
    """
    require_property_permission(db, caller, body.property_id,
                                "payments", "configure")
    try:
        res = run_night_audit(
            db,
            organization_id=caller_org(caller),
            property_id=body.property_id,
            business_date=body.business_date,
            nightly_charges=(
                [
                    NightlyCharge(
                        folio_id=c.folio_id,
                        amount=c.amount,
                        charge_code_id=c.charge_code_id,
                    )
                    for c in body.nightly_charges
                ]
                if body.nightly_charges
                else None
            ),
            allow_open_shifts=body.allow_open_shifts,
            run_by=caller.user_id,
            actor_subject=caller.subject,
        )
    except NightAuditError as exc:
        _raise_ledger(exc, exc.conflict)

    # Queued, not sent inline: this route commits after the handler returns, so
    # sending here would announce a close that has not happened yet -- and a
    # slow mail server would hold the auditor's screen waiting on it.
    if "already_closed" not in res.steps:
        name = db.execute(
            text("SELECT name FROM iam.properties WHERE id = :p"),
            {"p": body.property_id},
        ).scalar() or "Your property"
        background.add_task(
            _email_audit_summary, caller_org(caller), body.property_id, name, res)
    return _audit_out(res)


def _email_audit_summary(organization_id, property_id, property_name, res) -> None:
    """Runs after the response, on its own session, once the day is committed."""
    with SessionFactory() as session:
        # A fresh session has no caller, so it is told whose property this is.
        bind_tenant_context(session, organization_id=organization_id,
                            property_id=property_id, is_service=True)
        send_night_audit_report(
            session, property_id=property_id, property_name=property_name,
            business_date=res.business_date,
            rooms_charged=res.rooms_charged,
            amount_charged=res.amount_charged,
            no_shows=len(res.no_shows), overstays=len(res.overstays),
            open_shifts=res.open_shifts,
            next_business_date=res.next_business_date,
            warnings=res.warnings,
        )


def _pending_out(p) -> schemas.NightAuditPending:
    return schemas.NightAuditPending(
        unit_id=p.unit_id, reservation_number=p.reservation_number,
        room=p.room, guest=p.guest,
        arrival_date=p.arrival_date, departure_date=p.departure_date,
    )


def _audit_out(res) -> schemas.NightAuditOut:
    return schemas.NightAuditOut(
        run_id=res.run_id,
        business_date=res.business_date,
        charges_posted=res.charges_posted,
        next_business_date=res.next_business_date,
        steps=res.steps,
        rooms_charged=res.rooms_charged,
        amount_charged=res.amount_charged,
        no_shows=[_pending_out(p) for p in res.no_shows],
        overstays=[_pending_out(p) for p in res.overstays],
        horizon_days_added=res.horizon_days_added,
        open_shifts=res.open_shifts,
        warnings=res.warnings,
    )


@router.get(
    "/night-audit/preview",
    response_model=schemas.NightAuditPreview,
    tags=["night-audit"],
)
def night_audit_preview(
    property_id: uuid.UUID,
    business_date: date | None = None,
    caller: Caller = Depends(require_org_permission("payments", "view")),
    db: Session = Depends(get_session),
):
    """What tonight's audit would do, without doing it.

    Closing a day cannot be undone, so the auditor gets to look first. This
    runs the same derivation the real thing does -- it just never posts.
    """
    assert_property_in_org(db, caller, property_id)
    day = business_date or db.execute(
        text(
            """
            SELECT coalesce(
                (SELECT min(business_date) FROM finance.business_days
                  WHERE property_id = :p AND status <> 'closed'),
                CURRENT_DATE)
            """
        ),
        {"p": property_id},
    ).scalar_one()
    org = db.execute(
        text("SELECT organization_id FROM iam.properties WHERE id = :p"),
        {"p": property_id},
    ).scalar_one()

    # Derivation opens a folio for an in-house booking that has none, which is
    # a write. A preview must leave nothing behind, so it runs inside a
    # savepoint that is always rolled back.
    sp = db.begin_nested()
    try:
        charges, warnings = derive_nightly_charges(
            db, organization_id=org, property_id=property_id,
            business_date=day)
        rooms = len(charges)
        amount = sum((c.amount for c in charges), Decimal("0"))
        no_shows = find_no_shows(db, property_id=property_id, business_date=day)
        overstays = find_overstays(
            db, property_id=property_id, business_date=day)
        shifts = find_open_shifts(
            db, property_id=property_id, business_date=day)
    finally:
        sp.rollback()

    closed = db.execute(
        text(
            "SELECT status FROM finance.business_days "
            "WHERE property_id = :p AND business_date = :d"
        ),
        {"p": property_id, "d": day},
    ).scalar()
    # Why the button is off, in the order the reasons actually apply.
    #
    # `can_close` already accounted for a day being closed, but
    # `blocked_reason` did not -- so a day that had already been audited came
    # back as "cannot close" with nothing said, and the screen could only
    # render a disabled button beside an explanation it had not been given.
    # An already-closed day is the more specific answer and is checked first:
    # a future date that has also somehow been closed is closed, and saying so
    # is more use than telling somebody to wait for a day that is done.
    if closed == "closed":
        blocked = (f"Business date {day:%d %b %Y} has already been closed. "
                   f"Reopening a closed day is a platform operation, not a "
                   f"night audit.")
    else:
        blocked = future_day_reason(day, local_today(db, property_id))
    return schemas.NightAuditPreview(
        property_id=property_id,
        business_date=day,
        next_business_date=day + timedelta(days=1),
        already_closed=closed == "closed",
        can_close=blocked is None,
        blocked_reason=blocked,
        requires_override=bool(shifts) and blocked is None
                          and closed != "closed",
        override_reason=open_shift_block(shifts) if shifts else None,
        rooms_to_charge=rooms,
        amount_to_charge=amount,
        charge_lines=[
            schemas.NightAuditChargeLine(
                unit_id=c.unit_id, reservation_number=c.reservation_number,
                room=c.room, guest=c.guest, room_type=c.room_type,
                amount=c.amount,
            )
            for c in charges if c.unit_id is not None
        ],
        no_shows=[_pending_out(p) for p in no_shows],
        overstays=[_pending_out(p) for p in overstays],
        open_shifts=len(shifts),
        open_shift_rows=[
            schemas.NightAuditOpenShift(
                shift_id=sh.shift_id, cashier=sh.cashier,
                opened_at=sh.opened_at, opening_float=sh.opening_float,
            )
            for sh in shifts
        ],
        warnings=warnings,
        auto_audit_enabled=settings.night_audit_enabled,
        audit_hour=_audit_settings(db, property_id).audit_hour or AUDIT_HOUR,
    )


def _audit_settings(db: Session, property_id: uuid.UUID) -> schemas.NightAuditSettings:
    row = db.execute(
        text("SELECT audit_hour, no_show_penalty "
             "  FROM finance.night_audit_settings WHERE property_id = :p"),
        {"p": property_id},
    ).mappings().first()
    return schemas.NightAuditSettings(
        property_id=property_id,
        audit_hour=row["audit_hour"] if row else None,
        # normalise, so a row still holding the old "first_night" reads as the
        # basis it was actually charged at rather than as an unknown.
        no_show_penalty=no_show.normalise(
            row["no_show_penalty"] if row else None),
        no_show_options=[{"code": c, "label": no_show.LABELS[c]}
                         for c in no_show.BASES],
        default_hour=AUDIT_HOUR,
        scheduled_minute=jitter_minute(property_id),
        enabled=settings.night_audit_enabled,
    )


@router.get(
    "/night-audit/settings",
    response_model=schemas.NightAuditSettings,
    tags=["night-audit"],
)
def get_night_audit_settings(
    property_id: uuid.UUID,
    caller: Caller = Depends(require_org_permission("payments", "view")),
    db: Session = Depends(get_session),
):
    """When this property closes its books."""
    assert_property_in_org(db, caller, property_id)
    return _audit_settings(db, property_id)


@router.put(
    "/night-audit/settings",
    response_model=schemas.NightAuditSettings,
    tags=["night-audit"],
)
def put_night_audit_settings(
    property_id: uuid.UUID,
    body: schemas.NightAuditSettingsIn,
    caller: Caller = Depends(require_org_permission("payments", "configure")),
    db: Session = Depends(get_session),
):
    """Choose the hour, or clear it to follow the deployment default.

    Behind ``payments:configure`` -- the same permission that closes a day.
    When the books seal is a financial control, not a property detail, so it
    must not be reachable by whoever can correct the hotel's address.
    """
    assert_property_in_org(db, caller, property_id)
    org = db.execute(
        text("SELECT organization_id FROM iam.properties WHERE id = :p"),
        {"p": property_id},
    ).scalar_one()
    db.execute(
        text(
            """
            INSERT INTO finance.night_audit_settings
                (property_id, organization_id, audit_hour, no_show_penalty)
            VALUES (:p, :org, :h, :ns)
            ON CONFLICT (property_id) DO UPDATE
                SET audit_hour = EXCLUDED.audit_hour,
                    no_show_penalty = EXCLUDED.no_show_penalty,
                    updated_at = now(),
                    version = finance.night_audit_settings.version + 1
            """
        ),
        {"p": property_id, "org": org, "h": body.audit_hour,
         "ns": body.no_show_penalty or no_show.DEFAULT_BASIS},
    )
    record_audit(
        db, action="night_audit.settings_changed", entity_type="property",
        entity_id=str(property_id), organization_id=org,
        property_id=property_id, actor_subject=caller.subject,
        after={"audit_hour": body.audit_hour,
               "no_show_penalty": body.no_show_penalty},
    )
    return _audit_settings(db, property_id)


#: Debits for the day, grouped by what earned them, with exclusive tax folded
#: back onto the charge it belongs to. post_charge posts that tax as a separate
#: "<source>_tax" entry, so without the fold every category would appear twice.
_SALES_SQL = r"""
    SELECT CASE WHEN e.source_type LIKE '%%\_tax' ESCAPE '\'
                THEN left(e.source_type, length(e.source_type) - 4)
                ELSE e.source_type END AS category,
           sum(CASE WHEN e.source_type LIKE '%%\_tax' ESCAPE '\'
                    THEN 0 ELSE e.amount END) AS charges,
           sum(CASE WHEN e.source_type LIKE '%%\_tax' ESCAPE '\'
                    THEN e.amount ELSE 0 END) AS tax
    FROM finance.folio_entries e
    WHERE e.property_id = :p AND e.business_date = :d
      AND e.entry_type = 'debit'
    GROUP BY 1
    ORDER BY 2 DESC, 1
"""

#: Money actually taken on the day, by how it arrived and who took it. Joined
#: through the ledger rather than read off `payments`, because a payment row
#: carries the wall-clock time it was received and the *ledger* carries the
#: business date it belongs to -- and those differ every night after midnight.
_RECEIPTS_SQL = """
    SELECT coalesce(pm.method, 'unknown') AS method,
           coalesce(u.display_name, 'Unknown') AS cashier,
           e.amount,
           pm.reference,
           pm.received_at,
           rm.code AS room
    FROM finance.folio_entries e
    JOIN finance.payments pm ON pm.id::text = e.source_id
    LEFT JOIN iam.users u ON u.id = pm.cashier_id
    LEFT JOIN finance.folios f ON f.id = e.folio_id
    LEFT JOIN booking.reservation_units ru ON ru.reservation_id = f.reservation_id
    LEFT JOIN property.rooms rm ON rm.id = ru.assigned_room_id
    WHERE e.property_id = :p AND e.business_date = :d
      AND e.entry_type = 'credit' AND e.source_type = 'payment'
    ORDER BY pm.received_at
"""

#: Still owed by folios that are not settled. A day's takings mean little
#: without the debt it left behind.
_OUTSTANDING_SQL = """
    SELECT coalesce(sum(CASE WHEN e.entry_type = 'debit' THEN e.amount
                             ELSE -e.amount END), 0)
    FROM finance.folio_entries e
    JOIN finance.folios f ON f.id = e.folio_id
    WHERE f.property_id = :p AND f.status = 'open'
      AND e.business_date <= :d
"""

#: Who left on the day, and what their folio came to.
#:
#: Keyed on the checkout that actually happened rather than the departure the
#: booking predicted: a guest who leaves early departs on the day they walk
#: out, and a report that lists them under their original date describes a
#: night that did not occur.
#:
#: The folio figures are the folio's whole life, not the day's -- a departing
#: guest's balance is what they owe altogether, which is the only version of
#: that number anybody chases.
_DEPARTURES_SQL = """
    SELECT res.number AS reservation_number,
           rm.code AS room,
           g.full_name AS guest,
           u.arrival_date,
           u.departure_date,
           st.actual_checkout_at AS left_at,
           coalesce((SELECT sum(CASE WHEN e.entry_type = 'debit'
                                     THEN e.amount ELSE 0 END)
                       FROM finance.folio_entries e
                       JOIN finance.folios f ON f.id = e.folio_id
                      WHERE f.reservation_id = res.id), 0) AS charged,
           coalesce((SELECT sum(CASE WHEN e.entry_type = 'credit'
                                     THEN e.amount ELSE 0 END)
                       FROM finance.folio_entries e
                       JOIN finance.folios f ON f.id = e.folio_id
                      WHERE f.reservation_id = res.id), 0) AS paid
    FROM booking.stays st
    JOIN booking.reservation_units u ON u.id = st.reservation_unit_id
    JOIN booking.reservations res ON res.id = u.reservation_id
    LEFT JOIN property.rooms rm ON rm.id = u.assigned_room_id
    LEFT JOIN engagement.guests g ON g.id = res.primary_guest_id
    WHERE st.property_id = :p
      AND CAST(st.actual_checkout_at AS date) = :d
    ORDER BY rm.code NULLS LAST
"""

#: Who was in the house on a given night, reconstructed from what happened
#: rather than from where the booking stands today.
#:
#: `status` is a *current* fact -- a guest who has since departed reads
#: 'checked_out' whatever night you ask about. So occupancy for a past date
#: comes from the dates instead: they had arrived by then, and had not yet
#: checked out. Those two are historical and do not move.
_IN_HOUSE_ON = """
    FROM booking.reservation_units u
    LEFT JOIN booking.stays st ON st.reservation_unit_id = u.id
    WHERE u.property_id = :p
      AND u.status IN ('checked_in', 'checked_out')
      AND u.arrival_date <= :d
      AND (st.actual_checkout_at IS NULL
           OR CAST(st.actual_checkout_at AS date) > :d)
"""

_PAX_STATUS_SQL = f"""
    SELECT 'Arrived' AS label, count(*) AS rooms,
           coalesce(sum(u.adults), 0) AS adults,
           coalesce(sum(u.children), 0) AS children
    FROM booking.reservation_units u
    WHERE u.property_id = :p AND u.arrival_date = :d
      AND u.status IN ('checked_in', 'checked_out')
    UNION ALL
    SELECT 'Checked out', count(*),
           coalesce(sum(u.adults), 0), coalesce(sum(u.children), 0)
    FROM booking.stays st
    JOIN booking.reservation_units u ON u.id = st.reservation_unit_id
    WHERE st.property_id = :p
      AND CAST(st.actual_checkout_at AS date) = :d
    UNION ALL
    SELECT 'In house', count(*),
           coalesce(sum(u.adults), 0), coalesce(sum(u.children), 0)
    {_IN_HOUSE_ON}
"""

#: What each occupied room was sold on. The reference report calls this the
#: rate plan and lists CP and Room Only -- which are meal plans, and are what
#: this system's reservation grid calls the Rate Type. Same thing, its own
#: name.
_PAX_RATE_SQL = f"""
    SELECT coalesce(mp.name, 'Room only') AS label, count(*) AS rooms,
           coalesce(sum(u.adults), 0) AS adults,
           coalesce(sum(u.children), 0) AS children
    {_IN_HOUSE_ON.replace("FROM booking.reservation_units u",
                          "FROM booking.reservation_units u "
                          "LEFT JOIN property.meal_plans mp "
                          "ON mp.id = u.meal_plan_id")}
    GROUP BY 1
    ORDER BY 2 DESC, 1
"""

_PRETTY = {
    "room_night": "Room charges", "room_stay": "Room charges (stay)",
    "reservation_change": "Reservation changes", "minibar": "Minibar",
    "restaurant": "Restaurant", "spa": "Spa", "laundry": "Laundry",
    "transport": "Transport", "no_show_penalty": "No-show penalty",
}


@router.get(
    "/night-audit/report",
    response_model=schemas.NightAuditReport,
    tags=["night-audit"],
)
def night_audit_report(
    property_id: uuid.UUID,
    business_date: date,
    caller: Caller = Depends(require_org_permission("payments", "view")),
    db: Session = Depends(get_session),
):
    """The manager's report for one day: sales, receipts, and what is owed.

    Sales and receipts are read back from ledger rows carrying that business
    date. Those rows are immutable and dated, so the answer is the same next
    March as it is tonight -- which is the whole point of an audit trail, and
    the reason none of this is recomputed from today's bookings.
    """
    assert_property_in_org(db, caller, property_id)
    args = {"p": property_id, "d": business_date}

    sales, charged_total, tax_total = [], Decimal("0"), Decimal("0")
    for r in db.execute(text(_SALES_SQL), args).mappings():
        charges, tax = Decimal(str(r["charges"])), Decimal(str(r["tax"]))
        charged_total += charges
        tax_total += tax
        sales.append(schemas.SalesLine(
            category=_PRETTY.get(r["category"], r["category"].replace('_', ' ').title()),
            charges=charges, tax=tax, total=charges + tax,
        ))

    by_method: dict[str, list] = {}
    by_user: dict[str, Decimal] = {}
    receipts: list[schemas.ReceiptDetailLine] = []
    collected = Decimal("0")
    for r in db.execute(text(_RECEIPTS_SQL), args).mappings():
        amt = Decimal(str(r["amount"]))
        collected += amt
        m = by_method.setdefault(r["method"].title(), [Decimal("0"), 0])
        m[0] += amt
        m[1] += 1
        by_user[r["cashier"]] = by_user.get(r["cashier"], Decimal("0")) + amt
        receipts.append(schemas.ReceiptDetailLine(
            reference=r["reference"], room=r["room"],
            method=r["method"].title(), amount=amt,
            cashier=r["cashier"], received_at=r["received_at"],
        ))

    outstanding = Decimal(str(
        db.execute(text(_OUTSTANDING_SQL), args).scalar() or 0))

    # The snapshot the audit took, if that run took one.
    occupancy = db.execute(
        text(
            """
            SELECT st.detail FROM finance.night_audit_steps st
            JOIN finance.night_audit_runs r ON r.id = st.run_id
            WHERE r.property_id = :p AND r.business_date = :d
              AND st.step_code = 'snapshot_occupancy'
            ORDER BY st.created_at DESC LIMIT 1
            """
        ),
        args,
    ).scalar()

    departures = []
    for r in db.execute(text(_DEPARTURES_SQL), args).mappings():
        charged = Decimal(str(r["charged"]))
        paid = Decimal(str(r["paid"]))
        # Nights actually slept, not the nights the booking asked for: a
        # departure date is never an occupied night, so a same-day checkout
        # is zero and should read as zero.
        left = r["left_at"].date() if r["left_at"] else r["departure_date"]
        nights = max((left - r["arrival_date"]).days, 0)
        departures.append(schemas.DepartureLine(
            reservation_number=r["reservation_number"], room=r["room"],
            guest=r["guest"], arrival_date=r["arrival_date"],
            departure_date=r["departure_date"], nights=nights,
            left_at=r["left_at"], charged=charged, paid=paid,
            balance=charged - paid,
        ))

    pax_status = [
        schemas.PaxLine(label=r["label"], rooms=int(r["rooms"]),
                        adults=int(r["adults"]), children=int(r["children"]))
        for r in db.execute(text(_PAX_STATUS_SQL), args).mappings()
    ]
    pax_by_rate_type = [
        schemas.PaxLine(label=r["label"], rooms=int(r["rooms"]),
                        adults=int(r["adults"]), children=int(r["children"]))
        for r in db.execute(text(_PAX_RATE_SQL), args).mappings()
    ]

    currency = db.execute(
        text("SELECT currency FROM iam.properties WHERE id = :p"),
        {"p": property_id},
    ).scalar() or "INR"

    return schemas.NightAuditReport(
        property_id=property_id,
        business_date=business_date,
        currency=currency,
        sales=sales,
        sales_total=schemas.SalesLine(
            category="Total", charges=charged_total, tax=tax_total,
            total=charged_total + tax_total),
        receipts_by_method=[
            schemas.ReceiptLine(label=k, amount=v[0], count=v[1])
            for k, v in sorted(by_method.items())
        ],
        receipts_by_user=[
            schemas.ReceiptLine(label=k, amount=v)
            for k, v in sorted(by_user.items())
        ],
        receipts_total=collected,
        receipts=receipts,
        balance=schemas.DayBalance(
            charged=charged_total + tax_total,
            collected=collected,
            outstanding=outstanding,
        ),
        occupancy=occupancy,
        departures=departures,
        pax_status=pax_status,
        pax_by_rate_type=pax_by_rate_type,
    )


@router.get(
    "/night-audit/history",
    response_model=schemas.NightAuditHistoryOut,
    tags=["night-audit"],
)
def night_audit_history(
    property_id: uuid.UUID,
    from_date: date | None = None,
    to_date: date | None = None,
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    caller: Caller = Depends(require_org_permission("payments", "view")),
    db: Session = Depends(get_session),
):
    """Runs in a date range, newest first, with what each step actually did.

    The range is applied here rather than in the browser. Taking the newest N
    and filtering client-side means the nights outside that N are invisible
    however you filter -- and on an audit trail, quietly not showing a night is
    the one failure that matters.
    """
    assert_property_in_org(db, caller, property_id)
    where = "r.property_id = :p"
    args: dict = {"p": property_id, "lim": limit, "off": offset}
    if from_date is not None:
        where += " AND r.business_date >= :from_d"
        args["from_d"] = from_date
    if to_date is not None:
        where += " AND r.business_date <= :to_d"
        args["to_d"] = to_date

    total = db.execute(
        text(f"SELECT count(*) FROM finance.night_audit_runs r WHERE {where}"),
        args,
    ).scalar_one()

    runs = db.execute(
        text(
            f"""
            SELECT r.id, r.business_date, r.run_number, r.status,
                   r.started_at, r.completed_at, u.display_name AS run_by_name
            FROM finance.night_audit_runs r
            LEFT JOIN iam.users u ON u.id = r.run_by
            WHERE {where}
            ORDER BY r.business_date DESC, r.run_number DESC
            LIMIT :lim OFFSET :off
            """
        ),
        args,
    ).mappings().all()
    if not runs:
        return schemas.NightAuditHistoryOut(
            rows=[], total=total, limit=limit, offset=offset)
    steps = db.execute(
        text(
            """
            SELECT run_id, step_code, status, error, detail
            FROM finance.night_audit_steps
            WHERE run_id = ANY(:ids)
            ORDER BY created_at
            """
        ),
        {"ids": [r["id"] for r in runs]},
    ).mappings().all()
    by_run = {}
    for st in steps:
        by_run.setdefault(st["run_id"], []).append(
            schemas.NightAuditStepOut(
                step_code=st["step_code"], status=st["status"],
                error=st["error"], detail=st["detail"],
            )
        )
    return schemas.NightAuditHistoryOut(
        rows=[
            schemas.NightAuditRunOut(
                run_id=r["id"], business_date=r["business_date"],
                run_number=r["run_number"], status=r["status"],
                started_at=r["started_at"], completed_at=r["completed_at"],
                run_by_name=r["run_by_name"],
                steps=by_run.get(r["id"], []),
            )
            for r in runs
        ],
        total=total, limit=limit, offset=offset,
    )
