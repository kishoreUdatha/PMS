"""Room photos get a thumbnail, so the booking page is not megabytes of JPEG

Revision ID: 0031_photo_thumbnails
Revises: 0030_hold_payment_token
Create Date: 2026-09-12

A room photograph is uploaded at whatever size the camera produced. The booking
engine draws it in a box about 120 pixels wide and there are three of them on
the first screen a guest sees, so the page was sending several megabytes to
render a few thousand pixels. On a phone, on hotel wifi, that is the difference
between a page that appears and a page people leave.

The thumbnail is generated once, on upload, and its key recorded here. Derived
by convention (``thumbs/<original key>.jpg``) rather than random, so either half
of the pair can be found from the other -- but still stored, because a column
that says a thumbnail exists is worth more than a rule that says where one
would be if it did. Deriving alone would mean a HEAD request per photograph to
find out, which is the per-request work this exists to avoid.

Nullable, and everything treats it as optional. A photo whose thumbnail could
not be generated -- an odd format, a truncated file -- keeps working and simply
loads the original. Refusing an upload because the *thumbnail* failed would be
the wrong trade; the photograph is the thing the hotel cares about.

Existing rows are backfilled by ``scripts/backfill_thumbnails.py``, which reads
each original back out of object storage and resizes it. Kept out of the
migration on purpose: it downloads and re-encodes every photograph in the
deployment, which is not work to do inside a schema lock while the services
wait to start.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0031_photo_thumbnails"
down_revision = "0030_hold_payment_token"
branch_labels = None
depends_on = None

_TABLES = ("room_type_photos", "room_photos")


def upgrade() -> None:
    for table in _TABLES:
        op.add_column(
            table,
            sa.Column("thumb_key", sa.String(length=460), nullable=True),
            schema="property",
        )


def downgrade() -> None:
    for table in _TABLES:
        op.drop_column(table, "thumb_key", schema="property")
