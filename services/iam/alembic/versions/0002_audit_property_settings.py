"""Audit events + property settings fields

Revision ID: 0002_audit_property_settings
Revises: 0001_iam_core
Create Date: 2026-09-06

Adds iam.audit_events (append-only audit trail, §10) and an address column on
iam.properties for Property Settings (US-026). Supports the Administration
security/exceptions acceptance criteria: actor, property, before/after, reason,
correlation id captured for sensitive actions.
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0002_audit_property_settings"
down_revision: str | None = "0001_iam_core"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TABLE iam.properties ADD COLUMN IF NOT EXISTS address varchar(400)")

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS iam.audit_events (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            organization_id uuid,
            property_id uuid,
            actor_subject varchar(255),
            action varchar(100) NOT NULL,
            entity_type varchar(80) NOT NULL,
            entity_id varchar(80),
            redacted_before jsonb,
            redacted_after jsonb,
            reason varchar(400),
            correlation_id varchar(80),
            occurred_at timestamptz NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_audit_property_time "
        "ON iam.audit_events(property_id, occurred_at)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_audit_entity "
        "ON iam.audit_events(entity_type, entity_id)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS iam.audit_events CASCADE")
    op.execute("ALTER TABLE iam.properties DROP COLUMN IF EXISTS address")
