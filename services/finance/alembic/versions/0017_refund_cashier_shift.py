"""Tie a cash refund to the drawer it came out of.

A shift's expected cash was the opening float plus the cash it took in. Money
handed back was not subtracted, because a refund carried no shift — so a guest
refunded in cash left the cashier declaring less than expected and showing a
shortage they did not cause. With three shifts a day the shortage lands on
whoever happens to be counting.

Nullable: a card or UPI refund never touches a drawer, and a refund taken
outside any open shift has no drawer to name.

Revision ID: 0017_refund_cashier_shift
Revises: 0016_night_audit_settings
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0017_refund_cashier_shift"
down_revision = "0016_night_audit_settings"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "refunds",
        sa.Column("cashier_shift_id", sa.dialects.postgresql.UUID(as_uuid=True),
                  nullable=True),
        schema="finance",
    )
    op.add_column(
        "refunds",
        sa.Column("method", sa.String(length=30), nullable=True),
        schema="finance",
    )
    op.create_index(
        "ix_refund_cashier_shift", "refunds", ["cashier_shift_id"],
        unique=False, schema="finance",
        postgresql_where=sa.text("cashier_shift_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_refund_cashier_shift", table_name="refunds",
                  schema="finance")
    op.drop_column("refunds", "method", schema="finance")
    op.drop_column("refunds", "cashier_shift_id", schema="finance")
