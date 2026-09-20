"""Back office reports.

The catalog behind ``/reports``: every report in the back-office design, and
the query behind each one this system can answer today.

**One engine, many reports.** A report here is a definition -- its columns,
metric tiles, filter dropdowns, totals and basis note -- plus one function that
returns rows. The screen draws whatever it is sent, so adding a report is a
change to this file and nothing else. Metric tiles and totals are computed in
the browser over the rows on screen, which is what lets a column filter narrow
the tiles as well as the table; the definitions travel with the rows so the two
cannot drift.

**Read from the rows everything else reads.** ``finance.folio_entries``,
``payments``, ``refunds``, ``cashier_shifts``, the room calendar. There is no
reporting store and no nightly rollup, so no report can disagree with the
folio, the cashiering screen or the night audit about the same money.

**Not built is said, not faked.** Reports whose data has no home yet -- expense
vouchers, work orders, owner statements -- are listed with the reason and
cannot be opened. So are the ones whose tables exist but that are scheduled
for the next phase. A catalog that quietly omitted them would read as though
the property had no city ledger to ask about.

Business dates, not calendar dates, wherever money is dated: a payment belongs
to the business date it was posted to the folio on, which after an early night
audit is already tomorrow. Where something has no posting yet, the property's
own local date stands in -- never the server's.
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any

from chirala_common.authz import Caller, assert_property_in_org
from chirala_common.routing import TransactionalRoute
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session

from .database import get_session
from .night_audit import local_today
from .adjustment_routes import KIND_LABELS, REASON_LABELS
from .expense_routes import CATEGORIES as _EXPENSE_CATEGORIES
from .expense_routes import METHODS as _EXPENSE_METHODS
from .expense_routes import STATUS_LABELS as _EXPENSE_STATUS
from .report_routes import (
    _DEPOSIT_SOURCES, _REFUND_OF_DEPOSIT, PARTICULARS, require_permission,
)

backoffice_router = APIRouter(
    prefix="/reports/backoffice", tags=["reports"],
    route_class=TransactionalRoute,
)

#: Rows one report will send. Past this the response says it was cut short
#: rather than silently dropping the tail.
MAX_ROWS = 5000

#: The widest window a report will run over. A year of folio entries is
#: already more than anybody reads on a screen.
MAX_DAYS = 366


# ==========================================================================
# Definitions
# ==========================================================================
@dataclass(frozen=True)
class Col:
    key: str
    label: str
    #: text | status | date | datetime | money | number | percent
    kind: str = "text"


@dataclass(frozen=True)
class Metric:
    """A tile. Columns are named by key and resolved to indexes on the way out,
    so reordering columns cannot point a tile at the wrong one."""
    label: str
    #: count | sum | sumcols | difference | unique | max | nonzero | ratio
    op: str
    col: str | tuple[str, ...] | None = None
    fmt: str = "money"
    den: str | None = None
    #: Narrow the rows first: only those whose ``where`` column equals
    #: ``value`` (or, with ``negate``, does not).
    where: str | None = None
    value: str | None = None
    negate: bool = False
    sub: str = ""


@dataclass(frozen=True)
class Param:
    key: str
    label: str
    kind: str = "number"
    default: Any = None


@dataclass(frozen=True)
class Ctx:
    db: Session
    p: uuid.UUID
    a: date
    b: date
    tz: str
    params: dict[str, Any]

    def rows(self, sql: str, **extra: Any) -> list[dict[str, Any]]:
        binds = {"p": self.p, "a": self.a, "b": self.b, "tz": self.tz,
                 "lim": MAX_ROWS + 1, **extra}
        return [dict(r) for r in self.db.execute(text(sql), binds).mappings()]


Runner = Callable[[Ctx], list[dict[str, Any]]]


@dataclass(frozen=True)
class Report:
    number: int
    slug: str
    title: str
    category: str
    description: str
    basis: str = "Business date"
    #: range | single | none
    dates: str = "range"
    #: Width of the default window, ending on the open business date.
    default_days: int = 1
    columns: tuple[Col, ...] = ()
    metrics: tuple[Metric, ...] = ()
    filters: tuple[str, ...] = ()
    totals: tuple[str, ...] = ()
    params: tuple[Param, ...] = ()
    note: str = ""
    empty_text: str = "Nothing was recorded for these dates."
    run: Runner | None = None
    #: Row key holding the reservation behind the row, for the drawer's links.
    link: str | None = None
    planned: str | None = None
    unavailable_reason: str | None = None
    href: str | None = None
    #: Default window starts on the business date and runs forward, for
    #: reports that plan ahead rather than look back.
    forward: bool = False

    @property
    def available(self) -> bool:
        return self.run is not None or self.href is not None


CATALOG: list[Report] = []
REPORTS: dict[str, Report] = {}


def _register(r: Report) -> None:
    CATALOG.append(r)
    REPORTS[r.slug] = r


def report(**kw: Any) -> Callable[[Runner], Runner]:
    def wrap(fn: Runner) -> Runner:
        _register(Report(run=fn, **kw))
        return fn
    return wrap


# ==========================================================================
# Vocabulary shared by the queries
# ==========================================================================
_S = "COALESCE(e.source_type, '')"

#: Money moving rather than being earned. Excluded from every revenue figure.
_MOVEMENT_CREDIT = ("payment", "deposit", "security_deposit")
_MOVEMENT_DEBIT = ("refund", "deposit_refund")


def _in(values: tuple[str, ...]) -> str:
    return "(" + ", ".join(f"'{v}'" for v in values) + ")"


#: A posting that earns or reduces revenue: every charge, and every credit
#: that is not a payment or deposit -- adjustments and credit notes.
_REVENUE = f"""(
    (e.entry_type = 'debit' AND {_S} NOT IN {_in(_MOVEMENT_DEBIT)})
    OR (e.entry_type = 'credit' AND {_S} NOT IN {_in(_MOVEMENT_CREDIT)})
)"""

#: Guest, reservation and first room line behind a folio ``f``.
_GUEST_JOINS = """
    LEFT JOIN booking.reservations r ON r.id = f.reservation_id
    LEFT JOIN engagement.guests g ON g.id = r.primary_guest_id
    LEFT JOIN LATERAL (
        SELECT ru.arrival_date, ru.departure_date, ru.status,
               ru.room_type_id, ru.rate_plan_id, rm.code AS room_code
        FROM booking.reservation_units ru
        LEFT JOIN property.rooms rm ON rm.id = ru.assigned_room_id
        WHERE ru.reservation_id = r.id
        ORDER BY ru.line_index
        LIMIT 1
    ) u ON TRUE
"""

#: Department per posting source. The folio adjustment screen keeps its own
#: map (``adjustment_routes.DEPARTMENTS``) which files tax under the charge it
#: is levied on; revenue reports show tax as a line of its own instead, so the
#: two are deliberately separate.
_DEPARTMENT_OF = {
    "room_night": "Rooms", "room_stay": "Rooms", "room_upgrade": "Rooms",
    "room_move": "Rooms",
    "restaurant": "F&B", "minibar": "F&B", "breakfast": "F&B",
    "spa": "Spa", "activity": "Activities", "laundry": "Laundry",
    "transport": "Transport",
    "cancellation_fee": "Reservations", "no_show_penalty": "Reservations",
    "reservation_change": "Reservations",
    "adjustment": "Adjustments", "credit_note": "Adjustments",
}

_STAY = {
    "reserved": "Reserved", "confirmed": "Confirmed", "tentative": "Tentative",
    "checked_in": "In-house", "checked_out": "Checked out",
    "cancelled": "Cancelled", "no_show": "No show",
}

_CHANNEL = {"booking_engine": "Booking engine", "front_desk": "Front desk"}


def _department(source_type: str | None) -> str:
    st = source_type or ""
    if st.endswith("_tax"):
        return "Taxes"
    return _DEPARTMENT_OF.get(st, "Other")


def _describe(source_type: str | None) -> str:
    st = source_type or "other"
    if st in PARTICULARS:
        return PARTICULARS[st]
    if st.endswith("_tax"):
        base = st[:-4]
        return f"Tax on {PARTICULARS.get(base, base.replace('_', ' ')).lower()}"
    if st == "other":
        return "Other charge"
    return st.replace("_", " ").capitalize()


def _label(v: str | None, mapping: dict[str, str] | None = None) -> str | None:
    if not v:
        return None
    return (mapping or {}).get(v) or v.replace("_", " ").capitalize()


def _method(m: str | None) -> str:
    """Payment methods arrive as ``card``, ``Cash``, ``UPI`` and ``upi`` alike;
    grouping on the raw value would split one method into two rows."""
    if not m:
        return "Unknown"
    low = m.strip().lower()
    return "UPI" if low == "upi" else low.replace("_", " ").capitalize()


def _ref(reference: str | None, provider_ref: str | None,
         id_: Any, prefix: str) -> str:
    return reference or provider_ref or f"{prefix}-{str(id_)[:8].upper()}"


def _signed(e: dict[str, Any]) -> Decimal:
    return e["amount"] if e["entry_type"] == "debit" else -e["amount"]


def _div(n: Decimal | int | float, d: Decimal | int | float) -> float | None:
    return float(n) / float(d) if d else None


def _inr(v: Decimal | float | int | None) -> str:
    """Rupees with Indian digit grouping, for the one report whose value
    column mixes money with counts and so cannot be formatted by the screen."""
    q = Decimal(str(v or 0)).quantize(Decimal("0.01"))
    whole, frac = f"{abs(q):.2f}".split(".")
    if len(whole) > 3:
        head, tail = whole[:-3], whole[-3:]
        groups: list[str] = []
        while len(head) > 2:
            groups.insert(0, head[-2:])
            head = head[:-2]
        if head:
            groups.insert(0, head)
        whole = ",".join([*groups, tail])
    return f"{'-' if q < 0 else ''}₹{whole}.{frac}"


# ==========================================================================
# Shared queries
# ==========================================================================
#: The first folio a payment was allocated to, or the one its online intent
#: named. Expects ``p`` (payment).
_PAYMENT_FOLIO_JOINS = """
    LEFT JOIN finance.payment_intents pi ON pi.id = p.intent_id
    LEFT JOIN LATERAL (
        SELECT pa.folio_id FROM finance.payment_allocations pa
        WHERE pa.payment_id = p.id
        ORDER BY pa.created_at
        LIMIT 1
    ) fa ON TRUE
    LEFT JOIN finance.folios f ON f.id = COALESCE(fa.folio_id, pi.folio_id)
    LEFT JOIN booking.reservations r
           ON r.id = COALESCE(f.reservation_id, pi.reservation_id)
    LEFT JOIN engagement.guests g ON g.id = r.primary_guest_id
"""

_PAYMENTS_SQL = f"""
    SELECT * FROM (
        SELECT p.id, p.received_at, p.method, p.source, p.amount,
               p.reference, p.provider_transaction_id, p.intent_id,
               COALESCE(
                   (SELECT min(pe.business_date) FROM finance.folio_entries pe
                     WHERE pe.source_type = 'payment'
                       AND pe.source_id = p.id::text),
                   CAST(timezone(CAST(:tz AS text), p.received_at) AS date)
               ) AS business_date,
               cu.display_name AS cashier,
               f.folio_no, r.id AS reservation_id, r.number AS reservation,
               g.full_name AS guest,
               (SELECT COALESCE(sum(x.amount), 0) FROM finance.refunds x
                 WHERE x.payment_id = p.id AND x.status = 'succeeded')
                   AS refunded
        FROM finance.payments p
        LEFT JOIN iam.users cu ON cu.id = p.cashier_id
        {_PAYMENT_FOLIO_JOINS}
        WHERE p.property_id = :p
          -- A failed or abandoned attempt is not a receipt.
          AND p.status = 'succeeded'
          AND (CAST(:method AS text) IS NULL
               OR lower(p.method) = CAST(:method AS text))
    ) q
    WHERE q.business_date BETWEEN :a AND :b
    ORDER BY q.business_date, q.received_at, q.id
    LIMIT :lim
"""

_REFUNDS_SQL = f"""
    SELECT * FROM (
        SELECT x.id, x.created_at, x.amount, x.reason, x.status,
               x.provider_refund_id,
               COALESCE(x.method, p.method) AS method,
               COALESCE(
                   (SELECT min(re.business_date) FROM finance.folio_entries re
                     WHERE re.source_type IN {_in(_MOVEMENT_DEBIT)}
                       AND re.source_id = x.id::text),
                   CAST(timezone(CAST(:tz AS text), x.created_at) AS date)
               ) AS business_date,
               p.id AS payment_id, p.method AS payment_method,
               p.reference AS payment_reference,
               p.provider_transaction_id AS payment_provider_ref,
               cu.display_name AS cashier,
               f.folio_no, r.id AS reservation_id, r.number AS reservation,
               g.full_name AS guest
        FROM finance.refunds x
        LEFT JOIN finance.payments p ON p.id = x.payment_id
        LEFT JOIN finance.cashier_shifts s ON s.id = x.cashier_shift_id
        LEFT JOIN iam.users cu ON cu.id = s.cashier_id
        {_PAYMENT_FOLIO_JOINS}
        WHERE x.property_id = :p
    ) q
    WHERE q.business_date BETWEEN :a AND :b
    ORDER BY q.business_date, q.created_at, q.id
    LIMIT :lim
"""

_REVENUE_ENTRIES_SQL = f"""
    SELECT e.id, e.business_date, e.posted_at, e.entry_type, e.amount,
           -- An adjustment is filed under the charge it reduced.
           COALESCE(o.source_type, e.source_type, 'other') AS charge_type,
           e.source_type,
           f.folio_no, g.full_name AS guest, u.room_code AS room,
           u.room_type_id, u.rate_plan_id, r.id AS reservation_id
    FROM finance.folio_entries e
    JOIN finance.folios f ON f.id = e.folio_id
    LEFT JOIN finance.folio_entries o ON o.id = e.reversal_of_id
    {_GUEST_JOINS}
    WHERE e.property_id = :p
      AND e.business_date BETWEEN :a AND :b
      AND {_REVENUE}
    ORDER BY e.business_date, e.posted_at, e.id
    LIMIT :lim
"""


def _payments(ctx: Ctx, *, method: str | None = None,
              limit: bool = True) -> list[dict[str, Any]]:
    return ctx.rows(_PAYMENTS_SQL, method=method,
                    lim=MAX_ROWS + 1 if limit else None)


def _refunds(ctx: Ctx, *, limit: bool = True) -> list[dict[str, Any]]:
    return ctx.rows(_REFUNDS_SQL, lim=MAX_ROWS + 1 if limit else None)


def _revenue_entries(ctx: Ctx, *, limit: bool = True) -> list[dict[str, Any]]:
    return ctx.rows(_REVENUE_ENTRIES_SQL, lim=MAX_ROWS + 1 if limit else None)


def _available_nights(ctx: Ctx) -> dict[tuple[date, Any], int]:
    """Rooms that could be sold on each date, by room type: active rooms, less
    any that are out of order that day. Room blocks held for a group are still
    sellable to that group, so they are not taken off."""
    rows = ctx.rows("""
        SELECT CAST(d AS date) AS stay_date, rm.room_type_id, count(*) AS rooms
        FROM generate_series(CAST(:a AS timestamp), CAST(:b AS timestamp),
                             interval '1 day') d
        JOIN property.rooms rm
          ON rm.property_id = :p AND rm.status = 'active'
         AND (rm.active_from IS NULL OR rm.active_from <= CAST(d AS date))
         AND (rm.retired_on IS NULL OR rm.retired_on > CAST(d AS date))
        WHERE NOT EXISTS (
            SELECT 1 FROM booking.room_blocks b
            WHERE b.room_id = rm.id AND b.status = 'active'
              AND b.block_type = 'out_of_order'
              AND b.start_date <= CAST(d AS date)
              AND b.end_date >= CAST(d AS date))
        GROUP BY 1, 2
    """)
    return {(r["stay_date"], r["room_type_id"]): r["rooms"] for r in rows}


def _sold_nights(ctx: Ctx) -> list[dict[str, Any]]:
    """Room nights occupied, per stay date, from the reservation lines.

    Cancelled and no-show lines never occupied a room and are not counted.

    ``nights`` is every occupied night, which is what occupancy means: a room
    given to a VIP is not available to sell, and a hotel reporting it as empty
    would be lying about how full it was.

    ``paid_nights`` excludes the ones nobody was charged for -- rooms declared
    complimentary or taken for house use. That distinction exists because ADR
    divides revenue by nights, and a free night adds a denominator with no
    numerator: give away four rooms in a slow week and the average rate falls
    even though every paying guest paid exactly what they always did. Hotels
    therefore quote occupancy including comps and ADR excluding them, and
    keeping both counts here is what lets each report ask for the one it
    means.
    """
    return ctx.rows("""
        SELECT CAST(d AS date) AS stay_date, ru.room_type_id, ru.rate_plan_id,
               ru.reservation_id, count(*) AS nights,
               count(*) FILTER (WHERE ru.comp_kind IS NULL) AS paid_nights,
               count(*) FILTER (WHERE ru.comp_kind IS NOT NULL) AS comp_nights
        FROM booking.reservation_units ru
        CROSS JOIN LATERAL generate_series(
            CAST(GREATEST(ru.arrival_date, CAST(:a AS date)) AS timestamp),
            CAST(LEAST(ru.departure_date - 1, CAST(:b AS date)) AS timestamp),
            interval '1 day') d
        WHERE ru.property_id = :p
          AND ru.status NOT IN ('cancelled', 'no_show')
          AND ru.arrival_date <= :b AND ru.departure_date > :a
        GROUP BY 1, 2, 3, 4
    """)


_ROOM_NIGHT_NOTE = (
    "Available nights are active rooms on each date less those out of order. "
    "Sold nights are nights of reservation lines that were not cancelled or "
    "no-shows. Room revenue is room charges posted to folios on each business "
    "date, net of adjustments and before tax; it is credited to the room type "
    "of the reservation's first room line."
)


# ==========================================================================
# Finance & ledgers
# ==========================================================================
@report(
    number=4, slug="cashier-sales", title="Cashier Sales Report",
    category="Revenue & sales",
    description="Review collections, refunds and the cash count for every cashier shift.",
    columns=(
        Col("cashier", "Cashier"),
        Col("business_date", "Business date", "date"),
        Col("opened_at", "Opened", "datetime"),
        Col("closed_at", "Closed", "datetime"),
        Col("opening_float", "Opening float", "money"),
        Col("collections", "Collections", "money"),
        Col("cash", "Cash collected", "money"),
        Col("refunds", "Refunds", "money"),
        Col("net", "Net collected", "money"),
        Col("expected_cash", "Expected cash", "money"),
        Col("declared_cash", "Declared cash", "money"),
        Col("cash_check", "Cash check", "status"),
        Col("status", "Shift", "status"),
    ),
    metrics=(
        Metric("Shifts", "count", fmt="number"),
        Metric("Collections", "sum", "collections"),
        Metric("Refunds", "sum", "refunds"),
        Metric("Net collected", "sum", "net"),
    ),
    filters=("cashier", "cash_check", "status"),
    totals=("collections", "cash", "refunds", "net"),
    note=(
        "One row per cashier shift on a business date in the window. "
        "Collections and refunds are the payments and refunds recorded against "
        "that shift. Charges are not recorded against a cashier in this "
        "system, so sales before tax are not shown here; see Daily Revenue. "
        "Cash check compares the cash declared at close with the cash the "
        "shift expected to hold."
    ),
    empty_text="No cashier shift was opened on these business dates.",
)
def _cashier_sales(ctx: Ctx) -> list[dict[str, Any]]:
    rows = ctx.rows("""
        SELECT s.id, COALESCE(u.display_name, 'Unknown cashier') AS cashier,
               s.business_date, s.opened_at, s.closed_at, s.opening_float,
               COALESCE(pay.total, 0) AS collections,
               COALESCE(pay.cash, 0) AS cash,
               COALESCE(rf.total, 0) AS refunds,
               s.expected_cash, s.declared_cash, s.status
        FROM finance.cashier_shifts s
        LEFT JOIN iam.users u ON u.id = s.cashier_id
        LEFT JOIN LATERAL (
            SELECT sum(p.amount) AS total,
                   sum(p.amount) FILTER (WHERE lower(p.method) = 'cash') AS cash
            FROM finance.payments p
            WHERE p.cashier_shift_id = s.id AND p.status = 'succeeded'
        ) pay ON TRUE
        LEFT JOIN LATERAL (
            SELECT sum(x.amount) AS total FROM finance.refunds x
            WHERE x.cashier_shift_id = s.id AND x.status = 'succeeded'
        ) rf ON TRUE
        WHERE s.property_id = :p AND s.business_date BETWEEN :a AND :b
        ORDER BY s.business_date, s.opened_at
        LIMIT :lim
    """)
    for r in rows:
        r["net"] = r["collections"] - r["refunds"]
        declared, expected = r["declared_cash"], r["expected_cash"]
        r["cash_check"] = (
            "Not declared" if declared is None or expected is None
            else "Balanced" if declared == expected
            else "Short" if declared < expected
            else "Over"
        )
        r["status"] = _label(r["status"])
    return rows


@report(
    number=8, slug="credit-card-process", title="Credit Card Process - Detail",
    category="Finance & ledgers",
    description="Trace every card payment, what has been refunded against it and what is left.",
    columns=(
        Col("business_date", "Business date", "date"),
        Col("received_at", "Received", "datetime"),
        Col("payment_ref", "Payment ref."),
        Col("guest", "Guest"),
        Col("reservation", "Reservation"),
        Col("folio_no", "Folio"),
        Col("channel", "Taken via"),
        Col("processor", "Processor"),
        Col("amount", "Amount", "money"),
        Col("refunded", "Refunded", "money"),
        Col("net", "Net", "money"),
        Col("status", "Status", "status"),
    ),
    metrics=(
        Metric("Card payments", "count", fmt="number"),
        Metric("Captured", "sum", "amount"),
        Metric("Refunded", "sum", "refunded"),
        Metric("Net of refunds", "sum", "net"),
    ),
    filters=("channel", "processor", "status"),
    totals=("amount", "refunded", "net"),
    note=(
        "Card payments that succeeded, dated by the business date they were "
        "posted to the folio. Refunded is everything refunded against the "
        "payment to date, whenever it was refunded. Processor fees and "
        "settlement batches are not recorded in this system yet, so every "
        "amount is gross of fees."
    ),
    empty_text="No card payment was taken on these business dates.",
    link="reservation_id",
)
def _credit_card(ctx: Ctx) -> list[dict[str, Any]]:
    rows = _payments(ctx, method="card")
    for r in rows:
        r["payment_ref"] = _ref(r["reference"], r["provider_transaction_id"],
                                r["id"], "PAY")
        r["channel"] = _label(r["source"], _CHANNEL) or "Unknown"
        r["processor"] = ("Online gateway"
                          if r["intent_id"] or r["provider_transaction_id"]
                          else "Card terminal")
        r["net"] = r["amount"] - r["refunded"]
        r["status"] = ("Captured" if not r["refunded"]
                       else "Refunded" if r["refunded"] >= r["amount"]
                       else "Part refunded")
    return rows


@report(
    number=10, slug="daily-receipt-detail", title="Daily Receipt - Detail",
    category="Finance & ledgers",
    description="List every payment received, with who took it and how it was paid.",
    columns=(
        Col("business_date", "Business date", "date"),
        Col("received_at", "Time", "datetime"),
        Col("receipt", "Receipt"),
        Col("guest", "Guest / account"),
        Col("reservation", "Reservation"),
        Col("folio_no", "Folio"),
        Col("method", "Method"),
        Col("channel", "Taken via"),
        Col("amount", "Received", "money"),
        Col("cashier", "Cashier"),
    ),
    metrics=(
        Metric("Receipts", "count", fmt="number"),
        Metric("Received", "sum", "amount"),
        Metric("Cash", "sum", "amount", where="method", value="Cash"),
        Metric("Non-cash", "sum", "amount", where="method", value="Cash",
               negate=True),
    ),
    filters=("method", "channel", "cashier"),
    totals=("amount",),
    note=(
        "Payments that succeeded, dated by the business date they were posted "
        "to a folio, or the property's local date received if not yet "
        "allocated. Failed and abandoned attempts are not receipts and are left "
        "out. Refunds are reported separately in Daily Refund Report."
    ),
    empty_text="No payment was received on these business dates.",
    link="reservation_id",
)
def _receipt_detail(ctx: Ctx) -> list[dict[str, Any]]:
    rows = _payments(ctx)
    for r in rows:
        r["receipt"] = _ref(r["reference"], r["provider_transaction_id"],
                            r["id"], "PAY")
        r["method"] = _method(r["method"])
        r["channel"] = _label(r["source"], _CHANNEL) or "Unknown"
    return rows


@report(
    number=11, slug="daily-receipt-summary", title="Daily Receipt - Summary",
    category="Finance & ledgers",
    description="Compare money in and money out by payment method.",
    columns=(
        Col("method", "Payment method"),
        Col("receipts", "Receipts", "number"),
        Col("received", "Received", "money"),
        Col("refund_count", "Refunds", "number"),
        Col("refunded", "Refunded", "money"),
        Col("net", "Net", "money"),
        Col("share", "Share of received", "percent"),
    ),
    metrics=(
        Metric("Received", "sum", "received"),
        Metric("Refunded", "sum", "refunded"),
        Metric("Net", "sum", "net"),
        Metric("Receipts", "sum", "receipts", fmt="number"),
    ),
    totals=("receipts", "received", "refund_count", "refunded", "net"),
    note=(
        "Money in and money out per payment method over the window: receipts "
        "by the business date they were posted, refunds by the business date "
        "they were paid out. Net is what each method should have moved, which "
        "is what a cash drawer or a card settlement is reconciled against."
    ),
    empty_text="No payment was received or refunded on these business dates.",
)
def _receipt_summary(ctx: Ctx) -> list[dict[str, Any]]:
    by: dict[str, dict[str, Any]] = defaultdict(lambda: {
        "receipts": 0, "received": Decimal(0),
        "refund_count": 0, "refunded": Decimal(0)})
    for p in _payments(ctx, limit=False):
        m = by[_method(p["method"])]
        m["receipts"] += 1
        m["received"] += p["amount"]
    for x in _refunds(ctx, limit=False):
        if x["status"] != "succeeded":
            continue
        m = by[_method(x["method"])]
        m["refund_count"] += 1
        m["refunded"] += x["amount"]
    total = sum((m["received"] for m in by.values()), Decimal(0))
    rows = [
        {"method": k, **m, "net": m["received"] - m["refunded"],
         "share": _div(m["received"] * 100, total)}
        for k, m in by.items()
    ]
    return sorted(rows, key=lambda r: -r["received"])


@report(
    number=12, slug="daily-refund", title="Daily Refund Report",
    category="Finance & ledgers",
    description="Review every refund, the receipt it returned money from and why.",
    columns=(
        Col("business_date", "Business date", "date"),
        Col("created_at", "Time", "datetime"),
        Col("refund_ref", "Refund"),
        Col("guest", "Guest"),
        Col("reservation", "Reservation"),
        Col("receipt", "Original receipt"),
        Col("method", "Method"),
        Col("amount", "Amount", "money"),
        Col("reason", "Reason"),
        Col("cashier", "Cashier"),
        Col("status", "Status", "status"),
    ),
    metrics=(
        Metric("Refunds", "count", fmt="number"),
        Metric("Refunded", "sum", "amount"),
        Metric("Largest refund", "max", "amount"),
        Metric("Guests refunded", "unique", "guest", fmt="number"),
    ),
    filters=("method", "reason", "status"),
    totals=("amount",),
    note=(
        "Refunds dated by the business date they were posted to the folio, or "
        "the property's local date raised if never posted. Includes deposit "
        "refunds and refunds raised through a payment reversal. Cashier is the "
        "cashier whose shift the refund was paid from, where it was."
    ),
    empty_text="No refund was raised on these business dates.",
    link="reservation_id",
)
def _refund_detail(ctx: Ctx) -> list[dict[str, Any]]:
    rows = _refunds(ctx)
    for r in rows:
        r["refund_ref"] = _ref(None, r["provider_refund_id"], r["id"], "RF")
        r["receipt"] = (
            _ref(r["payment_reference"], r["payment_provider_ref"],
                 r["payment_id"], "PAY")
            if r["payment_id"] else None)
        r["method"] = _method(r["method"])
        r["reason"] = _label(r["reason"])
        r["status"] = _label(r["status"])
    return rows


@report(
    number=17, slug="folio-list", title="Folio List",
    category="Finance & ledgers",
    description="See every folio, what was charged and paid, and what is still owed.",
    basis="As of date", dates="single",
    columns=(
        Col("folio_no", "Folio"),
        Col("guest", "Guest"),
        Col("reservation", "Reservation"),
        Col("room", "Room"),
        Col("arrival_date", "Arrival", "date"),
        Col("departure_date", "Departure", "date"),
        Col("stay_status", "Stay status", "status"),
        Col("charges", "Charges", "money"),
        Col("credits", "Payments / credits", "money"),
        Col("balance", "Balance", "money"),
        Col("folio_status", "Folio", "status"),
    ),
    metrics=(
        Metric("Folios", "count", fmt="number"),
        Metric("Charges", "sum", "charges"),
        Metric("Payments / credits", "sum", "credits"),
        Metric("Folios with a balance", "nonzero", "balance", fmt="number"),
    ),
    filters=("stay_status", "folio_status"),
    totals=("charges", "credits", "balance"),
    note=(
        "Every folio opened on or before the date, with everything posted to it "
        "up to and including that business date. Payments / credits are net of "
        "refunds paid back out, so balance = charges − payments / credits. "
        "Cancelled and no-show folios are included because they can carry "
        "penalties."
    ),
    empty_text="No folio had been opened by this date.",
    link="reservation_id",
)
def _folio_list(ctx: Ctx) -> list[dict[str, Any]]:
    rows = _folio_positions(ctx, min_balance=None)
    return rows


def _folio_positions(ctx: Ctx, min_balance: Decimal | None) -> list[dict[str, Any]]:
    rows = ctx.rows(f"""
        SELECT * FROM (
            SELECT f.id, f.folio_no, f.status AS folio_status,
                   g.full_name AS guest, r.id AS reservation_id,
                   r.number AS reservation, u.room_code AS room,
                   u.arrival_date, u.departure_date, u.status AS stay,
                   COALESCE(sum(e.amount) FILTER (
                       WHERE e.entry_type = 'debit'
                         AND {_S} NOT IN {_in(_MOVEMENT_DEBIT)}), 0) AS charges,
                   COALESCE(sum(e.amount) FILTER (
                       WHERE e.entry_type = 'credit'), 0)
                   - COALESCE(sum(e.amount) FILTER (
                       WHERE e.entry_type = 'debit'
                         AND {_S} IN {_in(_MOVEMENT_DEBIT)}), 0) AS credits,
                   COALESCE(sum(CASE WHEN e.entry_type = 'debit'
                                     THEN e.amount ELSE -e.amount END), 0)
                       AS balance
            FROM finance.folios f
            LEFT JOIN finance.folio_entries e
                   ON e.folio_id = f.id AND e.business_date <= :b
            {_GUEST_JOINS}
            WHERE f.property_id = :p
              AND CAST(timezone(CAST(:tz AS text), f.created_at) AS date) <= :b
            GROUP BY f.id, f.folio_no, f.status, g.full_name, r.id, r.number,
                     u.room_code, u.arrival_date, u.departure_date, u.status
        ) q
        WHERE CAST(:minb AS numeric) IS NULL OR q.balance > CAST(:minb AS numeric)
        ORDER BY q.folio_no
        LIMIT :lim
    """, minb=min_balance)
    for r in rows:
        r["stay_status"] = _label(r["stay"], _STAY)
        r["folio_status"] = _label(r["folio_status"])
    return rows


@report(
    number=39, slug="high-balance-guest", title="High Balance Guest",
    category="Finance & ledgers",
    description="Find folios owing more than a threshold, departed guests first.",
    basis="As of date", dates="single",
    params=(Param("min_balance", "Balance above (INR)", "number", 1000),),
    columns=(
        Col("folio_no", "Folio"),
        Col("guest", "Guest"),
        Col("room", "Room"),
        Col("stay_status", "Stay status", "status"),
        Col("departure_date", "Departure", "date"),
        Col("charges", "Charges", "money"),
        Col("credits", "Payments / credits", "money"),
        Col("balance", "Outstanding", "money"),
        Col("follow_up", "Follow-up", "status"),
    ),
    metrics=(
        Metric("Folios over threshold", "count", fmt="number"),
        Metric("Outstanding", "sum", "balance"),
        Metric("Largest balance", "max", "balance"),
        Metric("Departed with a balance", "count", fmt="number",
               where="follow_up", value="Collect: departed"),
    ),
    filters=("stay_status", "follow_up"),
    totals=("charges", "credits", "balance"),
    note=(
        "Folios owing the property more than the threshold as of the date. "
        "Guests who have already left are listed first, because that money "
        "will not be settled at a desk; in-house guests may still be paying."
    ),
    empty_text="No folio owes more than the threshold as of this date.",
    link="reservation_id",
)
def _high_balance(ctx: Ctx) -> list[dict[str, Any]]:
    rows = _folio_positions(ctx, min_balance=ctx.params["min_balance"])
    order = {"Collect: departed": 0, "Collect: no show": 1,
             "Review: in-house": 2, "Before arrival": 3}
    for r in rows:
        r["follow_up"] = {
            "checked_out": "Collect: departed", "no_show": "Collect: no show",
            "cancelled": "Collect: departed", "checked_in": "Review: in-house",
        }.get(r["stay"] or "", "Before arrival")
    return sorted(rows, key=lambda r: (order[r["follow_up"]], -r["balance"]))


@report(
    number=19, slug="guest-ledger", title="Guest Ledger",
    category="Finance & ledgers",
    description="Roll each guest folio forward from its opening to its closing balance.",
    columns=(
        Col("folio_no", "Folio"),
        Col("guest", "Guest"),
        Col("room", "Room"),
        Col("stay_status", "Stay status", "status"),
        Col("opening", "Opening", "money"),
        Col("charges", "Charges", "money"),
        Col("payments", "Payments", "money"),
        Col("refunds", "Refunds", "money"),
        Col("adjustments", "Adjustments", "money"),
        Col("closing", "Closing", "money"),
    ),
    metrics=(
        Metric("Opening balance", "sum", "opening"),
        Metric("Charges", "sum", "charges"),
        Metric("Payments", "sum", "payments"),
        Metric("Closing balance", "sum", "closing"),
    ),
    filters=("stay_status",),
    totals=("opening", "charges", "payments", "refunds", "adjustments", "closing"),
    note=(
        "Every guest folio that carried a balance into the window or moved "
        "during it. Closing = opening + charges + refunds − payments − "
        "adjustments. Deposits taken on a guest folio count as payments here; "
        "the Hotel Ledger report splits them into a deposit ledger of their own."
    ),
    empty_text="No guest folio carried a balance or moved on these business dates.",
    link="reservation_id",
)
def _guest_ledger(ctx: Ctx) -> list[dict[str, Any]]:
    def between(cond: str) -> str:
        return (f"COALESCE(sum(e.amount) FILTER (WHERE e.business_date "
                f"BETWEEN :a AND :b AND {cond}), 0)")

    rows = ctx.rows(f"""
        SELECT * FROM (
            SELECT f.id, f.folio_no, g.full_name AS guest, r.id AS reservation_id,
                   u.room_code AS room, u.status AS stay,
                   COALESCE(sum(CASE WHEN e.entry_type = 'debit'
                                     THEN e.amount ELSE -e.amount END)
                            FILTER (WHERE e.business_date < :a), 0) AS opening,
                   {between(f"e.entry_type = 'debit' AND {_S} NOT IN {_in(_MOVEMENT_DEBIT)}")} AS charges,
                   {between(f"e.entry_type = 'credit' AND {_S} IN {_in(_MOVEMENT_CREDIT)}")} AS payments,
                   {between(f"e.entry_type = 'debit' AND {_S} IN {_in(_MOVEMENT_DEBIT)}")} AS refunds,
                   {between(f"e.entry_type = 'credit' AND {_S} NOT IN {_in(_MOVEMENT_CREDIT)}")} AS adjustments
            FROM finance.folios f
            JOIN finance.folio_entries e
              ON e.folio_id = f.id AND e.business_date <= :b
            {_GUEST_JOINS}
            WHERE f.property_id = :p AND f.type = 'guest'
            GROUP BY f.id, f.folio_no, g.full_name, r.id, u.room_code, u.status
        ) q
        WHERE q.opening <> 0 OR q.charges <> 0 OR q.payments <> 0
           OR q.refunds <> 0 OR q.adjustments <> 0
        ORDER BY q.folio_no
        LIMIT :lim
    """)
    for r in rows:
        r["closing"] = (r["opening"] + r["charges"] + r["refunds"]
                        - r["payments"] - r["adjustments"])
        r["stay_status"] = _label(r["stay"], _STAY)
    return rows


@report(
    number=33, slug="transaction-detail", title="Transaction Detail Report",
    category="Finance & ledgers",
    description="Audit every posting to every folio, in the order it was made.",
    columns=(
        Col("business_date", "Business date", "date"),
        Col("posted_at", "Posted", "datetime"),
        Col("folio_no", "Folio"),
        Col("guest", "Guest"),
        Col("reservation", "Reservation"),
        Col("type", "Type", "status"),
        Col("description", "Description"),
        Col("debit", "Debit", "money"),
        Col("credit", "Credit", "money"),
        Col("running", "Folio balance", "money"),
    ),
    metrics=(
        Metric("Transactions", "count", fmt="number"),
        Metric("Debits", "sum", "debit"),
        Metric("Credits", "sum", "credit"),
        Metric("Net movement", "difference", ("debit", "credit")),
    ),
    filters=("type", "description"),
    totals=("debit", "credit"),
    note=(
        "Every posting to every folio in the window, in the order it was made. "
        "A debit increases what the guest owes and a credit reduces it. Folio "
        "balance is that folio's balance just after the posting, counting its "
        "whole history, so filtering the list never changes it."
    ),
    empty_text="Nothing was posted to any folio on these business dates.",
    link="reservation_id",
)
def _transactions(ctx: Ctx) -> list[dict[str, Any]]:
    rows = ctx.rows(f"""
        WITH running AS (
            SELECT e.id,
                   sum(CASE WHEN e.entry_type = 'debit'
                            THEN e.amount ELSE -e.amount END)
                   OVER (PARTITION BY e.folio_id
                         ORDER BY e.business_date, e.posted_at, e.id
                         ROWS UNBOUNDED PRECEDING) AS balance
            FROM finance.folio_entries e
            WHERE e.property_id = :p AND e.business_date <= :b
        )
        SELECT e.business_date, e.posted_at, e.entry_type, e.amount,
               e.source_type, f.folio_no, g.full_name AS guest,
               r.id AS reservation_id, r.number AS reservation,
               rn.balance AS running
        FROM finance.folio_entries e
        JOIN finance.folios f ON f.id = e.folio_id
        JOIN running rn ON rn.id = e.id
        {_GUEST_JOINS}
        WHERE e.property_id = :p AND e.business_date BETWEEN :a AND :b
        ORDER BY e.business_date, e.posted_at, e.id
        LIMIT :lim
    """)
    for r in rows:
        st = r["source_type"] or "other"
        debit = r["entry_type"] == "debit"
        r["debit"] = r["amount"] if debit else Decimal(0)
        r["credit"] = Decimal(0) if debit else r["amount"]
        r["type"] = (
            "Payment" if st == "payment"
            else "Refund" if st in _MOVEMENT_DEBIT
            else "Deposit" if st in ("deposit", "security_deposit")
            else "Tax" if st.endswith("_tax")
            else "Adjustment" if not debit or st in ("adjustment", "credit_note")
            else "Charge"
        )
        r["description"] = _describe(st)
    return rows


# ==========================================================================
# Revenue & sales
# ==========================================================================
@report(
    number=9, slug="daily-extra-charge", title="Daily Extra Charge - Detail",
    category="Revenue & sales",
    description="List charges other than room nights: penalties, fees, food and services.",
    columns=(
        Col("business_date", "Business date", "date"),
        Col("posted_at", "Posted", "datetime"),
        Col("folio_no", "Folio"),
        Col("guest", "Guest"),
        Col("room", "Room"),
        Col("department", "Department"),
        Col("item", "Item"),
        Col("amount", "Charge", "money"),
    ),
    metrics=(
        Metric("Charges posted", "count", fmt="number"),
        Metric("Extra charges", "sum", "amount"),
        Metric("Guests charged", "unique", "guest", fmt="number"),
        Metric("Largest charge", "max", "amount"),
    ),
    filters=("department", "item"),
    totals=("amount",),
    note=(
        "Every charge posted in the window other than room nights, tax and "
        "refunds. Adjustments that later reduced a charge are not netted off "
        "here; Detail Revenue shows them against the charge."
    ),
    empty_text="No extra charge was posted on these business dates.",
    link="reservation_id",
)
def _extra_charges(ctx: Ctx) -> list[dict[str, Any]]:
    rows = ctx.rows(f"""
        SELECT e.business_date, e.posted_at, e.amount, e.source_type,
               f.folio_no, g.full_name AS guest, u.room_code AS room,
               r.id AS reservation_id
        FROM finance.folio_entries e
        JOIN finance.folios f ON f.id = e.folio_id
        {_GUEST_JOINS}
        WHERE e.property_id = :p AND e.business_date BETWEEN :a AND :b
          AND e.entry_type = 'debit'
          AND {_S} NOT IN {_in(_MOVEMENT_DEBIT)}
          AND {_S} NOT IN ('room_night', 'room_stay', 'room_upgrade', 'room_move')
          AND right({_S}, 4) <> '_tax'
        ORDER BY e.business_date, e.posted_at, e.id
        LIMIT :lim
    """)
    for r in rows:
        r["department"] = _department(r["source_type"])
        r["item"] = _describe(r["source_type"])
    return rows


_REVENUE_NOTE = (
    "Charges posted to folios in the window by department, less adjustments "
    "and credit notes that reduced them, which are filed under the department "
    "of the charge they reduced. Payments, refunds and deposits move money "
    "rather than earn it, and are left out. Tax is its own line."
)


@report(
    number=13, slug="daily-revenue", title="Daily Revenue",
    category="Revenue & sales",
    description="Summarise revenue by department for the business day or any window.",
    columns=(
        Col("department", "Department"),
        Col("postings", "Postings", "number"),
        Col("gross", "Charges", "money"),
        Col("adjustments", "Adjustments", "money"),
        Col("net", "Net revenue", "money"),
        Col("share", "Share of revenue", "percent"),
    ),
    metrics=(
        Metric("Revenue before tax", "sum", "net", where="department",
               value="Taxes", negate=True),
        Metric("Tax", "sum", "net", where="department", value="Taxes"),
        Metric("Charges posted", "sum", "gross"),
        Metric("Adjustments", "sum", "adjustments"),
    ),
    totals=("postings", "gross", "adjustments", "net"),
    note=_REVENUE_NOTE + " Share of revenue leaves tax out.",
    empty_text="No revenue was posted on these business dates.",
)
def _daily_revenue(ctx: Ctx) -> list[dict[str, Any]]:
    by: dict[str, dict[str, Any]] = defaultdict(lambda: {
        "postings": 0, "gross": Decimal(0), "adjustments": Decimal(0)})
    for e in _revenue_entries(ctx, limit=False):
        d = by[_department(e["charge_type"])]
        if e["entry_type"] == "debit":
            d["postings"] += 1
            d["gross"] += e["amount"]
        else:
            d["adjustments"] += e["amount"]
    earned = sum((d["gross"] - d["adjustments"]
                  for k, d in by.items() if k != "Taxes"), Decimal(0))
    rows = []
    for k, d in by.items():
        net = d["gross"] - d["adjustments"]
        rows.append({"department": k, **d, "net": net,
                     "share": None if k == "Taxes" else _div(net * 100, earned)})
    # Rooms first, tax last, the rest by size.
    return sorted(rows, key=lambda r: (r["department"] != "Rooms",
                                       r["department"] == "Taxes", -r["net"]))


@report(
    number=15, slug="detail-revenue", title="Detail Revenue Report",
    category="Revenue & sales",
    description="Every posting behind Daily Revenue, with the adjustments against it.",
    columns=(
        Col("business_date", "Business date", "date"),
        Col("posted_at", "Posted", "datetime"),
        Col("folio_no", "Folio"),
        Col("guest", "Guest"),
        Col("room", "Room"),
        Col("department", "Department"),
        Col("description", "Description"),
        Col("charge", "Charge", "money"),
        Col("adjustment", "Adjustment", "money"),
        Col("net", "Net revenue", "money"),
    ),
    metrics=(
        Metric("Postings", "count", fmt="number"),
        Metric("Charges", "sum", "charge"),
        Metric("Adjustments", "sum", "adjustment"),
        Metric("Net revenue", "sum", "net"),
    ),
    filters=("department", "description"),
    totals=("charge", "adjustment", "net"),
    note=_REVENUE_NOTE + " Totals here agree with Daily Revenue for the same dates.",
    empty_text="No revenue was posted on these business dates.",
    link="reservation_id",
)
def _detail_revenue(ctx: Ctx) -> list[dict[str, Any]]:
    rows = _revenue_entries(ctx)
    for r in rows:
        debit = r["entry_type"] == "debit"
        r["department"] = _department(r["charge_type"])
        r["description"] = (_describe(r["charge_type"]) if debit
                            else f"Adjustment: {_describe(r['charge_type']).lower()}")
        r["charge"] = r["amount"] if debit else Decimal(0)
        r["adjustment"] = Decimal(0) if debit else r["amount"]
        r["net"] = _signed(r)
    return rows


@report(
    number=29, slug="rate-card", title="Rate Card",
    category="Revenue & sales",
    description="See what every rate plan sells each room type for on a stay date.",
    basis="Stay date", dates="single",
    columns=(
        Col("room_type", "Room type"),
        Col("rate_plan", "Rate plan"),
        Col("plan_type", "Plan type"),
        Col("meal_plan", "Meals"),
        Col("nightly", "Nightly rate", "money"),
        Col("extra_adult", "Extra adult", "money"),
        Col("child", "Child", "money"),
        Col("min_stay", "Min. stay", "number"),
        Col("cancellation", "Cancellation"),
        Col("selling", "Selling", "status"),
        Col("status", "Plan status", "status"),
    ),
    metrics=(
        Metric("Rate lines", "count", fmt="number"),
        Metric("Rate plans", "unique", "rate_plan", fmt="number"),
        Metric("Room types", "unique", "room_type", fmt="number"),
        Metric("Highest nightly rate", "max", "nightly"),
    ),
    filters=("room_type", "rate_plan", "selling", "status"),
    note=(
        "Each room type's rate for the stay date from Rates & Inventory, or its "
        "base rate where no rate is set for that date, adjusted by the plan -- "
        "a flat rate, or a percentage or amount up or down, the same rule the "
        "rate plans screen applies. Rates are before tax."
    ),
    empty_text="No rate plan is set up for this property yet.",
)
def _rate_card(ctx: Ctx) -> list[dict[str, Any]]:
    rows = ctx.rows("""
        SELECT rt.name AS room_type, rp.name AS rate_plan, rp.plan_type,
               mp.name AS meal_plan, rp.status, rp.refundable,
               rp.free_cancellation_hours, rp.cancellation_policy,
               COALESCE(cal.min_stay, rp.min_stay) AS min_stay,
               COALESCE(cal.stop_sell, FALSE) AS stop_sell,
               COALESCE(rp.extra_adult_charge, rt.extra_adult_rate) AS extra_adult,
               COALESCE(rp.child_charge, rt.extra_child_rate) AS child,
               round(COALESCE(rp.flat_rate, GREATEST(
                   CASE WHEN rp.adjustment_type = 'percent'
                        THEN base.rate * (1 + (CASE WHEN rp.adjustment_direction = 'decrease'
                                                   THEN -COALESCE(rp.adjustment_value, 0)
                                                   ELSE COALESCE(rp.adjustment_value, 0) END) / 100.0)
                        ELSE base.rate + (CASE WHEN rp.adjustment_direction = 'decrease'
                                               THEN -COALESCE(rp.adjustment_value, 0)
                                               ELSE COALESCE(rp.adjustment_value, 0) END)
                   END, 0)), 2) AS nightly
        FROM property.rate_plans rp
        JOIN property.room_types rt
          ON rt.property_id = rp.property_id AND rt.status = 'active'
         AND (NOT EXISTS (SELECT 1 FROM property.rate_plan_room_types l
                           WHERE l.rate_plan_id = rp.id)
              OR EXISTS (SELECT 1 FROM property.rate_plan_room_types l
                          WHERE l.rate_plan_id = rp.id AND l.room_type_id = rt.id))
        LEFT JOIN property.rate_calendar_days cal
               ON cal.room_type_id = rt.id AND cal.stay_date = :b
        CROSS JOIN LATERAL (SELECT COALESCE(cal.rate, rt.base_rate) AS rate) base
        LEFT JOIN property.meal_plans mp ON mp.id = rp.meal_plan_id
        WHERE rp.property_id = :p
        ORDER BY rt.name, rp.name
        LIMIT :lim
    """)
    for r in rows:
        r["plan_type"] = _label(r["plan_type"])
        r["meal_plan"] = r["meal_plan"] or "Room only"
        r["cancellation"] = (
            "Non-refundable" if r["refundable"] is False
            else f"Free until {r['free_cancellation_hours']}h before arrival"
            if r["free_cancellation_hours"]
            else r["cancellation_policy"] or "Refundable"
        )
        r["selling"] = "Stop sell" if r["stop_sell"] else "Open"
        r["status"] = _label(r["status"])
    return rows


def _room_revenue(ctx: Ctx) -> list[dict[str, Any]]:
    return [e for e in _revenue_entries(ctx, limit=False)
            if _department(e["charge_type"]) == "Rooms"]


@report(
    number=30, slug="revenue-by-rate-type", title="Revenue By Rate Type",
    category="Revenue & sales",
    description="Compare room nights and room revenue across rate plans.",
    default_days=30,
    columns=(
        Col("rate_plan", "Rate plan"),
        Col("plan_type", "Plan type"),
        Col("reservations", "Reservations", "number"),
        Col("sold", "Sold room nights", "number"),
        Col("revenue", "Room revenue", "money"),
        Col("adr", "ADR", "money"),
        Col("share", "Share of room revenue", "percent"),
    ),
    metrics=(
        Metric("Room revenue", "sum", "revenue"),
        Metric("Sold room nights", "sum", "sold", fmt="number"),
        Metric("ADR", "ratio", "revenue", den="sold"),
        Metric("Rate plans used", "nonzero", "sold", fmt="number"),
    ),
    filters=("plan_type",),
    totals=("reservations", "sold", "revenue"),
    note=_ROOM_NIGHT_NOTE + " Reservation lines booked without a rate plan are "
    "grouped as “No rate plan recorded”.",
    empty_text="No room night was sold and no room revenue posted on these dates.",
)
def _by_rate_type(ctx: Ctx) -> list[dict[str, Any]]:
    plans = {r["id"]: r for r in ctx.rows(
        "SELECT id, name, plan_type FROM property.rate_plans WHERE property_id = :p")}
    sold: dict[Any, int] = defaultdict(int)
    bookings: dict[Any, set] = defaultdict(set)
    paid: dict[Any, int] = defaultdict(int)
    for s in _sold_nights(ctx):
        sold[s["rate_plan_id"]] += s["nights"]
        paid[s["rate_plan_id"]] += s["paid_nights"]
        bookings[s["rate_plan_id"]].add(s["reservation_id"])
    revenue: dict[Any, Decimal] = defaultdict(Decimal)
    for e in _room_revenue(ctx):
        revenue[e["rate_plan_id"]] += _signed(e)
    total = sum(revenue.values(), Decimal(0))
    rows = []
    for key in set(sold) | set(revenue):
        plan = plans.get(key)
        rows.append({
            "rate_plan": plan["name"] if plan else "No rate plan recorded",
            "plan_type": _label(plan["plan_type"]) if plan else None,
            "reservations": len(bookings[key]), "sold": sold[key],
            "revenue": revenue[key],
            # ADR divides by the nights somebody paid for. Occupancy still counts
    # the comps -- the room was occupied -- but dividing revenue by a
    # free night drags the average rate down for a giveaway that never
    # cost a paying guest anything.
    "adr": _div(revenue[key], paid[key]),
            "share": _div(revenue[key] * 100, total),
        })
    return sorted(rows, key=lambda r: -r["revenue"])


@report(
    number=31, slug="revenue-by-room-type", title="Revenue By Room Type",
    category="Revenue & sales",
    description="Compare occupancy, ADR and RevPAR across room types.",
    default_days=30,
    columns=(
        Col("room_type", "Room type"),
        Col("rooms", "Rooms", "number"),
        Col("available", "Available nights", "number"),
        Col("sold", "Sold nights", "number"),
        Col("occupancy", "Occupancy", "percent"),
        Col("revenue", "Room revenue", "money"),
        Col("adr", "ADR", "money"),
        Col("revpar", "RevPAR", "money"),
    ),
    metrics=(
        Metric("Room revenue", "sum", "revenue"),
        Metric("Occupancy", "ratio", "sold", den="available", fmt="percent"),
        Metric("ADR", "ratio", "revenue", den="sold"),
        Metric("RevPAR", "ratio", "revenue", den="available"),
    ),
    totals=("rooms", "available", "sold", "revenue"),
    note=_ROOM_NIGHT_NOTE,
    empty_text="This property has no active room types.",
)
def _by_room_type(ctx: Ctx) -> list[dict[str, Any]]:
    types = ctx.rows("""
        SELECT rt.id, rt.name,
               (SELECT count(*) FROM property.rooms rm
                 WHERE rm.room_type_id = rt.id AND rm.status = 'active') AS rooms
        FROM property.room_types rt
        WHERE rt.property_id = :p
        ORDER BY rt.name
    """)
    avail: dict[Any, int] = defaultdict(int)
    for (_, rt), n in _available_nights(ctx).items():
        avail[rt] += n
    sold: dict[Any, int] = defaultdict(int)
    paid: dict[Any, int] = defaultdict(int)
    for s in _sold_nights(ctx):
        sold[s["room_type_id"]] += s["nights"]
        paid[s["room_type_id"]] += s["paid_nights"]
    revenue: dict[Any, Decimal] = defaultdict(Decimal)
    for e in _room_revenue(ctx):
        revenue[e["room_type_id"]] += _signed(e)
    rows = []
    for t in types:
        k = t["id"]
        rows.append({
            "room_type": t["name"], "rooms": t["rooms"],
            "available": avail[k], "sold": sold[k],
            "occupancy": _div(sold[k] * 100, avail[k]),
            "revenue": revenue[k], "adr": _div(revenue[k], paid[k]),
            "revpar": _div(revenue[k], avail[k]),
        })
    if revenue.get(None):
        # Room charges on a folio with no reservation line to name a type.
        rows.append({"room_type": "No room type", "rooms": None,
                     "available": 0, "sold": 0, "occupancy": None,
                     "revenue": revenue[None], "adr": None, "revpar": None})
    return rows


@report(
    number=32, slug="room-type-daily-revenue",
    title="Room Type Wise Daily Room Revenue",
    category="Revenue & sales",
    description="Track room nights and room revenue for each room type, day by day.",
    default_days=7,
    columns=(
        Col("business_date", "Date", "date"),
        Col("room_type", "Room type"),
        Col("available", "Available nights", "number"),
        Col("sold", "Sold nights", "number"),
        Col("occupancy", "Occupancy", "percent"),
        Col("revenue", "Room revenue", "money"),
        Col("adr", "ADR", "money"),
    ),
    metrics=(
        Metric("Room revenue", "sum", "revenue"),
        Metric("Sold nights", "sum", "sold", fmt="number"),
        Metric("Occupancy", "ratio", "sold", den="available", fmt="percent"),
        Metric("ADR", "ratio", "revenue", den="sold"),
    ),
    filters=("room_type",),
    totals=("available", "sold", "revenue"),
    note=_ROOM_NIGHT_NOTE + " Nights are counted on the stay date and revenue "
    "on the business date it was posted, so a stay charged in one posting at "
    "checkout shows its revenue on that day.",
    empty_text="This property has no active room types.",
)
def _room_type_daily(ctx: Ctx) -> list[dict[str, Any]]:
    names = {r["id"]: r["name"] for r in ctx.rows(
        "SELECT id, name FROM property.room_types WHERE property_id = :p")}
    avail = _available_nights(ctx)
    sold: dict[tuple[date, Any], int] = defaultdict(int)
    paid: dict[tuple[date, Any], int] = defaultdict(int)
    for s in _sold_nights(ctx):
        sold[(s["stay_date"], s["room_type_id"])] += s["nights"]
        paid[(s["stay_date"], s["room_type_id"])] += s["paid_nights"]
    revenue: dict[tuple[date, Any], Decimal] = defaultdict(Decimal)
    for e in _room_revenue(ctx):
        revenue[(e["business_date"], e["room_type_id"])] += _signed(e)
    rows = []
    for key in sorted(set(avail) | set(sold) | set(revenue),
                      key=lambda k: (k[0], names.get(k[1], "~"))):
        d, rt = key
        rows.append({
            "business_date": d, "room_type": names.get(rt, "No room type"),
            "available": avail.get(key, 0), "sold": sold.get(key, 0),
            "occupancy": _div(sold.get(key, 0) * 100, avail.get(key, 0)),
            "revenue": revenue.get(key, Decimal(0)),
            "adr": _div(revenue.get(key, 0), paid.get(key, 0)),
        })
    return rows


# ==========================================================================
# Rooms & operations
# ==========================================================================
@report(
    number=20, slug="house-status", title="House Status",
    category="Rooms & operations",
    description="See who is in every room, what state it is in and who arrives next.",
    basis="Snapshot date", dates="single",
    columns=(
        Col("room", "Room"),
        Col("location", "Building / floor"),
        Col("room_type", "Room type"),
        Col("occupancy", "Occupancy", "status"),
        Col("housekeeping", "Housekeeping", "status"),
        Col("guest", "Guest"),
        Col("arrival_date", "Arrival", "date"),
        Col("departure_date", "Departure", "date"),
        Col("next_arrival", "Next arrival", "date"),
        Col("block_reason", "Block reason"),
    ),
    metrics=(
        Metric("Rooms", "count", fmt="number"),
        Metric("Occupied", "count", fmt="number", where="occupancy", value="Occupied"),
        Metric("Vacant", "count", fmt="number", where="occupancy", value="Vacant"),
        Metric("Dirty", "count", fmt="number", where="housekeeping", value="Dirty"),
    ),
    filters=("occupancy", "housekeeping", "room_type", "location"),
    note=(
        "Who holds each room on the snapshot date, from the same room calendar "
        "the reservation and housekeeping screens read. Housekeeping is the "
        "room's condition now: the system keeps each room's current condition "
        "rather than a history of it, so for a past date that column still "
        "shows today's state."
    ),
    empty_text="This property has no active rooms.",
    link="reservation_id",
)
def _house_status(ctx: Ctx) -> list[dict[str, Any]]:
    rows = ctx.rows("""
        WITH occ AS (
            SELECT DISTINCT ON (e.room_id) e.room_id, e.reservation_unit_id
            FROM booking.room_calendar_entries e
            WHERE e.property_id = :p AND e.status <> 'released'
              AND lower(e.occupied_period) <= CAST(:b AS timestamptz)
              AND upper(e.occupied_period) > CAST(:b AS timestamptz)
            ORDER BY e.room_id, lower(e.occupied_period) DESC
        ),
        nxt AS (
            SELECT e.room_id, min(CAST(lower(e.occupied_period) AS date)) AS next_arrival
            FROM booking.room_calendar_entries e
            WHERE e.property_id = :p AND e.status <> 'released'
              AND lower(e.occupied_period) > CAST(:b AS timestamptz)
            GROUP BY e.room_id
        ),
        blk AS (
            SELECT DISTINCT ON (b.room_id) b.room_id, b.block_type, b.reason
            FROM booking.room_blocks b
            WHERE b.property_id = :p AND b.status = 'active'
              AND b.start_date <= :b AND b.end_date >= :b
            ORDER BY b.room_id, (b.block_type = 'out_of_order') DESC
        )
        SELECT rm.code AS room, rm.building, rm.floor, rt.name AS room_type,
               COALESCE(rc.cleanliness, 'clean') AS cleanliness,
               occ.room_id IS NOT NULL AS occupied,
               blk.block_type, blk.reason AS block_reason,
               g.full_name AS guest, r.id AS reservation_id,
               ru.arrival_date, ru.departure_date, nxt.next_arrival
        FROM property.rooms rm
        JOIN property.room_types rt ON rt.id = rm.room_type_id
        LEFT JOIN operations.room_condition rc ON rc.room_id = rm.id
        LEFT JOIN occ ON occ.room_id = rm.id
        LEFT JOIN booking.reservation_units ru ON ru.id = occ.reservation_unit_id
        LEFT JOIN booking.reservations r ON r.id = ru.reservation_id
        LEFT JOIN engagement.guests g ON g.id = r.primary_guest_id
        LEFT JOIN nxt ON nxt.room_id = rm.id
        LEFT JOIN blk ON blk.room_id = rm.id
        WHERE rm.property_id = :p AND rm.status = 'active'
        ORDER BY rm.building NULLS LAST, rm.floor NULLS LAST, rm.code
        LIMIT :lim
    """)
    for r in rows:
        r["location"] = _location(r["building"], r["floor"])
        r["occupancy"] = (
            "Out of order" if r["block_type"] == "out_of_order"
            else "Occupied" if r["occupied"]
            else "Blocked" if r["block_type"]
            else "Vacant"
        )
        r["housekeeping"] = _label(r["cleanliness"])
    return rows


def _location(building: str | None, floor: str | None) -> str | None:
    parts = [building or None, f"Floor {floor}" if floor else None]
    return " · ".join(p for p in parts if p) or None


@report(
    number=21, slug="housekeeping-summary", title="Housekeeping Summary",
    category="Rooms & operations",
    description="Count rooms by housekeeping condition for each building and floor.",
    basis="Current status", dates="none",
    columns=(
        Col("building", "Building"),
        Col("floor", "Floor"),
        Col("dirty", "Dirty", "number"),
        Col("cleaning", "Being cleaned", "number"),
        Col("clean", "Clean", "number"),
        Col("inspected", "Inspected", "number"),
        Col("out_of_order", "Out of order", "number"),
        Col("occupied", "Occupied", "number"),
        Col("total", "Total rooms", "number"),
    ),
    metrics=(
        Metric("Dirty", "sum", "dirty", fmt="number"),
        Metric("Being cleaned", "sum", "cleaning", fmt="number"),
        Metric("Ready", "sumcols", ("clean", "inspected"), fmt="number",
               sub="Clean or inspected"),
        Metric("Rooms", "sum", "total", fmt="number"),
    ),
    filters=("building",),
    totals=("dirty", "cleaning", "clean", "inspected", "out_of_order",
            "occupied", "total"),
    note=(
        "Each active room's housekeeping condition right now, counted by "
        "building and floor. Dirty, being cleaned, clean and inspected add up "
        "to total rooms; out of order and occupied are counted as well as, not "
        "instead of, the room's condition."
    ),
    empty_text="This property has no active rooms.",
)
def _housekeeping_summary(ctx: Ctx) -> list[dict[str, Any]]:
    return ctx.rows("""
        SELECT COALESCE(NULLIF(rm.building, ''), '—') AS building,
               COALESCE(NULLIF(rm.floor, ''), '—') AS floor,
               count(*) FILTER (WHERE COALESCE(rc.cleanliness, 'clean') = 'dirty') AS dirty,
               count(*) FILTER (WHERE rc.cleanliness = 'cleaning') AS cleaning,
               count(*) FILTER (WHERE COALESCE(rc.cleanliness, 'clean') = 'clean') AS clean,
               count(*) FILTER (WHERE rc.cleanliness = 'inspected') AS inspected,
               count(*) FILTER (WHERE EXISTS (
                   SELECT 1 FROM booking.room_blocks b
                   WHERE b.room_id = rm.id AND b.status = 'active'
                     AND b.block_type = 'out_of_order'
                     AND b.start_date <= :b AND b.end_date >= :b)) AS out_of_order,
               count(*) FILTER (WHERE EXISTS (
                   SELECT 1 FROM booking.room_calendar_entries e
                   WHERE e.room_id = rm.id AND e.status <> 'released'
                     AND e.occupied_period @> now())) AS occupied,
               count(*) AS total
        FROM property.rooms rm
        LEFT JOIN operations.room_condition rc ON rc.room_id = rm.id
        WHERE rm.property_id = :p AND rm.status = 'active'
        GROUP BY 1, 2
        ORDER BY 1, 2
        LIMIT :lim
    """)


# ==========================================================================
# Tax & compliance
# ==========================================================================
@report(
    number=28, slug="police-inquiry-list", title="Police Inquiry List",
    category="Tax & compliance",
    description="List guests checked in, with nationality, address and identity document.",
    basis="Check-in date",
    columns=(
        Col("checked_in_at", "Checked in", "datetime"),
        Col("guest", "Guest"),
        Col("nationality", "Nationality"),
        Col("address", "Address"),
        Col("phone", "Phone"),
        Col("room", "Room"),
        Col("arrival_date", "Arrival", "date"),
        Col("departure_date", "Departure", "date"),
        Col("id_type", "ID type"),
        Col("id_number", "ID number"),
        Col("id_status", "ID check", "status"),
        Col("reservation", "Reservation"),
    ),
    metrics=(
        Metric("Guests checked in", "count", fmt="number"),
        Metric("IDs not verified", "count", fmt="number", where="id_status",
               value="Not verified"),
        Metric("Nationalities", "unique", "nationality", fmt="number"),
        Metric("Rooms", "unique", "room", fmt="number"),
    ),
    filters=("nationality", "id_status"),
    note=(
        "The primary guest of each room checked in during the window, dated by "
        "the property's local check-in time. ID numbers are masked to their "
        "last four characters; the full number is on the guest profile. Only "
        "the primary guest is recorded per reservation today, so accompanying "
        "guests are not listed and this is not yet a complete Form C register "
        "for foreign nationals."
    ),
    empty_text="No guest was checked in on these dates.",
    link="reservation_id",
)
def _police_list(ctx: Ctx) -> list[dict[str, Any]]:
    rows = ctx.rows("""
        SELECT ci.checked_in_at, g.full_name AS guest, g.nationality,
               g.address_line, g.city, g.state, g.country, g.phone,
               rm.code AS room, ru.arrival_date, ru.departure_date,
               g.id_type, g.id_number,
               (ci.id_verified OR g.id_verified_at IS NOT NULL) AS verified,
               r.id AS reservation_id, r.number AS reservation
        FROM booking.stay_checkins ci
        JOIN booking.reservation_units ru ON ru.id = ci.reservation_unit_id
        JOIN booking.reservations r ON r.id = ru.reservation_id
        LEFT JOIN engagement.guests g ON g.id = r.primary_guest_id
        LEFT JOIN property.rooms rm ON rm.id = ci.room_id
        WHERE ci.property_id = :p
          AND CAST(timezone(CAST(:tz AS text), ci.checked_in_at) AS date)
              BETWEEN :a AND :b
        ORDER BY ci.checked_in_at
        LIMIT :lim
    """)
    for r in rows:
        r["address"] = ", ".join(
            x for x in (r["address_line"], r["city"], r["state"], r["country"])
            if x) or None
        r["id_type"] = _label(r["id_type"])
        n = (r["id_number"] or "").strip()
        r["id_number"] = (("•" * max(len(n) - 4, 0)) + n[-4:]) if n else None
        r["id_status"] = "Verified" if r["verified"] else "Not verified"
    return rows


# ==========================================================================
# Management
# ==========================================================================
def _movement(ctx: Ctx) -> dict[str, Any]:
    return ctx.rows("""
        SELECT
          (SELECT count(*) FROM booking.reservation_units ru
            WHERE ru.property_id = :p AND ru.status NOT IN ('cancelled', 'no_show')
              AND ru.arrival_date BETWEEN :a AND :b) AS arrivals,
          (SELECT count(*) FROM booking.reservation_units ru
            WHERE ru.property_id = :p AND ru.status NOT IN ('cancelled', 'no_show')
              AND ru.departure_date BETWEEN :a AND :b) AS departures,
          (SELECT count(*) FROM booking.reservation_units ru
            WHERE ru.property_id = :p AND ru.status = 'no_show'
              AND ru.arrival_date BETWEEN :a AND :b) AS no_shows,
          (SELECT count(*) FROM booking.reservation_units ru
            WHERE ru.property_id = :p AND ru.status = 'cancelled'
              AND ru.arrival_date BETWEEN :a AND :b) AS cancellations,
          (SELECT count(*) FROM booking.stays s
            WHERE s.property_id = :p AND s.actual_checkin_at IS NOT NULL
              AND CAST(timezone(CAST(:tz AS text), s.actual_checkin_at) AS date) <= :b
              AND (s.actual_checkout_at IS NULL
                   OR CAST(timezone(CAST(:tz AS text), s.actual_checkout_at) AS date) > :b)
          ) AS in_house,
          (SELECT COALESCE(sum(bal), 0) FROM (
              SELECT sum(CASE WHEN e.entry_type = 'debit'
                              THEN e.amount ELSE -e.amount END) AS bal
              FROM finance.folio_entries e
              WHERE e.property_id = :p AND e.business_date <= :b
              GROUP BY e.folio_id) x
            WHERE bal > 0) AS owed
    """)[0]


@report(
    number=24, slug="manager-report", title="Manager Report",
    category="Management",
    description="The day's headline figures: rooms, revenue, money and guests.",
    columns=(
        Col("metric", "Metric"),
        Col("value", "Value"),
        Col("basis", "Basis / definition"),
    ),
    note=(
        "Every figure is read from the same records as the detailed report "
        "named beside it, for the same dates, so the two can be checked "
        "against each other."
    ),
)
def _manager(ctx: Ctx) -> list[dict[str, Any]]:
    available = sum(_available_nights(ctx).values())
    sold = sum(s["nights"] for s in _sold_nights(ctx))
    rooms = other = tax = Decimal(0)
    for e in _revenue_entries(ctx, limit=False):
        dept = _department(e["charge_type"])
        if dept == "Rooms":
            rooms += _signed(e)
        elif dept == "Taxes":
            tax += _signed(e)
        else:
            other += _signed(e)
    received = sum((p["amount"] for p in _payments(ctx, limit=False)), Decimal(0))
    refunded = sum((x["amount"] for x in _refunds(ctx, limit=False)
                    if x["status"] == "succeeded"), Decimal(0))
    mv = _movement(ctx)
    occ = _div(sold * 100, available)
    adr = _div(rooms, sold)
    revpar = _div(rooms, available)

    def row(metric: str, value: str, basis: str) -> dict[str, Any]:
        return {"metric": metric, "value": value, "basis": basis}

    return [
        row("Rooms available", f"{available:,}", "Room nights sellable: active rooms less out of order"),
        row("Room nights sold", f"{sold:,}", "Nights of reservation lines not cancelled or no-show"),
        row("Occupancy", "—" if occ is None else f"{occ:.1f}%", "Room nights sold ÷ rooms available"),
        row("Room revenue", _inr(rooms), "Room charges net of adjustments, before tax (Daily Revenue)"),
        row("ADR", "—" if adr is None else _inr(adr), "Room revenue ÷ room nights sold"),
        row("RevPAR", "—" if revpar is None else _inr(revpar), "Room revenue ÷ rooms available"),
        row("Other revenue", _inr(other), "All other departments, before tax (Daily Revenue)"),
        row("Tax posted", _inr(tax), "Tax charged on folios (Daily Revenue)"),
        row("Total revenue", _inr(rooms + other + tax), "Room + other revenue + tax"),
        row("Payments received", _inr(received), "Successful payments (Daily Receipt)"),
        row("Refunds paid", _inr(refunded), "Successful refunds (Daily Refund Report)"),
        row("Arrivals", f"{mv['arrivals']:,}", "Reservation lines arriving, excluding cancelled and no-shows"),
        row("Departures", f"{mv['departures']:,}", "Reservation lines departing, excluding cancelled and no-shows"),
        row("In-house at end of day", f"{mv['in_house']:,}", "Stays checked in and not yet checked out"),
        row("No-shows", f"{mv['no_shows']:,}", "Reservation lines marked no-show that were due to arrive"),
        row("Cancelled arrivals", f"{mv['cancellations']:,}", "Cancelled reservation lines that were due to arrive"),
        row("Owed by guests", _inr(mv["owed"]), "Folios with a debit balance at the end date (Folio List)"),
    ]


@report(
    number=36, slug="weekly-manager-report", title="Weekly Manager Report",
    category="Management",
    description="Occupancy, ADR, RevPAR and guest movement day by day across a week.",
    default_days=7,
    columns=(
        Col("business_date", "Date", "date"),
        Col("available", "Available nights", "number"),
        Col("sold", "Sold nights", "number"),
        Col("occupancy", "Occupancy", "percent"),
        Col("revenue", "Room revenue", "money"),
        Col("adr", "ADR", "money"),
        Col("revpar", "RevPAR", "money"),
        Col("arrivals", "Arrivals", "number"),
        Col("departures", "Departures", "number"),
    ),
    metrics=(
        Metric("Occupancy", "ratio", "sold", den="available", fmt="percent"),
        Metric("Room revenue", "sum", "revenue"),
        Metric("ADR", "ratio", "revenue", den="sold"),
        Metric("RevPAR", "ratio", "revenue", den="available"),
    ),
    totals=("available", "sold", "revenue", "arrivals", "departures"),
    note=_ROOM_NIGHT_NOTE,
)
def _weekly_manager(ctx: Ctx) -> list[dict[str, Any]]:
    avail: dict[date, int] = defaultdict(int)
    for (d, _), n in _available_nights(ctx).items():
        avail[d] += n
    sold: dict[date, int] = defaultdict(int)
    paid: dict[date, int] = defaultdict(int)
    for s in _sold_nights(ctx):
        sold[s["stay_date"]] += s["nights"]
        paid[s["stay_date"]] += s["paid_nights"]
    revenue: dict[date, Decimal] = defaultdict(Decimal)
    for e in _room_revenue(ctx):
        revenue[e["business_date"]] += _signed(e)
    moves = ctx.rows("""
        SELECT d, sum(arr) AS arrivals, sum(dep) AS departures FROM (
            SELECT arrival_date AS d, 1 AS arr, 0 AS dep
            FROM booking.reservation_units
            WHERE property_id = :p AND status NOT IN ('cancelled', 'no_show')
              AND arrival_date BETWEEN :a AND :b
            UNION ALL
            SELECT departure_date, 0, 1
            FROM booking.reservation_units
            WHERE property_id = :p AND status NOT IN ('cancelled', 'no_show')
              AND departure_date BETWEEN :a AND :b
        ) x GROUP BY d
    """)
    by_day = {m["d"]: m for m in moves}
    rows = []
    d = ctx.a
    while d <= ctx.b:
        m = by_day.get(d, {})
        rows.append({
            "business_date": d, "available": avail[d], "sold": sold[d],
            "occupancy": _div(sold[d] * 100, avail[d]),
            "revenue": revenue[d], "adr": _div(revenue[d], paid[d]),
            "revpar": _div(revenue[d], avail[d]),
            "arrivals": m.get("arrivals", 0), "departures": m.get("departures", 0),
        })
        d += timedelta(days=1)
    return rows


# ==========================================================================
# Deposits and receivables
# ==========================================================================
_ACCOUNT_KIND = {
    "company": "Company", "travel_agent": "Travel agent", "ota": "OTA",
    "government": "Government", "other": "Other",
}


@report(
    number=1, slug="advance-deposit-ledger", title="Advance Deposit Ledger",
    category="Finance & ledgers",
    description="Track scheduled deposits: what is due, what has come in and what is overdue.",
    basis="As of date", dates="single",
    columns=(
        Col("reservation", "Reservation"),
        Col("guest", "Guest"),
        Col("arrival_date", "Arrival", "date"),
        Col("installment", "Instalment"),
        Col("due_date", "Due date", "date"),
        Col("amount", "Due", "money"),
        Col("received", "Received", "money"),
        Col("waived", "Waived", "money"),
        Col("balance", "Balance", "money"),
        Col("status", "Status", "status"),
    ),
    metrics=(
        Metric("Scheduled", "sum", "amount"),
        Metric("Received", "sum", "received"),
        Metric("Balance due", "sum", "balance"),
        Metric("Overdue instalments", "count", fmt="number", where="status",
               value="Overdue"),
    ),
    filters=("status",),
    totals=("amount", "received", "waived", "balance"),
    note=(
        "Every deposit instalment scheduled on a reservation, as of the date. "
        "Received is the payments allocated to the instalment on or before that "
        "date, net of any reversed; a waiver counts only once it is approved. An "
        "instalment is overdue when its due date has passed with a balance left. "
        "Statuses follow the same rules as the Deposit Schedule screen. Security "
        "deposits taken at check-in are not scheduled instalments; the Hotel "
        "Ledger report shows them in its deposit ledger."
    ),
    empty_text="No deposit instalment had been scheduled on any reservation by this date.",
    link="reservation_id",
)
def _advance_deposits(ctx: Ctx) -> list[dict[str, Any]]:
    rows = ctx.rows("""
        SELECT i.id, i.seq, i.label, i.amount, i.due_date,
               i.status AS raw_status, i.waived_amount, i.waiver_status,
               r.id AS reservation_id, r.number AS reservation,
               g.full_name AS guest,
               (SELECT min(ru.arrival_date) FROM booking.reservation_units ru
                 WHERE ru.reservation_id = r.id) AS arrival_date,
               -- A reversal is recorded as a negative allocation, so the
               -- plain sum is what was actually kept.
               COALESCE((SELECT sum(a.amount) FROM finance.deposit_allocations a
                          WHERE a.installment_id = i.id
                            AND a.received_on <= :b), 0) AS received
        FROM finance.deposit_installments i
        LEFT JOIN booking.reservations r ON r.id = i.reservation_id
        LEFT JOIN engagement.guests g ON g.id = r.primary_guest_id
        WHERE i.property_id = :p
          AND CAST(timezone(CAST(:tz AS text), i.created_at) AS date) <= :b
        ORDER BY i.due_date NULLS LAST, r.number, i.seq
        LIMIT :lim
    """)
    for r in rows:
        amount, received = r["amount"], r["received"]
        waived = ((r["waived_amount"] or Decimal(0))
                  if r["waiver_status"] == "approved" else Decimal(0))
        if r["raw_status"] == "cancelled":
            status, balance = "Cancelled", Decimal(0)
        else:
            balance = max(amount - received - waived, Decimal(0))
            status = (
                "Waived" if waived >= amount and received <= 0
                else "Paid" if received + waived >= amount
                else "Overdue" if r["due_date"] and r["due_date"] < ctx.b
                else "Partially paid" if received + waived > 0
                else "Pending"
            )
        r.update(installment=r["label"] or f"Instalment {r['seq']}",
                 waived=waived, balance=balance, status=status)
    return rows


_BUCKETS = ("not_due", "d30", "d60", "d90", "d90p")


def _bucket(days_past_due: int) -> str:
    if days_past_due <= 0:
        return "not_due"
    if days_past_due <= 30:
        return "d30"
    if days_past_due <= 60:
        return "d60"
    if days_past_due <= 90:
        return "d90"
    return "d90p"


def _invoice_no(series: str | None, number: Any, issued_at: datetime | None) -> str:
    if not number:
        return "Draft"
    year = issued_at.year if issued_at else ""
    return f"{series or 'INV'}-{year}-{int(number):04d}"


def _receivables(ctx: Ctx) -> list[dict[str, Any]]:
    """Issued invoices still owed as of ``b``, each placed in an ageing bucket.

    The invoices screen subtracts a folio's payments from each of its invoices
    in full, which counts one payment twice once a folio carries a second
    invoice. Here payments settle a folio's invoices oldest first instead.
    """
    rows = ctx.rows("""
        SELECT i.id, i.folio_id, i.fiscal_series, i.invoice_number, i.issued_at,
               CAST(timezone(CAST(:tz AS text), i.issued_at) AS date) AS invoice_date,
               i.totals_snapshot, i.customer_snapshot,
               ca.name AS account_name, ca.kind AS account_kind,
               COALESCE(ca.credit_days, 0) AS credit_days,
               g.full_name AS guest, r.id AS reservation_id,
               COALESCE((SELECT sum(pa.amount) FROM finance.payment_allocations pa
                          WHERE pa.folio_id = i.folio_id
                            AND CAST(timezone(CAST(:tz AS text), pa.created_at) AS date) <= :b),
                        0) AS folio_received,
               COALESCE((SELECT sum(cn.amount) FROM finance.credit_notes cn
                          WHERE cn.invoice_id = i.id AND cn.status = 'issued'
                            AND CAST(timezone(CAST(:tz AS text), cn.issued_at) AS date) <= :b),
                        0) AS credited
        FROM finance.invoices i
        JOIN finance.folios f ON f.id = i.folio_id
        LEFT JOIN engagement.commercial_accounts ca ON ca.id = f.commercial_account_id
        LEFT JOIN booking.reservations r ON r.id = f.reservation_id
        LEFT JOIN engagement.guests g ON g.id = r.primary_guest_id
        WHERE i.property_id = :p AND i.status = 'issued' AND i.issued_at IS NOT NULL
          AND CAST(timezone(CAST(:tz AS text), i.issued_at) AS date) <= :b
        ORDER BY i.issued_at, i.id
    """)
    pool: dict[Any, Decimal] = {}
    out = []
    for r in rows:
        snap = r["totals_snapshot"] or {}
        total = Decimal(str(snap.get("total", 0)))
        owed = max(total - r["credited"], Decimal(0))
        left = pool.setdefault(r["folio_id"], r["folio_received"])
        paid = min(left, owed)
        pool[r["folio_id"]] = left - paid
        outstanding = owed - paid
        if outstanding <= 0:
            continue
        due = r["invoice_date"] + timedelta(days=int(r["credit_days"]))
        days = (ctx.b - due).days
        cust = r["customer_snapshot"] or {}
        row = {
            "account": r["account_name"] or cust.get("name") or r["guest"] or "Unknown",
            "bill_to": _ACCOUNT_KIND.get(r["account_kind"] or "", "Guest"),
            "invoice": _invoice_no(r["fiscal_series"], r["invoice_number"], r["issued_at"]),
            "invoice_date": r["invoice_date"], "due_date": due,
            "days": max(days, 0), "total": total, "outstanding": outstanding,
            "reservation_id": r["reservation_id"],
            **{b: Decimal(0) for b in _BUCKETS},
        }
        row[_bucket(days)] = outstanding
        out.append(row)
    return out


_AGEING_NOTE = (
    "Issued invoices with money still owed as of the date. The due date is the "
    "invoice date plus the company account's credit days; an invoice to a guest "
    "is due on issue. Payments on a folio settle its invoices oldest first, and "
    "issued credit notes reduce the invoice they were raised against. Drafts "
    "and cancelled invoices are not debts and are left out."
)

_AGEING_COLS = (
    Col("not_due", "Not due", "money"),
    Col("d30", "1–30 days", "money"),
    Col("d60", "31–60 days", "money"),
    Col("d90", "61–90 days", "money"),
    Col("d90p", "90+ days", "money"),
)


@report(
    number=2, slug="ageing-debtors-detail", title="Ageing Debtors - Detail",
    category="Finance & ledgers",
    description="Review each unpaid invoice and its age from the payment due date.",
    basis="As of date", dates="single",
    columns=(
        Col("account", "Account"),
        Col("bill_to", "Bill to"),
        Col("invoice", "Invoice"),
        Col("invoice_date", "Invoice date", "date"),
        Col("due_date", "Due date", "date"),
        Col("days", "Days past due", "number"),
        Col("total", "Invoice total", "money"),
        Col("outstanding", "Outstanding", "money"),
        *_AGEING_COLS,
    ),
    metrics=(
        Metric("Outstanding", "sum", "outstanding"),
        Metric("Not yet due", "sum", "not_due"),
        Metric("Over 60 days", "sumcols", ("d90", "d90p")),
        Metric("Unpaid invoices", "count", fmt="number"),
    ),
    filters=("account", "bill_to"),
    totals=("outstanding", *_BUCKETS),
    note=_AGEING_NOTE,
    empty_text="No issued invoice was still owed as of this date.",
    link="reservation_id",
)
def _ageing_detail(ctx: Ctx) -> list[dict[str, Any]]:
    return sorted(_receivables(ctx), key=lambda r: (r["account"], -r["days"]))


@report(
    number=3, slug="ageing-debtors-summary", title="Ageing Debtors - Summary",
    category="Finance & ledgers",
    description="Compare unpaid balances by account and ageing bucket.",
    basis="As of date", dates="single",
    columns=(
        Col("account", "Account"),
        Col("bill_to", "Bill to"),
        Col("invoices", "Invoices", "number"),
        *_AGEING_COLS,
        Col("outstanding", "Outstanding", "money"),
        Col("oldest", "Oldest (days past due)", "number"),
    ),
    metrics=(
        Metric("Outstanding", "sum", "outstanding"),
        Metric("Not yet due", "sum", "not_due"),
        Metric("Over 60 days", "sumcols", ("d90", "d90p")),
        Metric("Accounts owing", "count", fmt="number"),
    ),
    filters=("bill_to",),
    totals=("invoices", *_BUCKETS, "outstanding"),
    note=_AGEING_NOTE + " Totals here agree with Ageing Debtors - Detail for the same date.",
    empty_text="No issued invoice was still owed as of this date.",
)
def _ageing_summary(ctx: Ctx) -> list[dict[str, Any]]:
    by: dict[tuple[str, str], dict[str, Any]] = {}
    for r in _receivables(ctx):
        a = by.setdefault((r["account"], r["bill_to"]), {
            "account": r["account"], "bill_to": r["bill_to"], "invoices": 0,
            "outstanding": Decimal(0), "oldest": 0,
            **{b: Decimal(0) for b in _BUCKETS}})
        a["invoices"] += 1
        a["outstanding"] += r["outstanding"]
        a["oldest"] = max(a["oldest"], r["days"])
        for b in _BUCKETS:
            a[b] += r[b]
    return sorted(by.values(), key=lambda a: -a["outstanding"])


# ==========================================================================
# City ledger
# ==========================================================================
#: Exactly the Hotel Ledger's City / AR scope, so the two reports total the
#: same for the same dates.
_CITY_SCOPE = (
    "(f.type = 'company' OR f.commercial_account_id IS NOT NULL)"
    f" AND COALESCE(e.source_type, '') NOT IN {_DEPOSIT_SOURCES}"
    f" AND NOT ({_REFUND_OF_DEPOSIT})"
)
_NO_ACCOUNT = "Company folio with no account"
_CITY_NOTE = (
    "Folios billed to a company, travel agent or other commercial account, "
    "less deposits -- the same scope as the City / AR line of the Hotel Ledger "
    "report, which agrees with this total for the same dates."
)


@report(
    number=5, slug="city-ledger-detail", title="City Ledger - Detail",
    category="Finance & ledgers",
    description="Follow every debit and credit on company and agent accounts.",
    default_days=30,
    columns=(
        Col("business_date", "Business date", "date"),
        Col("account", "Account"),
        Col("account_type", "Account type"),
        Col("folio_no", "Folio"),
        Col("reservation", "Reservation"),
        Col("guest", "Guest"),
        Col("description", "Description"),
        Col("debit", "Debit", "money"),
        Col("credit", "Credit", "money"),
        Col("balance", "Account balance", "money"),
    ),
    metrics=(
        Metric("Postings", "count", fmt="number"),
        Metric("Debits", "sum", "debit"),
        Metric("Credits", "sum", "credit"),
        Metric("Net movement", "difference", ("debit", "credit")),
    ),
    filters=("account", "account_type"),
    totals=("debit", "credit"),
    note=_CITY_NOTE + " Account balance is that account's balance just after "
    "the posting, across all of its folios and its whole history.",
    empty_text="Nothing was posted to a company or agent account on these business dates.",
    link="reservation_id",
)
def _city_detail(ctx: Ctx) -> list[dict[str, Any]]:
    rows = ctx.rows(f"""
        WITH scoped AS (
            SELECT e.id, e.business_date, e.posted_at, e.entry_type, e.amount,
                   e.source_type, f.folio_no, f.reservation_id,
                   f.commercial_account_id
            FROM finance.folio_entries e
            JOIN finance.folios f ON f.id = e.folio_id
            WHERE e.property_id = :p AND e.business_date <= :b
              AND ({_CITY_SCOPE})
        ),
        running AS (
            SELECT s.id,
                   sum(CASE WHEN s.entry_type = 'debit' THEN s.amount ELSE -s.amount END)
                   OVER (PARTITION BY s.commercial_account_id
                         ORDER BY s.business_date, s.posted_at, s.id
                         ROWS UNBOUNDED PRECEDING) AS balance
            FROM scoped s
        )
        SELECT s.business_date, s.entry_type, s.amount, s.source_type, s.folio_no,
               ca.name AS account_name, ca.kind AS account_kind,
               r.id AS reservation_id, r.number AS reservation,
               g.full_name AS guest, rn.balance
        FROM scoped s
        JOIN running rn ON rn.id = s.id
        LEFT JOIN engagement.commercial_accounts ca ON ca.id = s.commercial_account_id
        LEFT JOIN booking.reservations r ON r.id = s.reservation_id
        LEFT JOIN engagement.guests g ON g.id = r.primary_guest_id
        WHERE s.business_date BETWEEN :a AND :b
        ORDER BY ca.name NULLS LAST, s.business_date, s.posted_at, s.id
        LIMIT :lim
    """)
    for r in rows:
        debit = r["entry_type"] == "debit"
        r["account"] = r["account_name"] or _NO_ACCOUNT
        r["account_type"] = _ACCOUNT_KIND.get(r["account_kind"] or "", "—")
        r["description"] = _describe(r["source_type"])
        r["debit"] = r["amount"] if debit else Decimal(0)
        r["credit"] = Decimal(0) if debit else r["amount"]
    return rows


@report(
    number=6, slug="city-ledger-summary", title="City Ledger - Summary",
    category="Finance & ledgers",
    description="Compare opening, movement and closing balance for each company account.",
    default_days=30,
    columns=(
        Col("account", "Account"),
        Col("account_type", "Account type"),
        Col("opening", "Opening", "money"),
        Col("debits", "Debits", "money"),
        Col("credits", "Credits", "money"),
        Col("closing", "Closing", "money"),
        Col("credit_limit", "Credit limit", "money"),
        Col("limit_status", "Credit limit check", "status"),
    ),
    metrics=(
        Metric("Opening", "sum", "opening"),
        Metric("Debits", "sum", "debits"),
        Metric("Credits", "sum", "credits"),
        Metric("Closing", "sum", "closing"),
    ),
    filters=("account_type", "limit_status"),
    totals=("opening", "debits", "credits", "closing"),
    note=_CITY_NOTE + " Closing = opening + debits − credits. Every active "
    "account is listed, including those with nothing posted yet.",
    empty_text="No commercial account exists yet. Create one under Guests › Company Accounts.",
)
def _city_summary(ctx: Ctx) -> list[dict[str, Any]]:
    moved = {r["account_id"]: r for r in ctx.rows(f"""
        SELECT f.commercial_account_id AS account_id,
               COALESCE(sum(CASE WHEN e.entry_type = 'debit' THEN e.amount ELSE -e.amount END)
                        FILTER (WHERE e.business_date < :a), 0) AS opening,
               COALESCE(sum(e.amount) FILTER (
                   WHERE e.entry_type = 'debit' AND e.business_date >= :a), 0) AS debits,
               COALESCE(sum(e.amount) FILTER (
                   WHERE e.entry_type = 'credit' AND e.business_date >= :a), 0) AS credits
        FROM finance.folio_entries e
        JOIN finance.folios f ON f.id = e.folio_id
        WHERE e.property_id = :p AND e.business_date <= :b AND ({_CITY_SCOPE})
        GROUP BY f.commercial_account_id
    """)}
    accounts = ctx.rows("""
        SELECT ca.id, ca.name, ca.kind, ca.credit_limit, ca.status
        FROM engagement.commercial_accounts ca
        WHERE ca.organization_id = (SELECT organization_id FROM iam.properties WHERE id = :p)
        ORDER BY ca.name
    """)
    zero = {"opening": Decimal(0), "debits": Decimal(0), "credits": Decimal(0)}
    rows = []
    for a in accounts:
        m = moved.pop(a["id"], None)
        if m is None and a["status"] != "active":
            continue
        figures = {k: (m or zero)[k] for k in zero}
        rows.append({"account": a["name"],
                     "account_type": _ACCOUNT_KIND.get(a["kind"] or "", "—"),
                     "credit_limit": a["credit_limit"], **figures})
    if None in moved:
        rows.append({"account": _NO_ACCOUNT, "account_type": "—", "credit_limit": None,
                     **{k: moved[None][k] for k in zero}})
    for r in rows:
        r["closing"] = r["opening"] + r["debits"] - r["credits"]
        r["limit_status"] = (
            "No limit set" if r["credit_limit"] is None
            else "Over limit" if r["closing"] > r["credit_limit"]
            else "Within limit"
        )
    return rows


# ==========================================================================
# Adjustments and tax
# ==========================================================================
@report(
    number=14, slug="detail-discount", title="Detail Discount Report",
    category="Revenue & sales",
    description="Review every discount and adjustment, with its reason and approver.",
    columns=(
        Col("business_date", "Business date", "date"),
        Col("folio_no", "Folio"),
        Col("guest", "Guest"),
        Col("department", "Department"),
        Col("charge", "Original charge", "money"),
        Col("kind", "Kind"),
        Col("amount", "Adjustment", "money"),
        Col("tax_amount", "Tax adjusted", "money"),
        Col("reason", "Reason"),
        Col("remarks", "Remarks"),
        Col("requested_by", "Requested by"),
        Col("approved_by", "Approved by"),
        Col("status", "Status", "status"),
    ),
    metrics=(
        Metric("Adjustments", "count", fmt="number"),
        Metric("Adjusted", "sum", "amount"),
        Metric("Tax adjusted", "sum", "tax_amount"),
        Metric("Discounts", "count", fmt="number", where="kind", value="Discount"),
    ),
    filters=("kind", "reason", "department", "status"),
    totals=("amount", "tax_amount"),
    note=(
        "Every folio adjustment -- corrections, discounts and allowances -- dated "
        "by the business date it was posted, or the property's local date it "
        "was raised if not posted yet. Pending and rejected adjustments are "
        "listed with their status; only posted ones reduced a folio, and those "
        "are the adjustments Daily Revenue nets off."
    ),
    empty_text="No folio adjustment was raised on these business dates.",
    link="reservation_id",
)
def _adjustments(ctx: Ctx) -> list[dict[str, Any]]:
    rows = ctx.rows("""
        SELECT * FROM (
            SELECT a.id, a.kind, a.status, a.amount, a.tax_amount, a.reason,
                   a.remarks, a.created_at, a.approval_required,
                   COALESCE(pe.business_date,
                            CAST(timezone(CAST(:tz AS text), a.created_at) AS date))
                       AS business_date,
                   ce.amount AS charge, ce.source_type AS charge_type,
                   f.folio_no, g.full_name AS guest,
                   r.id AS reservation_id,
                   cu.display_name AS requested_by,
                   CASE WHEN ar.status = 'approved' THEN ar.decided_by END AS approved_by
            FROM finance.folio_adjustments a
            JOIN finance.folios f ON f.id = a.folio_id
            LEFT JOIN finance.folio_entries ce ON ce.id = a.folio_entry_id
            LEFT JOIN finance.folio_entries pe ON pe.id = a.posted_entry_id
            LEFT JOIN booking.reservations r ON r.id = f.reservation_id
            LEFT JOIN engagement.guests g ON g.id = r.primary_guest_id
            LEFT JOIN iam.users cu ON cu.id = a.created_by
            LEFT JOIN iam.approval_requests ar ON ar.id = a.approval_request_id
            WHERE a.property_id = :p
        ) q
        WHERE q.business_date BETWEEN :a AND :b
        ORDER BY q.business_date, q.created_at, q.id
        LIMIT :lim
    """)
    for r in rows:
        r["department"] = _department(r["charge_type"])
        r["kind"] = KIND_LABELS.get(r["kind"]) or _label(r["kind"])
        r["reason"] = REASON_LABELS.get(r["reason"]) or _label(r["reason"])
        if not r["approved_by"] and not r["approval_required"]:
            r["approved_by"] = "Not required"
        r["status"] = _label(r["status"])
    return rows


_SUPPLY = {"intra_state": "Intra-state", "inter_state": "Inter-state"}


@report(
    number=18, slug="gst-india", title="GST - India",
    category="Tax & compliance",
    description="Break invoices down into taxable value, CGST, SGST and IGST.",
    basis="Invoice date", default_days=30,
    columns=(
        Col("doc_type", "Document"),
        Col("number", "Number"),
        Col("doc_date", "Date", "date"),
        Col("against", "Against invoice"),
        Col("recipient", "Recipient"),
        Col("gstin", "Recipient GSTIN"),
        Col("place", "Place of supply"),
        Col("supply", "Supply type"),
        Col("taxable", "Taxable value", "money"),
        Col("cgst", "CGST", "money"),
        Col("sgst", "SGST", "money"),
        Col("igst", "IGST", "money"),
        Col("other_tax", "Tax not split", "money"),
        Col("total", "Document total", "money"),
        Col("status", "Status", "status"),
    ),
    metrics=(
        Metric("Taxable value", "sum", "taxable"),
        Metric("GST", "sumcols", ("cgst", "sgst", "igst")),
        Metric("Tax not split", "sum", "other_tax"),
        Metric("Invoiced, net of credit notes", "sum", "total"),
    ),
    filters=("doc_type", "supply", "status"),
    totals=("taxable", "cgst", "sgst", "igst", "other_tax", "total"),
    note=(
        "Issued invoices and credit notes by the date they were issued, read "
        "from what each invoice recorded when it was issued, never recomputed. "
        "CGST and SGST apply when the place of supply is the property's own "
        "state and IGST when it is another; where the invoice could not decide "
        "that -- the property's GST registration or the customer's state was "
        "not recorded -- the tax is shown under Tax not split rather than "
        "guessed. Levies configured as other taxes are never GST and are shown "
        "there too. Cancelled invoices are listed at zero. Credit notes are "
        "shown at their gross amount; their tax is not split."
    ),
    empty_text=(
        "No invoice or credit note was issued on these dates. Invoices are "
        "issued from a folio under Finance › Invoices."
    ),
    link="reservation_id",
)
def _gst(ctx: Ctx) -> list[dict[str, Any]]:
    other_codes = {r["code"] for r in ctx.rows(
        "SELECT DISTINCT code FROM finance.tax_rules "
        "WHERE property_id = :p AND charge_type = 'other_tax'")}
    parties = """
        JOIN finance.folios f ON f.id = i.folio_id
        LEFT JOIN engagement.commercial_accounts ca ON ca.id = f.commercial_account_id
        LEFT JOIN booking.reservations r ON r.id = f.reservation_id
        LEFT JOIN engagement.guests g ON g.id = r.primary_guest_id
    """
    who = """ca.name AS account_name, ca.gstin AS account_gstin,
             ca.state AS account_state, g.full_name AS guest,
             g.state AS guest_state, r.id AS reservation_id"""
    invoices = ctx.rows(f"""
        SELECT i.fiscal_series, i.invoice_number, i.status, i.issued_at,
               CAST(timezone(CAST(:tz AS text), i.issued_at) AS date) AS doc_date,
               i.totals_snapshot, i.customer_snapshot, {who}
        FROM finance.invoices i {parties}
        WHERE i.property_id = :p AND i.status IN ('issued', 'cancelled')
          AND i.issued_at IS NOT NULL
          AND CAST(timezone(CAST(:tz AS text), i.issued_at) AS date) BETWEEN :a AND :b
        ORDER BY i.issued_at
        LIMIT :lim
    """)
    notes = ctx.rows(f"""
        SELECT cn.number AS note_number, cn.amount, cn.status,
               CAST(timezone(CAST(:tz AS text), cn.issued_at) AS date) AS doc_date,
               i.fiscal_series, i.invoice_number, i.issued_at,
               i.customer_snapshot, {who}
        FROM finance.credit_notes cn
        JOIN finance.invoices i ON i.id = cn.invoice_id {parties}
        WHERE cn.property_id = :p AND cn.issued_at IS NOT NULL
          AND CAST(timezone(CAST(:tz AS text), cn.issued_at) AS date) BETWEEN :a AND :b
        ORDER BY cn.issued_at
        LIMIT :lim
    """)

    def party(r: dict[str, Any]) -> dict[str, Any]:
        cust = r["customer_snapshot"] or {}
        return {"recipient": r["account_name"] or cust.get("name") or r["guest"],
                "gstin": cust.get("gstin") or r["account_gstin"],
                "place": cust.get("state") or r["account_state"] or r["guest_state"],
                "reservation_id": r["reservation_id"]}

    cent = Decimal("0.01")
    zero = Decimal(0)
    rows = []
    for r in invoices:
        snap = r["totals_snapshot"] or {}
        supply = (snap.get("tax_compliance") or {}).get("supply_type")
        gst = other = zero
        for t in snap.get("tax_lines") or []:
            amount = Decimal(str(t.get("tax_amount", 0)))
            if t.get("code") in other_codes:
                other += amount
            else:
                gst += amount
        live = r["status"] != "cancelled"
        intra, inter = supply == "intra_state", supply == "inter_state"
        cgst = (gst / 2).quantize(cent) if intra else zero
        rows.append({
            "doc_type": "Invoice",
            "number": _invoice_no(r["fiscal_series"], r["invoice_number"], r["issued_at"]),
            "doc_date": r["doc_date"], "against": None, **party(r),
            "supply": _SUPPLY.get(supply or "", "Not determined"),
            "taxable": Decimal(str(snap.get("subtotal", 0))) if live else zero,
            "cgst": cgst if live else zero,
            "sgst": (gst - cgst) if live and intra else zero,
            "igst": gst if live and inter else zero,
            "other_tax": (other + (zero if intra or inter else gst)) if live else zero,
            "total": Decimal(str(snap.get("total", 0))) if live else zero,
            "status": _label(r["status"]),
        })
    for n in notes:
        live = n["status"] == "issued"
        rows.append({
            "doc_type": "Credit note", "number": f"CN-{int(n['note_number']):04d}",
            "doc_date": n["doc_date"],
            "against": _invoice_no(n["fiscal_series"], n["invoice_number"], n["issued_at"]),
            **party(n), "supply": None, "taxable": None, "cgst": None,
            "sgst": None, "igst": None, "other_tax": None,
            "total": -n["amount"] if live else zero,
            "status": _label(n["status"]),
        })
    return sorted(rows, key=lambda r: (r["doc_date"], r["number"]))


@report(
    number=22, slug="luxury-tax-india", title="Luxury Tax Report - India",
    category="Tax & compliance",
    description="Review historical postings under a luxury tax code.",
    default_days=30,
    columns=(
        Col("business_date", "Posting date", "date"),
        Col("folio_no", "Folio"),
        Col("guest", "Guest / account"),
        Col("tax_code", "Tax code"),
        Col("rate", "Rate", "percent"),
        Col("taxable_amount", "Taxable amount", "money"),
        Col("tax_amount", "Tax posted", "money"),
    ),
    metrics=(
        Metric("Postings", "count", fmt="number"),
        Metric("Taxable amount", "sum", "taxable_amount"),
        Metric("Luxury tax", "sum", "tax_amount"),
        Metric("Folios", "unique", "folio_no", fmt="number"),
    ),
    filters=("tax_code",),
    totals=("taxable_amount", "tax_amount"),
    note=(
        "Tax lines posted under a tax code, or a tax rule, named as luxury tax. "
        "Luxury tax on hotel rooms was replaced by GST in July 2017, so this "
        "report is for postings retained from before then."
    ),
    empty_text=(
        "No luxury-tax postings for this period. Select a historical reporting "
        "period to review postings retained under a luxury tax code."
    ),
    link="reservation_id",
)
def _luxury_tax(ctx: Ctx) -> list[dict[str, Any]]:
    return ctx.rows("""
        SELECT e.business_date, f.folio_no, g.full_name AS guest,
               r.id AS reservation_id, t.tax_code, t.rate_snapshot AS rate,
               t.taxable_amount, t.tax_amount
        FROM finance.folio_entry_taxes t
        JOIN finance.folio_entries e ON e.id = t.folio_entry_id
        JOIN finance.folios f ON f.id = e.folio_id
        LEFT JOIN booking.reservations r ON r.id = f.reservation_id
        LEFT JOIN engagement.guests g ON g.id = r.primary_guest_id
        WHERE e.property_id = :p AND e.business_date BETWEEN :a AND :b
          AND (t.tax_code ILIKE '%luxury%'
               OR EXISTS (SELECT 1 FROM finance.tax_rules tr
                           WHERE tr.property_id = e.property_id
                             AND tr.code = t.tax_code
                             AND tr.name ILIKE '%luxury%'))
        ORDER BY e.business_date, e.posted_at
        LIMIT :lim
    """)


# ==========================================================================
# Room blocks
# ==========================================================================
@report(
    number=23, slug="maintenance-block", title="Maintenance Block",
    category="Rooms & operations",
    description="List rooms taken out of order or held, for how long and why.",
    basis="Block dates", default_days=30,
    columns=(
        Col("room", "Room"),
        Col("room_type", "Room type"),
        Col("block_type", "Block type", "status"),
        Col("category", "Reason category"),
        Col("reason", "Reason"),
        Col("severity", "Severity"),
        Col("start_date", "From", "date"),
        Col("end_date", "To", "date"),
        Col("nights", "Room nights in window", "number"),
        Col("status", "Status", "status"),
        Col("reference", "Reference"),
        Col("created_by", "Created by"),
    ),
    metrics=(
        Metric("Blocks", "count", fmt="number"),
        Metric("Rooms affected", "unique", "room", fmt="number"),
        Metric("Room nights blocked", "sum", "nights", fmt="number"),
        Metric("Out of order", "count", fmt="number", where="block_type",
               value="Out of order"),
    ),
    filters=("block_type", "category", "severity", "status"),
    totals=("nights",),
    note=(
        "Every room block that overlaps the window, whether active, ended or "
        "cancelled. Room nights count only the nights inside the window, and a "
        "block's end date is its last blocked night; a cancelled block counts "
        "none. Out-of-order blocks are taken off available rooms in the "
        "occupancy reports; blocks held for a group are not, since they can "
        "still be sold to that group."
    ),
    empty_text="No room was blocked or out of order on these dates.",
)
def _blocks(ctx: Ctx) -> list[dict[str, Any]]:
    rows = ctx.rows("""
        SELECT rm.code AS room, rt.name AS room_type, b.block_type,
               b.reason_category, b.reason, b.severity, b.start_date,
               b.end_date, b.status, b.linked_reference AS reference,
               u.display_name AS created_by
        FROM booking.room_blocks b
        JOIN property.rooms rm ON rm.id = b.room_id
        JOIN property.room_types rt ON rt.id = rm.room_type_id
        LEFT JOIN iam.users u ON u.id = b.created_by
        WHERE b.property_id = :p AND b.start_date <= :b AND b.end_date >= :a
        ORDER BY b.start_date, rm.code
        LIMIT :lim
    """)
    for r in rows:
        r["nights"] = (0 if r["status"] == "cancelled"
                       else (min(r["end_date"], ctx.b) - max(r["start_date"], ctx.a)).days + 1)
        r["block_type"] = {"out_of_order": "Out of order",
                           "room_block": "Room block"}.get(r["block_type"], _label(r["block_type"]))
        r["category"] = _label(r["reason_category"])
        r["severity"] = _label(r["severity"])
        r["status"] = _label(r["status"])
    return rows


# ==========================================================================
# Meals
# ==========================================================================
_MEALS = ("breakfast", "lunch", "dinner")

#: What each meal plan code includes. Half board is taken as breakfast and
#: dinner, the usual reading; the property's own plans describe it only as
#: "breakfast and one further meal".
_MEALS_BY_CODE: dict[str, tuple[str, ...]] = {
    "RO": (), "EP": (),
    "BB": ("breakfast",), "CP": ("breakfast",),
    "HB": ("breakfast", "dinner"), "MAP": ("breakfast", "dinner"),
    "FB": _MEALS, "AP": _MEALS, "AI": _MEALS,
}


def _meals(code: str | None, name: str | None) -> tuple[str, ...]:
    c = (code or "").strip().upper()
    if c in _MEALS_BY_CODE:
        return _MEALS_BY_CODE[c]
    n = (name or "").lower()
    if "inclusive" in n or "full board" in n:
        return _MEALS
    if "half board" in n:
        return ("breakfast", "dinner")
    if "breakfast" in n:
        return ("breakfast",)
    return ()


def _meal_stays(ctx: Ctx, first: date, last: date) -> list[dict[str, Any]]:
    return ctx.rows("""
        SELECT ru.id, ru.arrival_date, ru.departure_date,
               COALESCE(ru.adults, 0) AS adults, COALESCE(ru.children, 0) AS children,
               rm.code AS room, g.full_name AS guest,
               r.id AS reservation_id, r.number AS reservation,
               mp.code AS plan_code, mp.name AS plan_name
        FROM booking.reservation_units ru
        JOIN booking.reservations r ON r.id = ru.reservation_id
        LEFT JOIN engagement.guests g ON g.id = r.primary_guest_id
        LEFT JOIN property.rooms rm ON rm.id = ru.assigned_room_id
        LEFT JOIN property.rate_plans rp ON rp.id = ru.rate_plan_id
        LEFT JOIN property.meal_plans mp
               ON mp.id = COALESCE(ru.meal_plan_id, rp.meal_plan_id)
        WHERE ru.property_id = :p
          AND ru.status NOT IN ('cancelled', 'no_show')
          AND ru.arrival_date <= :last AND ru.departure_date >= :first
        ORDER BY rm.code NULLS LAST, r.number
    """, first=first, last=last)


def _served(stay: dict[str, Any], meal: str, on: date) -> bool:
    """Breakfast goes to whoever slept here the night before; lunch and dinner
    to whoever sleeps here tonight."""
    if meal not in _meals(stay["plan_code"], stay["plan_name"]):
        return False
    if meal == "breakfast":
        return stay["arrival_date"] < on <= stay["departure_date"]
    return stay["arrival_date"] <= on < stay["departure_date"]


_MEAL_NOTE = (
    "Covers are the adults and children booked on each room line whose meal "
    "plan includes the meal -- the plan on the line itself, or else the one on "
    "its rate plan. Breakfast is counted for guests who stayed the night "
    "before, lunch and dinner for guests staying that night. Half board is "
    "counted as breakfast and dinner. Cancelled and no-show lines are left out."
)


@report(
    number=25, slug="meal-plan", title="Meal Plan",
    category="Meals",
    description="List in-house guests with their meal plan and covers by meal.",
    basis="Service date", dates="single",
    columns=(
        Col("room", "Room"),
        Col("guest", "Guest"),
        Col("reservation", "Reservation"),
        Col("plan", "Meal plan"),
        Col("adults", "Adults", "number"),
        Col("children", "Children", "number"),
        Col("breakfast", "Breakfast", "number"),
        Col("lunch", "Lunch", "number"),
        Col("dinner", "Dinner", "number"),
        Col("arrival_date", "Arrival", "date"),
        Col("departure_date", "Departure", "date"),
    ),
    metrics=(
        Metric("Breakfast covers", "sum", "breakfast", fmt="number"),
        Metric("Lunch covers", "sum", "lunch", fmt="number"),
        Metric("Dinner covers", "sum", "dinner", fmt="number"),
        Metric("Rooms on a meal plan", "count", fmt="number", where="plan",
               value="No meal plan", negate=True),
    ),
    filters=("plan",),
    totals=("adults", "children", "breakfast", "lunch", "dinner"),
    note=_MEAL_NOTE + " Every room in house that day is listed, with or without a plan.",
    empty_text="No guest is in house on this date.",
    link="reservation_id",
)
def _meal_plan(ctx: Ctx) -> list[dict[str, Any]]:
    rows = []
    for s in _meal_stays(ctx, ctx.b, ctx.b):
        covers = s["adults"] + s["children"]
        rows.append({
            **s, "plan": s["plan_name"] or "No meal plan",
            **{m: covers if _served(s, m, ctx.b) else 0 for m in _MEALS},
        })
    return rows


@report(
    number=26, slug="meal-planner", title="Meal Planner",
    category="Meals",
    description="Plan covers for each meal service from in-house meal plans.",
    basis="Service date", dates="single",
    columns=(
        Col("meal", "Meal"),
        Col("rooms", "Rooms", "number"),
        Col("adults", "Adults", "number"),
        Col("children", "Children", "number"),
        Col("covers", "Covers", "number"),
        Col("plans", "From plans"),
    ),
    metrics=(
        Metric("Breakfast", "sum", "covers", fmt="number", where="meal", value="Breakfast"),
        Metric("Lunch", "sum", "covers", fmt="number", where="meal", value="Lunch"),
        Metric("Dinner", "sum", "covers", fmt="number", where="meal", value="Dinner"),
        Metric("All covers", "sum", "covers", fmt="number"),
    ),
    totals=("adults", "children", "covers"),
    note=_MEAL_NOTE + " Totals here agree with the Meal Plan report for the same date.",
    empty_text="No guest is in house on this date.",
)
def _meal_planner(ctx: Ctx) -> list[dict[str, Any]]:
    stays = _meal_stays(ctx, ctx.b, ctx.b)
    rows = []
    for meal in _MEALS:
        served = [s for s in stays if _served(s, meal, ctx.b)]
        plans: dict[str, int] = defaultdict(int)
        for s in served:
            plans[s["plan_code"] or s["plan_name"] or "?"] += 1
        rows.append({
            "meal": meal.capitalize(), "rooms": len(served),
            "adults": sum(s["adults"] for s in served),
            "children": sum(s["children"] for s in served),
            "covers": sum(s["adults"] + s["children"] for s in served),
            "plans": " · ".join(f"{k} {v}" for k, v in sorted(plans.items())) or None,
        })
    return rows


@report(
    number=37, slug="weekly-meal-plan", title="Weekly Meal Plan Report",
    category="Meals",
    description="Forecast breakfast, lunch and dinner covers for the week ahead.",
    basis="Service date", default_days=7, forward=True,
    columns=(
        Col("service_date", "Service date", "date"),
        Col("breakfast_adults", "Breakfast adults", "number"),
        Col("breakfast_children", "Breakfast children", "number"),
        Col("lunch_adults", "Lunch adults", "number"),
        Col("lunch_children", "Lunch children", "number"),
        Col("dinner_adults", "Dinner adults", "number"),
        Col("dinner_children", "Dinner children", "number"),
        Col("covers", "Total covers", "number"),
    ),
    metrics=(
        Metric("Breakfast covers", "sumcols", ("breakfast_adults", "breakfast_children"), fmt="number"),
        Metric("Lunch covers", "sumcols", ("lunch_adults", "lunch_children"), fmt="number"),
        Metric("Dinner covers", "sumcols", ("dinner_adults", "dinner_children"), fmt="number"),
        Metric("All covers", "sum", "covers", fmt="number"),
    ),
    totals=("breakfast_adults", "breakfast_children", "lunch_adults",
            "lunch_children", "dinner_adults", "dinner_children", "covers"),
    note=_MEAL_NOTE + " Future dates are a forecast from reservations as they "
    "stand now, and change as bookings do.",
    empty_text="No guest is booked in house on these dates.",
)
def _weekly_meals(ctx: Ctx) -> list[dict[str, Any]]:
    stays = _meal_stays(ctx, ctx.a, ctx.b)
    rows = []
    d = ctx.a
    while d <= ctx.b:
        row: dict[str, Any] = {"service_date": d, "covers": 0}
        for meal in _MEALS:
            served = [s for s in stays if _served(s, meal, d)]
            row[f"{meal}_adults"] = sum(s["adults"] for s in served)
            row[f"{meal}_children"] = sum(s["children"] for s in served)
            row["covers"] += row[f"{meal}_adults"] + row[f"{meal}_children"]
        rows.append(row)
        d += timedelta(days=1)
    return rows


# ==========================================================================
# Travel agents
# ==========================================================================
_COMMISSION_NOTE = (
    "Reservations brought by a travel agent or online channel -- through the "
    "reservation's business source, or a travel agent or OTA company account "
    "-- that depart in the window and were not cancelled or no-shows. "
    "Commission is earned on room revenue posted to the reservation's folios, "
    "net of adjustments and before tax, at the company account's commission "
    "rate or else the rate on the partner's channel connection. Commission "
    "payouts are not recorded in this system, so paid and outstanding are not "
    "shown; Settlement says who holds the commission."
)


def _commissions(ctx: Ctx) -> list[dict[str, Any]]:
    rows = ctx.rows(f"""
        WITH res AS (
            SELECT r.id, r.number, g.full_name AS guest,
                   min(ru.arrival_date) AS arrival_date,
                   max(ru.departure_date) AS departure_date,
                   COALESCE(sum(GREATEST(ru.departure_date - ru.arrival_date, 0))
                            FILTER (WHERE ru.status NOT IN ('cancelled', 'no_show')), 0)
                       AS nights,
                   ba.name AS source_name, ba.partner_type,
                   cc.commission_percent AS channel_rate, cc.payment_model,
                   ca.name AS account_name, ca.kind AS account_kind,
                   ca.commission_percent AS account_rate
            FROM booking.reservations r
            JOIN booking.reservation_units ru ON ru.reservation_id = r.id
            LEFT JOIN engagement.guests g ON g.id = r.primary_guest_id
            LEFT JOIN engagement.booking_attributes ba ON ba.id = r.business_source_id
            LEFT JOIN distribution.channel_connections cc
                   ON cc.partner_id = ba.id AND cc.property_id = r.property_id
            LEFT JOIN engagement.commercial_accounts ca ON ca.id = r.commercial_account_id
            WHERE r.property_id = :p
              AND (ba.partner_type IN ('travel_agent', 'online_channel')
                   OR ca.kind IN ('travel_agent', 'ota'))
            GROUP BY r.id, r.number, g.full_name, ba.name, ba.partner_type,
                     cc.commission_percent, cc.payment_model, ca.name, ca.kind,
                     ca.commission_percent
        )
        SELECT res.*,
               COALESCE((
                   SELECT sum(CASE WHEN e.entry_type = 'debit' THEN e.amount ELSE -e.amount END)
                   FROM finance.folio_entries e
                   JOIN finance.folios f ON f.id = e.folio_id
                   LEFT JOIN finance.folio_entries o ON o.id = e.reversal_of_id
                   WHERE f.reservation_id = res.id AND {_REVENUE}
                     AND COALESCE(o.source_type, e.source_type)
                         IN ('room_night', 'room_stay', 'room_upgrade', 'room_move')
               ), 0) AS eligible
        FROM res
        WHERE res.nights > 0 AND res.departure_date BETWEEN :a AND :b
        ORDER BY res.departure_date, res.number
        LIMIT :lim
    """)
    for r in rows:
        agent = r["account_kind"] == "travel_agent" or (
            r["account_kind"] is None and r["partner_type"] == "travel_agent")
        rate = r["account_rate"] if r["account_rate"] is not None else r["channel_rate"]
        r.update(
            reservation=r["number"],
            partner=r["account_name"] or r["source_name"],
            partner_type="Travel agent" if agent else "Online channel",
            rate=rate,
            commission=((r["eligible"] * rate / 100).quantize(Decimal("0.01"))
                        if rate is not None else None),
            settlement=(
                "No rate set" if rate is None
                else "Deducted by channel"
                if r["payment_model"] == "channel_collect" and r["account_name"] is None
                else "Payable to partner"
            ),
        )
    return rows


@report(
    number=34, slug="travel-agent-commission-detail",
    title="Travel Agent Commission - Detail",
    category="Travel agents",
    description="Calculate commission for each agent and channel booking.",
    basis="Departure date", default_days=30,
    columns=(
        Col("departure_date", "Departure", "date"),
        Col("reservation", "Reservation"),
        Col("partner", "Travel agent / channel"),
        Col("partner_type", "Partner type"),
        Col("guest", "Guest"),
        Col("arrival_date", "Arrival", "date"),
        Col("nights", "Room nights", "number"),
        Col("eligible", "Eligible room revenue", "money"),
        Col("rate", "Rate", "percent"),
        Col("commission", "Commission", "money"),
        Col("settlement", "Settlement", "status"),
    ),
    metrics=(
        Metric("Bookings", "count", fmt="number"),
        Metric("Eligible room revenue", "sum", "eligible"),
        Metric("Commission", "sum", "commission"),
        Metric("Effective rate", "ratio", "commission", den="eligible", fmt="percent"),
    ),
    filters=("partner", "partner_type", "settlement"),
    totals=("nights", "eligible", "commission"),
    note=_COMMISSION_NOTE,
    empty_text=(
        "No booking from a travel agent or online channel departed on these "
        "dates. A booking counts once its business source is a travel agent or "
        "channel, or it is billed to a travel agent account."
    ),
    link="id",
)
def _commission_detail(ctx: Ctx) -> list[dict[str, Any]]:
    return _commissions(ctx)


@report(
    number=35, slug="travel-agent-commission-summary",
    title="Travel Agent Commission - Summary",
    category="Travel agents",
    description="Compare bookings, revenue and commission earned by each agent and channel.",
    basis="Departure date", default_days=30,
    columns=(
        Col("partner", "Travel agent / channel"),
        Col("partner_type", "Partner type"),
        Col("bookings", "Bookings", "number"),
        Col("nights", "Room nights", "number"),
        Col("eligible", "Eligible room revenue", "money"),
        Col("commission", "Commission earned", "money"),
        Col("rate", "Effective rate", "percent"),
        Col("settlement", "Settlement", "status"),
    ),
    metrics=(
        Metric("Bookings", "sum", "bookings", fmt="number"),
        Metric("Eligible room revenue", "sum", "eligible"),
        Metric("Commission earned", "sum", "commission"),
        Metric("Effective rate", "ratio", "commission", den="eligible", fmt="percent"),
    ),
    filters=("partner_type", "settlement"),
    totals=("bookings", "nights", "eligible", "commission"),
    note=_COMMISSION_NOTE + " Totals here agree with the detail report for the same dates.",
    empty_text="No booking from a travel agent or online channel departed on these dates.",
)
def _commission_summary(ctx: Ctx) -> list[dict[str, Any]]:
    by: dict[tuple[Any, str], dict[str, Any]] = {}
    for r in _commissions(ctx):
        p = by.setdefault((r["partner"], r["partner_type"]), {
            "partner": r["partner"], "partner_type": r["partner_type"],
            "bookings": 0, "nights": 0, "eligible": Decimal(0),
            "commission": Decimal(0), "settlements": set()})
        p["bookings"] += 1
        p["nights"] += r["nights"]
        p["eligible"] += r["eligible"]
        p["commission"] += r["commission"] or Decimal(0)
        p["settlements"].add(r["settlement"])
    rows = []
    for p in by.values():
        s = p.pop("settlements")
        rows.append({**p, "rate": _div(p["commission"] * 100, p["eligible"]),
                     "settlement": s.pop() if len(s) == 1 else "Mixed"})
    return sorted(rows, key=lambda r: -r["commission"])


# ==========================================================================
# Complimentary stays, expenses, owners and work orders
# ==========================================================================
_ROOM_CHARGES = ("room_night", "room_stay", "room_upgrade", "room_move")


#: How a comp reason reads on the report. The codes are defined in
#: booking_core/comp_routes.py; these are the words a manager reads.
_COMP_REASON_LABELS = {
    "vip": "VIP guest", "fam_trip": "Agent familiarisation trip",
    "tour_leader": "Tour leader on a group",
    "service_recovery": "Putting right a bad stay",
    "owner": "Owner or management", "marketing": "Marketing or influencer",
    "loyalty": "Loyalty redemption",
    "maintenance": "Maintenance or repair", "office": "Office or back-of-house",
    "staff_accommodation": "Staff accommodation", "show_room": "Show room",
    "other": "Other",
}

@report(
    number=7, slug="complimentary-room", title="Complimentary Room Report",
    category="Rooms & operations",
    description="List complimentary stays and upgrades, why they were given and who approved them.",
    basis="Stay dates", default_days=30,
    columns=(
        Col("room", "Room"),
        Col("guest", "Guest"),
        Col("reservation", "Reservation"),
        Col("room_type", "Room type"),
        Col("arrival_date", "Arrival", "date"),
        Col("departure_date", "Departure", "date"),
        Col("nights", "Room nights", "number"),
        Col("basis", "Complimentary as"),
        Col("value", "Value given", "money"),
        Col("reason", "Reason"),
        Col("approved_by", "Approved by"),
    ),
    metrics=(
        Metric("Complimentary stays", "count", fmt="number"),
        Metric("Room nights", "sum", "nights", fmt="number"),
        Metric("Value given", "sum", "value"),
        Metric("Upgrades", "count", fmt="number", where="basis",
               value="Complimentary upgrade"),
    ),
    filters=("basis", "reason"),
    totals=("nights", "value"),
    note=(
        "A room declared complimentary or house use on the booking is listed "
        "first, with the reason and the person who authorised it. Stays that "
        "were never declared are still found from what was recorded, three "
        "ways: a room line booked at a zero nightly rate; a stay whose room "
        "charges were adjusted off in full, with the reason and approver of "
        "that adjustment; and a room move recorded as a complimentary "
        "upgrade, valued at the rate difference over the nights it applied. "
        "An inferred zero-rate stay has no posted value and no approver to "
        "show -- which is the reason to declare it instead."
    ),
    empty_text="No complimentary stay or upgrade was recorded for these dates.",
    link="reservation_id",
)

def _complimentary(ctx: Ctx) -> list[dict[str, Any]]:
    rooms = _in(_ROOM_CHARGES)
    rows: list[dict[str, Any]] = []
    declared: set[Any] = set()

    # Declared, first. A room somebody marked complimentary says so, says why,
    # and says who decided -- none of which can be inferred from a rate.
    for r in ctx.rows("""
        SELECT rm.code AS room, g.full_name AS guest, r.number AS reservation,
               r.id AS reservation_id, rt.name AS room_type,
               ru.id AS unit_id, ru.comp_kind, ru.comp_reason, ru.comp_note,
               u.display_name AS approved_by,
               ru.arrival_date, ru.departure_date,
               LEAST(ru.departure_date, CAST(:b AS date) + 1)
                   - GREATEST(ru.arrival_date, CAST(:a AS date)) AS nights,
               -- The same fallback the folio uses to value a stay before
               -- anything is posted (see folio_money.py). Most bookings never
               -- get an explicit nightly_rate written on the unit -- the price
               -- comes from the room type -- so reading nightly_rate alone
               -- left this report's one important number empty on the
               -- majority of rooms it listed.
               COALESCE(ru.nightly_rate, rt.base_rate) AS nightly_rate
        FROM booking.reservation_units ru
        JOIN booking.reservations r ON r.id = ru.reservation_id
        LEFT JOIN engagement.guests g ON g.id = r.primary_guest_id
        LEFT JOIN property.rooms rm ON rm.id = ru.assigned_room_id
        LEFT JOIN property.room_types rt ON rt.id = ru.room_type_id
        LEFT JOIN iam.users u ON u.id = ru.comp_authorised_by
        WHERE ru.property_id = :p AND ru.status NOT IN ('cancelled', 'no_show')
          AND ru.comp_kind IS NOT NULL
          AND ru.arrival_date <= :b AND ru.departure_date > :a
        ORDER BY ru.arrival_date
        LIMIT :lim
    """):
        declared.add(r["unit_id"])
        reason = _COMP_REASON_LABELS.get(r["comp_reason"], r["comp_reason"])
        rows.append({
            **{k: v for k, v in r.items()
               if k not in ("unit_id", "comp_kind", "comp_reason", "comp_note",
                            "nightly_rate")},
            "basis": ("House use" if r["comp_kind"] == "house_use"
                      else "Complimentary"),
            # What the room would have earned: the rate it was sold at, or
            # the room type's rate where the unit carries none. Null only when
            # neither exists, which is a room type with no price set.
            "value": (Decimal(r["nightly_rate"]) * int(r["nights"] or 0)
                      if r["nightly_rate"] else None),
            "reason": " — ".join(x for x in (reason, r["comp_note"]) if x),
            "approved_by": r["approved_by"],
        })

    # Then the old shapes, for stays that pre-date the flag. Dropping these
    # would quietly empty the report of its own history.
    for r in ctx.rows("""
        SELECT rm.code AS room, g.full_name AS guest, r.number AS reservation,
               r.id AS reservation_id, rt.name AS room_type,
               ru.id AS unit_id,
               ru.arrival_date, ru.departure_date, r.remarks,
               LEAST(ru.departure_date, CAST(:b AS date) + 1)
                   - GREATEST(ru.arrival_date, CAST(:a AS date)) AS nights
        FROM booking.reservation_units ru
        JOIN booking.reservations r ON r.id = ru.reservation_id
        LEFT JOIN engagement.guests g ON g.id = r.primary_guest_id
        LEFT JOIN property.rooms rm ON rm.id = ru.assigned_room_id
        LEFT JOIN property.room_types rt ON rt.id = ru.room_type_id
        WHERE ru.property_id = :p AND ru.status NOT IN ('cancelled', 'no_show')
          AND ru.nightly_rate = 0 AND ru.comp_kind IS NULL
          AND ru.arrival_date <= :b AND ru.departure_date > :a
        ORDER BY ru.arrival_date
        LIMIT :lim
    """):
        if r["unit_id"] in declared:
            continue
        rows.append({**{k: v for k, v in r.items() if k != "unit_id"},
                     "basis": "Zero room rate (not declared)", "value": None,
                     "reason": r["remarks"], "approved_by": None})

    for r in ctx.rows(f"""
        WITH res AS (
            SELECT DISTINCT ru.reservation_id
            FROM booking.reservation_units ru
            WHERE ru.property_id = :p AND ru.status NOT IN ('cancelled', 'no_show')
              AND ru.arrival_date <= :b AND ru.departure_date > :a
        ),
        charges AS (
            SELECT f.reservation_id,
                   COALESCE(sum(e.amount) FILTER (
                       WHERE e.entry_type = 'debit'
                         AND COALESCE(e.source_type, '') IN {rooms}), 0) AS gross,
                   COALESCE(sum(e.amount) FILTER (
                       WHERE e.entry_type = 'credit'
                         AND COALESCE(o.source_type, '') IN {rooms}), 0) AS waived
            FROM finance.folio_entries e
            JOIN finance.folios f ON f.id = e.folio_id
            LEFT JOIN finance.folio_entries o ON o.id = e.reversal_of_id
            WHERE f.reservation_id IN (SELECT reservation_id FROM res)
            GROUP BY f.reservation_id
        )
        SELECT c.reservation_id, c.waived AS value, r.number AS reservation,
               g.full_name AS guest, u.room, u.room_type, u.arrival_date,
               u.departure_date, u.nights, adj.reason, adj.approved_by
        FROM charges c
        JOIN booking.reservations r ON r.id = c.reservation_id
        LEFT JOIN engagement.guests g ON g.id = r.primary_guest_id
        LEFT JOIN LATERAL (
            SELECT min(rm.code) AS room, min(rt.name) AS room_type,
                   min(ru.arrival_date) AS arrival_date,
                   max(ru.departure_date) AS departure_date,
                   sum(ru.departure_date - ru.arrival_date) AS nights
            FROM booking.reservation_units ru
            LEFT JOIN property.rooms rm ON rm.id = ru.assigned_room_id
            LEFT JOIN property.room_types rt ON rt.id = ru.room_type_id
            WHERE ru.reservation_id = c.reservation_id
              AND ru.status NOT IN ('cancelled', 'no_show')
        ) u ON TRUE
        LEFT JOIN LATERAL (
            SELECT a.reason,
                   CASE WHEN ar.status = 'approved' THEN ar.decided_by END AS approved_by
            FROM finance.folio_adjustments a
            JOIN finance.folios f2 ON f2.id = a.folio_id
            LEFT JOIN iam.approval_requests ar ON ar.id = a.approval_request_id
            WHERE f2.reservation_id = c.reservation_id AND a.status = 'posted'
            ORDER BY a.amount DESC
            LIMIT 1
        ) adj ON TRUE
        WHERE c.gross > 0 AND c.waived >= c.gross
        LIMIT :lim
    """):
        rows.append({**r, "basis": "Charges adjusted off",
                     "reason": REASON_LABELS.get(r["reason"] or "") or _label(r["reason"])})

    for r in ctx.rows("""
        SELECT tr.code AS room, g.full_name AS guest, r.number AS reservation,
               r.id AS reservation_id, trt.name AS room_type,
               ru.arrival_date, ru.departure_date, mv.nights_applicable AS nights,
               mv.rate_difference, mv.remarks, du.display_name AS approved_by
        FROM booking.room_moves mv
        JOIN booking.reservation_units ru ON ru.id = mv.reservation_unit_id
        JOIN booking.reservations r ON r.id = ru.reservation_id
        LEFT JOIN engagement.guests g ON g.id = r.primary_guest_id
        LEFT JOIN property.rooms tr ON tr.id = mv.to_room_id
        LEFT JOIN property.room_types trt ON trt.id = mv.to_room_type_id
        LEFT JOIN iam.users du ON du.id = mv.decided_by
        WHERE mv.property_id = :p AND mv.reason = 'upgrade_complimentary'
          AND mv.status = 'completed'
          AND CAST(timezone(CAST(:tz AS text), mv.effective_at) AS date) BETWEEN :a AND :b
        ORDER BY mv.effective_at
        LIMIT :lim
    """):
        diff, nights = r["rate_difference"], r["nights"]
        rows.append({**r, "basis": "Complimentary upgrade",
                     "value": diff * nights if diff and nights and diff > 0 else None,
                     "reason": r["remarks"] or "Complimentary upgrade"})

    return sorted(rows, key=lambda x: (x["arrival_date"] or date.max, x["reservation"] or ""))


@report(
    number=16, slug="expense-voucher", title="Expense Voucher",
    category="Finance & ledgers",
    description="Review expense vouchers by payee, category and approval.",
    basis="Expense date", default_days=30,
    columns=(
        Col("voucher", "Voucher"),
        Col("expense_date", "Expense date", "date"),
        Col("payee", "Payee"),
        Col("category", "Category"),
        Col("description", "Description"),
        Col("method", "Method"),
        Col("room", "Room"),
        Col("amount", "Amount", "money"),
        Col("tax_amount", "Tax", "money"),
        Col("total", "Total", "money"),
        Col("requested_by", "Raised by"),
        Col("approved_by", "Approved by"),
        Col("status", "Status", "status"),
    ),
    metrics=(
        Metric("Vouchers", "count", fmt="number"),
        Metric("Total", "sum", "total"),
        Metric("Paid", "sum", "total", where="status", value="Paid"),
        Metric("Awaiting approval", "count", fmt="number", where="status",
               value="Pending approval"),
    ),
    filters=("category", "method", "status"),
    totals=("amount", "tax_amount", "total"),
    note=(
        "Expense vouchers by the date of the expense, whatever their status. "
        "Rejected and cancelled vouchers are listed so the record is complete, "
        "but were never spent; the Paid tile counts only vouchers marked paid. "
        "A voucher tagged to a room is charged to that unit on the Owner "
        "Statement once approved. Vouchers are raised under Finance › Expense "
        "Vouchers."
    ),
    empty_text="No expense voucher is dated in this window. Vouchers are raised under Finance › Expense Vouchers.",
)
def _expense_vouchers(ctx: Ctx) -> list[dict[str, Any]]:
    rows = ctx.rows("""
        SELECT v.voucher_number, v.expense_date, v.payee, v.category,
               v.description, v.method, v.amount, v.tax_amount, v.status,
               rm.code AS room, cu.display_name AS requested_by,
               du.display_name AS decided_by
        FROM finance.expense_vouchers v
        LEFT JOIN property.rooms rm ON rm.id = v.room_id
        LEFT JOIN iam.users cu ON cu.id = v.created_by
        LEFT JOIN iam.users du ON du.id = v.decided_by
        WHERE v.property_id = :p AND v.expense_date BETWEEN :a AND :b
        ORDER BY v.expense_date, v.voucher_number
        LIMIT :lim
    """)
    for r in rows:
        r.update(
            voucher=f"EV-{r['voucher_number']}",
            category=_EXPENSE_CATEGORIES.get(r["category"], _label(r["category"])),
            method=_EXPENSE_METHODS.get(r["method"], _label(r["method"])),
            total=r["amount"] + r["tax_amount"],
            approved_by=r["decided_by"] if r["status"] in ("approved", "paid") else None,
            status=_EXPENSE_STATUS.get(r["status"], _label(r["status"])),
        )
    return rows


@report(
    number=27, slug="owner-statement", title="Owner Statement",
    category="Management",
    description="Settle room revenue, management fee and charges for each owned unit.",
    basis="Statement period", default_days=30,
    columns=(
        Col("owner", "Owner"),
        Col("room", "Unit"),
        Col("room_type", "Room type"),
        Col("contract", "Contract in period"),
        Col("fee_percent", "Management fee %", "percent"),
        Col("nights", "Occupied nights", "number"),
        Col("gross", "Room revenue", "money"),
        Col("fee", "Management fee", "money"),
        Col("charges", "Operating charges", "money"),
        Col("net", "Net payable", "money"),
        Col("status", "Status", "status"),
    ),
    metrics=(
        Metric("Room revenue", "sum", "gross"),
        Metric("Management fees", "sum", "fee"),
        Metric("Operating charges", "sum", "charges"),
        Metric("Net payable", "sum", "net"),
    ),
    filters=("owner", "status"),
    totals=("nights", "gross", "fee", "charges", "net"),
    note=(
        "One line per unit under an owner contract during the period, counting "
        "only the days the contract covers. Room revenue is room charges posted "
        "to folios, net of adjustments and before tax; a stay with several rooms "
        "shares its room revenue between them by nights. The management fee is "
        "the contract's percentage of that revenue. Operating charges are "
        "approved or paid expense vouchers tagged to the unit. Net payable = "
        "room revenue − management fee − operating charges. Payouts to owners "
        "are not recorded yet, so this is a statement of what is due, not of "
        "what has been paid."
    ),
    empty_text=(
        "No unit is under an owner contract for these dates. Owners and their "
        "units are set up under Finance › Unit Owners."
    ),
)
def _owner_statement(ctx: Ctx) -> list[dict[str, Any]]:
    contracts = ctx.rows("""
        SELECT c.id, c.room_id, c.management_fee_percent, c.start_date, c.end_date,
               o.name AS owner, rm.code AS room, rt.name AS room_type
        FROM finance.unit_owner_contracts c
        JOIN finance.unit_owners o ON o.id = c.owner_id
        LEFT JOIN property.rooms rm ON rm.id = c.room_id
        LEFT JOIN property.room_types rt ON rt.id = rm.room_type_id
        WHERE c.property_id = :p AND c.start_date <= :b
          AND (c.end_date IS NULL OR c.end_date >= :a)
        ORDER BY o.name, rm.code
    """)
    if not contracts:
        return []
    room_ids = list({c["room_id"] for c in contracts})
    lines: dict[Any, list[dict[str, Any]]] = defaultdict(list)
    for u in ctx.rows("""
        SELECT ru.reservation_id, ru.assigned_room_id, ru.arrival_date, ru.departure_date
        FROM booking.reservation_units ru
        WHERE ru.property_id = :p AND ru.status NOT IN ('cancelled', 'no_show')
          AND ru.reservation_id IN (
              SELECT reservation_id FROM booking.reservation_units
              WHERE property_id = :p AND assigned_room_id = ANY(CAST(:rooms AS uuid[])))
    """, rooms=room_ids):
        lines[u["reservation_id"]].append(u)
    revenue = [e for e in _room_revenue(ctx) if e["reservation_id"] in lines]
    vouchers = ctx.rows("""
        SELECT room_id, expense_date, amount + tax_amount AS total
        FROM finance.expense_vouchers
        WHERE property_id = :p AND room_id = ANY(CAST(:rooms AS uuid[]))
          AND status IN ('approved', 'paid') AND expense_date BETWEEN :a AND :b
    """, rooms=room_ids)

    def nights(u: dict[str, Any]) -> int:
        return max((u["departure_date"] - u["arrival_date"]).days, 0)

    cent = Decimal("0.01")
    rows = []
    for c in contracts:
        start = max(ctx.a, c["start_date"])
        end = min(ctx.b, c["end_date"] or ctx.b)
        gross = Decimal(0)
        for e in revenue:
            if not start <= e["business_date"] <= end:
                continue
            stay = lines[e["reservation_id"]]
            mine = [u for u in stay if u["assigned_room_id"] == c["room_id"]]
            if not mine:
                continue
            total_nights = sum(nights(u) for u in stay)
            share = (Decimal(sum(nights(u) for u in mine)) / total_nights if total_nights
                     else Decimal(len(mine)) / len(stay))
            gross += _signed(e) * share
        gross = gross.quantize(cent)
        occupied = sum(
            max((min(u["departure_date"], end + timedelta(days=1))
                 - max(u["arrival_date"], start)).days, 0)
            for stay in lines.values() for u in stay
            if u["assigned_room_id"] == c["room_id"])
        fee = (gross * c["management_fee_percent"] / 100).quantize(cent)
        charges = sum((v["total"] for v in vouchers
                       if v["room_id"] == c["room_id"] and start <= v["expense_date"] <= end),
                      Decimal(0))
        net = gross - fee - charges
        rows.append({
            "owner": c["owner"], "room": c["room"], "room_type": c["room_type"],
            "contract": f"{start:%d %b %Y} – {end:%d %b %Y}",
            "fee_percent": c["management_fee_percent"], "nights": occupied,
            "gross": gross, "fee": fee, "charges": charges, "net": net,
            "status": ("Payable to owner" if net > 0
                       else "Owner owes" if net < 0 else "Nothing due"),
        })
    return rows


_WO_CATEGORIES = {
    "electrical": "Electrical", "plumbing": "Plumbing",
    "hvac": "Air conditioning", "carpentry": "Carpentry",
    "furniture": "Furniture & fixtures", "appliance": "Appliances",
    "civil": "Civil & painting", "it": "IT & telecom", "other": "Other",
}
_WO_STATUSES = {"open": "Open", "in_progress": "In progress", "on_hold": "On hold",
                "completed": "Completed", "cancelled": "Cancelled"}


@report(
    number=38, slug="work-order-list", title="Work Order List",
    category="Rooms & operations",
    description="Track maintenance work orders by priority, owner and due date.",
    basis="As of date", dates="single",
    columns=(
        Col("number", "Work order"),
        Col("created_at", "Reported", "datetime"),
        Col("location", "Location"),
        Col("title", "Issue"),
        Col("category", "Category"),
        Col("priority", "Priority", "status"),
        Col("assigned_to", "Assigned to"),
        Col("due_date", "Due date", "date"),
        Col("days_open", "Days open", "number"),
        Col("cost", "Cost", "money"),
        Col("status", "Status", "status"),
    ),
    metrics=(
        Metric("Work orders", "count", fmt="number"),
        Metric("Overdue", "count", fmt="number", where="status", value="Overdue"),
        Metric("Urgent", "count", fmt="number", where="priority", value="Urgent"),
        Metric("Cost recorded", "sum", "cost"),
    ),
    filters=("category", "priority", "status", "assigned_to"),
    totals=("cost",),
    note=(
        "Every work order reported on or before the date, as it stood then: a "
        "job completed after the date shows as still open, and one past its due "
        "date without being completed shows as overdue. Days open runs from the "
        "day it was reported to the day it was completed, or to the date. When "
        "a job was cancelled is not recorded, so a cancelled job shows as "
        "cancelled whatever the date. Work orders are raised under Work Orders."
    ),
    empty_text="No work order had been reported by this date. Faults are reported under Work Orders.",
)
def _work_orders(ctx: Ctx) -> list[dict[str, Any]]:
    rows = ctx.rows("""
        SELECT w.number, w.created_at, w.location, w.title, w.category,
               w.priority, w.status AS raw_status, w.due_date, w.cost,
               rm.code AS room, u.display_name AS assigned_to,
               CAST(timezone(CAST(:tz AS text), w.created_at) AS date) AS reported_on,
               CAST(timezone(CAST(:tz AS text), w.completed_at) AS date) AS completed_on
        FROM operations.work_orders w
        LEFT JOIN property.rooms rm ON rm.id = w.room_id
        LEFT JOIN iam.users u ON u.id = w.assigned_to
        WHERE w.property_id = :p
          AND CAST(timezone(CAST(:tz AS text), w.created_at) AS date) <= :b
        ORDER BY CASE w.priority WHEN 'urgent' THEN 0 WHEN 'high' THEN 1
                                 WHEN 'medium' THEN 2 ELSE 3 END,
                 w.due_date NULLS LAST, w.number
        LIMIT :lim
    """)
    for r in rows:
        done = r["completed_on"] is not None and r["completed_on"] <= ctx.b
        cancelled = r["raw_status"] == "cancelled"
        state = ("completed" if done else "cancelled" if cancelled
                 else r["raw_status"] if r["raw_status"] in ("open", "in_progress", "on_hold")
                 else "open")
        overdue = state in ("open", "in_progress", "on_hold") and bool(
            r["due_date"] and r["due_date"] < ctx.b)
        r.update(
            number=f"WO-{r['number']}",
            location=" · ".join(x for x in (
                f"Room {r['room']}" if r["room"] else None, r["location"]) if x) or None,
            category=_WO_CATEGORIES.get(r["category"], _label(r["category"])),
            priority=_label(r["priority"]),
            days_open=None if cancelled and not done else
            ((r["completed_on"] if done else ctx.b) - r["reported_on"]).days,
            status="Overdue" if overdue else _WO_STATUSES[state],
        )
    return rows


# ==========================================================================
# Not built yet
# ==========================================================================
_register(Report(
    number=0, slug="hotel-ledger", title="Hotel Ledger Report",
    category="Finance & ledgers",
    description="Reconcile guest, deposit, city ledger and package balances by business date.",
    href="/reports/ledger",
))

CATALOG.sort(key=lambda r: r.number)


# ==========================================================================
# API
# ==========================================================================
class ReportInfo(BaseModel):
    number: int
    slug: str
    title: str
    category: str
    description: str
    available: bool
    unavailable_reason: str | None
    planned: str | None
    href: str | None


class ColumnOut(BaseModel):
    key: str
    label: str
    kind: str


class MetricOut(BaseModel):
    label: str
    op: str
    col: int | list[int] | None
    den: int | None
    where: int | None
    value: str | None
    negate: bool
    fmt: str
    sub: str


class ParamOut(BaseModel):
    key: str
    label: str
    kind: str


class ReportResult(ReportInfo):
    basis: str
    dates: str
    date_from: date
    date_to: date
    business_date: date
    property_name: str
    currency: str
    columns: list[ColumnOut]
    metrics: list[MetricOut]
    filters: list[int]
    totals: list[int]
    params: list[ParamOut]
    param_values: dict[str, Any]
    note: str
    empty_text: str
    rows: list[list[Any]]
    links: list[str | None]
    truncated: bool


def _info(r: Report) -> dict[str, Any]:
    return {"number": r.number, "slug": r.slug, "title": r.title,
            "category": r.category, "description": r.description,
            "available": r.available, "unavailable_reason": r.unavailable_reason,
            "planned": r.planned, "href": r.href}


def resolve(r: Report) -> tuple[list[MetricOut], list[int], list[int]]:
    """Column keys to indexes. Raises ``KeyError`` on a key no column has,
    which the registry test runs for every report."""
    idx = {c.key: i for i, c in enumerate(r.columns)}
    metrics = []
    for m in r.metrics:
        col: int | list[int] | None
        if isinstance(m.col, tuple):
            col = [idx[k] for k in m.col]
        else:
            col = None if m.col is None else idx[m.col]
        metrics.append(MetricOut(
            label=m.label, op=m.op, col=col,
            den=None if m.den is None else idx[m.den],
            where=None if m.where is None else idx[m.where],
            value=m.value, negate=m.negate, fmt=m.fmt, sub=m.sub))
    return metrics, [idx[k] for k in r.filters], [idx[k] for k in r.totals]


def _cell(v: Any) -> Any:
    if isinstance(v, Decimal):
        return float(v)
    if isinstance(v, (datetime, date)):
        return v.isoformat()
    if isinstance(v, uuid.UUID):
        return str(v)
    return v


def execute(
    db: Session, r: Report, property_id: uuid.UUID,
    date_from: date | None, date_to: date | None, raw: dict[str, str],
) -> ReportResult:
    """Run one report. Split from the route so tests can run every report
    without an HTTP caller."""
    if r.run is None:
        raise HTTPException(status_code=409,
                            detail=r.unavailable_reason or "This report is not available.")
    prop = db.execute(
        text("SELECT name, COALESCE(currency, 'INR') AS currency, "
             "COALESCE(timezone, 'Asia/Kolkata') AS tz "
             "FROM iam.properties WHERE id = :p"),
        {"p": property_id},
    ).mappings().first()
    if prop is None:
        raise HTTPException(status_code=404, detail="Property not found.")

    today = local_today(db, property_id)
    business = db.execute(
        text("SELECT max(business_date) FROM finance.business_days "
             "WHERE property_id = :p AND status <> 'closed'"),
        {"p": property_id},
    ).scalar() or today

    if r.dates == "none":
        a = b = today
    elif r.dates == "single":
        b = date_to or business
        a = b
    elif r.forward:
        a = date_from or (min(business, date_to) if date_to else business)
        b = date_to or a + timedelta(days=r.default_days - 1)
    else:
        b = date_to or business
        a = date_from or b - timedelta(days=r.default_days - 1)
    if a > b:
        raise HTTPException(status_code=422,
                            detail="The end date must be on or after the start date.")
    if (b - a).days >= MAX_DAYS:
        raise HTTPException(status_code=422,
                            detail=f"Choose a window of at most {MAX_DAYS} days.")

    params: dict[str, Any] = {}
    for prm in r.params:
        value = raw.get(prm.key)
        if value in (None, ""):
            params[prm.key] = prm.default
        elif prm.kind == "number":
            try:
                params[prm.key] = Decimal(value)
            except InvalidOperation:
                raise HTTPException(status_code=422,
                                    detail=f"{prm.label} must be a number.") from None
            if params[prm.key] < 0:
                raise HTTPException(status_code=422,
                                    detail=f"{prm.label} cannot be negative.")
        else:
            params[prm.key] = value

    ctx = Ctx(db=db, p=property_id, a=a, b=b, tz=prop["tz"], params=params)
    out = r.run(ctx)
    truncated = len(out) > MAX_ROWS
    out = out[:MAX_ROWS]
    metrics, filters, totals = resolve(r)
    return ReportResult(
        **_info(r),
        basis=r.basis, dates=r.dates, date_from=a, date_to=b,
        business_date=business, property_name=prop["name"],
        currency=prop["currency"],
        columns=[ColumnOut(key=c.key, label=c.label, kind=c.kind) for c in r.columns],
        metrics=metrics, filters=filters, totals=totals,
        params=[ParamOut(key=p.key, label=p.label, kind=p.kind) for p in r.params],
        param_values={k: _cell(v) for k, v in params.items()},
        note=r.note, empty_text=r.empty_text,
        rows=[[_cell(row.get(c.key)) for c in r.columns] for row in out],
        links=[str(row[r.link]) if r.link and row.get(r.link) else None
               for row in out],
        truncated=truncated,
    )


@backoffice_router.get("", response_model=list[ReportInfo])
def catalog(
    property_id: uuid.UUID,
    caller: Caller = Depends(require_permission("reports", "view")),
    db: Session = Depends(get_session),
):
    """Every back-office report, built or not, in the design's order."""
    assert_property_in_org(db, caller, property_id)
    return [ReportInfo(**_info(r)) for r in CATALOG]


@backoffice_router.get("/{slug}", response_model=ReportResult)
def run_report(
    slug: str,
    request: Request,
    property_id: uuid.UUID,
    date_from: date | None = Query(None),
    date_to: date | None = Query(None),
    caller: Caller = Depends(require_permission("reports", "view")),
    db: Session = Depends(get_session),
):
    """One report over a window of dates.

    Defaults to the property's open business date -- the same default as the
    Hotel Ledger -- widened to the report's own default window where a single
    day says too little, such as a week for the weekly manager report.
    """
    r = REPORTS.get(slug)
    if r is None or r.href is not None:
        raise HTTPException(status_code=404, detail="No such report.")
    assert_property_in_org(db, caller, property_id)
    return execute(db, r, property_id, date_from, date_to,
                   dict(request.query_params))
