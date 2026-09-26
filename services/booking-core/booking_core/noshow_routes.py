"""No-Show Processing API (screen 051).

The Arrivals list deliberately keeps anyone who never checked in, because a
guest who did not turn up yesterday is still today's problem. Without this
screen that list only grew: there was no way to close out a missed arrival,
charge for it, or give the room back.

A no-show is a distinct outcome, not a cancellation. The guest did not cancel —
they simply did not come — and the difference matters both to the penalty (a
cancellation may be free; a no-show is not) and to any later question about why
a room sat empty. So the unit moves to ``no_show``, which the schema has always
allowed and nothing has ever set.

The penalty is taken from the property's cancellation policy, and its tax from
the same ``finance.tax_rules`` the folio uses — one default tax group plus every
stacking levy, exactly the rule ``finance_service.tax_engine`` applies. A number
invented here would disagree with the guest's bill.
"""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

from chirala_common.audit import record_audit
from chirala_common.authz import Caller, assert_property_in_org, build_authz
from chirala_common.routing import TransactionalRoute
from chirala_common.folio_posting import post_charge
from chirala_common.property_time import trading_day
from chirala_common.tax_engine import compute_tax, resolve_rules
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.orm import Session

from chirala_common import no_show, ota_actions

from .database import get_session
from .inventory import counter_for
from .settings import settings

_get_caller, require_permission, _require_org = build_authz(
    get_session, lambda: settings.environment,
    lambda: settings.service_token,
)

noshow_router = APIRouter(tags=["no-show"], route_class=TransactionalRoute)

#: Served from the shared module, not restated. This list and the night
#: audit's used to be different vocabularies -- the audit defaulted to
#: "first_night", which appears nowhere here -- so the screen and the
#: unattended run could not agree even in principle.
PENALTY_BASES = [{"code": c, "label": no_show.LABELS[c]}
                 for c in no_show.BASES]
DEPOSIT_ACTIONS = [
    {"code": "retain_as_penalty", "label": "Retain as No-show Penalty"},
    {"code": "refund_full", "label": "Refund in full"},
    {"code": "retain_full", "label": "Retain in full"},
]
REASONS = [
    {"code": "did_not_arrive", "label": "Guest did not arrive"},
    {"code": "no_contact", "label": "No contact from guest"},
    {"code": "late_cancellation", "label": "Cancelled too late to re-sell"},
    {"code": "travel_disruption", "label": "Travel disruption"},
    {"code": "other", "label": "Other"},
]


# --------------------------------------------------------------------------
# Schemas
# --------------------------------------------------------------------------
class CandidateRow(BaseModel):
    reservation_unit_id: uuid.UUID
    reservation_id: uuid.UUID
    number: str
    guest_name: str | None
    room_type: str
    room: str | None
    arrival_date: date
    departure_date: date
    nights: int
    days_overdue: int
    unit_status: str
    total_amount: Decimal
    paid: Decimal


class PenaltyOption(BaseModel):
    code: str
    label: str
    base_amount: Decimal
    tax_amount: Decimal
    total: Decimal


class NoShowView(BaseModel):
    reservation_unit_id: uuid.UUID
    reservation_id: uuid.UUID
    number: str
    guest_name: str | None
    guest_email: str | None
    guest_phone: str | None
    guest_city: str | None
    arrival_date: date
    departure_date: date
    nights: int
    adults: int
    children: int
    room_type: str
    room: str | None
    rooms: int
    unit_status: str
    already_no_show: bool
    blocked_reason: str | None

    nightly_rate: Decimal
    room_charges: Decimal
    taxes: Decimal
    total_stay_amount: Decimal
    advance_received: Decimal
    balance_if_proceeds: Decimal

    policy_text: str
    tax_label: str
    penalty_options: list[PenaltyOption]
    deposit_actions: list[dict]
    reasons: list[dict]
    folio_id: uuid.UUID | None
    can_waive: bool
    currency: str


class NoShowIn(BaseModel):
    penalty_basis: str = "one_night_tax"
    deposit_action: str = "retain_as_penalty"
    release_room: bool = True
    reason: str
    notes: str | None = Field(default=None, max_length=500)


class NoShowOut(BaseModel):
    reservation_unit_id: uuid.UUID
    penalty_charged: Decimal
    refund_due: Decimal
    room_released: str | None
    nights_returned: int
    warnings: list[str]


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------
_UNIT_SQL = """
    SELECT ru.id AS unit_id, ru.reservation_id, ru.room_type_id,
           ru.arrival_date, ru.departure_date, ru.adults, ru.children,
           ru.status AS unit_status, ru.assigned_room_id,
           ru.organization_id, ru.property_id,
           r.number, r.currency, r.status AS reservation_status,
           rt.name AS room_type,
           rm.code AS room,
           g.full_name AS guest_name, g.email AS guest_email,
           g.phone AS guest_phone, g.city AS guest_city
    FROM booking.reservation_units ru
    JOIN booking.reservations r ON r.id = ru.reservation_id
    LEFT JOIN property.room_types rt ON rt.id = ru.room_type_id
    LEFT JOIN property.rooms rm ON rm.id = ru.assigned_room_id
    LEFT JOIN engagement.guests g ON g.id = r.primary_guest_id
"""


def _unit(db: Session, unit_id: uuid.UUID, property_id: uuid.UUID, *,
          lock: bool = False):
    """The unit and its booking; with ``lock``, both held for the transaction.

    Marking a no-show releases the unit's nights, and so does a cancellation
    of the same booking and the night audit's own no-show pass. Read without
    a lock, any two of them could see the unit still reserved and each give
    its nights back -- one booking released twice, and the second release
    coming off somebody else's room.

    The reservation is locked first and the unit second, the order the
    change routes take them in (they lock the reservation, then write its
    units), so the two paths queue rather than deadlock.
    """
    if lock:
        db.execute(
            text(
                """
                SELECT r.id FROM booking.reservations r
                 WHERE r.id = (SELECT reservation_id
                                 FROM booking.reservation_units
                                WHERE id = :id AND property_id = :prop)
                 FOR UPDATE
                """
            ),
            {"id": unit_id, "prop": property_id},
        )
        db.execute(
            text("SELECT id FROM booking.reservation_units "
                 "WHERE id = :id AND property_id = :prop FOR UPDATE"),
            {"id": unit_id, "prop": property_id},
        )
    row = db.execute(
        text(_UNIT_SQL + " WHERE ru.id = :id AND ru.property_id = :prop"),
        {"id": unit_id, "prop": property_id},
    ).mappings().first()
    if row is None:
        raise HTTPException(status_code=404, detail="Reservation unit not found")
    return row


def _rate(db: Session, property_id, room_type_id, on: date) -> Decimal:
    return Decimal(db.execute(
        text(
            """
            SELECT COALESCE(
                (SELECT rate FROM property.rate_calendar_days
                  WHERE property_id = :p AND room_type_id = :rt
                    AND stay_date = :d),
                (SELECT base_rate FROM property.room_types WHERE id = :rt), 0)
            """
        ),
        {"p": property_id, "rt": room_type_id, "d": on},
    ).scalar_one())


def _room_tax(db: Session, property_id: uuid.UUID, amount: Decimal,
              units: int) -> tuple[Decimal, str]:
    """Tax on a room charge, by the engine the folio uses.

    This used to be a mirror of ``finance_service.tax_engine`` -- a second
    reading of the same table with its own arithmetic -- and the two had
    already drifted: the mirror ignored which rules are inclusive, rounded
    once instead of per component, and multiplied every flat levy by units
    whatever basis it declared. A penalty quoted here then posted as a
    different number. Now it is the engine itself, shared through
    ``chirala_common``, and the quote is what the posting will charge.
    """
    rules = resolve_rules(db, property_id=property_id, category="rooms",
                          on_date=trading_day(db, property_id))
    result = compute_tax(rules, amount=amount, units=units, nights=units)
    labels = [
        f"{ln.tax_code} {ln.rate_snapshot}{'%' if ln.rate_type == 'percent' else ''}"
        for ln in result.lines if ln.apply_as == "exclusive"
    ]
    return (result.exclusive_total.quantize(Decimal("0.01")),
            ", ".join(labels) or "No room taxes configured")


def _financials(db: Session, row, rooms: int):
    """Room charges, tax and what has been paid, for the stay as booked."""
    nights = (row["departure_date"] - row["arrival_date"]).days
    charges = Decimal("0")
    d = row["arrival_date"]
    while d < row["departure_date"]:
        charges += _rate(db, row["property_id"], row["room_type_id"], d)
        d = date.fromordinal(d.toordinal() + 1)
    charges *= rooms
    taxes, tax_label = _room_tax(db, row["property_id"], charges, nights * rooms)
    folio = db.execute(
        text(
            """
            SELECT f.id,
                   COALESCE(SUM(e.amount) FILTER (WHERE e.entry_type='credit'), 0)
                     AS paid
            FROM finance.folios f
            LEFT JOIN finance.folio_entries e ON e.folio_id = f.id
            WHERE f.reservation_id = :r GROUP BY f.id LIMIT 1
            """
        ),
        {"r": row["reservation_id"]},
    ).mappings().first()
    paid = Decimal(folio["paid"]) if folio else Decimal("0")
    return {
        "nightly": _rate(db, row["property_id"], row["room_type_id"],
                         row["arrival_date"]),
        "charges": charges, "taxes": taxes, "tax_label": tax_label,
        "total": charges + taxes, "paid": paid,
        "folio_id": folio["id"] if folio else None,
    }


def _penalty_options(db: Session, row, fin, rooms: int) -> list[PenaltyOption]:
    one_night = fin["nightly"] * rooms
    one_night_tax, _ = _room_tax(db, row["property_id"], one_night, rooms)
    return [
        PenaltyOption(code="one_night_tax", label="1 Night Room + Tax",
                      base_amount=one_night, tax_amount=one_night_tax,
                      total=one_night + one_night_tax),
        PenaltyOption(code="one_night", label="1 Night Room only",
                      base_amount=one_night, tax_amount=Decimal("0"),
                      total=one_night),
        PenaltyOption(code="full_stay", label="Full Stay Amount",
                      base_amount=fin["charges"], tax_amount=fin["taxes"],
                      total=fin["total"]),
        PenaltyOption(code="none", label="No Penalty",
                      base_amount=Decimal("0"), tax_amount=Decimal("0"),
                      total=Decimal("0")),
    ]


def _blocked(row, today: date) -> str | None:
    if row["unit_status"] == "no_show":
        return "This arrival is already marked as a no-show."
    if row["unit_status"] == "checked_in":
        return "The guest is in house, so they did arrive."
    if row["unit_status"] in ("checked_out", "cancelled"):
        return f"This unit is {row['unit_status'].replace('_', ' ')}."
    if row["reservation_status"] == "cancelled":
        return "This reservation is cancelled."
    if row["arrival_date"] > today:
        return (f"The guest is not due until {row['arrival_date']}. A no-show "
                f"can only be recorded once the arrival date has passed.")
    return None


def _may_waive(db: Session, caller: Caller, property_id: uuid.UUID) -> bool:
    from chirala_common.authz import _GRANT_SQL
    if caller.user_id is None:
        return False
    return bool(db.execute(text(_GRANT_SQL), {
        "uid": caller.user_id, "res": "reservations", "act": "approve",
        "prop": property_id}).first())


# --------------------------------------------------------------------------
# Reading
# --------------------------------------------------------------------------
@noshow_router.get("/no-show-candidates", response_model=list[CandidateRow])
def candidates(
    property_id: uuid.UUID,
    on_date: date | None = Query(None),
    caller: Caller = Depends(require_permission("reservations", "view")),
    db: Session = Depends(get_session),
):
    """Arrivals that never happened: due on or before the date, still reserved."""
    assert_property_in_org(db, caller, property_id)
    target = on_date or db.execute(text("SELECT CURRENT_DATE")).scalar_one()
    rows = db.execute(
        text(
            _UNIT_SQL
            + """
            WHERE ru.property_id = :prop
              AND ru.status = 'reserved'
              AND r.status IN ('confirmed', 'held')
              AND ru.arrival_date <= :d
            ORDER BY ru.arrival_date, r.number
            LIMIT 200
            """
        ),
        {"prop": property_id, "d": target},
    ).mappings().all()
    out = []
    for r in rows:
        fin = _financials(db, r, 1)
        out.append(CandidateRow(
            reservation_unit_id=r["unit_id"], reservation_id=r["reservation_id"],
            number=r["number"], guest_name=r["guest_name"],
            room_type=r["room_type"] or "—", room=r["room"],
            arrival_date=r["arrival_date"], departure_date=r["departure_date"],
            nights=(r["departure_date"] - r["arrival_date"]).days,
            days_overdue=(target - r["arrival_date"]).days,
            unit_status=r["unit_status"], total_amount=fin["total"],
            paid=fin["paid"],
        ))
    return out


@noshow_router.get("/reservation-units/{unit_id}/no-show-view",
                   response_model=NoShowView)
def no_show_view(
    unit_id: uuid.UUID,
    property_id: uuid.UUID,
    caller: Caller = Depends(require_permission("reservations", "view")),
    db: Session = Depends(get_session),
):
    """The missed arrival, what it was worth, and what a no-show would cost."""
    assert_property_in_org(db, caller, property_id)
    row = _unit(db, unit_id, property_id)
    today = db.execute(text("SELECT CURRENT_DATE")).scalar_one()
    fin = _financials(db, row, 1)
    pol = db.execute(
        text("SELECT policy_text, penalty_nights FROM "
             "property.cancellation_policies WHERE property_id = :p "
             "AND is_default LIMIT 1"),
        {"p": property_id},
    ).mappings().first()

    return NoShowView(
        reservation_unit_id=row["unit_id"], reservation_id=row["reservation_id"],
        number=row["number"], guest_name=row["guest_name"],
        guest_email=row["guest_email"], guest_phone=row["guest_phone"],
        guest_city=row["guest_city"],
        arrival_date=row["arrival_date"], departure_date=row["departure_date"],
        nights=(row["departure_date"] - row["arrival_date"]).days,
        adults=row["adults"], children=row["children"],
        room_type=row["room_type"] or "—", room=row["room"], rooms=1,
        unit_status=row["unit_status"],
        already_no_show=row["unit_status"] == "no_show",
        blocked_reason=_blocked(row, today),
        nightly_rate=fin["nightly"], room_charges=fin["charges"],
        taxes=fin["taxes"], total_stay_amount=fin["total"],
        advance_received=fin["paid"],
        balance_if_proceeds=fin["total"] - fin["paid"],
        policy_text=(pol["policy_text"] if pol else
                     "No cancellation policy is configured for this property."),
        tax_label=fin["tax_label"],
        penalty_options=_penalty_options(db, row, fin, 1),
        deposit_actions=DEPOSIT_ACTIONS, reasons=REASONS,
        folio_id=fin["folio_id"], can_waive=_may_waive(db, caller, property_id),
        currency=row["currency"],
    )


# --------------------------------------------------------------------------
# Marking it
# --------------------------------------------------------------------------
@noshow_router.post("/reservation-units/{unit_id}/no-show",
                    response_model=NoShowOut)
def mark_no_show(
    unit_id: uuid.UUID,
    property_id: uuid.UUID,
    body: NoShowIn,
    caller: Caller = Depends(require_permission("reservations", "edit")),
    db: Session = Depends(get_session),
):
    """Mark the arrival missed: charge the penalty, free the room, close it out."""
    assert_property_in_org(db, caller, property_id)
    row = _unit(db, unit_id, property_id, lock=True)
    today = db.execute(text("SELECT CURRENT_DATE")).scalar_one()
    warnings: list[str] = []

    blocked = _blocked(row, today)
    if blocked:
        raise HTTPException(status_code=422, detail=blocked)
    if body.reason not in {r["code"] for r in REASONS}:
        raise HTTPException(status_code=422, detail="Unknown reason")
    if body.deposit_action not in {d["code"] for d in DEPOSIT_ACTIONS}:
        raise HTTPException(status_code=422, detail="Unknown deposit action")

    fin = _financials(db, row, 1)
    options = {o.code: o for o in _penalty_options(db, row, fin, 1)}
    if body.penalty_basis not in options:
        raise HTTPException(status_code=422, detail="Unknown penalty basis")
    # Charging nothing is a waiver, and waiving is a decision someone has to
    # hold the right to make.
    if body.penalty_basis == "none" and not _may_waive(db, caller, property_id):
        raise HTTPException(
            status_code=403,
            detail="Waiving the no-show penalty needs reservations approve "
                   "permission.",
        )
    chosen = options[body.penalty_basis]

    # --- charge -----------------------------------------------------------
    if chosen.total > 0:
        if fin["folio_id"] is None:
            warnings.append(
                "The penalty was not charged: this reservation has no folio."
            )
        else:
            # Through the shared ledger path. The engine adds the tax as its
            # own debit when the chosen basis carries tax, so the tax is the
            # engine's figure rather than one computed here -- and the line
            # key is the one the night audit uses, so a no-show processed at
            # the desk and again by the audit is charged once.
            nights = (row["departure_date"] - row["arrival_date"]).days
            post_charge(
                db, organization_id=row["organization_id"],
                property_id=row["property_id"], folio_id=fin["folio_id"],
                amount=chosen.base_amount,
                business_date=trading_day(db, row["property_id"]),
                source_type="no_show_penalty", tax_category="rooms",
                taxed=chosen.tax_amount > 0,
                tax_units=(max(nights, 1)
                           if body.penalty_basis == "full_stay" else 1),
                source_line_key=f"no_show_penalty:{unit_id}",
                posted_by=caller.subject,
            )

    # --- what happens to money already held -------------------------------
    refund_due = Decimal("0")
    if body.deposit_action == "refund_full":
        refund_due = fin["paid"]
    elif body.deposit_action == "retain_as_penalty":
        refund_due = max(fin["paid"] - chosen.total, Decimal("0"))
    if refund_due > 0:
        warnings.append(
            f"A refund of {refund_due} is due. Process it from the folio — "
            f"marking a no-show does not move money back on its own."
        )

    # --- close the unit, then release what it held -----------------------
    # The state change comes first and is guarded on the state it expects, so
    # the nights below are returned only by the one writer that actually
    # moved this unit out of 'reserved'. The unit is locked above as well;
    # this is what makes a second release impossible rather than unlikely.
    moved = db.execute(
        text("UPDATE booking.reservation_units SET status = 'no_show', "
             "assigned_room_id = NULL, version = version + 1 "
             "WHERE id = :id AND status = 'reserved'"),
        {"id": unit_id},
    ).rowcount
    if moved != 1:
        raise HTTPException(
            status_code=409,
            detail="This arrival was changed by someone else a moment ago. "
                   "Reload it and try again.")

    released = None
    nights_returned = 0
    if body.release_room:
        db.execute(
            text("UPDATE booking.room_calendar_entries SET status = 'released', "
                 "version = version + 1 "
                 "WHERE reservation_unit_id = :u AND status = 'active'"),
            {"u": unit_id},
        )
        nights = []
        d = row["arrival_date"]
        while d < row["departure_date"]:
            nights.append(d)
            d = date.fromordinal(d.toordinal() + 1)
        if nights:
            # The counter the booking actually occupies: a booking still
            # merely held sits in held_units, and taking it off
            # reserved_units instead would strand the held room and free a
            # confirmed one that was never this booking's.
            counter = counter_for(row["reservation_status"])
            db.execute(
                text(
                    f"""
                    UPDATE booking.room_type_inventory_days
                       SET {counter} = GREATEST({counter} - 1, 0)
                     WHERE property_id = :p AND room_type_id = :rt
                       AND stay_date = ANY(:dates)
                    """
                ),
                {"p": property_id, "rt": row["room_type_id"], "dates": nights},
            )
            nights_returned = len(nights)
        released = row["room"]
    else:
        warnings.append(
            "The room was not released, so it stays blocked for these dates."
        )

    # With every unit missed, the booking itself is over.
    live = db.execute(
        text("SELECT count(*) FROM booking.reservation_units "
             "WHERE reservation_id = :r AND status NOT IN "
             "('no_show', 'cancelled')"),
        {"r": row["reservation_id"]},
    ).scalar_one()
    if live == 0:
        db.execute(
            text("UPDATE booking.reservations SET status = 'completed', "
                 "version = version + 1 WHERE id = :r"),
            {"r": row["reservation_id"]},
        )

    db.execute(
        text(
            """
            INSERT INTO booking.reservation_changes
                (organization_id, property_id, reservation_id, kind, status,
                 before_state, amount_before, penalty_amount, refund_estimate,
                 reason, notes, created_by)
            VALUES (:org, :prop, :res, 'cancellation', 'applied',
                    CAST(:before AS jsonb), :ab, :pen, :ref, 'no_show',
                    :notes, :who)
            """
        ),
        {"org": row["organization_id"], "prop": property_id,
         "res": row["reservation_id"],
         "before": f'{{"kind": "no_show", "room": "{row["room"] or ""}", '
                   f'"arrival": "{row["arrival_date"]}"}}',
         "ab": fin["total"], "pen": chosen.total, "ref": refund_due,
         "notes": body.notes, "who": caller.user_id},
    )
    record_audit(
        db, action="reservation_unit.no_show", entity_type="reservation_unit",
        entity_id=str(unit_id), organization_id=row["organization_id"],
        property_id=property_id, actor_subject=caller.subject,
        reason=body.notes,
        before={"status": row["unit_status"], "room": row["room"]},
        after={"status": "no_show", "penalty": str(chosen.total),
               "basis": body.penalty_basis, "refund_due": str(refund_due),
               "room_released": bool(released)},
    )
    # An OTA booking that no-showed has to be reported to that OTA within 24
    # hours or the hotel pays commission on a room nobody slept in. Nothing
    # used to say so, and nothing recorded whether anyone had done it.
    if ota_actions.raise_action(
            db, organization_id=row["organization_id"],
            property_id=property_id, reservation_id=row["reservation_id"],
            reservation_unit_id=unit_id, reservation_number=row["number"],
            action_type="no_show"):
        warnings.append(
            "This booking came from a channel — report the no-show to them "
            "within 24 hours or the commission stands. It is on the OTA "
            "Actions list.")

    return NoShowOut(
        reservation_unit_id=unit_id, penalty_charged=chosen.total,
        refund_due=refund_due, room_released=released,
        nights_returned=nights_returned, warnings=warnings,
    )

