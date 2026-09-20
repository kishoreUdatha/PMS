"""Record who posted each folio line

Revision ID: 0037_folio_entry_posted_by
Revises: 0036_folio_parent
Create Date: 2026-09-17

Every line on a folio is somebody's decision -- a desk clerk deciding the
minibar was drunk, a manager deciding to discount it -- and the ledger recorded
the decision without the decider. ``folio_entries`` held the amount, the date,
the department and the note, and nothing at all about who typed it.

That is the first question asked whenever a folio is disputed. A guest says
they never had the ₹1,450 dinner; the desk can see the charge, the time and the
note, and cannot see which of the four people on shift posted it or who to ask.
The screen could not show a "User" column because there was no user to show.

The audit log was not an answer. It carries ``payment.collected`` and
``folio.opened`` but nothing for a charge posted through ``post_charge`` -- at
the time of writing, three payment rows against three hundred and thirty-seven
folio entries. Auditing is for what happened to a record; this is part of the
record.

``posted_by``
    The caller's subject, the same string ``iam.audit_events.actor_subject``
    holds, so the display name resolves through the join everything else uses:
    ``LEFT JOIN iam.users u ON u.subject_id = posted_by``. The name is not
    copied in. People get married and correct their spelling, and a folio line
    that keeps the name as typed on the day would show the old one forever
    while every other screen shows the new.

    Nullable, and it will stay null for two kinds of row that are not anyone's
    doing: everything posted before this migration, and everything the night
    audit posts, which is the system charging the room because the clock passed
    midnight. Those read as "System" rather than being dressed up as a person.
"""
from alembic import op
import sqlalchemy as sa

revision = "0037_folio_entry_posted_by"
down_revision = "0036_folio_parent"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "folio_entries",
        sa.Column("posted_by", sa.String(length=255), nullable=True),
        schema="finance",
    )
    # Reading a folio means reading its lines and naming their authors, so the
    # join wants an index on the column it joins by.
    op.create_index(
        "ix_entries_posted_by", "folio_entries", ["posted_by"],
        schema="finance",
    )


def downgrade() -> None:
    op.drop_index("ix_entries_posted_by", table_name="folio_entries",
                  schema="finance")
    op.drop_column("folio_entries", "posted_by", schema="finance")
