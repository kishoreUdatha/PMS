"""Work orders: maintenance jobs against a room or a place

Revision ID: 0046_work_orders
Revises: 0045_guest_reference
Create Date: 2026-09-14

Housekeeping tasks answer "is this room clean". A work order answers "what is
broken, who is fixing it and by when" -- a different person, a different
urgency, and a job that may be a pump or a lift nowhere near a room. Until now
that had nowhere to be written down, so the Work Order List report had nothing
to list.

**A room or a location, never neither.** ``location`` exists for the pool pump
and the generator. The database refuses a job with neither, so the list never
shows work nobody can find.

**Numbered per property.** WO-1001 onwards, unique within the property, so the
number a technician is told is the number on the screen.
"""

from __future__ import annotations

from alembic import op

revision = "0046_work_orders"
down_revision = "0045_guest_reference"
branch_labels = None
depends_on = None

CATEGORIES = ("electrical", "plumbing", "hvac", "carpentry", "furniture",
              "appliance", "civil", "it", "other")
PRIORITIES = ("low", "medium", "high", "urgent")
STATUSES = ("open", "in_progress", "on_hold", "completed", "cancelled")


def _quoted(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{v}'" for v in values)


def upgrade() -> None:
    op.execute(f"""
        CREATE TABLE operations.work_orders (
            id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            organization_id uuid NOT NULL,
            property_id     uuid NOT NULL,
            number          integer NOT NULL,
            room_id         uuid,
            location        varchar(120),
            title           varchar(200) NOT NULL,
            description     text,
            category        varchar(30) NOT NULL DEFAULT 'other',
            priority        varchar(10) NOT NULL DEFAULT 'medium',
            status          varchar(20) NOT NULL DEFAULT 'open',
            assigned_to     uuid,
            due_date        date,
            cost            numeric(14, 2),
            resolution      varchar(500),
            reported_by     uuid,
            updated_by      uuid,
            started_at      timestamptz,
            completed_at    timestamptz,
            created_at      timestamptz NOT NULL DEFAULT now(),
            updated_at      timestamptz NOT NULL DEFAULT now(),
            version         bigint NOT NULL DEFAULT 1,
            CONSTRAINT uq_work_order_number UNIQUE (property_id, number),
            CONSTRAINT ck_work_order_category CHECK (category IN ({_quoted(CATEGORIES)})),
            CONSTRAINT ck_work_order_priority CHECK (priority IN ({_quoted(PRIORITIES)})),
            CONSTRAINT ck_work_order_status CHECK (status IN ({_quoted(STATUSES)})),
            CONSTRAINT ck_work_order_where CHECK (room_id IS NOT NULL OR location IS NOT NULL),
            CONSTRAINT ck_work_order_cost CHECK (cost IS NULL OR cost >= 0)
        )
    """)
    op.execute("CREATE INDEX ix_work_order_property_status "
               "ON operations.work_orders (property_id, status)")
    op.execute("CREATE INDEX ix_work_order_room "
               "ON operations.work_orders (room_id) WHERE room_id IS NOT NULL")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS operations.work_orders")
