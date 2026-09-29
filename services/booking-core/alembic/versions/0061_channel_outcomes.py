"""Two more outcomes for a channel booking revision.

Revision ID: 0061_channel_outcomes
Revises: 0060_guest_portal

``modified``: the OTA changed a booking that already exists here. It is
flagged for the desk rather than applied, because moving a stay can collide
with room assignments. It was previously dropped without a trace.

``cancel_blocked``: the OTA cancelled a booking whose guest has already
checked in or left, which a webhook must not undo.
"""
from __future__ import annotations

from alembic import op

revision: str = "0061_channel_outcomes"
down_revision: str | None = "0060_guest_portal"
branch_labels: str | None = None
depends_on: str | None = None

OLD = ("claimed", "created", "updated", "cancelled", "unmapped",
       "no_inventory", "duplicate", "ignored", "failed")
NEW = OLD + ("modified", "cancel_blocked")


def _set(values) -> None:
    listed = ", ".join(f"'{v}'" for v in values)
    op.execute("ALTER TABLE distribution.channel_booking_events "
               "DROP CONSTRAINT IF EXISTS ck_channel_event_outcome")
    op.execute("ALTER TABLE distribution.channel_booking_events "
               f"ADD CONSTRAINT ck_channel_event_outcome CHECK (outcome IN ({listed}))")


def upgrade() -> None:
    _set(NEW)


def downgrade() -> None:
    op.execute("UPDATE distribution.channel_booking_events SET outcome = 'failed' "
               "WHERE outcome IN ('modified', 'cancel_blocked')")
    _set(OLD)
