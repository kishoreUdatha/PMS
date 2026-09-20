"""Let each room on a booking name its own guest

Revision ID: 0056_room_guest
Revises: 0055_group_blocks
Create Date: 2026-09-17

A booking has one ``primary_guest_id``. For a family taking two rooms that is
right: one person books, one person pays, and the hotel deals with them. For a
wedding taking thirty it is useless -- thirty rooms under "Sharma Wedding",
and the desk finds out who is in 214 by asking somebody.

The workaround was thirty separate bookings, which then have to be found,
changed and cancelled thirty times, and which no longer add up to a group.

So a room line may name its own guest. ``primary_guest_id`` stays exactly what
it was -- who made the booking and who the hotel talks to -- and the new column
answers a different question: who is sleeping in this room. Null is the
ordinary case and means "the person who booked", which is what every existing
row means and why no backfill is needed.

ON DELETE SET NULL, like the block link: a guest record may be merged or
removed years later, and losing the name is a smaller wrong than being unable
to remove it. The room still exists and still has to be sold.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision: str = "0056_room_guest"
down_revision: str | None = "0055_group_blocks"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("reservation_units",
                  sa.Column("guest_id", pg.UUID(as_uuid=True), nullable=True),
                  schema="booking")
    op.create_foreign_key(
        "fk_unit_guest", "reservation_units", "guests",
        ["guest_id"], ["id"],
        source_schema="booking", referent_schema="engagement",
        ondelete="SET NULL",
    )
    # "Which rooms is this person in" is asked by the guest profile and by
    # arrivals; partial because named rooms are a small fraction of all rows.
    op.create_index("ix_unit_guest", "reservation_units", ["guest_id"],
                    schema="booking",
                    postgresql_where=sa.text("guest_id IS NOT NULL"))


def downgrade() -> None:
    op.drop_index("ix_unit_guest", "reservation_units", schema="booking")
    op.drop_constraint("fk_unit_guest", "reservation_units",
                       schema="booking", type_="foreignkey")
    op.drop_column("reservation_units", "guest_id", schema="booking")
