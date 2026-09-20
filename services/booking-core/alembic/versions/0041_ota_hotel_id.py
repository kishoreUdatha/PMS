"""The OTA's own id for this hotel, so the platform can build the channel

Revision ID: 0041_ota_hotel_id
Revises: 0040_channel_manager_group
Create Date: 2026-09-13

Two different identifiers have been quietly conflated, and only one of them
existed anywhere in this system.

``channel_manager_links.external_property_id`` is the *channel manager's* id
for a hotel. Nobody types it: provisioning creates the property there and the
channel manager hands the id back. It is what routes an arriving booking to a
tenant, and it is the same whichever OTA the booking came from.

The OTA's own id -- Agoda's 96019126, Booking.com's eight digits -- is a
different thing entirely. It comes with the hotel's contract with that OTA, it
differs per OTA and per property, and it is the one value a channel cannot be
created without. It had nowhere to live here, so a tenant wanting to sell on
Agoda had to log into the channel manager themselves and type it there --
which defeats the point of a platform that is meant to be the bridge.

It belongs on the connection, because that is the row that is already per
property and per OTA. Putting it on the link was tried and is wrong: the link
is shared by every channel, so one OTA's id would overwrite another's, and
worse, it would land in the column arriving bookings are matched on and make
every one of them unroutable.

Unique per partner and property rather than globally: two hotels legitimately
have different ids with the same OTA, but one hotel cannot have two ids with
the same OTA, and a duplicate would mean two channels fighting over one
listing.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0041_ota_hotel_id"
down_revision = "0040_channel_manager_group"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "channel_connections",
        sa.Column("ota_hotel_id", sa.String(80), nullable=True),
        schema="distribution",
    )
    # What the channel manager calls the channel once it exists, so the state
    # of a connection can be answered without listing every channel each time.
    op.add_column(
        "channel_connections",
        sa.Column("external_channel_id", sa.String(80), nullable=True),
        schema="distribution",
    )
    op.create_index(
        "uq_channel_connection_external", "channel_connections",
        ["external_channel_id"], unique=True, schema="distribution",
        postgresql_where=sa.text("external_channel_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_channel_connection_external", "channel_connections",
                  schema="distribution")
    op.drop_column("channel_connections", "external_channel_id",
                   schema="distribution")
    op.drop_column("channel_connections", "ota_hotel_id", schema="distribution")
