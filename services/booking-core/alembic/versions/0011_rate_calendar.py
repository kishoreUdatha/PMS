"""Per-date rates and restrictions (screen 034)

Revision ID: 0011_rate_calendar
Revises: 0010_rate_plans
Create Date: 2026-09-08

``room_types.base_rate`` is one number for all time. A resort cannot price a
Saturday differently from a Tuesday with it, cannot set a two-night minimum
over a weekend, and cannot stop selling a single date.

This adds the per-date layer. It is deliberately **sparse**: a row exists only
where someone has actually overridden something for that date. Everything else
falls back to the room type — so a new room type is immediately sellable for
every date without pre-generating a year of rows, and changing ``base_rate``
still moves every date nobody has touched.

``rate`` and ``min_stay`` are nullable for the same reason: NULL means "inherit",
which is different from "someone set it to this value that happens to match".
Clearing an override is therefore possible, and the grid can show which cells
are overridden.

Availability is *not* stored here. It is derived from the room inventory and
``booking.room_type_inventory_days`` at read time, because a second copy of
availability is exactly the thing that goes wrong.
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0011_rate_calendar"
down_revision: str | None = "0010_rate_plans"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE property.rate_calendar_days (
            organization_id uuid NOT NULL,
            property_id     uuid NOT NULL,
            room_type_id    uuid NOT NULL,
            stay_date       date NOT NULL,
            -- NULL means "inherit from the room type", not "zero".
            rate            numeric(12, 2),
            min_stay        integer,
            stop_sell       boolean NOT NULL DEFAULT false,
            updated_by      uuid,
            created_at      timestamptz NOT NULL DEFAULT now(),
            updated_at      timestamptz NOT NULL DEFAULT now(),
            version         bigint NOT NULL DEFAULT 0,
            PRIMARY KEY (property_id, room_type_id, stay_date),
            CONSTRAINT ck_rcd_rate CHECK (rate IS NULL OR rate >= 0),
            CONSTRAINT ck_rcd_min_stay CHECK (min_stay IS NULL OR min_stay >= 1),
            CONSTRAINT fk_rcd_room_type
                FOREIGN KEY (property_id, room_type_id)
                REFERENCES property.room_types (property_id, id) ON DELETE CASCADE
        )
        """
    )
    # The screen always reads a date window for a property.
    op.execute(
        "CREATE INDEX ix_rcd_property_date "
        "ON property.rate_calendar_days (property_id, stay_date)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS property.rate_calendar_days")
