"""Every channel booking event we were sent, and what we did with it

Revision ID: 0034_channel_booking_events
Revises: 0033_channel_mapping
Create Date: 2026-09-12

Channex retries a webhook up to eleven times over roughly a day, and a retry
after a timeout is indistinguishable from a new event except by its id. Without
somewhere to claim an id before acting on it, the second delivery of a booking
creates a second booking — and the hotel finds out when two rooms are held for
one guest.

So the id is claimed here first, by an INSERT that loses on the primary key if
it has been seen. The insert *is* the lock; checking-then-acting would leave a
window for two concurrent deliveries to both pass the check.

``outcome`` is the other half. A gateway sends a great deal that is none of our
business, and an event we deliberately ignored still has to be answerable when
somebody asks "did you get our booking". Recording why something was skipped is
what makes that answerable a fortnight later:

    claimed      — taken, still being worked on
    created      — a reservation was made
    updated      — an existing reservation was changed
    cancelled    — a reservation was cancelled
    unmapped     — the channel's room code maps to nothing here. The one
                   failure a person must act on, because the booking exists at
                   the OTA and does not exist here.
    no_inventory — nothing left to sell for those dates
    duplicate    — seen before; the retry did nothing
    failed       — anything else, with the detail kept

The raw payload is kept whole. When a booking lands wrong, the only way to tell
a mapping mistake from a parsing mistake is to read exactly what arrived, and
by then the channel has moved on.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0034_channel_booking_events"
down_revision = "0033_channel_mapping"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "channel_booking_events",
        #: The provider's own revision id. The primary key on purpose: claiming
        #: it is what makes a retry a no-op.
        sa.Column("revision_id", sa.String(length=120), primary_key=True),
        sa.Column("provider", sa.String(length=30), nullable=False,
                  server_default="channex"),
        sa.Column("organization_id", sa.Uuid(), nullable=True),
        sa.Column("property_id", sa.Uuid(), nullable=True),
        #: Groups the revisions of one booking: new, then modified, then
        #: cancelled all share it.
        sa.Column("booking_id", sa.String(length=120), nullable=True),
        #: What the OTA calls it — the number a guest quotes on the phone.
        sa.Column("ota_reservation_code", sa.String(length=120), nullable=True),
        sa.Column("ota_name", sa.String(length=80), nullable=True),
        sa.Column("event_type", sa.String(length=40), nullable=True),
        sa.Column("status", sa.String(length=30), nullable=True),
        sa.Column("outcome", sa.String(length=30), nullable=False,
                  server_default="claimed"),
        sa.Column("detail", sa.Text(), nullable=True),
        #: Ours, once one exists.
        sa.Column("reservation_id", sa.Uuid(), nullable=True),
        #: Whether the provider has been told we have it. Until this is true
        #: the booking comes back, by design.
        sa.Column("acknowledged", sa.Boolean(), nullable=False,
                  server_default=sa.false()),
        sa.Column("payload", sa.JSON(), nullable=True),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.CheckConstraint(
            "outcome IN ('claimed', 'created', 'updated', 'cancelled', "
            "'unmapped', 'no_inventory', 'duplicate', 'ignored', 'failed')",
            name="ck_channel_event_outcome"),
        schema="distribution",
    )
    # The two questions anybody asks of this table: what went wrong, and what
    # happened to booking X.
    op.create_index("ix_channel_events_outcome", "channel_booking_events",
                    ["outcome", "received_at"], schema="distribution")
    op.create_index("ix_channel_events_booking", "channel_booking_events",
                    ["booking_id"], schema="distribution")


def downgrade() -> None:
    op.drop_index("ix_channel_events_booking",
                  table_name="channel_booking_events", schema="distribution")
    op.drop_index("ix_channel_events_outcome",
                  table_name="channel_booking_events", schema="distribution")
    op.drop_table("channel_booking_events", schema="distribution")
