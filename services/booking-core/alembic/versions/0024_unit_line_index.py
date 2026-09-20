"""A room's position on its booking

Revision ID: 0024_unit_line_index
Revises: 0023_commercial_accts
Create Date: 2026-09-10

A booking can now be several different rooms — a suite and a deluxe, on
different boards, for different numbers of people — rather than N copies of
one room. That makes "which room is this" a real question, and until now the
answer was implicit: whatever order the rows happened to come back in.

The rows of one booking are all inserted in a single transaction, so they share
``created_at`` to the microsecond. Ordering by it falls through to ``id``,
which is a random UUID — so the same booking can list its rooms in a different
order on two consecutive reads. That is harmless when the rooms are identical
and quietly wrong when they are not: a caller that replays a hold and then
assigns rooms by position would put the guest expecting the sea view into the
garden villa.

``line_index`` records the position the booking was taken in: 0 for the first
room, 1 for the second. Existing rows are numbered by ``created_at`` then
``id`` — the order they were being returned in already — so nothing that has
already been assigned moves.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0024_unit_line_index"
down_revision = "0023_commercial_accts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "reservation_units",
        sa.Column("line_index", sa.Integer(), nullable=False,
                  server_default="0"),
        schema="booking",
    )

    # Number what is already there in the order it was already coming back in,
    # so no existing booking's rooms change places.
    op.execute(
        """
        UPDATE booking.reservation_units u
           SET line_index = n.rn
          FROM (SELECT id,
                       row_number() OVER (PARTITION BY reservation_id
                                          ORDER BY created_at, id) - 1 AS rn
                  FROM booking.reservation_units) n
         WHERE n.id = u.id
        """
    )

    # One position per booking. This is what makes ordering by it total, and
    # it is the constraint that would have caught the ambiguity in the first
    # place.
    op.create_unique_constraint(
        "uq_reservation_unit_line",
        "reservation_units",
        ["reservation_id", "line_index"],
        schema="booking",
    )


def downgrade() -> None:
    op.drop_constraint(
        "uq_reservation_unit_line", "reservation_units", schema="booking",
        type_="unique",
    )
    op.drop_column("reservation_units", "line_index", schema="booking")
