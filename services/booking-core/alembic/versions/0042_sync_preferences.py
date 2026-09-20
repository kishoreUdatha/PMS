"""What this property sends to the channel manager, and who hears when it fails

Revision ID: 0042_sync_preferences
Revises: 0041_ota_hotel_id
Create Date: 2026-09-13

Three switches that were previously only a deployment-wide setting
(``CHANNEL_PUSH_ENABLED``), which is the wrong grain: it is on or off for
every hotel at once, and the person who wants it off for one property has no
way to say so short of an engineer editing an environment variable.

On the link and not on the connection, which is the whole point and worth
stating. Rates and availability are pushed **once per property**, and the
channel manager fans them out to whichever channels that property sells
through -- ``push_all`` says exactly this. So "stop sending availability to
Agoda but keep sending it to Booking.com" is not a thing this architecture can
do, and putting these columns on ``channel_connections`` would have promised
per-OTA control that nothing behind it could honour. A switch that lies is
worse than a switch that is missing.

``notify_on_failure`` is separate from the other two because it is about
people rather than data: a push that fails is invisible today unless somebody
reads container logs, and the first symptom is an OTA selling last season's
prices.

All three default to true. A property that has gone to the trouble of
connecting a channel manager wants its rates to arrive there, and wants to be
told when they do not.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0042_sync_preferences"
down_revision = "0041_ota_hotel_id"
branch_labels = None
depends_on = None

_COLUMNS = (
    ("send_availability", "Send how many of each room type are sellable."),
    ("send_rates", "Send the nightly price of each mapped rate plan."),
    ("notify_on_failure", "Tell the property's admins when a push fails."),
)


def upgrade() -> None:
    for name, _why in _COLUMNS:
        op.add_column(
            "channel_manager_links",
            sa.Column(name, sa.Boolean, nullable=False,
                      server_default=sa.text("true")),
            schema="distribution",
        )


def downgrade() -> None:
    for name, _why in reversed(_COLUMNS):
        op.drop_column("channel_manager_links", name, schema="distribution")
