"""What the night audit does with a guest who never arrived.

Until now the audit found no-shows and reported them, because charging a
penalty unattended means charging an amount nobody configured. With a basis
recorded against the property, it can act: the amount stops being a guess and
becomes a policy someone chose.

'first_night' charges one night's room rate plus tax, which is the common
default. 'none' finds and reports them exactly as before, for a property that
would rather a person made the call. NULL follows the deployment default, so
the answer lives in one place rather than being stamped onto every tenant.

Revision ID: 0018_no_show_policy
Revises: 0017_refund_cashier_shift
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0018_no_show_policy"
down_revision = "0017_refund_cashier_shift"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "night_audit_settings",
        sa.Column("no_show_penalty", sa.String(length=20), nullable=True),
        schema="finance",
    )
    op.create_check_constraint(
        "ck_no_show_penalty",
        "night_audit_settings",
        "no_show_penalty IS NULL OR no_show_penalty IN ('first_night', 'none')",
        schema="finance",
    )


def downgrade() -> None:
    op.drop_constraint("ck_no_show_penalty", "night_audit_settings",
                       schema="finance", type_="check")
    op.drop_column("night_audit_settings", "no_show_penalty", schema="finance")
