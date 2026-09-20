"""When each channel connection was last pushed, and whether it worked

Revision ID: 0036_channel_push_state
Revises: 0035_unique_channel_property
Create Date: 2026-09-12

Pushing rates and availability to a channel is the half nobody watches. A
booking that fails to arrive is noticed within a day, because a guest turns up
or an OTA chases it. A *push* that fails is invisible: the OTA simply keeps
selling yesterday's prices and yesterday's availability, and the first symptom
is an overbooking or a room sold at last season's rate.

So every push records what happened, on the connection itself rather than in a
log nobody opens. Three columns, because three questions get asked:

``last_pushed_at``
    When did this channel last hear from us. A timestamp hours old on a
    connection that is supposed to sync every minute is the whole diagnosis.

``last_push_status``
    ``ok``, ``partial`` or ``failed``. Partial is its own state on purpose:
    availability going out while rates fail is not success, and calling it
    success is how a hotel ends up bookable at the wrong price.

``last_push_detail``
    What went wrong, in words. The alternative is reading container logs to
    find out why a channel is stale, which nobody does at the moment it
    matters.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0036_channel_push_state"
down_revision = "0035_unique_channel_property"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("channel_connections",
                  sa.Column("last_pushed_at", sa.DateTime(timezone=True),
                            nullable=True),
                  schema="distribution")
    op.add_column("channel_connections",
                  sa.Column("last_push_status", sa.String(length=20),
                            nullable=True),
                  schema="distribution")
    op.add_column("channel_connections",
                  sa.Column("last_push_detail", sa.Text(), nullable=True),
                  schema="distribution")
    op.create_check_constraint(
        "ck_channel_connection_push_status",
        "channel_connections",
        "last_push_status IS NULL OR last_push_status IN "
        "('ok', 'partial', 'failed')",
        schema="distribution",
    )


def downgrade() -> None:
    op.drop_constraint("ck_channel_connection_push_status",
                       "channel_connections", schema="distribution",
                       type_="check")
    op.drop_column("channel_connections", "last_push_detail",
                   schema="distribution")
    op.drop_column("channel_connections", "last_push_status",
                   schema="distribution")
    op.drop_column("channel_connections", "last_pushed_at",
                   schema="distribution")
