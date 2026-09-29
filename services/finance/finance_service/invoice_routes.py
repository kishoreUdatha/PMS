"""Invoice and Credit Note (screen 116).

The folio lifecycle stopped one step short. A guest could be charged, take a
payment, have a line adjusted and a payment reversed — and then leave with no
document. This is that document.

**An invoice is a snapshot, not a view.** ``customer_snapshot`` and
``totals_snapshot`` are written at issue and never recomputed. Reprinting last
March's invoice must show what it said last March, even though the guest has
since moved house and the tax rate has changed twice. A draft, by contrast, is
recomputed on every read — it is still a working document.

**A folio entry can be invoiced once.** ``uq_invoice_line_entry`` enforces it
in the database rather than in a code path somebody can forget, so no charge
can be billed on two invoices.

**Numbers are allocated at issue, never at draft.** An abandoned draft must not
burn a number out of a fiscal sequence; a gap in an invoice series is the kind
of thing an auditor asks about. The allocator is bumped under ``FOR UPDATE`` so
two cashiers issuing at the same moment cannot take the same number.

**A credit note posts to the ledger.** Issuing one writes a credit folio entry
with ``source_type = 'credit_note'``, so the guest's balance and the invoice's
outstanding figure move together. Nothing is edited or deleted: an invoice that
was wrong is credited, exactly as a folio line that was wrong is reversed.

**No tax registration is invented.** The mockup shows a GST invoice — GSTIN,
place of supply, SAC codes, a CGST/SGST split. A GSTIN is a real registration
issued by a real authority, and printing a made-up one produces a false tax
document. ``finance.invoice_settings`` holds those fields and they start empty:
until a property fills them in, ``tax_compliance`` reports the invoice as not
compliant and names the missing fields. Two consequences worth stating:

* **CGST/SGST versus IGST is decided, not assumed.** It depends on whether the
  place of supply matches the property's state, so with no ``state_code``
  configured the split cannot be determined and the tax lines are reported
  exactly as they were posted in ``folio_entry_taxes``.
* **SAC codes are absent.** They belong on a charge-code catalogue —
  ``finance.charge_codes`` exists but holds no rows, and no folio entry
  references one. Inventing a service accounting code per line would be
  guessing at a tax classification.
"""

from __future__ import annotations

import json
import re
import uuid
from datetime import date, datetime
from decimal import Decimal

from chirala_common.audit import record_audit
from chirala_common.gstin import normalise as normalise_gstin, problem as gstin_problem
from chirala_common.india import normalise_state, state_code_problem
from chirala_common.postal import problem as postal_problem
from chirala_common.authz import (
    Caller, assert_property_in_org, build_authz, require_property_permission,
)
from chirala_common.routing import TransactionalRoute
from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.orm import Session

from chirala_common import payment_methods
from .database import get_session
from .folio_pdf import render_folio_pdf
from .receipt_pdf import receipt_number, render_receipt_pdf
from chirala_common.mailer import MailNotConfigured, send as send_mail
from .invoice_pdf import render_invoice_pdf
from .registration_card import render_registration_card
from .settings import settings

_get_caller, require_permission, _require_org = build_authz(
    get_session, lambda: settings.environment,
    lambda: settings.service_token,
)

invoice_router = APIRouter(tags=["invoices"], route_class=TransactionalRoute)

# Entry sources that are how a bill was settled, not what was billed. An
# invoice lists charges; payments appear against it, not on it.
SETTLEMENT_SOURCES = ("payment", "refund", "security_deposit", "credit_note")

# What must be configured before an invoice can call itself GST compliant.
GST_REQUIRED = {
    "legal_name": "Registered legal name",
    "address_line": "Registered address",
    "state": "State",
    "state_code": "GST state code",
    "gstin": "GSTIN",
}

DESCRIPTIONS = {
    "room_stay": "Room charge", "room_stay_tax": "Room tax",
    "restaurant": "Restaurant", "minibar": "Mini bar",
    "breakfast": "Breakfast", "spa": "Spa treatment",
    "laundry": "Laundry service", "activity": "Activity",
    "transport": "Transport", "security_deposit": "Security deposit",
    "cancellation_fee": "Cancellation fee",
    "no_show_penalty": "No-show penalty",
    "no_show_penalty_tax": "No-show penalty tax",
    "reservation_change": "Reservation change", "room_move": "Room move",
    "adjustment": "Adjustment", "credit_note": "Credit note",
}


def _describe(source_type: str | None) -> str:
    return DESCRIPTIONS.get(
        source_type or "", (source_type or "Charge").replace("_", " ").capitalize()
    )


# --------------------------------------------------------------------------
# Schemas
# --------------------------------------------------------------------------
class InvoiceLine(BaseModel):
    entry_id: uuid.UUID
    business_date: date
    description: str
    source_type: str | None
    quantity: Decimal
    unit_price: Decimal
    amount: Decimal
    # Tax actually posted against this charge, never recomputed.
    taxes: list["TaxLine"] = []


class TaxLine(BaseModel):
    code: str
    rate: Decimal
    taxable_amount: Decimal
    tax_amount: Decimal


class Party(BaseModel):
    name: str | None = None
    address_line: str | None = None
    city: str | None = None
    state: str | None = None
    state_code: str | None = None
    postal_code: str | None = None
    country: str | None = None
    phone: str | None = None
    email: str | None = None
    gstin: str | None = None


class TaxCompliance(BaseModel):
    """Whether this can honestly be called a tax invoice."""

    compliant: bool
    missing: list[str]
    # Only decidable when both the supplier's and the customer's state are
    # known. Otherwise the posted tax lines are reported as-is.
    supply_type: str | None = None
    note: str


class PaymentRow(BaseModel):
    id: uuid.UUID
    received_at: datetime
    method: str
    reference: str | None
    amount: Decimal


class CreditNoteRow(BaseModel):
    id: uuid.UUID
    number: int
    display_number: str
    reason: str
    amount: Decimal
    status: str
    issued_at: datetime | None
    created_at: datetime


class AuditRow(BaseModel):
    label: str
    actor: str
    at: datetime
    status: str


class InvoiceOut(BaseModel):
    id: uuid.UUID
    status: str
    fiscal_series: str | None
    invoice_number: int | None
    display_number: str
    issued_at: datetime | None
    created_at: datetime
    notes: str | None
    cancel_reason: str | None

    folio_id: uuid.UUID
    folio_no: str | None
    reservation_number: str | None
    arrival_date: date | None
    departure_date: date | None
    nights: int | None
    adults: int | None
    children: int | None
    room_code: str | None
    room_type: str | None
    currency: str

    supplier: Party
    customer: Party
    lines: list[InvoiceLine]
    tax_lines: list[TaxLine]

    subtotal: Decimal
    tax_total: Decimal
    total: Decimal
    amount_received: Decimal
    # Gross taken and given back, so the payments panel can show the
    # arithmetic instead of a total that disagrees with its own rows.
    amount_taken: Decimal
    amount_refunded: Decimal
    credited: Decimal
    balance_due: Decimal
    amount_in_words: str

    payments: list[PaymentRow]
    credit_notes: list[CreditNoteRow]
    audit: list[AuditRow]
    tax_compliance: TaxCompliance

    can_issue: bool
    can_cancel: bool
    can_credit: bool


class InvoiceListRow(BaseModel):
    id: uuid.UUID
    display_number: str
    status: str
    guest_name: str | None
    reservation_number: str | None
    issued_at: datetime | None
    created_at: datetime
    total: Decimal
    balance_due: Decimal
    credited: Decimal


class InvoiceListOut(BaseModel):
    rows: list[InvoiceListRow]
    total: int
    can_create: bool


class InvoiceCreate(BaseModel):
    property_id: uuid.UUID
    folio_id: uuid.UUID
    # Billing details for this invoice only. There is no GSTIN field on a
    # guest record, so a business customer's number is captured here.
    customer_name: str | None = None
    customer_gstin: str | None = None
    customer_address: str | None = None
    notes: str | None = None


# What the desk can be configured to take. Adding one is a change here
# and a checkbox on the screen; it is not a migration.
# Was its own five-method list, missing the wallet that cashiering knew
# about, so an invoice could not name a method a cashier could take.
PAYMENT_METHODS = payment_methods.METHODS


class Settings(BaseModel):
    legal_name: str | None = None
    tagline: str | None = Field(default=None, max_length=120)
    #: Whether a quoted rate already contains tax or has it added. This
    #: changes what every folio line means, so it is stored rather than
    #: assumed per screen.
    tax_inclusive: bool = True
    #: What a cashier may choose from when taking money.
    payment_methods: list[str] = Field(
        default_factory=lambda: ["cash", "upi", "card", "bank_transfer"])
    address_line: str | None = None
    city: str | None = None
    state: str | None = None
    state_code: str | None = None
    postal_code: str | None = None
    country: str = "India"
    phone: str | None = None
    email: str | None = None
    #: Asked, not inferred from whether a GSTIN happens to be filled in. None
    #: means nobody has answered yet, which is not the same as "no" -- the
    #: onboarding step stays open and asks rather than guessing.
    gst_registered: bool | None = None
    gstin: str | None = None
    fiscal_series: str = "INV"
    next_number: int = 1
    footer_note: str | None = None


class CreditNoteCreate(BaseModel):
    property_id: uuid.UUID
    amount: Decimal = Field(gt=0)
    reason: str = Field(min_length=3, max_length=500)


class CancelIn(BaseModel):
    property_id: uuid.UUID
    reason: str = Field(min_length=3, max_length=500)


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------
ONES = ("", "One", "Two", "Three", "Four", "Five", "Six", "Seven", "Eight",
        "Nine", "Ten", "Eleven", "Twelve", "Thirteen", "Fourteen", "Fifteen",
        "Sixteen", "Seventeen", "Eighteen", "Nineteen")
TENS = ("", "", "Twenty", "Thirty", "Forty", "Fifty", "Sixty", "Seventy",
        "Eighty", "Ninety")


def _under_thousand(n: int) -> str:
    if n < 20:
        return ONES[n]
    if n < 100:
        return TENS[n // 10] + (f" {ONES[n % 10]}" if n % 10 else "")
    return ONES[n // 100] + " Hundred" + (
        f" {_under_thousand(n % 100)}" if n % 100 else "")


def _in_words(amount: Decimal, currency: str) -> str:
    """Indian numbering — lakh and crore, as an Indian invoice is read."""
    unit = "Rupees" if currency == "INR" else currency
    whole = int(amount)
    paise = int(round((amount - whole) * 100))
    if whole == 0:
        words = "Zero"
    else:
        parts, groups = [], [
            (10_000_000, "Crore"), (100_000, "Lakh"), (1_000, "Thousand"),
        ]
        rest = whole
        for size, name in groups:
            if rest >= size:
                parts.append(f"{_under_thousand(rest // size)} {name}")
                rest %= size
        if rest:
            parts.append(_under_thousand(rest))
        words = " ".join(parts)
    tail = f" and {_under_thousand(paise)} Paise" if paise else ""
    return f"{unit} {words}{tail} Only"


def _settings(db: Session, property_id: uuid.UUID) -> dict:
    row = db.execute(
        text("SELECT * FROM finance.invoice_settings WHERE property_id = :p"),
        {"p": property_id},
    ).mappings().first()
    return dict(row) if row else {}


def _compliance(supplier: Party, customer: Party) -> TaxCompliance:
    missing = [label for field, label in GST_REQUIRED.items()
               if not getattr(supplier, field, None)]
    if missing:
        return TaxCompliance(
            compliant=False, missing=missing, supply_type=None,
            note="Not a GST tax invoice. The property's registration details "
                 "are not configured, so no GSTIN or place of supply can be "
                 "shown. Set them under invoice settings.",
        )
    if customer.state_code and customer.state_code != supplier.state_code:
        return TaxCompliance(
            compliant=True, missing=[], supply_type="inter_state",
            note="Inter-state supply — IGST applies.",
        )
    if customer.state_code:
        return TaxCompliance(
            compliant=True, missing=[], supply_type="intra_state",
            note="Intra-state supply — CGST and SGST apply.",
        )
    return TaxCompliance(
        compliant=True, missing=[], supply_type=None,
        note="Place of supply not recorded for this customer, so the tax lines "
             "are shown exactly as they were posted.",
    )


_CONTEXT_SQL = """
    SELECT f.id AS folio_id, f.currency, f.organization_id,
           f.status AS folio_status, f.reservation_id,
           f.folio_no,
           -- The bill-to party's own details, set when this folio was opened.
           -- A booking in a company's name splits into the company's folio for
           -- the room and the guest's for what they ate, and the guest's
           -- employer is not reclaiming the tax on dinner -- so the number that
           -- belongs on *this* document is the one recorded against this folio,
           -- not the commercial account's.
           f.gstin AS folio_gstin,
           f.sharer_name,
           f.show_tax_on_folio,
           r.number AS reservation_number,
           g.full_name AS guest_name, g.email, g.phone,
           g.address_line, g.city, g.state, g.postal_code, g.country,
           ru.arrival_date, ru.departure_date, ru.adults, ru.children,
           rm.code AS room_code, rt.name AS room_type,
           f.type AS folio_type,
           ca.id AS account_id, ca.name AS account_name,
           ca.legal_name AS account_legal_name, ca.gstin AS account_gstin,
           ca.address_line AS account_address, ca.city AS account_city,
           ca.state AS account_state, ca.state_code AS account_state_code,
           ca.postal_code AS account_postal, ca.country AS account_country,
           ca.contact_phone AS account_phone, ca.contact_email AS account_email
    FROM finance.folios f
    -- A company folio is billed to the company, so the invoice needs its
    -- registered details, not the guest's home address.
    LEFT JOIN engagement.commercial_accounts ca
           ON ca.id = f.commercial_account_id
    LEFT JOIN booking.reservations r ON r.id = f.reservation_id
    LEFT JOIN engagement.guests g ON g.id = r.primary_guest_id
    LEFT JOIN booking.reservation_units ru ON ru.reservation_id = r.id
    LEFT JOIN property.rooms rm ON rm.id = ru.assigned_room_id
    LEFT JOIN property.room_types rt ON rt.id = ru.room_type_id
    WHERE f.id = :f
    ORDER BY ru.arrival_date
    LIMIT 1
"""

_UNINVOICED_SQL = """
    SELECT e.id, e.business_date, e.amount, e.entry_type, e.source_type
    FROM finance.folio_entries e
    LEFT JOIN finance.invoice_lines il ON il.folio_entry_id = e.id
    WHERE e.folio_id = :f
      AND il.id IS NULL
      AND COALESCE(e.source_type, '') <> ALL(:skip)
    ORDER BY e.posted_at
"""

_FROZEN_SQL = """
    SELECT e.id, e.business_date, e.amount, e.entry_type, e.source_type
    FROM finance.invoice_lines il
    JOIN finance.folio_entries e ON e.id = il.folio_entry_id
    WHERE il.invoice_id = :i
    ORDER BY e.posted_at
"""


def _lines_for(db: Session, invoice_id: uuid.UUID | None,
               folio_id: uuid.UUID) -> list[InvoiceLine]:
    """A draft is recomputed; an issued invoice reads its own frozen lines."""
    if invoice_id is None:
        rows = db.execute(
            text(_UNINVOICED_SQL),
            {"f": folio_id, "skip": list(SETTLEMENT_SOURCES)},
        ).mappings().all()
    else:
        rows = db.execute(text(_FROZEN_SQL), {"i": invoice_id}).mappings().all()

    ids = [r["id"] for r in rows]
    taxes: dict[uuid.UUID, list[TaxLine]] = {}
    if ids:
        for t in db.execute(
            text("SELECT folio_entry_id, tax_code, rate_snapshot, "
                 "taxable_amount, tax_amount FROM finance.folio_entry_taxes "
                 "WHERE folio_entry_id = ANY(:ids)"),
            {"ids": ids},
        ).mappings():
            taxes.setdefault(t["folio_entry_id"], []).append(TaxLine(
                code=t["tax_code"], rate=t["rate_snapshot"],
                taxable_amount=t["taxable_amount"], tax_amount=t["tax_amount"]))

    out = []
    for r in rows:
        # A credit among the charges reduces the bill; it is signed so the
        # arithmetic on the document matches the ledger.
        amount = r["amount"] if r["entry_type"] == "debit" else -r["amount"]
        out.append(InvoiceLine(
            entry_id=r["id"], business_date=r["business_date"],
            description=_describe(r["source_type"]),
            source_type=r["source_type"],
            quantity=Decimal(1), unit_price=amount, amount=amount,
            taxes=taxes.get(r["id"], []),
        ))
    return out


def _payments(db: Session, folio_id: uuid.UUID) -> list[PaymentRow]:
    return [
        PaymentRow(id=p["id"], received_at=p["received_at"],
                   method=p["method"], reference=p["reference"],
                   amount=p["amount"])
        for p in db.execute(
            text(
                """
                SELECT p.id, p.received_at, lower(p.method) AS method,
                       p.reference, sum(pa.amount) AS amount
                FROM finance.payment_allocations pa
                JOIN finance.payments p ON p.id = pa.payment_id
                WHERE pa.folio_id = :f
                GROUP BY p.id, p.received_at, p.method, p.reference
                ORDER BY p.received_at
                """
            ),
            {"f": folio_id},
        ).mappings()
    ]


def _credit_notes(db: Session, invoice_id: uuid.UUID) -> list[CreditNoteRow]:
    rows = db.execute(
        text("SELECT id, number, reason, amount, status, issued_at, created_at "
             "FROM finance.credit_notes WHERE invoice_id = :i "
             "ORDER BY created_at"),
        {"i": invoice_id},
    ).mappings().all()
    return [
        CreditNoteRow(
            **c,
            display_number=(
                f"CN-{(c['issued_at'] or c['created_at']).year}"
                f"-{int(c['number']):04d}"),
        )
        for c in rows
    ]


AUDIT_LABELS = {
    "invoice.drafted": ("Prepared By", "Draft"),
    "invoice.issued": ("Issued By", "Issued"),
    "invoice.cancelled": ("Cancelled By", "Cancelled"),
    "invoice.credit_note.issued": ("Credit Note By", "Credited"),
}


def _audit(db: Session, invoice_id: uuid.UUID) -> list[AuditRow]:
    out = []
    for a in db.execute(
        text(
            """
            SELECT a.occurred_at, a.action, a.actor_subject, u.display_name
            FROM iam.audit_events a
            LEFT JOIN iam.users u ON u.subject_id = a.actor_subject
            WHERE a.entity_type = 'invoice' AND a.entity_id = :id
            ORDER BY a.occurred_at
            """
        ),
        {"id": str(invoice_id)},
    ).mappings():
        label, status = AUDIT_LABELS.get(
            a["action"], (a["action"].replace(".", " ").capitalize(), "-"))
        out.append(AuditRow(
            label=label,
            actor=a["display_name"] or a["actor_subject"] or "System",
            at=a["occurred_at"], status=status))
    return out


def _may(db: Session, caller: Caller, property_id: uuid.UUID,
         action: str) -> bool:
    from chirala_common.authz import _GRANT_SQL
    return db.execute(
        text(_GRANT_SQL),
        {"uid": caller.user_id, "res": "payments", "act": action,
         "prop": str(property_id)},
    ).first() is not None


def _build(db: Session, inv: dict, caller: Caller) -> InvoiceOut:
    """Assemble one invoice. Drafts recompute; issued invoices read frozen data."""
    ctx = db.execute(text(_CONTEXT_SQL), {"f": inv["folio_id"]}).mappings().first()
    if ctx is None:
        raise HTTPException(status_code=404, detail="Folio not found")

    issued = inv["status"] != "draft"
    snap = inv["totals_snapshot"] or {}
    cust = inv["customer_snapshot"] or {}
    sett = _settings(db, inv["property_id"])

    supplier = Party(
        name=sett.get("legal_name"), address_line=sett.get("address_line"),
        city=sett.get("city"), state=sett.get("state"),
        state_code=sett.get("state_code"), postal_code=sett.get("postal_code"),
        country=sett.get("country"), phone=sett.get("phone"),
        email=sett.get("email"), gstin=sett.get("gstin"),
    )
    # An issued invoice shows the registration it was issued under, not
    # today's — reprinting last year's document must not rewrite it.
    if issued and snap.get("supplier"):
        supplier = Party(**snap["supplier"])

    # A company folio is billed to the company. The guest still sleeps in the
    # room, but the invoice is not addressed to them, and their home address
    # is not what a corporate accounts department needs to see. An explicit
    # per-invoice override still wins over both.
    if ctx["account_id"] and not cust.get("name"):
        customer = Party(
            name=ctx["account_legal_name"] or ctx["account_name"],
            address_line=ctx["account_address"], city=ctx["account_city"],
            state=ctx["account_state"], state_code=ctx["account_state_code"],
            postal_code=ctx["account_postal"], country=ctx["account_country"],
            phone=ctx["account_phone"], email=ctx["account_email"],
            gstin=ctx["account_gstin"],
        )
    else:
        customer = Party(
            name=cust.get("name") or ctx["guest_name"],
            address_line=cust.get("address_line") or ctx["address_line"],
            city=cust.get("city") or ctx["city"],
            state=cust.get("state") or ctx["state"],
            state_code=cust.get("state_code"),
            postal_code=cust.get("postal_code") or ctx["postal_code"],
            country=cust.get("country") or ctx["country"],
            phone=cust.get("phone") or ctx["phone"],
            email=cust.get("email") or ctx["email"], gstin=cust.get("gstin"),
        )

    lines = _lines_for(db, inv["id"] if issued else None, inv["folio_id"])

    tax_by_code: dict[str, TaxLine] = {}
    for ln in lines:
        for t in ln.taxes:
            agg = tax_by_code.get(t.code)
            if agg is None:
                tax_by_code[t.code] = TaxLine(**t.model_dump())
            else:
                agg.taxable_amount += t.taxable_amount
                agg.tax_amount += t.tax_amount
    tax_lines = sorted(tax_by_code.values(), key=lambda t: t.code)

    charge_total = sum((ln.amount for ln in lines), Decimal(0))
    tax_total = sum((t.tax_amount for t in tax_lines), Decimal(0))
    # Tax is posted as its own folio entries, so it is already inside the
    # charge total. The subtotal is what remains once it is taken back out.
    subtotal = charge_total - tax_total
    total = charge_total
    if issued and snap:
        subtotal = Decimal(str(snap.get("subtotal", subtotal)))
        tax_total = Decimal(str(snap.get("tax_total", tax_total)))
        total = Decimal(str(snap.get("total", total)))

    taken = db.execute(
        text("SELECT COALESCE(sum(amount), 0) FROM finance.payment_allocations "
             "WHERE folio_id = :f"),
        {"f": inv["folio_id"]},
    ).scalar_one()
    refunded = db.execute(
        text("SELECT COALESCE(sum(amount), 0) FROM finance.folio_entries "
             "WHERE folio_id = :f AND entry_type = 'debit' "
             "  AND source_type = 'refund'"),
        {"f": inv["folio_id"]},
    ).scalar_one()
    # Net of refunds. A payment that was handed back is not money received,
    # and counting it gross overstated what the guest had settled.
    received = taken - refunded
    credited = db.execute(
        text("SELECT COALESCE(sum(amount), 0) FROM finance.credit_notes "
             "WHERE invoice_id = :i AND status = 'issued'"),
        {"i": inv["id"]},
    ).scalar_one()

    nights = None
    if ctx["arrival_date"] and ctx["departure_date"]:
        nights = (ctx["departure_date"] - ctx["arrival_date"]).days

    series = inv["fiscal_series"] or sett.get("fiscal_series") or "INV"
    if inv["invoice_number"]:
        # Series, the year it was issued in, then the number in the sequence.
        year = (inv["issued_at"] or inv["created_at"]).year
        display = f"{series}-{year}-{int(inv['invoice_number']):04d}"
    else:
        display = "Draft - number assigned on issue"

    return InvoiceOut(
        id=inv["id"], status=inv["status"], fiscal_series=inv["fiscal_series"],
        invoice_number=inv["invoice_number"], display_number=display,
        issued_at=inv["issued_at"], created_at=inv["created_at"],
        notes=inv["notes"], cancel_reason=inv["cancel_reason"],
        folio_id=inv["folio_id"], folio_no=str(inv["folio_id"])[:8].upper(),
        reservation_number=ctx["reservation_number"],
        arrival_date=ctx["arrival_date"], departure_date=ctx["departure_date"],
        nights=nights, adults=ctx["adults"], children=ctx["children"],
        room_code=ctx["room_code"], room_type=ctx["room_type"],
        currency=ctx["currency"],
        supplier=supplier, customer=customer, lines=lines, tax_lines=tax_lines,
        subtotal=subtotal, tax_total=tax_total, total=total,
        amount_received=received, amount_taken=taken,
        amount_refunded=refunded, credited=credited,
        balance_due=total - received - credited,
        amount_in_words=_in_words(total, ctx["currency"]),
        payments=_payments(db, inv["folio_id"]),
        credit_notes=_credit_notes(db, inv["id"]),
        audit=_audit(db, inv["id"]),
        tax_compliance=_compliance(supplier, customer),
        can_issue=(inv["status"] == "draft" and bool(lines)
                   and _may(db, caller, inv["property_id"], "approve")),
        can_cancel=(inv["status"] == "draft"
                    and _may(db, caller, inv["property_id"], "edit")),
        can_credit=(inv["status"] == "issued"
                    and _may(db, caller, inv["property_id"], "approve")),
    )


def _guard_numbering_timing(db: Session, inv: dict) -> None:
    """Refuse to number a bill that is still moving.

    ``on_checkout`` -- the default, and what most desks want -- says the number
    belongs to the moment the guest leaves. Taking one before that is taking it
    against a stay that can still have a minibar charge posted to it.

    ``post_checkout`` says the opposite: this bill is not finished when the
    guest walks out. A company folio waiting on a purchase order, a group being
    reconciled line by line. It waits for the folio to be closed as well, which
    is the desk saying the bill has stopped moving.

    Both refusals name the setting and where to change it, because the person
    hitting this did not choose it and would otherwise be told only "no".
    """
    if not inv.get("folio_id"):
        return                      # nothing to check it against

    row = db.execute(
        text(
            """
            SELECT f.invoice_number_timing, f.status AS folio_status,
                   f.folio_no,
                   (SELECT ru.status FROM booking.reservation_units ru
                     WHERE ru.reservation_id = f.reservation_id
                     ORDER BY ru.line_index LIMIT 1) AS unit_status
              FROM finance.folios f
             WHERE f.id = :f
            """
        ),
        {"f": inv["folio_id"]},
    ).mappings().first()
    if row is None:
        return

    timing = row["invoice_number_timing"] or "on_checkout"
    # Over, not merely checked out. A cancelled stay and a no-show both carry
    # real invoiceable money -- the cancellation fee, the no-show penalty --
    # and both are as finished as a departure is. Testing for 'checked_out'
    # alone would have made every cancellation fee in this property
    # permanently un-invoiceable: there are 198 cancelled stays here against
    # 46 departures, so the common case would have been the broken one.
    #
    # What is blocked is a stay still running: 'reserved' or 'checked_in',
    # where a minibar charge can still land on the bill after it was numbered.
    stay_over = row["unit_status"] in ("checked_out", "cancelled", "no_show")
    where = f"folio {row['folio_no']}" if row["folio_no"] else "this folio"

    if not stay_over:
        raise HTTPException(
            status_code=409,
            detail=(
                f"This stay is still in progress, and {where} is set to take "
                f"its invoice number "
                + ("at checkout" if timing == "on_checkout"
                   else "after checkout")
                + ". Numbering it now would spend a number from the series on "
                  "a bill that can still change. Check the guest out first, or "
                  "change when this folio is numbered."),
        )

    if timing == "post_checkout" and row["folio_status"] == "open":
        raise HTTPException(
            status_code=409,
            detail=(
                # Not str.capitalize(): it lower-cases everything after the
                # first character, which turned "folio FOL-1191" into
                # "Folio fol-1191" -- a folio number that does not exist.
                f"{where[:1].upper() + where[1:]} is set to be numbered after "
                "checkout, "
                "and it is still open — which is the setting saying the bill "
                "is not finished. Close the folio when it is, or change when "
                "this folio is numbered."),
        )


def _load(db: Session, invoice_id: uuid.UUID, property_id: uuid.UUID) -> dict:
    row = db.execute(
        text("SELECT * FROM finance.invoices WHERE id = :i AND property_id = :p"),
        {"i": invoice_id, "p": property_id},
    ).mappings().first()
    if row is None:
        raise HTTPException(status_code=404, detail="Invoice not found")
    return dict(row)


# --------------------------------------------------------------------------
# Settings
# --------------------------------------------------------------------------
@invoice_router.get("/invoice-settings", response_model=Settings)
def get_settings(
    property_id: uuid.UUID,
    caller: Caller = Depends(require_permission("payments", "view")),
    db: Session = Depends(get_session),
):
    """The property's billing identity. Empty until somebody fills it in."""
    assert_property_in_org(db, caller, property_id)
    row = _settings(db, property_id)
    return Settings(**{k: v for k, v in row.items()
                       if k in Settings.model_fields}) if row else Settings()


@invoice_router.put("/invoice-settings", response_model=Settings)
def put_settings(
    property_id: uuid.UUID,
    body: Settings,
    caller: Caller = Depends(require_permission("payments", "configure")),
    db: Session = Depends(get_session),
):
    """Set the billing identity.

    ``next_number`` is deliberately writable — a property migrating from
    another system has to continue its existing sequence rather than restart
    at 1. It cannot be moved backwards over numbers already issued, because
    that would collide with ``uq_invoice_number``.
    """
    assert_property_in_org(db, caller, property_id)
    org = db.execute(
        text("SELECT organization_id FROM iam.properties WHERE id = :p"),
        {"p": property_id},
    ).scalar_one()

    # invoice_number is numeric, so the highest issued one is a plain max().
    highest = db.execute(
        text("SELECT COALESCE(max(invoice_number), 0) FROM finance.invoices "
             "WHERE property_id = :p AND fiscal_series = :s"),
        {"p": property_id, "s": body.fiscal_series},
    ).scalar_one()
    if body.next_number <= highest:
        raise HTTPException(
            status_code=422,
            detail=f"Series {body.fiscal_series} has already reached "
                   f"{highest}. The next number must be above it.",
        )

    unknown = [m for m in body.payment_methods if m not in PAYMENT_METHODS]
    if unknown:
        raise HTTPException(
            status_code=422,
            detail=f"Unknown payment method(s): {', '.join(unknown)}.",
        )
    if not body.payment_methods:
        raise HTTPException(
            status_code=422,
            detail="A property must accept at least one payment method.",
        )

    # The shape of the number, and that it agrees with the place of supply.
    # Checked here rather than on the screen alone, so every writer gets it --
    # an invoice headed TAX INVOICE is a tax record, and a registration number
    # nobody checked is the sort of thing that is only discovered in an audit.
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
    if body.gst_registered is False and body.gstin:
        raise HTTPException(
            status_code=422,
            detail="The property is marked as not GST registered, so it "
                   "cannot carry a GSTIN. Answer Yes, or clear the number.",
        )

    before = _settings(db, property_id)
    fields = body.model_dump()
    db.execute(
        text(
            """
            INSERT INTO finance.invoice_settings
                (property_id, organization_id, legal_name, tagline,
                 address_line, city,
                 state, state_code, postal_code, country, phone, email,
                 gst_registered, gstin,
                 fiscal_series, next_number, footer_note,
                 tax_inclusive, payment_methods)
            VALUES (:p, :org, :legal_name, :tagline, :address_line, :city,
                    :state,
                    :state_code, :postal_code, :country, :phone, :email,
                    :gst_registered, :gstin,
                    :fiscal_series, :next_number, :footer_note,
                    :tax_inclusive, :payment_methods)
            ON CONFLICT (property_id) DO UPDATE SET
                legal_name = EXCLUDED.legal_name,
                tagline = EXCLUDED.tagline,
                address_line = EXCLUDED.address_line, city = EXCLUDED.city,
                state = EXCLUDED.state, state_code = EXCLUDED.state_code,
                postal_code = EXCLUDED.postal_code, country = EXCLUDED.country,
                phone = EXCLUDED.phone, email = EXCLUDED.email,
                gst_registered = EXCLUDED.gst_registered,
                gstin = EXCLUDED.gstin,
                fiscal_series = EXCLUDED.fiscal_series,
                next_number = EXCLUDED.next_number,
                footer_note = EXCLUDED.footer_note,
                tax_inclusive = EXCLUDED.tax_inclusive,
                payment_methods = EXCLUDED.payment_methods,
                updated_at = now(),
                version = finance.invoice_settings.version + 1
            """
        ),
        {"p": property_id, "org": org, **fields},
    )
    record_audit(
        db, action="invoice.settings.updated", entity_type="property",
        entity_id=str(property_id), organization_id=org,
        property_id=property_id, actor_subject=caller.subject,
        before={k: str(v) for k, v in before.items() if k in Settings.model_fields},
        after={k: str(v) for k, v in fields.items() if v is not None},
    )
    return body


# --------------------------------------------------------------------------
# List and read
# --------------------------------------------------------------------------
@invoice_router.get("/invoices", response_model=InvoiceListOut)
def list_invoices(
    property_id: uuid.UUID,
    status: str | None = Query(None),
    q: str | None = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=200),
    caller: Caller = Depends(require_permission("payments", "view")),
    db: Session = Depends(get_session),
):
    """Invoices for a property, newest first."""
    assert_property_in_org(db, caller, property_id)
    base = """
        FROM finance.invoices i
        LEFT JOIN booking.reservations r ON r.id =
            (SELECT reservation_id FROM finance.folios WHERE id = i.folio_id)
        LEFT JOIN engagement.guests g ON g.id = r.primary_guest_id
        WHERE i.property_id = :p
          AND (CAST(:st AS text) IS NULL OR i.status = CAST(:st AS text))
          AND (CAST(:q AS text) IS NULL
               OR g.full_name ILIKE '%' || CAST(:q AS text) || '%'
               OR r.number ILIKE '%' || CAST(:q AS text) || '%'
               OR i.invoice_number = CAST(:qnum AS bigint))
    """
    # The number a user can see is INV-2026-0001; the number stored is 1. Take
    # the last run of digits from whatever they typed so searching what is on
    # screen actually finds the invoice.
    runs = re.findall(r"\d+", q or "")
    qnum = int(runs[-1]) if runs else None
    params = {"p": property_id, "st": status or None, "q": q or None,
              "qnum": qnum}
    total = db.execute(text(f"SELECT count(*) {base}"), params).scalar_one()
    rows = db.execute(
        text(
            f"""
            SELECT i.id, i.status, i.fiscal_series, i.invoice_number,
                   i.issued_at, i.created_at, i.folio_id, i.totals_snapshot,
                   g.full_name AS guest_name, r.number AS reservation_number
            {base}
            ORDER BY i.created_at DESC
            LIMIT :lim OFFSET :off
            """
        ),
        {**params, "lim": page_size, "off": (page - 1) * page_size},
    ).mappings().all()

    out = []
    for r in rows:
        snap = r["totals_snapshot"] or {}
        if snap:
            total_amt = Decimal(str(snap.get("total", 0)))
        else:
            total_amt = sum(
                (ln.amount for ln in _lines_for(db, None, r["folio_id"])),
                Decimal(0))
        received = db.execute(
            text("SELECT COALESCE(sum(amount), 0) FROM finance.payment_allocations "
                 "WHERE folio_id = :f"), {"f": r["folio_id"]}).scalar_one()
        credited = db.execute(
            text("SELECT COALESCE(sum(amount), 0) FROM finance.credit_notes "
                 "WHERE invoice_id = :i AND status = 'issued'"),
            {"i": r["id"]}).scalar_one()
        series = r["fiscal_series"] or "INV"
        if r["invoice_number"]:
            year = (r["issued_at"] or r["created_at"]).year
            shown = f"{series}-{year}-{int(r['invoice_number']):04d}"
        else:
            shown = "Draft"
        out.append(InvoiceListRow(
            id=r["id"], display_number=shown,
            status=r["status"], guest_name=r["guest_name"],
            reservation_number=r["reservation_number"],
            issued_at=r["issued_at"], created_at=r["created_at"],
            total=total_amt, credited=credited,
            balance_due=total_amt - received - credited,
        ))
    return InvoiceListOut(rows=out, total=total,
                          can_create=_may(db, caller, property_id, "create"))


@invoice_router.get("/folios/{folio_id}/pdf")
def folio_pdf(
    folio_id: uuid.UUID,
    property_id: uuid.UUID,
    caller: Caller = Depends(require_permission("payments", "view")),
    db: Session = Depends(get_session),
):
    """The guest folio as a printable A4 tax invoice.

    Rendered server-side rather than left to the browser's print dialog: this
    is a document a guest is handed and the property files, so it has to come
    out the same on every machine and be reproducible from the ledger later.

    Everything on it is read at request time — nothing is snapshotted — so a
    folio reprinted after a late charge shows the late charge.
    """
    assert_property_in_org(db, caller, property_id)

    ctx = db.execute(text(_CONTEXT_SQL), {"f": folio_id}).mappings().first()
    if ctx is None:
        raise HTTPException(status_code=404, detail="Folio not found")

    rows = db.execute(
        text(
            """
            SELECT e.id, e.business_date, e.entry_type, e.amount,
                   e.source_type, e.source_line_key
            FROM finance.folio_entries e
            WHERE e.folio_id = :f
            ORDER BY e.business_date, e.posted_at
            """
        ),
        {"f": folio_id},
    ).mappings().all()

    # Whether this folio itemises tax or prints tax-inclusive totals, as the
    # desk set it when the folio was opened. Both are asked for and neither is
    # universally right: a company reclaiming GST needs every rate broken out,
    # a walk-in wants one number.
    itemise_tax = bool(ctx["show_tax_on_folio"])

    # The tax a charge attracted, against the charge itself. `post_charge`
    # writes the tax as its own debit -- otherwise the balance would never
    # include it -- and keys it `<the charge's key>#tax`. That suffix is the
    # only link between the two, and it is what makes a tax-inclusive document
    # possible: without it the tax could only be shown as a separate line,
    # which is the one thing a tax-inclusive folio must not do.
    tax_of: dict[str, Decimal] = {}
    for r in rows:
        key = r["source_line_key"] or ""
        if key.endswith("#tax"):
            parent = key[: -len("#tax")]
            tax_of[parent] = tax_of.get(parent, Decimal(0)) + Decimal(
                r["amount"] or 0)

    lines: list[dict] = []
    subtotal = taxes = paid = Decimal("0")
    for r in rows:
        amount = Decimal(r["amount"] or 0)
        src = r["source_type"] or ""
        # source_type decides, not the wording: "Room tax" does not begin with
        # "tax", so testing the description put the tax into the taxable value
        # and reported a base of 17,920 on a 17,000 room charge. The tax test
        # comes first because room_stay_tax also starts with "room_".
        is_tax = src.endswith("_tax")
        dept = ("Payments" if r["entry_type"] == "credit"
                else "Taxes" if is_tax
                else "Rooms" if src.startswith("room_")
                else "Other")
        desc = _describe(src)
        if itemise_tax or not is_tax:
            # On a tax-inclusive folio the tax is not a line of its own; it is
            # already inside the charge above, added below.
            shown = amount
            if not itemise_tax and r["entry_type"] == "debit" and not is_tax:
                shown = amount + tax_of.get(r["source_line_key"] or "",
                                            Decimal(0))
            lines.append({
                "business_date": r["business_date"], "description": desc,
                "department": dept, "qty": 1, "amount": shown,
                "entry_type": r["entry_type"],
            })
        if r["entry_type"] == "credit":
            paid += amount
        elif is_tax:
            taxes += amount
        else:
            subtotal += amount

    # The tax break-up comes from what was actually posted, so a rate change
    # never rewrites a document that has already been handed over.
    tax_rows = db.execute(
        text(
            """
            SELECT fet.tax_code AS code, sum(fet.tax_amount) AS amount
            FROM finance.folio_entry_taxes fet
            JOIN finance.folio_entries e ON e.id = fet.folio_entry_id
            WHERE e.folio_id = :f
            GROUP BY fet.tax_code
            ORDER BY fet.tax_code
            """
        ),
        {"f": folio_id},
    ).mappings().all()

    nights = None
    if ctx["arrival_date"] and ctx["departure_date"]:
        nights = (ctx["departure_date"] - ctx["arrival_date"]).days

    unit_status = db.execute(
        text("SELECT status FROM booking.reservation_units "
             "WHERE reservation_id = :r ORDER BY line_index LIMIT 1"),
        {"r": ctx["reservation_id"]},
    ).scalar()

    pdf = render_folio_pdf(
        settings=_settings(db, property_id),
        context={**dict(ctx), "nights": nights, "unit_status": unit_status},
        lines=lines,
        totals={
            "subtotal": subtotal, "taxes": taxes,
            "grand_total": subtotal + taxes, "advance_paid": paid,
            "balance": subtotal + taxes - paid,
        },
        # Withheld entirely when the folio does not itemise: a "TAX BREAKUP"
        # panel on a document that prints tax-inclusive totals is the
        # contradiction the setting exists to avoid.
        tax_lines=[dict(t) for t in tax_rows] if itemise_tax else [],
        itemise_tax=itemise_tax,
    )

    name = f"folio-{ctx['reservation_number'] or folio_id}.pdf"
    return Response(
        content=pdf, media_type="application/pdf",
        # inline so Print opens it in the viewer; the viewer still offers Save.
        headers={"Content-Disposition": f'inline; filename="{name}"'},
    )


@invoice_router.get("/invoices/{invoice_id}", response_model=InvoiceOut)
def get_invoice(
    invoice_id: uuid.UUID,
    property_id: uuid.UUID,
    caller: Caller = Depends(require_permission("payments", "view")),
    db: Session = Depends(get_session),
):
    """One invoice, in full."""
    assert_property_in_org(db, caller, property_id)
    return _build(db, _load(db, invoice_id, property_id), caller)


@invoice_router.get("/invoices/{invoice_id}/pdf")
def invoice_pdf(
    invoice_id: uuid.UUID,
    property_id: uuid.UUID,
    caller: Caller = Depends(require_permission("payments", "view")),
    db: Session = Depends(get_session),
):
    """The invoice as a printable A4 document.

    Rendered from exactly what the detail screen shows -- same ``_load`` and
    ``_build`` -- so the paper and the screen can never disagree about what
    was charged. Nothing is snapshotted, so reprinting after a credit note
    shows the credit note.
    """
    assert_property_in_org(db, caller, property_id)
    inv = _build(db, _load(db, invoice_id, property_id), caller)
    pdf = render_invoice_pdf(
        settings=_settings(db, property_id),
        invoice=inv.model_dump(mode="json"),
    )
    name = f"invoice-{inv.display_number or invoice_id}.pdf"
    return Response(
        content=pdf, media_type="application/pdf",
        # inline so Print opens the viewer; the viewer still offers Save.
        headers={"Content-Disposition": f'inline; filename="{name}"'},
    )


# --------------------------------------------------------------------------
# Draft, issue, cancel
# --------------------------------------------------------------------------
@invoice_router.post("/invoices", response_model=InvoiceOut, status_code=201)
def create_invoice(
    body: InvoiceCreate,
    caller: Caller = Depends(require_permission("payments", "create")),
    db: Session = Depends(get_session),
):
    """Open a draft over every charge on a folio that is not yet invoiced.

    A folio can carry more than one invoice — a long stay may be billed in
    stages, and a company may settle part of it — so this does not refuse when
    one already exists. It refuses only when there is nothing left to bill.
    """
    require_property_permission(db, caller, body.property_id,
                                "payments", "create")
    folio = db.execute(
        text("SELECT id, organization_id, property_id, currency "
             "FROM finance.folios WHERE id = :f"),
        {"f": body.folio_id},
    ).mappings().first()
    if folio is None:
        raise HTTPException(status_code=404, detail="Folio not found")
    if folio["property_id"] != body.property_id:
        raise HTTPException(
            status_code=403, detail="That folio belongs to another property")

    lines = _lines_for(db, None, body.folio_id)
    if not lines:
        raise HTTPException(
            status_code=409,
            detail="Every charge on this folio has already been invoiced.")

    sett = _settings(db, body.property_id)
    customer = {k: v for k, v in {
        "name": body.customer_name, "gstin": body.customer_gstin,
        "address_line": body.customer_address,
    }.items() if v}

    invoice_id = uuid.uuid4()
    db.execute(
        text(
            """
            INSERT INTO finance.invoices
                (id, organization_id, property_id, folio_id, fiscal_series,
                 status, customer_snapshot, notes, created_by)
            VALUES (:id, :org, :prop, :f, :series, 'draft',
                    CAST(:cust AS jsonb), :notes, :who)
            """
        ),
        {"id": invoice_id, "org": folio["organization_id"],
         "prop": body.property_id, "f": body.folio_id,
         "series": sett.get("fiscal_series") or "INV",
         "cust": json.dumps(customer), "notes": body.notes,
         "who": caller.user_id},
    )
    record_audit(
        db, action="invoice.drafted", entity_type="invoice",
        entity_id=str(invoice_id), organization_id=folio["organization_id"],
        property_id=body.property_id, actor_subject=caller.subject,
        after={"folio_id": str(body.folio_id), "lines": len(lines)},
    )
    return _build(db, _load(db, invoice_id, body.property_id), caller)


@invoice_router.post("/invoices/{invoice_id}/issue", response_model=InvoiceOut)
def issue_invoice(
    invoice_id: uuid.UUID,
    property_id: uuid.UUID,
    caller: Caller = Depends(require_permission("payments", "approve")),
    db: Session = Depends(get_session),
):
    """Freeze the draft, claim its lines and give it a number.

    The number is taken here rather than at draft, under a row lock, so an
    abandoned draft leaves no gap and two cashiers issuing at once cannot
    collide. Claiming the lines is what makes the charges un-invoiceable
    again — ``uq_invoice_line_entry`` refuses a second claim at the database.
    """
    assert_property_in_org(db, caller, property_id)
    inv = _load(db, invoice_id, property_id)
    if inv["status"] != "draft":
        raise HTTPException(
            status_code=409,
            detail=f"This invoice is {inv['status']} and cannot be issued again.")

    # When this folio is allowed to take a number, and whether it may yet.
    #
    # This is the whole of what `invoice_number_timing` governs. The number is
    # drawn from `invoice_settings.next_number` a few lines below and can never
    # be handed back: an invoice issued against a bill that then changes has to
    # be cancelled and reissued, and a fiscal series with cancellations and
    # gaps in it is the thing an audit asks about. So the setting is checked
    # here, at the one place a number leaves the series, rather than anywhere a
    # document happens to be drawn.
    _guard_numbering_timing(db, inv)

    built = _build(db, inv, caller)
    if not built.lines:
        raise HTTPException(
            status_code=409, detail="There is nothing on this invoice to issue.")

    series = inv["fiscal_series"] or "INV"
    row = db.execute(
        text("SELECT next_number FROM finance.invoice_settings "
             "WHERE property_id = :p FOR UPDATE"),
        {"p": property_id},
    ).first()
    if row is None:
        db.execute(
            text("INSERT INTO finance.invoice_settings "
                 "(property_id, organization_id, fiscal_series, next_number) "
                 "VALUES (:p, :org, :s, 1)"),
            {"p": property_id, "org": inv["organization_id"], "s": series},
        )
        number = 1
    else:
        number = int(row[0])
    db.execute(
        text("UPDATE finance.invoice_settings SET next_number = :n, "
             "updated_at = now(), version = version + 1 WHERE property_id = :p"),
        {"n": number + 1, "p": property_id},
    )

    for ln in built.lines:
        db.execute(
            text(
                """
                INSERT INTO finance.invoice_lines
                    (id, invoice_id, folio_entry_id, description_snapshot,
                     quantity, amounts_snapshot)
                VALUES (:id, :inv, :e, :d, :q, CAST(:amt AS jsonb))
                """
            ),
            {"id": uuid.uuid4(), "inv": invoice_id, "e": ln.entry_id,
             "d": ln.description, "q": ln.quantity,
             "amt": json.dumps({
                 "unit_price": str(ln.unit_price), "amount": str(ln.amount),
                 "taxes": [t.model_dump(mode="json") for t in ln.taxes]})},
        )

    snapshot = {
        "subtotal": str(built.subtotal), "tax_total": str(built.tax_total),
        "total": str(built.total), "currency": built.currency,
        "supplier": built.supplier.model_dump(mode="json"),
        "tax_lines": [t.model_dump(mode="json") for t in built.tax_lines],
        "tax_compliance": built.tax_compliance.model_dump(mode="json"),
    }
    db.execute(
        text("UPDATE finance.invoices SET status = 'issued', "
             "invoice_number = :num, fiscal_series = :s, issued_at = now(), "
             "issued_by = :who, totals_snapshot = CAST(:snap AS jsonb), "
             "updated_at = now() WHERE id = :id"),
        {"num": number, "s": series, "who": caller.user_id,
         "snap": json.dumps(snapshot), "id": invoice_id},
    )
    record_audit(
        db, action="invoice.issued", entity_type="invoice",
        entity_id=str(invoice_id), organization_id=inv["organization_id"],
        property_id=property_id, actor_subject=caller.subject,
        before={"status": "draft"},
        after={"number": f"{series}-{number}", "total": str(built.total),
               "lines": len(built.lines),
               "tax_compliant": built.tax_compliance.compliant},
    )
    return _build(db, _load(db, invoice_id, property_id), caller)


@invoice_router.post("/invoices/{invoice_id}/cancel", response_model=InvoiceOut)
def cancel_invoice(
    invoice_id: uuid.UUID,
    body: CancelIn,
    caller: Caller = Depends(require_permission("payments", "edit")),
    db: Session = Depends(get_session),
):
    """Abandon a draft.

    Only a draft. An issued invoice is a document that has left the building;
    it is corrected with a credit note, not deleted.
    """
    require_property_permission(db, caller, body.property_id,
                                "payments", "edit")
    inv = _load(db, invoice_id, body.property_id)
    if inv["status"] != "draft":
        raise HTTPException(
            status_code=409,
            detail="Only a draft can be cancelled. An issued invoice is "
                   "corrected with a credit note.")
    db.execute(
        text("UPDATE finance.invoices SET status = 'cancelled', "
             "cancelled_at = now(), cancelled_by = :who, cancel_reason = :r, "
             "updated_at = now() WHERE id = :id"),
        {"who": caller.user_id, "r": body.reason, "id": invoice_id},
    )
    record_audit(
        db, action="invoice.cancelled", entity_type="invoice",
        entity_id=str(invoice_id), organization_id=inv["organization_id"],
        property_id=body.property_id, actor_subject=caller.subject,
        reason=body.reason, before={"status": "draft"},
        after={"status": "cancelled"},
    )
    return _build(db, _load(db, invoice_id, body.property_id), caller)


# --------------------------------------------------------------------------
# Credit notes
# --------------------------------------------------------------------------
@invoice_router.post("/invoices/{invoice_id}/credit-notes",
                     response_model=InvoiceOut, status_code=201)
def create_credit_note(
    invoice_id: uuid.UUID,
    body: CreditNoteCreate,
    caller: Caller = Depends(require_permission("payments", "approve")),
    db: Session = Depends(get_session),
):
    """Credit part or all of an issued invoice, and post it to the folio.

    The note is created and issued in one step because a draft credit note
    that never posts is just a note nobody acts on. It posts a credit folio
    entry, so the guest's balance moves with the document instead of being
    reconciled by hand afterwards.
    """
    require_property_permission(db, caller, body.property_id,
                                "payments", "approve")
    inv = _load(db, invoice_id, body.property_id)
    if inv["status"] != "issued":
        raise HTTPException(
            status_code=409,
            detail="Only an issued invoice can be credited.")

    built = _build(db, inv, caller)
    remaining = built.total - built.credited
    if body.amount > remaining:
        raise HTTPException(
            status_code=422,
            detail=f"That is more than is left to credit. This invoice is "
                   f"{built.total:,.2f} with {built.credited:,.2f} already "
                   f"credited, leaving {remaining:,.2f}.")

    # Serialised on the property's settings row, the same lock the invoice
    # series uses, so two notes issued at once cannot take the same number.
    locked = db.execute(
        text("SELECT 1 FROM finance.invoice_settings WHERE property_id = :p "
             "FOR UPDATE"),
        {"p": body.property_id},
    ).first()
    if locked is None:
        db.execute(
            text("INSERT INTO finance.invoice_settings "
                 "(property_id, organization_id) VALUES (:p, :org) "
                 "ON CONFLICT (property_id) DO NOTHING"),
            {"p": body.property_id, "org": inv["organization_id"]},
        )
    number = db.execute(
        text("SELECT COALESCE(max(number), 0) + 1 FROM finance.credit_notes "
             "WHERE property_id = :p"),
        {"p": body.property_id},
    ).scalar_one()

    business_date = db.execute(
        text("SELECT COALESCE(max(business_date), CURRENT_DATE) "
             "FROM finance.folio_entries WHERE folio_id = :f"),
        {"f": inv["folio_id"]},
    ).scalar_one()

    entry_id = uuid.uuid4()
    note_id = uuid.uuid4()
    db.execute(
        text(
            """
            INSERT INTO finance.folio_entries
                (id, organization_id, property_id, folio_id, entry_type, amount,
                 currency, business_date, source_type, source_id, source_line_key)
            VALUES (:id, :org, :prop, :f, 'credit', :amt, :cur, :bd,
                    'credit_note', :src, :slk)
            """
        ),
        {"id": entry_id, "org": inv["organization_id"], "prop": body.property_id,
         "f": inv["folio_id"], "amt": body.amount, "cur": built.currency,
         "bd": business_date, "src": str(note_id),
         "slk": f"credit_note:{note_id}"},
    )
    db.execute(
        text(
            """
            INSERT INTO finance.credit_notes
                (id, invoice_id, number, reason, amount, status, issued_at,
                 organization_id, property_id, posted_entry_id, created_by,
                 issued_by)
            VALUES (:id, :inv, :num, :r, :amt, 'issued', now(), :org, :prop,
                    :entry, :who, :who)
            """
        ),
        {"id": note_id, "inv": invoice_id, "num": number, "r": body.reason,
         "amt": body.amount, "org": inv["organization_id"],
         "prop": body.property_id, "entry": entry_id, "who": caller.user_id},
    )
    balance = db.execute(
        text("SELECT COALESCE(sum(CASE WHEN entry_type = 'debit' THEN amount "
             "ELSE -amount END), 0) FROM finance.folio_entries WHERE folio_id = :f"),
        {"f": inv["folio_id"]},
    ).scalar_one()
    record_audit(
        db, action="invoice.credit_note.issued", entity_type="invoice",
        entity_id=str(invoice_id), organization_id=inv["organization_id"],
        property_id=body.property_id, actor_subject=caller.subject,
        reason=body.reason,
        after={"credit_note": f"CN-{date.today().year}-{int(number):04d}",
               "amount": str(body.amount),
               "entry_id": str(entry_id), "folio_balance_after": str(balance)},
    )
    return _build(db, _load(db, invoice_id, body.property_id), caller)


# --------------------------------------------------------------------------
# Guest registration card
#
# Not a financial document, but it lives here because it carries the same
# letterhead as the folio and the tax invoice, and that letterhead is read
# from invoice_settings. Duplicating the stationery in another service is how
# one tenant's legal record ends up with another tenant's name at the top.
# --------------------------------------------------------------------------
_ID_LABELS = {
    "aadhaar": "Aadhaar", "passport": "Passport", "driving_licence": "Driving Licence",
    "driving_license": "Driving Licence", "voter_id": "Voter ID",
    "pan": "PAN", "other": "Other",
}

_GRC_SQL = """
    SELECT r.number       AS reservation_number,
           ru.arrival_date, ru.departure_date,
           (ru.departure_date - ru.arrival_date) AS nights,
           ru.adults, ru.children,
           ru.vehicle_number, ru.purpose_of_visit,
           rt.name        AS room_type,
           rm.code        AS room_code,
           g.full_name, g.email, g.phone, g.nationality,
           g.address_line, g.city, g.state, g.postal_code, g.country,
           g.id_type, g.id_number, g.id_verified_at,
           g.date_of_birth, g.occupation,
           (SELECT count(*) FROM engagement.guest_documents gd
             WHERE gd.guest_id = g.id) AS document_count
      FROM booking.reservation_units ru
      JOIN booking.reservations r   ON r.id  = ru.reservation_id
      LEFT JOIN property.room_types rt ON rt.id = ru.room_type_id
      LEFT JOIN property.rooms rm      ON rm.id = ru.assigned_room_id
      LEFT JOIN engagement.guests g    ON g.id  = r.primary_guest_id
     WHERE ru.id = :unit AND ru.property_id = :prop
"""


@invoice_router.get("/reservation-units/{unit_id}/registration-card")
def registration_card(
    unit_id: uuid.UUID,
    property_id: uuid.UUID,
    caller: Caller = Depends(require_permission("front_desk", "view")),
    db: Session = Depends(get_session),
):
    """The guest registration card, as a printable A4 form.

    Rendered fresh each time rather than snapshotted: a card reprinted after
    the guest corrects their address should show the corrected address, and
    the signed paper is the record of what was agreed, not this file.

    Behind ``front_desk`` rather than ``payments`` — it is an arrivals
    document, and it carries an unmasked identity number, so it is not
    something every role that can read a folio should be able to pull.
    """
    assert_property_in_org(db, caller, property_id)

    ctx = db.execute(text(_GRC_SQL),
                     {"unit": unit_id, "prop": property_id}).mappings().first()
    if ctx is None:
        raise HTTPException(status_code=404, detail="Reservation not found")

    data = dict(ctx)
    data["id_type_label"] = _ID_LABELS.get(
        (data.get("id_type") or "").lower(), data.get("id_type"))
    count = int(data.pop("document_count", 0) or 0)
    data["documents_label"] = (
        f"{count} on file" if count else None)
    # Nationality is free text, so this is a best guess and is worded as a
    # reminder rather than a determination: the desk decides, not the string.
    nat = (data.get("nationality") or "").strip().lower()
    data["foreign_national"] = bool(nat) and nat not in {"india", "indian", "in"}

    pdf = render_registration_card(settings=_settings(db, property_id),
                                   context=data)
    name = f"registration-card-{data['reservation_number'] or unit_id}.pdf"
    return Response(
        content=pdf, media_type="application/pdf",
        headers={"Content-Disposition": f'inline; filename="{name}"'},
    )


@invoice_router.get("/payments/{payment_id}/receipt.pdf")
def payment_receipt_pdf(
    payment_id: uuid.UUID,
    property_id: uuid.UUID,
    caller: Caller = Depends(require_permission("payments", "view")),
    db: Session = Depends(get_session),
):
    """Proof that one payment was made.

    Separate from the folio on purpose. The folio is the bill for the stay and
    cannot stand in for this: a guest paying a deposit in October for a stay in
    December has nothing to be handed, because the folio at that moment lists
    things not yet charged.

    Read at request time like every other document here, so a receipt reprinted
    after the payment was reversed says so rather than quietly standing.
    """
    assert_property_in_org(db, caller, property_id)

    pay = db.execute(
        text(
            """
            SELECT p.id, p.amount, p.currency, p.method, p.reference,
                   p.receipt_no,
                   p.provider_transaction_id, p.status, p.received_at,
                   f.folio_no, f.reservation_id,
                   EXISTS (SELECT 1 FROM finance.payment_reversals pr
                            WHERE pr.payment_id = p.id) AS voided
              FROM finance.payments p
              LEFT JOIN finance.payment_allocations pa ON pa.payment_id = p.id
              LEFT JOIN finance.folios f ON f.id = pa.folio_id
             WHERE p.id = :p AND p.property_id = :prop
             LIMIT 1
            """
        ),
        {"p": payment_id, "prop": property_id},
    ).mappings().first()
    if pay is None:
        raise HTTPException(status_code=404, detail="Payment not found")

    ctx = {}
    if pay["reservation_id"]:
        row = db.execute(
            text(
                """
                SELECT r.number AS reservation_number,
                       g.full_name AS guest_name,
                       rm.code AS room_code, rt.name AS room_type
                  FROM booking.reservations r
                  LEFT JOIN engagement.guests g ON g.id = r.primary_guest_id
                  LEFT JOIN booking.reservation_units ru
                         ON ru.reservation_id = r.id
                  LEFT JOIN property.rooms rm ON rm.id = ru.assigned_room_id
                  LEFT JOIN property.room_types rt ON rt.id = ru.room_type_id
                 WHERE r.id = :r
                 ORDER BY ru.line_index
                 LIMIT 1
                """
            ),
            {"r": pay["reservation_id"]},
        ).mappings().first()
        ctx = dict(row) if row else {}

    # One definition, in receipt_pdf. See the note there for why it is not
    # drawn from the invoice fiscal series.
    receipt_no = receipt_number(pay["id"], pay["receipt_no"])

    pdf = render_receipt_pdf(
        settings=_settings(db, property_id),
        payment={
            **dict(pay),
            "receipt_no": receipt_no,
            "method_label": payment_methods.LABELS.get(
                pay["method"], pay["method"]),
            # A card or UPI payment with no typed reference still has the
            # gateway's, which is the only string that finds it later.
            "reference": pay["reference"] or pay["provider_transaction_id"],
        },
        context=ctx,
    )
    return Response(
        content=pdf, media_type="application/pdf",
        headers={"Content-Disposition":
                 f'inline; filename="{receipt_no}.pdf"'},
    )


class SendReceiptIn(BaseModel):
    #: Where to send it. Defaults to the guest's address on the booking; a desk
    #: may type another, because the person paying is not always the person
    #: staying.
    to: str | None = Field(default=None, max_length=254)


@invoice_router.post("/payments/{payment_id}/receipt/email")
def email_payment_receipt(
    payment_id: uuid.UUID,
    property_id: uuid.UUID,
    body: SendReceiptIn,
    caller: Caller = Depends(require_permission("payments", "view")),
    db: Session = Depends(get_session),
):
    """Mail the receipt for one payment, with the PDF attached.

    The document is rendered here rather than referenced by a link: a receipt
    behind a login is a receipt the guest cannot open, and a link that outlives
    the stay is a small data leak waiting to happen.
    """
    assert_property_in_org(db, caller, property_id)

    pdf_response = payment_receipt_pdf(
        payment_id=payment_id, property_id=property_id, caller=caller, db=db)
    pdf: bytes = pdf_response.body

    row = db.execute(
        text(
            """
            SELECT p.amount, p.currency, p.receipt_no,
                   g.full_name AS guest_name,
                   g.email AS guest_email, r.number AS reservation_number
              FROM finance.payments p
              LEFT JOIN finance.payment_allocations pa ON pa.payment_id = p.id
              LEFT JOIN finance.folios f ON f.id = pa.folio_id
              LEFT JOIN booking.reservations r ON r.id = f.reservation_id
              LEFT JOIN engagement.guests g ON g.id = r.primary_guest_id
             WHERE p.id = :p AND p.property_id = :prop
             LIMIT 1
            """
        ),
        {"p": payment_id, "prop": property_id},
    ).mappings().first()
    if row is None:
        raise HTTPException(status_code=404, detail="Payment not found")

    to = (body.to or row["guest_email"] or "").strip()
    if not to:
        raise HTTPException(
            422,
            "No address to send to — this booking has no guest email, so type "
            "one.")

    sett = _settings(db, property_id)
    house = sett.get("legal_name") or "the property"
    amount = f"{row['currency'] or 'INR'} {Decimal(str(row['amount'] or 0)):,.2f}"
    receipt_no = receipt_number(payment_id, row["receipt_no"])

    try:
        send_mail(
            settings.mail_config,
            to=to,
            subject=f"Payment receipt {receipt_no} — {house}",
            text="\n".join([
                f"Dear {row['guest_name'] or 'Guest'},",
                "",
                f"Thank you. We have received {amount} against reservation "
                f"{row['reservation_number'] or ''}.",
                "",
                f"Your receipt ({receipt_no}) is attached.",
                "",
                house,
                "",
            ]),
            attachments=[(f"{receipt_no}.pdf", "application/pdf", pdf)],
        )
    except MailNotConfigured as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:                      # noqa: BLE001
        # The mail server's own refusal, in words the desk can act on -- a
        # bounce reported as "500 Internal Server Error" tells them nothing.
        raise HTTPException(
            status_code=502,
            detail=f"The receipt could not be sent: {exc}") from exc

    record_audit(
        db, actor_subject=caller.subject, action="payment.receipt.emailed",
        entity_type="payment", entity_id=str(payment_id),
        property_id=property_id, after={"to": to, "receipt_no": receipt_no},
    )
    return {"sent_to": to, "receipt_no": receipt_no}
