"""How a booking arrived, and what it was booked on

Revision ID: 0020_reservation_attrs
Revises: 0019_housekeeping
Create Date: 2026-09-10

``booking.reservations`` records what a stay *is* — number, status, currency,
guest — and nothing about how it came to exist. That absence has now surfaced
in four places:

* the New Reservation screen collects a Booking Source and then discards it;
* the Room Rack cannot show a Walk-ins count, because nothing says a booking
  was a walk-in;
* ``/reservations/{id}/detail`` carries a comment admitting it cannot price a
  stay properly because "a reservation does not record which rate plan booked
  it";
* Reservation Details (screen 028) asks for ten fields that have nowhere to
  live — source, rate plan, meal plan, package, market segment, purpose of
  stay, agent/company, travel agent, reference and remarks.

**Source is constrained, the rest are not.** ``source`` decides a KPI and will
decide reporting, so it is a closed set enforced by a check constraint rather
than free text that drifts into "walk in", "Walk-In" and "walkin". Market
segment and purpose of stay are varchar because every property classifies
them differently and inventing a taxonomy here would be worse than letting
one emerge.

**Company and travel agent are text, deliberately.** There is no company or
agent table — eZee's ``Verify Travel Agent`` has no counterpart here, and
building an agent master with commission and credit terms is a domain, not a
column. Recording the name is honest and useful now; it upgrades cleanly to a
foreign key when that domain exists.

**Rate plan and meal plan go on the unit, not the reservation**, because a
booking of three rooms can put two on one plan and one on another. Package is
already there. All three are nullable and ON DELETE SET NULL: retiring a rate
plan must not delete the history of what was booked on it.
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0020_reservation_attrs"
down_revision: str | None = "0019_housekeeping"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SOURCES = (
    "direct", "website", "phone", "email", "walk_in", "ota",
    "travel_agent", "corporate", "group", "other",
)


def upgrade() -> None:
    for col in (
        sa.Column("source", sa.String(30), nullable=True),
        sa.Column("market_segment", sa.String(80), nullable=True),
        sa.Column("purpose_of_stay", sa.String(80), nullable=True),
        sa.Column("company_name", sa.String(200), nullable=True),
        sa.Column("travel_agent", sa.String(200), nullable=True),
        # The booker's own reference — a PO number, an OTA booking id.
        sa.Column("reference", sa.String(120), nullable=True),
        sa.Column("remarks", sa.Text, nullable=True),
        # What the guest asked for, as they asked for it.
        sa.Column("special_requests", sa.Text, nullable=True),
        sa.Column("cancellation_policy_id", postgresql.UUID(as_uuid=True),
                  nullable=True),
    ):
        op.add_column("reservations", col, schema="booking")

    op.create_check_constraint(
        "ck_reservation_source", "reservations",
        "source IS NULL OR source IN ({})".format(
            ", ".join(f"'{s}'" for s in SOURCES)),
        schema="booking",
    )
    op.create_foreign_key(
        "fk_reservation_cancellation_policy", "reservations",
        "cancellation_policies", ["cancellation_policy_id"], ["id"],
        source_schema="booking", referent_schema="property",
        ondelete="SET NULL",
    )
    op.create_index("ix_reservation_source", "reservations", ["source"],
                    schema="booking")

    # Per unit: three rooms on one booking can sit on different plans.
    op.add_column("reservation_units",
                  sa.Column("rate_plan_id", postgresql.UUID(as_uuid=True),
                            nullable=True), schema="booking")
    op.add_column("reservation_units",
                  sa.Column("meal_plan_id", postgresql.UUID(as_uuid=True),
                            nullable=True), schema="booking")
    op.create_foreign_key(
        "fk_unit_rate_plan", "reservation_units", "rate_plans",
        ["rate_plan_id"], ["id"], source_schema="booking",
        referent_schema="property", ondelete="SET NULL",
    )
    op.create_foreign_key(
        "fk_unit_meal_plan", "reservation_units", "meal_plans",
        ["meal_plan_id"], ["id"], source_schema="booking",
        referent_schema="property", ondelete="SET NULL",
    )

    # Every existing reservation predates the field. They are left NULL rather
    # than backfilled to a guess: "unknown" is true, "direct" would not be.
    op.execute(
        """
        UPDATE booking.reservations r
           SET cancellation_policy_id = p.id
          FROM property.cancellation_policies p
         WHERE p.property_id = r.property_id
           AND p.is_default
           AND r.cancellation_policy_id IS NULL
        """
    )


def downgrade() -> None:
    op.drop_constraint("fk_unit_meal_plan", "reservation_units",
                       schema="booking", type_="foreignkey")
    op.drop_constraint("fk_unit_rate_plan", "reservation_units",
                       schema="booking", type_="foreignkey")
    op.drop_column("reservation_units", "meal_plan_id", schema="booking")
    op.drop_column("reservation_units", "rate_plan_id", schema="booking")

    op.drop_index("ix_reservation_source", "reservations", schema="booking")
    op.drop_constraint("fk_reservation_cancellation_policy", "reservations",
                       schema="booking", type_="foreignkey")
    op.drop_constraint("ck_reservation_source", "reservations", schema="booking")
    for name in ("cancellation_policy_id", "special_requests", "remarks",
                 "reference", "travel_agent", "company_name",
                 "purpose_of_stay", "market_segment", "source"):
        op.drop_column("reservations", name, schema="booking")
