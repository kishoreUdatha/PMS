"""Stays, stay room segments, unit assigned room, and room condition

Revision ID: 0002_stays
Revises: 0001_booking_core
Create Date: 2026-09-06

Adds the actual-stay tables (§4): booking.stays and booking.stay_room_segments,
an assigned_room_id column on reservation_units (physical room allocation), and
a minimal operations.room_condition so checkout can flag a room dirty (§5).
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0002_stays"
down_revision: str | None = "0001_booking_core"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("CREATE SCHEMA IF NOT EXISTS operations")

    # Physical room allocated to a reservation unit (nullable until assigned).
    op.execute(
        """
        ALTER TABLE booking.reservation_units
        ADD COLUMN assigned_room_id uuid
        """
    )
    op.execute(
        "CREATE INDEX ix_units_assigned_room "
        "ON booking.reservation_units(assigned_room_id)"
    )

    # booking.stays — actual stay, distinct from the planned reservation (§4).
    op.execute(
        """
        CREATE TABLE booking.stays (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            organization_id uuid NOT NULL,
            property_id uuid NOT NULL,
            reservation_unit_id uuid NOT NULL,
            actual_checkin_at timestamptz NOT NULL DEFAULT now(),
            actual_checkout_at timestamptz,
            checked_in_by uuid,
            checked_out_by uuid,
            status varchar(20) NOT NULL DEFAULT 'in_house',
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now(),
            version bigint NOT NULL DEFAULT 0,
            CONSTRAINT uq_stay_reservation_unit UNIQUE (reservation_unit_id),
            CONSTRAINT ck_stay_status CHECK (status IN ('in_house','checked_out')),
            CONSTRAINT ck_stay_checkout_after_checkin CHECK (
                actual_checkout_at IS NULL
                OR actual_checkout_at >= actual_checkin_at
            )
        )
        """
    )

    # booking.stay_room_segments — preserves room-change history (§4).
    op.execute(
        """
        CREATE TABLE booking.stay_room_segments (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            organization_id uuid NOT NULL,
            property_id uuid NOT NULL,
            stay_id uuid NOT NULL REFERENCES booking.stays(id),
            room_calendar_entry_id uuid NOT NULL
                REFERENCES booking.room_calendar_entries(id),
            actual_start_at timestamptz NOT NULL DEFAULT now(),
            actual_end_at timestamptz,
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        "CREATE INDEX ix_stay_segments_stay "
        "ON booking.stay_room_segments(stay_id)"
    )

    # operations.room_condition — one current row per room; cleanliness (§5).
    op.execute(
        """
        CREATE TABLE operations.room_condition (
            room_id uuid PRIMARY KEY,
            organization_id uuid NOT NULL,
            property_id uuid NOT NULL,
            cleanliness varchar(20) NOT NULL DEFAULT 'clean',
            inspected_at timestamptz,
            inspector_id uuid,
            updated_at timestamptz NOT NULL DEFAULT now(),
            version bigint NOT NULL DEFAULT 0,
            CONSTRAINT ck_room_cleanliness CHECK (
                cleanliness IN ('dirty','cleaning','clean','inspected')
            )
        )
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS operations.room_condition CASCADE")
    op.execute("DROP TABLE IF EXISTS booking.stay_room_segments CASCADE")
    op.execute("DROP TABLE IF EXISTS booking.stays CASCADE")
    op.execute("DROP INDEX IF EXISTS booking.ix_units_assigned_room")
    op.execute(
        "ALTER TABLE booking.reservation_units DROP COLUMN IF EXISTS assigned_room_id"
    )
