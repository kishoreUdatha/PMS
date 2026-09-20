"""Let a rate rule depend on how full the date already is

Revision ID: 0051_occupancy_yield
Revises: 0050_rate_provenance
Create Date: 2026-09-16

Weekend and festival pricing answers "what is this date worth in principle".
Occupancy answers "what is it worth now" -- a Tuesday in February with two
rooms left is worth more than the same Tuesday half empty, and a weekend
still wide open three days out is worth less than the rule says.

This is one more *condition* on a rule, not a new kind of thing. A rule
already declines to apply outside its dates, its weekdays, its room types, its
stay length and its booking window; occupancy joins that list. So "+20% once
80% is sold" is an ordinary rule with ``occupancy_min = 80``, and it inherits
priority layering, room-type targeting and the publish pipeline for free
rather than growing a second engine beside the first.

Both ends, because yield goes both ways. ``occupancy_min`` alone is the
surcharge on a filling date; ``occupancy_max`` alone is the discount on an
empty one, which is the more useful half for a resort in a shoulder month and
the half people forget to build.

Percent of *sellable* rooms, not of the building: a room out of service cannot
be sold, so counting it would report a full hotel as 80% full and quietly
withhold the surcharge. What counts as sold is defined in rate_publish, where
the arithmetic lives.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0051_occupancy_yield"
down_revision: str | None = "0050_rate_provenance"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("rate_rules",
                  sa.Column("occupancy_min", sa.Integer, nullable=True),
                  schema="property")
    op.add_column("rate_rules",
                  sa.Column("occupancy_max", sa.Integer, nullable=True),
                  schema="property")
    op.create_check_constraint(
        "ck_rate_rule_occupancy", "rate_rules",
        "(occupancy_min IS NULL OR occupancy_min BETWEEN 0 AND 100)"
        " AND (occupancy_max IS NULL OR occupancy_max BETWEEN 0 AND 100)"
        # A band that cannot contain anything is a rule that silently never
        # fires. Refused here rather than discovered in a quiet month.
        " AND (occupancy_min IS NULL OR occupancy_max IS NULL"
        "      OR occupancy_min <= occupancy_max)",
        schema="property",
    )


def downgrade() -> None:
    op.drop_constraint("ck_rate_rule_occupancy", "rate_rules",
                       schema="property", type_="check")
    op.drop_column("rate_rules", "occupancy_max", schema="property")
    op.drop_column("rate_rules", "occupancy_min", schema="property")
