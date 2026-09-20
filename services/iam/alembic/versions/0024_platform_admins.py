"""A platform tier above the tenant boundary, in its own table

Revision ID: 0024_platform_admins
Revises: 0023_property_create_grant
Create Date: 2026-09-14

Until now the highest scope in this system was ``organization``. Every role is
owned by one (``iam.roles.organization_id`` is NOT NULL), every grant hangs off
a membership in one, and ``Caller.organization_id`` is resolved from that
membership. ``guard_request_tenancy`` then refuses any request naming a tenant
that is not the caller's -- which is the whole point of it, and which a
platform operator must stand outside of.

This is deliberately NOT a new ``scope_type`` on ``role_assignments``.

The permission query in authz.py reads

    ra.scope_type = 'organization' OR (:prop IS NOT NULL AND ra.property_id = :prop)

An organisation-scoped grant already matches *any* property_id, because the
grant is not tied to one; that is precisely the hole the tenancy guard was
added to close. Introducing ``scope_type = 'platform'`` into the same table
puts a row in it that is meant to match everything, and every query of the
shape above would then need re-auditing to make sure it does not accidentally
satisfy a tenant permission check. One missed OR and a platform grant silently
becomes a cross-tenant one.

A separate table cannot do that. Nothing joins it to role_assignments, so no
tenant permission check can be satisfied by a row here, no matter how it is
written. Platform routes get their own dependency and their own prefix, and
the tenant guard never learns an exception.

There is no seed. Nobody can be made a platform admin by a migration running
in an environment that does not know who its operators are -- the first row is
granted out of band (scripts/grant_platform_admin.py), by someone who already
holds database access, which is a higher privilege than this table confers.
Subsequent grants happen in-app and are audited.
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0024_platform_admins"
down_revision: str | None = "0023_property_create_grant"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS iam.platform_admins (
            id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            user_id     uuid NOT NULL REFERENCES iam.users(id),
            status      varchar(20) NOT NULL DEFAULT 'active',
            granted_by  uuid NULL REFERENCES iam.users(id),
            granted_at  timestamptz NOT NULL DEFAULT now(),
            revoked_at  timestamptz NULL,
            note        varchar(300) NULL,
            created_at  timestamptz NOT NULL DEFAULT now(),
            updated_at  timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT uq_platform_admin_user UNIQUE (user_id),
            CONSTRAINT ck_platform_admin_status
                CHECK (status IN ('active', 'revoked'))
        )
        """
    )
    # Every check is "is this caller active platform staff", so index the
    # answer rather than the table.
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_platform_admins_active
            ON iam.platform_admins (user_id) WHERE status = 'active'
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS iam.ix_platform_admins_active")
    op.execute("DROP TABLE IF EXISTS iam.platform_admins")
