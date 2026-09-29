"""Pydantic request/response models for the finance service."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, Field, field_validator, model_validator

from chirala_common import no_show, payment_methods


class FolioCreate(BaseModel):
    property_id: uuid.UUID
    reservation_id: uuid.UUID | None = None
    type: str = "guest"
    #: Omit it and the folio is opened in the property's own currency.
    currency: str | None = None


class FolioOut(BaseModel):
    id: uuid.UUID
    type: str
    currency: str
    status: str


class ChargeCreate(BaseModel):
    property_id: uuid.UUID
    folio_id: uuid.UUID
    amount: Decimal = Field(gt=0)
    #: Omit it and the server stamps the day the property is actually trading.
    #: Sent, it is honoured -- a desk may legitimately back-date a posting.
    #: It is optional because the clients that filled it in were filling it in
    #: with the BROWSER's date in UTC, which is the calendar's day rather than
    #: the one the night audit has reached, and after 18:30 in India is already
    #: tomorrow. Money stamped that way lands on a date the drawer was never
    #: counted against, and can never be voided -- a void is only for the day
    #: the payment was taken.
    business_date: date | None = None
    source_type: str = Field(max_length=40)
    source_line_key: str = Field(max_length=200)
    charge_code_id: uuid.UUID | None = None
    source_id: str | None = None
    #: Omit it: the folio (or payment) already says what currency it keeps,
    #: and the ledger takes it from there. A stated currency that disagrees is
    #: refused. This used to default to "INR", which silently relabelled every
    #: posting on a non-rupee folio whose client did not think to send it.
    currency: str | None = None
    #: What the desk typed. ``amount`` stays the net the guest owes; these say
    #: how it was arrived at, which is what makes a queried line answerable.
    note: str | None = Field(default=None, max_length=300)
    quantity: Decimal | None = Field(default=None, gt=0)
    unit_amount: Decimal | None = Field(default=None, ge=0)
    discount_amount: Decimal | None = Field(default=None, ge=0)


class EntryOut(BaseModel):
    entry_id: uuid.UUID
    created: bool


class BalanceOut(BaseModel):
    folio_id: uuid.UUID
    balance: Decimal


class FolioEntryOut(BaseModel):
    id: uuid.UUID
    business_date: date
    entry_type: str
    amount: Decimal
    currency: str
    source_type: str
    description: str
    department: str


class TaxLineOut(BaseModel):
    tax_code: str
    rate_snapshot: Decimal
    taxable_amount: Decimal
    tax_amount: Decimal


class FolioSummaryOut(BaseModel):
    folio_id: uuid.UUID
    subtotal: Decimal
    taxes: Decimal
    grand_total: Decimal
    advance_paid: Decimal
    balance_due: Decimal
    currency: str = "INR"
    #: Per tax code, from what was actually posted — empty when nothing on this
    #: folio was taxed.
    tax_lines: list[TaxLineOut] = []


class AllocationIn(BaseModel):
    folio_id: uuid.UUID
    amount: Decimal = Field(gt=0)


class PaymentCreate(BaseModel):
    property_id: uuid.UUID
    #: Validated and normalised, not merely length-capped. This endpoint used
    #: to accept any string up to 30 characters, which is how "UPI" and "upi"
    #: both ended up in finance.payments as separate methods.
    method: str = Field(max_length=30)
    #: Omit it and the server stamps the day the property is actually trading.
    #: Sent, it is honoured -- a desk may legitimately back-date a posting.
    #: It is optional because the clients that filled it in were filling it in
    #: with the BROWSER's date in UTC, which is the calendar's day rather than
    #: the one the night audit has reached, and after 18:30 in India is already
    #: tomorrow. Money stamped that way lands on a date the drawer was never
    #: counted against, and can never be voided -- a void is only for the day
    #: the payment was taken.
    business_date: date | None = None
    allocations: list[AllocationIn] = Field(min_length=1)
    #: Omit it: the folio (or payment) already says what currency it keeps,
    #: and the ledger takes it from there. A stated currency that disagrees is
    #: refused. This used to default to "INR", which silently relabelled every
    #: posting on a non-rupee folio whose client did not think to send it.
    currency: str | None = None
    #: What the guest can quote back if the payment is ever questioned.
    #:
    #: The ledger has always stored one; this endpoint had no field for it, so
    #: every payment taken through here — the folio screen, the reservation's
    #: own Add payment — recorded none. The cashiering route demanded one for
    #: a card payment and this one would not accept it: the same money through
    #: two doors under two rules.
    reference: str | None = Field(default=None, max_length=160)
    #: What the payment was for, in the desk's own words -- "night drinks",
    #: "deposit refund in cash". Distinct from ``reference``, which is the
    #: string the *guest* quotes back; this is the string the *hotel* reads.
    #: It becomes the folio line's description, so a folio showing three
    #: payments on one day says what each of them was.
    note: str | None = Field(default=None, max_length=300)

    @model_validator(mode="after")
    def _reference_when_needed(self) -> "PaymentCreate":
        if (self.method in payment_methods.NEEDS_REFERENCE
                and not (self.reference or "").strip()):
            raise ValueError(
                f"A {payment_methods.LABELS.get(self.method, self.method)} "
                f"payment needs a reference — it is what the guest quotes back "
                f"if the payment has to be traced.")
        return self

    @field_validator("method")
    @classmethod
    def _known_method(cls, v: str) -> str:
        if not payment_methods.is_valid(v):
            raise ValueError(
                f"Unknown payment method {v!r}. Known methods: "
                + ", ".join(payment_methods.METHODS)
            )
        # Stored canonically whatever spelling arrived, so one method is one
        # value in every report from here on.
        return payment_methods.normalise(v)


class PaymentOut(BaseModel):
    payment_id: uuid.UUID
    credit_entry_ids: list[uuid.UUID]


class RefundCreate(BaseModel):
    property_id: uuid.UUID
    payment_id: uuid.UUID
    amount: Decimal = Field(gt=0)
    business_date: date
    reason: str | None = None
    #: Omit it: the folio (or payment) already says what currency it keeps,
    #: and the ledger takes it from there. A stated currency that disagrees is
    #: refused. This used to default to "INR", which silently relabelled every
    #: posting on a non-rupee folio whose client did not think to send it.
    currency: str | None = None


class RefundOut(BaseModel):
    refund_id: uuid.UUID
    debit_entry_ids: list[uuid.UUID]


class NightlyChargeIn(BaseModel):
    folio_id: uuid.UUID
    amount: Decimal = Field(gt=0)
    charge_code_id: uuid.UUID | None = None


class NightAuditRun(BaseModel):
    property_id: uuid.UUID
    business_date: date
    #: Close the day even though a till is still open. A deliberate override,
    #: recorded with the run; never the default.
    allow_open_shifts: bool = False
    nightly_charges: list[NightlyChargeIn] = Field(default_factory=list)


class NightAuditPending(BaseModel):
    """A booking the audit found and a person has to deal with."""

    unit_id: uuid.UUID
    reservation_number: str | None = None
    room: str | None = None
    guest: str | None = None
    arrival_date: date
    departure_date: date


class NightAuditChargeLine(BaseModel):
    """One room, one night — what the audit is about to bill for it."""

    unit_id: uuid.UUID
    reservation_number: str | None = None
    room: str | None = None
    guest: str | None = None
    room_type: str | None = None
    amount: Decimal


class NightAuditOpenShift(BaseModel):
    """A till still open as the day closes."""

    shift_id: uuid.UUID
    cashier: str | None = None
    opened_at: datetime | None = None
    opening_float: Decimal


class NightAuditOut(BaseModel):
    run_id: uuid.UUID
    business_date: date
    charges_posted: int
    next_business_date: date
    steps: list[str]
    # What the night actually came to, so the auditor sees the money without
    # opening a second screen.
    rooms_charged: int = 0
    amount_charged: Decimal = Decimal("0")
    no_shows: list[NightAuditPending] = Field(default_factory=list)
    overstays: list[NightAuditPending] = Field(default_factory=list)
    horizon_days_added: int = 0
    open_shifts: int = 0
    warnings: list[str] = Field(default_factory=list)


class NightAuditPreview(BaseModel):
    """What running the audit would do, before it is done."""

    property_id: uuid.UUID
    business_date: date
    #: The date the business moves to when this one closes. Sent rather than
    #: derived in the browser: "the next day" looks like one line of date
    #: arithmetic and is not, once a timezone is involved -- adding a day and
    #: formatting through UTC landed back on the same date in IST.
    next_business_date: date
    already_closed: bool
    #: Whether this day may be closed at all, and why not. A day in the future
    #: cannot: sealing it would lock out the charges it has yet to earn.
    can_close: bool = True
    blocked_reason: str | None = None
    #: Closing is possible but needs an explicit override, and why.
    requires_override: bool = False
    override_reason: str | None = None
    rooms_to_charge: int
    amount_to_charge: Decimal
    #: The lines behind that total, so "review the room charges" is something
    #: an auditor can actually do rather than a box that ticks itself.
    charge_lines: list[NightAuditChargeLine] = Field(default_factory=list)
    no_shows: list[NightAuditPending] = Field(default_factory=list)
    overstays: list[NightAuditPending] = Field(default_factory=list)
    open_shifts: int = 0
    #: Which tills, not just how many.
    open_shift_rows: list[NightAuditOpenShift] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    # Whether anything will close this day on its own, and when. The screen
    # says "auto audit enabled" -- it must be reading the actual setting, not
    # asserting it, or it reassures somebody that a job is running when it is
    # switched off.
    auto_audit_enabled: bool = True
    audit_hour: int = 3


class SalesLine(BaseModel):
    """One revenue category for the day, charge and tax kept apart."""

    category: str
    charges: Decimal
    tax: Decimal
    total: Decimal


class ReceiptLine(BaseModel):
    label: str
    amount: Decimal
    count: int = 0


class DayBalance(BaseModel):
    """What the day earned, what it took, and what is still owed.

    The gap between the first two is the number a night auditor looks for and
    the reference report never printed.
    """

    charged: Decimal
    collected: Decimal
    outstanding: Decimal


class PaxLine(BaseModel):
    """Rooms and heads under one heading."""

    label: str
    rooms: int
    adults: int
    children: int


class ReceiptDetailLine(BaseModel):
    """One receipt, as it would appear on a cashier's tape."""

    reference: str | None = None
    room: str | None = None
    method: str
    amount: Decimal
    cashier: str | None = None
    received_at: datetime | None = None


class DepartureLine(BaseModel):
    """A guest who left on this business date."""

    reservation_number: str | None = None
    room: str | None = None
    guest: str | None = None
    arrival_date: date
    departure_date: date
    nights: int
    left_at: datetime | None = None
    #: What their folio came to, and what is left on it.
    charged: Decimal = Decimal("0")
    paid: Decimal = Decimal("0")
    balance: Decimal = Decimal("0")


class NightAuditReport(BaseModel):
    """The manager's report for one closed day."""

    property_id: uuid.UUID
    business_date: date
    currency: str = "INR"
    sales: list[SalesLine] = Field(default_factory=list)
    sales_total: SalesLine | None = None
    receipts_by_method: list[ReceiptLine] = Field(default_factory=list)
    receipts_by_user: list[ReceiptLine] = Field(default_factory=list)
    receipts_total: Decimal = Decimal("0")
    receipts: list[ReceiptDetailLine] = Field(default_factory=list)
    balance: DayBalance
    #: Rooms and heads as the day closed. None for a day whose run predates
    #: the snapshot, which is honest -- it was never recorded.
    occupancy: dict | None = None
    #: Who left, and whether they left anything owing.
    departures: list[DepartureLine] = Field(default_factory=list)
    #: Guest movements on the day: who arrived, who left, who stayed.
    pax_status: list[PaxLine] = Field(default_factory=list)
    #: The house that night, split by what each room was sold on.
    pax_by_rate_type: list[PaxLine] = Field(default_factory=list)


class NightAuditHistoryOut(BaseModel):
    """A page of runs, and how many there are in total.

    ``total`` is the count matching the filter, not the count returned. A
    history page that shows the newest N and says nothing about the rest looks
    complete while hiding exactly the night somebody came here to find.
    """

    rows: list["NightAuditRunOut"]
    total: int
    limit: int
    offset: int


class NightAuditSettings(BaseModel):
    """When this property closes its books."""

    property_id: uuid.UUID
    #: None means "follow the deployment default", which is a real answer and
    #: not a missing one -- it keeps the default in one place.
    audit_hour: int | None = None
    #: What the default currently is, so a screen can say what None means
    #: without hardcoding a number that could drift out of step.
    default_hour: int
    #: What an unattended no-show costs. "none" means the audit records the
    #: no-show and releases the room but charges nothing -- which is what a
    #: property that has never chosen gets, because silence is not consent to
    #: bill a guest.
    no_show_penalty: str = "none"
    #: The bases a property may choose from, served rather than written into
    #: the screen so the list, the audit and the No-Show screen cannot drift.
    no_show_options: list[dict] = []
    #: The minute past the hour this property actually fires, derived from its
    #: id so a thousand tenants on the default do not all fire at once.
    scheduled_minute: int
    enabled: bool


class NightAuditSettingsIn(BaseModel):
    audit_hour: int | None = Field(default=None, ge=0, le=23)
    no_show_penalty: str | None = None

    @field_validator("no_show_penalty")
    @classmethod
    def _known_basis(cls, v: str | None) -> str | None:
        if v is None:
            return None
        if not no_show.is_valid(v):
            raise ValueError(
                f"Unknown no-show basis {v!r}. Known: "
                + ", ".join(no_show.BASES))
        return no_show.normalise(v)


class NightAuditStepOut(BaseModel):
    step_code: str
    status: str
    error: str | None = None
    detail: dict | None = None


class NightAuditRunOut(BaseModel):
    run_id: uuid.UUID
    business_date: date
    run_number: int
    status: str
    started_at: datetime | None = None
    completed_at: datetime | None = None
    #: Who ran it. None means the schedule did, unattended.
    run_by_name: str | None = None
    steps: list[NightAuditStepOut] = Field(default_factory=list)
