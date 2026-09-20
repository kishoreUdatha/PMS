"""Separate the channel manager link from the per-OTA commercial terms

Revision ID: 0037_channel_manager_link
Revises: 0036_channel_push_state
Create Date: 2026-09-13

The original model put the channel manager's property id on the *partner*
connection, one row per OTA. That was wrong, and the way it surfaced is exact:
a hotel that had connected Booking.com could not then connect Agoda. The second
attempt collided with the unique index on ``external_property_id`` and returned
409, because both OTAs legitimately share one channel-manager property.

The aggregator sits between us and the OTAs:

    our property  ──  ONE channel manager property
                            ├── Booking.com
                            ├── Agoda
                            └── Expedia

One property, one link, many channels. And the mappings belong to the link
rather than to any OTA, because the aggregator normalises the ids: a booking
from Agoda and one from Booking.com both arrive carrying the *aggregator's*
room id, not the OTA's. Mapping per partner would mean maintaining the same
three pairs three times and having them drift.

What genuinely is per-partner stays per-partner: commission and payment model
differ by OTA — Booking.com at fifteen percent, Agoda at eighteen — and a
hotel needs both recorded to know what a channel actually costs.

So this splits one table in two:

``channel_manager_links``   one per property: the aggregator's property id,
                            the mappings, and the push state.
``channel_connections``     one per partner per property: commission, payment
                            model, status.

Existing data is carried across rather than dropped: every connection that had
a channel property id becomes a link, and its mappings are repointed. A
deployment that has already mapped rooms does not have to map them again.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0037_channel_manager_link"
down_revision = "0036_channel_push_state"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "channel_manager_links",
        sa.Column("id", sa.Uuid(), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        # One link per property. This is the constraint the old model got
        # wrong by putting it on the partner connection instead.
        sa.Column("property_id", sa.Uuid(), nullable=False, unique=True),
        sa.Column("provider", sa.String(length=30), nullable=False,
                  server_default="channex"),
        #: What the aggregator calls this property. Unique across the
        #: deployment: it is what routes an arriving booking to a tenant, and
        #: two hotels claiming one id would route by chance.
        sa.Column("external_property_id", sa.String(length=80), nullable=True),
        sa.Column("currency", sa.String(length=3), nullable=True),
        sa.Column("last_pushed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_push_status", sa.String(length=20), nullable=True),
        sa.Column("last_push_detail", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("updated_by", sa.Uuid(), nullable=True),
        sa.CheckConstraint(
            "last_push_status IS NULL OR last_push_status IN "
            "('ok', 'partial', 'failed')",
            name="ck_channel_link_push_status"),
        schema="distribution",
    )
    op.create_index("uq_channel_link_external_property",
                    "channel_manager_links", ["external_property_id"],
                    unique=True, schema="distribution",
                    postgresql_where=sa.text(
                        "external_property_id IS NOT NULL"))

    # Carry the existing connections across. One link per property, taking the
    # first connection that actually had a channel property id — there can
    # only be one, because the old unique index enforced it.
    op.execute(
        """
        INSERT INTO distribution.channel_manager_links
            (organization_id, property_id, provider, external_property_id,
             currency, last_pushed_at, last_push_status, last_push_detail)
        SELECT DISTINCT ON (property_id)
               organization_id, property_id, 'channex', external_property_id,
               currency, last_pushed_at, last_push_status, last_push_detail
          FROM distribution.channel_connections
         WHERE external_property_id IS NOT NULL
         ORDER BY property_id, created_at
        """
    )

    # Repoint the mappings from the connection to the link.
    for table in ("channel_room_mappings", "channel_rate_mappings"):
        op.add_column(table, sa.Column("link_id", sa.Uuid(), nullable=True),
                      schema="distribution")
        op.execute(
            f"""
            UPDATE distribution.{table} m
               SET link_id = l.id
              FROM distribution.channel_connections c
              JOIN distribution.channel_manager_links l
                ON l.property_id = c.property_id
             WHERE m.connection_id = c.id
            """
        )
        # Anything that could not be matched belonged to a connection with no
        # channel property id, so it was never usable. Dropped rather than
        # left pointing at nothing.
        op.execute(f"DELETE FROM distribution.{table} WHERE link_id IS NULL")
        op.drop_constraint(f"{table}_connection_id_fkey", table,
                           schema="distribution", type_="foreignkey")
        op.drop_column(table, "connection_id", schema="distribution")
        op.alter_column(table, "link_id", nullable=False,
                        schema="distribution")
        op.create_foreign_key(
            f"{table}_link_id_fkey", table, "channel_manager_links",
            ["link_id"], ["id"], source_schema="distribution",
            referent_schema="distribution", ondelete="CASCADE")

    # The connection keeps only what is genuinely per-OTA.
    op.drop_index("uq_channel_connection_external_property",
                  table_name="channel_connections", schema="distribution")
    for col in ("external_property_id", "currency", "last_pushed_at",
                "last_push_status", "last_push_detail"):
        op.drop_column("channel_connections", col, schema="distribution")
    # Postgres drops a CHECK with the last column it references, so by now it
    # may already be gone. IF EXISTS rather than ordering the statements to
    # suit it: the constraint being absent is a success, not a failure.
    op.execute("ALTER TABLE distribution.channel_connections "
               "DROP CONSTRAINT IF EXISTS ck_channel_connection_push_status")


def downgrade() -> None:
    raise NotImplementedError(
        "Splitting the link back into per-partner connections would have to "
        "choose which OTA keeps the mappings, and there is no answer to that "
        "which is not arbitrary. Restore from a backup instead."
    )
