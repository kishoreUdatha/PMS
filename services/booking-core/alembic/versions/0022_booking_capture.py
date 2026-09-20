"""Salutation, and the times a guest actually said they would arrive

Revision ID: 0022_booking_capture
Revises: 0021_enquiries
Create Date: 2026-09-10

Three fields the booking screen asks for on paper and had nowhere to put.

**Salutation.** ``engagement.guests`` stores a full name and nothing else about
how to address someone. "Mr Bob Baker" on a registration card and a folio is
not decoration in a hotel; it is how the document reads.

**Expected arrival and departure times.** ``reservation_units`` holds dates
only, so every screen falls back to the property's published check-in and
check-out. That is the right default and a poor fact: a guest who says they
land at 2am needs the desk to know, and the Room Rack currently draws every
arrival at 14:00 because it has nothing better. These are *expected* times,
kept distinct from ``stay_checkins.checked_in_at``, which is what happened.

**Business source.** ``reservations.source`` records the channel — phone, OTA,
travel agent. It does not record *which* OTA, or which agent, and those are
different questions: "how did this reach us" versus "who sent it". Free text
rather than a lookup, because there is no channel or agent master to point at
and inventing one here would be worse than recording the name.

All nullable. Nothing is backfilled: a blank means nobody was asked, which is
not the same as a default.
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0022_booking_capture"
down_revision: str | None = "0021_enquiries"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TITLES = ("Mr", "Ms", "Mrs", "Miss", "Dr", "Prof")


def upgrade() -> None:
    op.add_column("guests", sa.Column("title", sa.String(10), nullable=True),
                  schema="engagement")
    op.create_check_constraint(
        "ck_guest_title", "guests",
        "title IS NULL OR title IN ({})".format(
            ", ".join(f"'{t}'" for t in TITLES)),
        schema="engagement",
    )

    for col in (
        sa.Column("expected_arrival_time", sa.Time, nullable=True),
        sa.Column("expected_departure_time", sa.Time, nullable=True),
    ):
        op.add_column("reservation_units", col, schema="booking")

    op.add_column("reservations",
                  sa.Column("business_source", sa.String(120), nullable=True),
                  schema="booking")


def downgrade() -> None:
    op.drop_column("reservations", "business_source", schema="booking")
    op.drop_column("reservation_units", "expected_departure_time", schema="booking")
    op.drop_column("reservation_units", "expected_arrival_time", schema="booking")
    op.drop_constraint("ck_guest_title", "guests", schema="engagement")
    op.drop_column("guests", "title", schema="engagement")
