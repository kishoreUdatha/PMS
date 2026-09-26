"""Money and inventory integrity across booking-core's write paths.

Every test here reproduces a defect found in review -- a counter moved twice,
a currency written as a literal, a cancellation that could not be recorded --
and pins the fix. They need PostgreSQL with every service's migrations at head
and are skipped without ``BOOKING_DATABASE_URL``.

The work runs as the *runtime* role under a tenant context, the way a request
does, so row-level security is in force exactly as in production. Only the
fixture's setup and teardown use the owner, because fabricating an
organisation and deleting everything it left behind are above any one tenant.
"""

from __future__ import annotations

import os
import threading
import uuid
from contextlib import contextmanager
from datetime import date, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import text

DB_URL = os.getenv("BOOKING_DATABASE_URL")
OWNER_URL = os.getenv("BOOKING_MIGRATION_DATABASE_URL") or DB_URL
pytestmark = pytest.mark.skipif(
    not DB_URL, reason="BOOKING_DATABASE_URL not set; integration test skipped"
)


# --------------------------------------------------------------------------
# Fixtures
# --------------------------------------------------------------------------
@pytest.fixture(scope="module")
def owner():
    from chirala_common.db import make_engine

    return make_engine(OWNER_URL)


@pytest.fixture(scope="module")
def runtime():
    from chirala_common.db import make_engine

    return make_engine(DB_URL)


def _tables_with_property_id(conn) -> list[str]:
    return [
        f"{r.table_schema}.{r.table_name}"
        for r in conn.execute(text(
            """
            SELECT c.table_schema, c.table_name
              FROM information_schema.columns c
              JOIN information_schema.tables t
                ON t.table_schema = c.table_schema
               AND t.table_name = c.table_name
             WHERE c.column_name = 'property_id'
               AND t.table_type = 'BASE TABLE'
               AND c.table_schema NOT IN ('pg_catalog', 'information_schema')
            """
        ))
    ]


def _purge(owner, org, prop) -> None:
    """Delete everything the tenant left behind, whatever the FK order.

    Written against the catalogue rather than a list, because the code under
    test writes to more tables than any list kept by hand would remember --
    audit events, outbox rows, ARI pushes. Each pass deletes what it can
    inside a savepoint and the loop repeats until nothing is left that a
    foreign key was protecting.
    """
    with owner.begin() as conn:
        conn.execute(text("SELECT set_config('app.system', 'on', true)"))
        tables = _tables_with_property_id(conn)
        children = (
            ("finance.folio_entry_taxes", "folio_entry_id",
             "SELECT id FROM finance.folio_entries WHERE property_id = :p"),
            ("finance.night_audit_steps", "run_id",
             "SELECT id FROM finance.night_audit_runs WHERE property_id = :p"),
            ("booking.group_block_nights", "block_id",
             "SELECT id FROM booking.group_blocks WHERE property_id = :p"),
        )
        for _ in range(8):
            failed = False
            for table, col, parent in children:
                sp = conn.begin_nested()
                try:
                    conn.execute(text(
                        f"DELETE FROM {table} WHERE {col} IN ({parent})"),
                        {"p": prop})
                    sp.commit()
                except Exception:  # noqa: BLE001 - table may not exist
                    sp.rollback()
            for table in tables:
                if table == "iam.properties":
                    continue
                sp = conn.begin_nested()
                try:
                    conn.execute(text(
                        f"DELETE FROM {table} WHERE property_id = :p"),
                        {"p": prop})
                    sp.commit()
                except Exception:  # noqa: BLE001 - retried next pass
                    sp.rollback()
                    failed = True
            if not failed:
                break
        for table in ("iam.audit_events",):
            sp = conn.begin_nested()
            try:
                conn.execute(text(
                    f"DELETE FROM {table} WHERE organization_id = :o"),
                    {"o": org})
                sp.commit()
            except Exception:  # noqa: BLE001
                sp.rollback()
        conn.execute(text("DELETE FROM iam.properties WHERE id = :p"),
                     {"p": prop})
        conn.execute(text("DELETE FROM iam.organizations WHERE id = :o"),
                     {"o": org})


class Tenant:
    """One fabricated hotel: an org, a property, a room type and its rooms."""

    def __init__(self, owner, runtime, *, currency="USD", rooms=2,
                 policy=True):
        self.owner, self.runtime = owner, runtime
        self.org, self.prop, self.rt = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
        self.currency = currency
        self.rooms: list[uuid.UUID] = []
        with owner.begin() as c:
            c.execute(text("SELECT set_config('app.system', 'on', true)"))
            c.execute(text(
                "INSERT INTO iam.organizations (id, name) VALUES (:o, :n)"),
                {"o": self.org, "n": f"test-{self.org.hex[:8]}"})
            c.execute(text(
                """
                INSERT INTO iam.properties
                    (id, organization_id, code, name, timezone, currency)
                VALUES (:p, :o, :code, 'Test hotel', 'Asia/Kolkata', :cur)
                """),
                {"p": self.prop, "o": self.org,
                 "code": f"{uuid.uuid4().int % 1_000_000:06d}",
                 "cur": currency})
            c.execute(text(
                """
                INSERT INTO property.room_types
                    (id, organization_id, property_id, code, name,
                     max_adults, max_children, max_occupancy, base_rate)
                VALUES (:rt, :o, :p, 'DLX', 'Deluxe', 2, 1, 3, 100)
                """),
                {"rt": self.rt, "o": self.org, "p": self.prop})
            for i in range(rooms):
                rid = uuid.uuid4()
                self.rooms.append(rid)
                c.execute(text(
                    """
                    INSERT INTO property.rooms
                        (id, organization_id, property_id, room_type_id, code)
                    VALUES (:id, :o, :p, :rt, :code)
                    """),
                    {"id": rid, "o": self.org, "p": self.prop, "rt": self.rt,
                     "code": f"{101 + i}"})
            if policy:
                c.execute(text(
                    """
                    INSERT INTO property.cancellation_policies
                        (organization_id, property_id, name, free_until_days,
                         penalty_nights, policy_text)
                    VALUES (:o, :p, 'Standard', 2, 1, 'One night inside 2 days')
                    """),
                    {"o": self.org, "p": self.prop})

    @contextmanager
    def session(self):
        """A runtime-role session bound to this tenant, as a request is."""
        from chirala_common.db import bind_tenant_context, make_session_factory

        s = make_session_factory(self.runtime)()
        bind_tenant_context(s, organization_id=self.org,
                            property_id=self.prop)
        try:
            yield s
            s.commit()
        except Exception:
            s.rollback()
            raise
        finally:
            s.close()

    def seed(self, start: date, nights: int, capacity: int | None = None):
        cap = len(self.rooms) if capacity is None else capacity
        with self.owner.begin() as c:
            for i in range(nights):
                c.execute(text(
                    """
                    INSERT INTO booking.room_type_inventory_days
                        (organization_id, property_id, room_type_id,
                         stay_date, physical_capacity, out_of_service,
                         held_units, reserved_units, allotment_units)
                    VALUES (:o, :p, :rt, :d, :cap, 0, 0, 0, 0)
                    ON CONFLICT (property_id, room_type_id, stay_date)
                    DO UPDATE SET physical_capacity = :cap
                    """),
                    {"o": self.org, "p": self.prop, "rt": self.rt,
                     "d": start + timedelta(days=i), "cap": cap})

    def counters(self, start: date, nights: int):
        with self.owner.connect() as c:
            return c.execute(text(
                """
                SELECT coalesce(sum(held_units), 0) AS held,
                       coalesce(sum(reserved_units), 0) AS reserved,
                       coalesce(sum(allotment_units), 0) AS allotment,
                       coalesce(sum(out_of_service), 0) AS oos
                  FROM booking.room_type_inventory_days
                 WHERE property_id = :p AND room_type_id = :rt
                   AND stay_date >= :s AND stay_date < :e
                """),
                {"p": self.prop, "rt": self.rt, "s": start,
                 "e": start + timedelta(days=nights)}).one()

    def hold(self, arrival: date, nights: int, *, units: int = 1,
             rate: Decimal | None = Decimal("100.00"), **kw):
        from booking_core.inventory import HoldLine, create_hold

        with self.session() as s:
            return create_hold(
                s, organization_id=self.org, property_id=self.prop,
                arrival_date=arrival,
                departure_date=arrival + timedelta(days=nights),
                idempotency_key=f"t-{uuid.uuid4()}",
                lines=[HoldLine(room_type_id=self.rt, units=units,
                                nightly_rate=rate)],
                hold_ttl_minutes=15, overbooking_allowance=0, **kw)

    def confirm(self, reservation_id):
        from booking_core.flow import confirm_reservation

        with self.session() as s:
            return confirm_reservation(s, reservation_id=reservation_id)

    def caller(self):
        from chirala_common.authz import Caller

        return Caller(subject="service:test", user_id=None,
                      organization_id=self.org, is_service=True)


@pytest.fixture()
def tenant(owner, runtime):
    made: list[Tenant] = []

    def make(**kw) -> Tenant:
        t = Tenant(owner, runtime, **kw)
        made.append(t)
        return t

    try:
        yield make
    finally:
        for t in made:
            _purge(owner, t.org, t.prop)


# --------------------------------------------------------------------------
# 1. Currency
# --------------------------------------------------------------------------
def test_a_new_reservation_is_in_its_propertys_currency(tenant):
    """A hold used to be written in the literal 'INR' whatever the hotel sold in.

    The folio opens in the reservation's currency and every posting takes the
    folio's, so this one literal relabelled a dollar property's whole ledger.
    """
    t = tenant(currency="USD")
    arrival = date.today() + timedelta(days=20)
    t.seed(arrival, 2)
    held = t.hold(arrival, 2)
    with t.owner.connect() as c:
        cur = c.execute(text(
            "SELECT currency FROM booking.reservations WHERE id = :r"),
            {"r": held.reservation_id}).scalar_one()
    assert cur == "USD"


# --------------------------------------------------------------------------
# 2. Cancel / modify race
# --------------------------------------------------------------------------
def _cancel(t, reservation_id, *, waive=False, reason="guest_request"):
    from booking_core.change_routes import CancelIn, cancel_reservation

    with t.session() as s:
        return cancel_reservation(
            reservation_id, t.prop,
            CancelIn(reason=reason, notes="test", waive_penalty=waive),
            caller=t.caller(), db=s)


def test_two_simultaneous_cancellations_release_the_rooms_once(tenant):
    """A desk cancel racing an OTA's redelivered cancel gave the rooms back twice.

    Both read the booking as confirmed before either committed, so both
    decremented ``reserved_units``: the second decrement came off *another*
    booking's night and the property sold a room it did not have. Two
    bookings share the nights here precisely so a double release shows up as
    1 -> 0 rather than being hidden by the counter's floor at zero.
    """
    from fastapi import HTTPException

    t = tenant(rooms=2)
    arrival = date.today() + timedelta(days=30)
    t.seed(arrival, 2)
    a = t.hold(arrival, 2)
    b = t.hold(arrival, 2)
    t.confirm(a.reservation_id)
    t.confirm(b.reservation_id)
    assert t.counters(arrival, 2).reserved == 4

    barrier = threading.Barrier(2)
    outcomes: list[str] = []

    def attempt():
        barrier.wait(timeout=5)
        try:
            _cancel(t, a.reservation_id, waive=True)
            outcomes.append("cancelled")
        except HTTPException as exc:
            outcomes.append(f"refused {exc.status_code}")

    threads = [threading.Thread(target=attempt) for _ in range(2)]
    for th in threads:
        th.start()
    for th in threads:
        th.join()

    assert sorted(outcomes) == ["cancelled", "refused 422"], outcomes
    # Booking b still holds both of its nights.
    assert t.counters(arrival, 2).reserved == 2
