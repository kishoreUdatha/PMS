"""One channel property belongs to one hotel, and the database must say so

Revision ID: 0035_unique_channel_property
Revises: 0034_channel_booking_events
Create Date: 2026-09-12

An arriving channel booking is routed to a tenant by one value: the id the
channel knows the property by. The lookup was ``WHERE external_property_id = ?
LIMIT 1`` against a column with no uniqueness, which means two hotels that
entered the same id — a typo, a copied example, the same id pasted twice during
onboarding — would both match, and ``LIMIT 1`` would pick whichever row came
back first.

The consequence is the worst kind this system has: one hotel's guest arriving
in another hotel's PMS, with their name, dates and card handling. Nobody would
notice until a guest turned up somewhere that had never heard of them, and by
then the same mistake would have been quietly repeating for weeks.

``LIMIT 1`` is a reasonable thing to write against a column you believe is
unique. This is the constraint that makes the belief true, so a second hotel
claiming the same channel property is refused at the point somebody types it
rather than discovered later by a guest.

Partial, on ``NOT NULL``: a connection is created before its channel property
id is known — that is what the Setup step is for — and any number of
connections may legitimately be sitting there unconfigured at once.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0035_unique_channel_property"
down_revision = "0034_channel_booking_events"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Any existing collision has to surface now rather than at the index
    # build: an opaque "could not create unique index" tells whoever is
    # deploying nothing about which two hotels are involved.
    conn = op.get_bind()
    dupes = conn.exec_driver_sql(
        """
        SELECT external_property_id, count(*) AS n
        FROM distribution.channel_connections
        WHERE external_property_id IS NOT NULL
        GROUP BY external_property_id HAVING count(*) > 1
        """
    ).fetchall()
    if dupes:
        listed = ", ".join(f"{d[0]} ({d[1]} connections)" for d in dupes)
        raise RuntimeError(
            "Two or more properties claim the same channel property id, which "
            "means channel bookings have been routed by chance: " + listed +
            ". Decide which property owns each id and clear the others before "
            "applying this migration."
        )

    op.create_index(
        "uq_channel_connection_external_property",
        "channel_connections",
        ["external_property_id"],
        unique=True,
        schema="distribution",
        postgresql_where=sa.text("external_property_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_channel_connection_external_property",
                  table_name="channel_connections", schema="distribution")
