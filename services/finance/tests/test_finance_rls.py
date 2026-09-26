"""The database itself keeps one tenant's money from another.

These tests connect as the runtime login the services use -- not the owner,
which as a superuser skips row security -- and check what Postgres returns,
not what the application chooses to ask for. In particular they run queries
that deliberately forget their tenant filter, because that is the bug row
security exists to catch.

Skipped unless both FINANCE_DATABASE_URL (the runtime login) and
FINANCE_MIGRATION_DATABASE_URL (the owner) are set, as they are inside the
finance container.
"""

from __future__ import annotations

import os
import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

RUNTIME_URL = os.getenv("FINANCE_DATABASE_URL")
OWNER_URL = os.getenv("FINANCE_MIGRATION_DATABASE_URL")
# Two tenants seeded by the root conftest when the database has fewer:
# isolation cannot be shown with one, and skipping for want of a second
# is how these tests used to pass on every fresh database.
pytestmark = [
    pytest.mark.skipif(
        not (RUNTIME_URL and OWNER_URL),
        reason="FINANCE_DATABASE_URL and FINANCE_MIGRATION_DATABASE_URL required",
    ),
    pytest.mark.usefixtures("two_tenants"),
]


@pytest.fixture(scope="module")
def runtime():
    from chirala_common.db import make_engine
    return make_engine(RUNTIME_URL)


@pytest.fixture(scope="module")
def owner():
    from chirala_common.db import make_engine
    return make_engine(OWNER_URL)


def _orgs_with_entries(owner) -> dict[str, int]:
    with owner.connect() as c:
        return {str(o): n for o, n in c.execute(text(
            "SELECT organization_id, count(*) FROM finance.folio_entries GROUP BY 1"))}


def test_runtime_login_is_bound_by_row_security(runtime):
    with runtime.connect() as c:
        user, sup, bypass = c.execute(text(
            "SELECT current_user, r.rolsuper, r.rolbypassrls FROM pg_roles r "
            "WHERE r.rolname = current_user")).one()
    assert (sup, bypass) == (False, False), f"{user} skips row security"


def test_every_finance_table_has_forced_row_security_and_a_policy(owner):
    with owner.connect() as c:
        rows = c.execute(text("""
            SELECT c.relname, c.relrowsecurity, c.relforcerowsecurity,
                   EXISTS (SELECT 1 FROM pg_policy p WHERE p.polrelid = c.oid) AS has_policy
            FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE n.nspname = 'finance' AND c.relkind = 'r'
              AND c.relname <> 'alembic_version'
        """)).all()
    unsecured = [r.relname for r in rows
                 if not (r.relrowsecurity and r.relforcerowsecurity and r.has_policy)]
    assert rows and not unsecured, f"finance tables without forced RLS: {unsecured}"


def test_no_tenant_bound_sees_nothing(runtime, owner):
    if not _orgs_with_entries(owner):
        pytest.skip("no folio entries to hide")
    with runtime.connect() as c:
        assert c.execute(text("SELECT count(*) FROM finance.folio_entries")).scalar() == 0
        assert c.execute(text("SELECT count(*) FROM finance.payments")).scalar() == 0


def test_a_query_that_forgets_its_filter_still_sees_one_tenant(runtime, owner):
    from chirala_common.db import bind_tenant_context
    from sqlalchemy.orm import Session

    orgs = _orgs_with_entries(owner)
    if len(orgs) < 2:
        pytest.skip("need two tenants with folio entries")
    for org, expected in orgs.items():
        with Session(runtime) as s:
            bind_tenant_context(s, organization_id=org)
            # No WHERE clause at all: exactly the mistake row security is for.
            seen = s.execute(text(
                "SELECT count(*), count(DISTINCT organization_id), "
                "min(organization_id::text) FROM finance.folio_entries")).one()
            assert seen == (expected, 1, org), (org, seen)
            joined = s.execute(text(
                "SELECT count(DISTINCT e.organization_id) FROM finance.folio_entry_taxes t "
                "JOIN finance.folio_entries e ON e.id = t.folio_entry_id")).scalar()
            assert joined in (0, 1)
            s.rollback()


def test_writing_into_another_tenant_is_refused(runtime, owner):
    from chirala_common.db import bind_tenant_context
    from sqlalchemy.orm import Session

    orgs = list(_orgs_with_entries(owner))
    if len(orgs) < 2:
        pytest.skip("need two tenants")
    mine, theirs = orgs[0], orgs[1]
    with owner.connect() as c:
        their_property = c.execute(text(
            "SELECT id FROM iam.properties WHERE organization_id = :o LIMIT 1"),
            {"o": theirs}).scalar()
    with Session(runtime) as s:
        bind_tenant_context(s, organization_id=mine)
        with pytest.raises(DBAPIError):
            s.execute(text(
                "INSERT INTO finance.unit_owners (organization_id, property_id, name) "
                "VALUES (:o, :p, 'should never land')"),
                {"o": theirs, "p": their_property})
        s.rollback()
    with Session(runtime) as s:
        bind_tenant_context(s, organization_id=mine)
        changed = s.execute(text(
            "UPDATE finance.folio_entries SET amount = amount WHERE organization_id = :o"),
            {"o": theirs}).rowcount
        assert changed == 0, "updated another tenant's ledger"
        s.rollback()


def test_system_context_sees_every_tenant_and_ends_with_the_transaction(runtime, owner):
    from chirala_common.db import system_context
    from sqlalchemy.orm import Session

    orgs = _orgs_with_entries(owner)
    if not orgs:
        pytest.skip("no folio entries")
    with Session(runtime) as s:
        system_context(s, reason="test: count every tenant")
        assert s.execute(text("SELECT count(*) FROM finance.folio_entries")).scalar() == sum(orgs.values())
        s.commit()
        # A new transaction on the same connection starts with no context.
        assert s.execute(text("SELECT count(*) FROM finance.folio_entries")).scalar() == 0
        s.rollback()


def test_provider_events_are_system_only(runtime):
    from chirala_common.db import bind_tenant_context
    from sqlalchemy.orm import Session

    with Session(runtime) as s:
        bind_tenant_context(s, organization_id=uuid.uuid4())
        assert s.execute(text("SELECT count(*) FROM finance.provider_events")).scalar() == 0
        s.rollback()
