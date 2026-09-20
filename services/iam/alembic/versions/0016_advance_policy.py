"""How much of a stay must be paid up front.

Onboarding step 5 asks for it as "25%" or "a fixed amount", and it is a
property-wide rule rather than something decided per booking, so it lives
beside the check-in and check-out times it is quoted with.

Stored as a kind and a value rather than two nullable columns, because
"25 percent" and "2500 rupees" are the same fact in different units and two
columns invites both being set.

Revision ID: 0016_advance_policy
Revises: 0015_property_profile
"""
import sqlalchemy as sa
from alembic import op

revision = "0016_advance_policy"
down_revision = "0015_property_profile"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("properties",
                  sa.Column("advance_kind", sa.String(length=16), nullable=True),
                  schema="iam")
    op.add_column("properties",
                  sa.Column("advance_value", sa.Numeric(12, 2), nullable=True),
                  schema="iam")
    op.create_check_constraint(
        "ck_advance_policy", "properties",
        "(advance_kind IS NULL AND advance_value IS NULL) OR ("
        " advance_kind IN ('percent', 'fixed') AND advance_value >= 0"
        " AND (advance_kind <> 'percent' OR advance_value <= 100))",
        schema="iam",
    )


def downgrade() -> None:
    op.drop_constraint("ck_advance_policy", "properties", schema="iam",
                       type_="check")
    op.drop_column("properties", "advance_value", schema="iam")
    op.drop_column("properties", "advance_kind", schema="iam")
