"""Waitlist and enquiries: demand that has not become a booking yet

Revision ID: 0021_enquiries
Revises: 0020_reservation_attrs
Create Date: 2026-09-10

Everything in this system so far describes a stay that exists. Nothing records
someone who *asked* — the couple who wanted a sea view over the long weekend
and were told it was full, the company that is still deciding. That demand is
invisible, so it cannot be followed up, and when a room frees up nobody knows
who wanted it.

An enquiry is not a reservation and must not be modelled as one. It holds no
inventory, has no folio and may never become a stay. It is a record of
interest with a date range attached, which is exactly enough to answer the
question the screen exists for: *when this room becomes free, who wanted it?*

**Contact details are stored on the enquiry, not on a guest record.** Creating
a guest for every enquiry would fill the directory with people who never came;
the duplicate problem there is bad enough already. ``guest_id`` is there for
when the enquirer turns out to be someone known, and gets set on conversion.

**Status is a closed set with one deliberate addition: ``lost``.** The mockup
shows five columns and stops at Converted, but an enquiry that goes nowhere
has to be closeable — otherwise the board grows forever and "Follow-ups Due"
becomes a number nobody trusts. ``lost`` is not a board column; it is how
something leaves the board.

**Budget is a range, nullable at both ends.** "Under 40,000" and "at least
30,000" are both real things a guest says, and forcing a midpoint would invent
precision the enquiry never had.

``next_follow_up_at`` is indexed because the whole screen sorts by it, and a
partial index skips the closed rows nobody follows up.
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0021_enquiries"
down_revision: str | None = "0020_reservation_attrs"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

STATUSES = ("new", "contacted", "waitlisted", "offered", "converted", "lost")
# The same vocabulary a reservation records, so an enquiry that converts keeps
# its provenance instead of becoming an anonymous "direct" booking.
CHANNELS = (
    "direct", "website", "phone", "email", "walk_in", "ota",
    "travel_agent", "corporate", "group", "other",
)


def upgrade() -> None:
    op.create_table(
        "enquiries",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("property_id", postgresql.UUID(as_uuid=True), nullable=False),
        # Set only when the enquirer is someone already known, or on conversion.
        sa.Column("guest_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("full_name", sa.String(200), nullable=False),
        sa.Column("email", sa.String(200), nullable=True),
        sa.Column("phone", sa.String(60), nullable=True),
        sa.Column("arrival_date", sa.Date, nullable=True),
        sa.Column("departure_date", sa.Date, nullable=True),
        sa.Column("adults", sa.Integer, nullable=False, server_default="2"),
        sa.Column("children", sa.Integer, nullable=False, server_default="0"),
        sa.Column("room_type_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("budget_min", sa.Numeric(12, 2), nullable=True),
        sa.Column("budget_max", sa.Numeric(12, 2), nullable=True),
        sa.Column("channel", sa.String(30), nullable=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="new"),
        sa.Column("next_follow_up_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("notes", sa.Text, nullable=True),
        sa.Column("lost_reason", sa.Text, nullable=True),
        sa.Column("converted_reservation_id", postgresql.UUID(as_uuid=True),
                  nullable=True),
        sa.Column("converted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("updated_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("version", sa.BigInteger, nullable=False, server_default="1"),
        sa.CheckConstraint(
            "status IN ({})".format(", ".join(f"'{s}'" for s in STATUSES)),
            name="ck_enquiry_status"),
        sa.CheckConstraint(
            "channel IS NULL OR channel IN ({})".format(
                ", ".join(f"'{c}'" for c in CHANNELS)),
            name="ck_enquiry_channel"),
        sa.CheckConstraint("adults >= 1 AND children >= 0",
                           name="ck_enquiry_party"),
        sa.CheckConstraint(
            "departure_date IS NULL OR arrival_date IS NULL "
            "OR departure_date > arrival_date",
            name="ck_enquiry_dates"),
        sa.CheckConstraint(
            "budget_min IS NULL OR budget_max IS NULL OR budget_max >= budget_min",
            name="ck_enquiry_budget"),
        # Converted means there is a booking to point at, and nothing else does.
        sa.CheckConstraint(
            "(status = 'converted') = (converted_reservation_id IS NOT NULL)",
            name="ck_enquiry_converted"),
        sa.ForeignKeyConstraint(["guest_id"], ["engagement.guests.id"],
                                ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["room_type_id"], ["property.room_types.id"],
                                ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["converted_reservation_id"],
                                ["booking.reservations.id"], ondelete="SET NULL"),
        schema="engagement",
    )
    op.create_index("ix_enquiry_property_status", "enquiries",
                    ["property_id", "status"], schema="engagement")
    op.create_index("ix_enquiry_dates", "enquiries",
                    ["property_id", "arrival_date"], schema="engagement")
    # Only rows anybody still chases.
    op.create_index(
        "ix_enquiry_follow_up", "enquiries", ["next_follow_up_at"],
        schema="engagement",
        postgresql_where=sa.text("status NOT IN ('converted', 'lost')"),
    )


def downgrade() -> None:
    op.drop_index("ix_enquiry_follow_up", "enquiries", schema="engagement")
    op.drop_index("ix_enquiry_dates", "enquiries", schema="engagement")
    op.drop_index("ix_enquiry_property_status", "enquiries", schema="engagement")
    op.drop_table("enquiries", schema="engagement")
