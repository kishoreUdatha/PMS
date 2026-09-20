"""What an extra adult or child costs, per room type.

A room type carried one price: the room. Onboarding asks for the two charges
that sit on top of it, and they belong on the type rather than on a booking
because they are a published tariff -- the same third guest costs the same in
every Deluxe room.

``extra_bed_charge`` already existed and is a different thing: a bed is
furniture, an extra adult is a person. A property can charge for one, the
other, or both.

Revision ID: 0029_extra_rates
Revises: 0028_guest_photo
"""
import sqlalchemy as sa
from alembic import op

revision = "0029_extra_rates"
down_revision = "0028_guest_photo"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for col in ("extra_adult_rate", "extra_child_rate"):
        op.add_column("room_types",
                      sa.Column(col, sa.Numeric(12, 2), nullable=True),
                      schema="property")
        op.create_check_constraint(
            f"ck_{col}_non_negative", "room_types",
            f"{col} IS NULL OR {col} >= 0", schema="property",
        )


def downgrade() -> None:
    for col in ("extra_adult_rate", "extra_child_rate"):
        op.drop_constraint(f"ck_{col}_non_negative", "room_types",
                           schema="property", type_="check")
        op.drop_column("room_types", col, schema="property")
