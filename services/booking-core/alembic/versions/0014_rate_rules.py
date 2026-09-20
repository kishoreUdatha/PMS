"""Rate rules (screen 119)

Revision ID: 0014_rate_rules
Revises: 0013_flat_rate_pricing
Create Date: 2026-09-09

Pricing so far has been things a person types: a room type's base rate, a date
overridden on the calendar, a plan's adjustment. A rate rule is the standing
instruction that does it automatically — "15% off Mon–Thu in September for
advance bookings of 7+ days".

Rules are **layered, not exclusive**. Several can touch the same night, so
``priority`` decides the order they apply in, lowest number first, which is what
the mockup means by "Lower number = higher priority". Two published rules with
the same priority over the same dates and room types is a genuine conflict, and
the screen has a tab for exactly that — so the schema records enough to detect
it rather than leaving it to whoever notices a wrong price.

``weekdays`` carries the "Selected Days (Mon-Thu)" / "Weekends (Fri-Sat)" part
as ISO weekday numbers, 1=Monday. An empty array means every day, which keeps
the common case free of rows to maintain.

Status is a lifecycle, not a flag: draft -> scheduled -> published, with paused
and inactive as ways to stop it without deleting the history of what was sold.
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0014_rate_rules"
down_revision: str | None = "0013_flat_rate_pricing"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

RULE_TYPES = ("derived_adjustment", "fixed_rate")
STATUSES = ("draft", "scheduled", "published", "paused", "inactive")
APPLICABLE_FOR = ("all_rate_plans", "specific_rate_plans")


def upgrade() -> None:
    quoted = lambda values: ", ".join(f"'{v}'" for v in values)  # noqa: E731

    op.execute(
        f"""
        CREATE TABLE property.rate_rules (
            id                   uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            organization_id      uuid NOT NULL,
            property_id          uuid NOT NULL,
            name                 varchar(100) NOT NULL,
            description          varchar(500),
            rule_type            varchar(30) NOT NULL DEFAULT 'derived_adjustment',
            status               varchar(20) NOT NULL DEFAULT 'draft',
            -- Lowest number wins, per the screen's own wording.
            priority             integer NOT NULL DEFAULT 10,
            applicable_for       varchar(30) NOT NULL DEFAULT 'all_rate_plans',
            date_from            date NOT NULL,
            date_to              date NOT NULL,
            -- ISO weekday numbers, 1=Mon. Empty means every day.
            weekdays             integer[] NOT NULL DEFAULT '{{}}',
            adjustment_direction varchar(10) NOT NULL DEFAULT 'decrease',
            adjustment_type      varchar(10) NOT NULL DEFAULT 'percent',
            adjustment_value     numeric(12, 2) NOT NULL DEFAULT 0,
            fixed_rate           numeric(12, 2),
            min_stay             integer,
            max_stay             integer,
            advance_days_min     integer,
            advance_days_max     integer,
            closed_to_arrival    boolean NOT NULL DEFAULT false,
            closed_to_departure  boolean NOT NULL DEFAULT false,
            stop_sell            boolean NOT NULL DEFAULT false,
            channel_scope        varchar(30) NOT NULL DEFAULT 'all',
            created_by           uuid,
            updated_by           uuid,
            created_at           timestamptz NOT NULL DEFAULT now(),
            updated_at           timestamptz NOT NULL DEFAULT now(),
            version              bigint NOT NULL DEFAULT 0,
            CONSTRAINT uq_rate_rule_name UNIQUE (property_id, name),
            CONSTRAINT uq_rate_rule_property_id UNIQUE (property_id, id),
            CONSTRAINT ck_rr_type CHECK (rule_type IN ({quoted(RULE_TYPES)})),
            CONSTRAINT ck_rr_status CHECK (status IN ({quoted(STATUSES)})),
            CONSTRAINT ck_rr_applicable CHECK (
                applicable_for IN ({quoted(APPLICABLE_FOR)})
            ),
            CONSTRAINT ck_rr_dates CHECK (date_to >= date_from),
            CONSTRAINT ck_rr_priority CHECK (priority >= 0),
            CONSTRAINT ck_rr_direction
                CHECK (adjustment_direction IN ('increase', 'decrease')),
            CONSTRAINT ck_rr_adj_type CHECK (adjustment_type IN ('percent', 'amount')),
            CONSTRAINT ck_rr_adj_value CHECK (adjustment_value >= 0),
            CONSTRAINT ck_rr_percent_cap CHECK (
                adjustment_type <> 'percent'
                OR adjustment_direction <> 'decrease'
                OR adjustment_value <= 100
            ),
            -- A fixed-rate rule needs the rate; an adjustment must not carry one.
            CONSTRAINT ck_rr_fixed CHECK (
                (rule_type = 'fixed_rate' AND fixed_rate IS NOT NULL AND fixed_rate >= 0)
                OR (rule_type = 'derived_adjustment' AND fixed_rate IS NULL)
            ),
            CONSTRAINT ck_rr_stay CHECK (
                (min_stay IS NULL OR min_stay >= 1)
                AND (max_stay IS NULL OR max_stay >= 1)
                AND (min_stay IS NULL OR max_stay IS NULL OR max_stay >= min_stay)
            ),
            CONSTRAINT ck_rr_advance CHECK (
                (advance_days_min IS NULL OR advance_days_min >= 0)
                AND (advance_days_max IS NULL OR advance_days_max >= 0)
                AND (advance_days_min IS NULL OR advance_days_max IS NULL
                     OR advance_days_max >= advance_days_min)
            ),
            CONSTRAINT ck_rr_weekdays CHECK (
                weekdays <@ ARRAY[1, 2, 3, 4, 5, 6, 7]
            )
        )
        """
    )
    op.execute(
        "CREATE INDEX ix_rate_rules_window "
        "ON property.rate_rules (property_id, status, date_from, date_to)"
    )
    op.execute(
        "CREATE INDEX ix_rate_rules_priority "
        "ON property.rate_rules (property_id, priority)"
    )

    # No rows means "every one", the convention rate plans and packages use.
    op.execute(
        """
        CREATE TABLE property.rate_rule_room_types (
            rate_rule_id uuid NOT NULL,
            room_type_id uuid NOT NULL,
            property_id  uuid NOT NULL,
            PRIMARY KEY (rate_rule_id, room_type_id),
            CONSTRAINT fk_rrrt_rule
                FOREIGN KEY (property_id, rate_rule_id)
                REFERENCES property.rate_rules (property_id, id) ON DELETE CASCADE,
            CONSTRAINT fk_rrrt_room_type
                FOREIGN KEY (property_id, room_type_id)
                REFERENCES property.room_types (property_id, id) ON DELETE CASCADE
        )
        """
    )
    op.execute(
        """
        CREATE TABLE property.rate_rule_rate_plans (
            rate_rule_id uuid NOT NULL,
            rate_plan_id uuid NOT NULL,
            property_id  uuid NOT NULL,
            PRIMARY KEY (rate_rule_id, rate_plan_id),
            CONSTRAINT fk_rrrp_rule
                FOREIGN KEY (property_id, rate_rule_id)
                REFERENCES property.rate_rules (property_id, id) ON DELETE CASCADE,
            CONSTRAINT fk_rrrp_rate_plan
                FOREIGN KEY (property_id, rate_plan_id)
                REFERENCES property.rate_plans (property_id, id) ON DELETE CASCADE
        )
        """
    )
    op.execute(
        "CREATE INDEX ix_rrrt_room_type ON property.rate_rule_room_types (room_type_id)"
    )
    op.execute(
        "CREATE INDEX ix_rrrp_rate_plan ON property.rate_rule_rate_plans (rate_plan_id)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS property.rate_rule_rate_plans")
    op.execute("DROP TABLE IF EXISTS property.rate_rule_room_types")
    op.execute("DROP TABLE IF EXISTS property.rate_rules")
