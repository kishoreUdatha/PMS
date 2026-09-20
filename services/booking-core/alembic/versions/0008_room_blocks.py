"""Room blocks and out-of-order rooms (screen 063)

Revision ID: 0008_room_blocks
Revises: 0007_buildings_floors
Create Date: 2026-09-08

``rooms.service_status`` could say a room was out of service, but it carried no
dates, no reason and no history — so a block could not be scheduled, could not
end, and could not be checked against the booking calendar.

This adds the business record. The *invariant* is deliberately not reinvented:
every active block also owns a ``booking.room_calendar_entries`` row of kind
'maintenance', so the GiST exclusion constraint already guarding that table
(§4) is what prevents a block from overlapping a reservation, or another block,
on the same room. One rule, enforced in one place, for both kinds of occupancy.

``group_id`` ties the per-room rows created together by one action, so the
screen can show "101 - 104 | Annual Maintenance" as a single line while the
constraint still works per room.
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0008_room_blocks"
down_revision: str | None = "0007_buildings_floors"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

REASON_CATEGORIES = (
    "maintenance_scheduled",
    "maintenance_emergency",
    "deep_cleaning",
    "renovation",
    "pest_control",
    "vip_hold",
    "group_hold",
    "damage",
    "other",
)


def upgrade() -> None:
    allowed = ", ".join(f"'{c}'" for c in REASON_CATEGORIES)
    op.execute(
        f"""
        CREATE TABLE booking.room_blocks (
            id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            organization_id  uuid NOT NULL,
            property_id      uuid NOT NULL,
            room_id          uuid NOT NULL,
            group_id         uuid NOT NULL,
            block_type       varchar(20) NOT NULL,
            reason_category  varchar(40) NOT NULL,
            reason           varchar(500),
            severity         varchar(10) NOT NULL DEFAULT 'medium',
            start_date       date NOT NULL,
            end_date         date NOT NULL,
            status           varchar(20) NOT NULL DEFAULT 'active',
            linked_reference varchar(60),
            -- The calendar row that actually reserves the dates. Cleared when
            -- the block is cancelled or ends, releasing the room.
            calendar_entry_id uuid REFERENCES booking.room_calendar_entries(id),
            created_by       uuid,
            updated_by       uuid,
            created_at       timestamptz NOT NULL DEFAULT now(),
            updated_at       timestamptz NOT NULL DEFAULT now(),
            ended_at         timestamptz,
            version          bigint NOT NULL DEFAULT 0,
            CONSTRAINT ck_block_type
                CHECK (block_type IN ('room_block', 'out_of_order')),
            CONSTRAINT ck_block_status
                CHECK (status IN ('active', 'ended', 'cancelled')),
            CONSTRAINT ck_block_severity
                CHECK (severity IN ('low', 'medium', 'high')),
            CONSTRAINT ck_block_category CHECK (reason_category IN ({allowed})),
            CONSTRAINT ck_block_dates CHECK (end_date >= start_date),
            CONSTRAINT fk_block_room
                FOREIGN KEY (property_id, room_id)
                REFERENCES property.rooms (property_id, id)
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_room_blocks_room "
        "ON booking.room_blocks (room_id, start_date, end_date)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_room_blocks_group "
        "ON booking.room_blocks (group_id)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_room_blocks_active "
        "ON booking.room_blocks (property_id, status, start_date, end_date)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS booking.room_blocks")
