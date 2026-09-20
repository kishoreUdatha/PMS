"""Acceptance tests for the reservation flow (schema §4, §13).

Requires a running PostgreSQL with booking-core migrations at head. Skipped when
BOOKING_DATABASE_URL is not set.

Covered:
  - confirm moves held_units -> reserved_units for each night.
  - assigning a physical room, then a second overlapping assignment on the same
    room is rejected by the GiST exclusion constraint (RoomCollision).
  - check-in creates a stay; a second check-in is rejected.
  - early checkout releases the nights not stayed and flags the room dirty.
"""

from __future__ import annotations

import os
import uuid
from datetime import date, timedelta

import pytest
from sqlalchemy import text

DB_URL = os.getenv("BOOKING_DATABASE_URL")
pytestmark = pytest.mark.skipif(
    not DB_URL, reason="BOOKING_DATABASE_URL not set; integration test skipped"
)


@pytest.fixture()
def engine():
    from chirala_common.db import make_engine

    # Booking invariants, not tenancy: these fabricate tenants freely, so they
    # run as the schema owner when it is available. Row security has its own
    # tests in test_tenant_rls.py.
    return make_engine(os.getenv("BOOKING_MIGRATION_DATABASE_URL") or DB_URL)


#: Everything this test writes hangs off a property id, so deleting by that
#: one key clears the lot. Children first -- these are real foreign keys.
_SCRATCH_TABLES = (
    "operations.room_status_events",
    "operations.room_condition",
    "booking.stay_room_segments",
    "booking.stays",
    "booking.room_calendar_entries",
    "booking.reservation_units",
    "booking.reservations",
    "booking.booking_holds",
    "booking.room_type_inventory_days",
    "property.rooms",
    "property.room_types",
)


@pytest.fixture()
def scratch_ids(engine):
    """Fabricated org / property / room-type ids, cleaned up afterwards.

    The ids are invented rather than onboarded, so nothing in ``iam`` refers to
    them. That is fine for exercising the flow, but it means the rows they
    create are invisible to every tenant-scoped screen -- while still being
    real rows, counted by real inventory.

    This test commits, and runs against whatever ``BOOKING_DATABASE_URL``
    points at, which on a developer machine is the working database. Without
    this teardown, each run left a phantom property holding reserved nights
    that no screen could show and nobody could cancel.

    Teardown runs in a ``finally``: a failing run leaves exactly the same
    debris as a passing one, and the run that discovers a bug is precisely the
    one you do not want dirtying the database.
    """
    org, prop, rt = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    try:
        yield org, prop, rt
    finally:
        with engine.begin() as conn:
            for table in _SCRATCH_TABLES:
                conn.execute(
                    text(f"DELETE FROM {table} WHERE property_id = :p"),
                    {"p": prop},
                )


def _seed_capacity(conn, org, prop, rt, start, nights, cap):
    for i in range(nights):
        conn.execute(
            text(
                """
                INSERT INTO booking.room_type_inventory_days
                    (organization_id, property_id, room_type_id, stay_date,
                     physical_capacity, out_of_service, held_units,
                     reserved_units, allotment_units)
                VALUES (:org, :prop, :rt, :d, :cap, 0, 0, 0, 0)
                ON CONFLICT (property_id, room_type_id, stay_date)
                DO UPDATE SET physical_capacity = :cap, held_units = 0,
                              reserved_units = 0, out_of_service = 0,
                              allotment_units = 0
                """
            ),
            {"org": org, "prop": prop, "rt": rt, "d": start + timedelta(days=i),
             "cap": cap},
        )


def _make_room(conn, org, prop, rt, code):
    room_id = uuid.uuid4()
    conn.execute(
        text(
            """
            INSERT INTO property.room_types
                (id, organization_id, property_id, code, name,
                 max_adults, max_children, max_occupancy)
            VALUES (:rt, :org, :prop, :rtcode, 'Deluxe', 2, 1, 3)
            ON CONFLICT (id) DO NOTHING
            """
        ),
        {"rt": rt, "org": org, "prop": prop, "rtcode": f"RT{code}"},
    )
    conn.execute(
        text(
            """
            INSERT INTO property.rooms
                (id, organization_id, property_id, room_type_id, code)
            VALUES (:id, :org, :prop, :rt, :code)
            """
        ),
        {"id": room_id, "org": org, "prop": prop, "rt": rt, "code": code},
    )
    return room_id


def _held_counts(conn, prop, rt, start, nights):
    return conn.execute(
        text(
            """
            SELECT coalesce(sum(held_units),0) AS held,
                   coalesce(sum(reserved_units),0) AS reserved
            FROM booking.room_type_inventory_days
            WHERE property_id = :prop AND room_type_id = :rt
              AND stay_date >= :s AND stay_date < :e
            """
        ),
        {"prop": prop, "rt": rt, "s": start, "e": start + timedelta(days=nights)},
    ).first()


def test_full_flow_confirm_assign_checkin_checkout(engine, scratch_ids):
    from chirala_common.db import make_session_factory

    from booking_core.flow import (
        FlowError,
        RoomCollision,
        assign_room,
        check_in,
        check_out,
        confirm_reservation,
    )
    from booking_core.inventory import create_hold

    org, prop, rt = scratch_ids
    arrival = date.today() + timedelta(days=90)
    nights = 3
    departure = arrival + timedelta(days=nights)

    with engine.begin() as conn:
        _seed_capacity(conn, org, prop, rt, arrival, nights, cap=2)
        room_a = _make_room(conn, org, prop, rt, code=f"A{uuid.uuid4().hex[:4]}")
        room_b = _make_room(conn, org, prop, rt, code=f"B{uuid.uuid4().hex[:4]}")

    factory = make_session_factory(engine)

    # --- hold ---
    s = factory()
    hold = create_hold(
        s, organization_id=org, property_id=prop, room_type_id=rt,
        arrival_date=arrival, departure_date=departure, units=1, adults=2,
        children=0, idempotency_key=f"flow-{uuid.uuid4()}",
        hold_ttl_minutes=15, overbooking_allowance=0,
    )
    s.commit(); s.close()
    unit_id = hold.reservation_unit_id

    with engine.connect() as conn:
        c = _held_counts(conn, prop, rt, arrival, nights)
    assert c.held == nights and c.reserved == 0

    # --- confirm: held -> reserved ---
    s = factory()
    confirm_reservation(s, reservation_id=hold.reservation_id)
    s.commit(); s.close()
    with engine.connect() as conn:
        c = _held_counts(conn, prop, rt, arrival, nights)
    assert c.held == 0 and c.reserved == nights

    # --- assign room A ---
    s = factory()
    assign_room(s, reservation_unit_id=unit_id, room_id=room_a)
    s.commit(); s.close()

    # --- GiST collision: second unit assigned to the SAME room, overlap ---
    s = factory()
    hold2 = create_hold(
        s, organization_id=org, property_id=prop, room_type_id=rt,
        arrival_date=arrival, departure_date=departure, units=1, adults=1,
        children=0, idempotency_key=f"flow2-{uuid.uuid4()}",
        hold_ttl_minutes=15, overbooking_allowance=0,
    )
    s.commit(); s.close()
    s = factory()
    confirm_reservation(s, reservation_id=hold2.reservation_id)
    s.commit(); s.close()
    s = factory()
    with pytest.raises(RoomCollision):
        assign_room(s, reservation_unit_id=hold2.reservation_unit_id, room_id=room_a)
    s.rollback(); s.close()
    # Assigning to a DIFFERENT free room succeeds.
    s = factory()
    assign_room(s, reservation_unit_id=hold2.reservation_unit_id, room_id=room_b)
    s.commit(); s.close()

    # --- check-in unit 1 ---
    s = factory()
    ci = check_in(s, reservation_unit_id=unit_id)
    s.commit(); s.close()
    assert ci.stay_id is not None

    # --- double check-in rejected ---
    s = factory()
    with pytest.raises(FlowError):
        check_in(s, reservation_unit_id=unit_id)
    s.rollback(); s.close()

    # --- early checkout on the arrival date: every night goes back on sale ---
    # Nights are half-open, so a guest leaving on date D has stayed
    # [arrival, D) -- leaving on the arrival date is a stay of no nights, and
    # all of them are returned. The checkout night used to be kept back, which
    # left the room type one short of the rack for that date.
    s = factory()
    co = check_out(s, reservation_unit_id=unit_id, business_date=arrival)
    s.commit(); s.close()
    assert co.nights_released == nights

    # reserved_units for the released future nights dropped by 1.
    with engine.connect() as conn:
        released = conn.execute(
            text(
                """
                SELECT reserved_units FROM booking.room_type_inventory_days
                WHERE property_id = :prop AND room_type_id = :rt AND stay_date = :d
                """
            ),
            {"prop": prop, "rt": rt, "d": arrival + timedelta(days=1)},
        ).scalar_one()
        # The checkout night itself: the departing unit must have let go of it
        # too, leaving only unit2. This is the night the old code kept back.
        on_checkout_night = conn.execute(
            text(
                """
                SELECT reserved_units FROM booking.room_type_inventory_days
                WHERE property_id = :prop AND room_type_id = :rt AND stay_date = :d
                """
            ),
            {"prop": prop, "rt": rt, "d": arrival},
        ).scalar_one()
        # room A flagged dirty
        cond = conn.execute(
            text(
                "SELECT cleanliness FROM operations.room_condition WHERE room_id = :r"
            ),
            {"r": room_a},
        ).scalar_one()
    # unit1 held that night plus unit2 (room B) still reserved -> 1 remains.
    assert released == 1
    assert on_checkout_night == 1
    assert cond == "dirty"
