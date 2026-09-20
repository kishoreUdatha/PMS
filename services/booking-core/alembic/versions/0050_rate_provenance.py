"""Record where a calendar rate came from

Revision ID: 0050_rate_provenance
Revises: 0049_booking_branding
Create Date: 2026-09-16

``property.rate_rules`` has had date ranges, a weekday array, priority layering
and percent/amount adjustments since the start. What it has never had is any
effect on a price. The rule resolution query lives in four places, all of them
inside the rules screen -- list, read, simulate -- and every path that actually
quotes money reads ``COALESCE(rate_calendar_days.rate, room_types.base_rate)``:
the booking engine's search and its book, the reservation's stored
``nightly_rate``, room moves, no-show penalties, and the rates pushed to OTAs.
A rule saying "+30% at weekends" published cleanly, simulated correctly, and
changed nothing anybody paid.

Rules now publish into that calendar, which is the one table all of those
already read, so a weekend surcharge reaches the front desk and Booking.com
without touching five pricing paths.

That makes provenance necessary. A grid cell someone typed by hand and a cell
a rule computed look identical once written, and re-running the rules must not
quietly overwrite the first kind -- a manager who set 4,500 for a wedding
party on the 18th means it, and a rule that says 2,600 does not know about the
wedding.

``source`` says which kind a row is. Existing rows are all manual: until now
the only way a rate got into this table was somebody typing it.

``rule_id`` says which rule produced a computed row, so the screen can show
why a night costs what it does, and so retiring a rule can find its rates.
Deliberately ON DELETE SET NULL rather than CASCADE: deleting a rule must not
delete the prices guests are being quoted today.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision: str = "0050_rate_provenance"
down_revision: str | None = "0049_booking_branding"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "rate_calendar_days",
        sa.Column("source", sa.String(10), nullable=False,
                  server_default="manual"),
        schema="property",
    )
    op.add_column(
        "rate_calendar_days",
        sa.Column("rule_id", pg.UUID(as_uuid=True), nullable=True),
        schema="property",
    )
    op.create_check_constraint(
        "ck_rcd_source", "rate_calendar_days",
        "source IN ('manual', 'rule')", schema="property",
    )
    op.create_foreign_key(
        "fk_rcd_rule", "rate_calendar_days", "rate_rules",
        ["rule_id"], ["id"],
        source_schema="property", referent_schema="property",
        ondelete="SET NULL",
    )
    # Finding every rate one rule published, to recompute or withdraw it.
    op.create_index("ix_rcd_rule", "rate_calendar_days", ["rule_id"],
                    schema="property",
                    postgresql_where=sa.text("rule_id IS NOT NULL"))


def downgrade() -> None:
    op.drop_index("ix_rcd_rule", "rate_calendar_days", schema="property")
    op.drop_constraint("fk_rcd_rule", "rate_calendar_days",
                       schema="property", type_="foreignkey")
    op.drop_constraint("ck_rcd_source", "rate_calendar_days",
                       schema="property", type_="check")
    op.drop_column("rate_calendar_days", "rule_id", schema="property")
    op.drop_column("rate_calendar_days", "source", schema="property")
