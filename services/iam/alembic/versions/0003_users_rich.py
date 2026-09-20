"""Departments, employees, login sessions, user MFA + last login (US-039 full)

Revision ID: 0003_users_rich
Revises: 0002_audit_property_settings
Create Date: 2026-09-06

Adds the data the User Management screen (mockup 039) needs to be fully real:
- users.mfa_status + users.last_login_at
- iam.departments (org-scoped)
- iam.employees (employee_code + department per membership)
- iam.login_sessions (for last-login, active-session count, revoke)
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0003_users_rich"
down_revision: str | None = "0002_audit_property_settings"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE iam.users ADD COLUMN IF NOT EXISTS mfa_status varchar(20) "
        "NOT NULL DEFAULT 'disabled'"
    )
    op.execute(
        "ALTER TABLE iam.users ADD COLUMN IF NOT EXISTS last_login_at timestamptz"
    )

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS iam.departments (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            organization_id uuid NOT NULL,
            code varchar(30) NOT NULL,
            name varchar(120) NOT NULL,
            created_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT uq_department_org_code UNIQUE (organization_id, code)
        )
        """
    )

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS iam.employees (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            organization_id uuid NOT NULL,
            membership_id uuid NOT NULL REFERENCES iam.memberships(id),
            employee_code varchar(30) NOT NULL,
            department_id uuid REFERENCES iam.departments(id),
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT uq_employee_membership UNIQUE (membership_id),
            CONSTRAINT uq_employee_org_code UNIQUE (organization_id, employee_code)
        )
        """
    )

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS iam.login_sessions (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            user_id uuid NOT NULL REFERENCES iam.users(id),
            organization_id uuid,
            created_at timestamptz NOT NULL DEFAULT now(),
            expires_at timestamptz,
            revoked_at timestamptz,
            ip_address varchar(64),
            device varchar(200)
        )
        """
    )
    # Active sessions = not revoked and not expired. Partial index for lookups.
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_sessions_user_active "
        "ON iam.login_sessions(user_id) WHERE revoked_at IS NULL"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_employees_membership "
        "ON iam.employees(membership_id)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS iam.login_sessions CASCADE")
    op.execute("DROP TABLE IF EXISTS iam.employees CASCADE")
    op.execute("DROP TABLE IF EXISTS iam.departments CASCADE")
    op.execute("ALTER TABLE iam.users DROP COLUMN IF EXISTS last_login_at")
    op.execute("ALTER TABLE iam.users DROP COLUMN IF EXISTS mfa_status")
