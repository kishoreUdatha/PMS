"""What a guest profile knows about a guest that stays cannot tell it

Revision ID: 0044_guest_profile
Revises: 0043_send_restrictions
Create Date: 2026-09-13

Screen 009 is mostly derived: total stays, lifetime value, the current
reservation and the activity timeline all come from stays and folios, and are
right by construction because nothing keeps them in step by hand.

Three things on it cannot be derived, and this migration is those three.

``guest_preferences`` -- sea view, high floor, vegetarian, airport pickup. The
guest directory (screen 010) returns this column empty today and says in its
own docstring that preferences belong to a table that does not exist. This is
that table. Free text with a kind, not an enum: a hotel's preferences are its
own, and a fixed list would be wrong at the second property.

``guest_tags`` -- VIP Gold, Repeated Guest, Leisure. Deliberately tags rather
than a ``membership_tier`` column, because that is what they are: a label
somebody puts on a guest. There is no loyalty programme behind "VIP Gold" --
no rules for earning it and nothing that recalculates it -- and a column of
that name would imply one. A tag is honest about being a thing a human wrote.

``guest_notes`` -- the internal notes card. Append-only and attributed: "Prefers
quiet rooms, celebrating a birthday" is only useful if you can see who said so
and when, and a note somebody can quietly edit later is evidence of nothing.

Plus two columns the mockup shows and the table lacked: date of birth and
occupation. Both are ordinary guest attributes the desk already asks for at
check-in, and birthdays in particular are the sort of thing this screen exists
to surface.

What is NOT added, because there is nothing behind it: an average rating (no
feedback is collected anywhere) and a communications log (nothing in this
system sends a guest a WhatsApp message or records that it did). Those two
tabs of the mockup are absent rather than empty.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0044_guest_profile"
down_revision = "0043_send_restrictions"
branch_labels = None
depends_on = None


def _guest_child(name: str, *cols: sa.Column) -> None:
    """A table hanging off one guest, scoped to the organisation that owns it.

    ``organization_id`` is carried on the row rather than joined for through
    the guest: every query on this screen filters by it, and a child table that
    can only be scoped by joining its parent is one bad join away from leaking
    across tenants.
    """
    op.create_table(
        name,
        sa.Column("id", sa.dialects.postgresql.UUID(as_uuid=True),
                  primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("organization_id", sa.dialects.postgresql.UUID(as_uuid=True),
                  nullable=False),
        sa.Column("guest_id", sa.dialects.postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("engagement.guests.id", ondelete="CASCADE"),
                  nullable=False),
        *cols,
        sa.Column("created_by", sa.dialects.postgresql.UUID(as_uuid=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        schema="engagement",
    )
    op.create_index(f"ix_{name}_guest", name, ["organization_id", "guest_id"],
                    schema="engagement")


def upgrade() -> None:
    _guest_child(
        "guest_preferences",
        # A grouping the UI draws an icon from. Unconstrained on purpose: an
        # unknown kind renders with a neutral icon rather than failing to save.
        sa.Column("kind", sa.String(30), nullable=False, server_default="other"),
        sa.Column("label", sa.String(120), nullable=False),
        sa.Column("position", sa.Integer, nullable=False, server_default="0"),
    )
    _guest_child(
        "guest_tags",
        sa.Column("tag", sa.String(40), nullable=False),
    )
    # One of each tag per guest. Without this, "VIP Gold" added twice shows
    # twice, and removing it removes one of them.
    op.create_unique_constraint(
        "uq_guest_tag", "guest_tags", ["guest_id", "tag"], schema="engagement")
    _guest_child(
        "guest_notes",
        sa.Column("body", sa.Text, nullable=False),
    )

    op.add_column("guests", sa.Column("date_of_birth", sa.Date()),
                  schema="engagement")
    op.add_column("guests", sa.Column("occupation", sa.String(120)),
                  schema="engagement")


def downgrade() -> None:
    op.drop_column("guests", "occupation", schema="engagement")
    op.drop_column("guests", "date_of_birth", schema="engagement")
    for name in ("guest_notes", "guest_tags", "guest_preferences"):
        op.drop_table(name, schema="engagement")
