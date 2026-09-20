"""Record what each night-audit step actually did.

A step row said only that it ran and whether it failed. That answers "did the
audit complete", but never "what did it do" -- how many rooms were billed, how
much, how many no-shows it found, how far it pushed the booking horizon. That
is exactly what someone asks when they come back to a run days later, so the
numbers belong with the run rather than in a log nobody kept.

Revision ID: 0014_night_audit_detail
Revises: 0013_gst_registered
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0014_night_audit_detail"
down_revision = "0013_gst_registered"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "night_audit_steps",
        sa.Column("detail", postgresql.JSONB(astext_type=sa.Text()),
                  nullable=True),
        schema="finance",
    )


def downgrade() -> None:
    op.drop_column("night_audit_steps", "detail", schema="finance")
