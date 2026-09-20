"""What a channel calls our rooms, so a future integration has something to match on

Revision ID: 0033_channel_mapping
Revises: 0032_partner_type
Create Date: 2026-09-12

A channel does not know what a "Deluxe Sea View" is. It knows room 458721001,
and the only way a booking for 458721001 becomes a booking for a Deluxe Sea
View is a table that says so. Somebody has to sit down with the channel's
extranet open and write those pairs out; no integration can derive them, and
every integration needs them before it can do anything at all.

So this is built now, before there is anything to connect to. The mapping is
the part that survives the choice of channel manager — SiteMinder, STAAH, a
direct XML connection, it makes no difference which reads it. Collecting it
early means the day somebody wires a connection, the tedious human half is
already done rather than being discovered as a fortnight of work.

Three tables:

``channel_connections``
    One per partner per property, because a channel lists a *property*. A group
    with three hotels has three Booking.com listings and three external ids;
    one row per partner would force them to share one, and bookings would land
    at the wrong hotel.

    ``status`` is deliberately dull. It records what a person set, not what a
    connection reported — nothing reports anything yet — and defaults to
    ``not_connected`` so nothing claims otherwise.

``channel_room_mappings`` and ``channel_rate_mappings``
    The pairs themselves. Unique on both sides: one room type maps to one
    channel room and vice versa, because two of ours pointing at one of theirs
    means an arriving booking is ambiguous, and that ambiguity would be
    discovered by a guest standing at the desk.

Nothing reads these yet, and that is the point of writing the constraints down
now. A mapping table that lets you enter nonsense while nobody is looking is a
mapping table full of nonsense on the day something finally reads it.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0033_channel_mapping"
down_revision = "0032_partner_type"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "channel_connections",
        sa.Column("id", sa.Uuid(), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("property_id", sa.Uuid(), nullable=False),
        #: The partner in engagement.booking_attributes this connection is for.
        sa.Column("partner_id", sa.Uuid(), nullable=False),
        #: What the channel calls this property — their hotel id. The one value
        #: every integration starts from, and the one nobody can guess.
        sa.Column("external_property_id", sa.String(length=80), nullable=True),
        sa.Column("currency", sa.String(length=3), nullable=True),
        #: Agreed commission, for reporting what a channel actually costs.
        sa.Column("commission_percent", sa.Numeric(5, 2), nullable=True),
        sa.Column("payment_model", sa.String(length=30), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False,
                  server_default="not_connected"),
        sa.Column("notes", sa.String(length=400), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("updated_by", sa.Uuid(), nullable=True),
        sa.CheckConstraint(
            "status IN ('not_connected', 'configuring', 'ready', 'paused')",
            name="ck_channel_connection_status"),
        sa.CheckConstraint(
            "payment_model IS NULL OR payment_model IN "
            "('hotel_collect', 'channel_collect', 'virtual_card')",
            name="ck_channel_connection_payment_model"),
        sa.CheckConstraint(
            "commission_percent IS NULL OR "
            "(commission_percent >= 0 AND commission_percent <= 100)",
            name="ck_channel_connection_commission"),
        # One listing per partner per property. See the header.
        sa.UniqueConstraint("partner_id", "property_id",
                            name="uq_channel_connection_partner_property"),
        schema="distribution",
    )
    op.create_index("ix_channel_connections_property", "channel_connections",
                    ["property_id"], schema="distribution")

    for table, owner_col, owner_label in (
        ("channel_room_mappings", "room_type_id", "room"),
        ("channel_rate_mappings", "rate_plan_id", "rate"),
    ):
        op.create_table(
            table,
            sa.Column("id", sa.Uuid(), primary_key=True,
                      server_default=sa.text("gen_random_uuid()")),
            sa.Column("connection_id", sa.Uuid(), nullable=False),
            sa.Column(owner_col, sa.Uuid(), nullable=False),
            #: What the channel calls it. Free text: every channel has its own
            #: shape and some are not numeric.
            sa.Column("external_id", sa.String(length=80), nullable=False),
            sa.Column("external_name", sa.String(length=160), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                      server_default=sa.text("now()")),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                      server_default=sa.text("now()")),
            sa.ForeignKeyConstraint(
                ["connection_id"],
                ["distribution.channel_connections.id"],
                ondelete="CASCADE"),
            # Both directions unique: two of ours pointing at one of theirs
            # makes an arriving booking ambiguous.
            sa.UniqueConstraint("connection_id", owner_col,
                                name=f"uq_{owner_label}_map_ours"),
            sa.UniqueConstraint("connection_id", "external_id",
                                name=f"uq_{owner_label}_map_theirs"),
            schema="distribution",
        )


def downgrade() -> None:
    op.drop_table("channel_rate_mappings", schema="distribution")
    op.drop_table("channel_room_mappings", schema="distribution")
    op.drop_index("ix_channel_connections_property",
                  table_name="channel_connections", schema="distribution")
    op.drop_table("channel_connections", schema="distribution")
