"""Platform access as composable capabilities, not one broad flag

Revision ID: 0026_platform_capabilities
Revises: 0025_iam_rls
Create Date: 2026-09-14

0024 gave the platform its own tier, and gave every member of it everything.
``iam.platform_admins`` is a single active/revoked row: anyone in it can
suspend a tenant, create a permission, revoke any session and read every audit
entry. That was the right shape for establishing the boundary and the wrong
one to keep, because it cannot express any of the five staff roles the product
actually needs -- a billing admin with no integration secrets, a support agent
with no power to deactivate an account, an operator who cannot approve their
own restore request. Separation of duties is not a policy you can write down
if the system has only one level.

So a role here is a set of named capabilities, and a staff member holds roles.
``platform_admins`` keeps its job -- it says *whether* somebody is platform
staff at all, and remains the single row that grants or revokes that -- while
what they may then do comes from the roles assigned to it.

Four tables rather than three because the assignment is many-to-many: an
onboarding specialist who also covers support holds both roles and the union
of their capabilities, instead of somebody inventing a sixth role for the
overlap.

**Capabilities are seeded, roles are seeded, grants are seeded.** Unlike the
tenant permission catalogue -- where a route once shipped gated on a
permission nobody had created, and every tenant got 403 on a button they could
see -- the platform catalogue and the code that reads it land together. The
seed below names exactly the capabilities the 22 platform routes ask for.

Existing platform administrators are backfilled to ``platform_owner``. There is
no other safe default: they hold unrestricted access today, and quietly
narrowing it during a migration would lock the only operators out of the
console that grants access.

Row-level security follows 0025's scheme. The catalogue tables are readable by
anyone and writable only in system context, like ``iam.permissions`` -- they
are not tenant data. Assignments are readable in system context or by the
administrator they belong to, which is what lets ``require_capability``
resolve a caller's capabilities under identity context, before it elevates.
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0026_platform_capabilities"
down_revision: str | None = "0025_iam_rls"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_SYSTEM = "tenancy.system_context()"
_OWN_ASSIGNMENT = (
    "(tenancy.system_context() OR EXISTS ("
    "SELECT 1 FROM iam.platform_admins pa "
    "WHERE pa.id = platform_admin_id "
    "AND pa.user_id = tenancy.identity_user_id()))"
)

#: code -> what holding it lets somebody do. One per distinct authority the
#: console exposes, named for the action rather than the endpoint, so a screen
#: split into two routes does not become two capabilities.
CAPABILITIES: list[tuple[str, str]] = [
    ("tenant.view", "See tenant accounts, their properties, people and usage"),
    ("tenant.create", "Create a tenant account and invite its owner"),
    ("tenant.lifecycle", "Rename, suspend and reactivate a tenant"),
    ("property.view", "See properties across tenants and their entitlements"),
    ("property.lifecycle", "Suspend and reactivate a property"),
    ("property.entitlements", "Enable and disable modules for a property"),
    ("user.view", "Find a person across tenants and see their memberships"),
    ("user.lifecycle", "Deactivate and restore a person platform-wide"),
    ("user.sessions", "Revoke a person's active sessions"),
    ("catalog.view", "See the global permission catalogue"),
    ("catalog.manage", "Add permissions to the global catalogue"),
    ("audit.view", "Read the cross-tenant audit trail"),
    ("operations.view", "See platform health, migrations and business dates"),
    ("staff.view", "See platform staff and the roles they hold"),
    ("staff.manage", "Grant and revoke platform access and roles"),
]

#: The five roles from the specification, with the restrictions it states.
#: Deliberately narrow: a role gets what its stated responsibilities need and
#: nothing adjacent, because the whole point of this migration is that
#: "platform staff" stops meaning "everything".
ROLES: list[tuple[str, str, str, tuple[str, ...]]] = [
    (
        "platform_owner",
        "Platform owner",
        "Platform staff, roles, catalogue, tenant lifecycle and approvals.",
        tuple(c for c, _ in CAPABILITIES),
    ),
    (
        "billing_admin",
        "Billing admin",
        "Software plans, subscriptions, invoices and approved credits. "
        "No guest folios, guest card data or integration secrets.",
        ("tenant.view", "property.view", "audit.view", "staff.view"),
    ),
    (
        "support_agent",
        "Support agent",
        "Tickets, tenant metadata and permitted diagnostics. No automatic "
        "guest access; temporary access must be requested and approved.",
        ("tenant.view", "property.view", "user.view", "operations.view",
         "audit.view"),
    ),
    (
        "platform_operator",
        "Platform operator",
        "Service health, queues, provider configuration and recovery "
        "requests. No routine access to guest records.",
        ("operations.view", "property.view", "user.sessions", "audit.view",
         "staff.view"),
    ),
    (
        "onboarding_specialist",
        "Onboarding specialist",
        "Tenant setup, owner invitation and configuration guidance. No "
        "platform roles, production credentials or invoice adjustments.",
        ("tenant.view", "tenant.create", "property.view",
         "property.entitlements"),
    ),
]


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS iam.platform_permissions (
            id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            code        varchar(60) NOT NULL UNIQUE,
            description varchar(300) NOT NULL,
            created_at  timestamptz NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS iam.platform_roles (
            id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            code        varchar(60) NOT NULL UNIQUE,
            name        varchar(120) NOT NULL,
            description varchar(400) NOT NULL DEFAULT '',
            -- A seeded role. Its capabilities may be edited; it may not be
            -- deleted, because the backfill and the docs both name it.
            is_system   boolean NOT NULL DEFAULT false,
            created_at  timestamptz NOT NULL DEFAULT now(),
            updated_at  timestamptz NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS iam.platform_role_permissions (
            role_id       uuid NOT NULL REFERENCES iam.platform_roles(id)
                            ON DELETE CASCADE,
            permission_id uuid NOT NULL
                            REFERENCES iam.platform_permissions(id),
            PRIMARY KEY (role_id, permission_id)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS iam.platform_role_assignments (
            id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            platform_admin_id uuid NOT NULL
                                REFERENCES iam.platform_admins(id)
                                ON DELETE CASCADE,
            role_id           uuid NOT NULL REFERENCES iam.platform_roles(id),
            granted_by        uuid NULL REFERENCES iam.users(id),
            granted_at        timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT uq_platform_role_assignment
                UNIQUE (platform_admin_id, role_id)
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_platform_role_assignments_admin "
        "ON iam.platform_role_assignments (platform_admin_id)"
    )

    for code, description in CAPABILITIES:
        op.execute(
            "INSERT INTO iam.platform_permissions (code, description) "
            f"VALUES ('{code}', '{description.replace(chr(39), chr(39) * 2)}') "
            "ON CONFLICT (code) DO UPDATE SET description = EXCLUDED.description"
        )

    for code, name, description, caps in ROLES:
        op.execute(
            "INSERT INTO iam.platform_roles (code, name, description, is_system) "
            f"VALUES ('{code}', '{name}', "
            f"'{description.replace(chr(39), chr(39) * 2)}', true) "
            "ON CONFLICT (code) DO UPDATE SET name = EXCLUDED.name, "
            "description = EXCLUDED.description, updated_at = now()"
        )
        caps_sql = ", ".join(f"'{c}'" for c in caps)
        op.execute(
            f"""
            INSERT INTO iam.platform_role_permissions (role_id, permission_id)
            SELECT r.id, p.id
            FROM iam.platform_roles r
            JOIN iam.platform_permissions p ON p.code IN ({caps_sql})
            WHERE r.code = '{code}'
            ON CONFLICT DO NOTHING
            """
        )

    # Everyone who already holds platform access holds all of it. Narrowing
    # them here would lock the only operators out of the console that grants
    # access back.
    op.execute(
        """
        INSERT INTO iam.platform_role_assignments (platform_admin_id, role_id)
        SELECT pa.id, r.id
        FROM iam.platform_admins pa
        JOIN iam.platform_roles r ON r.code = 'platform_owner'
        WHERE pa.status = 'active'
        ON CONFLICT (platform_admin_id, role_id) DO NOTHING
        """
    )

    # --- row-level security, following 0025 -------------------------------
    policies = {
        "platform_permissions": ("true", _SYSTEM),
        "platform_roles": ("true", _SYSTEM),
        "platform_role_permissions": ("true", _SYSTEM),
        "platform_role_assignments": (_OWN_ASSIGNMENT, _SYSTEM),
    }
    for table, (read, write) in policies.items():
        op.execute(f"ALTER TABLE iam.{table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE iam.{table} FORCE ROW LEVEL SECURITY")
        for name in ("tenant_read", "tenant_insert", "tenant_update",
                     "tenant_delete"):
            op.execute(f"DROP POLICY IF EXISTS {name} ON iam.{table}")
        op.execute(
            f"CREATE POLICY tenant_read ON iam.{table} FOR SELECT USING ({read})")
        op.execute(
            f"CREATE POLICY tenant_insert ON iam.{table} FOR INSERT "
            f"WITH CHECK ({write})")
        op.execute(
            f"CREATE POLICY tenant_update ON iam.{table} FOR UPDATE "
            f"USING ({write}) WITH CHECK ({write})")
        op.execute(
            f"CREATE POLICY tenant_delete ON iam.{table} FOR DELETE "
            f"USING ({write})")

    op.execute(
        """
        DO $$
        BEGIN
          IF EXISTS (SELECT FROM pg_roles WHERE rolname = 'pms_app') THEN
            GRANT SELECT, INSERT, UPDATE, DELETE ON
                iam.platform_permissions, iam.platform_roles,
                iam.platform_role_permissions, iam.platform_role_assignments
                TO pms_app;
          END IF;
        END
        $$
        """
    )


def downgrade() -> None:
    for table in ("platform_role_assignments", "platform_role_permissions",
                  "platform_roles", "platform_permissions"):
        op.execute(f"DROP TABLE IF EXISTS iam.{table} CASCADE")
