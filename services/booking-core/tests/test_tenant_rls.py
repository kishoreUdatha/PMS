"""The database itself keeps one tenant's bookings, rooms and guests from another.

Connects as the runtime login the services use and checks what Postgres
returns -- including for queries that deliberately forget their tenant filter.
Skipped unless BOOKING_DATABASE_URL (runtime) and BOOKING_MIGRATION_DATABASE_URL
(owner) are both set, as they are inside the booking-core container.
"""

from __future__ import annotations

import os
import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

RUNTIME_URL = os.getenv("BOOKING_DATABASE_URL")
OWNER_URL = os.getenv("BOOKING_MIGRATION_DATABASE_URL")
pytestmark = pytest.mark.skipif(
    not (RUNTIME_URL and OWNER_URL),
    reason="BOOKING_DATABASE_URL and BOOKING_MIGRATION_DATABASE_URL required",
)

SCHEMAS = ("booking", "property", "operations", "engagement", "distribution")

#: One table per schema with rows in a working deployment, checked for leaks.
PROBES = (
    ("booking.reservations", "organization_id"),
    ("property.rooms", "organization_id"),
    ("operations.room_status_events", "organization_id"),
    ("engagement.guests", "organization_id"),
)


@pytest.fixture(scope="module")
def runtime():
    from chirala_common.db import make_engine
    return make_engine(RUNTIME_URL)


@pytest.fixture(scope="module")
def owner():
    from chirala_common.db import make_engine
    return make_engine(OWNER_URL)


def _counts(owner, table) -> dict[str, int]:
    with owner.connect() as c:
        return {str(o): n for o, n in c.execute(text(
            f"SELECT organization_id, count(*) FROM {table} GROUP BY 1"))}


def test_every_table_has_forced_row_security_and_a_policy(owner):
    with owner.connect() as c:
        rows = c.execute(text("""
            SELECT n.nspname || '.' || c.relname AS name, c.relrowsecurity,
                   c.relforcerowsecurity,
                   EXISTS (SELECT 1 FROM pg_policy p WHERE p.polrelid = c.oid) AS has_policy
            FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE n.nspname = ANY(:s) AND c.relkind = 'r'
              AND c.relname <> 'alembic_version'
        """), {"s": list(SCHEMAS)}).all()
    bad = [r.name for r in rows if not (r.relrowsecurity and r.relforcerowsecurity and r.has_policy)]
    assert rows and not bad, f"tables without forced RLS: {bad}"


def test_no_views_read_past_row_security(owner):
    with owner.connect() as c:
        views = c.execute(text(
            "SELECT schemaname || '.' || viewname FROM pg_views WHERE schemaname = ANY(:s) "
            "UNION ALL SELECT schemaname || '.' || matviewname FROM pg_matviews "
            "WHERE schemaname = ANY(:s)"), {"s": list(SCHEMAS)}).scalars().all()
    assert not views, f"views owned by the schema owner bypass RLS: {views}"


@pytest.mark.parametrize("table,col", PROBES)
def test_no_tenant_bound_sees_nothing(runtime, owner, table, col):
    if not _counts(owner, table):
        pytest.skip(f"{table} is empty")
    with runtime.connect() as c:
        assert c.execute(text(f"SELECT count(*) FROM {table}")).scalar() == 0


@pytest.mark.parametrize("table,col", PROBES)
def test_a_query_that_forgets_its_filter_sees_one_tenant(runtime, owner, table, col):
    from chirala_common.db import bind_tenant_context
    from sqlalchemy.orm import Session

    counts = _counts(owner, table)
    if len(counts) < 2:
        pytest.skip(f"{table} needs rows from two tenants")
    for org, expected in counts.items():
        with Session(runtime) as s:
            bind_tenant_context(s, organization_id=org)
            seen = s.execute(text(
                f"SELECT count(*), count(DISTINCT {col}), min({col}::text) FROM {table}")).one()
            assert seen == (expected, 1, org), (table, org, seen)
            s.rollback()


def test_child_tables_follow_their_parent(runtime, owner):
    from chirala_common.db import bind_tenant_context
    from sqlalchemy.orm import Session

    with owner.connect() as c:
        total = c.execute(text("SELECT count(*) FROM property.rate_plan_room_types")).scalar()
        by_org = dict(c.execute(text(
            "SELECT p.organization_id::text, count(*) FROM property.rate_plan_room_types l "
            "JOIN iam.properties p ON p.id = l.property_id GROUP BY 1")).all())
    if not total:
        pytest.skip("no rate plan room types")
    for org, expected in by_org.items():
        with Session(runtime) as s:
            bind_tenant_context(s, organization_id=org)
            assert s.execute(text("SELECT count(*) FROM property.rate_plan_room_types")).scalar() == expected
            s.rollback()


def test_writing_into_another_tenant_is_refused(runtime, owner):
    from chirala_common.db import bind_tenant_context
    from sqlalchemy.orm import Session

    orgs = list(_counts(owner, "engagement.guests"))
    if len(orgs) < 2:
        pytest.skip("need two tenants with guests")
    mine, theirs = orgs[0], orgs[1]
    with Session(runtime) as s:
        bind_tenant_context(s, organization_id=mine)
        with pytest.raises(DBAPIError):
            s.execute(text("INSERT INTO engagement.guests (id, organization_id, full_name) "
                           "VALUES (:i, :o, 'should never land')"),
                      {"i": uuid.uuid4(), "o": theirs})
        s.rollback()
    with Session(runtime) as s:
        bind_tenant_context(s, organization_id=mine)
        changed = s.execute(text("UPDATE engagement.guests SET full_name = full_name "
                                 "WHERE organization_id = :o"), {"o": theirs}).rowcount
        assert changed == 0, "renamed another tenant's guests"
        s.rollback()


def test_system_context_sees_all_and_ends_with_the_transaction(runtime, owner):
    from chirala_common.db import system_context
    from sqlalchemy.orm import Session

    total = sum(_counts(owner, "booking.reservations").values())
    if not total:
        pytest.skip("no reservations")
    with Session(runtime) as s:
        system_context(s, reason="test: count every tenant's reservations")
        assert s.execute(text("SELECT count(*) FROM booking.reservations")).scalar() == total
        s.commit()
        assert s.execute(text("SELECT count(*) FROM booking.reservations")).scalar() == 0
        s.rollback()
