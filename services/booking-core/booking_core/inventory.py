"""Concurrency-safe inventory and hold creation (schema §4).

This is the most safety-critical code in the system. It implements the exact
protocol the blueprint requires:

1. One transaction for the whole request.
2. Lock ``booking.room_type_inventory_days`` rows for each requested night with
   ``SELECT ... FOR UPDATE``, in deterministic (property, room_type, date) order,
   to avoid deadlocks between concurrent bookings.
3. Validate every night's sellable count:
   ``sellable = physical_capacity - out_of_service - held_units
              - reserved_units - allotment_units``.
   Overbooking allowance is zero initially.
4. In the same transaction: increment counters, insert reservation + units,
   insert the hold, and enqueue an outbox event.
5. Any single-night shortage rolls back the entire request (no partial-night
   allocation).

Explicit SQL is used deliberately (not the ORM) so lock ordering and counter
updates are exact and predictable. Rows are inserted with conflict-safe handling
so a missing inventory day is created on demand.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal

from chirala_common.outbox import enqueue_event
from sqlalchemy import text
from sqlalchemy.orm import Session
from .settings import settings


class InventoryShortage(Exception):
    """Raised when at least one requested night lacks availability."""

    def __init__(self, stay_date: date, requested: int, sellable: int) -> None:
        self.stay_date = stay_date
        self.requested = requested
        self.sellable = sellable
        super().__init__(
            f"Insufficient inventory on {stay_date}: "
            f"requested {requested}, sellable {sellable}"
        )


class InventoryOversold(Exception):
    """A change would take a night past what the property can sell.

    Distinct from :class:`InventoryShortage`, which is raised before a fresh
    booking takes anything. This one is raised after counters have moved
    inside the transaction, which is the only point at which a *change* can be
    judged: extending a stay releases nothing and takes more, and the two have
    to be weighed together.
    """

    def __init__(self, stay_date: date, room_type: str, short: int) -> None:
        self.stay_date = stay_date
        self.room_type = room_type
        self.short = short
        super().__init__(
            f"{room_type} would be oversold by {short} room(s) on "
            f"{stay_date}."
        )


@dataclass
class HoldLine:
    """One room on the booking.

    A booking is not always N copies of the same room. A family takes a suite
    and a deluxe; a group takes two doubles on breakfast and one on full
    board. Each line carries its own type, plan and occupancy, so the units
    that come out of it can differ — which is what ``reservation_units`` has
    always been able to express and what the API could not previously say.
    """

    room_type_id: uuid.UUID
    units: int = 1
    adults: int = 1
    children: int = 0
    rate_plan_id: uuid.UUID | None = None
    meal_plan_id: uuid.UUID | None = None
    #: What the guest agreed to pay per night for this room, before tax.
    #: Recorded because it is otherwise unknowable afterwards: a desk that
    #: quotes anything but the list price leaves no trace of it, and check-in
    #: then asks the guest for a number they never agreed to.
    nightly_rate: Decimal | None = None


@dataclass
class HoldResult:
    reservation_id: uuid.UUID
    #: The first unit, kept for callers that only ever book one room.
    reservation_unit_id: uuid.UUID
    hold_id: uuid.UUID
    number: str
    #: Every unit created — one per room. ``units=3`` takes three rooms out of
    #: inventory, so it must create three rows to charge and assign.
    reservation_unit_ids: list[uuid.UUID] = field(default_factory=list)


def _nights(arrival: date, departure: date) -> list[date]:
    """Half-open [arrival, departure): one inventory night per stay date."""
    if departure <= arrival:
        raise ValueError("departure_date must be after arrival_date")
    n = (departure - arrival).days
    return [arrival + timedelta(days=i) for i in range(n)]


#: The counters ``shift_inventory`` may move.
#:
#: A booking that is merely held sits in ``held_units`` until it is confirmed,
#: and moving the wrong one leaves rooms off sale forever.
#:
#: ``allotment_units`` is not a booking at all: it is rooms a group block holds
#: off sale without having sold them. It moves through the same function
#: because it obeys the same invariant -- the sellable count must not go
#: negative -- and because a block and a booking competing for the last room
#: must queue behind one another on the same locks. Giving it its own path
#: would be giving it its own lock order, which is how two writers deadlock.
COUNTERS = ("held_units", "reserved_units", "allotment_units")


def counter_for(status: str) -> str:
    """Which counter a reservation in this state occupies."""
    return "held_units" if status == "held" else "reserved_units"


def occupancy(units) -> dict[tuple[uuid.UUID, date], int]:
    """How many rooms of each type these units take, night by night.

    A room type and a stay date is the grain inventory is counted at, so it is
    the grain a change has to be expressed in. Rows already cancelled are the
    caller's to exclude — they hold nothing.
    """
    out: dict[tuple[uuid.UUID, date], int] = {}
    for u in units:
        for day in _nights(u["arrival_date"], u["departure_date"]):
            key = (u["room_type_id"], day)
            out[key] = out.get(key, 0) + 1
    return out


def occupancy_delta(
    before: dict[tuple[uuid.UUID, date], int],
    after: dict[tuple[uuid.UUID, date], int],
) -> dict[tuple[uuid.UUID, date], int]:
    """What has to move, for the nights where anything actually changed.

    Nights common to both sides cancel to zero and are dropped, so shortening
    a stay by a night touches one counter rather than rewriting the whole
    stay's worth of rows.
    """
    return {
        k: after.get(k, 0) - before.get(k, 0)
        for k in set(before) | set(after)
        if after.get(k, 0) != before.get(k, 0)
    }


def shift_inventory(
    session: Session,
    *,
    property_id: uuid.UUID,
    organization_id: uuid.UUID,
    counter: str,
    delta: dict[tuple[uuid.UUID, date], int],
    overbooking_allowance: int = 0,
) -> None:
    """Move inventory counters by ``delta``, refusing any oversold night.

    Changing a booking is not a fresh booking: it gives nights back and takes
    others in the same breath, and the two have to be weighed together — a
    stay moved one day later frees its first night and needs its last, which
    checked separately can look like a shortage that is not there.

    So the counters are moved first and the result is checked afterwards. Every
    affected night is locked before anything is written, in one order for every
    caller, which is what stops two concurrent changes deadlocking or reading
    each other's half-applied state.

    Must run inside the caller's transaction: raising rolls the whole change
    back, which is the only correct outcome for a partially-applied one.
    """
    if counter not in COUNTERS:
        raise ValueError(f"Unknown inventory counter {counter!r}")
    delta = {k: v for k, v in delta.items() if v}
    if not delta:
        return

    keys = sorted(delta, key=lambda k: (str(k[0]), k[1]))
    room_types = [k[0] for k in keys]
    days = [k[1] for k in keys]

    # A stay can be extended into nights that have no inventory row yet.
    # physical_capacity defaults to 0, so such a night is created already
    # sold out and the check below reports it rather than silently allowing
    # a booking onto a date nobody put on sale.
    for rt, day in keys:
        if delta[(rt, day)] > 0:
            session.execute(
                text(
                    """
                    INSERT INTO booking.room_type_inventory_days
                        (organization_id, property_id, room_type_id, stay_date,
                         physical_capacity, out_of_service, held_units,
                         reserved_units, allotment_units)
                    VALUES (:org, :prop, :rt, :d, 0, 0, 0, 0, 0)
                    ON CONFLICT (property_id, room_type_id, stay_date)
                        DO NOTHING
                    """
                ),
                {"org": organization_id, "prop": property_id, "rt": rt,
                 "d": day},
            )

    # Lock every affected night, in (room_type, date) order — the same order
    # create_hold takes them in, so a change and a fresh booking queue behind
    # one another instead of deadlocking.
    session.execute(
        text(
            """
            SELECT 1 FROM booking.room_type_inventory_days
             WHERE property_id = :prop
               AND (room_type_id, stay_date) IN (
                   SELECT * FROM unnest(CAST(:rts AS uuid[]),
                                        CAST(:days AS date[])))
             ORDER BY room_type_id, stay_date
             FOR UPDATE
            """
        ),
        {"prop": property_id, "rts": room_types, "days": days},
    )

    for (rt, day), n in delta.items():
        session.execute(
            text(
                f"""
                UPDATE booking.room_type_inventory_days
                   SET {counter} = GREATEST({counter} + :n, 0)
                 WHERE property_id = :prop AND room_type_id = :rt
                   AND stay_date = :d
                """
            ),
            {"n": n, "prop": property_id, "rt": rt, "d": day},
        )

    # The invariant, checked on exactly the nights that moved. Anything
    # negative means the change sold a room the property does not have.
    bad = session.execute(
        text(
            """
            SELECT rt.name AS room_type, i.stay_date,
                   -(i.physical_capacity - i.out_of_service - i.held_units
                     - i.reserved_units - i.allotment_units
                     + CAST(:allow AS integer)) AS short
            FROM booking.room_type_inventory_days i
            JOIN property.room_types rt ON rt.id = i.room_type_id
            WHERE i.property_id = :prop
              AND (i.room_type_id, i.stay_date) IN (
                  SELECT * FROM unnest(CAST(:rts AS uuid[]),
                                       CAST(:days AS date[])))
              AND (i.physical_capacity - i.out_of_service - i.held_units
                   - i.reserved_units - i.allotment_units
                   + CAST(:allow AS integer)) < 0
            ORDER BY i.stay_date, rt.name
            LIMIT 1
            """
        ),
        {"prop": property_id, "rts": room_types, "days": days,
         "allow": overbooking_allowance},
    ).first()
    if bad is not None:
        raise InventoryOversold(bad.stay_date, bad.room_type, int(bad.short))


def create_hold(
    session: Session,
    *,
    organization_id: uuid.UUID,
    property_id: uuid.UUID,
    arrival_date: date,
    departure_date: date,
    idempotency_key: str,
    # Either give ``lines`` — one entry per distinct room on the booking — or
    # the flat room_type/units/adults/children, which is the same thing said
    # for a single line. The flat form is kept because most callers book one
    # kind of room and saying so should not cost them a list.
    lines: list[HoldLine] | None = None,
    room_type_id: uuid.UUID | None = None,
    units: int = 1,
    adults: int = 1,
    children: int = 0,
    hold_ttl_minutes: int,
    overbooking_allowance: int,
    guest_id: uuid.UUID | None = None,
    #: Take these rooms from a group block rather than from general
    #: availability. The block's rooms are already off sale, so without this a
    #: group member is refused the very rooms the block is holding for them.
    group_block_id: uuid.UUID | None = None,
    # How the booking arrived and what it sits on. Collected at the point the
    # booking is taken, because that is the only moment anybody knows.
    source: str | None = None,
    market_segment_id: uuid.UUID | None = None,
    purpose_of_stay: str | None = None,
    company_name: str | None = None,
    travel_agent: str | None = None,
    reference: str | None = None,
    special_requests: str | None = None,
    rate_plan_id: uuid.UUID | None = None,
    meal_plan_id: uuid.UUID | None = None,
    business_source_id: uuid.UUID | None = None,
    bill_to: str = "guest",
    commercial_account_id: uuid.UUID | None = None,
    expected_arrival_time=None,
    expected_departure_time=None,
) -> HoldResult:
    """Create a booking hold for the requested rooms across the stay.

    Every line is validated before any counter moves: a booking that cannot be
    filled in full is refused in full, so the guest is never told they have
    two of the three rooms they asked for.

    Must be invoked inside a transaction scope managed by the caller (the
    request dependency commits on success / rolls back on exception).
    """
    if not lines:
        if room_type_id is None:
            raise ValueError("A booking needs at least one room type.")
        lines = [HoldLine(room_type_id=room_type_id, units=units,
                          adults=adults, children=children,
                          rate_plan_id=rate_plan_id, meal_plan_id=meal_plan_id)]
    if any(line.units < 1 for line in lines):
        raise ValueError("Every room line must book at least one room.")

    # Inventory is held per room type, so two lines of the same type compete
    # for the same counter and must be checked against their combined demand.
    # Checking them separately would pass two lines of one room each against a
    # single sellable room.
    wanted: dict[uuid.UUID, int] = {}
    for line in lines:
        wanted[line.room_type_id] = wanted.get(line.room_type_id, 0) + line.units

    nights = _nights(arrival_date, departure_date)

    # Idempotency: a repeated request key returns the existing hold rather than
    # creating a duplicate (§4 booking_holds unique request key, §10).
    existing = session.execute(
        text(
            """
            SELECT h.id AS hold_id, h.reservation_id AS reservation_id
            FROM booking.booking_holds h
            WHERE h.idempotency_key = :key
            """
        ),
        {"key": idempotency_key},
    ).first()
    if existing is not None:
        prior = [
            r.id for r in session.execute(
                text(
                    """
                    SELECT id FROM booking.reservation_units
                    WHERE reservation_id = :rid ORDER BY line_index
                    """
                ),
                {"rid": existing.reservation_id},
            )
        ]
        num = session.execute(
            text("SELECT number FROM booking.reservations WHERE id = :rid"),
            {"rid": existing.reservation_id},
        ).scalar_one()
        return HoldResult(
            reservation_id=existing.reservation_id,
            reservation_unit_id=prior[0] if prior else existing.reservation_id,
            reservation_unit_ids=prior,
            hold_id=existing.hold_id,
            number=num,
        )

    # Room types are visited in sorted order so two concurrent bookings that
    # want the same pair of types take the locks in the same sequence and
    # cannot deadlock against each other.
    for rt in sorted(wanted, key=str):
        need = wanted[rt]

        # 1) Ensure an inventory row exists for every night (conflict-safe),
        #    then lock the rows in deterministic order. physical_capacity
        #    defaults to 0 when auto-created; real capacity is seeded from
        #    rooms elsewhere.
        for stay_date in nights:
            session.execute(
                text(
                    """
                    INSERT INTO booking.room_type_inventory_days
                        (organization_id, property_id, room_type_id, stay_date,
                         physical_capacity, out_of_service, held_units,
                         reserved_units, allotment_units)
                    VALUES
                        (:org, :prop, :rt, :d, 0, 0, 0, 0, 0)
                    ON CONFLICT (property_id, room_type_id, stay_date)
                        DO NOTHING
                    """
                ),
                {
                    "org": organization_id,
                    "prop": property_id,
                    "rt": rt,
                    "d": stay_date,
                },
            )

        # 2) Lock all requested nights FOR UPDATE in deterministic order.
        locked = session.execute(
            text(
                """
                SELECT stay_date, physical_capacity, out_of_service,
                       held_units, reserved_units, allotment_units
                FROM booking.room_type_inventory_days
                WHERE property_id = :prop
                  AND room_type_id = :rt
                  AND stay_date = ANY(:dates)
                ORDER BY stay_date
                FOR UPDATE
                """
            ),
            {
                "prop": property_id,
                "rt": rt,
                "dates": nights,
            },
        ).all()

        # 2b) If this booking is drawing on a group block, hand the block's
        #     rooms back *before* the check below. The rooms the group was
        #     promised are exactly the rooms the block has taken off sale, so
        #     a group member booking against ordinary availability is refused
        #     by the hotel's own block. The rows are already locked above, so
        #     this takes no new locks and cannot reorder them.
        if group_block_id is not None:
            from . import group_blocks

            group_blocks.draw_down(
                session, block_id=group_block_id, property_id=property_id,
                room_type_id=rt, nights=nights, need=need)
            locked = session.execute(
                text(
                    """
                    SELECT stay_date, physical_capacity, out_of_service,
                           held_units, reserved_units, allotment_units
                    FROM booking.room_type_inventory_days
                    WHERE property_id = :prop AND room_type_id = :rt
                      AND stay_date = ANY(:dates)
                    ORDER BY stay_date
                    """
                ),
                {"prop": property_id, "rt": rt, "dates": nights},
            ).all()

        # 3) Validate every night. No partial allocation.
        for row in locked:
            sellable = (
                row.physical_capacity
                - row.out_of_service
                - row.held_units
                - row.reserved_units
                - row.allotment_units
                + overbooking_allowance
            )
            if sellable < need:
                raise InventoryShortage(row.stay_date, need, sellable)

        # 4) All nights valid for this type — take the rooms off sale.
        session.execute(
            text(
                """
                UPDATE booking.room_type_inventory_days
                SET held_units = held_units + :units
                WHERE property_id = :prop
                  AND room_type_id = :rt
                  AND stay_date = ANY(:dates)
                """
            ),
            {"units": need, "prop": property_id, "rt": rt, "dates": nights},
        )

    reservation_id = uuid.uuid4()
    number = f"CBR{uuid.uuid4().hex[:8].upper()}"
    session.execute(
        text(
            """
            INSERT INTO booking.reservations
                (id, organization_id, property_id, number, status, currency,
                 primary_guest_id, source, market_segment_id,
                 purpose_of_stay,
                 company_name, travel_agent, reference, special_requests,
                 business_source_id, bill_to, commercial_account_id,
                 group_block_id, cancellation_policy_id)
            -- The property's own currency. This was the literal 'INR', and
            -- the folio opens in the reservation's currency and every
            -- posting inherits the folio's -- so one hard-coded string made
            -- a dollar property's entire ledger claim to be rupees. The
            -- fallback is only for a property row this session cannot see.
            VALUES (:id, :org, :prop, :number, 'held',
                    COALESCE((SELECT currency FROM iam.properties
                               WHERE id = :prop), 'INR'),
                    :guest,
                    :source, :segment, :purpose, :company, :agent, :ref,
                    :requests, :bizsrc, :billto, :acct, :block,
                    (SELECT id FROM property.cancellation_policies
                      WHERE property_id = :prop AND is_default LIMIT 1))
            """
        ),
        {
            "id": reservation_id,
            "org": organization_id,
            "prop": property_id,
            "number": number,
            "guest": guest_id,
            "source": source,
            "segment": market_segment_id,
            "purpose": purpose_of_stay,
            "company": company_name,
            "agent": travel_agent,
            "ref": reference,
            "requests": special_requests,
            "bizsrc": business_source_id,
            "billto": bill_to,
            "acct": commercial_account_id,
            "block": group_block_id,
        },
    )

    # One row per room, carrying the line it came from. The inventory
    # decrement above is by the number of rooms, so collapsing a line into a
    # single row would hold three rooms and bill for one — the guest would be
    # charged a third of what the property took off sale.
    unit_ids: list[uuid.UUID] = []
    for line in lines:
        for _ in range(line.units):
            unit_id = uuid.uuid4()
            unit_ids.append(unit_id)
            # The room's position on the booking, recorded rather than left to
            # whatever order the rows come back in — every row here shares a
            # created_at to the microsecond, so that order is arbitrary.
            line_index = len(unit_ids) - 1
            session.execute(
                text(
                    """
                    INSERT INTO booking.reservation_units
                        (id, organization_id, property_id, reservation_id,
                         room_type_id, arrival_date, departure_date, adults,
                         children, status, rate_plan_id, meal_plan_id,
                         expected_arrival_time, expected_departure_time,
                         line_index, nightly_rate)
                    VALUES (:id, :org, :prop, :rid, :rt, :arr, :dep, :ad, :ch,
                            'reserved', :plan, :meal, :eta, :etd, :idx,
                            :rate)
                    """
                ),
                {
                    "id": unit_id,
                    "org": organization_id,
                    "prop": property_id,
                    "rid": reservation_id,
                    "rt": line.room_type_id,
                    # A line's own plan wins; the booking-wide one is the
                    # fallback for callers that only ever set it once.
                    "plan": line.rate_plan_id or rate_plan_id,
                    "meal": line.meal_plan_id or meal_plan_id,
                    "eta": expected_arrival_time,
                    "etd": expected_departure_time,
                    "arr": arrival_date,
                    "dep": departure_date,
                    "ad": line.adults,
                    "ch": line.children,
                    "idx": line_index,
                    "rate": line.nightly_rate,
                },
            )

    hold_id = uuid.uuid4()
    session.execute(
        text(
            """
            INSERT INTO booking.booking_holds
                (id, organization_id, property_id, reservation_id, status,
                 idempotency_key, expires_at)
            VALUES (:id, :org, :prop, :rid, 'held', :key,
                    now() + make_interval(mins => :ttl))
            """
        ),
        {
            "id": hold_id,
            "org": organization_id,
            "prop": property_id,
            "rid": reservation_id,
            "key": idempotency_key,
            "ttl": hold_ttl_minutes,
        },
    )

    # 5) Outbox event, atomic with the business change (§10).
    enqueue_event(
        session,
        aggregate_type="reservation",
        aggregate_id=str(reservation_id),
        event_type="booking.hold_created",
        payload={
            "reservation_id": str(reservation_id),
            "property_id": str(property_id),
            # The first line's type is kept for consumers written when a
            # booking could only have one; "rooms" is the whole truth.
            "room_type_id": str(lines[0].room_type_id),
            "arrival_date": arrival_date.isoformat(),
            "departure_date": departure_date.isoformat(),
            "units": len(unit_ids),
            "rooms": [
                {"room_type_id": str(rt), "units": n}
                for rt, n in sorted(wanted.items(), key=lambda kv: str(kv[0]))
            ],
        },
    )

    return HoldResult(
        reservation_id=reservation_id,
        reservation_unit_id=unit_ids[0],
        reservation_unit_ids=unit_ids,
        hold_id=hold_id,
        number=number,
    )


# --------------------------------------------------------------------------
# Physical capacity
# --------------------------------------------------------------------------
def active_room_count(
    session: Session, *, property_id: uuid.UUID, room_type_id: uuid.UUID,
) -> int:
    """How many rooms of this type can actually be sold."""
    return int(
        session.execute(
            text(
                "SELECT count(*) FROM property.rooms "
                "WHERE property_id = :p AND room_type_id = :rt "
                "AND status = 'active'"
            ),
            {"p": property_id, "rt": room_type_id},
        ).scalar_one()
    )


def capacity_shortfall(
    session: Session, *, property_id: uuid.UUID, room_type_id: uuid.UUID,
    new_capacity: int, leaving: list[uuid.UUID] | None = None,
) -> list[date]:
    """Nights already committed beyond ``new_capacity``.

    Asked *before* a room is removed, so a delete that would leave the type
    holding more bookings than it has rooms is refused rather than discovered
    later as an oversell nobody can explain.

    Committed means everything the sellable count subtracts, not only
    bookings: rooms a group block is holding and rooms out of service are
    just as unavailable. Counting only held and reserved let a room be
    removed from a type whose remaining rooms were all promised to a group,
    leaving the block holding rooms that no longer existed.

    ``leaving`` names the rooms being removed. One of them may itself be out
    of service, and it takes its out-of-service night with it -- counting it
    against the rooms that remain would refuse a delete that is safe.
    """
    rows = session.execute(
        text(
            """
            SELECT i.stay_date FROM booking.room_type_inventory_days i
            WHERE i.property_id = :p AND i.room_type_id = :rt
              AND i.held_units + i.reserved_units + i.allotment_units
                  + GREATEST(i.out_of_service - (
                        SELECT count(*) FROM unnest(CAST(:leaving AS uuid[]))
                               AS gone(id)
                         WHERE booking.room_out_of_service(
                                   gone.id, i.stay_date,
                                   booking.local_today(:p))), 0)
                  > :cap
            ORDER BY i.stay_date
            """
        ),
        {"p": property_id, "rt": room_type_id, "cap": new_capacity,
         "leaving": list(leaving or [])},
    ).scalars().all()
    return list(rows)


def sync_capacity(
    session: Session, *, property_id: uuid.UUID, room_type_id: uuid.UUID,
) -> int:
    """Make ``physical_capacity`` equal the rooms that exist.

    Nothing used to maintain this. Capacity was written once when the property
    was set up and never again, so every room added or removed pushed the
    counters further from the truth -- a bulk delete once left a type
    advertising four rooms it did not have, and another with none at all.

    Called on every path that changes what rooms exist or whether they are
    sellable: create, update, delete, bulk delete.

    **It creates the nights as well as updating them.** This only ever ran an
    UPDATE, which does nothing when there are no rows -- and nothing created
    them except a seed migration that ran once, for one property. So a
    property set up through onboarding had rooms, room types and rates, and
    every night reading "not on sale (0/1)", because a night with no inventory
    row has nothing to sell. The rooms existed; there was simply no calendar
    to put them in.

    Rows are written from today to the booking horizon. Dates already past are
    left alone: what was sellable last week is history, not something to
    restate.
    """
    from chirala_common.property_time import local_today

    count = active_room_count(
        session, property_id=property_id, room_type_id=room_type_id)
    # From the property's own today. CURRENT_DATE is UTC, so between midnight
    # and 05:30 in India it left tonight's night out of the resync entirely.
    today = local_today(session, property_id)
    session.execute(
        text(
            """
            INSERT INTO booking.room_type_inventory_days
                (organization_id, property_id, room_type_id, stay_date,
                 physical_capacity, out_of_service, held_units,
                 reserved_units, allotment_units)
            SELECT p.organization_id, :p, :rt, d::date, :n, 0, 0, 0, 0
            FROM iam.properties p
            CROSS JOIN generate_series(
                CAST(:today AS date), CAST(:today AS date) + :days,
                interval '1 day') AS d
            WHERE p.id = :p
            ON CONFLICT (property_id, room_type_id, stay_date) DO UPDATE
                SET physical_capacity = EXCLUDED.physical_capacity
                -- Never below what is already committed: a room being
                -- removed must not turn existing bookings, a group's block or
                -- rooms under repair into an oversell. Callers check
                -- capacity_shortfall first and refuse; this is the backstop,
                -- and it counts what that check counts.
                WHERE booking.room_type_inventory_days.held_units
                    + booking.room_type_inventory_days.reserved_units
                    + booking.room_type_inventory_days.allotment_units
                    + booking.room_type_inventory_days.out_of_service
                    <= EXCLUDED.physical_capacity
            """
        ),
        {"n": count, "p": property_id, "rt": room_type_id, "today": today,
         "days": settings.inventory_horizon_days},
    )
    return count
