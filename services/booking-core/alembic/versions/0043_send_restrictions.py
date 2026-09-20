"""Whether this property sends stay rules to the channel manager

Revision ID: 0043_send_restrictions
Revises: 0042_sync_preferences
Create Date: 2026-09-13

The third of the three switches on the Sync settings tab. The other two
shipped with 0042; this one was deliberately left out and shown as "Not sent",
because nothing behind it sent anything and a toggle that does nothing is
worse than an absent one -- a hotel would have believed its minimum-stay rules
were reaching the OTAs.

They reach them now, so the switch is real.

What travels is what ``property.rate_rules`` already holds and nothing more:
minimum and maximum stay, closed to arrival, closed to departure, and stop
sell. Those five are the ones every channel understands; the rest of a rule
(its adjustment, its advance-booking window) is priced into the rate that goes
out beside it and has nothing to send on its own.

Default true, like the others. A property that has connected a channel manager
means for its stay rules to apply there too -- a minimum-stay that holds at
the desk and not on Booking.com is how a hotel ends up with a one-night
booking in the middle of a three-night weekend it meant to protect.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0043_send_restrictions"
down_revision = "0042_sync_preferences"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "channel_manager_links",
        sa.Column("send_restrictions", sa.Boolean, nullable=False,
                  server_default=sa.text("true")),
        schema="distribution",
    )


def downgrade() -> None:
    op.drop_column("channel_manager_links", "send_restrictions",
                   schema="distribution")
