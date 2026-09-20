"""Assign Rooms panel — the arrivals still waiting for a room.

``/rooms/{id}/assignable`` already answers "which bookings could go in this
room". This is the question the front desk actually asks, which is the other
way round: *these people are arriving, what can I put them in?*

Bookings are grouped by room type because that is how the decision is made —
a clerk works through the Suite arrivals, then the Twins. Each unit carries
its own candidate rooms rather than the group sharing one list, because two
units of the same type can have different date spans and therefore different
free rooms.

**The candidate list is a filter, not the authority.** A room is offered when
nothing overlaps it in ``booking.room_calendar_entries`` for the stay's dates
and it is not out of service. The GiST exclusion constraint on that table is
what actually prevents a double-booking, and ``assign_room`` still refuses
with a 409 if the room went in the moment between reading this list and
clicking. Anything else would be a race dressed up as a guarantee.

**Nothing is hidden; unavailable rooms are returned and disabled.** A clerk
is often working from a room number the guest asked for by name, and a list
that silently omits it cannot answer "why not 101?". Every room of the type
comes back with whether it can be taken and, when it cannot, what is in the
way — the booking occupying it, or the service status. Hiding them would also
make the list change shape as the dates change, which is the same reason the
New Reservation picker disables sold-out room types rather than dropping them.

**Dirty rooms are available, and labelled.** A room being cleaned is still
assignable — housekeeping will get to it before the guest walks up — so it is
offered with its condition rather than withheld.
"""

from __future__ import annotations

import uuid
from datetime import date, timedelta

from chirala_common.authz import Caller, assert_property_in_org, build_authz
from chirala_common.routing import TransactionalRoute
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session

from .database import get_session
from .settings import settings

_get_caller, require_permission, _require_org = build_authz(
    get_session, lambda: settings.environment,
    lambda: settings.service_token,
)

assign_router = APIRouter(tags=["front-desk"], route_class=TransactionalRoute)

# Statuses that still need a room. A cancelled or departed booking does not.
OPEN_UNIT_STATUSES = ("reserved", "confirmed", "held")


class CandidateRoom(BaseModel):
    room_id: uuid.UUID
    code: str
    floor: str | None
    cleanliness: str | None
    # False when the room still needs cleaning — assignable, but say so.
    ready: bool
    # False when it cannot be taken at all. The list still carries it so the
    # desk can see why, rather than wondering where room 101 went.
    available: bool
    # 'occupied' | 'out_of_service' | 'retired', or None when free.
    blocked_reason: str | None = None
    # What is in the way — the booking number, or the service status.
    blocked_by: str | None = None


class AssignUnit(BaseModel):
    unit_id: uuid.UUID
    reservation_id: uuid.UUID
    number: str
    guest_name: str | None
    arrival_date: date
    departure_date: date
    nights: int
    adults: int
    children: int
    room_type_id: uuid.UUID | None
    room_type: str | None
    candidates: list[CandidateRoom]
    # How many of them can actually be taken.
    free_count: int = 0


class AssignGroup(BaseModel):
    room_type_id: uuid.UUID | None
    name: str
    count: int
    units: list[AssignUnit]


class DayCell(BaseModel):
    day: date
    unassigned: int
    is_weekend: bool
    is_today: bool


class AssignBoard(BaseModel):
    business_date: date
    on_date: date
    days: list[DayCell]
    groups: list[AssignGroup]
    total_unassigned: int
    can_assign: bool


_UNITS_SQL = """
    SELECT ru.id, ru.reservation_id, ru.arrival_date, ru.departure_date,
           ru.adults, ru.children, ru.room_type_id,
           rt.name AS room_type,
           r.number, g.full_name AS guest_name
    FROM booking.reservation_units ru
    JOIN booking.reservations r ON r.id = ru.reservation_id
    LEFT JOIN property.room_types rt ON rt.id = ru.room_type_id
    LEFT JOIN engagement.guests g ON g.id = r.primary_guest_id
    WHERE ru.property_id = :prop
      AND ru.assigned_room_id IS NULL
      AND ru.status = ANY(:open)
      AND ru.arrival_date = :d
    ORDER BY rt.name NULLS LAST, ru.arrival_date, r.number
"""

# Free means nothing overlaps the stay in the calendar the exclusion
# constraint guards. Plain date bounds handle same-day turnover correctly:
# [10, 12) and [12, 14) do not overlap.
_CANDIDATES_SQL = """
    SELECT rm.id, rm.code, rm.floor, rm.service_status, rm.retired_on,
           -- No housekeeping record means nobody has stayed in it, not that
           -- it is dirty. Without the default, every room in a property that
           -- has just opened is offered as "needs cleaning".
           COALESCE(rc.cleanliness, 'clean') AS cleanliness,
           (SELECT string_agg(DISTINCT COALESCE(r.number, e.kind), ', ')
              FROM booking.room_calendar_entries e
              LEFT JOIN booking.reservation_units ru ON ru.id = e.reservation_unit_id
              LEFT JOIN booking.reservations r ON r.id = ru.reservation_id
             WHERE e.room_id = rm.id
               -- Only live entries block. Checking out releases the entry but
               -- leaves its period spanning the *scheduled* departure, so
               -- without this a guest who left this morning went on holding
               -- the room until the date they were originally due to leave --
               -- offered to the desk as "Occupied" by a booking already
               -- closed. This is the rule the exclusion constraint itself
               -- uses (WHERE status = 'active'), and what check-in and room
               -- moves already ask for.
               AND e.status = 'active'
               AND e.occupied_period && tstzrange(CAST(:arr AS timestamptz),
                                                  CAST(:dep AS timestamptz))
           ) AS clash
    FROM property.rooms rm
    LEFT JOIN operations.room_condition rc ON rc.room_id = rm.id
    WHERE rm.property_id = :prop
      AND rm.room_type_id = :rt
    ORDER BY rm.code
"""

_STRIP_SQL = """
    SELECT ru.arrival_date, count(*) AS n
    FROM booking.reservation_units ru
    WHERE ru.property_id = :prop
      AND ru.assigned_room_id IS NULL
      AND ru.status = ANY(:open)
      AND ru.arrival_date >= :from_d AND ru.arrival_date < :to_d
    GROUP BY ru.arrival_date
"""


@assign_router.get("/assign-board", response_model=AssignBoard)
def assign_board(
    property_id: uuid.UUID,
    on_date: date | None = Query(None),
    days: int = Query(7, ge=1, le=31),
    caller: Caller = Depends(require_permission("front_desk", "view")),
    db: Session = Depends(get_session),
):
    """Arrivals on one day that still need a room, and what they can go in."""
    from chirala_common.authz import _GRANT_SQL

    assert_property_in_org(db, caller, property_id)
    today = db.execute(text("SELECT CURRENT_DATE")).scalar_one()
    day = on_date or today
    open_list = list(OPEN_UNIT_STATUSES)

    counts = {
        r[0]: int(r[1])
        for r in db.execute(
            text(_STRIP_SQL),
            {"prop": property_id, "open": open_list,
             "from_d": day, "to_d": day + timedelta(days=days)},
        )
    }
    strip = [
        DayCell(day=day + timedelta(days=i),
                unassigned=counts.get(day + timedelta(days=i), 0),
                is_weekend=(day + timedelta(days=i)).weekday() >= 5,
                is_today=(day + timedelta(days=i)) == today)
        for i in range(days)
    ]

    rows = db.execute(
        text(_UNITS_SQL), {"prop": property_id, "open": open_list, "d": day}
    ).mappings().all()

    groups: dict[uuid.UUID | None, AssignGroup] = {}
    for u in rows:
        candidates: list[CandidateRoom] = []
        if u["room_type_id"]:
            for c in db.execute(
                text(_CANDIDATES_SQL),
                {"prop": property_id, "rt": u["room_type_id"],
                 "arr": u["arrival_date"], "dep": u["departure_date"]},
            ).mappings():
                reason = by = None
                if c["retired_on"] and c["retired_on"] <= u["arrival_date"]:
                    reason, by = "retired", str(c["retired_on"])
                elif (c["service_status"] or "in_service") != "in_service":
                    reason, by = "out_of_service", c["service_status"]
                elif c["clash"]:
                    reason, by = "occupied", c["clash"]
                candidates.append(CandidateRoom(
                    room_id=c["id"], code=c["code"], floor=c["floor"],
                    cleanliness=c["cleanliness"],
                    ready=c["cleanliness"] in ("clean", "inspected"),
                    available=reason is None,
                    blocked_reason=reason, blocked_by=by,
                ))
        unit = AssignUnit(
            unit_id=u["id"], reservation_id=u["reservation_id"],
            number=u["number"], guest_name=u["guest_name"],
            arrival_date=u["arrival_date"], departure_date=u["departure_date"],
            nights=(u["departure_date"] - u["arrival_date"]).days,
            adults=u["adults"], children=u["children"],
            room_type_id=u["room_type_id"], room_type=u["room_type"],
            candidates=candidates,
            free_count=sum(1 for c in candidates if c.available),
        )
        g = groups.get(u["room_type_id"])
        if g is None:
            g = AssignGroup(room_type_id=u["room_type_id"],
                            name=u["room_type"] or "No room type", count=0,
                            units=[])
            groups[u["room_type_id"]] = g
        g.units.append(unit)
        g.count += 1

    may = db.execute(
        text(_GRANT_SQL),
        {"uid": caller.user_id, "res": "front_desk", "act": "edit",
         "prop": str(property_id)},
    ).first() is not None

    return AssignBoard(
        business_date=today, on_date=day, days=strip,
        groups=sorted(groups.values(), key=lambda g: g.name),
        total_unassigned=len(rows), can_assign=may,
    )


# --------------------------------------------------------------------------
# Free rooms for a stay that does not exist yet (screen 004)
# --------------------------------------------------------------------------
@assign_router.get("/available-rooms", response_model=list[CandidateRoom])
def available_rooms(
    property_id: uuid.UUID,
    room_type_id: uuid.UUID,
    arrival_date: date,
    departure_date: date,
    caller: Caller = Depends(require_permission("front_desk", "view")),
    db: Session = Depends(get_session),
):
    """Rooms of one type, free across a stay, before the booking exists.

    The assign board answers this for a reservation unit that has already been
    created. A booking being taken has no unit yet, so it needs the same
    question asked of the dates alone — same SQL, same rules, so a room shown
    while booking is a room the assign panel would also offer.
    """
    assert_property_in_org(db, caller, property_id)
    if departure_date <= arrival_date:
        raise HTTPException(
            status_code=422, detail="Departure must be after arrival.")

    out: list[CandidateRoom] = []
    for c in db.execute(
        text(_CANDIDATES_SQL),
        {"prop": property_id, "rt": room_type_id,
         "arr": arrival_date, "dep": departure_date},
    ).mappings():
        reason = by = None
        if c["retired_on"] and c["retired_on"] <= arrival_date:
            reason, by = "retired", str(c["retired_on"])
        elif (c["service_status"] or "in_service") != "in_service":
            reason, by = "out_of_service", c["service_status"]
        elif c["clash"]:
            reason, by = "occupied", c["clash"]
        out.append(CandidateRoom(
            room_id=c["id"], code=c["code"], floor=c["floor"],
            cleanliness=c["cleanliness"],
            ready=c["cleanliness"] in ("clean", "inspected"),
            available=reason is None, blocked_reason=reason, blocked_by=by,
        ))
    return out
