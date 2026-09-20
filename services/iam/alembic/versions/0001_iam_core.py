"""IAM core: orgs, properties, modules, users, memberships, permissions, roles

Revision ID: 0001_iam_core
Revises:
Create Date: 2026-09-06

Creates the core §2 access tables in the ``iam`` schema. The ``iam`` schema and
``pgcrypto`` extension are created by infra/postgres/init/01_init.sql; guarded
here with IF NOT EXISTS for standalone runs.
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0001_iam_core"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("CREATE SCHEMA IF NOT EXISTS iam")
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")

    op.execute(
        """
        CREATE TABLE iam.organizations (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            name varchar(200) NOT NULL,
            status varchar(30) NOT NULL DEFAULT 'active',
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now(),
            version bigint NOT NULL DEFAULT 0
        )
        """
    )

    op.execute(
        """
        CREATE TABLE iam.properties (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            organization_id uuid NOT NULL REFERENCES iam.organizations(id),
            code varchar(30) NOT NULL,
            name varchar(200) NOT NULL,
            timezone varchar(64) NOT NULL,
            currency varchar(3) NOT NULL,
            checkin_time varchar(5),
            checkout_time varchar(5),
            status varchar(30) NOT NULL DEFAULT 'active',
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now(),
            version bigint NOT NULL DEFAULT 0,
            CONSTRAINT uq_property_org_code UNIQUE (organization_id, code),
            CONSTRAINT uq_property_org_id UNIQUE (organization_id, id)
        )
        """
    )

    op.execute(
        """
        CREATE TABLE iam.property_modules (
            property_id uuid NOT NULL REFERENCES iam.properties(id),
            module_code varchar(50) NOT NULL,
            enabled boolean NOT NULL DEFAULT false,
            enabled_at timestamptz,
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now(),
            PRIMARY KEY (property_id, module_code)
        )
        """
    )

    op.execute(
        """
        CREATE TABLE iam.users (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            identity_provider varchar(100) NOT NULL,
            subject_id varchar(255) NOT NULL,
            display_name varchar(200) NOT NULL,
            status varchar(30) NOT NULL DEFAULT 'active',
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now(),
            version bigint NOT NULL DEFAULT 0,
            CONSTRAINT uq_user_provider_subject
                UNIQUE (identity_provider, subject_id)
        )
        """
    )

    op.execute(
        """
        CREATE TABLE iam.memberships (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            organization_id uuid NOT NULL REFERENCES iam.organizations(id),
            user_id uuid NOT NULL REFERENCES iam.users(id),
            status varchar(30) NOT NULL DEFAULT 'active',
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now(),
            version bigint NOT NULL DEFAULT 0,
            CONSTRAINT uq_membership_org_user UNIQUE (organization_id, user_id),
            CONSTRAINT uq_membership_org_id UNIQUE (organization_id, id)
        )
        """
    )

    op.execute(
        """
        CREATE TABLE iam.permissions (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            resource_code varchar(100) NOT NULL,
            action_code varchar(100) NOT NULL,
            description varchar(300),
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT uq_permission_resource_action
                UNIQUE (resource_code, action_code)
        )
        """
    )

    op.execute(
        """
        CREATE TABLE iam.roles (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            organization_id uuid NOT NULL REFERENCES iam.organizations(id),
            code varchar(50) NOT NULL,
            name varchar(150) NOT NULL,
            template_code varchar(50),
            active boolean NOT NULL DEFAULT true,
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now(),
            version bigint NOT NULL DEFAULT 0,
            CONSTRAINT uq_role_org_code UNIQUE (organization_id, code),
            CONSTRAINT uq_role_org_id UNIQUE (organization_id, id)
        )
        """
    )

    op.execute(
        """
        CREATE TABLE iam.role_permissions (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            role_id uuid NOT NULL REFERENCES iam.roles(id),
            permission_id uuid NOT NULL REFERENCES iam.permissions(id),
            record_scope varchar(20) NOT NULL DEFAULT 'property',
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT uq_role_permission UNIQUE (role_id, permission_id),
            CONSTRAINT ck_role_permission_scope
                CHECK (record_scope IN ('property','assigned','own'))
        )
        """
    )

    op.execute(
        """
        CREATE TABLE iam.role_assignments (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            membership_id uuid NOT NULL REFERENCES iam.memberships(id),
            role_id uuid NOT NULL REFERENCES iam.roles(id),
            property_id uuid REFERENCES iam.properties(id),
            outlet_id uuid,
            scope_type varchar(20) NOT NULL,
            valid_from date,
            valid_until date,
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now(),
            version bigint NOT NULL DEFAULT 0,
            CONSTRAINT ck_role_assignment_scope
                CHECK (scope_type IN ('organization','property','outlet')),
            CONSTRAINT ck_role_assignment_property_required
                CHECK (
                    (scope_type = 'organization' AND property_id IS NULL)
                    OR (scope_type IN ('property','outlet') AND property_id IS NOT NULL)
                )
        )
        """
    )

    # FK-child indexes (§12).
    op.execute("CREATE INDEX ix_properties_org ON iam.properties(organization_id)")
    op.execute("CREATE INDEX ix_memberships_org ON iam.memberships(organization_id)")
    op.execute("CREATE INDEX ix_memberships_user ON iam.memberships(user_id)")
    op.execute("CREATE INDEX ix_roles_org ON iam.roles(organization_id)")
    op.execute("CREATE INDEX ix_role_perms_role ON iam.role_permissions(role_id)")
    op.execute(
        "CREATE INDEX ix_role_perms_perm ON iam.role_permissions(permission_id)"
    )
    op.execute(
        "CREATE INDEX ix_role_assign_membership "
        "ON iam.role_assignments(membership_id)"
    )
    op.execute("CREATE INDEX ix_role_assign_role ON iam.role_assignments(role_id)")


def downgrade() -> None:
    for table in (
        "role_assignments",
        "role_permissions",
        "roles",
        "permissions",
        "memberships",
        "users",
        "property_modules",
        "properties",
        "organizations",
    ):
        op.execute(f"DROP TABLE IF EXISTS iam.{table} CASCADE")
