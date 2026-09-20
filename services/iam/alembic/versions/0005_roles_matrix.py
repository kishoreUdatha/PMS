"""Role scope/limits + module.action permission catalogue (US-041)

Revision ID: 0005_roles_matrix
Revises: 0004_invitations
Create Date: 2026-09-07

Adds record_scope / property_scope / max_discount / max_refund to roles, and
seeds the module x action permission catalogue the Roles & Permission Matrix
(mockup 041) toggles: 13 modules x 7 actions.
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0005_roles_matrix"
down_revision: str | None = "0004_invitations"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

MODULES = [
    "dashboard", "front_desk", "reservations", "rooms", "guests",
    "housekeeping", "rates", "distribution", "payments", "pos",
    "reports", "ai_center", "administration",
]
ACTIONS = ["view", "create", "edit", "cancel", "approve", "export", "configure"]


def upgrade() -> None:
    op.execute(
        "ALTER TABLE iam.roles ADD COLUMN IF NOT EXISTS record_scope "
        "varchar(30) NOT NULL DEFAULT 'assigned'"
    )
    op.execute(
        "ALTER TABLE iam.roles ADD COLUMN IF NOT EXISTS property_scope "
        "varchar(30) NOT NULL DEFAULT 'own'"
    )
    op.execute(
        "ALTER TABLE iam.roles ADD COLUMN IF NOT EXISTS max_discount numeric(19,4)"
    )
    op.execute(
        "ALTER TABLE iam.roles ADD COLUMN IF NOT EXISTS max_refund numeric(19,4)"
    )

    # Seed the module.action permission catalogue (idempotent).
    for module in MODULES:
        for action in ACTIONS:
            op.execute(
                f"""
                INSERT INTO iam.permissions (id, resource_code, action_code, description)
                VALUES (gen_random_uuid(), '{module}', '{action}',
                        '{module}.{action}')
                ON CONFLICT (resource_code, action_code) DO NOTHING
                """
            )

    # role.view / role.manage permissions for the Roles screen itself.
    for action in ("view", "manage"):
        op.execute(
            f"""
            INSERT INTO iam.permissions (id, resource_code, action_code, description)
            VALUES (gen_random_uuid(), 'role', '{action}', 'role.{action}')
            ON CONFLICT (resource_code, action_code) DO NOTHING
            """
        )


def downgrade() -> None:
    # Leave catalogue permissions (harmless); drop the added role columns.
    op.execute("ALTER TABLE iam.roles DROP COLUMN IF EXISTS max_refund")
    op.execute("ALTER TABLE iam.roles DROP COLUMN IF EXISTS max_discount")
    op.execute("ALTER TABLE iam.roles DROP COLUMN IF EXISTS property_scope")
    op.execute("ALTER TABLE iam.roles DROP COLUMN IF EXISTS record_scope")
