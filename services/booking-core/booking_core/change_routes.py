"""Modify or Cancel Reservation API (screen 029).

Until now a booking could be created but never changed. ``cancelled`` was a
status the product could not reach, so the Cancelled filter on the reservations
list was permanently empty and a guest who wanted different dates had to be
handled outside the system.

Both operations are quoted before they are committed. A modification is priced
against the same rate resolution the rest of the system uses, and a cancellation
against the property's written policy — the penalty is never a number this
module invented.

Three things worth stating:

* **Availability is re-checked, not assumed.** New dates or a new room type go
  through the same inventory and the same GiST-guarded calendar as a fresh
  booking, and a modification that cannot be accommodated is refused rather
  than half-applied.
* **A cancellation releases what it holds.** The room calendar entries are
  released and the reserved inventory given back, so the nights become sellable
  again the moment the booking dies.
* **Approval is not simulated.** A cancellation whose refund exceeds the
  policy's threshold is recorded as ``pending_approval`` and the booking stays
  live. Screen 042 has a queue but still no path into it, and cancelling while
  imagining the paperwork would leave a guest with no room and no refund.
"""

from __future__ import annotations

import json
import uuid
from datetime import date, datetime, time, timezone
from decimal import Decimal

from chirala_common.audit import record_audit
from chirala_common.authz import Caller, assert_property_in_org, build_authz
from chirala_common.routing import TransactionalRoute
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .database import get_session
from .inventory import (
    InventoryOversold,
    counter_for,
    occupancy,
    occupancy_delta,
    shift_inventory,
)
from .settings import settings

_get_caller, require_permission, _require_org = build_authz(
    get_session, lambda: settings.environment,
    lambda: settings.service_token,
)

change_router = APIRouter(tags=["reservation-changes"],
                          route_class=TransactionalRoute)

#: The reasons a change may carry. ``booking.reservation_changes`` has a check
#: constraint on the same list, and without this guard a value outside it
#: reaches the database and comes back as a 500 rather than a sentence saying
#: what is allowed.
REASON_CODES = ("guest_request", "travel_plans", "date_change", "price",
                "duplicate", "no_show", "property_initiated", "other")


def _check_reason(reason: str | None) -> None:
    if reason is not None and reason not in REASON_CODES:
        raise HTTPException(
            status_code=422,
            detail=f"Unknown reason {reason!r}. Use one of: "
                   f"{', '.join(REASON_CODES)}.",
        )


CANCEL_REASONS = [
    {"code": "travel_plans", "label": "Change in travel plans"},
    {"code": "guest_request", "label": "Guest request"},
    {"code": "date_change", "label": "Wants different dates"},
    {"code": "price", "label": "Price / found better rate"},
    {"code": "duplicate", "label": "Duplicate booking"},
    {"code": "no_show", "label": "No-show"},
    {"code": "property_initiated", "label": "Property initiated"},
    {"code": "other", "label": "Other"},
]


# --------------------------------------------------------------------------
# Schemas
# --------------------------------------------------------------------------
class StaySide(BaseModel):
    arrival_date: date
    departure_date: date
    nights: int
    room_type_id: uuid.UUID | None
    room_type: str
    adults: int
    children: int
    rooms: int
    nightly_rate: Decimal
    total_amount: Decimal


class Policy(BaseModel):
    name: str
    free_until_days: int
    penalty_nights: int
    requires_approval: bool
    approval_above: Decimal
    policy_text: str


class ChangeView(BaseModel):
    reservation_id: uuid.UUID
    number: str
    status: str
    guest_name: str | None
    guest_phone: str | None
    guest_email: str | None
    currency: str
    current: StaySide
    room_types: list[dict]
    policy: Policy
    cancel_reasons: list[dict]
    amount_paid: Decimal
    folio_id: uuid.UUID | None
    can_modify: bool
    can_cancel: bool
    blocked_reason: str | None
    can_approve: bool


class ProposeIn(BaseModel):
    arrival_date: date
    departure_date: date
    room_type_id: uuid.UUID | None = None
    adults: int = Field(ge=1)
    children: int = Field(default=0, ge=0)


class ModifyQuote(BaseModel):
    current: StaySide
    proposed: StaySide
    difference: Decimal
    available: bool
    unavailable_reason: str | None


class ModifyIn(ProposeIn):
    reason: str | None = None
    notes: str | None = Field(default=None, max_length=600)
    charge_difference: bool = True


class CancelQuote(BaseModel):
    original: StaySide
    days_before_arrival: int
    within_penalty_window: bool
    penalty_amount: Decimal
    amount_paid: Decimal
    refund_estimate: Decimal
    requires_approval: bool
    policy: Policy


class CancelIn(BaseModel):
    reason: str
    notes: str | None = Field(default=None, max_length=600)
    # Waiving is a decision, so it is explicit and needs the approval right.
    waive_penalty: bool = False


class ChangeOut(BaseModel):
    id: uuid.UUID
    kind: str
    status: str
    difference: Decimal
    penalty_amount: Decimal
    refund_estimate: Decimal
    warnings: list[str]


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------
_RES_SQL = """
    SELECT r.id, r.number, r.status, r.currency, r.organization_id,
           r.property_id, r.primary_guest_id, r.group_block_id,
           g.full_name AS guest_name, g.phone AS guest_phone,
           g.email AS guest_email
    FROM booking.reservations r
    LEFT JOIN engagement.guests g ON g.id = r.primary_guest_id
"""


def _reservation(db: Session, rid: uuid.UUID, property_id: uuid.UUID, *,
                 lock: bool = False):
    """The reservation, optionally locked for the rest of the transaction.

    Every path that *changes* a booking passes ``lock=True``. Without it two
    writers -- a desk cancelling while the OTA's redelivered cancellation
    arrives, say -- both read ``confirmed``, both pass ``_blocked`` and both
    give the rooms back, so ``reserved_units`` went down twice for one
    booking and the property sold a room it did not have. With the row
    locked the second writer waits, then reads the status the first one
    committed and is refused by the same check.

    The lock is taken before the units are read, so the units a writer acts
    on are the ones the winner left behind, not a snapshot from before it.
    ``OF r``: the guest row is joined for display and must not be locked --
    an outer join's nullable side cannot be, and a guest shared by two
    bookings would serialise unrelated changes.
    """
    row = db.execute(
        text(_RES_SQL + " WHERE r.id = :id AND r.property_id = :prop"
             + (" FOR UPDATE OF r" if lock else "")),
        {"id": rid, "prop": property_id},
    ).mappings().first()
    if row is None:
        raise HTTPException(status_code=404, detail="Reservation not found")
    return row


def _units(db: Session, rid: uuid.UUID):
    return db.execute(
        text(
            """
            SELECT ru.id, ru.room_type_id, ru.arrival_date, ru.departure_date,
                   ru.adults, ru.children, ru.status, ru.assigned_room_id,
                   -- The rate this booking was actually sold at. Extending a
                   -- stay is quoted from this rather than from the rate
                   -- calendar, because it is what the night audit will
                   -- charge; see `extend_quote`.
                   ru.nightly_rate,
                   rt.name AS room_type
            FROM booking.reservation_units ru
            LEFT JOIN property.room_types rt ON rt.id = ru.room_type_id
            WHERE ru.reservation_id = :r AND ru.status NOT IN ('cancelled', 'no_show')
            ORDER BY ru.arrival_date
            """
        ),
        {"r": rid},
    ).mappings().all()


def _rate(db: Session, property_id, room_type_id, on: date) -> Decimal:
    return Decimal(
        db.execute(
            text(
                """
                SELECT COALESCE(
                    (SELECT rate FROM property.rate_calendar_days
                      WHERE property_id = :p AND room_type_id = :rt
                        AND stay_date = :d),
                    (SELECT base_rate FROM property.room_types WHERE id = :rt),
                    0)
                """
            ),
            {"p": property_id, "rt": room_type_id, "d": on},
        ).scalar_one()
    )


def _price(db: Session, property_id, room_type_id, arrival: date,
           departure: date, rooms: int) -> tuple[Decimal, Decimal]:
    """Total for the stay, and the first night's rate for display."""
    total = Decimal("0")
    d = arrival
    while d < departure:
        total += _rate(db, property_id, room_type_id, d)
        d = date.fromordinal(d.toordinal() + 1)
    first = _rate(db, property_id, room_type_id, arrival)
    return total * rooms, first


def _side(db: Session, property_id, *, room_type_id, room_type, arrival,
          departure, adults, children, rooms) -> StaySide:
    nights = (departure - arrival).days
    total, first = _price(db, property_id, room_type_id, arrival, departure, rooms)
    return StaySide(
        arrival_date=arrival, departure_date=departure, nights=nights,
        room_type_id=room_type_id, room_type=room_type or "—",
        adults=adults, children=children, rooms=rooms,
        nightly_rate=first, total_amount=total,
    )


def _current(db: Session, res, units) -> StaySide:
    if not units:
        today = db.execute(text("SELECT CURRENT_DATE")).scalar_one()
        return _side(db, res["property_id"], room_type_id=None, room_type="—",
                     arrival=today, departure=today, adults=0, children=0,
                     rooms=0)
    u = units[0]
    return _side(
        db, res["property_id"], room_type_id=u["room_type_id"],
        room_type=u["room_type"],
        arrival=min(x["arrival_date"] for x in units),
        departure=max(x["departure_date"] for x in units),
        adults=sum(x["adults"] for x in units),
        children=sum(x["children"] for x in units), rooms=len(units),
    )


#: What a waived cancellation is measured against when the property has no
#: policy of its own. Nothing is charged, so there is nothing for a policy to
#: decide -- and refusing the cancellation for want of one is what left OTA
#: cancellations (always waived) failing forever on such properties.
_WAIVED_NO_POLICY = {
    "name": "No policy (penalty waived)", "free_until_days": 0,
    "penalty_nights": 0, "requires_approval": False,
    "approval_above": Decimal("0"),
    "policy_text": "This property has no cancellation policy configured; the "
                   "penalty was waived.",
}


def _policy(db: Session, property_id: uuid.UUID, *, required: bool = True):
    row = db.execute(
        text("SELECT * FROM property.cancellation_policies "
             "WHERE property_id = :p AND is_default LIMIT 1"),
        {"p": property_id},
    ).mappings().first()
    if row is None and not required:
        return _WAIVED_NO_POLICY
    if row is None:
        raise HTTPException(
            status_code=422,
            detail="This property has no cancellation policy configured, so a "
                   "penalty cannot be calculated.",
        )
    return row


def _paid(db: Session, reservation_id: uuid.UUID):
    row = db.execute(
        text(
            """
            SELECT f.id AS folio_id,
                   COALESCE(SUM(e.amount) FILTER (WHERE e.entry_type='credit'), 0)
                     AS paid
            FROM finance.folios f
            LEFT JOIN finance.folio_entries e ON e.folio_id = f.id
            WHERE f.reservation_id = :r
            GROUP BY f.id LIMIT 1
            """
        ),
        {"r": reservation_id},
    ).mappings().first()
    return (row["folio_id"], Decimal(row["paid"])) if row else (None, Decimal("0"))


def _may_approve(db: Session, caller: Caller, property_id: uuid.UUID) -> bool:
    from chirala_common.authz import _GRANT_SQL
    if caller.is_service:
        # The platform acting on a fact from outside -- an OTA cancellation
        # the hotel must honour -- not a clerk asking for an exception.
        return True
    if caller.user_id is None:
        return False
    return bool(db.execute(text(_GRANT_SQL), {
        "uid": caller.user_id, "res": "reservations", "act": "approve",
        "prop": property_id}).first())


def _blocked(res, units) -> str | None:
    """Why this booking cannot be changed, in words, or None."""
    if res["status"] == "cancelled":
        return "This reservation is already cancelled."
    if res["status"] == "completed":
        return "This reservation is completed and can no longer be changed."
    if any(u["status"] == "checked_out" for u in units):
        return "The guest has checked out. Changes are no longer possible."
    if any(u["status"] == "checked_in" for u in units):
        return ("The guest is already in house. Use Room Move to change the "
                "room, or Check-out to end the stay early.")
    return None


# --------------------------------------------------------------------------
# Reading
# --------------------------------------------------------------------------
@change_router.get("/reservations/{reservation_id}/change-view",
                   response_model=ChangeView)
def change_view(
    reservation_id: uuid.UUID,
    property_id: uuid.UUID,
    caller: Caller = Depends(require_permission("reservations", "view")),
    db: Session = Depends(get_session),
):
    """The booking as it stands, the policy, and whether it can be changed."""
    assert_property_in_org(db, caller, property_id)
    res = _reservation(db, reservation_id, property_id)
    units = _units(db, reservation_id)
    pol = _policy(db, property_id)
    folio_id, paid = _paid(db, reservation_id)
    blocked = _blocked(res, units)
    types = db.execute(
        text("SELECT id, name, base_rate FROM property.room_types "
             "WHERE property_id = :p ORDER BY name"),
        {"p": property_id},
    ).mappings().all()

    return ChangeView(
        reservation_id=res["id"], number=res["number"], status=res["status"],
        guest_name=res["guest_name"], guest_phone=res["guest_phone"],
        guest_email=res["guest_email"], currency=res["currency"],
        current=_current(db, res, units),
        room_types=[{"id": str(t["id"]), "name": t["name"],
                     "base_rate": str(t["base_rate"] or 0)} for t in types],
        policy=Policy(**{k: pol[k] for k in (
            "name", "free_until_days", "penalty_nights", "requires_approval",
            "approval_above", "policy_text")}),
        cancel_reasons=CANCEL_REASONS, amount_paid=paid, folio_id=folio_id,
        can_modify=blocked is None, can_cancel=blocked is None,
        blocked_reason=blocked,
        can_approve=_may_approve(db, caller, property_id),
    )


def _after_units(units, *, room_type_id, arrival: date, departure: date):
    """The rooms as they would stand after the change.

    A booking can be several kinds of room. Only an explicit room-type change
    moves every room onto one type; a plain change of dates leaves each room
    as the type it was booked as, because collapsing a suite and a deluxe into
    two of whichever happened to be first is not a date change.
    """
    return [
        {
            "id": u["id"],
            "room_type_id": room_type_id or u["room_type_id"],
            "arrival_date": arrival,
            "departure_date": departure,
        }
        for u in units
    ]


def _capacity_problem(db: Session, property_id, after, exclude_res) -> str | None:
    """Whether the proposed rooms could be sold, in words, or None.

    This booking's own current hold is added back, because it is being moved
    rather than added to. Reads without locking, so it is a preview only:
    ``shift_inventory`` is what actually decides, under a lock, at the moment
    the change is applied.
    """
    for rt, day in sorted(after, key=lambda k: (k[1], str(k[0]))):
        need = after[(rt, day)]
        row = db.execute(
            text(
                """
                SELECT rt.name,
                       i.physical_capacity, i.out_of_service, i.held_units,
                       i.reserved_units, i.allotment_units,
                       COALESCE((
                          SELECT count(*) FROM booking.reservation_units ru
                           WHERE ru.property_id = :p
                             AND ru.room_type_id = :rt
                             AND ru.reservation_id = :res
                             AND ru.status NOT IN ('cancelled', 'no_show')
                             AND ru.arrival_date <= :d AND ru.departure_date > :d
                       ), 0) AS mine
                FROM property.room_types rt
                LEFT JOIN booking.room_type_inventory_days i
                       ON i.room_type_id = rt.id
                      AND i.property_id = :p
                      AND i.stay_date = :d
                WHERE rt.id = :rt
                """
            ),
            {"p": property_id, "rt": rt, "d": day, "res": exclude_res},
        ).mappings().first()
        if row is None:
            return "That room type does not belong to this property."
        if row["physical_capacity"] is None:
            return f"{row['name']} is not on sale on {day}."
        # The same sum inventory.py uses to decide a fresh booking, plus this
        # reservation's own hold — it is being moved, not added.
        free = (row["physical_capacity"] - row["out_of_service"]
                - row["held_units"] - row["reserved_units"]
                - row["allotment_units"] + row["mine"])
        if free < need:
            return (f"Only {max(free, 0)} {row['name']} room(s) are free on "
                    f"{day}; {need} needed.")
    return None


@change_router.post("/reservations/{reservation_id}/modify-quote",
                    response_model=ModifyQuote)
def modify_quote(
    reservation_id: uuid.UUID,
    property_id: uuid.UUID,
    body: ProposeIn,
    caller: Caller = Depends(require_permission("reservations", "view")),
    db: Session = Depends(get_session),
):
    """Price the proposed stay and say whether it can actually be had."""
    assert_property_in_org(db, caller, property_id)
    res = _reservation(db, reservation_id, property_id)
    units = _units(db, reservation_id)
    if body.departure_date <= body.arrival_date:
        raise HTTPException(status_code=422,
                            detail="Check-out must be after check-in.")
    current = _current(db, res, units)
    rt = body.room_type_id or current.room_type_id
    name = db.execute(
        text("SELECT name FROM property.room_types WHERE id = :id"),
        {"id": rt},
    ).scalar_one_or_none()
    proposed = _side(
        db, property_id, room_type_id=rt, room_type=name,
        arrival=body.arrival_date, departure=body.departure_date,
        adults=body.adults, children=body.children,
        rooms=max(current.rooms, 1),
    )
    reason = _capacity_problem(
        db, property_id,
        occupancy(_after_units(units, room_type_id=body.room_type_id,
                               arrival=body.arrival_date,
                               departure=body.departure_date)),
        reservation_id,
    )
    return ModifyQuote(
        current=current, proposed=proposed,
        difference=proposed.total_amount - current.total_amount,
        available=reason is None, unavailable_reason=reason,
    )


@change_router.get("/reservations/{reservation_id}/cancel-quote",
                   response_model=CancelQuote)
def cancel_quote(
    reservation_id: uuid.UUID,
    property_id: uuid.UUID,
    caller: Caller = Depends(require_permission("reservations", "view")),
    db: Session = Depends(get_session),
):
    """The penalty the policy produces, and what would go back."""
    assert_property_in_org(db, caller, property_id)
    return _cancel_terms(db, reservation_id, property_id)


def _cancel_terms(db: Session, reservation_id: uuid.UUID,
                  property_id: uuid.UUID, *, waived: bool = False
                  ) -> CancelQuote:
    """The quote itself, shared by the quote screen and the cancellation.

    ``waived`` says the penalty will not be charged, which is the one case a
    missing policy does not matter: there is no penalty to calculate.
    """
    res = _reservation(db, reservation_id, property_id)
    units = _units(db, reservation_id)
    pol = _policy(db, property_id, required=not waived)
    _, paid = _paid(db, reservation_id)
    current = _current(db, res, units)
    today = db.execute(text("SELECT CURRENT_DATE")).scalar_one()

    days_before = (current.arrival_date - today).days
    within = days_before < pol["free_until_days"]
    penalty = Decimal("0")
    if within:
        # One night per room, at the arrival night's rate — the policy's words
        # turned into arithmetic, nothing more.
        penalty = (current.nightly_rate * pol["penalty_nights"]
                   * max(current.rooms, 1))
    refund = max(paid - penalty, Decimal("0"))
    return CancelQuote(
        original=current, days_before_arrival=days_before,
        within_penalty_window=within, penalty_amount=penalty,
        amount_paid=paid, refund_estimate=refund,
        requires_approval=bool(pol["requires_approval"])
        and refund > Decimal(pol["approval_above"]),
        policy=Policy(**{k: pol[k] for k in (
            "name", "free_until_days", "penalty_nights", "requires_approval",
            "approval_above", "policy_text")}),
    )


# --------------------------------------------------------------------------
# Writing
# --------------------------------------------------------------------------
def _state(side: StaySide) -> dict:
    return {
        "arrival": str(side.arrival_date), "departure": str(side.departure_date),
        "nights": side.nights, "room_type": side.room_type,
        "adults": side.adults, "children": side.children, "rooms": side.rooms,
        "total": str(side.total_amount),
    }


@change_router.post("/reservations/{reservation_id}/modify",
                    response_model=ChangeOut)
def modify_reservation(
    reservation_id: uuid.UUID,
    property_id: uuid.UUID,
    body: ModifyIn,
    caller: Caller = Depends(require_permission("reservations", "edit")),
    db: Session = Depends(get_session),
):
    """Apply the change, in one transaction, only if it can be accommodated."""
    assert_property_in_org(db, caller, property_id)
    _check_reason(body.reason)
    res = _reservation(db, reservation_id, property_id, lock=True)
    units = _units(db, reservation_id)
    warnings: list[str] = []

    blocked = _blocked(res, units)
    if blocked:
        raise HTTPException(status_code=422, detail=blocked)
    if body.departure_date <= body.arrival_date:
        raise HTTPException(status_code=422,
                            detail="Check-out must be after check-in.")

    current = _current(db, res, units)
    rt = body.room_type_id or current.room_type_id
    after = _after_units(units, room_type_id=body.room_type_id,
                         arrival=body.arrival_date,
                         departure=body.departure_date)

    unavailable = _capacity_problem(db, property_id, occupancy(after),
                                    reservation_id)
    if unavailable:
        raise HTTPException(status_code=409, detail=unavailable)

    # Move the inventory this booking holds onto the nights it will now
    # occupy. This is the whole point: the units below get new dates either
    # way, and without this the counters go on describing the old stay — so
    # nights added to a booking are never taken off sale and the property can
    # sell a room it has already given away.
    #
    # It runs before the units are touched so that an oversold night is a 409
    # with nothing written, and it locks every affected night, so the check
    # above being a stale read does not matter.
    try:
        shift_inventory(
            db,
            property_id=property_id,
            organization_id=res["organization_id"],
            # A booking still merely held sits in held_units; anything else in
            # reserved_units. Moving the wrong one leaves rooms off sale.
            counter=counter_for(res["status"]),
            delta=occupancy_delta(occupancy(units), occupancy(after)),
            overbooking_allowance=settings.overbooking_allowance,
        )
    except InventoryOversold as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    name = db.execute(
        text("SELECT name FROM property.room_types WHERE id = :id"),
        {"id": rt},
    ).scalar_one_or_none()
    proposed = _side(db, property_id, room_type_id=rt, room_type=name,
                     arrival=body.arrival_date, departure=body.departure_date,
                     adults=body.adults, children=body.children,
                     rooms=max(current.rooms, 1))
    difference = proposed.total_amount - current.total_amount

    for u, nxt in zip(units, after):
        db.execute(
            text(
                """
                UPDATE booking.reservation_units
                   SET arrival_date = :a, departure_date = :d,
                       room_type_id = :rt, adults = :ad, children = :ch,
                       version = version + 1
                 WHERE id = :id
                """
            ),
            {"a": body.arrival_date, "d": body.departure_date,
             "rt": nxt["room_type_id"],
             "ad": body.adults, "ch": body.children, "id": u["id"]},
        )
        # A room already assigned may no longer suit the new dates or type, so
        # the hold is released and the room re-picked rather than silently kept.
        if u["assigned_room_id"] is not None:
            db.execute(
                text("UPDATE booking.room_calendar_entries "
                     "SET status = 'released', version = version + 1 "
                     "WHERE reservation_unit_id = :u AND status = 'active'"),
                {"u": u["id"]},
            )
            db.execute(
                text("UPDATE booking.reservation_units "
                     "SET assigned_room_id = NULL, version = version + 1 "
                     "WHERE id = :id"),
                {"id": u["id"]},
            )
            warnings.append(
                f"Room {u['assigned_room_id'] and ''}was released because the "
                f"stay changed — assign a room again before check-in."
            )

    folio_entry_id = None
    folio_id, _ = _paid(db, reservation_id)
    if body.charge_difference and difference != 0 and folio_id is not None:
        folio_entry_id = uuid.uuid4()
        db.execute(
            text(
                """
                INSERT INTO finance.folio_entries
                    (id, organization_id, property_id, folio_id, entry_type,
                     amount, currency, business_date, source_type,
                     source_line_key)
                VALUES (:id, :org, :prop, :folio, :etype, :amt, :cur,
                        CURRENT_DATE, 'reservation_change', :slk)
                """
            ),
            {"id": folio_entry_id, "org": res["organization_id"],
             "prop": property_id, "folio": folio_id,
             "etype": "debit" if difference > 0 else "credit",
             "amt": abs(difference), "cur": res["currency"],
             "slk": f"reservation_change:{uuid.uuid4()}"},
        )
    elif body.charge_difference and difference != 0:
        warnings.append(
            "The price difference was not posted: this reservation has no "
            "folio yet."
        )

    change_id = uuid.uuid4()
    db.execute(
        text(
            """
            INSERT INTO booking.reservation_changes
                (id, organization_id, property_id, reservation_id, kind,
                 status, before_state, after_state, amount_before,
                 amount_after, difference, folio_entry_id, reason, notes,
                 created_by)
            VALUES (:id, :org, :prop, :res, 'modification', 'applied',
                    CAST(:before AS jsonb), CAST(:after AS jsonb), :ab, :aa,
                    :diff, :fe, :reason, :notes, :who)
            """
        ),
        {"id": change_id, "org": res["organization_id"], "prop": property_id,
         "res": reservation_id, "before": json.dumps(_state(current)),
         "after": json.dumps(_state(proposed)),
         "ab": current.total_amount, "aa": proposed.total_amount,
         "diff": difference, "fe": folio_entry_id, "reason": body.reason,
         "notes": body.notes, "who": caller.user_id},
    )
    record_audit(
        db, action="reservation.modified", entity_type="reservation",
        entity_id=str(reservation_id), organization_id=res["organization_id"],
        property_id=property_id, actor_subject=caller.subject,
        reason=body.notes, before=_state(current), after=_state(proposed),
    )
    return ChangeOut(id=change_id, kind="modification", status="applied",
                     difference=difference, penalty_amount=Decimal("0"),
                     refund_estimate=Decimal("0"), warnings=warnings)


@change_router.post("/reservations/{reservation_id}/cancel",
                    response_model=ChangeOut)
def cancel_reservation(
    reservation_id: uuid.UUID,
    property_id: uuid.UUID,
    body: CancelIn,
    caller: Caller = Depends(require_permission("reservations", "cancel")),
    db: Session = Depends(get_session),
):
    """Cancel the booking, charge the policy's penalty, release the rooms."""
    assert_property_in_org(db, caller, property_id)
    _check_reason(body.reason)
    res = _reservation(db, reservation_id, property_id, lock=True)
    units = _units(db, reservation_id)
    warnings: list[str] = []

    blocked = _blocked(res, units)
    if blocked:
        raise HTTPException(status_code=422, detail=blocked)
    if body.reason not in {r["code"] for r in CANCEL_REASONS}:
        raise HTTPException(status_code=422, detail="Unknown cancellation reason")

    quote = _cancel_terms(db, reservation_id, property_id,
                          waived=body.waive_penalty)
    penalty = quote.penalty_amount
    approves = _may_approve(db, caller, property_id)

    if body.waive_penalty:
        if not approves:
            raise HTTPException(
                status_code=403,
                detail="Waiving the cancellation penalty needs reservations "
                       "approve permission.",
            )
        penalty = Decimal("0")
        warnings.append("The cancellation penalty was waived.")
    refund = max(quote.amount_paid - penalty, Decimal("0"))

    # Over the threshold and unable to approve: record it, leave the booking
    # alive. A cancellation that has not been approved has not happened.
    if quote.requires_approval and not approves:
        change_id = uuid.uuid4()
        db.execute(
            text(
                """
                INSERT INTO booking.reservation_changes
                    (id, organization_id, property_id, reservation_id, kind,
                     status, before_state, amount_before, penalty_amount,
                     refund_estimate, reason, notes, requires_approval,
                     created_by)
                VALUES (:id, :org, :prop, :res, 'cancellation',
                        'pending_approval', CAST(:before AS jsonb), :ab,
                        :pen, :ref, :reason, :notes, true, :who)
                """
            ),
            {"id": change_id, "org": res["organization_id"],
             "prop": property_id, "res": reservation_id,
             "before": json.dumps(_state(quote.original)),
             "ab": quote.original.total_amount, "pen": penalty, "ref": refund,
             "reason": body.reason, "notes": body.notes, "who": caller.user_id},
        )
        record_audit(
            db, action="reservation.cancellation_requested",
            entity_type="reservation", entity_id=str(reservation_id),
            organization_id=res["organization_id"], property_id=property_id,
            actor_subject=caller.subject, reason=body.notes,
            after={"penalty": str(penalty), "refund": str(refund)},
        )
        return ChangeOut(
            id=change_id, kind="cancellation", status="pending_approval",
            difference=Decimal("0"), penalty_amount=penalty,
            refund_estimate=refund,
            warnings=[
                f"A refund of {refund} is above the "
                f"{quote.policy.approval_above} threshold, so the booking has "
                f"NOT been cancelled. The request is recorded and needs a "
                f"manager."
            ],
        )

    # Release everything this booking was holding. The counter is chosen by
    # the booking's state: one still merely held sits in held_units, and
    # decrementing reserved_units instead would leave its rooms off sale for
    # good while taking a room off a booking that never had one.
    shift_inventory(
        db,
        property_id=property_id,
        organization_id=res["organization_id"],
        counter=counter_for(res["status"]),
        delta={k: -n for k, n in occupancy(units).items()},
        overbooking_allowance=settings.overbooking_allowance,
    )
    # A booking picked up from a group block gives its rooms back to the
    # block, not to general sale -- the group agreed those rooms and still
    # has them until its cut-off. ``give_back`` decides whether the block can
    # still take them (it must be open and definite) and otherwise leaves
    # them on sale where the release above put them.
    if res["group_block_id"] is not None and units:
        from . import group_blocks

        returned = group_blocks.give_back(
            db, block_id=res["group_block_id"], property_id=property_id,
            organization_id=res["organization_id"], units=units)
        if returned:
            warnings.append(
                f"{returned} room-night(s) went back to the group block.")
    for u in units:
        db.execute(
            text("UPDATE booking.room_calendar_entries "
                 "SET status = 'released', version = version + 1 "
                 "WHERE reservation_unit_id = :u AND status = 'active'"),
            {"u": u["id"]},
        )
        # Guarded on state as well as locked above: a unit something else
        # already ended is not ended again.
        db.execute(
            text("UPDATE booking.reservation_units SET status = 'cancelled', "
                 "assigned_room_id = NULL, version = version + 1 "
                 "WHERE id = :id AND status NOT IN ('cancelled', 'no_show')"),
            {"id": u["id"]},
        )
    db.execute(
        text("UPDATE booking.reservations SET status = 'cancelled', "
             "version = version + 1 WHERE id = :id AND status <> 'cancelled'"),
        {"id": reservation_id},
    )
    # A booking cancelled while still held leaves its hold row 'held', and
    # the reaper would later find it expired and try to give its rooms back
    # again. Closed here, where the rooms actually went back.
    db.execute(
        text("UPDATE booking.booking_holds SET status = 'released', "
             "updated_at = now() WHERE reservation_id = :id "
             "AND status = 'held'"),
        {"id": reservation_id},
    )

    folio_entry_id = None
    folio_id, _ = _paid(db, reservation_id)
    if penalty > 0:
        if folio_id is None:
            warnings.append(
                "The penalty was not charged: this reservation has no folio."
            )
        else:
            folio_entry_id = uuid.uuid4()
            db.execute(
                text(
                    """
                    INSERT INTO finance.folio_entries
                        (id, organization_id, property_id, folio_id,
                         entry_type, amount, currency, business_date,
                         source_type, source_line_key)
                    VALUES (:id, :org, :prop, :folio, 'debit', :amt, :cur,
                            CURRENT_DATE, 'cancellation_fee', :slk)
                    """
                ),
                {"id": folio_entry_id, "org": res["organization_id"],
                 "prop": property_id, "folio": folio_id, "amt": penalty,
                 "cur": res["currency"],
                 "slk": f"cancellation_fee:{reservation_id}"},
            )
    if refund > 0:
        warnings.append(
            f"A refund of {refund} is due. Process it from the folio — "
            f"cancelling does not move money back on its own."
        )

    change_id = uuid.uuid4()
    db.execute(
        text(
            """
            INSERT INTO booking.reservation_changes
                (id, organization_id, property_id, reservation_id, kind,
                 status, before_state, amount_before, penalty_amount,
                 refund_estimate, folio_entry_id, reason, notes, created_by)
            VALUES (:id, :org, :prop, :res, 'cancellation', 'applied',
                    CAST(:before AS jsonb), :ab, :pen, :ref, :fe, :reason,
                    :notes, :who)
            """
        ),
        {"id": change_id, "org": res["organization_id"], "prop": property_id,
         "res": reservation_id, "before": json.dumps(_state(quote.original)),
         "ab": quote.original.total_amount, "pen": penalty, "ref": refund,
         "fe": folio_entry_id, "reason": body.reason, "notes": body.notes,
         "who": caller.user_id},
    )
    record_audit(
        db, action="reservation.cancelled", entity_type="reservation",
        entity_id=str(reservation_id), organization_id=res["organization_id"],
        property_id=property_id, actor_subject=caller.subject,
        reason=body.notes, before={"status": res["status"]},
        after={"status": "cancelled", "penalty": str(penalty),
               "refund_estimate": str(refund)},
    )
    return ChangeOut(id=change_id, kind="cancellation", status="applied",
                     difference=Decimal("0"), penalty_amount=penalty,
                     refund_estimate=refund, warnings=warnings)


@change_router.get("/reservations/{reservation_id}/changes",
                   response_model=list[dict])
def list_changes(
    reservation_id: uuid.UUID,
    property_id: uuid.UUID,
    caller: Caller = Depends(require_permission("reservations", "view")),
    db: Session = Depends(get_session),
):
    """Every change made to this booking, newest first."""
    assert_property_in_org(db, caller, property_id)
    rows = db.execute(
        text(
            """
            SELECT id, kind, status, before_state, after_state, difference,
                   penalty_amount, refund_estimate, reason, notes, created_at
            FROM booking.reservation_changes
            WHERE reservation_id = :r AND property_id = :p
            ORDER BY created_at DESC LIMIT 50
            """
        ),
        {"r": reservation_id, "p": property_id},
    ).mappings().all()
    return [
        {**dict(r), "id": str(r["id"]),
         "created_at": r["created_at"].isoformat(),
         "difference": str(r["difference"]),
         "penalty_amount": str(r["penalty_amount"]),
         "refund_estimate": str(r["refund_estimate"])}
        for r in rows
    ]


# --------------------------------------------------------------------------
# Extend Stay
# --------------------------------------------------------------------------
# A guest already in the house asking to stay longer. The most ordinary
# request a resort front desk gets, and until now the one change this system
# could not make: `modify` refuses the moment a unit is `checked_in`, and it
# is right to -- it releases the assigned room and re-picks it, which for
# somebody whose suitcase is in room 201 is not a modification but an
# eviction.
#
# So this is its own path rather than `modify` with the guard loosened, and
# it is deliberately the narrowest operation that answers the question:
#
#   * departure only. Arrival cannot move once nights have been slept, and
#     the room type cannot change without moving the guest -- that is Room
#     Move, which exists.
#   * forward only. Shortening an in-house stay is an early check-out, which
#     also exists, and which does things this does not (settling the folio).
#   * the guest does not move. The calendar entry is EXTENDED in place, so
#     the room they are sitting in stays theirs and the nights already slept
#     keep pointing at it.
#
# **Nothing is charged here, and that is not an omission.** The night audit
# accrues a room night for every unit that is `checked_in` and has not
# actually checked out -- it reads the stay, not the planned departure date.
# So the extra nights bill themselves as they happen, at each night's own
# rate, and posting a lump sum here would charge the guest twice.
#
# Which also says what this feature is really fixing. The money was never the
# broken part: a guest who overstayed was always billed. What was broken is
# everything that reads `departure_date` -- the room went back on sale on the
# original departure date and could be sold out from under an occupant,
# housekeeping was told to turn a room that was not leaving, and the due-out
# list was wrong. This closes that, which is why the inventory shift below
# matters more than the quote above it.

class ExtendQuote(BaseModel):
    in_house: bool
    blocked_reason: str | None
    current_departure: date | None
    new_departure: date
    added_nights: int
    rooms: int
    #: The first added night, for display. Each night is priced on its own
    #: date, so a weekend added to a weekday stay is not quoted at the
    #: weekday rate.
    nightly_rate: Decimal
    estimated_amount: Decimal
    available: bool
    unavailable_reason: str | None
    #: Rooms whose own calendar already has somebody else in the added
    #: nights. Distinct from `unavailable_reason`, which is about room-type
    #: inventory: a type can have spare rooms while THIS guest's room is
    #: taken, and the answer to each is different -- sell them another room,
    #: or move them.
    room_conflicts: list[str]


class ExtendIn(BaseModel):
    new_departure_date: date
    reason: str | None = None
    notes: str | None = Field(default=None, max_length=600)


def _extend_blocked(res, units) -> str | None:
    """Why this stay cannot be extended, in words, or None."""
    if res["status"] == "cancelled":
        return "This reservation is cancelled."
    if res["status"] == "completed":
        return "This reservation is completed."
    if not any(u["status"] == "checked_in" for u in units):
        # Not a failure -- the other screen is simply the right one, and
        # saying so beats refusing without a next step.
        return ("Nobody from this booking is in the house yet. Use Modify "
                "Reservation to change the dates.")
    return None


def _in_house_units(units):
    """The rooms actually occupied. Only these get extended.

    A booking can hold a room that has checked out and another still
    occupied, and stretching the departed one would put a room back on hold
    that housekeeping has already turned.
    """
    return [u for u in units if u["status"] == "checked_in"]


def _room_conflicts(db: Session, units, new_departure: date) -> list[str]:
    """Occupied rooms that somebody else already holds in the added nights.

    Read-only and therefore a preview: the exclusion constraint on
    ``room_calendar_entries`` is what actually decides, under a lock, when
    the extension is applied.
    """
    out: list[str] = []
    for u in units:
        if u["assigned_room_id"] is None:
            continue
        clash = db.execute(
            text(
                """
                SELECT rm.code
                  FROM booking.room_calendar_entries e
                  JOIN property.rooms rm ON rm.id = e.room_id
                 WHERE e.room_id = :room
                   AND e.status = 'active'
                   AND e.reservation_unit_id IS DISTINCT FROM :unit
                   AND e.occupied_period && tstzrange(
                         CAST(:from_d AS timestamptz),
                         CAST(:to_d AS timestamptz), '[)')
                 LIMIT 1
                """
            ),
            {"room": u["assigned_room_id"], "unit": u["id"],
             "from_d": datetime.combine(u["departure_date"], time(0, 0),
                                        tzinfo=timezone.utc),
             "to_d": datetime.combine(new_departure, time(23, 59),
                                      tzinfo=timezone.utc)},
        ).scalar()
        if clash:
            out.append(clash)
    return out


def _extend_sides(units, new_departure: date):
    """The occupied rooms as they would stand, for the inventory delta."""
    return [
        {"id": u["id"], "room_type_id": u["room_type_id"],
         "arrival_date": u["arrival_date"], "departure_date": new_departure}
        for u in units
    ]


@change_router.post("/reservations/{reservation_id}/extend-quote",
                    response_model=ExtendQuote)
def extend_quote(
    reservation_id: uuid.UUID,
    property_id: uuid.UUID,
    body: ExtendIn,
    caller: Caller = Depends(require_permission("reservations", "view")),
    db: Session = Depends(get_session),
):
    """What extending this stay would cost, and whether it can be had."""
    assert_property_in_org(db, caller, property_id)
    res = _reservation(db, reservation_id, property_id)
    units = _units(db, reservation_id)
    blocked = _extend_blocked(res, units)
    occupied = _in_house_units(units)

    current_dep = (max(u["departure_date"] for u in occupied)
                   if occupied else None)
    new_dep = body.new_departure_date

    if blocked or current_dep is None:
        return ExtendQuote(
            in_house=False, blocked_reason=blocked,
            current_departure=current_dep, new_departure=new_dep,
            added_nights=0, rooms=0, nightly_rate=Decimal("0"),
            estimated_amount=Decimal("0"), available=False,
            unavailable_reason=blocked, room_conflicts=[],
        )

    if new_dep <= current_dep:
        return ExtendQuote(
            in_house=True, blocked_reason=None,
            current_departure=current_dep, new_departure=new_dep,
            added_nights=0, rooms=len(occupied), nightly_rate=Decimal("0"),
            estimated_amount=Decimal("0"), available=False,
            unavailable_reason=(
                f"The guest is already due to leave on {current_dep}. To end "
                f"the stay sooner, check them out early."),
            room_conflicts=[],
        )

    # Priced per night on its own date, over the ADDED nights only. The
    # nights already slept are charged and settled; re-quoting them would
    # invite somebody to charge the difference twice.
    added = (new_dep - current_dep).days
    # Priced at the rate the booking HOLDS, not the rate calendar's price for
    # those dates. The two differ whenever a guest was sold anything but the
    # published rate, and the night audit accrues `ru.nightly_rate` -- so
    # quoting the calendar told the desk 1,800 for a night the folio then
    # billed at 2,250. A quote that does not match the bill is worse than no
    # quote: the desk says the smaller number out loud to the guest.
    #
    # It also means an extension continues the guest's own rate rather than
    # silently re-pricing their stay, which is what a desk means by "two more
    # nights" and what the guest assumes they agreed to.
    total = Decimal("0")
    for u in occupied:
        total += Decimal(u["nightly_rate"] or 0) * added
    first = Decimal(occupied[0]["nightly_rate"] or 0)

    # The full occupancy after the change, not the delta. `_capacity_problem`
    # compares each night's need against what is free WITH this booking's own
    # hold added back, so it has to see the whole stay; handed a delta it
    # would check only the added nights and, worse, read their `need` as a
    # difference rather than a count.
    reason = _capacity_problem(
        db, property_id,
        occupancy(_extend_sides(occupied, new_dep)),
        reservation_id,
    )
    conflicts = _room_conflicts(db, occupied, new_dep)

    return ExtendQuote(
        in_house=True, blocked_reason=None,
        current_departure=current_dep, new_departure=new_dep,
        added_nights=added, rooms=len(occupied), nightly_rate=first,
        estimated_amount=total,
        available=reason is None and not conflicts,
        unavailable_reason=reason, room_conflicts=conflicts,
    )


@change_router.post("/reservations/{reservation_id}/extend",
                    response_model=ChangeOut)
def extend_stay(
    reservation_id: uuid.UUID,
    property_id: uuid.UUID,
    body: ExtendIn,
    caller: Caller = Depends(require_permission("reservations", "edit")),
    db: Session = Depends(get_session),
):
    """Keep the guest where they are for longer. One transaction."""
    assert_property_in_org(db, caller, property_id)
    _check_reason(body.reason)
    res = _reservation(db, reservation_id, property_id, lock=True)
    units = _units(db, reservation_id)
    warnings: list[str] = []

    blocked = _extend_blocked(res, units)
    if blocked:
        raise HTTPException(status_code=422, detail=blocked)

    occupied = _in_house_units(units)
    current_dep = max(u["departure_date"] for u in occupied)
    new_dep = body.new_departure_date
    if new_dep <= current_dep:
        raise HTTPException(
            status_code=422,
            detail=(f"The guest is already due to leave on {current_dep}. To "
                    f"end the stay sooner, check them out early rather than "
                    f"shortening it here -- an early check-out settles the "
                    f"folio, and this does not."),
        )

    after = _extend_sides(occupied, new_dep)
    delta = occupancy_delta(occupancy(occupied), occupancy(after))

    unavailable = _capacity_problem(db, property_id, occupancy(after),
                                    reservation_id)
    if unavailable:
        raise HTTPException(status_code=409, detail=unavailable)

    # Take the added nights off sale before anything else is written, so an
    # oversold night is a 409 with nothing changed. This is the part that was
    # actually missing: a guest who simply overstayed was always billed by
    # the audit, but their room went back on sale on the original departure
    # date and could be sold to somebody else while they were still in it.
    try:
        shift_inventory(
            db,
            property_id=property_id,
            organization_id=res["organization_id"],
            counter=counter_for(res["status"]),
            delta=delta,
            overbooking_allowance=settings.overbooking_allowance,
        )
    except InventoryOversold as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    for u in occupied:
        db.execute(
            text("UPDATE booking.reservation_units "
                 "SET departure_date = :d, version = version + 1 "
                 "WHERE id = :id"),
            {"d": new_dep, "id": u["id"]},
        )
        if u["assigned_room_id"] is None:
            warnings.append(
                "This room has no room assigned, so nothing was held for the "
                "extra nights -- assign one.")
            continue
        # Extended in place, never released and re-picked: the guest stays
        # put and the nights already slept keep pointing at this room. The
        # check-out TIME of day is carried over rather than reset to the
        # default, so a late check-out already granted is not quietly undone.
        try:
            db.execute(
                text(
                    """
                    UPDATE booking.room_calendar_entries
                       SET occupied_period = tstzrange(
                             lower(occupied_period),
                             (CAST(:d AS date)
                              + CAST(upper(occupied_period) AT TIME ZONE 'UTC'
                                     AS time)) AT TIME ZONE 'UTC',
                             '[)'),
                           version = version + 1
                     WHERE reservation_unit_id = :u AND status = 'active'
                    """
                ),
                {"d": new_dep, "u": u["id"]},
            )
            db.flush()
        except IntegrityError as exc:
            # The GiST exclusion refused: somebody else holds this room in
            # the added nights. Nothing is written, and the honest answer is
            # that this guest cannot stay in THIS room -- which is a room
            # move, not an extension.
            raise HTTPException(
                status_code=409,
                detail=("That room is already booked for the extra nights. "
                        "Move the guest to another room first, then extend."),
            ) from exc

    record_audit(
        db, action="reservation.extended", entity_type="reservation",
        entity_id=str(reservation_id), organization_id=res["organization_id"],
        property_id=property_id, actor_subject=caller.subject,
        reason=body.notes,
        before={"departure": str(current_dep), "rooms": len(occupied)},
        after={"departure": str(new_dep), "rooms": len(occupied),
               "added_nights": (new_dep - current_dep).days,
               "reason_code": body.reason},
    )

    return ChangeOut(
        id=reservation_id, kind="extend", status=res["status"],
        # Nothing is posted: the night audit charges each added night as it
        # reaches it, from the stay rather than the planned departure.
        difference=Decimal("0"), penalty_amount=Decimal("0"),
        refund_estimate=Decimal("0"), warnings=warnings,
    )
