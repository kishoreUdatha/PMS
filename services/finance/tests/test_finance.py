"""Acceptance tests for the finance ledger and night audit (schema §6, §13).

Requires PostgreSQL with finance migrations at head. Skipped when
FINANCE_DATABASE_URL is not set.

Covered invariants:
  - A payment posts exactly one credit entry per allocation; balance reflects it.
  - Over-allocation beyond captured funds is rejected.
  - Refunds cannot exceed the refundable balance (bounded), even across calls.
  - Duplicate charge posting (same source_line_key) posts once.
  - Night audit rerun posts no duplicate charges (idempotent close).
"""

from __future__ import annotations

import os
import uuid
from datetime import date, timedelta
from decimal import Decimal

import pytest
# Module scope on purpose. This file uses postponed annotations, so an
# annotation FastAPI has to resolve -- the `request: Request` on the session
# override below -- is a *string* looked up in this module's globals. Imported
# inside the test function it is invisible there, and FastAPI silently treats
# the parameter as a required query field: every request then 422s before the
# endpoint runs.
from fastapi import Request
from sqlalchemy import text

DB_URL = os.getenv("FINANCE_DATABASE_URL")
pytestmark = pytest.mark.skipif(
    not DB_URL, reason="FINANCE_DATABASE_URL not set; integration test skipped"
)


@pytest.fixture()
def engine():
    from chirala_common.db import make_engine

    # Ledger invariants, not tenancy: these fabricate tenants freely, so they
    # run as the schema owner when it is available. Row security has its own
    # tests in test_finance_rls.py.
    return make_engine(os.getenv("FINANCE_MIGRATION_DATABASE_URL") or DB_URL)


#: Every row these tests write hangs off a property id, so deleting by that one
#: key clears the lot -- but only in an order the foreign keys allow. This list
#: is derived from the actual FK graph, children first. Getting it wrong is not
#: harmless: the first version failed half-way through, and because the whole
#: teardown runs in one transaction, the rollback left *everything* behind.
_SCRATCH_TABLES = (
    "finance.credit_notes",
    "finance.invoices",
    "finance.payment_reversals",
    "finance.refunds",
    "finance.deposit_allocations",
    "finance.deposit_installments",
    "finance.payment_allocations",
    "finance.folio_adjustments",
    "finance.folio_entries",
    "finance.payments",
    "finance.cashier_shifts",
    "finance.payment_intents",
    "finance.folios",
    "finance.business_days",
    "booking.room_type_inventory_days",
    "booking.reservation_changes",
    "booking.room_moves",
    "booking.stay_checkins",
    "booking.stay_checkouts",
    "booking.reservation_units",
    "booking.reservations",
    "property.rooms",
    "property.room_types",
    # The audit trail is written by the code under test, so the test owns
    # those rows too. Missed on the first pass, and they leak exactly like
    # everything else.
    "iam.audit_events",
    "finance.night_audit_settings",
)

#: Tables with no property of their own, reached through their parent.
_SCRATCH_CHILDREN = (
    ("finance.night_audit_steps", "run_id",
     "SELECT id FROM finance.night_audit_runs WHERE property_id = :p"),
    ("finance.folio_entry_taxes", "folio_entry_id",
     "SELECT id FROM finance.folio_entries WHERE property_id = :p"),
    ("finance.invoice_lines", "invoice_id",
     "SELECT id FROM finance.invoices WHERE property_id = :p"),
)


@pytest.fixture()
def scratch(engine):
    """A fabricated org/property, and every row it leaves behind.

    These tests commit, and run against whatever FINANCE_DATABASE_URL points
    at -- on a developer machine, the working database. Without this teardown
    each run left a phantom property behind: invisible to every tenant-scoped
    screen because nothing in ``iam`` refers to it, yet still holding folios,
    payments and inventory.

    Teardown runs in a ``finally``, because the run that finds a bug is exactly
    the one you least want leaving debris.
    """
    org, prop = uuid.uuid4(), uuid.uuid4()
    try:
        yield org, prop
    finally:
        with engine.begin() as conn:
            for table, col, parent in _SCRATCH_CHILDREN:
                conn.execute(
                    text(f"DELETE FROM {table} WHERE {col} IN ({parent})"),
                    {"p": prop},
                )
            for table in _SCRATCH_TABLES:
                conn.execute(
                    text(f"DELETE FROM {table} WHERE property_id = :p"),
                    {"p": prop},
                )
            # Runs last: night_audit_steps is reached through it.
            conn.execute(
                text("DELETE FROM finance.night_audit_runs "
                     "WHERE property_id = :p"),
                {"p": prop},
            )
            # A test that needs the property to exist (the timezone guard
            # reads it) creates these two; keyed by `id`, not `property_id`,
            # so they need saying separately rather than being quietly missed.
            conn.execute(
                text("DELETE FROM iam.properties WHERE id = :p"), {"p": prop})
            conn.execute(
                text("DELETE FROM iam.organizations WHERE id = :org"),
                {"org": org})


def _make_folio(conn, org, prop):
    fid = uuid.uuid4()
    conn.execute(
        text(
            """
            INSERT INTO finance.folios
                (id, organization_id, property_id, type, currency, status)
            VALUES (:id, :org, :prop, 'guest', 'INR', 'open')
            """
        ),
        {"id": fid, "org": org, "prop": prop},
    )
    return fid


def test_payment_posts_one_credit_per_allocation_and_balance(engine, scratch):
    from chirala_common.db import make_session_factory

    from finance_service.ledger import (
        Allocation,
        folio_balance,
        post_charge,
        post_payment,
    )

    org, prop = scratch
    bd = date.today()
    with engine.begin() as conn:
        folio = _make_folio(conn, org, prop)

    factory = make_session_factory(engine)
    s = factory()
    # Charge 1000 (debit) -> balance 1000.
    post_charge(
        s, organization_id=org, property_id=prop, folio_id=folio,
        amount=Decimal("1000.00"), business_date=bd, source_type="room",
        source_line_key="room:1",
    )
    # Pay 400 -> one credit entry -> balance 600. Card, not cash: this is
    # about the arithmetic, and cash would need a drawer open to receive it.
    res = post_payment(
        s, organization_id=org, property_id=prop, method="card", business_date=bd,
        allocations=[Allocation(folio_id=folio, amount=Decimal("400.00"))],
    )
    s.commit()
    assert len(res.credit_entry_ids) == 1
    bal = folio_balance(s, folio)
    s.close()
    assert bal == Decimal("600.0000")


def test_over_allocation_rejected(engine, scratch):
    from chirala_common.db import make_session_factory

    from finance_service.ledger import (
        Allocation,
        LedgerError,
        allocate_payment,
        post_payment,
    )

    org, prop = scratch
    bd = date.today()
    with engine.begin() as conn:
        folio = _make_folio(conn, org, prop)

    factory = make_session_factory(engine)
    s = factory()
    res = post_payment(
        s, organization_id=org, property_id=prop, method="card", business_date=bd,
        allocations=[Allocation(folio_id=folio, amount=Decimal("500.00"))],
    )
    s.commit(); s.close()

    # Allocating 1 more than captured (500) must be rejected.
    s = factory()
    with pytest.raises(LedgerError) as ei:
        allocate_payment(
            s, organization_id=org, property_id=prop, payment_id=res.payment_id,
            folio_id=folio, amount=Decimal("0.01"), business_date=bd,
        )
    assert ei.value.conflict is True
    s.rollback(); s.close()


def test_refund_bounded_by_refundable(engine, scratch):
    from chirala_common.db import make_session_factory

    from finance_service.ledger import (
        Allocation,
        LedgerError,
        post_payment,
        post_refund,
    )

    org, prop = scratch
    bd = date.today()
    with engine.begin() as conn:
        folio = _make_folio(conn, org, prop)

    factory = make_session_factory(engine)
    s = factory()
    res = post_payment(
        s, organization_id=org, property_id=prop, method="card", business_date=bd,
        allocations=[Allocation(folio_id=folio, amount=Decimal("300.00"))],
    )
    s.commit(); s.close()

    # First refund 200 succeeds.
    s = factory()
    post_refund(
        s, organization_id=org, property_id=prop, payment_id=res.payment_id,
        amount=Decimal("200.00"), business_date=bd,
    )
    s.commit(); s.close()

    # Second refund 150 would exceed remaining 100 -> rejected.
    s = factory()
    with pytest.raises(LedgerError) as ei:
        post_refund(
            s, organization_id=org, property_id=prop, payment_id=res.payment_id,
            amount=Decimal("150.00"), business_date=bd,
        )
    assert ei.value.conflict is True
    s.rollback(); s.close()

    # Remaining 100 refund succeeds.
    s = factory()
    post_refund(
        s, organization_id=org, property_id=prop, payment_id=res.payment_id,
        amount=Decimal("100.00"), business_date=bd,
    )
    s.commit(); s.close()


def test_duplicate_charge_posts_once(engine, scratch):
    from chirala_common.db import make_session_factory

    from finance_service.ledger import post_charge

    org, prop = scratch
    bd = date.today()
    with engine.begin() as conn:
        folio = _make_folio(conn, org, prop)

    factory = make_session_factory(engine)
    s = factory()
    r1 = post_charge(
        s, organization_id=org, property_id=prop, folio_id=folio,
        amount=Decimal("250.00"), business_date=bd, source_type="minibar",
        source_line_key="minibar:xyz",
    )
    r2 = post_charge(
        s, organization_id=org, property_id=prop, folio_id=folio,
        amount=Decimal("250.00"), business_date=bd, source_type="minibar",
        source_line_key="minibar:xyz",
    )
    s.commit()
    assert r1.created is True
    assert r2.created is False
    assert r1.entry_id == r2.entry_id
    count = s.execute(
        text(
            "SELECT count(*) FROM finance.folio_entries "
            "WHERE folio_id = :f AND source_type = 'minibar'"
        ),
        {"f": folio},
    ).scalar_one()
    s.close()
    assert count == 1


def test_night_audit_rerun_idempotent(engine, scratch):
    from chirala_common.db import make_session_factory

    from finance_service.night_audit import NightlyCharge, run_night_audit

    org, prop = scratch
    bd = date.today() - timedelta(days=200)
    with engine.begin() as conn:
        folio = _make_folio(conn, org, prop)

    factory = make_session_factory(engine)
    charges = [NightlyCharge(folio_id=folio, amount=Decimal("5000.00"))]

    # First run posts the room-night charge and closes the day.
    s = factory()
    r1 = run_night_audit(
        s, organization_id=org, property_id=prop, business_date=bd,
        nightly_charges=charges,
    )
    s.commit(); s.close()
    assert r1.charges_posted == 1

    # Rerun must not post duplicates.
    s = factory()
    r2 = run_night_audit(
        s, organization_id=org, property_id=prop, business_date=bd,
        nightly_charges=charges,
    )
    s.commit()
    assert r2.charges_posted == 0
    total = s.execute(
        text(
            "SELECT count(*) FROM finance.folio_entries "
            "WHERE folio_id = :f AND source_type = 'room_night'"
        ),
        {"f": folio},
    ).scalar_one()
    s.close()
    assert total == 1


def _seed_stay(conn, org, prop, *, arrival, departure, status, rate,
               rooms=1, number=None):
    """A booking of `rooms` rooms, all on one folio-less reservation."""
    rt = uuid.uuid4()
    conn.execute(
        text(
            """
            INSERT INTO property.room_types
                (id, organization_id, property_id, code, name, status)
            VALUES (:id, :org, :prop, :code, 'Scratch Type', 'active')
            """
        ),
        {"id": rt, "org": org, "prop": prop,
         "code": f"RT{uuid.uuid4().hex[:6]}"},
    )
    res = uuid.uuid4()
    conn.execute(
        text(
            """
            INSERT INTO booking.reservations
                (id, organization_id, property_id, number, status, currency)
            VALUES (:id, :org, :prop, :num, 'confirmed', 'INR')
            """
        ),
        {"id": res, "org": org, "prop": prop,
         "num": number or f"T{uuid.uuid4().hex[:8].upper()}"},
    )
    units = []
    for i in range(rooms):
        room = uuid.uuid4()
        conn.execute(
            text(
                """
                INSERT INTO property.rooms
                    (id, organization_id, property_id, room_type_id, code,
                     status)
                VALUES (:id, :org, :prop, :rt, :code, 'active')
                """
            ),
            {"id": room, "org": org, "prop": prop, "rt": rt,
             "code": f"S{uuid.uuid4().hex[:4]}"},
        )
        unit = uuid.uuid4()
        conn.execute(
            text(
                """
                INSERT INTO booking.reservation_units
                    (id, organization_id, property_id, reservation_id,
                     room_type_id, arrival_date, departure_date, status,
                     nightly_rate, assigned_room_id, line_index)
                VALUES (:id, :org, :prop, :res, :rt, :arr, :dep, :st, :rate,
                        :room, :ix)
                """
            ),
            {"id": unit, "org": org, "prop": prop, "res": res, "rt": rt,
             "arr": arrival, "dep": departure, "st": status, "rate": rate,
             "room": room, "ix": i},
        )
        units.append(unit)
    return rt, res, units


def test_night_audit_accrues_the_night_and_reports_the_morning(engine, scratch):
    """The whole point of the audit, end to end.

    Covers what the derivation must get right, in one scenario because these
    facts only mean anything together: the guest asleep in the house is billed
    for tonight and only tonight, a two-room booking is billed twice on one
    folio, the booking that never arrived is reported rather than billed, the
    guest who has overstayed is billed like anyone else in a bed, and the
    booking window is pushed a day further out.
    """
    from chirala_common.db import make_session_factory
    from finance_service.night_audit import (
        INVENTORY_HORIZON_DAYS, run_night_audit,
    )

    org, prop = scratch
    bd = date.today() - timedelta(days=300)

    with engine.begin() as conn:
        # Two rooms on one booking, in-house tonight.
        _, res_in, units_in = _seed_stay(
            conn, org, prop, arrival=bd, departure=bd + timedelta(days=3),
            status="checked_in", rate=Decimal("4000.00"), rooms=2)
        # Due today, never checked in.
        _, _, _ = _seed_stay(
            conn, org, prop, arrival=bd, departure=bd + timedelta(days=1),
            status="reserved", rate=Decimal("4000.00"))
        # Should have left yesterday, still in the room.
        _, _, _ = _seed_stay(
            conn, org, prop, arrival=bd - timedelta(days=2), departure=bd,
            status="checked_in", rate=Decimal("2500.00"))

    factory = make_session_factory(engine)
    s = factory()
    r = run_night_audit(
        s, organization_id=org, property_id=prop, business_date=bd)
    s.commit(); s.close()

    # Three beds slept in: two rooms of the pair, plus the overstay.
    assert r.rooms_charged == 3
    assert r.charges_posted == 3
    assert r.amount_charged == Decimal("10500.00")  # 4000 + 4000 + 2500

    # The no-show is reported, not billed.
    assert len(r.no_shows) == 1
    assert len(r.overstays) == 1

    # The booking window moved.
    assert r.horizon_days_added > 0

    s = factory()
    # Both rooms of the one booking posted -- keyed by folio alone, the second
    # collided with the first and was silently dropped.
    on_folio = s.execute(
        text(
            """
            SELECT count(*) FROM finance.folio_entries e
            JOIN finance.folios f ON f.id = e.folio_id
            WHERE f.reservation_id = :res AND e.source_type = 'room_night'
            """
        ),
        {"res": res_in},
    ).scalar_one()
    assert on_folio == 2

    # Inventory now reaches the far edge of the horizon.
    furthest = s.execute(
        text(
            "SELECT max(stay_date) FROM booking.room_type_inventory_days "
            "WHERE property_id = :p"
        ),
        {"p": prop},
    ).scalar_one()
    assert furthest == bd + timedelta(days=INVENTORY_HORIZON_DAYS)

    # The run recorded the lines behind the total, not just the total. A
    # report opened months later has to say what was billed, and re-deriving
    # it then would answer a different question.
    detail = s.execute(
        text(
            """
            SELECT st.detail FROM finance.night_audit_steps st
            JOIN finance.night_audit_runs r ON r.id = st.run_id
            WHERE r.property_id = :p AND st.step_code = 'post_room_charges'
            """
        ),
        {"p": prop},
    ).scalar_one()
    lines = detail["lines"]
    assert len(lines) == 3
    assert sum(Decimal(l["amount"]) for l in lines) == Decimal("10500.00")
    # Each line names the room it is for, so the auditor can check it.
    assert all(l["room"] and l["reservation_number"] for l in lines)

    # A rerun is a no-op: the day is closed and nothing posts twice.
    again = run_night_audit(
        s, organization_id=org, property_id=prop, business_date=bd)
    s.commit()
    assert again.charges_posted == 0
    total = s.execute(
        text(
            "SELECT count(*) FROM finance.folio_entries "
            "WHERE property_id = :p AND source_type = 'room_night'"
        ),
        {"p": prop},
    ).scalar_one()
    s.close()
    assert total == 3


def test_night_audit_skips_a_stay_with_no_rate_rather_than_guessing(
    engine, scratch
):
    """A missing rate is reported, never invented.

    Billing a guest a made-up number is worse than telling the auditor a number
    is missing: one is a wrong bill nobody notices, the other is a line on a
    screen somebody fixes.
    """
    from chirala_common.db import make_session_factory
    from finance_service.night_audit import run_night_audit

    org, prop = scratch
    bd = date.today() - timedelta(days=320)
    with engine.begin() as conn:
        _seed_stay(conn, org, prop, arrival=bd,
                   departure=bd + timedelta(days=2),
                   status="checked_in", rate=None, number="NORATE1")

    factory = make_session_factory(engine)
    s = factory()
    r = run_night_audit(
        s, organization_id=org, property_id=prop, business_date=bd)
    s.commit(); s.close()

    assert r.rooms_charged == 0
    assert r.charges_posted == 0
    assert any("NORATE1" in w for w in r.warnings)


def test_a_day_that_has_not_happened_cannot_be_closed(engine, scratch):
    """Closing the future would silently lose the revenue it has yet to earn.

    Closing seals a date against posting, and the audit only ever visits days
    that are still open — so a night sealed before it happens is a night no
    guest is charged for, with nothing left to notice or retry. Today may be
    closed early, which is a normal operational call; tomorrow may not.
    """
    from chirala_common.db import make_session_factory
    from finance_service.night_audit import (
        NightAuditError, local_today, run_night_audit,
    )

    org, prop = scratch
    # The property must exist for the guard to read its timezone.
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO iam.organizations (id, name) VALUES (:org, 'Scratch')
                ON CONFLICT (id) DO NOTHING
                """
            ),
            {"org": org},
        )
        conn.execute(
            text(
                """
                INSERT INTO iam.properties
                    (id, organization_id, code, name, timezone, currency, status)
                VALUES (:p, :org, :code, 'Scratch', 'Asia/Kolkata', 'INR', 'active')
                """
            ),
            {"p": prop, "org": org,
             "code": f"{uuid.uuid4().int % 900000 + 100000}"},
        )

    factory = make_session_factory(engine)

    # Relative to the *property's* date, not the server's. The container runs
    # in UTC and the property is in Asia/Kolkata, so for five and a half hours
    # each night `date.today()` here is the day before the property's -- and a
    # naive "tomorrow" is really the property's today, which is closeable. The
    # guard was right; the test was reading a different clock from the code.
    probe = factory()
    today_there = local_today(probe, prop)
    probe.close()

    tomorrow = today_there + timedelta(days=1)
    s = factory()
    with pytest.raises(NightAuditError) as exc:
        run_night_audit(s, organization_id=org, property_id=prop,
                        business_date=tomorrow)
    s.rollback(); s.close()
    assert "has not happened yet" in str(exc.value)

    # Nothing was written: no day row, no run.
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT count(*) FROM finance.business_days "
                "WHERE property_id = :p AND business_date = :d"
            ),
            {"p": prop, "d": tomorrow},
        ).scalar_one()
    assert rows == 0

    # The property's today is allowed — running early is a legitimate choice.
    s = factory()
    res = run_night_audit(s, organization_id=org, property_id=prop,
                          business_date=today_there)
    s.commit(); s.close()
    assert "close_business_day" in res.steps


def test_an_open_till_stops_the_close_until_it_is_overridden(engine, scratch):
    """A drawer left open ties its cash to the wrong day.

    A shift is counted against the business date it belongs to, so one still
    open when the date rolls leaves its cash straddling two days and the
    banking never squares. The audit stops — as the systems hotels actually run
    on do — but it can be overridden, because a day that cannot close is worse:
    the revenue stays unposted and tomorrow there are two open days.

    What the override must not do is disappear. The run records how many tills
    it closed over and whose, so the report cannot be read as a clean close.
    """
    from chirala_common.db import make_session_factory
    from finance_service.night_audit import NightAuditError, run_night_audit

    org, prop = scratch
    bd = date.today() - timedelta(days=250)
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO finance.cashier_shifts
                    (id, organization_id, property_id, cashier_id,
                     business_date, status, opening_float)
                VALUES (:id, :org, :prop, :u, :d, 'open', 2000)
                """
            ),
            {"id": uuid.uuid4(), "org": org, "prop": prop, "u": uuid.uuid4(),
             "d": bd},
        )

    factory = make_session_factory(engine)

    # Refused by default, and the day is left alone.
    s = factory()
    with pytest.raises(NightAuditError) as exc:
        run_night_audit(s, organization_id=org, property_id=prop,
                        business_date=bd)
    s.rollback(); s.close()
    assert "cashier shift(s) are still open" in str(exc.value)

    with engine.connect() as conn:
        still_open = conn.execute(
            text(
                "SELECT count(*) FROM finance.business_days "
                "WHERE property_id = :p AND business_date = :d "
                "AND status = 'closed'"
            ),
            {"p": prop, "d": bd},
        ).scalar_one()
    assert still_open == 0

    # Overridden: the day closes, and says what it closed over.
    s = factory()
    res = run_night_audit(s, organization_id=org, property_id=prop,
                          business_date=bd, allow_open_shifts=True)
    s.commit(); s.close()
    assert res.open_shifts == 1
    assert any("still open when the day closed" in w for w in res.warnings)

    with engine.connect() as conn:
        detail = conn.execute(
            text(
                """
                SELECT st.detail FROM finance.night_audit_steps st
                JOIN finance.night_audit_runs r ON r.id = st.run_id
                WHERE r.property_id = :p AND st.step_code = 'reconcile_cashiering'
                ORDER BY st.created_at DESC LIMIT 1
                """
            ),
            {"p": prop},
        ).scalar_one()
    assert detail["open_shifts"] == 1
    assert detail["overridden"] is True


def test_a_closed_day_records_who_closed_it(engine, scratch):
    """Sealing a day is a financial act; it has to name someone.

    The run row said when a day was closed and never by whom, and nothing in
    the audit trail did either. That matters most for the case the override
    exists for: the run records whose till was left uncounted, so it must also
    record who decided to close over it — otherwise the exception names a
    cashier and protects nobody.

    A null actor is a real answer, not a missing one: it means the schedule ran
    unattended, which is different from a person having done it.
    """
    from chirala_common.db import make_session_factory
    from finance_service.night_audit import run_night_audit

    org, prop = scratch
    factory = make_session_factory(engine)
    who = uuid.uuid4()

    # Closed by a person.
    s = factory()
    run_night_audit(s, organization_id=org, property_id=prop,
                    business_date=date.today() - timedelta(days=260),
                    run_by=who, actor_subject='night.auditor@example.com')
    s.commit(); s.close()

    # Closed by the schedule.
    s = factory()
    run_night_audit(s, organization_id=org, property_id=prop,
                    business_date=date.today() - timedelta(days=259))
    s.commit(); s.close()

    with engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT business_date, run_by FROM finance.night_audit_runs "
                "WHERE property_id = :p ORDER BY business_date"
            ),
            {"p": prop},
        ).all()
        actors = conn.execute(
            text(
                """
                SELECT actor_subject FROM iam.audit_events
                WHERE property_id = :p AND action = 'night_audit.closed'
                ORDER BY occurred_at
                """
            ),
            {"p": prop},
        ).scalars().all()

    assert [r.run_by for r in rows] == [who, None]
    # Both paths leave a trail; the unattended one says so rather than
    # borrowing the name of whoever happened to deploy it.
    assert actors == ['night.auditor@example.com', 'system:night-audit']


def test_each_property_picks_its_own_hour_and_they_do_not_all_fire_at_once():
    """Two separate things, and only one of them is the setting.

    A per-property hour lets a city hotel close at 02:00 and a resort at 04:00.
    What it does *not* do is spread load: a thousand tenants that all leave it
    alone still fire at the same instant, and the default is the case that
    matters. The minute is derived from the property id so they spread anyway.
    """
    from finance_service.scheduler import AUDIT_HOUR, jitter_minute

    # Stable: the same property gets the same minute every night, so it does
    # not wander, and two replicas agree without coordinating.
    one = uuid.uuid4()
    assert jitter_minute(one) == jitter_minute(one)
    assert 0 <= jitter_minute(one) <= 59

    # Spread: a realistic tenant count does not pile onto one minute.
    minutes = [jitter_minute(uuid.uuid4()) for _ in range(600)]
    busiest = max(minutes.count(m) for m in set(minutes))
    assert len(set(minutes)) > 45, "600 tenants should not share a few minutes"
    assert busiest < 40, f"one minute took {busiest} of 600 tenants"

    assert 0 <= AUDIT_HOUR <= 23


def test_a_property_hour_is_honoured_and_never_closes_the_day_in_progress(
    engine, scratch
):
    """The chosen hour gates *when*, never *which* day.

    Whatever hour a tenant picks, the day in progress is never sealed — that is
    the guarantee the hour must not be able to weaken.
    """
    from datetime import datetime
    from zoneinfo import ZoneInfo

    from chirala_common.db import make_session_factory
    from finance_service.scheduler import due_days, jitter_minute

    org, prop = scratch
    with engine.begin() as conn:
        conn.execute(
            text("INSERT INTO iam.organizations (id, name) VALUES (:o, 'S') "
                 "ON CONFLICT (id) DO NOTHING"),
            {"o": org},
        )
        conn.execute(
            text(
                """
                INSERT INTO iam.properties
                    (id, organization_id, code, name, timezone, currency, status)
                VALUES (:p, :o, :c, 'Scratch', 'Asia/Kolkata', 'INR', 'active')
                """
            ),
            {"p": prop, "o": org,
             "c": f"{uuid.uuid4().int % 900000 + 100000}"},
        )
        # A day well in the past, so it is unambiguously finished.
        conn.execute(
            text(
                """
                INSERT INTO finance.business_days
                    (organization_id, property_id, business_date, status)
                VALUES (:o, :p, :d, 'open')
                """
            ),
            {"o": org, "p": prop, "d": date.today() - timedelta(days=270)},
        )

    factory = make_session_factory(engine)
    s = factory()

    now = datetime.now(ZoneInfo('Asia/Kolkata'))
    today_there = now.date()

    # An hour already past today: the finished day is due.
    past_hour = 0 if (now.hour, now.minute) > (0, jitter_minute(prop)) else None
    if past_hour is not None:
        due = due_days(s, prop, 'Asia/Kolkata', past_hour)
        assert due, "a finished day should be due once the hour has passed"
        assert today_there not in due, "the day in progress must never close"

    # An hour still ahead: nothing is due yet, and today is still not in it.
    future_hour = 23
    due_later = due_days(s, prop, 'Asia/Kolkata', future_hour)
    assert today_there not in due_later
    s.close()


def test_a_cash_refund_comes_out_of_the_drawer_it_was_paid_from(engine, scratch):
    """Money handed back has to leave the till the count is made against.

    A shift's expected cash was float plus cash taken, with nothing subtracted
    for cash paid out. So a cashier who refunded a guest ₹3,000 declared ₹3,000
    less than expected and showed a shortage they did not cause — and with
    three shifts a day, on whoever happened to be counting.

    Card and UPI refunds still play no part: that money never entered a drawer,
    so taking it out of one would invent the opposite error.
    """
    from chirala_common.db import make_session_factory
    from finance_service.ledger import Allocation, post_payment, post_refund

    org, prop = scratch
    bd = date.today() - timedelta(days=280)
    shift = uuid.uuid4()
    cashier = uuid.uuid4()
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO finance.cashier_shifts
                    (id, organization_id, property_id, cashier_id,
                     business_date, status, opening_float)
                VALUES (:id, :org, :prop, :u, :d, 'open', 2000)
                """
            ),
            {"id": shift, "org": org, "prop": prop, "u": cashier, "d": bd},
        )
        folio = _make_folio(conn, org, prop)

    factory = make_session_factory(engine)
    s = factory()
    pay = post_payment(
        s, organization_id=org, property_id=prop, method="cash",
        business_date=bd,
        allocations=[Allocation(folio_id=folio, amount=Decimal("5000.00"))],
        cashier_shift_id=shift,
    )
    s.execute(
        text("UPDATE finance.payments SET cashier_id = :u WHERE id = :id"),
        {"u": cashier, "id": pay.payment_id},
    )
    s.commit(); s.close()

    def expected() -> Decimal:
        with engine.connect() as conn:
            took = conn.execute(
                text("SELECT coalesce(sum(amount),0) FROM finance.payments "
                     "WHERE cashier_shift_id = :sh AND status = 'succeeded' "
                     "AND lower(method) = 'cash'"),
                {"sh": shift},
            ).scalar_one()
            gave = conn.execute(
                text("SELECT coalesce(sum(amount),0) FROM finance.refunds "
                     "WHERE cashier_shift_id = :sh AND status = 'succeeded' "
                     "AND lower(method) = 'cash'"),
                {"sh": shift},
            ).scalar_one()
        return Decimal("2000") + Decimal(str(took)) - Decimal(str(gave))

    assert expected() == Decimal("7000")  # float 2000 + cash in 5000

    # Hand ₹3,000 back out of the same drawer.
    s = factory()
    post_refund(
        s, organization_id=org, property_id=prop, payment_id=pay.payment_id,
        amount=Decimal("3000.00"), business_date=bd,
        cashier_shift_id=shift, method="cash",
    )
    s.commit(); s.close()

    assert expected() == Decimal("4000"), "the drawer should be 3000 lighter"

    # A card refund never touched the till, so it must not move the count.
    s = factory()
    post_refund(
        s, organization_id=org, property_id=prop, payment_id=pay.payment_id,
        amount=Decimal("1000.00"), business_date=bd,
        cashier_shift_id=shift, method="card",
    )
    s.commit(); s.close()

    assert expected() == Decimal("4000"), "a card refund is not drawer cash"


def test_cash_cannot_cross_a_drawer_that_is_not_open(engine, scratch):
    """Both directions, and every door, or the rule is not a rule.

    This is RCPT-1010: ₹2,250 in cash taken at 00:38, four minutes after the
    cashier closed and counted their drawer and nine hours before anyone
    opened the next one. The folio was credited, the guest got a receipt, and
    the money belonged to no shift — so no count would ever be short by it and
    nobody would ever be asked where it went.

    The Cashiering Centre refused this from the start. The folio's Add payment
    dialog, which is where it actually happened, did not, and neither did the
    deposit screen: one rule written at one of three doors. It lives in
    ``post_payment`` now, so the door does not matter.

    The closed-shift case is the same money and a worse lie — a drawer that
    has been counted and signed off cannot grow afterwards.
    """
    from chirala_common.db import make_session_factory
    from finance_service.ledger import (Allocation, LedgerError, post_payment,
                                        post_refund)

    org, prop = scratch
    bd = date.today() - timedelta(days=281)
    shut = uuid.uuid4()
    cashier = uuid.uuid4()
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO finance.cashier_shifts
                    (id, organization_id, property_id, cashier_id,
                     business_date, status, opening_float, closed_at)
                VALUES (:id, :org, :prop, :u, :d, 'closed', 0, now())
                """
            ),
            {"id": shut, "org": org, "prop": prop, "u": cashier, "d": bd},
        )
        folio = _make_folio(conn, org, prop)

    factory = make_session_factory(engine)

    def take(**kw):
        s = factory()
        try:
            return post_payment(
                s, organization_id=org, property_id=prop, business_date=bd,
                allocations=[Allocation(folio_id=folio,
                                        amount=Decimal("2250.00"))],
                **kw)
        finally:
            s.rollback(); s.close()

    with pytest.raises(LedgerError) as no_drawer:
        take(method="cash")
    assert no_drawer.value.conflict, "the desk can fix this by opening a shift"
    assert "open drawer" in str(no_drawer.value).lower()

    with pytest.raises(LedgerError) as counted:
        take(method="cash", cashier_shift_id=shut)
    assert "closed and counted" in str(counted.value)

    # Card needs no drawer: it never touches one.
    s = factory()
    paid = post_payment(
        s, organization_id=org, property_id=prop, method="card",
        business_date=bd,
        allocations=[Allocation(folio_id=folio, amount=Decimal("2250.00"))],
    )
    s.commit(); s.close()

    # And the same rule the other way, which is where it started.
    s = factory()
    with pytest.raises(LedgerError) as out:
        post_refund(
            s, organization_id=org, property_id=prop,
            payment_id=paid.payment_id, amount=Decimal("10.00"),
            business_date=bd, cashier_shift_id=shut, method="cash",
        )
    s.rollback(); s.close()
    assert "closed and counted" in str(out.value)


def test_an_unconfigured_property_records_a_no_show_without_charging(
        engine, scratch):
    """No policy chosen means no money taken, but the room still comes back.

    The default used to be one night plus tax, and no property had ever set
    the column, so every deployment was billing guests automatically for
    missed arrivals under a policy nobody had agreed to. ``find_no_shows``
    already stated the principle its sibling was breaking: a background job
    should not take a guest's money while nobody is watching.

    Recording the no-show is not the same act as charging for it, and this is
    the test that keeps them apart -- the booking is still closed off and the
    nights still go back on sale, because leaving a room off sale hurts the
    hotel and helps nobody.
    """
    from chirala_common.db import make_session_factory
    from finance_service.night_audit import run_night_audit

    org, prop = scratch
    bd = date.today() - timedelta(days=291)
    with engine.begin() as conn:
        rt, res, units = _seed_stay(
            conn, org, prop, arrival=bd, departure=bd + timedelta(days=2),
            status="reserved", rate=Decimal("4000.00"), number="NOSHOW2")
        for i in range(2):
            conn.execute(
                text(
                    """
                    INSERT INTO booking.room_type_inventory_days
                        (organization_id, property_id, room_type_id, stay_date,
                         physical_capacity, out_of_service, held_units,
                         reserved_units, allotment_units)
                    VALUES (:o, :p, :rt, :d, 5, 0, 0, 1, 0)
                    """
                ),
                {"o": org, "p": prop, "rt": rt, "d": bd + timedelta(days=i)},
            )

    factory = make_session_factory(engine)
    s = factory()
    r = run_night_audit(s, organization_id=org, property_id=prop,
                        business_date=bd)
    s.commit(); s.close()

    assert len(r.no_shows) == 1
    with engine.connect() as conn:
        status = conn.execute(
            text("SELECT status FROM booking.reservation_units WHERE id = :u"),
            {"u": units[0]},
        ).scalar_one()
        charged = conn.execute(
            text("SELECT count(*) FROM finance.folio_entries "
                 " WHERE property_id = :p AND source_type = 'no_show_penalty'"),
            {"p": prop},
        ).scalar_one()
        still_held = conn.execute(
            text("SELECT coalesce(sum(reserved_units),0) "
                 "  FROM booking.room_type_inventory_days WHERE property_id = :p"),
            {"p": prop},
        ).scalar_one()

    # Recorded and closed off...
    assert status == "no_show"
    # ...the nights back on sale...
    assert still_held == 0
    # ...and nobody billed for a policy the property never chose.
    assert charged == 0


def test_the_audit_resolves_a_no_show_and_charges_it_once(engine, scratch):
    """An arrival that never came is charged, released, and closed off.

    Left alone it is worse than untidy: the booking sits on a sealed date still
    holding its room, so the night is neither sold nor sellable, and stays that
    way until somebody notices.

    The penalty shares its idempotency key with the manual no-show screen, so a
    guest dealt with by hand this afternoon and by the audit tonight is charged
    once — which is the only safe way to have two paths to the same money.

    The property has to have *chosen* to charge. That used to be implicit: an
    unconfigured property charged a night because the default said so, and no
    property had ever configured one. Saying it here is the point — an
    unattended charge is a decision somebody made, and the test that proves
    the charge should show the decision.
    """
    from chirala_common.db import make_session_factory
    from finance_service.night_audit import run_night_audit

    org, prop = scratch
    bd = date.today() - timedelta(days=290)
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO finance.night_audit_settings
                    (property_id, organization_id, no_show_penalty)
                VALUES (:p, :o, 'one_night_tax')
                ON CONFLICT (property_id) DO UPDATE
                    SET no_show_penalty = EXCLUDED.no_show_penalty
                """
            ),
            {"p": prop, "o": org},
        )
        rt, res, units = _seed_stay(
            conn, org, prop, arrival=bd, departure=bd + timedelta(days=2),
            status="reserved", rate=Decimal("4000.00"), number="NOSHOW1")
        # The nights it is holding, so the release can be seen.
        for i in range(2):
            conn.execute(
                text(
                    """
                    INSERT INTO booking.room_type_inventory_days
                        (organization_id, property_id, room_type_id, stay_date,
                         physical_capacity, out_of_service, held_units,
                         reserved_units, allotment_units)
                    VALUES (:o, :p, :rt, :d, 5, 0, 0, 1, 0)
                    """
                ),
                {"o": org, "p": prop, "rt": rt, "d": bd + timedelta(days=i)},
            )

    factory = make_session_factory(engine)
    s = factory()
    r = run_night_audit(s, organization_id=org, property_id=prop,
                        business_date=bd)
    s.commit(); s.close()

    assert len(r.no_shows) == 1

    with engine.connect() as conn:
        unit = conn.execute(
            text("SELECT status, assigned_room_id FROM booking.reservation_units "
                 "WHERE id = :u"),
            {"u": units[0]},
        ).one()
        booking = conn.execute(
            text("SELECT status FROM booking.reservations WHERE id = :r"),
            {"r": res},
        ).scalar_one()
        charges = conn.execute(
            text("SELECT count(*), coalesce(sum(amount),0) "
                 "FROM finance.folio_entries "
                 "WHERE property_id = :p AND source_type = 'no_show_penalty'"),
            {"p": prop},
        ).one()
        still_held = conn.execute(
            text("SELECT coalesce(sum(reserved_units),0) "
                 "FROM booking.room_type_inventory_days WHERE property_id = :p"),
            {"p": prop},
        ).scalar_one()

    assert unit.status == 'no_show'
    assert unit.assigned_room_id is None
    # Every unit missed, so the booking itself is over.
    assert booking == 'cancelled'
    # One night's rate, charged once.
    assert charges[0] == 1 and Decimal(str(charges[1])) == Decimal("4000.00")
    # Both nights handed back to inventory.
    assert still_held == 0

    # Tomorrow's audit must not find it again, nor bill it twice.
    s = factory()
    again = run_night_audit(s, organization_id=org, property_id=prop,
                            business_date=bd + timedelta(days=1))
    s.commit(); s.close()
    assert again.no_shows == []

    with engine.connect() as conn:
        n = conn.execute(
            text("SELECT count(*) FROM finance.folio_entries "
                 "WHERE property_id = :p AND source_type = 'no_show_penalty'"),
            {"p": prop},
        ).scalar_one()
    assert n == 1, "the penalty must not post twice"


def test_a_webhook_is_verified_and_never_acted_on_twice(engine, scratch):
    """The four things that make a webhook handler safe.

    A gateway guarantees at-least-once delivery, so the same "payment
    captured" arrives more than once and each copy is an instruction to credit
    a folio. And the URL is public: anything that skips the signature is a
    stranger's instruction to credit somebody's account.

    The fourth is classification, and it is here because this test did not
    have it and went stale as a result. A capture is sorted into *malformed*
    (no order reference, so nothing to reconcile against) or *unmatched*
    (a real order nobody here opened, so somebody's money is at large) or a
    posting. The two refusals look alike and mean opposite things: one is a
    sender misconfigured, the other is money to chase. This test asserted the
    old undifferentiated "recorded" and kept passing until the split landed.
    """
    import hashlib
    import hmac
    import json as _json

    from fastapi.testclient import TestClient
    from sqlalchemy.orm import sessionmaker

    from finance_service.database import engine as app_engine, get_session
    from finance_service.settings import settings
    from finance_service.webhook_routes import verify_signature

    secret = "whsec_test_" + uuid.uuid4().hex

    # --- the signature check itself ------------------------------------
    body = b'{"event":"payment.captured"}'
    good = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    assert verify_signature(body, good, secret)
    assert not verify_signature(body, good, "another-secret")
    assert not verify_signature(b'{"event":"tampered"}', good, secret)
    # No secret and no signature are refusals, not free passes.
    assert not verify_signature(body, good, "")
    assert not verify_signature(body, "", secret)

    # --- and the endpoint ----------------------------------------------
    from finance_service.main import app

    # Everything the endpoint writes goes into this transaction and no
    # further. `create_savepoint` is what makes that work through a route
    # that commits: its commit releases a savepoint, and the outer rollback
    # in the `finally` below discards the lot.
    connection = app_engine.connect()
    outer = connection.begin()
    IsolatedSession = sessionmaker(
        bind=connection, join_transaction_mode="create_savepoint")

    def isolated_session(request: Request):
        session = IsolatedSession()
        # TransactionalRoute commits whatever it finds here, so it has to be
        # this session and not one from the real factory.
        request.state.db_session = session
        try:
            yield session
            if session.in_transaction():
                session.commit()
        finally:
            session.close()

    app.dependency_overrides[get_session] = isolated_session
    original = settings.razorpay_webhook_secret
    settings.razorpay_webhook_secret = secret
    try:
        client = TestClient(app)
        event_id = "evt_" + uuid.uuid4().hex[:16]
        # Deliberately carries no order_id: there is nothing to reconcile it
        # against, so the right answer is `malformed` -- kept, because a
        # gateway sending these is itself worth knowing about, but never
        # treated as money that landed somewhere.
        payload = _json.dumps(
            {"event": "payment.captured",
             "payload": {"payment": {"entity": {"id": "pay_x", "amount": 500000}}}}
        ).encode()
        sig = hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()
        headers = {"x-razorpay-signature": sig,
                   "x-razorpay-event-id": event_id,
                   "content-type": "application/json"}

        # Forged signature: refused before the body is even read.
        bad = client.post("/webhooks/razorpay", content=payload,
                          headers={**headers, "x-razorpay-signature": "0" * 64})
        assert bad.status_code == 400

        first = client.post("/webhooks/razorpay", content=payload,
                            headers=headers)
        assert first.status_code == 200
        assert first.json()["status"] == "malformed", (
            "a capture with no order id has nothing to reconcile against")

        # The retry a gateway sends after a timeout. Same id, same body.
        again = client.post("/webhooks/razorpay", content=payload,
                            headers=headers)
        assert again.status_code == 200, "a duplicate must not be an error"
        assert again.json()["status"] == "duplicate"
        # And a replay of the same signed body under a new event-id header.
        # The signature does not cover headers, so the header cannot be what
        # decides that this is the same event.
        replay = client.post("/webhooks/razorpay", content=payload,
                             headers={**headers, "x-razorpay-event-id": "evt_new"})
        assert replay.json()["status"] == "duplicate"
        event_id = first.json()["event_id"]

        # The other refusal, and the reason both are worth separating. This
        # one names an order the gateway really captured against and we have
        # no intent for: not a broken payload but somebody's payment with no
        # home, which is the one outcome on this route that needs a person.
        loose_id = "evt_" + uuid.uuid4().hex[:16]
        loose = _json.dumps(
            {"event": "payment.captured",
             "payload": {"payment": {"entity": {
                 "id": "pay_y", "amount": 500000,
                 "order_id": "order_" + uuid.uuid4().hex[:14]}}}}
        ).encode()
        lsig = hmac.new(secret.encode(), loose, hashlib.sha256).hexdigest()
        lost = client.post(
            "/webhooks/razorpay", content=loose,
            headers={"x-razorpay-signature": lsig,
                     "x-razorpay-event-id": loose_id,
                     "content-type": "application/json"})
        assert lost.status_code == 200
        loose_id = lost.json()["event_id"]
        assert lost.json()["status"] == "unmatched", (
            "a capture against an order we never opened is money to chase, "
            "not a malformed payload")

        # An event we do not act on is still recorded, so its retry is also
        # recognised rather than reconsidered forever.
        other_id = "evt_" + uuid.uuid4().hex[:16]
        chatter = _json.dumps({"event": "payment.authorized"}).encode()
        csig = hmac.new(secret.encode(), chatter, hashlib.sha256).hexdigest()
        ch = client.post(
            "/webhooks/razorpay", content=chatter,
            headers={"x-razorpay-signature": csig,
                     "x-razorpay-event-id": other_id,
                     "content-type": "application/json"})
        assert ch.status_code == 200 and ch.json()["status"] == "ignored"
        other_id = ch.json()["event_id"]

        # Every event is kept, whatever became of it -- that is what makes a
        # retry recognisable rather than reconsidered forever, and it is what
        # lets the platform's operations screen count malformed apart from
        # unmatched instead of showing one alarming lump.
        #
        # Read on the same connection the endpoint wrote through: the rows
        # exist only inside this transaction, so a separate one cannot see
        # them.
        rows = connection.execute(
            text("SELECT event_id, outcome FROM finance.provider_events "
                 "WHERE event_id IN (:a, :b, :c)"),
            {"a": event_id, "b": other_id, "c": loose_id},
        ).all()
        assert sorted(r.outcome for r in rows) == [
            "ignored", "malformed", "unmatched"]
    finally:
        settings.razorpay_webhook_secret = original
        app.dependency_overrides.pop(get_session, None)
        # No DELETE to forget: nothing was ever committed. This used to be a
        # tidy-up at the end of the happy path, which is why a failing run
        # left its rows in the live table.
        outer.rollback()
        connection.close()
