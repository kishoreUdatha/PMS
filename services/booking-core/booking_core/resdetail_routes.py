"""Reservation Details (screen 028).

The existing ``/reservations/{id}/detail`` returns a header and its unit lines
— enough for a drawer, not enough for the screen the mockup asks for. This is
the whole booking in one call: what it is, how it arrived, what it was booked
on, what it has cost and what has been done to it.

Everything is read from records rather than recomputed guesses. The financial
summary comes from ``finance.folio_entries`` through the folio, so it agrees
with the folio screen by construction rather than by coincidence. The activity
tab reads ``iam.audit_events``, so it shows what was actually written —
including anything done outside this screen.

Two honest notes:

* **Communications is empty and says so.** There is no message table and no
  mail or WhatsApp transport, so "Send Confirmation" has nothing to send. The
  tab is returned as an explicit empty state rather than omitted, because its
  absence is the answer.
* **Source and the rest may be blank on older bookings.** ``0020`` added those
  fields; reservations made before it were left NULL rather than backfilled to
  a plausible default. A blank Booking Source means nobody recorded one, which
  is different from "Direct".
"""

from __future__ import annotations

import uuid
from datetime import date, time, datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from decimal import Decimal

from chirala_common import charge_types
from chirala_common.audit import record_audit
from chirala_common.authz import Caller, build_authz, assert_entity_in_org
from chirala_common.routing import TransactionalRoute
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session

from .attribute_routes import assert_attribute
from .database import get_session
from .folio_money import folio_money
from .settings import settings

_get_caller, _require_permission, require_org_permission = build_authz(
    get_session, lambda: settings.environment,
    lambda: settings.service_token,
)

resdetail_router = APIRouter(tags=["reservations"], route_class=TransactionalRoute)

SOURCE_LABELS = {
    "direct": "Direct", "website": "Direct Website", "phone": "Phone",
    "email": "Email", "walk_in": "Walk-in", "ota": "OTA",
    "travel_agent": "Travel Agent", "corporate": "Corporate",
    "group": "Group", "other": "Other",
}

# What a folio line is called. The charge types come from the shared module
# -- the same list the Add Charge dropdown offers and the tax engine maps --
# and the few entries below it are ledger events rather than charges, so they
# belong here and nowhere else.
ENTRY_LABELS = {
    **charge_types.LABELS,
    "room_stay_tax": "Room tax",
    "no_show_penalty_tax": "No-show penalty tax",
    "payment": "Payment", "refund": "Refund", "adjustment": "Adjustment",
    "credit_note": "Credit note",
}

AUDIT_LABELS = {
    "reservation.confirmed": "Reservation confirmed",
    "reservation.cancelled": "Reservation cancelled",
    "reservation.modified": "Stay modified",
    "reservation.details_updated": "Booking details corrected",
    "reservation_unit.room_assigned": "Room assigned",
    "reservation.room_assigned": "Room assigned",
    "stay.checked_in": "Guest checked in",
    "stay.checked_out": "Guest checked out",
    "guest.identity.saved": "Guest details saved",
    "payment.collected": "Payment taken",
    "room_move.completed": "Room moved",
    "reservation_unit.no_show": "Marked no-show",
}


def local_today(db: Session, property_id: uuid.UUID) -> date:
    """Today WHERE THE PROPERTY IS, not where the server is.

    The arrivals and departures lists used to default to ``CURRENT_DATE``,
    which in a UTC container is the server's date. India is UTC+5:30, so from
    18:30 local until midnight that is yesterday -- and a receptionist at
    half past midnight could not see the guests arriving that morning, because
    the list was quietly showing the day before.

    Deliberately the property's calendar date rather than its business date:
    this answers "who walks in today", which is a wall-clock question. The
    business date is the accounting one and can lag when the night audit has
    not run.
    """
    tz_name = db.execute(
        text("SELECT timezone FROM iam.properties WHERE id = :p"),
        {"p": property_id},
    ).scalar() or "Asia/Kolkata"
    try:
        tz = ZoneInfo(tz_name)
    except (ZoneInfoNotFoundError, ValueError):
        tz = ZoneInfo("Asia/Kolkata")
    return datetime.now(tz).date()


def open_business_date(db: Session, property_id: uuid.UUID) -> date:
    """The day this property is actually trading.

    The OLDEST unclosed day, not the newest and not the calendar's: a property
    cannot be selling the 18th while the 17th is still open, because the night
    audit closes them oldest first. Where no day is open at all it falls back to
    the property's own local date -- a resort in Kolkata and one in Lisbon do
    not change date together, and the server may be in neither.

    The same rule finance applies in ``reversal_routes._today``. It is repeated
    rather than imported because these are separate services; what must not
    differ is the answer, and a screen that stamps money with a date finance
    disagrees with is how a payment lands on a day the drawer was never counted
    against -- and can then never be voided, because a void is only for the day
    the payment was taken.
    """
    day = db.execute(
        text("SELECT business_date FROM finance.business_days "
             "WHERE property_id = :p AND status = 'open' "
             "ORDER BY business_date ASC LIMIT 1"),
        {"p": property_id},
    ).scalar()
    if day is not None:
        return day
    tz_name = db.execute(
        text("SELECT timezone FROM iam.properties WHERE id = :p"),
        {"p": property_id},
    ).scalar() or "Asia/Kolkata"
    try:
        tz = ZoneInfo(tz_name)
    except (ZoneInfoNotFoundError, ValueError):
        tz = ZoneInfo("Asia/Kolkata")
    return datetime.now(tz).date()


class UnitRow(BaseModel):
    id: uuid.UUID
    status: str
    arrival_date: date
    departure_date: date
    nights: int
    adults: int
    children: int
    room_type: str | None
    room_code: str | None
    rate_plan: str | None
    meal_plan: str | None
    package: str | None
    #: When the guest said they would arrive and leave. Recorded at booking
    #: and shown beside the dates -- a 2am arrival is the difference between a
    #: room held and a room given away.
    expected_arrival_time: time | None = None
    expected_departure_time: time | None = None


class GuestRow(BaseModel):
    id: uuid.UUID | None
    full_name: str | None
    email: str | None
    phone: str | None
    city: str | None
    country: str | None
    nationality: str | None
    id_type: str | None
    id_verified: bool
    is_primary: bool


class ChargeRow(BaseModel):
    id: uuid.UUID
    business_date: date
    posted_at: datetime
    description: str
    entry_type: str
    amount: Decimal
    is_reversal: bool
    #: A debit that gave a payment back -- a void, a refund or a reversal.
    #: Kept apart from ``is_reversal`` deliberately. Both read as "Reversal" on
    #: the ledger, because neither is a new charge and colouring a returned
    #: ₹2,000 the same as a spa bill is how it gets read as one. But only
    #: ``is_reversal`` is an *adjustment*, and the folio totals a tile from it:
    #: folding refunds into that flag put every refunded payment into
    #: "Adjustments", which is the bug the flag was added to fix.
    is_refund: bool = False
    #: Which folio this line is on. A booking in a company's name carries two
    #: -- the company's for the room, the guest's for everything they ate --
    #: and until this was here the screen could not tell one party's charges
    #: from the other's, because it only ever loaded the first folio.
    folio_id: uuid.UUID
    #: The folio's number, so a line can say which bill it is on without the
    #: screen having to look it up.
    folio_no: str | None = None
    #: Who posted it. None for everything the night audit did and everything
    #: posted before the ledger started recording it -- both read as "System"
    #: rather than being attributed to a person who was not there.
    posted_by: str | None = None
    #: For a payment credit, the payment it came from. The screen needs it to
    #: say *how* the money arrived: the entry records "Payment", and only the
    #: payments table knows it was a card. Without it the folio was reduced to
    #: matching the two up by amount, which cannot tell two ₹1,000 payments
    #: apart -- and a folio holding one cash and one wallet payment of the same
    #: size showed the instrument for neither.
    source_id: str | None = None
    quantity: Decimal | None = None
    unit_amount: Decimal | None = None
    discount_amount: Decimal | None = None


class PaymentRow(BaseModel):
    id: uuid.UUID
    received_at: datetime
    method: str
    reference: str | None
    amount: Decimal
    #: What the gateway called this capture. The only handle anyone has on a
    #: card payment here -- no card number, expiry or holder name is stored,
    #: and none ever should be.
    provider_transaction_id: str | None = None
    status: str | None = None
    #: What has already been given back against this payment, and whether that
    #: is all of it.
    #:
    #: Read from ``finance.refunds`` rather than from the payment's own status,
    #: because that status never changes: a payment voided to the last rupee
    #: stays 'succeeded' forever, so the folio had no way to know the line was
    #: dead and drew it like any other -- live, and still offering "Void
    #: payment" on a payment there was nothing left to void.
    #:
    #: Not derived from ``reversal_of_id`` on the refund entry either. That
    #: column is a filter in live money queries (the cashier's category
    #: breakdown, the adjustment picker), and setting it to mark a refund
    #: would quietly drop refunds out of figures that are meant to count them.
    refunded: Decimal = Decimal(0)
    #: Whole payment given back. Only this strikes the line out -- a partial
    #: refund leaves a payment that still settles part of the bill, and
    #: striking it would say otherwise.
    fully_reversed: bool = False
    #: A void or refund has been RAISED on this payment and not yet posted.
    #:
    #: Nothing had gone back, so the folio drew the line as untouched and its
    #: menu offered "Void payment" again -- which lands on a screen where every
    #: action is refused, because one is already open. The request is real and
    #: somebody has to finish it; the folio is where they will be looking.
    reversal_pending: bool = False
    #: Which kind is open -- 'void', 'refund' or 'reversal'. The folio's menu
    #: names it, and a menu that says "Finish the void" over a pending refund
    #: is the same sort of lie this field exists to stop telling.
    reversal_pending_kind: str | None = None


class ActivityRow(BaseModel):
    at: datetime
    label: str
    actor: str
    reason: str | None
    detail: str | None


class Financials(BaseModel):
    currency: str
    total_charges: Decimal
    total_paid: Decimal
    balance_due: Decimal
    # 0-100. What share of the bill has actually been settled.
    paid_percent: int
    folio_id: uuid.UUID | None
    folio_status: str | None


class Policy(BaseModel):
    name: str | None = None
    free_until_days: int | None = None
    penalty_nights: int | None = None
    no_show_refund: bool | None = None
    text: str | None = None
    # The date free cancellation actually runs out for this booking.
    free_until_date: date | None = None


class BookingInfo(BaseModel):
    source: str | None
    source_label: str | None
    # Which OTA or agent specifically — a different question from `source`,
    # which is only how the booking reached us. Captured when the booking is
    # taken and, until now, never shown again anywhere.
    business_source: str | None
    business_source_id: uuid.UUID | None
    market_segment: str | None
    market_segment_id: uuid.UUID | None
    purpose_of_stay: str | None
    company_name: str | None
    travel_agent: str | None
    reference: str | None
    remarks: str | None
    special_requests: str | None
    rate_plan: str | None
    meal_plan: str | None
    package: str | None


class ReservationFull(BaseModel):
    id: uuid.UUID
    number: str
    status: str
    currency: str
    created_at: datetime
    property_id: uuid.UUID
    #: The day the property is trading, for anything that posts money. The
    #: folio dialogs used to default to the browser's clock in UTC, which after
    #: 18:30 in India is already tomorrow -- and is the calendar's day, not the
    #: one the night audit has reached.
    business_date: date

    guest: GuestRow | None
    arrival_date: date | None
    departure_date: date | None
    nights: int | None
    adults: int
    children: int
    rooms: int

    booking: BookingInfo
    units: list[UnitRow]
    guests: list[GuestRow]
    charges: list[ChargeRow]
    payments: list[PaymentRow]
    activity: list[ActivityRow]
    financials: Financials
    policy: Policy

    # There is no message store and no transport. Stated, not omitted.
    communications_note: str

    can_modify: bool
    can_cancel: bool
    can_assign: bool


_HEAD_SQL = """
    SELECT r.id, r.number, r.status, r.currency, r.created_at, r.property_id,
           r.source, r.business_source_id, r.market_segment_id,
           bs.name AS business_source, ms.name AS market_segment,
           r.purpose_of_stay, r.company_name,
           r.travel_agent, r.reference, r.remarks, r.special_requests,
           r.primary_guest_id,
           cp.name AS policy_name, cp.free_until_days, cp.penalty_nights,
           cp.no_show_refund, cp.policy_text
    FROM booking.reservations r
    LEFT JOIN property.cancellation_policies cp
           ON cp.id = r.cancellation_policy_id
    -- Both classifications are master rows now, so the readable name comes
    -- from the join and the id goes back for the edit form to preselect.
    LEFT JOIN engagement.booking_attributes bs ON bs.id = r.business_source_id
    LEFT JOIN engagement.booking_attributes ms ON ms.id = r.market_segment_id
    WHERE r.id = :id
"""

_UNITS_SQL = """
    SELECT ru.id, ru.status, ru.arrival_date, ru.departure_date,
           ru.adults, ru.children,
           rt.name AS room_type, rm.code AS room_code,
           rp.name AS rate_plan, mp.name AS meal_plan, pk.name AS package,
           ru.expected_arrival_time, ru.expected_departure_time
    FROM booking.reservation_units ru
    LEFT JOIN property.room_types rt ON rt.id = ru.room_type_id
    LEFT JOIN property.rooms rm ON rm.id = ru.assigned_room_id
    LEFT JOIN property.rate_plans rp ON rp.id = ru.rate_plan_id
    LEFT JOIN property.meal_plans mp ON mp.id = ru.meal_plan_id
    LEFT JOIN property.packages pk ON pk.id = ru.package_id
    WHERE ru.reservation_id = :id
    -- The order the booking was taken in. Rooms on one booking share an
    -- arrival date, so sorting by it alone falls through to whatever comes
    -- next and the list can reshuffle between reads.
    ORDER BY ru.arrival_date, ru.line_index
"""


@resdetail_router.get("/reservations/{reservation_id}/full",
                      response_model=ReservationFull)
def reservation_full(
    reservation_id: uuid.UUID,
    caller: Caller = Depends(require_org_permission("reservations", "view")),
    db: Session = Depends(get_session),
):
    """The whole booking, in one call."""
    assert_entity_in_org(db, caller, table="booking.reservations", entity_id=reservation_id)
    from chirala_common.authz import _GRANT_SQL

    head = db.execute(text(_HEAD_SQL), {"id": reservation_id}).mappings().first()
    if head is None:
        raise HTTPException(status_code=404, detail="Reservation not found")

    units_raw = db.execute(text(_UNITS_SQL), {"id": reservation_id}).mappings().all()
    units = [
        UnitRow(
            id=u["id"], status=u["status"], arrival_date=u["arrival_date"],
            departure_date=u["departure_date"],
            nights=(u["departure_date"] - u["arrival_date"]).days,
            adults=u["adults"], children=u["children"],
            room_type=u["room_type"], room_code=u["room_code"],
            rate_plan=u["rate_plan"], meal_plan=u["meal_plan"],
            package=u["package"],
            expected_arrival_time=u["expected_arrival_time"],
            expected_departure_time=u["expected_departure_time"],
        )
        for u in units_raw
    ]

    live = [u for u in units if u.status != "cancelled"] or units
    arrival = min((u.arrival_date for u in live), default=None)
    departure = max((u.departure_date for u in live), default=None)

    guest = None
    if head["primary_guest_id"]:
        g = db.execute(
            text(
                """
                SELECT g.id, g.full_name, g.email, g.phone, g.city, g.country,
                       g.nationality, g.id_type, g.id_verified_at
                FROM engagement.guests g WHERE g.id = :g
                """
            ),
            {"g": head["primary_guest_id"]},
        ).mappings().first()
        if g:
            guest = GuestRow(
                id=g["id"], full_name=g["full_name"], email=g["email"],
                phone=g["phone"], city=g["city"], country=g["country"],
                nationality=g["nationality"], id_type=g["id_type"],
                id_verified=g["id_verified_at"] is not None, is_primary=True,
            )

    # Only the primary guest is linked to a booking — there is no sharer table,
    # so a second occupant cannot be named. The tab shows who is known.
    guests = [guest] if guest else []

    # The *master* folio: the one this screen opens on and the one the
    # financial summary belongs to. A booking's other folios are loaded too --
    # see the entries query below -- but this stays the headline.
    folio = db.execute(
        text("SELECT id, status, currency FROM finance.folios "
             "WHERE reservation_id = :id "
             "ORDER BY (parent_folio_id IS NOT NULL), created_at LIMIT 1"),
        {"id": reservation_id},
    ).mappings().first()

    charges: list[ChargeRow] = []
    payments: list[PaymentRow] = []
    total_charges = total_paid = Decimal(0)
    if folio:
        for e in db.execute(
            text(
                """
                SELECT e.id, e.business_date, e.posted_at, e.entry_type,
                       e.amount, e.source_type, e.source_id, e.reversal_of_id,
                       e.note, e.quantity, e.unit_amount, e.discount_amount,
                       e.folio_id, f.folio_no,
                       -- The display name, resolved at read time rather than
                       -- copied onto the line: people correct their spelling
                       -- and change their name, and a folio that kept the name
                       -- as typed would show the old one forever.
                       COALESCE(u.display_name, e.posted_by) AS posted_by
                  FROM finance.folio_entries e
                  JOIN finance.folios f ON f.id = e.folio_id
                  LEFT JOIN iam.users u ON u.subject_id = e.posted_by
                 WHERE f.reservation_id = :r
                 ORDER BY e.posted_at
                """
            ),
            {"r": reservation_id},
        ).mappings():
            charges.append(ChargeRow(
                id=e["id"], business_date=e["business_date"],
                posted_at=e["posted_at"],
                # What the desk typed, where it typed anything. The source
                # type is a department, not a description — three different
                # things billed to a guest all read as "Restaurant" without
                # this.
                description=(e["note"] or ENTRY_LABELS.get(
                    e["source_type"] or "",
                    (e["source_type"] or "Entry").replace("_", " ").capitalize())),
                quantity=e["quantity"], unit_amount=e["unit_amount"],
                discount_amount=e["discount_amount"],
                entry_type=e["entry_type"], amount=e["amount"],
                is_reversal=e["reversal_of_id"] is not None,
                is_refund=(e["source_type"] or "")
                in charge_types.REFUND_SOURCES,
                source_id=e["source_id"],
                folio_id=e["folio_id"],
                folio_no=e["folio_no"],
                posted_by=e["posted_by"],
            ))
        # Charges are what was billed; payments are how it was settled. Keeping
        # them apart is why the folio and this screen never disagree.
        #
        # The booking's totals are NOT worked out here. Two queries used to
        # stand in this spot computing `total_charges` and `total_paid`, and
        # both results were thrown away a few lines below, where
        # `folio_money()` overwrites them unconditionally. Two round-trips per
        # request for figures nothing read.
        #
        # They were worse than merely wasted: they looked authoritative. One of
        # them was carefully corrected to net off deposit refunds as well as
        # payment refunds -- a fix to code that has never run. `folio_money` is
        # the single calculation the bookings list, the arrivals list and this
        # screen all share, and it already handles both kinds.
        for p in db.execute(
            text(
                """
                SELECT p.id, p.received_at, lower(p.method) AS method,
                       p.reference, p.provider_transaction_id, p.status,
                       sum(pa.amount) AS amount,
                       COALESCE(rf.refunded, 0) AS refunded,
                       -- Against the payment's OWN amount, not the sum of its
                       -- allocations above. Those are only the ones landing on
                       -- this booking's folios; a payment split across two
                       -- bookings would otherwise read as fully given back
                       -- once the part shown here had been.
                       COALESCE(rf.refunded, 0) >= p.amount AS fully_reversed,
                       rv.kind IS NOT NULL AS reversal_pending,
                       rv.kind AS reversal_pending_kind
                FROM finance.payment_allocations pa
                JOIN finance.payments p ON p.id = pa.payment_id
                LEFT JOIN LATERAL (
                    SELECT sum(r.amount) AS refunded
                      FROM finance.refunds r
                     WHERE r.payment_id = p.id
                       AND r.status IN ('succeeded', 'pending')
                ) rf ON TRUE
                LEFT JOIN LATERAL (
                    SELECT pr.kind FROM finance.payment_reversals pr
                     WHERE pr.payment_id = p.id
                       AND pr.status IN ('pending_approval', 'approved')
                     ORDER BY pr.created_at LIMIT 1
                ) rv ON TRUE
                WHERE pa.folio_id IN (SELECT id FROM finance.folios
                                       WHERE reservation_id = :r)
                GROUP BY p.id, p.received_at, p.method, p.reference,
                         p.provider_transaction_id, p.status, p.amount,
                         rf.refunded, rv.kind
                ORDER BY p.received_at
                """
            ),
            {"r": reservation_id},
        ).mappings():
            payments.append(PaymentRow(**p))

    # What the stay is *worth*, not only what has been posted to the folio.
    #
    # Room charges are posted by the night audit, so a booking that has not
    # been stayed yet has none -- and this screen showed Total Amount 0 for
    # every future reservation, including ones already paid in full. A guest
    # who has paid eight thousand rupees against a total of zero is not a
    # screen anybody can act on; it also made Balance Due negative, which
    # reads as the property owing the guest money.
    #
    # ``folio_money`` is the same calculation the bookings and arrivals lists
    # use, and reusing it is the point: a total that differs between the list
    # and the reservation it opens is worse than either being wrong.
    money = folio_money(db, reservation_id)
    total_charges = money["total"]
    total_paid = money["paid"]

    pct = 0
    if total_charges > 0:
        pct = max(0, min(100, int(total_paid / total_charges * 100)))
    elif total_paid > 0:
        pct = 100

    unit_ids = [str(u.id) for u in units]
    activity = []
    for a in db.execute(
        text(
            """
            SELECT a.occurred_at, a.action, a.actor_subject, a.reason,
                   a.redacted_after, u.display_name
            FROM iam.audit_events a
            LEFT JOIN iam.users u ON u.subject_id = a.actor_subject
            WHERE (a.entity_type = 'reservation' AND a.entity_id = :rid)
               OR (a.entity_type IN ('reservation_unit', 'stay')
                   AND a.entity_id = ANY(:units))
            ORDER BY a.occurred_at DESC
            """
        ),
        {"rid": str(reservation_id), "units": unit_ids or [""]},
    ).mappings():
        after = a["redacted_after"] or {}
        bits = []
        if isinstance(after, dict):
            for key in ("room", "room_code", "amount", "status", "balance_after"):
                if after.get(key):
                    bits.append(f"{key.replace('_', ' ')} {after[key]}")
        activity.append(ActivityRow(
            at=a["occurred_at"],
            label=AUDIT_LABELS.get(
                a["action"], a["action"].replace(".", " ").replace("_", " ")),
            actor=a["display_name"] or a["actor_subject"] or "System",
            reason=a["reason"], detail=", ".join(bits) or None,
        ))

    free_until = None
    if head["free_until_days"] is not None and arrival:
        from datetime import timedelta
        free_until = arrival - timedelta(days=int(head["free_until_days"]))

    def may(action: str) -> bool:
        return db.execute(
            text(_GRANT_SQL),
            {"uid": caller.user_id, "res": "reservations", "act": action,
             "prop": str(head["property_id"])},
        ).first() is not None

    open_status = head["status"] not in ("cancelled", "completed")
    first = units[0] if units else None

    return ReservationFull(
        id=head["id"], number=head["number"], status=head["status"],
        currency=head["currency"], created_at=head["created_at"],
        property_id=head["property_id"],
        business_date=open_business_date(db, head["property_id"]),
        guest=guest, arrival_date=arrival, departure_date=departure,
        nights=(departure - arrival).days if arrival and departure else None,
        adults=sum(u.adults for u in live), children=sum(u.children for u in live),
        rooms=len(live),
        booking=BookingInfo(
            source=head["source"],
            source_label=SOURCE_LABELS.get(head["source"] or "") or None,
            business_source=head["business_source"],
            business_source_id=head["business_source_id"],
            market_segment=head["market_segment"],
            market_segment_id=head["market_segment_id"],
            purpose_of_stay=head["purpose_of_stay"],
            company_name=head["company_name"], travel_agent=head["travel_agent"],
            reference=head["reference"], remarks=head["remarks"],
            special_requests=head["special_requests"],
            rate_plan=first.rate_plan if first else None,
            meal_plan=first.meal_plan if first else None,
            package=first.package if first else None,
        ),
        units=units, guests=guests, charges=charges, payments=payments,
        activity=activity,
        financials=Financials(
            currency=head["currency"], total_charges=total_charges,
            total_paid=total_paid, balance_due=total_charges - total_paid,
            paid_percent=pct,
            folio_id=folio["id"] if folio else None,
            folio_status=folio["status"] if folio else None,
        ),
        policy=Policy(
            name=head["policy_name"], free_until_days=head["free_until_days"],
            penalty_nights=head["penalty_nights"],
            no_show_refund=head["no_show_refund"], text=head["policy_text"],
            free_until_date=free_until,
        ),
        communications_note=(
            "No messages have been sent from this system. There is no message "
            "store and no mail or WhatsApp transport configured, so nothing "
            "here would be a record of anything."
        ),
        can_modify=open_status and may("edit"),
        can_cancel=open_status and may("cancel"),
        can_assign=open_status and any(u.room_code is None for u in units)
        and may("edit"),
    )


class BookingEdit(BaseModel):
    """The descriptive side of a booking — what it is *about*.

    Deliberately not here: dates, room type, guest counts and status. Those
    change what is sold and what it costs, so they go through Modify Stay
    where availability is re-checked and the price difference is shown. This
    endpoint cannot move a booking or re-price it.
    """

    source: str | None = None
    business_source_id: uuid.UUID | None = None
    market_segment_id: uuid.UUID | None = None
    purpose_of_stay: str | None = None
    company_name: str | None = None
    travel_agent: str | None = None
    reference: str | None = None
    remarks: str | None = None
    special_requests: str | None = None


SOURCES = tuple(SOURCE_LABELS)

_EDITABLE = (
    "source", "business_source_id", "market_segment_id", "purpose_of_stay",
    "company_name", "travel_agent", "reference", "remarks",
    "special_requests",
)


@resdetail_router.patch("/reservations/{reservation_id}/booking",
                        response_model=ReservationFull)
def update_booking_details(
    reservation_id: uuid.UUID,
    body: BookingEdit,
    caller: Caller = Depends(require_org_permission("reservations", "edit")),
    db: Session = Depends(get_session),
):
    """Correct how a booking was recorded.

    These fields are written once when the booking is taken and were, until
    now, unchangeable — so a source picked wrongly at the desk, or a company
    name learned later, stayed wrong forever. Every change is audited with
    what it was before, because "who changed the market segment" is exactly
    the question a revenue report provokes.
    """
    assert_entity_in_org(db, caller, table="booking.reservations", entity_id=reservation_id)
    before = db.execute(
        text("SELECT organization_id, property_id, status, "
             + ", ".join(_EDITABLE)
             + " FROM booking.reservations WHERE id = :id"),
        {"id": reservation_id},
    ).mappings().first()
    if before is None:
        raise HTTPException(status_code=404, detail="Reservation not found")

    if body.source is not None and body.source not in SOURCES:
        raise HTTPException(
            status_code=422,
            detail=f"Unknown booking source. Use one of: {', '.join(SOURCES)}.")

    values = body.model_dump()
    changed = {
        k: v for k, v in values.items()
        if (v or None) != (before[k] or None)
    }
    if not changed:
        return reservation_full(reservation_id, caller, db)

    # Only what changed: a booking already carrying a retired segment stays
    # editable, and a new value has to be one that is genuinely in use.
    for field, kind in (("business_source_id", "business_source"),
                        ("market_segment_id", "market_segment")):
        if field in changed:
            assert_attribute(db, attribute_id=changed[field], kind=kind,
                             organization_id=before["organization_id"])

    db.execute(
        text("UPDATE booking.reservations SET "
             + ", ".join(f"{k} = :{k}" for k in _EDITABLE)
             + ", updated_at = now(), version = version + 1 WHERE id = :id"),
        {**{k: (values[k] or None) for k in _EDITABLE}, "id": reservation_id},
    )
    record_audit(
        db, action="reservation.details_updated", entity_type="reservation",
        entity_id=str(reservation_id),
        organization_id=before["organization_id"],
        property_id=before["property_id"], actor_subject=caller.subject,
        before={k: str(before[k]) for k in changed if before[k]},
        after={k: str(v) for k, v in changed.items() if v},
    )
    return reservation_full(reservation_id, caller, db)


# ==========================================================================
# Tasks on this booking's rooms
#
# Neither work orders nor housekeeping tasks belong to a reservation — both
# belong to a *room*, which is right: a dripping tap is the room's problem
# whoever is sleeping in it. But the desk's question is about the guest in
# front of them ("is 101 ready?", "did anyone look at that shower?"), so this
# answers it by asking about the rooms that booking occupies, over the nights
# it occupies them.
#
# A booking with no room assigned yet returns nothing, which is the truth: the
# question has no subject.
# ==========================================================================

class TaskRow(BaseModel):
    id: uuid.UUID
    kind: str
    room: str | None
    title: str
    detail: str | None
    status: str
    priority: str | None
    on_date: date | None
    assigned_to: str | None


@resdetail_router.get("/reservations/{reservation_id}/tasks",
                      response_model=list[TaskRow])
def reservation_tasks(
    reservation_id: uuid.UUID,
    caller: Caller = Depends(require_org_permission("reservations", "view")),
    db: Session = Depends(get_session),
):
    """Maintenance and housekeeping on the rooms this booking occupies."""
    assert_entity_in_org(db, caller, table="booking.reservations",
                         entity_id=reservation_id)

    rows = db.execute(
        text(
            """
            WITH stay AS (
                SELECT ru.assigned_room_id AS room_id,
                       min(ru.arrival_date) AS arrival,
                       max(ru.departure_date) AS departure
                  FROM booking.reservation_units ru
                 WHERE ru.reservation_id = :r
                   AND ru.assigned_room_id IS NOT NULL
                 GROUP BY ru.assigned_room_id
            )
            SELECT w.id, 'work_order' AS kind, rm.code AS room,
                   w.title, w.description AS detail, w.status,
                   w.priority, w.due_date AS on_date,
                   u.display_name AS assigned_to,
                   COALESCE(w.due_date, w.created_at::date) AS sort_date
              FROM operations.work_orders w
              JOIN stay ON stay.room_id = w.room_id
              LEFT JOIN property.rooms rm ON rm.id = w.room_id
              LEFT JOIN iam.users u ON u.id = w.assigned_to
             WHERE w.status <> 'cancelled'

            UNION ALL

            SELECT h.id, 'housekeeping' AS kind, rm.code AS room,
                   initcap(replace(h.kind, '_', ' ')) AS title,
                   NULL AS detail, h.state AS status,
                   h.priority, h.task_date AS on_date,
                   u.display_name AS assigned_to,
                   h.task_date AS sort_date
              FROM operations.housekeeping_tasks h
              JOIN stay ON stay.room_id = h.room_id
               -- Only the nights this booking is in the room. A clean from
               -- three months ago is not this guest's business.
               AND h.task_date >= stay.arrival
               AND h.task_date <= stay.departure
              LEFT JOIN property.rooms rm ON rm.id = h.room_id
              LEFT JOIN iam.users u ON u.id = h.assigned_to

             ORDER BY sort_date DESC NULLS LAST
             LIMIT 200
            """
        ),
        {"r": reservation_id},
    ).mappings().all()
    return [TaskRow(**{k: v for k, v in r.items() if k != "sort_date"})
            for r in rows]
