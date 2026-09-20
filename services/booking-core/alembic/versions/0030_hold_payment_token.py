"""A hold carries the one credential that lets a guest pay for it

Revision ID: 0030_hold_payment_token
Revises: 0029_extra_rates
Create Date: 2026-09-12

A guest paying through the booking engine has no account and no session. So
something in their request has to prove they are the person this room is being
held for, and until now nothing could: the hold was addressed only by its
reservation id.

A reservation id is the wrong thing to use for it. It is an internal
identifier — it sits in staff URLs, in logs, in exports, in a support ticket —
and an identifier that doubles as a credential is a credential that leaks
through every one of those. This is the same call ``webhook_ref`` made on the
finance side, for the same reason: what identifies a thing and what authorises
an action on it should not be the same string.

So the hold gets its own secret, minted when the room is held and handed to the
guest exactly once, in the response to their booking request. It is never
listed, never returned again, and it dies with the hold it belongs to — fifteen
minutes, and the reaper takes both.

Stored as a SHA-256 hash rather than the value. The token is short-lived and
low-value, so this is not the difference between safe and unsafe; it is that a
database backup of a live system should not be a pile of working payment
capabilities, and hashing it costs two lines. Nothing needs to read it back —
the only operation is "does the one presented match", which a hash answers.

Nullable, because every hold created before this migration has no token. Those
holds simply cannot be paid for online; they expire within the quarter hour,
which makes backfilling them a pointless exercise.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0030_hold_payment_token"
down_revision = "0029_extra_rates"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "booking_holds",
        sa.Column("payment_token_hash", sa.String(length=64), nullable=True),
        schema="booking",
    )


def downgrade() -> None:
    op.drop_column("booking_holds", "payment_token_hash", schema="booking")
