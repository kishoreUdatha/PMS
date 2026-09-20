"""Room Move and Upgrade API (screen 053).

Moving a guest is not editing a field. A stay that changes room is two facts —
this room until a moment, that room after it — and the schema already says so:
``room_calendar_entries`` is ranged and ``stay_room_segments`` gives one segment
per room. So the old room's entry is **shortened**, not deleted, and the new
room gets its own. Nights already slept stay attached to the room they were
slept in, which is what housekeeping, a minibar dispute and any later audit
actually need.

The whole thing runs in one transaction, and the new entry goes in through the
same GiST exclusion constraint everything else uses. If the target room was
taken in the seconds since the screen loaded, the insert is rejected and the
guest stays exactly where they were.

Two honest limits:

* **An upgrade over the threshold is not performed.** It is recorded as
  ``pending_approval`` and the guest stays put. Approval routing does not exist
  (screen 042 has a queue but no path into it), and quietly moving someone into
  a better room while imagining the paperwork would be worse than waiting.
* **"Reissue key card" is recorded, not done.** There is no door-lock
  integration; nothing is encoded or deactivated.
"""

from __future__ import annotations

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
from .flow import _nights
from .inventory import InventoryOversold, counter_for, shift_inventory
from .settings import settings

_get_caller, require_permission, _require_org = build_authz(
    get_session, lambda: settings.environment,
    lambda: settings.service_token,
)

move_router = APIRouter(tags=["room-move"], route_class=TransactionalRoute)

REASONS = [
    {"code": "upgrade_guest_request", "label": "Upgrade (Guest Request)",
     "upgrade": True},
    {"code": "upgrade_complimentary", "label": "Upgrade (Complimentary)",
     "upgrade": True},
    {"code": "downgrade", "label": "Downgrade", "upgrade": False},
    {"code": "maintenance", "label": "Maintenance / Room Fault", "upgrade": False},
    {"code": "guest_complaint", "label": "Guest Complaint", "upgrade": False},
    {"code": "operational", "label": "Operational", "upgrade": False},
    {"code": "overbooking", "label": "Overbooking", "upgrade": False},
]
REASON_LABELS = {r["code"]: r["label"] for r in REASONS}

# A chargeable upgrade above this needs a manager, mirroring the waiver rule on
# screen 058 rather than inventing a second policy shape.
APPROVAL_THRESHOLD = Decimal("5000")


# --------------------------------------------------------------------------
# Schemas
# --------------------------------------------------------------------------
class RoomCard(BaseModel):
    id: uuid.UUID
    code: str
    room_type_id: uuid.UUID
    room_type: str
    bed_setup: str | None
    view_type: str | None
    floor: str | None
    base_rate: Decimal | None
    photo_url: str | None
    readiness: str
    readiness_label: str


class MoveView(BaseModel):
    reservation_unit_id: uuid.UUID
    reservation_id: uuid.UUID
    number: str
    guest_name: str | None
    guest_phone: str | None
    guest_email: str | None
    arrival_date: date
    departure_date: date
    nights: int
    adults: int
    children: int
    unit_status: str
    stay_balance: Decimal
    currency: str

    current_room: RoomCard | None
    room_types: list[dict]
    approval_threshold: Decimal
    reasons: list[dict]
    can_approve: bool
    # Nights left to move, from today. Zero means there is nothing to move.
    remaining_nights: int


class MoveQuote(BaseModel):
    rate_current: Decimal
    rate_new: Decimal
    rate_difference: Decimal
    nights_applicable: int
    total_additional: Decimal
    requires_approval: bool
    room_type_changes: bool


class MoveIn(BaseModel):
    to_room_id: uuid.UUID
    reason: str
    remarks: str | None = Field(default=None, max_length=500)
    effective_date: date | None = None
    effective_time: str | None = None
    notify_housekeeping: bool = False
    reissue_key: bool = False
    guest_consent: bool = False
    # Post the rate difference to the folio. Off by default: a complimentary
    # upgrade is a move with no money.
    charge_difference: bool = True


class MoveOut(BaseModel):
    id: uuid.UUID
    reference: str
    status: str
    from_room: str
    to_room: str
    effective_at: datetime
    rate_difference: Decimal
    nights_applicable: int
    total_additional: Decimal
    requires_approval: bool
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
           g.full_name AS guest_name, g.phone AS guest_phone,
           g.email AS guest_email,
           s.id AS stay_id
    FROM booking.reservation_units ru
    JOIN booking.reservations r ON r.id = ru.reservation_id
    LEFT JOIN engagement.guests g ON g.id = r.primary_guest_id
    LEFT JOIN booking.stays s
           ON s.reservation_unit_id = ru.id AND s.status = 'in_house'
"""

_READINESS = {
    "clean": ("ready", "Ready"),
    "inspected": ("ready", "Ready"),
    "cleaning": ("cleaning", "Being cleaned"),
    "dirty": ("dirty", "Needs cleaning"),
}


def _unit(db: Session, unit_id: uuid.UUID, property_id: uuid.UUID):
    row = db.execute(
        text(_UNIT_SQL + " WHERE ru.id = :id AND ru.property_id = :prop"),
        {"id": unit_id, "prop": property_id},
    ).mappings().first()
    if row is None:
        raise HTTPException(status_code=404, detail="Reservation unit not found")
    return row


def _nightly_rate(db: Session, property_id: uuid.UUID, room_type_id, on: date):
    """The calendar's rate for the date, else the room type's base rate."""
    return db.execute(
        text(
            """
            SELECT COALESCE(
                (SELECT rate FROM property.rate_calendar_days
                  WHERE property_id = :prop AND room_type_id = :rt
                    AND stay_date = :d),
                (SELECT base_rate FROM property.room_types WHERE id = :rt),
                0)
            """
        ),
        {"prop": property_id, "rt": room_type_id, "d": on},
    ).scalar_one()


def _effective_at(row, body: MoveIn, today: date) -> datetime:
    """When the move takes effect, clamped inside the stay.

    A move cannot take effect before the guest arrived or after they leave —
    outside the stay it would either rewrite history or move nobody.
    """
    d = body.effective_date or max(today, row["arrival_date"])
    if d < row["arrival_date"]:
        d = row["arrival_date"]
    if d >= row["departure_date"]:
        raise HTTPException(
            status_code=422,
            detail="The move must take effect before the guest departs.",
        )
    hh, mm = 14, 0
    if body.effective_time:
        try:
            parts = body.effective_time.split(":")
            hh, mm = int(parts[0]), int(parts[1])
        except (ValueError, IndexError):
            raise HTTPException(status_code=422,
                                detail="Effective time must look like 14:00.")
    return datetime.combine(d, time(hh, mm), tzinfo=timezone.utc)


def _quote(db: Session, row, to_room_type_id, effective: datetime) -> MoveQuote:
    """What the move costs: the nightly gap times the nights it applies to."""
    eff_date = effective.date()
    nights = max((row["departure_date"] - eff_date).days, 0)
    cur = Decimal(_nightly_rate(db, row["property_id"], row["room_type_id"], eff_date))
    new = Decimal(_nightly_rate(db, row["property_id"], to_room_type_id, eff_date))
    diff = new - cur
    total = diff * nights
    return MoveQuote(
        rate_current=cur, rate_new=new, rate_difference=diff,
        nights_applicable=nights,
        total_additional=total if total > 0 else Decimal("0"),
        requires_approval=total > APPROVAL_THRESHOLD,
        room_type_changes=to_room_type_id != row["room_type_id"],
    )


def _may_approve(db: Session, caller: Caller, property_id: uuid.UUID) -> bool:
    from chirala_common.authz import _GRANT_SQL
    if caller.user_id is None:
        return False
    return bool(
        db.execute(
            text(_GRANT_SQL),
            {"uid": caller.user_id, "res": "front_desk", "act": "approve",
             "prop": property_id},
        ).first()
    )


# --------------------------------------------------------------------------
# Reading
# --------------------------------------------------------------------------
@move_router.get(
    "/reservation-units/{unit_id}/room-move-view", response_model=MoveView
)
def room_move_view(
    unit_id: uuid.UUID,
    property_id: uuid.UUID,
    caller: Caller = Depends(require_permission("front_desk", "view")),
    db: Session = Depends(get_session),
):
    """The guest, the room they are in, and what they owe."""
    assert_property_in_org(db, caller, property_id)
    row = _unit(db, unit_id, property_id)
    today = db.execute(text("SELECT CURRENT_DATE")).scalar_one()

    current = None
    if row["assigned_room_id"]:
        current = _room_cards(db, property_id, room_id=row["assigned_room_id"])
        current = current[0] if current else None

    types = db.execute(
        text("SELECT id, name, base_rate FROM property.room_types "
             "WHERE property_id = :p ORDER BY name"),
        {"p": property_id},
    ).mappings().all()

    balance = db.execute(
        text(
            """
            SELECT COALESCE(SUM(CASE WHEN e.entry_type = 'debit'
                                     THEN e.amount ELSE -e.amount END), 0)
            FROM finance.folios f
            JOIN finance.folio_entries e ON e.folio_id = f.id
            WHERE f.reservation_id = :r
            """
        ),
        {"r": row["reservation_id"]},
    ).scalar_one()

    return MoveView(
        reservation_unit_id=row["unit_id"], reservation_id=row["reservation_id"],
        number=row["number"], guest_name=row["guest_name"],
        guest_phone=row["guest_phone"], guest_email=row["guest_email"],
        arrival_date=row["arrival_date"], departure_date=row["departure_date"],
        nights=(row["departure_date"] - row["arrival_date"]).days,
        adults=row["adults"], children=row["children"],
        unit_status=row["unit_status"], stay_balance=Decimal(balance),
        currency=row["currency"], current_room=current,
        room_types=[{"id": str(t["id"]), "name": t["name"],
                     "base_rate": str(t["base_rate"] or 0)} for t in types],
        approval_threshold=APPROVAL_THRESHOLD, reasons=REASONS,
        can_approve=_may_approve(db, caller, property_id),
        remaining_nights=max(
            (row["departure_date"] - max(today, row["arrival_date"])).days, 0),
    )


def _room_cards(db: Session, property_id: uuid.UUID, *, room_id=None,
                room_type_id=None, start=None, end=None) -> list[RoomCard]:
    """Rooms with their picture and readiness, optionally filtered to free ones."""
    sql = """
        SELECT r.id, r.code, r.room_type_id, rt.name AS room_type,
               r.bed_setup, r.view_type, r.floor,
               COALESCE(r.base_rate, rt.base_rate) AS base_rate,
               COALESCE(rc.cleanliness, 'clean') AS cleanliness,
               (SELECT p.url FROM property.room_photos p
                 WHERE p.room_id = r.id ORDER BY p.is_primary DESC,
                       p.sort_order LIMIT 1) AS photo_url
        FROM property.rooms r
        JOIN property.room_types rt ON rt.id = r.room_type_id
        LEFT JOIN operations.room_condition rc ON rc.room_id = r.id
        WHERE r.property_id = :prop
    """
    params: dict = {"prop": property_id}
    if room_id is not None:
        sql += " AND r.id = :rid"
        params["rid"] = room_id
    else:
        sql += " AND r.status = 'active' AND r.service_status = 'in_service'"
        if room_type_id is not None:
            sql += " AND r.room_type_id = :rt"
            params["rt"] = room_type_id
        if start is not None and end is not None:
            # Free means free for the whole remaining stay, checked against the
            # same table the constraint guards.
            sql += """
              AND NOT EXISTS (
                    SELECT 1 FROM booking.room_calendar_entries e
                     WHERE e.room_id = r.id AND e.status = 'active'
                       AND e.occupied_period && tstzrange(
                             CAST(:start AS timestamptz),
                             CAST(:end AS timestamptz), '[)')
              )
            """
            params["start"] = start
            params["end"] = end
    sql += " ORDER BY r.code"
    rows = db.execute(text(sql), params).mappings().all()
    out = []
    for r in rows:
        code, label = _READINESS.get(r["cleanliness"], ("unknown", "Unknown"))
        out.append(
            RoomCard(
                id=r["id"], code=r["code"], room_type_id=r["room_type_id"],
                room_type=r["room_type"], bed_setup=r["bed_setup"],
                view_type=r["view_type"], floor=r["floor"],
                base_rate=r["base_rate"], photo_url=r["photo_url"],
                readiness=code, readiness_label=label,
            )
        )
    return out


@move_router.get(
    "/reservation-units/{unit_id}/move-candidates", response_model=list[RoomCard]
)
def move_candidates(
    unit_id: uuid.UUID,
    property_id: uuid.UUID,
    room_type_id: uuid.UUID | None = Query(None),
    effective_date: date | None = Query(None),
    caller: Caller = Depends(require_permission("front_desk", "view")),
    db: Session = Depends(get_session),
):
    """Rooms the guest could actually be moved into.

    Free for every night from the move onwards, and never the room they are
    already in.
    """
    assert_property_in_org(db, caller, property_id)
    row = _unit(db, unit_id, property_id)
    today = db.execute(text("SELECT CURRENT_DATE")).scalar_one()
    start = effective_date or max(today, row["arrival_date"])
    # An overstay or a departure already reached leaves no nights to move into,
    # and asking the calendar for an empty range is a error, not an empty
    # answer. The view reports remaining_nights so the screen can explain it.
    if start >= row["departure_date"]:
        return []
    cards = _room_cards(
        db, property_id, room_type_id=room_type_id,
        start=datetime.combine(start, time(0, 0), tzinfo=timezone.utc),
        end=datetime.combine(row["departure_date"], time(0, 0),
                             tzinfo=timezone.utc),
    )
    return [c for c in cards if c.id != row["assigned_room_id"]]


@move_router.get(
    "/reservation-units/{unit_id}/move-quote", response_model=MoveQuote
)
def move_quote(
    unit_id: uuid.UUID,
    property_id: uuid.UUID,
    to_room_id: uuid.UUID,
    effective_date: date | None = Query(None),
    caller: Caller = Depends(require_permission("front_desk", "view")),
    db: Session = Depends(get_session),
):
    """What the move would cost, before anyone commits to it."""
    assert_property_in_org(db, caller, property_id)
    row = _unit(db, unit_id, property_id)
    today = db.execute(text("SELECT CURRENT_DATE")).scalar_one()
    to_type = db.execute(
        text("SELECT room_type_id FROM property.rooms "
             "WHERE id = :id AND property_id = :p"),
        {"id": to_room_id, "p": property_id},
    ).scalar_one_or_none()
    if to_type is None:
        raise HTTPException(status_code=404, detail="Room not found")
    eff = _effective_at(row, MoveIn(to_room_id=to_room_id, reason="operational",
                                    effective_date=effective_date), today)
    return _quote(db, row, to_type, eff)


# --------------------------------------------------------------------------
# The move
# --------------------------------------------------------------------------
def _next_reference(db: Session, property_id: uuid.UUID, today: date) -> str:
    stamp = today.strftime("%Y%m%d")
    n = db.execute(
        text("SELECT count(*) FROM booking.room_moves "
             "WHERE property_id = :p AND reference LIKE :like"),
        {"p": property_id, "like": f"RMU-{stamp}-%"},
    ).scalar_one()
    return f"RMU-{stamp}-{n + 1:03d}"


@move_router.post(
    "/reservation-units/{unit_id}/room-move", response_model=MoveOut,
    status_code=201,
)
def create_room_move(
    unit_id: uuid.UUID,
    property_id: uuid.UUID,
    body: MoveIn,
    caller: Caller = Depends(require_permission("front_desk", "edit")),
    db: Session = Depends(get_session),
):
    """Move the guest, in one transaction, or record that approval is needed."""
    assert_property_in_org(db, caller, property_id)
    row = _unit(db, unit_id, property_id)
    today = db.execute(text("SELECT CURRENT_DATE")).scalar_one()
    warnings: list[str] = []

    if body.reason not in REASON_LABELS:
        raise HTTPException(status_code=422, detail="Unknown reason")
    if row["unit_status"] not in ("reserved", "checked_in"):
        raise HTTPException(
            status_code=422,
            detail=f"A unit in status '{row['unit_status']}' cannot be moved.",
        )
    if row["assigned_room_id"] is None:
        raise HTTPException(
            status_code=422,
            detail="This booking has no room yet, so there is nothing to move "
                   "it from. Assign a room instead.",
        )
    if body.to_room_id == row["assigned_room_id"]:
        raise HTTPException(status_code=422,
                            detail="That is the room the guest is already in.")

    to_room = db.execute(
        text("SELECT r.id, r.code, r.room_type_id, r.status, r.service_status, "
             "       rt.name AS room_type "
             "FROM property.rooms r "
             "JOIN property.room_types rt ON rt.id = r.room_type_id "
             "WHERE r.id = :id AND r.property_id = :p"),
        {"id": body.to_room_id, "p": property_id},
    ).mappings().first()
    if to_room is None:
        raise HTTPException(status_code=404, detail="Room not found")
    if to_room["status"] != "active" or to_room["service_status"] != "in_service":
        raise HTTPException(
            status_code=422,
            detail=f"Room {to_room['code']} is not in service.",
        )

    effective = _effective_at(row, body, today)
    quote = _quote(db, row, to_room["room_type_id"], effective)
    from_room = db.execute(
        text("SELECT code FROM property.rooms WHERE id = :id"),
        {"id": row["assigned_room_id"]},
    ).scalar_one()

    needs_approval = (
        quote.requires_approval and body.charge_difference
        and not _may_approve(db, caller, property_id)
    )
    reference = _next_reference(db, property_id, today)

    # Over the threshold and unable to approve: record the request and leave the
    # guest where they are. A move that has not been approved has not happened.
    if needs_approval:
        move_id = uuid.uuid4()
        db.execute(
            text(
                """
                INSERT INTO booking.room_moves
                    (id, organization_id, property_id, reference,
                     reservation_unit_id, stay_id, from_room_id, to_room_id,
                     from_room_type_id, to_room_type_id, effective_at, reason,
                     remarks, rate_current, rate_new, rate_difference,
                     nights_applicable, total_additional, notify_housekeeping,
                     reissue_key, guest_consent, status, requires_approval,
                     created_by)
                VALUES (:id, :org, :prop, :ref, :unit, :stay, :from_room,
                        :to_room, :from_type, :to_type, :eff, :reason, :remarks,
                        :rc, :rn, :rd, :nights, :total, :hk, :key, :consent,
                        'pending_approval', true, :who)
                """
            ),
            _move_params(row, body, to_room, quote, effective, reference,
                         move_id, caller),
        )
        record_audit(
            db, action="room_move.requested", entity_type="reservation_unit",
            entity_id=str(unit_id), organization_id=row["organization_id"],
            property_id=property_id, actor_subject=caller.subject,
            reason=body.remarks,
            after={"reference": reference, "from": from_room,
                   "to": to_room["code"], "total": str(quote.total_additional)},
        )
        return MoveOut(
            id=move_id, reference=reference, status="pending_approval",
            from_room=from_room, to_room=to_room["code"], effective_at=effective,
            rate_difference=quote.rate_difference,
            nights_applicable=quote.nights_applicable,
            total_additional=quote.total_additional, requires_approval=True,
            warnings=[
                f"{quote.total_additional} is above the "
                f"{APPROVAL_THRESHOLD} approval threshold, so the guest has "
                f"NOT been moved. The request is recorded and needs a manager."
            ],
        )

    from_entry_id, to_entry_id = _perform_move(
        db, row, to_room, effective, warnings
    )

    folio_entry_id = None
    if body.charge_difference and quote.total_additional > 0:
        folio_entry_id = _post_difference(db, row, quote, to_room, reference)
        if folio_entry_id is None:
            warnings.append(
                "The rate difference was not charged: this reservation has no "
                "folio yet."
            )

    if body.reissue_key:
        warnings.append(
            "Key reissue recorded. There is no door-lock integration, so no "
            "card was encoded or deactivated."
        )
    if body.notify_housekeeping:
        # The old room genuinely needs cleaning now, and that we can do.
        db.execute(
            text(
                """
                INSERT INTO operations.room_condition
                    (room_id, organization_id, property_id, cleanliness)
                VALUES (:room, :org, :prop, 'dirty')
                ON CONFLICT (room_id) DO UPDATE
                   SET cleanliness = 'dirty', updated_at = now(),
                       version = operations.room_condition.version + 1
                """
            ),
            {"room": row["assigned_room_id"], "org": row["organization_id"],
             "prop": property_id},
        )
    if not body.guest_consent:
        warnings.append("Guest consent was not recorded for this move.")

    move_id = uuid.uuid4()
    params = _move_params(row, body, to_room, quote, effective, reference,
                          move_id, caller)
    params.update({"from_entry": from_entry_id, "to_entry": to_entry_id,
                   "folio_entry": folio_entry_id})
    db.execute(
        text(
            """
            INSERT INTO booking.room_moves
                (id, organization_id, property_id, reference,
                 reservation_unit_id, stay_id, from_room_id, to_room_id,
                 from_room_type_id, to_room_type_id, from_entry_id, to_entry_id,
                 effective_at, reason, remarks, rate_current, rate_new,
                 rate_difference, nights_applicable, total_additional,
                 folio_entry_id, notify_housekeeping, reissue_key,
                 guest_consent, status, requires_approval, created_by)
            VALUES (:id, :org, :prop, :ref, :unit, :stay, :from_room, :to_room,
                    :from_type, :to_type, :from_entry, :to_entry, :eff, :reason,
                    :remarks, :rc, :rn, :rd, :nights, :total, :folio_entry,
                    :hk, :key, :consent, 'completed', false, :who)
            """
        ),
        params,
    )
    record_audit(
        db, action="room_move.completed", entity_type="reservation_unit",
        entity_id=str(unit_id), organization_id=row["organization_id"],
        property_id=property_id, actor_subject=caller.subject,
        reason=body.remarks,
        before={"room": from_room},
        after={"reference": reference, "room": to_room["code"],
               "effective_at": effective.isoformat(),
               "charged": str(quote.total_additional if folio_entry_id else 0)},
    )
    return MoveOut(
        id=move_id, reference=reference, status="completed", from_room=from_room,
        to_room=to_room["code"], effective_at=effective,
        rate_difference=quote.rate_difference,
        nights_applicable=quote.nights_applicable,
        total_additional=quote.total_additional, requires_approval=False,
        warnings=warnings,
    )


def _move_params(row, body, to_room, quote, effective, reference, move_id, caller):
    return {
        "id": move_id, "org": row["organization_id"], "prop": row["property_id"],
        "ref": reference, "unit": row["unit_id"], "stay": row["stay_id"],
        "from_room": row["assigned_room_id"], "to_room": to_room["id"],
        "from_type": row["room_type_id"], "to_type": to_room["room_type_id"],
        "eff": effective, "reason": body.reason, "remarks": body.remarks,
        "rc": quote.rate_current, "rn": quote.rate_new,
        "rd": quote.rate_difference, "nights": quote.nights_applicable,
        "total": quote.total_additional, "hk": body.notify_housekeeping,
        "key": body.reissue_key, "consent": body.guest_consent,
        "who": caller.user_id,
    }


def _perform_move(db: Session, row, to_room, effective: datetime,
                  warnings: list[str]):
    """Shorten the old occupancy, open the new one, and follow the stay across.

    The new entry is inserted through the exclusion constraint, so a room taken
    since the screen loaded fails here and the guest keeps their old room.
    """
    entry = db.execute(
        text(
            """
            SELECT id, occupied_period FROM booking.room_calendar_entries
            WHERE reservation_unit_id = :unit AND status = 'active'
            FOR UPDATE
            """
        ),
        {"unit": row["unit_id"]},
    ).mappings().first()

    from_entry_id = entry["id"] if entry else None
    if entry is not None:
        lower = db.execute(
            text("SELECT lower(occupied_period) FROM "
                 "booking.room_calendar_entries WHERE id = :id"),
            {"id": entry["id"]},
        ).scalar_one()
        if effective <= lower:
            # Moving before the guest ever occupied it: the old entry describes
            # nothing, so it is released rather than left as a zero-length range.
            db.execute(
                text("UPDATE booking.room_calendar_entries "
                     "SET status = 'released', version = version + 1 "
                     "WHERE id = :id"),
                {"id": entry["id"]},
            )
        else:
            db.execute(
                text(
                    """
                    UPDATE booking.room_calendar_entries
                       SET occupied_period = tstzrange(
                             lower(occupied_period), :eff, '[)'),
                           version = version + 1
                     WHERE id = :id
                    """
                ),
                {"id": entry["id"], "eff": effective},
            )

    new_entry_id = uuid.uuid4()
    end = datetime.combine(row["departure_date"], time(11, 0),
                           tzinfo=timezone.utc)
    try:
        db.execute(
            text(
                """
                INSERT INTO booking.room_calendar_entries
                    (id, organization_id, property_id, room_id,
                     reservation_unit_id, kind, occupied_period, status)
                VALUES (:id, :org, :prop, :room, :unit, 'reservation',
                        tstzrange(:start, :end, '[)'), 'active')
                """
            ),
            {"id": new_entry_id, "org": row["organization_id"],
             "prop": row["property_id"], "room": to_room["id"],
             "unit": row["unit_id"], "start": effective, "end": end},
        )
        db.flush()
    except IntegrityError as exc:
        raise HTTPException(
            status_code=409,
            detail=f"Room {to_room['code']} was taken while this screen was "
                   f"open. Pick another room.",
        ) from exc

    # A move between room types has to take the inventory with it. Without
    # this the old type keeps the night reserved and the new type never
    # records it: the rack shows the old room free while the counters still
    # say it is sold, so a room standing empty cannot be booked — and the new
    # type is quietly oversold by one.
    #
    # Only the nights from the move onwards move. The guest genuinely occupied
    # the old room for the nights before it, and rewriting those would make
    # the past disagree with what was actually sold.
    if to_room["room_type_id"] != row["room_type_id"]:
        eff_date = effective.date()
        moving = [d for d in _nights(row["arrival_date"], row["departure_date"])
                  if d >= eff_date]
        if moving:
            counter = counter_for(row["reservation_status"])
            delta: dict[tuple[uuid.UUID, date], int] = {}
            for d in moving:
                delta[(row["room_type_id"], d)] = delta.get(
                    (row["room_type_id"], d), 0) - 1
                delta[(to_room["room_type_id"], d)] = delta.get(
                    (to_room["room_type_id"], d), 0) + 1
            try:
                shift_inventory(
                    db,
                    property_id=row["property_id"],
                    organization_id=row["organization_id"],
                    counter=counter,
                    delta=delta,
                )
            except InventoryOversold as exc:
                raise HTTPException(
                    status_code=409,
                    detail=f"{to_room['room_type']} is fully booked on "
                           f"{exc.stay_date:%d %b %Y}, so this move would "
                           f"oversell it.",
                ) from exc

    db.execute(
        text("UPDATE booking.reservation_units "
             "SET assigned_room_id = :room, room_type_id = :rt, "
             "version = version + 1 WHERE id = :id"),
        {"room": to_room["id"], "rt": to_room["room_type_id"],
         "id": row["unit_id"]},
    )

    # An in-house guest's stay follows them: close the current segment and open
    # one on the new room, so the stay records both rooms and their times.
    if row["stay_id"]:
        db.execute(
            text("UPDATE booking.stay_room_segments SET actual_end_at = :eff "
                 "WHERE stay_id = :stay AND actual_end_at IS NULL"),
            {"stay": row["stay_id"], "eff": effective},
        )
        db.execute(
            text(
                """
                INSERT INTO booking.stay_room_segments
                    (organization_id, property_id, stay_id,
                     room_calendar_entry_id, actual_start_at)
                VALUES (:org, :prop, :stay, :entry, :eff)
                """
            ),
            {"org": row["organization_id"], "prop": row["property_id"],
             "stay": row["stay_id"], "entry": new_entry_id, "eff": effective},
        )
    return from_entry_id, new_entry_id


def _post_difference(db: Session, row, quote: MoveQuote, to_room, reference):
    """Charge the upgrade to the folio, if there is one to charge."""
    folio_id = db.execute(
        text("SELECT id FROM finance.folios WHERE reservation_id = :r "
             "ORDER BY created_at LIMIT 1"),
        {"r": row["reservation_id"]},
    ).scalar_one_or_none()
    if folio_id is None:
        return None
    entry_id = uuid.uuid4()
    db.execute(
        text(
            """
            INSERT INTO finance.folio_entries
                (id, organization_id, property_id, folio_id, entry_type,
                 amount, currency, business_date, source_type, source_line_key)
            VALUES (:id, :org, :prop, :folio, 'debit', :amt, :cur,
                    CURRENT_DATE, 'room_upgrade', :slk)
            """
        ),
        {"id": entry_id, "org": row["organization_id"],
         "prop": row["property_id"], "folio": folio_id,
         "amt": quote.total_additional, "cur": row["currency"],
         "slk": f"room_upgrade:{reference}"},
    )
    return entry_id


@move_router.get("/room-moves", response_model=list[dict])
def list_room_moves(
    property_id: uuid.UUID,
    reservation_unit_id: uuid.UUID | None = Query(None),
    caller: Caller = Depends(require_permission("front_desk", "view")),
    db: Session = Depends(get_session),
):
    """The move history — a room change is a thing that happened, not a state."""
    assert_property_in_org(db, caller, property_id)
    rows = db.execute(
        text(
            """
            SELECT m.id, m.reference, m.status, m.effective_at, m.reason,
                   m.remarks, m.total_additional, m.nights_applicable,
                   m.rate_difference, m.created_at,
                   fr.code AS from_room, tr.code AS to_room,
                   r.number, g.full_name AS guest_name
            FROM booking.room_moves m
            JOIN property.rooms fr ON fr.id = m.from_room_id
            JOIN property.rooms tr ON tr.id = m.to_room_id
            JOIN booking.reservation_units ru ON ru.id = m.reservation_unit_id
            JOIN booking.reservations r ON r.id = ru.reservation_id
            LEFT JOIN engagement.guests g ON g.id = r.primary_guest_id
            WHERE m.property_id = :prop
              AND (CAST(:unit AS uuid) IS NULL
                   OR m.reservation_unit_id = CAST(:unit AS uuid))
            ORDER BY m.created_at DESC
            LIMIT 100
            """
        ),
        {"prop": property_id, "unit": reservation_unit_id},
    ).mappings().all()
    return [
        {**dict(r), "id": str(r["id"]),
         "reason_label": REASON_LABELS.get(r["reason"], r["reason"]),
         "effective_at": r["effective_at"].isoformat(),
         "created_at": r["created_at"].isoformat(),
         "total_additional": str(r["total_additional"]),
         "rate_difference": str(r["rate_difference"])}
        for r in rows
    ]
