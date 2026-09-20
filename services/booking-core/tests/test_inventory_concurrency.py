"""Acceptance tests for booking-core inventory concurrency (schema §13).

These tests require a running PostgreSQL with the booking-core migrations
applied. They are skipped automatically when BOOKING_DATABASE_URL is not set.

Covered invariants:
  - Two simultaneous last-room bookings yield exactly one success.
  - A single-night shortage rolls back the whole multi-night request.
  - A repeated idempotency key returns the same hold, not a duplicate.
"""

from __future__ import annotations

import os
import threading
import uuid
from contextlib import contextmanager
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

    return make_engine(DB_URL)


#: Everything these tests write hangs off a property id. Children first.
_SCRATCH_TABLES = (
    "booking.booking_holds",
    "booking.reservation_units",
    "booking.reservations",
    "booking.room_type_inventory_days",
)


@contextmanager
def _as_reaper(engine):
    """A session in system context, the way ``reaper.run_once`` calls it.

    ``expire_stale_holds`` does not elevate itself -- ``run_once`` sets system
    context and then calls it, because a sweep that releases every tenant's
    expired holds is above the tenant boundary by nature while the function
    that does the work should not decide that for itself.

    A test calling it on an unscoped session therefore sees nothing at all,
    and the two assertions that a sweep releases *zero* holds passed for
    exactly the wrong reason: not because the holds were still live, but
    because row-level security had hidden them. A green assertion that would
    stay green if the feature were deleted is worse than a red one.
    """
    from chirala_common.db import make_session_factory, system_context

    session = make_session_factory(engine)()
    system_context(session, reason="test: hold reaper sweep")
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


@contextmanager
def _as_tenant(engine, org):
    """A session scoped to one tenant, the way a request is.

    Every table these tests touch carries ``tenant_isolation``, whose policy
    is ``tenancy.org_visible(organization_id)`` -- so a statement that has not
    published an organisation is refused on write and matches nothing on read.

    The fixtures here predate that policy and went straight at the engine, so
    seeding raised *new row violates row-level security policy* and, worse,
    the cleanup silently deleted nothing: a DELETE with no context matches no
    rows and reports success. This file's own docstring records how much
    trouble leftover rows caused, and they were being left behind by the very
    code written to remove them.

    The domain functions deliberately do not bind context themselves --
    ``create_hold`` acts on whatever session it is handed, and deciding whose
    data a caller may touch is the request's job, not the domain's. So a test
    standing in for a request has to do what the request does.
    """
    from chirala_common.db import bind_tenant_context, make_session_factory

    session = make_session_factory(engine)()
    # Transaction-local, so it must be set inside the transaction the work
    # runs in -- bind first, commit last, never a commit in between.
    bind_tenant_context(session, organization_id=org)
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


@pytest.fixture()
def scratch(engine):
    """A fabricated org / property / room type, cleaned up afterwards.

    These tests commit against whatever BOOKING_DATABASE_URL points at, which
    on a developer machine is the working database.

    Leaving rows behind did more than litter here -- it broke the tests. Holds
    are keyed by ``idempotency_key``, and that key is unique *globally*, not
    per property. A hold left behind under a fixed key meant the next run
    matched it and returned the existing hold instead of competing for the
    room, so the oversell test reported two successes and failed every run
    after the first. The keys are per-run now as well, so a stray row cannot
    silently turn a real test into a no-op.
    """
    org, prop, rt = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    try:
        yield org, prop, rt
    finally:
        with _as_tenant(engine, org) as session:
            for table in _SCRATCH_TABLES:
                session.execute(
                    text(f"DELETE FROM {table} WHERE property_id = :p"),
                    {"p": prop},
                )


def _seed_one_room(engine, org, prop, rt, day):
    with _as_tenant(engine, org) as session:
        session.execute(
            text(
                """
                INSERT INTO booking.room_type_inventory_days
                    (organization_id, property_id, room_type_id, stay_date,
                     physical_capacity, out_of_service, held_units,
                     reserved_units, allotment_units)
                VALUES (:org, :prop, :rt, :d, 1, 0, 0, 0, 0)
                ON CONFLICT (property_id, room_type_id, stay_date)
                DO UPDATE SET physical_capacity = 1, held_units = 0,
                              reserved_units = 0, allotment_units = 0,
                              out_of_service = 0
                """
            ),
            {"org": org, "prop": prop, "rt": rt, "d": day},
        )


def test_two_simultaneous_last_room_bookings_one_success(engine, scratch):
    from chirala_common.db import make_session_factory

    from booking_core.inventory import InventoryShortage, create_hold

    org, prop, rt = scratch
    arrival = date.today() + timedelta(days=30)
    departure = arrival + timedelta(days=1)
    _seed_one_room(engine, org, prop, rt, arrival)

    factory = make_session_factory(engine)
    results: list[str] = []
    barrier = threading.Barrier(2)

    def attempt(key: str) -> None:
        from chirala_common.db import bind_tenant_context

        session = factory()
        # What the request dependency does before a route touches anything.
        bind_tenant_context(session, organization_id=org)
        try:
            barrier.wait(timeout=5)
            create_hold(
                session,
                organization_id=org,
                property_id=prop,
                room_type_id=rt,
                arrival_date=arrival,
                departure_date=departure,
                units=1,
                adults=1,
                children=0,
                idempotency_key=key,
                hold_ttl_minutes=15,
                overbooking_allowance=0,
            )
            session.commit()
            results.append("success")
        except InventoryShortage:
            session.rollback()
            results.append("shortage")
        except Exception:  # noqa: BLE001
            session.rollback()
            results.append("error")
        finally:
            session.close()

    run = uuid.uuid4()
    t1 = threading.Thread(target=attempt, args=(f"race-a-{run}",))
    t2 = threading.Thread(target=attempt, args=(f"race-b-{run}",))
    t1.start()
    t2.start()
    t1.join()
    t2.join()

    assert results.count("success") == 1, results
    assert results.count("shortage") == 1, results


def test_partial_night_shortage_rolls_back_whole_request(engine, scratch):
    from chirala_common.db import make_session_factory

    from booking_core.inventory import InventoryShortage, create_hold

    org, prop, rt = scratch
    n1 = date.today() + timedelta(days=60)
    n2 = n1 + timedelta(days=1)
    departure = n2 + timedelta(days=1)
    _seed_one_room(engine, org, prop, rt, n1)  # night 1 available
    # night 2 intentionally not seeded with capacity -> capacity 0 on demand
    with _as_tenant(engine, org) as session:
        session.execute(
            text(
                """
                INSERT INTO booking.room_type_inventory_days
                    (organization_id, property_id, room_type_id, stay_date,
                     physical_capacity, out_of_service, held_units,
                     reserved_units, allotment_units)
                VALUES (:org, :prop, :rt, :d, 0, 0, 0, 0, 0)
                ON CONFLICT (property_id, room_type_id, stay_date) DO NOTHING
                """
            ),
            {"org": org, "prop": prop, "rt": rt, "d": n2},
        )

    from chirala_common.db import bind_tenant_context

    factory = make_session_factory(engine)
    session = factory()
    bind_tenant_context(session, organization_id=org)
    with pytest.raises(InventoryShortage):
        create_hold(
            session,
            organization_id=org,
            property_id=prop,
            room_type_id=rt,
            arrival_date=n1,
            departure_date=departure,
            units=1,
            adults=1,
            children=0,
            idempotency_key=f"multi-night-{uuid.uuid4()}",
            hold_ttl_minutes=15,
            overbooking_allowance=0,
        )
    session.rollback()
    session.close()

    # Night 1 must not have been consumed (full rollback).
    with _as_tenant(engine, org) as conn:
        held = conn.execute(
            text(
                """
                SELECT held_units FROM booking.room_type_inventory_days
                WHERE property_id = :prop AND room_type_id = :rt AND stay_date = :d
                """
            ),
            {"prop": prop, "rt": rt, "d": n1},
        ).scalar_one()
    assert held == 0


def test_an_abandoned_hold_gives_its_rooms_back(engine, scratch):
    """A hold nothing expires is a room permanently off sale.

    ``booking_holds`` has carried an ``expires_at`` and a partial index on it
    since the first migration, and nothing ever read either. Harmless while the
    only thing holding rooms is a member of staff who confirms seconds later;
    fatal the moment a booking engine exists, because most guests abandon at
    the payment step and every one of them would take a room out of inventory
    for good.

    The counter is the part worth pinning: a hold sits in ``held_units``, so
    that is what must come back. Returning ``reserved_units`` instead would
    take the rooms off sale rather than putting them on it — the exact
    opposite of the fix.
    """
    from chirala_common.db import make_session_factory

    from booking_core.inventory import create_hold
    from booking_core.reaper import expire_stale_holds

    org, prop, rt = scratch
    arrival = date.today() + timedelta(days=45)
    nights = 2
    departure = arrival + timedelta(days=nights)
    # One room per night, so the hold takes the only one there is.
    for i in range(nights):
        _seed_one_room(engine, org, prop, rt, arrival + timedelta(days=i))

    from chirala_common.db import bind_tenant_context

    factory = make_session_factory(engine)
    s = factory()
    bind_tenant_context(s, organization_id=org)
    create_hold(
        s, organization_id=org, property_id=prop, room_type_id=rt,
        arrival_date=arrival, departure_date=departure, units=1,
        adults=1, children=0, idempotency_key=f"reap-{uuid.uuid4()}",
        hold_ttl_minutes=15, overbooking_allowance=0,
    )
    s.commit(); s.close()

    def counters():
        # Scoped like everything else: an unscoped read here would not error,
        # it would return zeroes -- and a test that passes because RLS hid
        # the rows is worse than one that fails.
        with _as_tenant(engine, org) as conn:
            return conn.execute(
                text("SELECT coalesce(sum(held_units),0), "
                     "       coalesce(sum(reserved_units),0) "
                     "FROM booking.room_type_inventory_days "
                     "WHERE property_id = :p"),
                {"p": prop},
            ).one()

    held, reserved = counters()
    assert held == nights and reserved == 0, "the hold should be holding both nights"

    # Not yet expired: a live hold must survive the sweep. Run as the reaper
    # runs, so a zero here means the hold was spared rather than hidden.
    with _as_reaper(engine) as sweep:
        assert expire_stale_holds(sweep) == 0
    assert counters() == (nights, 0)

    # Time passes.
    with _as_tenant(engine, org) as conn:
        conn.execute(
            text("UPDATE booking.booking_holds SET expires_at = now() - interval "
                 "'1 minute' WHERE property_id = :p"),
            {"p": prop},
        )

    with _as_reaper(engine) as sweep:
        assert expire_stale_holds(sweep) == 1

    held, reserved = counters()
    assert held == 0, "the nights must go back on sale"
    assert reserved == 0, "nothing was ever confirmed, so nothing is reserved"

    with _as_tenant(engine, org) as conn:
        hold_status = conn.execute(
            text("SELECT status FROM booking.booking_holds WHERE property_id = :p"),
            {"p": prop},
        ).scalar_one()
        res_status = conn.execute(
            text("SELECT status FROM booking.reservations WHERE property_id = :p"),
            {"p": prop},
        ).scalar_one()
    assert hold_status == 'expired'
    assert res_status == 'cancelled'

    # A second sweep finds nothing and must not double-decrement.
    with _as_reaper(engine) as sweep:
        assert expire_stale_holds(sweep) == 0
    assert counters() == (0, 0)
