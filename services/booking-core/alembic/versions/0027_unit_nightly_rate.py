"""The rate a room was actually sold at

Revision ID: 0027_unit_rate
Revises: 0026_booking_attrs
Create Date: 2026-09-10

A booking recorded which room type, which board and how many people — and not
what the guest agreed to pay. ``reservation_units`` carried ``rate_plan_id``
and nothing else, so the moment a desk quoted anything other than the room
type's list price, that number existed only on the screen it was typed into.

The consequence was visible at check-in: a stay booked at 4,500 a night was
presented to the guest as 6,500 a night, because the check-in screen had
nothing to read but ``room_types.base_rate``. A two-night stay quoted at
9,590 asked for 13,000 on arrival.

``nightly_rate`` records what was agreed, per room, at the moment of booking.
It is nullable and stays that way: existing bookings genuinely do not have
this figure, and inventing one — from a list price, or by dividing a folio
total — would put a number nobody agreed to into the record. Readers fall
back to the room type's rate and are none the worse than they are today.

Tax is deliberately not stored here. It is computed from ``finance.tax_rules``
at the date it applies, and a second copy would drift the first time a rate
changed.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0027_unit_rate"
down_revision = "0026_booking_attrs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "reservation_units",
        sa.Column("nightly_rate", sa.Numeric(12, 2), nullable=True),
        schema="booking",
    )
    op.create_check_constraint(
        "ck_reservation_unit_rate_nonneg",
        "reservation_units",
        "nightly_rate IS NULL OR nightly_rate >= 0",
        schema="booking",
    )


def downgrade() -> None:
    op.drop_constraint("ck_reservation_unit_rate_nonneg", "reservation_units",
                       schema="booking", type_="check")
    op.drop_column("reservation_units", "nightly_rate", schema="booking")
