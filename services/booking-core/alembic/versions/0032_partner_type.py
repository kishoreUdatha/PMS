"""A business source says which OTA or agent; this says which kind it is

Revision ID: 0032_partner_type
Revises: 0031_photo_thumbnails
Create Date: 2026-09-12

``engagement.booking_attributes`` already holds the list of places a booking can
come from — Booking.com, a travel agent, the hotel's own website. What it has
never recorded is the distinction that actually changes how a partner is worked
with: an online channel is a system that should push bookings at us, while a
travel agent is a person who telephones and whose bookings are typed in by
hand. Those need different screens, different expectations and different
chasing when nothing arrives for a week.

Nullable, and only meaningful for ``kind = 'business_source'``. A market
segment is not a partner and has no type; leaving it NULL says so more
honestly than inventing a third value for "not applicable".

Not a new table. The registry of partners already exists and is already the
thing reservations point at through ``business_source_id`` — a second table
would mean two lists of the same partners, and a week later they would
disagree about which ones are active.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0032_partner_type"
down_revision = "0031_photo_thumbnails"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "booking_attributes",
        sa.Column("partner_type", sa.String(length=20), nullable=True),
        schema="engagement",
    )
    op.create_check_constraint(
        "ck_booking_attr_partner_type",
        "booking_attributes",
        "partner_type IS NULL OR partner_type IN "
        "('online_channel', 'travel_agent', 'direct')",
        schema="engagement",
    )


def downgrade() -> None:
    op.drop_constraint("ck_booking_attr_partner_type", "booking_attributes",
                       schema="engagement", type_="check")
    op.drop_column("booking_attributes", "partner_type", schema="engagement")
