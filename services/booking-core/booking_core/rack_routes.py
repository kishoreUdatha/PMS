"""Reservation Calendar / room rack API (screen 003).

The rack is a picture of one thing: which room is spoken for, when. That is
exactly what ``booking.room_calendar_entries`` records, and it is the same table
the GiST exclusion constraint guards, so a bar drawn here cannot disagree with
what the system will actually allow. Reservations and maintenance blocks are
both entries; they differ only by ``kind``, which is why the rack can show them
side by side without a second source.

A booking with no room yet is still a booking, so it is returned in an
**unassigned lane** under its room type rather than left out. That lane is the
point of the screen: it is the queue of work, and dropping one of those onto a
room is what ``assign_room`` does — through the same constraint, so the rack
cannot be used to double-book.

Occupancy follows the usual hotel definition rather than a naive
occupied/total: rooms out for maintenance are taken out of the denominator, not
counted as sold. Selling 20 of 40 rooms while 4 are out of order is 20/36, not
20/40 — and a rack that claimed otherwise would understate how full the property
really is.
"""

from __future__ import annotations

import uuid
from datetime import date, timedelta
from decimal import Decimal

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

rack_router = APIRouter(tags=["reservation-calendar"], route_class=TransactionalRoute)

# What a bar means, in the legend's words. 'held' is the mockup's "Tentative".
BAR_STATUSES = ("confirmed", "tentative", "checked_in", "checked_out",
                "blocked")
STATUS_LABELS = {
    "confirmed": "Confirmed", "tentative": "Tentative",
    "checked_in": "Checked In", "checked_out": "Checked Out",
    "blocked": "Blocked",
}
MAX_WINDOW_DAYS = 92


# --------------------------------------------------------------------------
# Schemas
# --------------------------------------------------------------------------
class RoomRow(BaseModel):
    id: uuid.UUID
    code: str
    floor: str | None = None
    # A price set on this room specifically, overriding its type's. Null where
    # the room simply charges the type rate -- which is most of them, and the
    # distinction is the point: an override is worth showing, an inherited
    # rate would just repeat the band above it on every line.
    base_rate: Decimal | None = None


class Bar(BaseModel):
    id: str
    room_id: uuid.UUID | None
    room_type_id: uuid.UUID | None
    reservation_unit_id: uuid.UUID | None
    reservation_id: uuid.UUID | None
    number: str | None
    guest_name: str | None
    label: str
    status: str
    status_label: str
    start_date: date
    end_date: date
    nights: int
    adults: int | None = None
    children: int | None = None
    reason: str | None = None
    # Clipped bars keep their true dates but say the stay runs on past the edge
    # of the window, so a partial bar is never read as a short stay.
    starts_before: bool = False
    ends_after: bool = False
    # What the stay has been charged and what has come in. A stayview is where
    # a manager notices an arrival with nothing paid, and without these the
    # only way to find out was to leave the screen.
    has_folio: bool = False
    total: Decimal = Decimal("0")
    paid: Decimal = Decimal("0")
    balance: Decimal = Decimal("0")


class Group(BaseModel):
    room_type_id: uuid.UUID
    name: str
    rooms: list[RoomRow]
    unassigned: list[Bar]
    # One entry per day in the window. A stayview answers two questions at
    # once -- who is in which room, and what is still sellable and at what
    # price -- and without these it only answers the first.
    availability: list[int] = []
    rates: list[Decimal | None] = []
    #: True where the rooms differ in price and ``rates`` is the lowest.
    rate_varies: list[bool] = []
    stop_sell: list[bool] = []


class Stat(BaseModel):
    value: Decimal
    previous: Decimal | None = None
    delta_pct: Decimal | None = None


class RackStats(BaseModel):
    total_bookings: Stat
    occupancy_pct: Stat
    available_tonight: Stat
    revenue: Stat
    total_rooms: int
    rooms_booked: int


class RackOut(BaseModel):
    start_date: date
    end_date: date
    days: list[date]
    # The two footer rows: rooms left to sell, and how full the house is,
    # for each day in the window.
    inventory: list[int] = []
    occupancy_pct: list[Decimal] = []
    # How the house stands on the first day of the window, for the filter
    # chips: all, vacant, occupied, reserved, blocked, due_out, dirty.
    room_states: dict[str, int] = {}
    groups: list[Group]
    bars: list[Bar]
    stats: RackStats
    # Said plainly so the screen can explain an empty-looking rack instead of
    # leaving someone to guess.
    unassigned_count: int


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------
def _pct_change(now: Decimal, before: Decimal) -> Decimal | None:
    """Percent change, or None when there is no baseline to compare against."""
    if before == 0:
        return None
    return ((now - before) / before * 100).quantize(Decimal("0.1"))


def _stat(now, before) -> Stat:
    n = Decimal(now or 0)
    p = Decimal(before or 0)
    return Stat(value=n, previous=p, delta_pct=_pct_change(n, p))


def _window_stats(
    db: Session, property_id: uuid.UUID, start: date, end: date, total_rooms: int
) -> dict:
    """Bookings, sold/blocked room-nights and room revenue for one window."""
    nights = (end - start).days

    row = db.execute(
        text(
            """
            SELECT
              COUNT(DISTINCT ru.reservation_id) AS bookings,
              COALESCE(SUM(
                GREATEST(0, LEAST(ru.departure_date, CAST(:end AS date))
                          - GREATEST(ru.arrival_date, CAST(:start AS date)))
              ), 0) AS room_nights,
              COALESCE(SUM(
                GREATEST(0, LEAST(ru.departure_date, CAST(:end AS date))
                          - GREATEST(ru.arrival_date, CAST(:start AS date)))
                * COALESCE(rt.base_rate, 0)
              ), 0) AS revenue
            FROM booking.reservation_units ru
            JOIN booking.reservations r
              ON r.id = ru.reservation_id AND r.property_id = :prop
            LEFT JOIN property.room_types rt ON rt.id = ru.room_type_id
            WHERE ru.property_id = :prop
              AND ru.status NOT IN ('cancelled', 'no_show')
              AND r.status <> 'cancelled'
              AND ru.arrival_date < CAST(:end AS date)
              AND ru.departure_date > CAST(:start AS date)
            """
        ),
        {"prop": property_id, "start": start, "end": end},
    ).mappings().first()

    # Rooms out of order shrink the denominator; they are not sales.
    blocked = db.execute(
        text(
            """
            SELECT COALESCE(SUM(
                GREATEST(0,
                  LEAST(CAST(upper(occupied_period) AS date), CAST(:end AS date))
                  - GREATEST(CAST(lower(occupied_period) AS date),
                             CAST(:start AS date)))
            ), 0)
            FROM booking.room_calendar_entries
            WHERE property_id = :prop AND status = 'active'
              AND kind <> 'reservation'
              AND lower(occupied_period) < CAST(:end AS timestamptz)
              AND upper(occupied_period) > CAST(:start AS timestamptz)
            """
        ),
        {"prop": property_id, "start": start, "end": end},
    ).scalar_one()

    capacity = max(total_rooms * nights - int(blocked or 0), 0)
    sold = int(row["room_nights"] or 0)
    occupancy = (
        (Decimal(sold) / Decimal(capacity) * 100).quantize(Decimal("0.1"))
        if capacity > 0 else Decimal("0")
    )
    return {
        "bookings": int(row["bookings"] or 0),
        "occupancy": occupancy,
        "revenue": Decimal(row["revenue"] or 0),
    }


# --------------------------------------------------------------------------
# The rack
# --------------------------------------------------------------------------
@rack_router.get("/reservation-calendar", response_model=RackOut)
def reservation_calendar(
    property_id: uuid.UUID,
    start_date: date,
    end_date: date,
    room_type_id: uuid.UUID | None = Query(None),
    status: str | None = Query(None),
    caller: Caller = Depends(require_permission("rooms", "view")),
    db: Session = Depends(get_session),
):
    """Rooms down the side, dates across the top, and what occupies them.

    The window is half-open: ``end_date`` is the morning everyone has left, so a
    one-night stay on the 5th spans 5 -> 6 and occupies one column.
    """
    assert_property_in_org(db, caller, property_id)
    if end_date <= start_date:
        raise HTTPException(status_code=422, detail="End date must be after start")
    if (end_date - start_date).days > MAX_WINDOW_DAYS:
        raise HTTPException(
            status_code=422,
            detail=f"Pick a window of {MAX_WINDOW_DAYS} days or fewer.",
        )
    if status and status not in BAR_STATUSES:
        raise HTTPException(status_code=422, detail="Unknown status")

    days = [start_date + timedelta(days=n)
            for n in range((end_date - start_date).days)]

    # ---- rooms, grouped by type -------------------------------------------
    room_rows = db.execute(
        text(
            """
            SELECT r.id, r.code, r.floor,
                   -- Only when the room costs more than the figure on its
                   -- type's band, which is the cheapest room of the type.
                   -- Comparing against the type's list price instead meant
                   -- that a property which had repriced all its rooms saw the
                   -- same number on the band and on every row beneath it.
                   CASE WHEN COALESCE(r.base_rate, rt.base_rate)
                             IS DISTINCT FROM eff.lo
                        THEN COALESCE(r.base_rate, rt.base_rate) END AS base_rate,
                   rt.id AS room_type_id, rt.name AS room_type
            FROM property.rooms r
            JOIN property.room_types rt ON rt.id = r.room_type_id
            LEFT JOIN LATERAL (
                SELECT min(COALESCE(rm.base_rate, rt.base_rate)) AS lo
                FROM property.rooms rm
                WHERE rm.room_type_id = r.room_type_id
                  AND rm.status = 'active'
            ) eff ON true
            WHERE r.property_id = :prop
              -- Only rooms that can actually be sold. A draft or deactivated
              -- room, or one retired before this window, is not inventory, and
              -- a lane for it would invite someone to put a guest in it.
              AND r.status = 'active'
              AND (r.retired_on IS NULL OR r.retired_on > CAST(:start AS date))
              AND (CAST(:rt AS uuid) IS NULL OR rt.id = CAST(:rt AS uuid))
            ORDER BY rt.name, r.code
            """
        ),
        {"prop": property_id, "rt": room_type_id, "start": start_date},
    ).mappings().all()

    groups: dict[uuid.UUID, dict] = {}
    for r in room_rows:
        g = groups.setdefault(
            r["room_type_id"],
            {"room_type_id": r["room_type_id"], "name": r["room_type"],
             "rooms": [], "unassigned": []},
        )
        g["rooms"].append(RoomRow(id=r["id"], code=r["code"],
                                  floor=r["floor"],
                                  base_rate=r["base_rate"]))

    # ---- bars from the occupancy table ------------------------------------
    entry_rows = db.execute(
        text(
            """
            SELECT e.id, e.room_id, e.kind, e.reason,
                   CAST(lower(e.occupied_period) AS date) AS start_date,
                   CAST(upper(e.occupied_period) AS date) AS end_date,
                   ru.id AS unit_id, ru.status AS unit_status,
                   ru.adults, ru.children, ru.room_type_id,
                   ru.assigned_room_id,
                   res.id AS reservation_id, res.number, res.status AS res_status,
                   g.full_name AS guest_name
            FROM booking.room_calendar_entries e
            LEFT JOIN booking.reservation_units ru
                   ON ru.id = e.reservation_unit_id
            LEFT JOIN booking.reservations res ON res.id = ru.reservation_id
            LEFT JOIN engagement.guests g ON g.id = res.primary_guest_id
            WHERE e.property_id = :prop
              -- A stayview is the record of who was in which room, not only
              -- of what is still sellable. Check-out releases the calendar
              -- entry -- correctly, the room is free again -- but dropping
              -- released entries here made every past date render empty even
              -- when the hotel had been full, and left nobody able to answer
              -- "who was in 201 last Tuesday".
              --
              -- Completed stays are kept; cancelled blocks and abandoned
              -- holds are not. The room-id test is what stops a stay that was
              -- moved between rooms drawing a bar in both: it keeps only the
              -- room the guest actually left from, so one stay is one bar.
              AND (e.status = 'active'
                   OR (e.kind = 'reservation'
                       AND ru.status = 'checked_out'
                       AND e.room_id = ru.assigned_room_id))
              AND lower(e.occupied_period) < CAST(:end AS timestamptz)
              AND upper(e.occupied_period) > CAST(:start AS timestamptz)
            ORDER BY lower(e.occupied_period)
            """
        ),
        {"prop": property_id, "start": start_date, "end": end_date},
    ).mappings().all()

    known_rooms = {r["id"] for r in room_rows}
    bars: list[Bar] = []
    for e in entry_rows:
        if e["room_id"] not in known_rooms:
            continue  # filtered out by room type
        if e["kind"] != "reservation":
            bar_status = "blocked"
            label = e["reason"] or e["kind"].replace("_", " ").title()
        else:
            bar_status = _bar_status(e["unit_status"], e["res_status"])
            label = e["guest_name"] or e["number"] or "Reservation"
        if status and bar_status != status:
            continue
        bars.append(
            Bar(
                id=str(e["id"]), room_id=e["room_id"],
                room_type_id=e["room_type_id"],
                reservation_unit_id=e["unit_id"],
                reservation_id=e["reservation_id"], number=e["number"],
                guest_name=e["guest_name"], label=label,
                status=bar_status, status_label=STATUS_LABELS[bar_status],
                start_date=e["start_date"], end_date=e["end_date"],
                nights=max((e["end_date"] - e["start_date"]).days, 1),
                adults=e["adults"], children=e["children"], reason=e["reason"],
                starts_before=e["start_date"] < start_date,
                ends_after=e["end_date"] > end_date,
            )
        )

    # ---- bookings with no room yet ----------------------------------------
    unassigned_rows = db.execute(
        text(
            """
            SELECT ru.id AS unit_id, ru.room_type_id, ru.arrival_date,
                   ru.departure_date, ru.adults, ru.children,
                   ru.status AS unit_status,
                   res.id AS reservation_id, res.number,
                   res.status AS res_status, g.full_name AS guest_name,
                   rt.name AS room_type
            FROM booking.reservation_units ru
            JOIN booking.reservations res
              ON res.id = ru.reservation_id AND res.property_id = :prop
            LEFT JOIN property.room_types rt ON rt.id = ru.room_type_id
            LEFT JOIN engagement.guests g ON g.id = res.primary_guest_id
            WHERE ru.property_id = :prop
              AND ru.assigned_room_id IS NULL
              AND ru.status NOT IN ('cancelled', 'no_show')
              AND res.status <> 'cancelled'
              AND ru.arrival_date < CAST(:end AS date)
              AND ru.departure_date > CAST(:start AS date)
              AND (CAST(:rt AS uuid) IS NULL
                   OR ru.room_type_id = CAST(:rt AS uuid))
            ORDER BY ru.arrival_date, res.number
            """
        ),
        {"prop": property_id, "start": start_date, "end": end_date,
         "rt": room_type_id},
    ).mappings().all()

    unassigned_total = 0
    for u in unassigned_rows:
        bar_status = _bar_status(u["unit_status"], u["res_status"])
        if status and bar_status != status:
            continue
        # A room type with no rooms can still hold bookings — Garden Villa has
        # bookings and zero keys — so the group is created on demand rather than
        # only from the room list, or those bookings would vanish.
        g = groups.setdefault(
            u["room_type_id"],
            {"room_type_id": u["room_type_id"], "name": u["room_type"] or "—",
             "rooms": [], "unassigned": []},
        )
        g["unassigned"].append(
            Bar(
                id=f"u:{u['unit_id']}", room_id=None,
                room_type_id=u["room_type_id"],
                reservation_unit_id=u["unit_id"],
                reservation_id=u["reservation_id"], number=u["number"],
                guest_name=u["guest_name"],
                label=u["guest_name"] or u["number"] or "Reservation",
                status=bar_status, status_label=STATUS_LABELS[bar_status],
                start_date=u["arrival_date"], end_date=u["departure_date"],
                nights=max((u["departure_date"] - u["arrival_date"]).days, 1),
                adults=u["adults"], children=u["children"],
                starts_before=u["arrival_date"] < start_date,
                ends_after=u["departure_date"] > end_date,
            )
        )
        unassigned_total += 1

    # ---- the four cards ----------------------------------------------------
    total_rooms = int(
        db.execute(
            text("SELECT count(*) FROM property.rooms "
                 "WHERE property_id = :p AND status = 'active' "
                 "AND (retired_on IS NULL OR retired_on > CAST(:d AS date))"),
            {"p": property_id, "d": start_date},
        ).scalar_one()
    )
    span = (end_date - start_date).days
    now = _window_stats(db, property_id, start_date, end_date, total_rooms)
    prev = _window_stats(
        db, property_id, start_date - timedelta(days=span), start_date, total_rooms
    )

    today = db.execute(text("SELECT CURRENT_DATE")).scalar_one()
    occupied_tonight = int(
        db.execute(
            text(
                """
                SELECT count(DISTINCT room_id)
                FROM booking.room_calendar_entries
                WHERE property_id = :p AND status = 'active'
                  AND lower(occupied_period) <= CAST(:d AS timestamptz)
                  AND upper(occupied_period) > CAST(:d AS timestamptz)
                """
            ),
            {"p": property_id, "d": today},
        ).scalar_one()
    )
    rooms_booked = len({b.room_id for b in bars if b.status != "blocked"})

    # ---- what each stay owes ---------------------------------------------
    # One aggregate for every booking on the rack rather than a folio lookup
    # per bar: a full rack is a few hundred bars, and asking per bar turned a
    # single screen into a few hundred round trips.
    res_ids = [b.reservation_id for b in bars if b.reservation_id]
    if res_ids:
        money_rows = db.execute(
            text(
                """
                SELECT f.reservation_id,
                       COALESCE(sum(fe.amount)
                                FILTER (WHERE fe.entry_type = 'debit'), 0)  AS total,
                       COALESCE(sum(fe.amount)
                                FILTER (WHERE fe.entry_type = 'credit'), 0) AS paid
                FROM finance.folios f
                LEFT JOIN finance.folio_entries fe ON fe.folio_id = f.id
                WHERE f.reservation_id = ANY(:ids)
                GROUP BY f.reservation_id
                """
            ),
            {"ids": res_ids},
        ).mappings().all()
        money = {r["reservation_id"]: r for r in money_rows}
        for b in bars:
            row = money.get(b.reservation_id)
            if row is None:
                continue
            b.has_folio = True
            b.total = Decimal(row["total"])
            b.paid = Decimal(row["paid"])
            b.balance = b.total - b.paid

    # ---- what is sellable, and at what price -----------------------------
    # Read from the inventory counters rather than counted off the bars: the
    # counters are what the booking path actually decrements, so a stayview
    # built on them cannot disagree with what the engine will sell.
    inv_rows = db.execute(
        text(
            """
            SELECT i.room_type_id, i.stay_date,
                   GREATEST(i.physical_capacity - i.out_of_service
                            - i.held_units - i.reserved_units, 0) AS free,
                   i.physical_capacity - i.out_of_service AS sellable,
                   -- What a night of this type would actually be charged at.
                   -- Three sources, in order of authority: a date-specific
                   -- rate from the rate calendar, then the rooms' own prices,
                   -- then the type's list price. Reading only the type's
                   -- price meant a property that had priced its rooms
                   -- individually saw a figure none of them sell at.
                   COALESCE(rc.rate, rr.lo, rt.base_rate) AS rate,
                   COALESCE(rc.rate, rr.hi, rt.base_rate) AS rate_hi,
                   COALESCE(rc.stop_sell, false) AS stop_sell
            FROM booking.room_type_inventory_days i
            JOIN property.room_types rt ON rt.id = i.room_type_id
            LEFT JOIN LATERAL (
                SELECT min(COALESCE(rm.base_rate, rt.base_rate)) AS lo,
                       max(COALESCE(rm.base_rate, rt.base_rate)) AS hi
                FROM property.rooms rm
                WHERE rm.room_type_id = i.room_type_id
                  AND rm.status = 'active'
            ) rr ON true
            LEFT JOIN property.rate_calendar_days rc
                   ON rc.property_id = i.property_id
                  AND rc.room_type_id = i.room_type_id
                  AND rc.stay_date = i.stay_date
            WHERE i.property_id = :prop
              AND i.stay_date >= :start AND i.stay_date < :end
            """
        ),
        {"prop": property_id, "start": start_date, "end": end_date},
    ).mappings().all()

    by_type: dict[tuple, dict] = {
        (r["room_type_id"], r["stay_date"]): r for r in inv_rows
    }
    for g in groups.values():
        rows = [by_type.get((g["room_type_id"], d)) for d in days]
        g["availability"] = [int(r["free"]) if r else len(g["rooms"]) for r in rows]
        g["rates"] = [r["rate"] if r and r["rate"] is not None else None
                      for r in rows]
        # True where the rooms of this type are not all the same price, so the
        # figure shown is the cheapest rather than the price.
        g["rate_varies"] = [
            bool(r and r["rate_hi"] is not None and r["rate"] != r["rate_hi"])
            for r in rows
        ]
        g["stop_sell"] = [bool(r["stop_sell"]) if r else False for r in rows]

    # The footer totals every type for the day.
    inventory = [
        sum(g["availability"][i] for g in groups.values())
        for i, _ in enumerate(days)
    ]
    sellable_by_day = [
        sum(int(by_type[(g["room_type_id"], d)]["sellable"])
            if (g["room_type_id"], d) in by_type else len(g["rooms"])
            for g in groups.values())
        for d in days
    ]
    occupancy_pct = [
        (Decimal(sellable - free) / Decimal(sellable) * 100).quantize(Decimal("0.1"))
        if sellable else Decimal("0.0")
        for sellable, free in zip(sellable_by_day, inventory)
    ]

    # ---- how the house stands, for the chips ------------------------------
    # Counted for the first day of the window, which is the day the screen
    # opens on. Occupied and reserved come from the units; blocked and dirty
    # from the room's own state, because a room can be out of service with
    # nobody booked in it at all.
    on = days[0] if days else start_date
    state_rows = db.execute(
        text(
            """
            WITH active AS (
                SELECT r.id,
                       r.service_status,
                       -- operations.room_condition, not the event log.
                       --
                       -- The event log is written by the housekeeping and
                       -- room-status screens; room_condition is written by
                       -- those AND by check-in, checkout, room moves and
                       -- assignment. So a guest leaving marked the room
                       -- dirty in one store and left the other reading
                       -- 'occupied' -- rooms 201 and 202 sat that way, and
                       -- this rack reported 7 rooms not ready while the
                       -- dashboard reported 9, for the same twelve rooms at
                       -- the same moment. Today's Rack already reads
                       -- cleanliness; this now agrees with both of them
                       -- rather than being the third opinion.
                       rc.cleanliness AS hk_status
                FROM property.rooms r
                LEFT JOIN operations.room_condition rc ON rc.room_id = r.id
                WHERE r.property_id = :prop AND r.status = 'active'
            ),
            stays AS (
                SELECT ru.assigned_room_id AS room_id, ru.status,
                       ru.departure_date
                FROM booking.reservation_units ru
                WHERE ru.property_id = :prop
                  AND ru.status IN ('reserved', 'checked_in')
                  AND ru.arrival_date <= :on AND ru.departure_date > :on
            ),
            blocks AS (
                SELECT DISTINCT e.room_id
                FROM booking.room_calendar_entries e
                WHERE e.property_id = :prop AND e.status = 'active'
                  AND e.kind <> 'reservation'
                  AND lower(e.occupied_period) <= CAST(:on AS timestamptz)
                  AND upper(e.occupied_period) > CAST(:on AS timestamptz)
            )
            SELECT
              count(*)                                              AS all_rooms,
              count(*) FILTER (WHERE s.status = 'checked_in')        AS occupied,
              count(*) FILTER (WHERE s.status = 'reserved')          AS reserved,
              count(*) FILTER (WHERE b.room_id IS NOT NULL
                                  OR a.service_status <> 'in_service') AS blocked,
              count(*) FILTER (WHERE s.status = 'checked_in'
                                 AND s.departure_date = :on)         AS due_out,
              -- Not sellable tonight without housekeeping first. 'cleaning'
              -- is a room being done right now and 'dirty' one not started;
              -- both are off the market, and counting only the second left
              -- the chip at zero while eight rooms were unavailable. The
              -- dashboard has always counted the pair.
              count(*) FILTER (WHERE a.hk_status IN ('dirty', 'cleaning'))
                                                                    AS not_ready
            FROM active a
            LEFT JOIN stays s ON s.room_id = a.id
            LEFT JOIN blocks b ON b.room_id = a.id
            """
        ),
        {"prop": property_id, "on": on},
    ).mappings().first()

    room_states = {k: int(v or 0) for k, v in dict(state_rows or {}).items()}
    # Vacant is what is left once every other state is accounted for, so the
    # chips always add up to the house rather than double-counting a room that
    # is both dirty and reserved.
    # Deliberately NOT reduced by not_ready: a vacant dirty room is vacant.
    # It is simply not sellable yet, which is what the not_ready chip is for.
    room_states["vacant"] = max(
        room_states.get("all_rooms", 0)
        - room_states.get("occupied", 0)
        - room_states.get("reserved", 0)
        - room_states.get("blocked", 0),
        0,
    )

    return RackOut(
        start_date=start_date, end_date=end_date, days=days,
        room_states=room_states,
        inventory=inventory, occupancy_pct=occupancy_pct,
        groups=[Group(**g) for g in groups.values()],
        bars=bars,
        stats=RackStats(
            total_bookings=_stat(now["bookings"], prev["bookings"]),
            occupancy_pct=_stat(now["occupancy"], prev["occupancy"]),
            # "Tonight" is a fact about today, not about the window, so there is
            # no previous-period figure to compare it with.
            available_tonight=Stat(value=Decimal(total_rooms - occupied_tonight)),
            revenue=_stat(now["revenue"], prev["revenue"]),
            total_rooms=total_rooms,
            rooms_booked=rooms_booked,
        ),
        unassigned_count=unassigned_total,
    )


def _bar_status(unit_status: str | None, res_status: str | None) -> str:
    """The legend's colours, from the two statuses the system records."""
    if unit_status == "checked_in":
        return "checked_in"
    if unit_status == "checked_out":
        return "checked_out"
    if res_status == "held":
        return "tentative"
    return "confirmed"
