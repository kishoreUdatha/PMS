"""The housekeeping task, so cleaning a room is a thing that happens (screen 006)

Revision ID: 0019_housekeeping
Revises: 0018_reservation_changes
Create Date: 2026-09-09

Checkout has been marking rooms dirty since it was written, and
``operations.room_condition`` has been faithfully recording that. But a
cleanliness column can only say what a room *is*, never who is dealing with it,
since when, or how long it took — so a dirty room had nowhere to go and nobody
attached to it.

``operations.housekeeping_tasks`` is that missing middle. One row is one piece
of work on one room on one day, moving through

    assigned -> in_progress -> inspection -> ready

with the person and the timestamp recorded at each step. The board's five lanes
are that state, plus a synthetic first lane for dirty rooms that have no task
yet, which is precisely the set of rooms nobody has picked up.

**Room condition stays the single source of truth for what a room is.** The
task drives it rather than duplicating it: starting a task sets ``cleaning``,
finishing sets ``clean``, passing inspection sets ``inspected``. Nothing reads
cleanliness from the task table, so the rack, the dashboard and the room history
keep working exactly as before and cannot disagree with the board.

The partial unique index is the important constraint: a room can have any number
of finished tasks but only ever one open one. Without it, two attendants can be
sent to the same room, which is the failure this screen exists to prevent.
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0019_housekeeping"
down_revision: str | None = "0018_reservation_changes"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

STATES = ("assigned", "in_progress", "inspection", "ready", "cancelled")
OPEN_STATES = ("assigned", "in_progress", "inspection")
KINDS = ("departure", "stayover", "turndown", "deep_clean", "inspection_only")
PRIORITIES = ("urgent", "high", "normal", "low")


def upgrade() -> None:
    q = lambda v: ", ".join(f"'{x}'" for x in v)  # noqa: E731

    op.execute(
        f"""
        CREATE TABLE operations.housekeeping_tasks (
            id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            organization_id  uuid NOT NULL,
            property_id      uuid NOT NULL,
            room_id          uuid NOT NULL,
            -- The business date the work belongs to. Yesterday's unfinished
            -- task is still yesterday's, which is how a board can be behind.
            task_date        date NOT NULL,
            kind             varchar(20) NOT NULL DEFAULT 'departure',
            priority         varchar(10) NOT NULL DEFAULT 'normal',
            state            varchar(20) NOT NULL DEFAULT 'assigned',
            assigned_to      uuid,
            assigned_at      timestamptz,
            started_at       timestamptz,
            finished_at      timestamptz,
            inspected_by     uuid,
            inspected_at     timestamptz,
            -- Set when an inspection sends the room back to be redone, so a
            -- second pass is visibly a second pass.
            rejected_count   integer NOT NULL DEFAULT 0,
            notes            varchar(600),
            created_by       uuid,
            created_at       timestamptz NOT NULL DEFAULT now(),
            updated_at       timestamptz NOT NULL DEFAULT now(),
            version          bigint NOT NULL DEFAULT 0,
            CONSTRAINT ck_hk_task_state CHECK (state IN ({q(STATES)})),
            CONSTRAINT ck_hk_task_kind CHECK (kind IN ({q(KINDS)})),
            CONSTRAINT ck_hk_task_priority CHECK (priority IN ({q(PRIORITIES)})),
            CONSTRAINT ck_hk_task_rejected CHECK (rejected_count >= 0),
            CONSTRAINT fk_hk_task_room
                FOREIGN KEY (room_id) REFERENCES property.rooms (id)
        )
        """
    )
    # One open task per room. Two attendants sent to the same room is the
    # failure this screen exists to prevent, so the database refuses it.
    op.execute(
        f"""
        CREATE UNIQUE INDEX uq_hk_task_open_room
            ON operations.housekeeping_tasks (room_id)
         WHERE state IN ({q(OPEN_STATES)})
        """
    )
    op.execute(
        "CREATE INDEX ix_hk_task_board "
        "ON operations.housekeeping_tasks (property_id, task_date, state)"
    )
    op.execute(
        "CREATE INDEX ix_hk_task_attendant "
        "ON operations.housekeeping_tasks (assigned_to, state) "
        "WHERE assigned_to IS NOT NULL"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS operations.housekeeping_tasks")
