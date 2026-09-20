"""Record who closed the business day.

A run said when a day was sealed and never by whom. That was tolerable while
closing was routine; it stopped being so once the audit could be closed *over*
an open cashier till. The run already records whose drawer was left uncounted —
it needs to record who decided to close over it, or the exception names a
cashier and protects nobody.

``run_by`` is nullable on purpose: NULL means the scheduler ran it unattended,
which is a real and different answer from "a person did this".

Revision ID: 0015_night_audit_actor
Revises: 0014_night_audit_detail
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0015_night_audit_actor"
down_revision = "0014_night_audit_detail"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "night_audit_runs",
        sa.Column("run_by", sa.dialects.postgresql.UUID(as_uuid=True),
                  nullable=True),
        schema="finance",
    )


def downgrade() -> None:
    op.drop_column("night_audit_runs", "run_by", schema="finance")
