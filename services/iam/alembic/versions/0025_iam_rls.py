"""Row-level security on identity, access and audit data

Revision ID: 0025_iam_rls
Revises: 0024_platform_admins
Create Date: 2026-09-14

Stage 4, the last, of tenant isolation in the database (schema blueprint §12).
Finance (0028) and booking-core (0047) are already bound. What remained is the
identity layer itself: organisations and properties, users and their
memberships, roles and grants, invitations, approvals and the audit trail.

This stage is different because identity is read *before* a tenant is known:
a request has to find out who is calling before it can know whose data they
may touch. Three narrow ways in exist for that, besides the tenant binding and
system context the earlier stages introduced:

* **Identity** (``app.identity_subject``, via ``identity_context``): while a
  caller is being resolved, a transaction may read that one subject's own user
  row, memberships, and the organisations, properties and roles those
  memberships belong to -- nothing else. It is replaced by the caller's tenant
  the moment the lookup is done.
* **Property code** (``app.property_code``, via ``property_code_context``): the
  public booking engine may read the one property whose code is in its URL,
  and that property's module entitlements.
* **Sign-in and platform** run in system context, named: they are the identity
  provider and the operator console, above the tenant boundary by nature.

**Users are global identities**, not tenant rows: a user is visible to a
tenant that holds a membership for them, to themselves, and during their own
identity lookup. Creating one is allowed to any bound transaction, because
inviting a person creates their user before their membership.

``tenancy.identity_user_id()`` is ``SECURITY DEFINER``, owned by the schema
owner, with a pinned search path and no execute grant to PUBLIC. It returns
exactly one id -- the user whose subject the transaction is resolving -- and is
what lets the users and memberships policies refer to each other without
recursing.

The permission catalogue is not tenant data: readable by anyone, changed only in
system context. Email verification codes belong to nobody yet and are system
only. The audit log accepts rows with no organisation (platform actions) from
any bound transaction but shows each tenant only its own.
"""

from __future__ import annotations

from alembic import op

revision = "0025_iam_rls"
down_revision = "0024_platform_admins"
branch_labels = None
depends_on = None

RUNTIME_ROLE = "pms_app"

_ORG = "tenancy.org_visible(organization_id)"
_IDENTITY_USER = "tenancy.identity_user_id()"
_BOUND = ("(tenancy.system_context() OR "
          "nullif(current_setting('app.organization_id', true), '') IS NOT NULL)")
_SYSTEM = "tenancy.system_context()"
_IDENTITY_ORG = ("EXISTS (SELECT 1 FROM iam.memberships m WHERE m.organization_id = {col} "
                 "AND m.user_id = tenancy.identity_user_id())")
_USER_VISIBLE = ("(tenancy.system_context() "
                 "OR id = tenancy.identity_user_id() "
                 "OR id::text = nullif(current_setting('app.user_id', true), '') "
                 "OR EXISTS (SELECT 1 FROM iam.memberships m WHERE m.user_id = iam.users.id))")

#: table -> {command: expression}. ``read`` is USING for SELECT; ``write`` is
#: USING and WITH CHECK for UPDATE and DELETE and WITH CHECK for INSERT unless
#: ``insert`` says otherwise.
POLICIES: dict[str, dict[str, str]] = {
    # --- tenant rows ------------------------------------------------------
    "approval_policies": {"read": _ORG, "write": _ORG},
    "approval_requests": {"read": _ORG, "write": _ORG},
    "departments": {"read": _ORG, "write": _ORG},
    "employees": {"read": _ORG, "write": _ORG},
    "invitations": {"read": _ORG, "write": _ORG},
    "login_sessions": {"read": _ORG, "write": _ORG},
    "property_onboarding": {"read": _ORG, "write": _ORG},
    "audit_events": {"read": _ORG, "write": _ORG,
                     "insert": f"(organization_id IS NULL OR {_ORG})"},
    "roles": {"read": f"({_ORG} OR {_IDENTITY_ORG.format(col='iam.roles.organization_id')})",
              "write": _ORG},
    "memberships": {"read": f"({_ORG} OR user_id = {_IDENTITY_USER})", "write": _ORG},
    "organizations": {"read": f"(tenancy.org_visible(id) OR {_IDENTITY_ORG.format(col='iam.organizations.id')})",
                      "write": "tenancy.org_visible(id)"},
    "properties": {"read": (f"({_ORG} "
                            "OR code = nullif(current_setting('app.property_code', true), '') "
                            f"OR {_IDENTITY_ORG.format(col='iam.properties.organization_id')})"),
                   "write": _ORG},
    # --- reached through a parent -----------------------------------------
    "property_modules": {"read": "EXISTS (SELECT 1 FROM iam.properties p WHERE p.id = property_id)",
                         "write": "EXISTS (SELECT 1 FROM iam.properties p WHERE p.id = property_id)"},
    "role_assignments": {"read": "EXISTS (SELECT 1 FROM iam.memberships m WHERE m.id = membership_id)",
                         "write": "EXISTS (SELECT 1 FROM iam.memberships m WHERE m.id = membership_id)"},
    "role_permissions": {"read": "EXISTS (SELECT 1 FROM iam.roles r WHERE r.id = role_id)",
                         "write": "EXISTS (SELECT 1 FROM iam.roles r WHERE r.id = role_id)"},
    "invitation_grants": {"read": "EXISTS (SELECT 1 FROM iam.invitations i WHERE i.id = invitation_id)",
                          "write": "EXISTS (SELECT 1 FROM iam.invitations i WHERE i.id = invitation_id)"},
    "approval_decisions": {"read": "EXISTS (SELECT 1 FROM iam.approval_requests r WHERE r.id = request_id)",
                           "write": "EXISTS (SELECT 1 FROM iam.approval_requests r WHERE r.id = request_id)"},
    # --- identities ---------------------------------------------------------
    "users": {"read": _USER_VISIBLE, "write": _USER_VISIBLE, "insert": _BOUND},
    "user_credentials": {"read": "EXISTS (SELECT 1 FROM iam.users u WHERE u.id = user_id)",
                         "write": "EXISTS (SELECT 1 FROM iam.users u WHERE u.id = user_id)",
                         "insert": _BOUND},
    "password_tokens": {"read": "EXISTS (SELECT 1 FROM iam.users u WHERE u.id = user_id)",
                        "write": "EXISTS (SELECT 1 FROM iam.users u WHERE u.id = user_id)",
                        "insert": _BOUND},
    # --- above every tenant -------------------------------------------------
    # Readable for its own row during identity lookup: a session has to know
    # whether its user may open the operator console.
    "platform_admins": {"read": f"({_SYSTEM} OR user_id = {_IDENTITY_USER})", "write": _SYSTEM},
    "email_verifications": {"read": _SYSTEM, "write": _SYSTEM},
    "permissions": {"read": "true", "write": _SYSTEM},
}

FUNCTIONS = f"""
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

CREATE OR REPLACE FUNCTION tenancy.identity_user_id() RETURNS uuid
LANGUAGE sql STABLE SECURITY DEFINER
SET search_path = pg_catalog, pg_temp AS $$
    SELECT u.id FROM iam.users u
    WHERE u.subject_id = nullif(current_setting('app.identity_subject', true), '')
      AND u.status = 'active'
    LIMIT 1
$$;
REVOKE ALL ON FUNCTION tenancy.identity_user_id() FROM PUBLIC;
"""


def _tables() -> list[str]:
    return [r[0] for r in op.get_bind().exec_driver_sql(
        "SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
        "WHERE n.nspname = 'iam' AND c.relkind = 'r' AND c.relname <> 'alembic_version' "
        "ORDER BY 1").fetchall()]


def _drop_policies(table: str) -> None:
    for name in ("tenant_isolation", "tenant_read", "tenant_insert",
                 "tenant_update", "tenant_delete"):
        op.execute(f"DROP POLICY IF EXISTS {name} ON iam.{table}")


def upgrade() -> None:
    views = op.get_bind().exec_driver_sql(
        "SELECT viewname FROM pg_views WHERE schemaname = 'iam' "
        "UNION ALL SELECT matviewname FROM pg_matviews WHERE schemaname = 'iam'").fetchall()
    if views:
        raise RuntimeError(f"iam views would bypass row security: {[v[0] for v in views]}")

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

    tables = _tables()
    missing = sorted(set(tables) - set(POLICIES))
    if missing:
        raise RuntimeError(f"iam tables with no tenant policy: {missing}")

    for table in tables:
        p = POLICIES[table]
        write = p["write"]
        insert = p.get("insert", write)
        op.execute(f"ALTER TABLE iam.{table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE iam.{table} FORCE ROW LEVEL SECURITY")
        _drop_policies(table)
        op.execute(f"CREATE POLICY tenant_read ON iam.{table} FOR SELECT USING ({p['read']})")
        op.execute(f"CREATE POLICY tenant_insert ON iam.{table} FOR INSERT WITH CHECK ({insert})")
        op.execute(f"CREATE POLICY tenant_update ON iam.{table} FOR UPDATE "
                   f"USING ({write}) WITH CHECK ({write})")
        op.execute(f"CREATE POLICY tenant_delete ON iam.{table} FOR DELETE USING ({write})")


def downgrade() -> None:
    for table in _tables():
        _drop_policies(table)
        op.execute(f"ALTER TABLE iam.{table} NO FORCE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE iam.{table} DISABLE ROW LEVEL SECURITY")
    op.execute("DROP FUNCTION IF EXISTS tenancy.identity_user_id()")
