"""Hotel Ledger Report.

Opening, debit, credit and closing per ledger, then the accounts inside the
one ledger this system actually keeps. Read straight out of
``finance.folio_entries`` — the same rows the folio, the cashiering screen and
the night audit read — so the report cannot disagree with them. There is no
reporting store and no nightly rollup to run first.

**Debit and credit mean the folio's side of it, not double-entry.** A charge
is a debit against a guest; a payment is a credit. There is no contra account
and no journal balancing to zero, because this is a folio ledger rather than a
general ledger. That is why this report does not claim to be "balanced": in a
folio ledger debits and credits are *not* meant to match, and a green tick
saying they do would be a reassuring lie on a finance screen. What it asserts
instead is the thing that is actually true and checkable —
``opening + debit - credit = closing`` — and it shows the arithmetic.

**Three of the four ledgers do not exist yet**, and are reported as absent
rather than as zero:

``guest``    real. Every folio is ``type = 'guest'``.
``deposit``  ``finance.deposit_installments`` is empty; nothing schedules one.
``city``     ``engagement.commercial_accounts`` is empty and every reservation
             bills the guest, so no folio has ever been billed to a company.
``package``  ``property.packages`` is empty.

The columns for all four exist on ``finance.folios`` (``type``,
``commercial_account_id``, ``group_id``), so each becomes real the day
something writes to it — the report is already asking the right question of
each one.
"""

from __future__ import annotations

import uuid
from datetime import date, timedelta
from decimal import Decimal

from chirala_common.authz import (
    Caller,
    assert_property_in_org,
    build_authz,
)
from chirala_common.routing import TransactionalRoute
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session

from .night_audit import local_today
from .database import get_session
from .settings import settings

_get_caller, require_permission, _require_org = build_authz(
    get_session, lambda: settings.environment,
    lambda: settings.service_token,
)

report_router = APIRouter(
    prefix="/reports", tags=["reports"], route_class=TransactionalRoute
)

#: Debit and credit as the folio keeps them. `e` is the folio_entries alias.
_DEBIT = "sum(e.amount) FILTER (WHERE e.entry_type = 'debit')"
_CREDIT = "sum(e.amount) FILTER (WHERE e.entry_type = 'credit')"

#: What an entry was for, in the words a report reader uses. The ledger stamps
#: a machine word on every row; this is the only place that turns it into
#: English.
PARTICULARS = {
    "room_stay": "Room charges",
    "room_night": "Room charges",
    "room_stay_tax": "Tax on room charges",
    "no_show_penalty": "No-show penalty",
    "no_show_penalty_tax": "Tax on no-show penalty",
    "cancellation_fee": "Cancellation fee",
    "reservation_change": "Reservation change",
    "room_move": "Room move",
    "room_upgrade": "Room upgrade",
    "restaurant": "Restaurant charges",
    "minibar": "Minibar",
    "service": "Services",
    "adjustment": "Adjustment",
    "deposit": "Deposit",
    "security_deposit": "Security deposit",
    "payment": "Payment received",
    "refund": "Refund",
}

#: Charges that are the room itself, kept apart from everything else because
#: the guest ledger shows the two separately.
_ROOM_SOURCES = "('room_stay', 'room_night', 'room_stay_tax')"

#: Money held rather than earned. Its own ledger, and excluded from the guest
#: one so the two do not count the same entry twice.
_DEPOSIT_SOURCES = "('deposit', 'security_deposit')"

#: Sold on a package. A property of the reservation rather than the folio,
#: which is why it overlapped the other scopes instead of partitioning with
#: them, and why every scope below it in the precedence has to exclude it.
_ON_A_PACKAGE = """
    EXISTS (SELECT 1 FROM booking.reservation_units ru
             WHERE ru.reservation_id = f.reservation_id
               AND ru.package_id IS NOT NULL)
"""

#: True for a refund entry that is giving a deposit back, false for one giving
#: back an ordinary payment. A folio can hold both -- FOL-1008 refunded a card
#: payment and a security deposit on the same day -- so this has to be decided
#: per entry, not per folio.
#:
#: ``e.source_id`` on a refund entry is the refund's id; the refund names the
#: payment; the payment's own entry carries the source_type that put it in a
#: ledger to begin with.
_REFUND_OF_DEPOSIT = f"""
    COALESCE(e.source_type, '') = 'deposit_refund'
    OR COALESCE(e.source_type, '') = 'refund' AND EXISTS (
        SELECT 1
          FROM finance.refunds rf
          JOIN finance.folio_entries pe ON pe.source_id = rf.payment_id::text
         WHERE rf.id::text = e.source_id
           AND COALESCE(pe.source_type, '') IN {_DEPOSIT_SOURCES}
    )
"""


class LedgerLine(BaseModel):
    key: str
    label: str
    #: False when nothing in this deployment writes to this ledger yet. The
    #: figures are zero and the UI says why rather than showing a row of
    #: zeroes as though they were a finding.
    available: bool
    reason: str | None
    opening: Decimal
    debit: Decimal
    credit: Decimal
    closing: Decimal
    #: Accounts this ledger touched inside the window. Movement, not standing:
    #: a folio that was paid off during the range is counted here.
    accounts: int
    #: Accounts still carrying a balance, counted the same way the table below
    #: decides what to list -- over all time, not the window, because a guest
    #: who owes money owes it whatever dates you are looking at.
    #:
    #: Sent separately because the two differ, and the tab badge used to show
    #: whichever it happened to have: `accounts` while a tab was unselected and
    #: the row count once you clicked it. Eighteen became zero on click, for a
    #: ledger where every account had simply been settled.
    open_accounts: int


class GuestRow(BaseModel):
    folio_id: uuid.UUID
    folio_no: str
    reservation_id: uuid.UUID | None
    confirmation_no: str | None
    guest_name: str | None
    room_code: str | None
    arrival_date: date | None
    departure_date: date | None
    stay_status: str | None
    room_charges: Decimal
    other_charges: Decimal
    payments: Decimal
    balance: Decimal


class JournalRow(BaseModel):
    entry_id: uuid.UUID
    business_date: date
    posted_at: object
    folio_id: uuid.UUID
    folio_no: str
    reservation_id: uuid.UUID | None
    confirmation_no: str | None
    guest_name: str | None
    particulars: str
    source_type: str
    debit: Decimal
    credit: Decimal
    #: This **folio's** balance after this entry -- what the guest owed, or
    #: was owed, at that moment. Not the ledger's: a running total carried
    #: across folios adds one guest's account to the next and lands on a
    #: number that is nobody's.
    #:
    #: Computed over the folio's whole history, so filtering the journal or
    #: paging through it changes which rows you see, never what they say.
    running: Decimal


class Check(BaseModel):
    """The assertion this report is willing to make.

    Not "debits equal credits" -- they are not supposed to in a folio ledger.
    This is the arithmetic of the summary itself, recomputed, so a reader can
    see the figures tie rather than being told they do.
    """
    opening: Decimal
    debit: Decimal
    credit: Decimal
    closing: Decimal
    ties: bool
    #: One line, for the tile. The tile sits beside the summary table and has
    #: to be no taller than it, or the two boxes stop lining up.
    note: str
    #: The whole reasoning, for the tile's tooltip and for anyone reading the
    #: API. Saying it in full on screen cost 66px of height the table did not
    #: have to give.
    note_long: str


class Option(BaseModel):
    value: str
    label: str


class FilterOptions(BaseModel):
    """What the filter panel can offer, built from what this property has.

    Every list is derived rather than hard-coded, so a dropdown never offers
    a value that would return nothing — and `market` comes back empty here,
    which is honest: no market segment has ever been set on a reservation.
    """
    ledger_types: list[Option]
    transaction_codes: list[Option]
    payment_methods: list[Option]
    markets: list[Option]
    rooms: list[Option]


class HotelLedgerReport(BaseModel):
    property_id: uuid.UUID
    property_name: str
    currency: str
    date_from: date
    date_to: date
    ledgers: list[LedgerLine]
    hotel: LedgerLine
    check: Check
    #: The accounts of whichever ledger is selected -- guest unless
    #: `ledger_type` says otherwise. Named for what it is rather than for the
    #: guest ledger it usually holds.
    guest_rows: list[GuestRow]
    guest_total: Decimal
    #: Which ledger `guest_rows` belongs to, echoed back so the UI can show
    #: the right tab as selected after a reload.
    rows_ledger: str
    journal: list[JournalRow]
    journal_truncated: bool
    options: FilterOptions


@report_router.get("/ledger", response_model=HotelLedgerReport)
def ledger(
    property_id: uuid.UUID,
    date_from: date | None = Query(None),
    date_to: date | None = Query(None),
    ledger_type: str | None = Query(None),
    q: str | None = Query(None, description="Guest or company name"),
    confirmation_no: str | None = Query(None),
    folio_no: str | None = Query(None),
    room: str | None = Query(None),
    transaction_code: str | None = Query(None),
    payment_method: str | None = Query(None),
    market: str | None = Query(None),
    limit: int = Query(500, ge=1, le=5000),
    caller: Caller = Depends(require_permission("reports", "view")),
    db: Session = Depends(get_session),
):
    """The hotel ledger over a window of business dates.

    Defaults to the last 30 days ending on the property's **open business
    date**, because a report that opens empty and asks for a date range first
    is one nobody reads.

    Not ``date.today()``, which it used to be, and which was wrong twice over.
    The window is measured in business dates, and a business date is not a
    calendar date: the day ends when the auditor closes it, so a property whose
    audit has already closed tonight is posting into tomorrow. Lekhana closed
    14 September at 00:15 and moved to the 15th, so every refund taken that day
    landed on business date 15 -- outside a window ending "today" -- and the
    report showed a hotel balance of -59,224 for money that had already gone
    back. Entries you can post but cannot see is the worst way for a ledger to
    be wrong.

    And ``date.today()`` is the server's date in UTC, which after 18:30 is
    already tomorrow in Chirala.
    """
    assert_property_in_org(db, caller, property_id)
    prop = db.execute(
        text("SELECT name, COALESCE(currency, 'INR') AS currency "
             "FROM iam.properties WHERE id = :p"),
        {"p": property_id},
    ).mappings().one()

    # The latest date the ledger can hold an entry: the open business day if
    # there is one, else the property's own calendar date. Never the server's.
    default_to = db.execute(
        text("""
            SELECT max(business_date) FROM finance.business_days
            WHERE property_id = :p AND status <> 'closed'
        """),
        {"p": property_id},
    ).scalar() or local_today(db, property_id)

    to_ = date_to or default_to
    from_ = date_from or (to_ - timedelta(days=29))
    params = {
        "p": property_id, "a": from_, "b": to_,
        "q": (q or "").strip() or None,
        "conf": (confirmation_no or "").strip() or None,
        # Typed with or without the prefix: FOL-1004 and 1004 both find it.
        "folio": ((folio_no or "").strip().upper().replace("FOL-", "")
                  .replace("FOL/", "") or None),
        "room": room or None,
        "code": transaction_code or None,
        "method": payment_method or None,
        "market": market or None,
    }

    # One WHERE fragment, applied to the account list and the journal alike,
    # so the two can never be filtered differently and disagree.
    #
    # `payment_method` reaches the ledger through the payment the entry came
    # from: a folio entry records what was charged, not how it was settled, so
    # the filter has to follow `source_id` back to finance.payments.
    _MATCH = """
          AND (CAST(:q AS text) IS NULL
               OR g.full_name ILIKE '%' || CAST(:q AS text) || '%'
               OR r.number    ILIKE '%' || CAST(:q AS text) || '%'
               OR upper(f.folio_no) ILIKE '%' || upper(CAST(:q AS text)) || '%')
          AND (CAST(:conf AS text) IS NULL
               OR r.number ILIKE '%' || CAST(:conf AS text) || '%')
          AND (CAST(:folio AS text) IS NULL
               OR upper(f.folio_no) LIKE '%' || CAST(:folio AS text) || '%')
          AND (CAST(:room AS text) IS NULL OR rmx.code = CAST(:room AS text))
          AND (CAST(:code AS text) IS NULL
               OR COALESCE(e.source_type, 'other') = CAST(:code AS text))
          AND (CAST(:market AS text) IS NULL OR r.source = CAST(:market AS text))
          AND (CAST(:method AS text) IS NULL
               OR EXISTS (SELECT 1 FROM finance.payments pm
                           -- `source_id` is text and holds whatever the
                           -- posting source was, so the uuid is cast to
                           -- text rather than the other way: a row whose
                           -- source_id is not a uuid must not error the
                           -- whole report.
                           WHERE pm.id::text = e.source_id
                             AND lower(pm.method) = lower(CAST(:method AS text))))
    """

    # Joins the fragment above depends on. Repeated into both queries rather
    # than factored into a view, because a view would have to be migrated and
    # this is two lines.
    _MATCH_JOINS = """
        LEFT JOIN booking.reservations r ON r.id = f.reservation_id
        LEFT JOIN engagement.guests g ON g.id = r.primary_guest_id
        LEFT JOIN LATERAL (
            SELECT rm2.code
            FROM booking.reservation_units ru
            JOIN property.rooms rm2 ON rm2.id = ru.assigned_room_id
            WHERE ru.reservation_id = r.id
            ORDER BY ru.line_index LIMIT 1
        ) rmx ON TRUE
    """

    # ---- each ledger, measured rather than assumed -----------------------
    #
    # These used to be hard-coded: guest was real and the other three returned
    # a fixed "none yet". That was true of this data and wrong as code -- a
    # tenant who created a company account and billed a folio to it would have
    # found the City / AR tab still greyed out, with a message telling them
    # the thing they had just done had never happened.
    #
    # These must PARTITION the entries, not merely describe them: Hotel
    # Balance is their plain sum, so anything matching two scopes is counted
    # twice there. That is a real defect this code shipped with -- package is
    # a property of the reservation, not of the folio, so a guest folio whose
    # booking carried a package satisfied the guest scope and the package
    # scope at once and a 1,000 charge produced a 2,000 hotel total. Nothing
    # detected it, because the tie check sums the same categories.
    #
    # So the order below is the precedence, most specific first, and each
    # scope excludes the ones above it:
    #
    #   deposit  money held rather than earned -- decided per ENTRY, since a
    #            deposit sits on an ordinary guest folio rather than one of
    #            its own, and a refund of one goes back out the same way
    #   city     billed to a company, so not the guest's balance
    #   package  sold as a package, whoever is paying
    #   guest    everything else
    #
    # Add a scope and it must exclude the ones above it, or Hotel Balance
    # silently inflates again.
    LEDGER_SCOPES = [
        (
            "guest", "Guest Ledger",
            "f.type = 'guest' AND f.commercial_account_id IS NULL"
            f" AND COALESCE(e.source_type, '') NOT IN {_DEPOSIT_SOURCES}"
            # A deposit going back out belongs to the ledger it came from.
            f" AND NOT ({_REFUND_OF_DEPOSIT})"
            # ...and not one of the more specific ledgers above.
            f" AND NOT ({_ON_A_PACKAGE})",
            "No guest folio has been opened yet.",
        ),
        (
            "deposit", "Deposit Ledger",
            f"COALESCE(e.source_type, '') IN {_DEPOSIT_SOURCES}"
            f" OR ({_REFUND_OF_DEPOSIT})",
            "Nothing has been taken as a deposit yet. Deposits scheduled on "
            "a reservation appear here once they are collected.",
        ),
        (
            "city", "City / AR Ledger",
            "(f.type = 'company' OR f.commercial_account_id IS NOT NULL)"
            f" AND COALESCE(e.source_type, '') NOT IN {_DEPOSIT_SOURCES}"
            f" AND NOT ({_REFUND_OF_DEPOSIT})",
            "No folio has been billed to a company. Create a commercial "
            "account and set a reservation to bill it, and it appears here.",
        ),
        (
            "package", "Package Ledger",
            f"({_ON_A_PACKAGE})"
            # Below deposit and city in the precedence above.
            f" AND COALESCE(e.source_type, '') NOT IN {_DEPOSIT_SOURCES}"
            f" AND NOT ({_REFUND_OF_DEPOSIT})"
            " AND f.type <> 'company' AND f.commercial_account_id IS NULL",
            "No reservation has been sold on a package yet.",
        ),
    ]

    ledgers: list[LedgerLine] = []
    for key, label, scope, empty_reason in LEDGER_SCOPES:
        # The same filters the movements below carry. Without them this was
        # the whole property's opening balance with one reservation's
        # movements applied to it -- two different populations, added
        # together, and reported as one account's position.
        opening = db.execute(
            text(f"""
                SELECT COALESCE({_DEBIT}, 0) - COALESCE({_CREDIT}, 0)
                FROM finance.folio_entries e
                JOIN finance.folios f ON f.id = e.folio_id
                {_MATCH_JOINS}
                WHERE e.property_id = :p AND e.business_date < :a
                  AND ({scope})
                {_MATCH}
            """),
            params,
        ).scalar() or Decimal(0)

        mv = db.execute(
            text(f"""
                SELECT COALESCE({_DEBIT}, 0) AS debit,
                       COALESCE({_CREDIT}, 0) AS credit,
                       count(DISTINCT e.folio_id) AS accounts
                FROM finance.folio_entries e
                JOIN finance.folios f ON f.id = e.folio_id
                {_MATCH_JOINS}
                WHERE e.property_id = :p
                  AND e.business_date BETWEEN :a AND :b
                  AND ({scope})
                {_MATCH}
            """),
            params,
        ).mappings().one()

        # Available means "this ledger is in use", judged over all of time
        # rather than the window: a tab that switched off because you looked
        # at a quiet fortnight would be worse than one that never switched on.
        ever = db.execute(
            text(f"""
                SELECT EXISTS (
                    SELECT 1 FROM finance.folio_entries e
                    JOIN finance.folios f ON f.id = e.folio_id
                    WHERE e.property_id = :p AND ({scope})
                )
            """),
            {"p": property_id},
        ).scalar()

        # Counted over all time and with no window, to agree with the rows
        # table -- which deliberately lists every account with a balance, not
        # only those touched in the range.
        open_accounts = db.execute(
            text(f"""
                SELECT count(*) FROM (
                    SELECT f.id
                    FROM finance.folios f
                    JOIN finance.folio_entries e ON e.folio_id = f.id
                    WHERE f.property_id = :p AND ({scope})
                    GROUP BY f.id
                    HAVING COALESCE({_DEBIT}, 0) - COALESCE({_CREDIT}, 0) <> 0
                ) x
            """),
            {"p": property_id},
        ).scalar() or 0

        ledgers.append(LedgerLine(
            key=key, label=label,
            available=bool(ever), reason=None if ever else empty_reason,
            opening=opening, debit=mv["debit"], credit=mv["credit"],
            closing=opening + mv["debit"] - mv["credit"],
            accounts=mv["accounts"],
            open_accounts=open_accounts,
        ))

    guest_line = ledgers[0]
    opening = guest_line.opening

    # Which ledger's accounts to list. The tabs drive this: clicking City / AR
    # asks the server for that ledger's accounts rather than filtering guest
    # rows in the browser, which would have nothing to filter.
    scope_by_key = {k: predicate for k, _, predicate, _ in LEDGER_SCOPES}
    chosen = ledger_type if ledger_type in scope_by_key else "guest"
    row_scope = scope_by_key[chosen]

    hotel = LedgerLine(
        key="hotel", label="Hotel Balance", available=True, reason=None,
        opening=sum((x.opening for x in ledgers), Decimal(0)),
        debit=sum((x.debit for x in ledgers), Decimal(0)),
        credit=sum((x.credit for x in ledgers), Decimal(0)),
        closing=sum((x.closing for x in ledgers), Decimal(0)),
        accounts=sum(x.accounts for x in ledgers),
        open_accounts=sum(x.open_accounts for x in ledgers),
    )

    # ---- the accounts inside the guest ledger --------------------------
    # Every folio with a position, not only those touched in the range: a
    # guest who owes money owes it whatever window is on screen.
    guest_rows = [
        GuestRow(**r) for r in db.execute(
            text(f"""
                WITH bal AS (
                    SELECT f.id AS folio_id, f.reservation_id,
                           COALESCE(sum(e.amount) FILTER (
                               WHERE e.entry_type = 'debit'
                                 AND COALESCE(e.source_type, '') IN {_ROOM_SOURCES}
                           ), 0) AS room_charges,
                           COALESCE(sum(e.amount) FILTER (
                               WHERE e.entry_type = 'debit'
                                 AND COALESCE(e.source_type, '') NOT IN {_ROOM_SOURCES}
                           ), 0) AS other_charges,
                           COALESCE({_CREDIT}, 0) AS payments,
                           COALESCE({_DEBIT}, 0) - COALESCE({_CREDIT}, 0) AS balance
                    FROM finance.folios f
                    JOIN finance.folio_entries e ON e.folio_id = f.id
                    {_MATCH_JOINS}
                    WHERE f.property_id = :p AND ({row_scope})
                    {_MATCH}
                    GROUP BY f.id, f.reservation_id
                )
                SELECT bal.folio_id,
                       fo.folio_no,
                       bal.reservation_id,
                       r.number AS confirmation_no,
                       g.full_name AS guest_name,
                       rm.code AS room_code,
                       u.arrival_date, u.departure_date, u.status AS stay_status,
                       bal.room_charges, bal.other_charges,
                       bal.payments, bal.balance
                FROM bal
                JOIN finance.folios fo ON fo.id = bal.folio_id
                LEFT JOIN booking.reservations r ON r.id = bal.reservation_id
                LEFT JOIN engagement.guests g ON g.id = r.primary_guest_id
                LEFT JOIN LATERAL (
                    SELECT ru.arrival_date, ru.departure_date, ru.status,
                           ru.assigned_room_id
                    FROM booking.reservation_units ru
                    WHERE ru.reservation_id = r.id
                    ORDER BY ru.line_index
                    LIMIT 1
                ) u ON TRUE
                LEFT JOIN property.rooms rm ON rm.id = u.assigned_room_id
                WHERE bal.balance <> 0
                ORDER BY abs(bal.balance) DESC
            """),
            # The filter fragment is in this query now, so it needs every
            # bind the fragment names — not just the property.
            params,
        ).mappings()
    ]

    # ---- the journal behind all of it ----------------------------------
    rows = db.execute(
        text(f"""
            WITH folio_running AS (
                -- Every entry at this property, with its folio's balance as at
                -- that entry. Deliberately unfiltered: the balance a guest was
                -- carrying on the 14th includes what they did on the 11th,
                -- whether or not the 11th is inside the window being viewed.
                SELECT e.id,
                       sum(CASE WHEN e.entry_type = 'debit'
                                THEN e.amount ELSE -e.amount END)
                       OVER (PARTITION BY e.folio_id
                             ORDER BY e.business_date, e.posted_at, e.id
                             ROWS UNBOUNDED PRECEDING) AS folio_balance
                FROM finance.folio_entries e
                WHERE e.property_id = :p
            )
            SELECT e.id AS entry_id, e.business_date, e.posted_at,
                   e.folio_id,
                   f.folio_no,
                   f.reservation_id, r.number AS confirmation_no,
                   g.full_name AS guest_name,
                   COALESCE(e.source_type, 'other') AS source_type,
                   CASE WHEN e.entry_type = 'debit'  THEN e.amount ELSE 0 END AS debit,
                   CASE WHEN e.entry_type = 'credit' THEN e.amount ELSE 0 END AS credit,
                   fr.folio_balance
            FROM finance.folio_entries e
            JOIN finance.folios f ON f.id = e.folio_id
            JOIN folio_running fr ON fr.id = e.id
            {_MATCH_JOINS}
            WHERE e.property_id = :p AND e.business_date BETWEEN :a AND :b
            {_MATCH}
            ORDER BY e.business_date, e.posted_at, e.id
            LIMIT :lim
        """),
        {**params, "lim": limit + 1},
    ).mappings().all()

    truncated = len(rows) > limit
    journal: list[JournalRow] = []
    for r in rows[:limit]:
        journal.append(JournalRow(
            **{k: r[k] for k in (
                "entry_id", "business_date", "posted_at", "folio_id",
                "folio_no", "reservation_id", "confirmation_no", "guest_name",
                "source_type", "debit", "credit")},
            particulars=PARTICULARS.get(r["source_type"], r["source_type"]),
            running=r["folio_balance"],
        ))

    # ---- what the filter panel can offer -------------------------------
    # Derived, never hard-coded, so a dropdown cannot offer a value that
    # returns nothing. `markets` comes back empty on this data, which is the
    # honest answer: no reservation has ever carried a source.
    codes = [
        Option(value=r[0], label=PARTICULARS.get(r[0], r[0]))
        for r in db.execute(
            text("SELECT DISTINCT COALESCE(source_type, 'other') "
                 "FROM finance.folio_entries WHERE property_id = :p "
                 "ORDER BY 1"),
            {"p": property_id})
    ]
    methods = [
        Option(value=r[0], label=r[0].replace('_', ' ').title())
        for r in db.execute(
            text("SELECT DISTINCT lower(method) FROM finance.payments "
                 "WHERE property_id = :p AND method IS NOT NULL ORDER BY 1"),
            {"p": property_id})
    ]
    markets = [
        Option(value=r[0], label=r[0].replace('_', ' ').title())
        for r in db.execute(
            text("SELECT DISTINCT source FROM booking.reservations "
                 "WHERE property_id = :p AND source IS NOT NULL ORDER BY 1"),
            {"p": property_id})
    ]
    rooms = [
        Option(value=r[0], label=r[0])
        for r in db.execute(
            text("SELECT code FROM property.rooms WHERE property_id = :p "
                 "AND retired_on IS NULL ORDER BY code"),
            {"p": property_id})
    ]

    return HotelLedgerReport(
        property_id=property_id, property_name=prop["name"],
        currency=prop["currency"], date_from=from_, date_to=to_,
        ledgers=ledgers, hotel=hotel,
        check=Check(
            opening=hotel.opening, debit=hotel.debit, credit=hotel.credit,
            closing=hotel.closing,
            ties=(hotel.opening + hotel.debit - hotel.credit) == hotel.closing,
            note="A folio ledger has no contra entries, so debits and "
                 "credits are not meant to match.",
            note_long="This is a folio ledger, not a general ledger: a charge "
                      "is a debit against a guest with no contra entry, so "
                      "debits and credits are not meant to match. What is "
                      "checked is that the closing balance is the opening "
                      "balance plus debits less credits.",
        ),
        guest_rows=guest_rows,
        guest_total=sum((x.balance for x in guest_rows), Decimal(0)),
        rows_ledger=chosen,
        journal=journal, journal_truncated=truncated,
        options=FilterOptions(
            ledger_types=[Option(value=x.key, label=x.label) for x in ledgers],
            transaction_codes=codes, payment_methods=methods,
            markets=markets, rooms=rooms,
        ),
    )
