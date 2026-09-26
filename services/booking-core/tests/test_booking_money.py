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
                 policy=True, timezone="Asia/Kolkata"):
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
                VALUES (:p, :o, :code, 'Test hotel', :tz, :cur)
                """),
                {"p": self.prop, "o": self.org,
                 "code": f"{uuid.uuid4().int % 1_000_000:06d}",
                 "cur": currency, "tz": timezone})
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


# --------------------------------------------------------------------------
# 3. No-show release
# --------------------------------------------------------------------------
def _mark_no_show(t, unit_id, basis="one_night"):
    from booking_core.noshow_routes import NoShowIn, mark_no_show

    with t.session() as s:
        return mark_no_show(
            unit_id, t.prop,
            NoShowIn(penalty_basis=basis, reason="did_not_arrive"),
            caller=t.caller(), db=s)


def test_a_held_no_show_gives_back_the_held_room(tenant):
    """The desk's no-show always decremented reserved_units.

    A booking never confirmed sits in held_units, so its room stayed off sale
    and a *confirmed* guest's room was freed instead.
    """
    t = tenant(rooms=2)
    arrival = date.today() - timedelta(days=1)
    t.seed(arrival, 2)
    held = t.hold(arrival, 2)
    other = t.hold(arrival, 2)
    t.confirm(other.reservation_id)
    assert tuple(t.counters(arrival, 2))[:2] == (2, 2)

    _mark_no_show(t, held.reservation_unit_id)

    c = t.counters(arrival, 2)
    assert c.held == 0, "the held room goes back on sale"
    assert c.reserved == 2, "the confirmed booking keeps its room"


def test_the_audit_does_not_release_a_no_show_the_desk_already_released(
        tenant, monkeypatch):
    """The audit released nights before its guarded update and ignored the count.

    ``find_no_shows`` reads without locks. When the desk resolved the same
    arrival between that read and the audit's loop, the audit gave the nights
    back a second time. ``find_no_shows`` is pinned to its stale answer here
    to make that interleaving deterministic.
    """
    night_audit = pytest.importorskip("finance_service.night_audit")

    t = tenant(rooms=2)
    arrival = date.today() - timedelta(days=1)
    t.seed(arrival, 2)
    a = t.hold(arrival, 2)
    b = t.hold(arrival, 2)
    t.confirm(a.reservation_id)
    t.confirm(b.reservation_id)

    with t.session() as s:
        stale = night_audit.find_no_shows(
            s, property_id=t.prop, business_date=arrival)
    stale = [p for p in stale if p.unit_id == a.reservation_unit_id]
    assert stale

    _mark_no_show(t, a.reservation_unit_id)
    assert t.counters(arrival, 2).reserved == 2

    monkeypatch.setattr(night_audit, "find_no_shows", lambda *a, **k: stale)
    with t.session() as s:
        done, _, _ = night_audit.process_no_shows(
            s, organization_id=t.org, property_id=t.prop,
            business_date=arrival, basis="none")
    assert done == []
    assert t.counters(arrival, 2).reserved == 2, "b's nights are untouched"


# --------------------------------------------------------------------------
# 4. OTA cancellations and the cancellation policy
# --------------------------------------------------------------------------
def test_a_waived_cancellation_needs_no_policy(tenant):
    """Every OTA cancellation is waived, and was still refused for want of a policy.

    The quote demanded a policy before it looked at whether any penalty would
    be charged. Without the waiver a policy is still required: that is the
    case where a number has to come from somewhere.
    """
    from fastapi import HTTPException

    t = tenant(policy=False)
    arrival = date.today() + timedelta(days=1)
    t.seed(arrival, 1)
    a = t.hold(arrival, 1)
    t.confirm(a.reservation_id)

    with pytest.raises(HTTPException) as refused:
        _cancel(t, a.reservation_id, waive=False)
    assert refused.value.status_code == 422

    out = _cancel(t, a.reservation_id, waive=True)
    assert out.status == "applied" and out.penalty_amount == 0
    assert t.counters(arrival, 1).reserved == 0


def _channel_setup(t, reservation_id, booking_id):
    ext = f"ext-{uuid.uuid4().hex[:10]}"
    with t.owner.begin() as c:
        c.execute(text(
            "INSERT INTO distribution.channel_manager_links "
            "(organization_id, property_id, external_property_id) "
            "VALUES (:o, :p, :x)"), {"o": t.org, "p": t.prop, "x": ext})
        c.execute(text(
            "INSERT INTO distribution.channel_booking_events "
            "(revision_id, provider, event_type, outcome, booking_id, "
            " reservation_id, organization_id, property_id) "
            "VALUES (:r, 'channex', 'booking_new', 'created', :b, :res, "
            "        :o, :p)"),
            {"r": f"rev-{uuid.uuid4()}", "b": booking_id,
             "res": reservation_id, "o": t.org, "p": t.prop})
    return ext


def _deliver(t, attrs: dict) -> tuple[str, dict]:
    """Run one revision through ``_apply`` the way ``ingest`` does."""
    from chirala_common.db import make_session_factory, system_context

    from booking_core.channel_routes import _apply

    rev = f"rev-{uuid.uuid4()}"
    s = make_session_factory(t.runtime)()
    try:
        system_context(s, reason="test: channel webhook")
        s.execute(text(
            "INSERT INTO distribution.channel_booking_events "
            "(revision_id, provider, event_type, outcome) "
            "VALUES (:r, 'channex', 'booking_cancellation', 'claimed')"),
            {"r": rev})
        out = _apply(s, rev, attrs)
        s.commit()
    finally:
        s.close()
    return rev, out


def test_an_ota_cancellation_that_cannot_apply_is_recorded_not_raised(tenant):
    """An OTA cancelling a guest who is already in house raised out of the webhook.

    The claim row lives in the same transaction, so it rolled back too: the
    event disappeared and the poller offered it again for ever. It is now
    written as 'failed' with the reason, unacknowledged and replayable, and
    the booking is untouched.
    """
    t = tenant()
    arrival = date.today()
    t.seed(arrival, 2)
    a = t.hold(arrival, 2)
    t.confirm(a.reservation_id)
    with t.owner.begin() as c:
        c.execute(text("UPDATE booking.reservation_units "
                       "SET status = 'checked_in' WHERE id = :u"),
                  {"u": a.reservation_unit_id})
    booking_id = f"B-{uuid.uuid4().hex[:8]}"
    ext = _channel_setup(t, a.reservation_id, booking_id)

    rev, out = _deliver(t, {"status": "cancelled", "property_id": ext,
                            "booking_id": booking_id,
                            "ota_reservation_code": "OTA1",
                            "ota_name": "Booking.com"})

    assert out["status"] == "failed"
    with t.owner.connect() as c:
        ev = c.execute(text(
            "SELECT outcome, detail, acknowledged FROM "
            "distribution.channel_booking_events WHERE revision_id = :r"),
            {"r": rev}).one()
        status_ = c.execute(text(
            "SELECT status FROM booking.reservations WHERE id = :r"),
            {"r": a.reservation_id}).scalar_one()
    assert ev.outcome == "failed" and not ev.acknowledged
    assert "in house" in ev.detail
    assert status_ == "confirmed"
    assert t.counters(arrival, 2).reserved == 2


def test_an_ota_cancellation_on_a_property_without_a_policy_applies(tenant):
    t = tenant(policy=False)
    arrival = date.today() + timedelta(days=3)
    t.seed(arrival, 1)
    a = t.hold(arrival, 1)
    t.confirm(a.reservation_id)
    booking_id = f"B-{uuid.uuid4().hex[:8]}"
    ext = _channel_setup(t, a.reservation_id, booking_id)

    _, out = _deliver(t, {"status": "cancelled", "property_id": ext,
                          "booking_id": booking_id})
    assert out["status"] == "cancelled"
    assert t.counters(arrival, 1).reserved == 0


def test_a_new_property_gets_a_default_cancellation_policy(tenant):
    """Sign-up and the platform's create-tenant never seeded one."""
    from chirala_common.db import make_session_factory, system_context

    auth_routes = pytest.importorskip("iam_service.auth_routes")

    t = tenant(policy=False)
    s = make_session_factory(t.owner)()
    try:
        system_context(s, reason="test: seed property defaults")
        auth_routes._seed_property_defaults(s, org_id=t.org,
                                            property_id=t.prop)
        s.commit()
    finally:
        s.close()
    with t.owner.connect() as c:
        n = c.execute(text(
            "SELECT count(*) FROM property.cancellation_policies "
            "WHERE property_id = :p AND is_default"), {"p": t.prop}).scalar()
    assert n == 1


# --------------------------------------------------------------------------
# 5. Group blocks get their rooms back
# --------------------------------------------------------------------------
def _block(t, arrival, nights, rooms, commitment="definite"):
    from booking_core import group_blocks

    with t.session() as s:
        return group_blocks.create_block(
            s, organization_id=t.org, property_id=t.prop, name="Wedding",
            arrival_date=arrival,
            departure_date=arrival + timedelta(days=nights),
            lines=[group_blocks.BlockLine(room_type_id=t.rt,
                                          rooms_blocked=rooms)],
            commitment=commitment)


def _block_held(t, block_id):
    with t.owner.connect() as c:
        return c.execute(text(
            "SELECT coalesce(sum(rooms_held), 0) FROM "
            "booking.group_block_nights WHERE block_id = :b"),
            {"b": block_id}).scalar_one()


def test_a_cancelled_group_booking_goes_back_to_its_block(tenant):
    """give_back existed and nothing called it.

    A cancelled pick-up went back on general sale, so the group silently lost
    a room it had agreed and the block's numbers stopped adding up.
    """
    t = tenant(rooms=3)
    arrival = date.today() + timedelta(days=40)
    t.seed(arrival, 2)
    block = _block(t, arrival, 2, rooms=2)
    assert t.counters(arrival, 2).allotment == 4

    a = t.hold(arrival, 2, group_block_id=block)
    t.confirm(a.reservation_id)
    c = t.counters(arrival, 2)
    assert (c.allotment, c.reserved) == (2, 2)

    _cancel(t, a.reservation_id, waive=True)
    c = t.counters(arrival, 2)
    assert (c.allotment, c.reserved) == (4, 0), "the block has both rooms again"
    assert _block_held(t, block) == 4


def test_an_expired_group_hold_goes_back_to_its_block(tenant):
    from chirala_common.db import make_session_factory, system_context

    from booking_core.reaper import expire_stale_holds

    t = tenant(rooms=3)
    arrival = date.today() + timedelta(days=41)
    t.seed(arrival, 1)
    block = _block(t, arrival, 1, rooms=2)
    t.hold(arrival, 1, group_block_id=block)
    assert tuple(t.counters(arrival, 1))[:3] == (1, 0, 1)

    with t.owner.begin() as c:
        c.execute(text("UPDATE booking.booking_holds SET expires_at = "
                       "now() - interval '1 minute' WHERE property_id = :p"),
                  {"p": t.prop})
    s = make_session_factory(t.runtime)()
    try:
        system_context(s, reason="test: hold reaper")
        assert expire_stale_holds(s) == 1
        s.commit()
    finally:
        s.close()
    assert tuple(t.counters(arrival, 1))[:3] == (0, 0, 2)
    assert _block_held(t, block) == 2


def test_a_released_block_does_not_take_rooms_back(tenant):
    """After cut-off the block has handed its rooms to the hotel.

    A pick-up cancelled then must go on general sale; crediting the block
    would take a room off sale for a group that no longer holds any.
    """
    from booking_core import group_blocks

    t = tenant(rooms=3)
    arrival = date.today() + timedelta(days=42)
    t.seed(arrival, 1)
    block = _block(t, arrival, 1, rooms=2)
    a = t.hold(arrival, 1, group_block_id=block)
    t.confirm(a.reservation_id)
    with t.session() as s:
        group_blocks.release(s, block_id=block, property_id=t.prop,
                             organization_id=t.org)
    assert tuple(t.counters(arrival, 1))[:3] == (0, 1, 0)

    _cancel(t, a.reservation_id, waive=True)
    assert tuple(t.counters(arrival, 1))[:3] == (0, 0, 0)
    assert _block_held(t, block) == 0


# --------------------------------------------------------------------------
# 6. Out-of-service rooms are off sale
# --------------------------------------------------------------------------
def test_a_room_marked_out_of_service_is_not_sold(tenant):
    """Only room blocks reached out_of_service; the room's own flag reached nothing.

    A room set out of service on the room screen stayed bookable at the desk
    and on every channel. A room that is both blocked and out of service is
    one room off sale, not two.
    """
    from booking_core.inventory import InventoryShortage

    t = tenant(rooms=2)
    arrival = date.today() + timedelta(days=10)
    t.seed(arrival, 3)
    assert t.counters(arrival, 3).oos == 0

    with t.session() as s:
        s.execute(text("UPDATE property.rooms SET service_status = "
                       "'out_of_service' WHERE id = :r"), {"r": t.rooms[0]})
    assert t.counters(arrival, 3).oos == 3, "one room off sale on each night"

    with pytest.raises(InventoryShortage):
        t.hold(arrival, 1, units=2)

    # A block on the same room, first night only: still one room, not two.
    with t.session() as s:
        s.execute(text(
            """
            INSERT INTO booking.room_blocks
                (organization_id, property_id, room_id, group_id, block_type,
                 reason_category, start_date, end_date)
            VALUES (:o, :p, :r, gen_random_uuid(), 'room_block', 'other',
                    :d, :d)
            """), {"o": t.org, "p": t.prop, "r": t.rooms[0], "d": arrival})
    assert t.counters(arrival, 3).oos == 3

    # Back in service: only the blocked night stays off sale.
    with t.session() as s:
        s.execute(text("UPDATE property.rooms SET service_status = "
                       "'in_service' WHERE id = :r"), {"r": t.rooms[0]})
    assert t.counters(arrival, 3).oos == 1
    t.hold(arrival + timedelta(days=1), 1, units=2)


def test_a_new_inventory_night_starts_with_out_of_service_rooms_counted(
        tenant):
    t = tenant(rooms=2)
    with t.session() as s:
        s.execute(text("UPDATE property.rooms SET service_status = "
                       "'maintenance' WHERE id = :r"), {"r": t.rooms[1]})
    later = date.today() + timedelta(days=90)
    t.seed(later, 1)
    assert t.counters(later, 1).oos == 1


# --------------------------------------------------------------------------
# 7. Seeding with reset_counters
# --------------------------------------------------------------------------
def _seed_route(t, start, end, *, cap, reset):
    from booking_core.routes import seed_inventory
    from booking_core.schemas import InventorySeed

    with t.session() as s:
        return seed_inventory(
            InventorySeed(property_id=t.prop, room_type_id=t.rt,
                          start_date=start, end_date=end,
                          physical_capacity=cap, reset_counters=reset),
            caller=t.caller(), db=s)


def test_resetting_counters_is_refused_under_live_bookings(tenant):
    """reset_counters zeroed held/reserved on nights with live bookings.

    Every booking on those nights went back on sale while its guest still
    expected the room.
    """
    from fastapi import HTTPException

    t = tenant(rooms=2)
    arrival = date.today() + timedelta(days=50)
    t.seed(arrival, 3)
    a = t.hold(arrival + timedelta(days=1), 1)
    t.confirm(a.reservation_id)

    with pytest.raises(HTTPException) as refused:
        _seed_route(t, arrival, arrival + timedelta(days=2), cap=2, reset=True)
    assert refused.value.status_code == 409
    assert str(arrival + timedelta(days=1)) in refused.value.detail
    assert t.counters(arrival, 3).reserved == 1

    # A night with nothing live can still be reset.
    _seed_route(t, arrival, arrival, cap=2, reset=True)


# --------------------------------------------------------------------------
# 8. Folio postings go through the ledger's rules
# --------------------------------------------------------------------------
def _folio_and_tax(t, reservation_id, *, open_day: date):
    """A folio, a 12% room tax, and a business day the audit has not closed."""
    folio = uuid.uuid4()
    with t.owner.begin() as c:
        c.execute(text(
            "INSERT INTO finance.folios (id, organization_id, property_id, "
            "reservation_id, type, currency, status) "
            "VALUES (:f, :o, :p, :r, 'guest', :cur, 'open')"),
            {"f": folio, "o": t.org, "p": t.prop, "r": reservation_id,
             "cur": t.currency})
        c.execute(text(
            "INSERT INTO finance.tax_rules (organization_id, property_id, "
            "code, effective_from, name, charge_type, rate_type, rate_value, "
            "apply_as, applicability, is_default) VALUES (:o, :p, 'GST12', "
            "'2000-01-01', 'GST 12', 'tax_group', 'percent', 12, "
            "'exclusive', ARRAY['rooms'], true)"),
            {"o": t.org, "p": t.prop})
        c.execute(text(
            "INSERT INTO finance.business_days (organization_id, property_id, "
            "business_date, status) VALUES (:o, :p, :d, 'open')"),
            {"o": t.org, "p": t.prop, "d": open_day})
    return folio


def test_a_cancellation_fee_is_posted_through_the_ledger(tenant):
    """booking-core wrote the fee straight into folio_entries.

    Untaxed (finance taxes a cancellation fee as a room), stamped with UTC's
    CURRENT_DATE rather than the ledger's open business day, and in the
    reservation's currency whatever the folio kept. It now goes through the
    same posting code finance uses.
    """
    t = tenant(currency="USD")
    arrival = date.today() + timedelta(days=1)  # inside the 2-day window
    t.seed(arrival, 2)
    a = t.hold(arrival, 2, rate=Decimal("100.00"))
    t.confirm(a.reservation_id)
    open_day = date.today() - timedelta(days=3)
    folio = _folio_and_tax(t, a.reservation_id, open_day=open_day)

    out = _cancel(t, a.reservation_id)
    assert out.penalty_amount == Decimal("100.00")

    with t.owner.connect() as c:
        rows = c.execute(text(
            "SELECT source_type, amount, currency, business_date "
            "FROM finance.folio_entries WHERE folio_id = :f "
            "ORDER BY source_type"), {"f": folio}).all()
    assert [(r.source_type, r.amount, r.currency, r.business_date)
            for r in rows] == [
        ("cancellation_fee", Decimal("100.0000"), "USD", open_day),
        ("cancellation_fee_tax", Decimal("12.0000"), "USD", open_day),
    ]


def test_a_no_show_penalty_is_taxed_by_the_engine_it_is_quoted_from(tenant):
    """The desk computed no-show tax with its own mirror and wrote it raw.

    Now the quote and the posting are the same engine, and the line keys are
    the night audit's, so the two paths cannot charge twice.
    """
    t = tenant(currency="USD")
    arrival = date.today() - timedelta(days=1)
    t.seed(arrival, 2)
    a = t.hold(arrival, 2)
    t.confirm(a.reservation_id)
    open_day = date.today() - timedelta(days=1)
    folio = _folio_and_tax(t, a.reservation_id, open_day=open_day)

    out = _mark_no_show(t, a.reservation_unit_id, basis="one_night_tax")
    assert out.penalty_charged == Decimal("112.00")
    with t.owner.connect() as c:
        rows = c.execute(text(
            "SELECT source_type, source_line_key, amount, business_date "
            "FROM finance.folio_entries WHERE folio_id = :f "
            "ORDER BY source_type"), {"f": folio}).all()
    u = a.reservation_unit_id
    assert [tuple(r) for r in rows] == [
        ("no_show_penalty", f"no_show_penalty:{u}", Decimal("100.0000"),
         open_day),
        ("no_show_penalty_tax", f"no_show_penalty:{u}#tax",
         Decimal("12.0000"), open_day),
    ]


# --------------------------------------------------------------------------
# 9. A payment that arrives after the hold expired
# --------------------------------------------------------------------------
def _reap(t):
    from chirala_common.db import make_session_factory, system_context

    from booking_core.reaper import expire_stale_holds

    with t.owner.begin() as c:
        c.execute(text("UPDATE booking.booking_holds SET expires_at = "
                       "now() - interval '1 minute' WHERE property_id = :p "
                       "AND status = 'held'"), {"p": t.prop})
    s = make_session_factory(t.runtime)()
    try:
        system_context(s, reason="test: hold reaper")
        n = expire_stale_holds(s)
        s.commit()
    finally:
        s.close()
    return n


def test_a_late_payment_confirms_an_expired_hold_whose_rooms_are_free(tenant):
    """Paying after the reaper ran left money on a folio for a dead booking.

    confirm_reservation refused anything not 'held'. If the rooms are still
    free, the guest now gets them.
    """
    t = tenant(rooms=1)
    arrival = date.today() + timedelta(days=60)
    t.seed(arrival, 2)
    a = t.hold(arrival, 2)
    assert _reap(t) == 1
    assert tuple(t.counters(arrival, 2))[:2] == (0, 0)

    assert t.confirm(a.reservation_id) is True
    assert tuple(t.counters(arrival, 2))[:2] == (0, 2)
    with t.owner.connect() as c:
        st = c.execute(text(
            "SELECT r.status, (SELECT array_agg(DISTINCT u.status) FROM "
            "booking.reservation_units u WHERE u.reservation_id = r.id) "
            "FROM booking.reservations r WHERE r.id = :r"),
            {"r": a.reservation_id}).one()
    assert st[0] == "confirmed" and st[1] == ["reserved"]


def test_a_late_payment_for_a_resold_room_is_refused_clearly(tenant):
    from booking_core.flow import FlowError

    t = tenant(rooms=1)
    arrival = date.today() + timedelta(days=61)
    t.seed(arrival, 1)
    a = t.hold(arrival, 1)
    assert _reap(t) == 1
    b = t.hold(arrival, 1)  # the room is sold to someone else
    t.confirm(b.reservation_id)

    with pytest.raises(FlowError) as refused:
        t.confirm(a.reservation_id)
    assert refused.value.conflict
    assert "refund" in str(refused.value)
    assert tuple(t.counters(arrival, 1))[:2] == (0, 1)


# --------------------------------------------------------------------------
# 10. The property's date, not UTC's
# --------------------------------------------------------------------------
def _off_by_a_day_zone():
    """A timezone whose date differs from UTC's right now, and its date.

    Chosen by the clock so the test means the same thing whenever it runs:
    UTC+14 once UTC is past 10:00 (it is already tomorrow there), UTC-11
    before (it is still yesterday).
    """
    from datetime import datetime, timezone
    from zoneinfo import ZoneInfo

    now = datetime.now(timezone.utc)
    zone = "Pacific/Kiritimati" if now.hour >= 10 else "Pacific/Pago_Pago"
    local = datetime.now(ZoneInfo(zone)).date()
    assert local != now.date()
    return zone, local, now.date()


def test_the_penalty_window_counts_days_on_the_propertys_calendar(tenant):
    """cancel_quote counted days to arrival from UTC's CURRENT_DATE."""
    from booking_core.change_routes import _cancel_terms

    zone, local, _ = _off_by_a_day_zone()
    t = tenant(timezone=zone)
    arrival = local + timedelta(days=5)
    t.seed(arrival, 1)
    a = t.hold(arrival, 1)
    with t.session() as s:
        q = _cancel_terms(s, a.reservation_id, t.prop)
    assert q.days_before_arrival == 5


def test_a_block_is_released_on_its_cut_off_in_the_propertys_timezone(tenant):
    """The cut-off sweep compared every tenant's blocks with the server's date."""
    from chirala_common.db import make_session_factory

    from booking_core import group_blocks

    zone, local, utc = _off_by_a_day_zone()
    t = tenant(timezone=zone, rooms=2)
    arrival = max(local, utc) + timedelta(days=10)
    t.seed(arrival, 1)
    # The date the old sweep got wrong: local's when it is ahead of UTC (it
    # should release and did not), UTC's when it is behind (it should not
    # release and did).
    cut_off = local if local > utc else utc
    with t.session() as s:
        block = group_blocks.create_block(
            s, organization_id=t.org, property_id=t.prop, name="Tour",
            arrival_date=arrival, departure_date=arrival + timedelta(days=1),
            lines=[group_blocks.BlockLine(room_type_id=t.rt,
                                          rooms_blocked=1)],
            commitment="definite", cut_off_date=cut_off)
    s = make_session_factory(t.runtime)()
    try:
        results = group_blocks.sweep_cut_offs(s)
        s.commit()
    finally:
        s.close()
    mine = [r for r in results if r["block_id"] == str(block)]
    assert bool(mine) == (cut_off <= local)
