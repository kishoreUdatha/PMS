"""Let a room be declared complimentary or house use

Revision ID: 0053_complimentary
Revises: 0052_registration_details
Create Date: 2026-09-16

There is already a Complimentary Room Report, and its own note admits the
problem it was built around: "A reservation cannot be marked complimentary as
such, so this report finds complimentary stays from what was actually
recorded." It reverse-engineers them three ways -- a zero nightly rate, room
charges adjusted off in full, or a room move recorded as a complimentary
upgrade.

Inference is not good enough for this, for three reasons.

**A zero rate is ambiguous.** A room genuinely given away and a rate somebody
forgot to type look identical. One is a decision; the other is a mistake, and
the report cannot tell a manager which they are looking at.

**Nobody's name is on it.** A comp arranged through a folio adjustment carries
an approver, because adjustments are approved. A room simply booked at zero
carries nothing -- so the giveaway with no paper trail is exactly the one the
report cannot attribute.

**House use is not complimentary.** A room the hotel occupies itself -- a
maintenance hold, an office, staff accommodation, a show room -- currently has
to be faked as a zero-rate booking, and then appears in the report as a
complimentary *guest* stay. They are different things: one is revenue given
away, the other is inventory the hotel took off sale for itself.

So a unit now says what it is. ``comp_kind`` null is the ordinary case.

``comp_reason`` is a fixed set on purpose, and this is not the "Tiffin"
mistake from the service catalogue: those were a property's own vocabulary for
what it sells, whereas these are reporting categories that a revenue manager
compares across properties and periods. Free text would make the report
ungroupable. Anything the set does not cover is ``other`` plus ``comp_note``,
which is where the specifics belong.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision: str = "0053_complimentary"
down_revision: str | None = "0052_registration_details"
branch_labels = None
depends_on = None

#: Why a room was given away, or taken for the hotel's own use. Kept in step
#: with COMP_REASONS in booking_core/comp_routes.py.
REASONS = (
    # Complimentary to a guest
    "vip", "fam_trip", "tour_leader", "service_recovery",
    "owner", "marketing", "loyalty",
    # The hotel's own use
    "maintenance", "office", "staff_accommodation", "show_room",
    "other",
)


def upgrade() -> None:
    kinds = "'complimentary', 'house_use'"
    reasons = ", ".join(f"'{r}'" for r in REASONS)

    op.add_column("reservation_units",
                  sa.Column("comp_kind", sa.String(20), nullable=True),
                  schema="booking")
    op.add_column("reservation_units",
                  sa.Column("comp_reason", sa.String(30), nullable=True),
                  schema="booking")
    op.add_column("reservation_units",
                  sa.Column("comp_note", sa.String(300), nullable=True),
                  schema="booking")
    # Who decided, and when. No foreign key to iam.users: that is another
    # service's table, and a staff member leaving must not take the record of
    # what they authorised with them.
    op.add_column("reservation_units",
                  sa.Column("comp_authorised_by", pg.UUID(as_uuid=True),
                            nullable=True),
                  schema="booking")
    op.add_column("reservation_units",
                  sa.Column("comp_authorised_at", sa.DateTime(timezone=True),
                            nullable=True),
                  schema="booking")

    op.create_check_constraint(
        "ck_ru_comp", "reservation_units",
        f"(comp_kind IS NULL AND comp_reason IS NULL)"
        f" OR (comp_kind IN ({kinds}) AND comp_reason IN ({reasons}))",
        schema="booking",
    )
    # Every report that separates comp from sold starts by finding these, and
    # they are a small fraction of the rows.
    op.create_index("ix_ru_comp", "reservation_units",
                    ["property_id", "comp_kind"], schema="booking",
                    postgresql_where=sa.text("comp_kind IS NOT NULL"))


def downgrade() -> None:
    op.drop_index("ix_ru_comp", "reservation_units", schema="booking")
    op.drop_constraint("ck_ru_comp", "reservation_units",
                       schema="booking", type_="check")
    for col in ("comp_authorised_at", "comp_authorised_by", "comp_note",
                "comp_reason", "comp_kind"):
        op.drop_column("reservation_units", col, schema="booking")
