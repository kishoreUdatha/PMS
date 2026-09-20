"""Row-level security on booking, property, operations, guest and channel data

Revision ID: 0047_tenant_rls
Revises: 0046_work_orders
Create Date: 2026-09-14

Stage 3 of tenant isolation in the database (schema blueprint §12), after
finance (finance 0028). Every table this service owns -- reservations and
holds, rooms and rates, housekeeping and work orders, guests and their
documents, channel-manager links and bookings -- now answers only to the tenant
the transaction was bound to by ``chirala_common.db.bind_tenant_context``.
With no tenant bound nothing is visible; ``system_context`` sees every tenant
and is reserved for work that is above the tenant boundary by nature (the hold
reaper, the channel sweeps' listing step, resolving which property a channel
booking belongs to).

**Tables without an organisation column** are secured through their parent:
the property link tables (room photos, amenities, rate-plan and package
membership) through the property they name, and the channel room and rate
mappings through their channel-manager link.

**No views, materialized views or SECURITY DEFINER functions exist** in these
schemas, checked when this was written; a view owned by the schema owner would
run with the owner's rights and read straight past these policies. The trigger
that numbers guests runs as the caller and is bound like any other write.

The tenancy functions are the same ones finance 0028 creates; both migrations
replace them under one advisory lock, so services migrating at the same moment
on a fresh database do not collide.
"""

from __future__ import annotations

from alembic import op

revision = "0047_tenant_rls"
down_revision = "0046_work_orders"
branch_labels = None
depends_on = None

RUNTIME_ROLE = "pms_app"
SCHEMAS = ("booking", "property", "operations", "engagement", "distribution")

_VIA_PROPERTY = ("EXISTS (SELECT 1 FROM iam.properties p WHERE p.id = property_id"
                 " AND tenancy.org_visible(p.organization_id))")
_VIA_LINK = ("EXISTS (SELECT 1 FROM distribution.channel_manager_links l"
             " WHERE l.id = link_id)")

CHILD_POLICIES = {
    ("property", "package_inclusions"): _VIA_PROPERTY,
    ("property", "package_room_types"): _VIA_PROPERTY,
    ("property", "rate_plan_room_types"): _VIA_PROPERTY,
    ("property", "rate_rule_rate_plans"): _VIA_PROPERTY,
    ("property", "rate_rule_room_types"): _VIA_PROPERTY,
    ("property", "room_amenities"): _VIA_PROPERTY,
    ("property", "room_photos"): _VIA_PROPERTY,
    ("property", "room_type_amenities"): _VIA_PROPERTY,
    ("property", "room_type_photos"): _VIA_PROPERTY,
    ("distribution", "channel_room_mappings"): _VIA_LINK,
    ("distribution", "channel_rate_mappings"): _VIA_LINK,
}

FUNCTIONS = """
SELECT pg_advisory_xact_lock(hashtext('chirala:tenancy-functions'));

CREATE SCHEMA IF NOT EXISTS tenancy;

CREATE OR REPLACE FUNCTION tenancy.system_context() RETURNS boolean
LANGUAGE sql STABLE AS $$
    SELECT coalesce(current_setting('app.system', true), '') = 'on'
$$;

CREATE OR REPLACE FUNCTION tenancy.org_visible(org uuid) RETURNS boolean
LANGUAGE sql STABLE AS $$
    SELECT tenancy.system_context()
        OR (org IS NOT NULL
            AND org::text = nullif(current_setting('app.organization_id', true), ''))
$$;
"""

_SCHEMA_LIST = ", ".join(f"'{s}'" for s in SCHEMAS)


def _tables(with_org: bool) -> list[tuple[str, str]]:
    condition = "AND EXISTS" if with_org else "AND NOT EXISTS"
    rows = op.get_bind().exec_driver_sql(
        f"""
        SELECT n.nspname, c.relname
        FROM pg_class c
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname IN ({_SCHEMA_LIST}) AND c.relkind = 'r'
          AND c.relname <> 'alembic_version'
          {condition} (SELECT 1 FROM pg_attribute a
                      WHERE a.attrelid = c.oid AND a.attname = 'organization_id'
                        AND NOT a.attisdropped)
        ORDER BY 1, 2
        """
    ).fetchall()
    return [(r[0], r[1]) for r in rows]


def _secure(schema: str, table: str, expression: str) -> None:
    name = f"{schema}.{table}"
    op.execute(f"ALTER TABLE {name} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {name} FORCE ROW LEVEL SECURITY")
    op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {name}")
    op.execute(f"CREATE POLICY tenant_isolation ON {name} "
               f"USING ({expression}) WITH CHECK ({expression})")


def upgrade() -> None:
    views = op.get_bind().exec_driver_sql(
        f"SELECT schemaname || '.' || viewname FROM pg_views WHERE schemaname IN ({_SCHEMA_LIST}) "
        f"UNION ALL SELECT schemaname || '.' || matviewname FROM pg_matviews "
        f"WHERE schemaname IN ({_SCHEMA_LIST})"
    ).fetchall()
    if views:
        # A view owned by the schema owner reads past row security. Refuse to
        # call these schemas secured while one exists.
        raise RuntimeError(f"views would bypass row security: {[v[0] for v in views]}")

    op.execute(FUNCTIONS)
    op.execute(f"""
        DO $$
        BEGIN
          IF EXISTS (SELECT FROM pg_roles WHERE rolname = '{RUNTIME_ROLE}') THEN
            GRANT USAGE ON SCHEMA tenancy TO {RUNTIME_ROLE};
            GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA tenancy TO {RUNTIME_ROLE};
          END IF;
        END
        $$
    """)

    for schema, table in _tables(with_org=True):
        _secure(schema, table, "tenancy.org_visible(organization_id)")
    without_org = set(_tables(with_org=False))
    unknown = sorted(without_org - set(CHILD_POLICIES))
    if unknown:
        raise RuntimeError(f"tables with no organisation column and no parent policy: {unknown}")
    for (schema, table), expression in CHILD_POLICIES.items():
        if (schema, table) in without_org:
            _secure(schema, table, expression)


def downgrade() -> None:
    for schema, table in _tables(with_org=True) + _tables(with_org=False):
        name = f"{schema}.{table}"
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {name}")
        op.execute(f"ALTER TABLE {name} NO FORCE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {name} DISABLE ROW LEVEL SECURITY")
