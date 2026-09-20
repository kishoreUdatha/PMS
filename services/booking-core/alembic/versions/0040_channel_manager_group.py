"""A channel manager group per tenant, so one account is not one bucket

Revision ID: 0040_channel_manager_group
Revises: 0039_channel_provision_state
Create Date: 2026-09-13

Every tenant's property has been created in whichever group the channel
manager happened to return first -- literally ``rows[0]``. With three
properties that is untidy. With a hundred hotels belonging to a hundred
unrelated businesses it is a single bucket holding all of them, and it breaks
something real:

Provisioning adopts an existing property at the far side by matching its
*name*, so that a half-finished run does not leave a second listing behind.
Searched across the whole account, that match is not tenant-safe. Two
customers with a hotel called "Sunrise Resort" -- not unlikely at a hundred --
and the second tenant's run adopts the first tenant's property. Today the
unique index on ``external_property_id`` catches it and the run fails, so it
is caught rather than leaked; but it fails with an error nobody can read, and
the safety rests entirely on that one index.

Giving each organisation its own group and searching only inside it makes the
match correct rather than merely caught.

It is worth being clear about what this is and is not. The channel manager
offers no way to restrict an API key to a group, so a group is an
organisational boundary, not a security one: isolation between tenants is
still enforced here, by the routing and the unique constraints. The group
narrows what a mistake can reach, and makes an account of a hundred hotels
legible to whoever has to look at it.

One row per organisation, not per property: two properties of the same tenant
in different groups is a state nothing should be able to reach, and a table
keyed on the organisation cannot represent it.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0040_channel_manager_group"
down_revision = "0039_channel_provision_state"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "channel_manager_groups",
        sa.Column("id", sa.UUID, primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("organization_id", sa.UUID, nullable=False),
        sa.Column("provider", sa.String(30), nullable=False,
                  server_default="channex"),
        # Nullable: the row is written the moment the group is wanted, and the
        # id arrives once the far side has created it. A failed create then
        # leaves a row saying "this tenant has no group yet" rather than
        # nothing at all.
        sa.Column("external_group_id", sa.String(80), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.UniqueConstraint("organization_id", "provider",
                            name="uq_channel_group_org"),
        schema="distribution",
    )
    # One group belongs to one tenant, in both directions. Without this a
    # mistake could point two organisations at one group, which is exactly the
    # shared bucket this exists to end.
    op.create_index(
        "uq_channel_group_external", "channel_manager_groups",
        ["external_group_id"], unique=True, schema="distribution",
        postgresql_where=sa.text("external_group_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_channel_group_external", "channel_manager_groups",
                  schema="distribution")
    op.drop_table("channel_manager_groups", schema="distribution")
