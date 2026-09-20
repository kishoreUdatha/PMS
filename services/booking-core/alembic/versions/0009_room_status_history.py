"""Room status history (screen 064)

Revision ID: 0009_room_status_history
Revises: 0008_room_blocks
Create Date: 2026-09-08

Room state was only ever stored as *current* values — ``rooms.status``,
``rooms.service_status``, ``operations.room_condition.cleanliness``. Each write
overwrote the last, so "who marked 205 dirty, and when" had no answer.

This adds an append-only event log. It is written by the paths that actually
change a room's state (housekeeping, blocks, check-in/out, room edits) and is
never updated or deleted — screen 064 is explicitly read-only, and a correction
is a new event, not an edit.

Existing history is backfilled from ``iam.audit_events`` and from current
housekeeping state so the timeline is not empty on day one; those rows are
marked ``source='system'`` so they are honestly distinguishable from events
captured live.
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0009_room_status_history"
down_revision: str | None = "0008_room_blocks"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

STATUSES = (
    "occupied", "available", "clean", "dirty", "cleaning", "inspected",
    "out_of_order", "blocked", "inactive", "maintenance",
)
SOURCES = (
    "reservation_checkin", "reservation_checkout", "housekeeping",
    "maintenance", "block", "user_update", "system",
)


def upgrade() -> None:
    statuses = ", ".join(f"'{s}'" for s in STATUSES)
    sources = ", ".join(f"'{s}'" for s in SOURCES)
    op.execute(
        f"""
        CREATE TABLE operations.room_status_events (
            id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            organization_id  uuid NOT NULL,
            property_id      uuid NOT NULL,
            room_id          uuid NOT NULL,
            status           varchar(20) NOT NULL,
            source           varchar(30) NOT NULL,
            source_reference varchar(60),
            remarks          varchar(500),
            changed_by       uuid,
            occurred_at      timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT ck_rse_status CHECK (status IN ({statuses})),
            CONSTRAINT ck_rse_source CHECK (source IN ({sources})),
            CONSTRAINT fk_rse_room
                FOREIGN KEY (property_id, room_id)
                REFERENCES property.rooms (property_id, id) ON DELETE CASCADE
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_rse_room_time "
        "ON operations.room_status_events (room_id, occurred_at DESC)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_rse_property_time "
        "ON operations.room_status_events (property_id, occurred_at DESC)"
    )

    # ---------------- backfill ------------------------------------------------
    # Room-level audit entries we already recorded.
    op.execute(
        """
        INSERT INTO operations.room_status_events
            (organization_id, property_id, room_id, status, source,
             remarks, changed_by, occurred_at)
        SELECT ae.organization_id, ae.property_id, r.id,
               CASE
                   WHEN ae.action IN ('room.block') THEN 'out_of_order'
                   WHEN ae.action IN ('room.unblock') THEN 'available'
                   WHEN ae.action = 'room.create' THEN 'available'
                   ELSE 'available'
               END,
               'system',
               'Backfilled from audit trail: ' || ae.action,
               u.id, ae.occurred_at
        FROM iam.audit_events ae
        JOIN property.rooms r ON r.id::text = ae.entity_id
        LEFT JOIN iam.users u ON u.subject_id = ae.actor_subject
        WHERE ae.entity_type = 'room'
          AND ae.action IN ('room.create', 'room.block', 'room.unblock')
        """
    )
    # Blocks already recorded: each one really did take a room out of sale.
    op.execute(
        """
        INSERT INTO operations.room_status_events
            (organization_id, property_id, room_id, status, source,
             source_reference, remarks, changed_by, occurred_at)
        SELECT b.organization_id, b.property_id, b.room_id,
               CASE WHEN b.block_type = 'out_of_order'
                    THEN 'out_of_order' ELSE 'blocked' END,
               'system', b.linked_reference,
               'Backfilled from block: ' || b.reason_category
                   || ' (' || b.start_date || ' to ' || b.end_date || ')',
               b.created_by, b.created_at
        FROM booking.room_blocks b
        """
    )
    # Current housekeeping state, as a starting point for each room.
    op.execute(
        """
        INSERT INTO operations.room_status_events
            (organization_id, property_id, room_id, status, source,
             remarks, occurred_at)
        SELECT rc.organization_id, rc.property_id, rc.room_id,
               rc.cleanliness, 'system',
               'Backfilled from current housekeeping state', rc.updated_at
        FROM operations.room_condition rc
        WHERE rc.cleanliness IN ('clean', 'dirty', 'cleaning', 'inspected')
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS operations.room_status_events")
