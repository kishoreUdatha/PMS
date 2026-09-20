"""When each property was last set up at the channel manager, and how it went

Revision ID: 0039_channel_provision_state
Revises: 0038_restore_mapping_uniqueness
Create Date: 2026-09-13

Provisioning stopped being something a person does. It runs when a property
goes live and again on a sweep, which means nobody is watching the result of
any individual run — so each run has to leave its result somewhere, on the
link itself rather than in a log nobody opens.

Three columns, for the three questions asked about an automatic job:

``last_provisioned_at``
    When this property was last reconciled with the channel manager, and the
    sweep's own cooldown. A property that failed is not retried every minute
    for a day; this is what says how long ago the last attempt was.

``last_provision_status``
    ``ok``, ``partial`` or ``failed``. Partial is its own state for the same
    reason it is on a push: the property and most rooms went across and one
    room type has nothing to sell, which is neither success nor failure and
    must not be filed as either.

``last_provision_detail``
    What is wrong, named. "Sea View Suite: no rate plan covers this room
    type" is something a person can act on; "partial" is not.

The link row is also allowed to exist with no ``external_property_id`` now.
That sounds like a loosening but is the opposite: a run that cannot even
create the property at the far side previously had nowhere to record that it
failed, so the sweep had no memory of it and would try again, and again, on
every pass. A row with a null external id is exactly that memory.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0039_channel_provision_state"
down_revision = "0038_restore_mapping_uniqueness"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("channel_manager_links",
                  sa.Column("last_provisioned_at", sa.DateTime(timezone=True),
                            nullable=True),
                  schema="distribution")
    op.add_column("channel_manager_links",
                  sa.Column("last_provision_status", sa.String(20),
                            nullable=True),
                  schema="distribution")
    op.add_column("channel_manager_links",
                  sa.Column("last_provision_detail", sa.Text, nullable=True),
                  schema="distribution")
    op.create_check_constraint(
        "ck_channel_link_provision_status", "channel_manager_links",
        "last_provision_status IS NULL OR last_provision_status IN "
        "('ok', 'partial', 'failed')",
        schema="distribution",
    )


def downgrade() -> None:
    op.drop_constraint("ck_channel_link_provision_status",
                       "channel_manager_links", schema="distribution",
                       type_="check")
    for col in ("last_provision_detail", "last_provision_status",
                "last_provisioned_at"):
        op.drop_column("channel_manager_links", col, schema="distribution")
