"""Identity, access and audit data are isolated by the database itself.

Connects as the runtime login and checks what Postgres returns in each of the
contexts a transaction can be in: none, a tenant, one subject's identity
lookup, one property code, and system. Skipped unless IAM_DATABASE_URL (runtime)
and IAM_MIGRATION_DATABASE_URL (owner) are both set, as they are in the iam
container.
"""

from __future__ import annotations

import os
import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

RUNTIME_URL = os.getenv("IAM_DATABASE_URL")
OWNER_URL = os.getenv("IAM_MIGRATION_DATABASE_URL")
# Two tenants seeded by the root conftest when the database has fewer:
# isolation cannot be shown with one, and skipping for want of a second
# is how these tests used to pass on every fresh database.
pytestmark = [
    pytest.mark.skipif(
        not (RUNTIME_URL and OWNER_URL),
        reason="IAM_DATABASE_URL and IAM_MIGRATION_DATABASE_URL required",
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


def _session(engine):
    from sqlalchemy.orm import Session
    return Session(engine)


def test_every_iam_table_has_forced_row_security(owner):
    with owner.connect() as c:
        rows = c.execute(text("""
            SELECT c.relname, c.relrowsecurity, c.relforcerowsecurity,
                   (SELECT count(*) FROM pg_policy p WHERE p.polrelid = c.oid) AS policies
            FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE n.nspname = 'iam' AND c.relkind = 'r' AND c.relname <> 'alembic_version'
        """)).all()
    bad = [r.relname for r in rows if not (r.relrowsecurity and r.relforcerowsecurity and r.policies)]
    assert rows and not bad, f"iam tables without forced RLS: {bad}"


def test_identity_function_is_narrow(owner):
    with owner.connect() as c:
        definer, config = c.execute(text(
            "SELECT p.prosecdef, p.proconfig FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace "
            "WHERE n.nspname = 'tenancy' AND p.proname = 'identity_user_id'")).one()
        public = c.execute(text(
            "SELECT has_function_privilege('public', 'tenancy.identity_user_id()', 'EXECUTE')")).scalar()
    assert definer and any(s.startswith("search_path=") for s in (config or []))
    assert public is False


def test_no_context_sees_no_identity_data(runtime, owner):
    with owner.connect() as c:
        if not c.execute(text("SELECT count(*) FROM iam.users")).scalar():
            pytest.skip("no users")
    with runtime.connect() as c:
        for table in ("iam.users", "iam.memberships", "iam.properties", "iam.organizations",
                      "iam.audit_events", "iam.user_credentials"):
            assert c.execute(text(f"SELECT count(*) FROM {table}")).scalar() == 0, table
        # The catalogue of permissions is not tenant data.
        assert c.execute(text("SELECT count(*) FROM iam.permissions")).scalar() > 0


def test_a_tenant_sees_only_its_organisation_and_members(runtime, owner):
    from chirala_common.db import bind_tenant_context

    with owner.connect() as c:
        orgs = [str(o) for (o,) in c.execute(text("SELECT id FROM iam.organizations"))]
        expected = {str(o): (n, u) for o, n, u in c.execute(text(
            "SELECT o.id, (SELECT count(*) FROM iam.properties p WHERE p.organization_id = o.id), "
            "(SELECT count(DISTINCT m.user_id) FROM iam.memberships m WHERE m.organization_id = o.id) "
            "FROM iam.organizations o"))}
    if len(orgs) < 2:
        pytest.skip("need two organisations")
    for org in orgs:
        with _session(runtime) as s:
            bind_tenant_context(s, organization_id=org)
            props, orgs_seen, users = s.execute(text(
                "SELECT (SELECT count(*) FROM iam.properties), "
                "(SELECT count(*) FROM iam.organizations), "
                "(SELECT count(*) FROM iam.users)")).one()
            assert props == expected[org][0], (org, props)
            assert orgs_seen == 1, (org, orgs_seen)
            assert users == expected[org][1], (org, users)
            s.rollback()


def test_identity_lookup_sees_one_subject_only(runtime, owner):
    from chirala_common.db import identity_context

    with owner.connect() as c:
        row = c.execute(text(
            "SELECT u.subject_id, u.id, m.organization_id FROM iam.users u "
            "JOIN iam.memberships m ON m.user_id = u.id AND m.status = 'active' "
            "WHERE u.status = 'active' LIMIT 1")).first()
        total_users = c.execute(text("SELECT count(*) FROM iam.users")).scalar()
    if row is None:
        pytest.skip("no active member")
    with _session(runtime) as s:
        identity_context(s, subject=row.subject_id)
        users = s.execute(text("SELECT id FROM iam.users")).scalars().all()
        assert users == [row.id], f"identity lookup saw {len(users)} of {total_users} users"
        orgs = s.execute(text("SELECT DISTINCT organization_id FROM iam.memberships")).scalars().all()
        assert str(row.organization_id) in {str(o) for o in orgs}
        assert s.execute(text("SELECT count(*) FROM iam.audit_events")).scalar() == 0
        assert s.execute(text("SELECT count(*) FROM iam.user_credentials WHERE user_id <> :u"),
                         {"u": row.id}).scalar() == 0
        s.rollback()


def test_property_code_lookup_sees_one_property(runtime, owner):
    from chirala_common.db import property_code_context

    with owner.connect() as c:
        code = c.execute(text("SELECT code FROM iam.properties WHERE code IS NOT NULL LIMIT 1")).scalar()
    if not code:
        pytest.skip("no property codes")
    with _session(runtime) as s:
        property_code_context(s, code=code)
        codes = s.execute(text("SELECT code FROM iam.properties")).scalars().all()
        assert codes == [code]
        assert s.execute(text("SELECT count(*) FROM iam.users")).scalar() == 0
        s.rollback()


def test_writing_into_another_organisation_is_refused(runtime, owner):
    from chirala_common.db import bind_tenant_context

    with owner.connect() as c:
        orgs = [str(o) for (o,) in c.execute(text("SELECT id FROM iam.organizations LIMIT 2"))]
    if len(orgs) < 2:
        pytest.skip("need two organisations")
    mine, theirs = orgs
    with _session(runtime) as s:
        bind_tenant_context(s, organization_id=mine)
        with pytest.raises(DBAPIError):
            s.execute(text("INSERT INTO iam.roles (id, organization_id, name, code) "
                           "VALUES (:i, :o, 'should never land', 'ZZ_QA')"),
                      {"i": uuid.uuid4(), "o": theirs})
        s.rollback()
    with _session(runtime) as s:
        bind_tenant_context(s, organization_id=mine)
        changed = s.execute(text("UPDATE iam.memberships SET status = status "
                                 "WHERE organization_id = :o"), {"o": theirs}).rowcount
        assert changed == 0
        s.rollback()


def test_system_context_sees_everyone(runtime, owner):
    from chirala_common.db import system_context

    with owner.connect() as c:
        total = c.execute(text("SELECT count(*) FROM iam.users")).scalar()
    with _session(runtime) as s:
        system_context(s, reason="test: every user")
        assert s.execute(text("SELECT count(*) FROM iam.users")).scalar() == total
        s.rollback()
