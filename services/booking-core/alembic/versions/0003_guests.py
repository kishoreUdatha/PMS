"""Guests (engagement) + reservation primary guest link

Revision ID: 0003_guests
Revises: 0002_stays
Create Date: 2026-09-06

Adds a minimal engagement.guests table (schema §3) and a primary_guest_id link
on booking.reservations so a reservation can carry a real guest. Guest name then
surfaces on reservation detail and the dashboard arrivals list.
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0003_guests"
down_revision: str | None = "0002_stays"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("CREATE SCHEMA IF NOT EXISTS engagement")

    op.execute(
        """
        CREATE TABLE engagement.guests (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            organization_id uuid NOT NULL,
            full_name varchar(200) NOT NULL,
            email varchar(200),
            phone varchar(40),
            nationality varchar(80),
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now(),
            version bigint NOT NULL DEFAULT 0
        )
        """
    )
    # Search helpers (name / phone / email) scoped by organization.
    op.execute(
        "CREATE INDEX ix_guests_org_name ON engagement.guests(organization_id, full_name)"
    )
    op.execute(
        "CREATE INDEX ix_guests_phone ON engagement.guests(organization_id, phone)"
    )

    # Link a primary guest onto reservations (nullable; set on hold when known).
    op.execute(
        "ALTER TABLE booking.reservations ADD COLUMN primary_guest_id uuid"
    )
    op.execute(
        "CREATE INDEX ix_reservations_primary_guest "
        "ON booking.reservations(primary_guest_id)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS booking.ix_reservations_primary_guest")
    op.execute(
        "ALTER TABLE booking.reservations DROP COLUMN IF EXISTS primary_guest_id"
    )
    op.execute("DROP TABLE IF EXISTS engagement.guests CASCADE")
