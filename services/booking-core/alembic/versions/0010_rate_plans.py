"""Rate plans and meal plans (screen 033)

Revision ID: 0010_rate_plans
Revises: 0009_room_status_history
Create Date: 2026-09-08

Pricing had exactly one dimension: ``room_types.base_rate``, optionally
overridden per room. There was no way to sell the same room at a different
price under different terms — refundable or not, with or without breakfast, to
a corporate account — which is what a rate plan is.

A rate plan does not store its own nightly price. It stores an *adjustment* to
the room type's base rate plus the terms that go with it, so changing a room
type's rate moves every plan built on it and the two can never drift apart. The
"from" price the screen shows is therefore computed, never stored.

``rate_plan_room_types`` limits a plan to particular room types; **no rows means
the plan applies to every room type**, which is the common case and avoids
having to re-link every plan whenever a room type is added.

Meal plans are a small master in their own right because the rate plan form
selects one, and because the same list is needed by rate rules and packages
later. The five seeded here are the standard hotel board bases.
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0010_rate_plans"
down_revision: str | None = "0009_room_status_history"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

PLAN_TYPES = ("standard", "corporate", "package")
ADJUSTMENT_DIRECTIONS = ("increase", "decrease")
ADJUSTMENT_TYPES = ("percent", "amount")
STATUSES = ("active", "inactive")

# Board bases every property uses; seeded so the rate plan form is never empty.
MEAL_PLANS = [
    ("RO", "Room Only", "No meals included.", 1),
    ("BB", "Breakfast", "Breakfast included for all occupants.", 2),
    ("HB", "Half Board", "Breakfast and one further meal included.", 3),
    ("FB", "Full Board", "Breakfast, lunch and dinner included.", 4),
    ("AI", "All Inclusive", "All meals, snacks and selected beverages.", 5),
]

# The plans shown on mockup 033, seeded as demo data alongside the existing
# seeded rooms and reservations.
RATE_PLANS = [
    # code, name, description, meal, dir, type, value, extra_adult, child,
    # min_stay, refundable, free_cancel_hours, policy, plan_type, corporate
    ("BAR", "Best Available Rate", "Our standard flexible rate with the best available price.",
     "RO", "increase", "percent", 0, 1500, 800, 1, True, 24,
     "Fully refundable until 24 hours before arrival.", "standard", None),
    ("BB", "Breakfast Included", "Comfortable stay with delicious breakfast included.",
     "BB", "increase", "percent", 15, 1500, 800, 1, True, 24,
     "Fully refundable until 24 hours before arrival.", "standard", None),
    ("FLEX", "Flexible", "Change or cancel your plans with ease.",
     "RO", "increase", "percent", 8, 1500, 800, 1, True, 24,
     "Fully refundable until 24 hours before arrival.", "standard", None),
    ("NRF", "Non-refundable", "Save more with our non-refundable rate.",
     "RO", "decrease", "percent", 15, 1200, 600, 1, False, None,
     "No refund once booked.", "standard", None),
    ("CORP", "Corporate BlueWave", "Special rates for our corporate partners.",
     "BB", "decrease", "percent", 8, 1200, 700, 1, True, 48,
     "Fully refundable until 48 hours before arrival.", "corporate", "BlueWave Technologies"),
]


def upgrade() -> None:
    quoted = lambda values: ", ".join(f"'{v}'" for v in values)  # noqa: E731

    op.execute(
        """
        CREATE TABLE property.meal_plans (
            id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            organization_id uuid NOT NULL,
            property_id     uuid NOT NULL,
            code            varchar(10) NOT NULL,
            name            varchar(80) NOT NULL,
            description     varchar(300),
            display_order   integer NOT NULL DEFAULT 0,
            status          varchar(20) NOT NULL DEFAULT 'active',
            created_by      uuid,
            updated_by      uuid,
            created_at      timestamptz NOT NULL DEFAULT now(),
            updated_at      timestamptz NOT NULL DEFAULT now(),
            version         bigint NOT NULL DEFAULT 0,
            CONSTRAINT ck_meal_plan_status CHECK (status IN ('active', 'inactive')),
            CONSTRAINT uq_meal_plan_code UNIQUE (property_id, code)
        )
        """
    )
    # Lets rate_plans reference a meal plan with a composite FK, so a plan can
    # never point at another property's meal plan (§1).
    op.execute(
        "ALTER TABLE property.meal_plans "
        "ADD CONSTRAINT uq_meal_plan_property_id UNIQUE (property_id, id)"
    )

    op.execute(
        f"""
        CREATE TABLE property.rate_plans (
            id                   uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            organization_id      uuid NOT NULL,
            property_id          uuid NOT NULL,
            code                 varchar(12) NOT NULL,
            name                 varchar(120) NOT NULL,
            description          varchar(500),
            plan_type            varchar(20) NOT NULL DEFAULT 'standard',
            corporate_account    varchar(120),
            meal_plan_id         uuid,
            adjustment_direction varchar(10) NOT NULL DEFAULT 'increase',
            adjustment_type      varchar(10) NOT NULL DEFAULT 'percent',
            adjustment_value     numeric(12, 2) NOT NULL DEFAULT 0,
            extra_adult_charge   numeric(12, 2),
            child_charge         numeric(12, 2),
            min_stay             integer NOT NULL DEFAULT 1,
            min_guests           integer NOT NULL DEFAULT 1,
            max_guests           integer NOT NULL DEFAULT 2,
            refundable           boolean NOT NULL DEFAULT true,
            free_cancellation_hours integer,
            cancellation_policy  varchar(300),
            image_url            text,
            image_storage_key    varchar(400),
            status               varchar(20) NOT NULL DEFAULT 'active',
            created_by           uuid,
            updated_by           uuid,
            created_at           timestamptz NOT NULL DEFAULT now(),
            updated_at           timestamptz NOT NULL DEFAULT now(),
            version              bigint NOT NULL DEFAULT 0,
            CONSTRAINT uq_rate_plan_code UNIQUE (property_id, code),
            CONSTRAINT ck_rate_plan_type CHECK (plan_type IN ({quoted(PLAN_TYPES)})),
            CONSTRAINT ck_rate_plan_status CHECK (status IN ({quoted(STATUSES)})),
            CONSTRAINT ck_rate_plan_direction
                CHECK (adjustment_direction IN ({quoted(ADJUSTMENT_DIRECTIONS)})),
            CONSTRAINT ck_rate_plan_adj_type
                CHECK (adjustment_type IN ({quoted(ADJUSTMENT_TYPES)})),
            CONSTRAINT ck_rate_plan_adj_value CHECK (adjustment_value >= 0),
            -- A percentage discount of more than 100% would invert the price.
            CONSTRAINT ck_rate_plan_percent_cap CHECK (
                adjustment_type <> 'percent'
                OR adjustment_direction <> 'decrease'
                OR adjustment_value <= 100
            ),
            CONSTRAINT ck_rate_plan_min_stay CHECK (min_stay >= 1),
            CONSTRAINT ck_rate_plan_guests
                CHECK (min_guests >= 1 AND max_guests >= min_guests),
            CONSTRAINT ck_rate_plan_charges CHECK (
                (extra_adult_charge IS NULL OR extra_adult_charge >= 0)
                AND (child_charge IS NULL OR child_charge >= 0)
            ),
            -- Free-cancellation hours only mean something on a refundable plan.
            CONSTRAINT ck_rate_plan_cancellation CHECK (
                refundable OR free_cancellation_hours IS NULL
            ),
            CONSTRAINT ck_rate_plan_corporate CHECK (
                plan_type <> 'corporate' OR corporate_account IS NOT NULL
            ),
            CONSTRAINT fk_rate_plan_meal_plan
                FOREIGN KEY (property_id, meal_plan_id)
                REFERENCES property.meal_plans (property_id, id)
        )
        """
    )
    op.execute(
        "ALTER TABLE property.rate_plans "
        "ADD CONSTRAINT uq_rate_plan_property_id UNIQUE (property_id, id)"
    )
    op.execute(
        "CREATE UNIQUE INDEX uq_rate_plans_image_key "
        "ON property.rate_plans (image_storage_key) "
        "WHERE image_storage_key IS NOT NULL"
    )
    op.execute(
        "CREATE INDEX ix_rate_plans_property "
        "ON property.rate_plans (property_id, status, name)"
    )

    # No rows for a plan means "every room type" — see the module docstring.
    op.execute(
        """
        CREATE TABLE property.rate_plan_room_types (
            rate_plan_id uuid NOT NULL,
            room_type_id uuid NOT NULL,
            property_id  uuid NOT NULL,
            PRIMARY KEY (rate_plan_id, room_type_id),
            CONSTRAINT fk_rprt_plan
                FOREIGN KEY (property_id, rate_plan_id)
                REFERENCES property.rate_plans (property_id, id) ON DELETE CASCADE,
            CONSTRAINT fk_rprt_room_type
                FOREIGN KEY (property_id, room_type_id)
                REFERENCES property.room_types (property_id, id) ON DELETE CASCADE
        )
        """
    )
    op.execute(
        "CREATE INDEX ix_rprt_room_type ON property.rate_plan_room_types (room_type_id)"
    )

    # ---------------- seed ----------------------------------------------------
    # Applied per property that already exists, so every tenant gets the board
    # bases without a second migration.
    for code, name, description, order in [
        (m[0], m[1], m[2], m[3]) for m in MEAL_PLANS
    ]:
        op.execute(
            f"""
            INSERT INTO property.meal_plans
                (organization_id, property_id, code, name, description, display_order)
            SELECT p.organization_id, p.id, '{code}', $${name}$$, $${description}$$, {order}
            FROM iam.properties p
            ON CONFLICT (property_id, code) DO NOTHING
            """
        )

    for (code, name, description, meal, direction, adj_type, value, extra_adult,
         child, min_stay, refundable, free_hours, policy, plan_type,
         corporate) in RATE_PLANS:
        op.execute(
            f"""
            INSERT INTO property.rate_plans
                (organization_id, property_id, code, name, description, plan_type,
                 corporate_account, meal_plan_id, adjustment_direction,
                 adjustment_type, adjustment_value, extra_adult_charge,
                 child_charge, min_stay, min_guests, max_guests, refundable,
                 free_cancellation_hours, cancellation_policy)
            SELECT p.organization_id, p.id, '{code}', $${name}$$, $${description}$$,
                   '{plan_type}',
                   {f"$${corporate}$$" if corporate else "NULL"},
                   (SELECT mp.id FROM property.meal_plans mp
                     WHERE mp.property_id = p.id AND mp.code = '{meal}'),
                   '{direction}', '{adj_type}', {value},
                   {extra_adult}, {child}, {min_stay}, 1, 2,
                   {str(refundable).lower()},
                   {free_hours if free_hours is not None else "NULL"},
                   $${policy}$$
            FROM iam.properties p
            ON CONFLICT (property_id, code) DO NOTHING
            """
        )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS property.rate_plan_room_types")
    op.execute("DROP TABLE IF EXISTS property.rate_plans")
    op.execute("DROP TABLE IF EXISTS property.meal_plans")
