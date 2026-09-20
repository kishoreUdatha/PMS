"""Packages, add-ons and promo codes (screen 035)

Revision ID: 0012_packages_promotions
Revises: 0011_rate_calendar
Create Date: 2026-09-08

A rate plan sells a room on different terms. A **package** sells a room plus
things that are not the room — breakfast, a spa credit, a late checkout — as one
marketed offer with its own name, artwork and validity window. A **promo code**
is a discount someone types in. Neither existed.

Pricing deliberately reuses the rate plan model: a package holds an *adjustment*
to the room type's base rate, not a price of its own, so the headline "from"
figure is computed and a change to a room type's rate moves every package built
on it. One pricing model, one place it can be wrong.

Inclusions are rows rather than a text blob because the screen lists them
individually and because each one may point at a real add-on. An inclusion can
also be free text ("Late check-out (2 PM)"), which is not something the property
stocks and sells, so ``addon_id`` is nullable.

``booking.reservation_units.package_id`` is added so a package's bookings and
revenue can be **derived** rather than typed in. Nothing sets it yet — the
booking flow does not offer packages — so those figures read zero today, which
is the truth rather than a decorative number.
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0012_packages_promotions"
down_revision: str | None = "0011_rate_calendar"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

ADDON_CATEGORIES = (
    "dining", "spa", "transport", "activity", "room_service", "other",
)
PRICING_UNITS = ("per_stay", "per_night", "per_person", "per_person_per_night")
DISCOUNT_TYPES = ("percent", "amount")
STATUSES = ("active", "inactive")


def upgrade() -> None:
    quoted = lambda values: ", ".join(f"'{v}'" for v in values)  # noqa: E731

    # ---------------------------------------------------------------- add-ons
    op.execute(
        f"""
        CREATE TABLE property.addons (
            id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            organization_id uuid NOT NULL,
            property_id     uuid NOT NULL,
            code            varchar(20) NOT NULL,
            name            varchar(120) NOT NULL,
            description     varchar(400),
            category        varchar(30) NOT NULL DEFAULT 'other',
            price           numeric(12, 2) NOT NULL DEFAULT 0,
            pricing_unit    varchar(30) NOT NULL DEFAULT 'per_stay',
            status          varchar(20) NOT NULL DEFAULT 'active',
            created_by      uuid,
            updated_by      uuid,
            created_at      timestamptz NOT NULL DEFAULT now(),
            updated_at      timestamptz NOT NULL DEFAULT now(),
            version         bigint NOT NULL DEFAULT 0,
            CONSTRAINT uq_addon_code UNIQUE (property_id, code),
            CONSTRAINT uq_addon_property_id UNIQUE (property_id, id),
            CONSTRAINT ck_addon_category CHECK (category IN ({quoted(ADDON_CATEGORIES)})),
            CONSTRAINT ck_addon_unit CHECK (pricing_unit IN ({quoted(PRICING_UNITS)})),
            CONSTRAINT ck_addon_status CHECK (status IN ({quoted(STATUSES)})),
            CONSTRAINT ck_addon_price CHECK (price >= 0)
        )
        """
    )

    # --------------------------------------------------------------- packages
    op.execute(
        f"""
        CREATE TABLE property.packages (
            id                   uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            organization_id      uuid NOT NULL,
            property_id          uuid NOT NULL,
            code                 varchar(20) NOT NULL,
            name                 varchar(120) NOT NULL,
            tagline              varchar(200),
            description          varchar(1000),
            badge                varchar(40),
            valid_from           date NOT NULL,
            valid_to             date NOT NULL,
            adjustment_direction varchar(10) NOT NULL DEFAULT 'increase',
            adjustment_type      varchar(10) NOT NULL DEFAULT 'percent',
            adjustment_value     numeric(12, 2) NOT NULL DEFAULT 0,
            min_nights           integer NOT NULL DEFAULT 1,
            max_nights           integer,
            is_featured          boolean NOT NULL DEFAULT true,
            image_url            text,
            image_storage_key    varchar(400),
            status               varchar(20) NOT NULL DEFAULT 'active',
            created_by           uuid,
            updated_by           uuid,
            created_at           timestamptz NOT NULL DEFAULT now(),
            updated_at           timestamptz NOT NULL DEFAULT now(),
            version              bigint NOT NULL DEFAULT 0,
            CONSTRAINT uq_package_code UNIQUE (property_id, code),
            CONSTRAINT uq_package_property_id UNIQUE (property_id, id),
            CONSTRAINT ck_package_status CHECK (status IN ({quoted(STATUSES)})),
            CONSTRAINT ck_package_dates CHECK (valid_to >= valid_from),
            CONSTRAINT ck_package_direction
                CHECK (adjustment_direction IN ('increase', 'decrease')),
            CONSTRAINT ck_package_adj_type
                CHECK (adjustment_type IN ('percent', 'amount')),
            CONSTRAINT ck_package_adj_value CHECK (adjustment_value >= 0),
            -- A percentage discount over 100% would invert the price.
            CONSTRAINT ck_package_percent_cap CHECK (
                adjustment_type <> 'percent'
                OR adjustment_direction <> 'decrease'
                OR adjustment_value <= 100
            ),
            CONSTRAINT ck_package_nights CHECK (
                min_nights >= 1 AND (max_nights IS NULL OR max_nights >= min_nights)
            )
        )
        """
    )
    op.execute(
        "CREATE UNIQUE INDEX uq_packages_image_key "
        "ON property.packages (image_storage_key) WHERE image_storage_key IS NOT NULL"
    )
    op.execute(
        "CREATE INDEX ix_packages_property "
        "ON property.packages (property_id, status, valid_from)"
    )

    # No rows for a package means it applies to every room type, matching the
    # convention rate plans already use.
    op.execute(
        """
        CREATE TABLE property.package_room_types (
            package_id   uuid NOT NULL,
            room_type_id uuid NOT NULL,
            property_id  uuid NOT NULL,
            PRIMARY KEY (package_id, room_type_id),
            CONSTRAINT fk_prt_package
                FOREIGN KEY (property_id, package_id)
                REFERENCES property.packages (property_id, id) ON DELETE CASCADE,
            CONSTRAINT fk_prt_room_type
                FOREIGN KEY (property_id, room_type_id)
                REFERENCES property.room_types (property_id, id) ON DELETE CASCADE
        )
        """
    )

    # An inclusion is a line on the card. It may name a real add-on, or be
    # something the property does not stock ("Late check-out"), hence nullable.
    op.execute(
        """
        CREATE TABLE property.package_inclusions (
            id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            package_id  uuid NOT NULL,
            property_id uuid NOT NULL,
            addon_id    uuid,
            label       varchar(200) NOT NULL,
            sort_order  integer NOT NULL DEFAULT 0,
            CONSTRAINT fk_pi_package
                FOREIGN KEY (property_id, package_id)
                REFERENCES property.packages (property_id, id) ON DELETE CASCADE,
            CONSTRAINT fk_pi_addon
                FOREIGN KEY (property_id, addon_id)
                REFERENCES property.addons (property_id, id) ON DELETE SET NULL
        )
        """
    )
    op.execute(
        "CREATE INDEX ix_package_inclusions_package "
        "ON property.package_inclusions (package_id, sort_order)"
    )

    # ------------------------------------------------------------ promo codes
    op.execute(
        f"""
        CREATE TABLE property.promo_codes (
            id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            organization_id uuid NOT NULL,
            property_id     uuid NOT NULL,
            code            varchar(30) NOT NULL,
            description     varchar(300),
            discount_type   varchar(10) NOT NULL DEFAULT 'percent',
            discount_value  numeric(12, 2) NOT NULL,
            usage_limit     integer,
            usage_count     integer NOT NULL DEFAULT 0,
            valid_from      date NOT NULL,
            valid_to        date NOT NULL,
            min_nights      integer,
            package_id      uuid,
            status          varchar(20) NOT NULL DEFAULT 'active',
            created_by      uuid,
            updated_by      uuid,
            created_at      timestamptz NOT NULL DEFAULT now(),
            updated_at      timestamptz NOT NULL DEFAULT now(),
            version         bigint NOT NULL DEFAULT 0,
            CONSTRAINT uq_promo_code UNIQUE (property_id, code),
            CONSTRAINT uq_promo_property_id UNIQUE (property_id, id),
            CONSTRAINT ck_promo_type CHECK (discount_type IN ({quoted(DISCOUNT_TYPES)})),
            CONSTRAINT ck_promo_status CHECK (status IN ({quoted(STATUSES)})),
            CONSTRAINT ck_promo_dates CHECK (valid_to >= valid_from),
            CONSTRAINT ck_promo_value CHECK (discount_value > 0),
            CONSTRAINT ck_promo_percent_cap CHECK (
                discount_type <> 'percent' OR discount_value <= 100
            ),
            CONSTRAINT ck_promo_limit CHECK (usage_limit IS NULL OR usage_limit >= 0),
            -- Redemptions only ever increment, and never past the limit.
            CONSTRAINT ck_promo_usage CHECK (
                usage_count >= 0
                AND (usage_limit IS NULL OR usage_count <= usage_limit)
            ),
            CONSTRAINT ck_promo_min_nights CHECK (min_nights IS NULL OR min_nights >= 1),
            CONSTRAINT fk_promo_package
                FOREIGN KEY (property_id, package_id)
                REFERENCES property.packages (property_id, id) ON DELETE SET NULL
        )
        """
    )
    op.execute(
        "CREATE INDEX ix_promo_codes_property "
        "ON property.promo_codes (property_id, status, valid_to)"
    )

    # The redemption log backs the Redemption tab. usage_count on the code is a
    # counter for the limit check; this is the evidence behind it.
    op.execute(
        """
        CREATE TABLE property.promo_redemptions (
            id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            organization_id uuid NOT NULL,
            property_id     uuid NOT NULL,
            promo_code_id   uuid NOT NULL,
            reservation_id  uuid,
            guest_name      varchar(200),
            discount_amount numeric(12, 2),
            redeemed_by     uuid,
            redeemed_at     timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT fk_pr_code
                FOREIGN KEY (property_id, promo_code_id)
                REFERENCES property.promo_codes (property_id, id) ON DELETE CASCADE
        )
        """
    )
    op.execute(
        "CREATE INDEX ix_promo_redemptions_code "
        "ON property.promo_redemptions (promo_code_id, redeemed_at DESC)"
    )
    op.execute(
        "CREATE INDEX ix_promo_redemptions_property "
        "ON property.promo_redemptions (property_id, redeemed_at DESC)"
    )

    # ------------------------------------------- link bookings to packages
    op.execute("ALTER TABLE booking.reservation_units ADD COLUMN package_id uuid")
    op.execute(
        "ALTER TABLE booking.reservation_units ADD CONSTRAINT fk_unit_package "
        "FOREIGN KEY (property_id, package_id) "
        "REFERENCES property.packages (property_id, id) ON DELETE SET NULL"
    )
    op.execute(
        "CREATE INDEX ix_reservation_units_package "
        "ON booking.reservation_units (package_id) WHERE package_id IS NOT NULL"
    )


def downgrade() -> None:
    op.execute(
        "ALTER TABLE booking.reservation_units "
        "DROP CONSTRAINT IF EXISTS fk_unit_package"
    )
    op.execute("ALTER TABLE booking.reservation_units DROP COLUMN IF EXISTS package_id")
    op.execute("DROP TABLE IF EXISTS property.promo_redemptions")
    op.execute("DROP TABLE IF EXISTS property.promo_codes")
    op.execute("DROP TABLE IF EXISTS property.package_inclusions")
    op.execute("DROP TABLE IF EXISTS property.package_room_types")
    op.execute("DROP TABLE IF EXISTS property.packages")
    op.execute("DROP TABLE IF EXISTS property.addons")
