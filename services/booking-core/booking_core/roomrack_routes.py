"""Room Rack — the front desk's day at a glance (screen 002).

The Reservation Calendar (screen 003) answers "which room is spoken for, on
which *days*". This screen answers a different question: "what is happening in
this building *today*". Same underlying truth, an hour-by-hour slice of it.

Three things are drawn on a room's row, and all three come from records rather
than from a status field somebody has to remember to update:

* **Stays**, from ``booking.room_calendar_entries`` — the same table the GiST
  exclusion constraint guards, so a bar here cannot disagree with what the
  system will actually allow.
* **Maintenance**, the same table with ``kind = 'maintenance'``.
* **Cleaning**, from ``operations.housekeeping_tasks`` — only tasks that have
  actually been started, because a task nobody has begun is not an event on
  the timeline, it is a plan.

**Where a bar starts and stops.** A stay that began before today starts at the
left edge; one arriving today starts at the hour it actually happened if it
has, and otherwise at the property's published check-in time. Departures work
the same way in reverse. Each bar says which it is — ``time_is_actual`` — so
an expected 2 pm arrival is never mistaken for one that has occurred.

**One thing the mockup shows that is not here: Walk-ins.** Nothing records how
a booking arrived. ``booking.reservations`` has no source column, and the
Booking Source dropdown on New Reservation is collected and then discarded. A
walk-in count could be *guessed* — booked today, arriving today — but that is
an inference dressed as a fact, and a front desk would act on it. The fourth
card counts unassigned arrivals instead, which is both true and a job someone
has to do before the guest reaches the desk.
"""

from __future__ import annotations

import uuid
from datetime import date, time

from chirala_common.authz import Caller, assert_property_in_org, build_authz
from chirala_common.routing import TransactionalRoute
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session

from .database import get_session
from .settings import settings

_get_caller, require_permission, _require_org = build_authz(
    get_session, lambda: settings.environment,
    lambda: settings.service_token,
)

roomrack_router = APIRouter(tags=["front-desk"], route_class=TransactionalRoute)

DAY_MINUTES = 24 * 60

# Lane order. A room with a guest asleep in it is occupied, whatever else is
# flagged against it — a rack that filed room 102 under Maintenance because
# somebody ticked a box would make its guest vanish from the front desk's day.
# The maintenance flag is not lost: it rides along as ``out_of_service``.
LANES = ("occupied", "maintenance", "reserved", "dirty", "available")

STATE_LABELS = {
    "checked_in": "Checked In",
    "due_out": "Due Out",
    "departed": "Checked Out",
    "arriving": "Arriving",
    "reserved": "Reserved",
    "cleaning": "Cleaning",
    "maintenance": "Maintenance",
}


class RackBar(BaseModel):
    id: str
    kind: str  # reservation | maintenance | housekeeping
    state: str
    label: str
    sublabel: str
    start_minute: int
    end_minute: int
    # The stay continues past the edge of the day rather than starting/ending.
    starts_before: bool = False
    ends_after: bool = False
    # False when the time shown is the property's published time, not a
    # recorded one. An expectation must not read as a fact.
    time_is_actual: bool = False
    reservation_unit_id: uuid.UUID | None = None
    reservation_number: str | None = None
    guest_name: str | None = None


class RackRoom(BaseModel):
    room_id: uuid.UUID
    code: str
    room_type: str | None = None
    floor: str | None = None
    building: str | None = None
    service_status: str | None = None
    # True when the room is flagged out of service. Reported alongside the lane
    # rather than instead of it, so an occupied room can be both.
    out_of_service: bool = False
    cleanliness: str | None = None
    photo_url: str | None = None
    lane: str
    bars: list[RackBar]


class RackKpis(BaseModel):
    arrivals: int
    departures: int
    pending_check_ins: int
    unassigned_arrivals: int


class RoomRackOut(BaseModel):
    business_date: date
    checkin_time: str
    checkout_time: str
    # Minutes past midnight, so the UI can draw a "now" line without trusting
    # the browser clock against the property's business date.
    now_minute: int | None
    rooms: list[RackRoom]
    kpis: RackKpis
    lane_counts: dict[str, int]
    total_rooms: int


def _parse_minutes(raw: str | None, fallback: int) -> int:
    """``property.checkin_time`` is a varchar, not a time. Read it defensively."""
    if not raw:
        return fallback
    try:
        parts = str(raw).strip().split(":")
        return int(parts[0]) * 60 + (int(parts[1]) if len(parts) > 1 else 0)
    except (ValueError, IndexError):
        return fallback


def _minutes_of(value) -> int | None:
    if value is None:
        return None
    if isinstance(value, time):
        return value.hour * 60 + value.minute
    return None


_ROOMS_SQL = """
    SELECT rm.id, rm.code, rm.floor, rm.building, rm.service_status,
           rt.name AS room_type,
           COALESCE(rc.cleanliness, 'clean') AS cleanliness,
           (SELECT p.url FROM property.room_photos p
             WHERE p.room_id = rm.id
             ORDER BY p.is_primary DESC, p.sort_order
             LIMIT 1) AS photo_url
    FROM property.rooms rm
    LEFT JOIN property.room_types rt ON rt.id = rm.room_type_id
    LEFT JOIN operations.room_condition rc ON rc.room_id = rm.id
    WHERE rm.property_id = :prop
      AND (rm.retired_on IS NULL OR rm.retired_on > :d)
    ORDER BY rm.floor NULLS FIRST, rm.code
"""

# Stays touching the day, with the times they actually happened where those
# exist. LEFT JOINs throughout: a bar must still be drawn for a booking whose
# guest record or check-in row is missing.
_STAYS_SQL = """
    SELECT rce.id AS entry_id, rce.room_id, rce.kind, rce.reason,
           CAST(lower(rce.occupied_period) AS date) AS entry_from,
           CAST(upper(rce.occupied_period) AS date) AS entry_to,
           ru.id AS unit_id, ru.status AS unit_status,
           ru.arrival_date, ru.departure_date,
           r.number AS reservation_number,
           g.full_name AS guest_name,
           CAST(ci.checked_in_at AT TIME ZONE 'UTC' AS time) AS actual_in,
           CAST(co.checked_out_at AT TIME ZONE 'UTC' AS time) AS actual_out,
           CAST(ci.checked_in_at AS date) AS actual_in_date,
           CAST(co.checked_out_at AS date) AS actual_out_date
    FROM booking.room_calendar_entries rce
    LEFT JOIN booking.reservation_units ru ON ru.id = rce.reservation_unit_id
    LEFT JOIN booking.reservations r ON r.id = ru.reservation_id
    LEFT JOIN engagement.guests g ON g.id = r.primary_guest_id
    LEFT JOIN booking.stay_checkins ci ON ci.reservation_unit_id = ru.id
    LEFT JOIN booking.stay_checkouts co ON co.reservation_unit_id = ru.id
    WHERE rce.property_id = :prop
      AND rce.room_id IS NOT NULL
      -- A released entry keeps the period it was booked for, so an early
      -- checkout left a bar drawn across every night the guest was due to
      -- stay but did not -- a departed guest still shown in the room days
      -- later. Released entries are therefore bounded by the checkout that
      -- ended them: the departure still shows on the day it happened, which
      -- is what the rack draws `actual_out` for, and nothing after it. One
      -- released with no checkout at all (a cancellation) was never occupied
      -- and draws nothing.
      AND (rce.status = 'active'
           OR (co.checked_out_at IS NOT NULL
               AND CAST(:d AS date) <= CAST(co.checked_out_at AS date)))
      AND rce.occupied_period && tstzrange(CAST(:d AS timestamptz),
                                           CAST(:d AS timestamptz) + INTERVAL '1 day')
"""

# Only started tasks. A task nobody has begun is a plan, not an event.
_CLEANING_SQL = """
    SELECT t.id, t.room_id, t.kind, t.state,
           GREATEST(0, FLOOR(EXTRACT(EPOCH FROM
               (t.started_at - CAST(:d AS timestamptz))) / 60))::int AS start_minute,
           LEAST(1440, CEIL(EXTRACT(EPOCH FROM
               (COALESCE(t.finished_at, now()) - CAST(:d AS timestamptz)))
               / 60))::int AS end_minute
    FROM operations.housekeeping_tasks t
    WHERE t.property_id = :prop
      AND t.task_date = :d
      AND t.started_at IS NOT NULL
      AND t.state <> 'cancelled'
"""

_KPI_SQL = """
    SELECT
      count(*) FILTER (
        WHERE ru.arrival_date = :d
          AND ru.status NOT IN ('cancelled', 'no_show')
      ) AS arrivals,
      count(*) FILTER (
        WHERE ru.departure_date = :d
          AND ru.status IN ('checked_in', 'checked_out')
      ) AS departures,
      count(*) FILTER (
        WHERE ru.arrival_date = :d
          AND ru.status NOT IN ('cancelled', 'no_show', 'checked_in', 'checked_out')
      ) AS pending_check_ins,
      count(*) FILTER (
        WHERE ru.arrival_date = :d
          AND ru.status NOT IN ('cancelled', 'no_show', 'checked_out')
          AND ru.assigned_room_id IS NULL
      ) AS unassigned_arrivals
    FROM booking.reservation_units ru
    WHERE ru.property_id = :prop
"""


@roomrack_router.get("/room-rack", response_model=RoomRackOut)
def room_rack(
    property_id: uuid.UUID,
    on_date: date | None = Query(None),
    caller: Caller = Depends(require_permission("rooms", "view")),
    db: Session = Depends(get_session),
):
    """Every room on one day, with what is happening in it and when."""
    assert_property_in_org(db, caller, property_id)
    day = on_date or db.execute(text("SELECT CURRENT_DATE")).scalar_one()

    prop = db.execute(
        text("SELECT checkin_time, checkout_time FROM iam.properties WHERE id = :p"),
        {"p": property_id},
    ).mappings().first()
    checkin_min = _parse_minutes(prop["checkin_time"] if prop else None, 14 * 60)
    checkout_min = _parse_minutes(prop["checkout_time"] if prop else None, 11 * 60)

    # The "now" line only means anything while the day on screen is today.
    today = db.execute(text("SELECT CURRENT_DATE")).scalar_one()
    now_minute = None
    if day == today:
        now_minute = int(
            db.execute(
                text("SELECT EXTRACT(HOUR FROM now()) * 60 + EXTRACT(MINUTE FROM now())")
            ).scalar_one()
        )

    rooms = db.execute(
        text(_ROOMS_SQL), {"prop": property_id, "d": day}
    ).mappings().all()

    bars: dict[uuid.UUID, list[RackBar]] = {}

    for s in db.execute(
        text(_STAYS_SQL), {"prop": property_id, "d": day}
    ).mappings():
        if s["kind"] == "maintenance":
            bar = RackBar(
                id=str(s["entry_id"]), kind="maintenance", state="maintenance",
                label="Maintenance",
                sublabel=(s["reason"] or "Out of service"),
                start_minute=0, end_minute=DAY_MINUTES,
                starts_before=bool(s["entry_from"] and s["entry_from"] < day),
                ends_after=bool(s["entry_to"] and s["entry_to"] > day),
            )
            bars.setdefault(s["room_id"], []).append(bar)
            continue

        status = s["unit_status"] or "reserved"
        arrives_today = s["arrival_date"] == day
        departs_today = s["departure_date"] == day

        if status == "checked_out":
            state = "departed"
        elif status == "checked_in":
            state = "due_out" if departs_today else "checked_in"
        elif arrives_today:
            state = "arriving"
        else:
            state = "reserved"

        actual = False
        if arrives_today:
            mins = _minutes_of(s["actual_in"]) if s["actual_in_date"] == day else None
            start = mins if mins is not None else checkin_min
            actual = mins is not None
        else:
            start = 0

        if departs_today:
            mins = _minutes_of(s["actual_out"]) if s["actual_out_date"] == day else None
            end = mins if mins is not None else checkout_min
            actual = actual or mins is not None
        else:
            end = DAY_MINUTES

        # A same-day arrival and departure, or a clock skew, must not produce a
        # bar of negative width.
        if end <= start:
            end = min(DAY_MINUTES, start + 60)

        bars.setdefault(s["room_id"], []).append(RackBar(
            id=str(s["entry_id"]), kind="reservation", state=state,
            label=s["guest_name"] or "Unnamed guest",
            sublabel=STATE_LABELS[state],
            start_minute=int(start), end_minute=int(end),
            starts_before=not arrives_today,
            ends_after=not departs_today,
            time_is_actual=actual,
            reservation_unit_id=s["unit_id"],
            reservation_number=s["reservation_number"],
            guest_name=s["guest_name"],
        ))

    for c in db.execute(
        text(_CLEANING_SQL), {"prop": property_id, "d": day}
    ).mappings():
        start, end = int(c["start_minute"]), int(c["end_minute"])
        bars.setdefault(c["room_id"], []).append(RackBar(
            id=str(c["id"]), kind="housekeeping", state="cleaning",
            label="Cleaning",
            sublabel=(c["kind"] or "").replace("_", " ").capitalize() or "Housekeeping",
            start_minute=start,
            end_minute=max(end, start + 15),  # a just-started task still needs width
        ))

    out: list[RackRoom] = []
    counts = dict.fromkeys(LANES, 0)
    for r in rooms:
        mine = sorted(bars.get(r["id"], []), key=lambda b: b.start_minute)
        states = {b.state for b in mine}
        oos = (r["service_status"] in ("maintenance", "out_of_order")
               or "maintenance" in states)
        if states & {"checked_in", "due_out"}:
            lane = "occupied"
        elif oos:
            lane = "maintenance"
        elif states & {"arriving", "reserved"}:
            lane = "reserved"
        elif r["cleanliness"] in ("dirty", "cleaning"):
            lane = "dirty"
        else:
            lane = "available"
        counts[lane] += 1
        out.append(RackRoom(
            room_id=r["id"], code=r["code"], room_type=r["room_type"],
            floor=r["floor"], building=r["building"],
            service_status=r["service_status"], out_of_service=oos,
            cleanliness=r["cleanliness"],
            photo_url=r["photo_url"], lane=lane, bars=mine,
        ))

    k = db.execute(text(_KPI_SQL), {"prop": property_id, "d": day}).mappings().first()

    return RoomRackOut(
        business_date=day,
        checkin_time=f"{checkin_min // 60:02d}:{checkin_min % 60:02d}",
        checkout_time=f"{checkout_min // 60:02d}:{checkout_min % 60:02d}",
        now_minute=now_minute,
        rooms=out,
        kpis=RackKpis(**k),
        lane_counts=counts,
        total_rooms=len(out),
    )
