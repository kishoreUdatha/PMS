"""Booking core: room types, rooms, inventory days, reservations, holds, calendar

Revision ID: 0001_booking_core
Revises:
Create Date: 2026-09-06

Implements the schema §4 booking/inventory core plus the shared
integration.outbox_events table (§10). Key concurrency structures:
  - booking.room_type_inventory_days: serialized inventory authority, locked
    FOR UPDATE per night during holds.
  - booking.room_calendar_entries: GiST exclusion constraint prevents two active
    entries (reservation OR maintenance) overlapping on the same physical room.
Requires extensions btree_gist + pgcrypto (created in init or below).
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0001_booking_core"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("CREATE SCHEMA IF NOT EXISTS property")
    op.execute("CREATE SCHEMA IF NOT EXISTS booking")
    op.execute("CREATE SCHEMA IF NOT EXISTS integration")
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")
    op.execute("CREATE EXTENSION IF NOT EXISTS btree_gist")

    # ---- property.room_types ----
    op.execute(
        """
        CREATE TABLE property.room_types (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            organization_id uuid NOT NULL,
            property_id uuid NOT NULL,
            code varchar(30) NOT NULL,
            name varchar(150) NOT NULL,
            max_adults int NOT NULL DEFAULT 2,
            max_children int NOT NULL DEFAULT 0,
            max_occupancy int NOT NULL DEFAULT 2,
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now(),
            version bigint NOT NULL DEFAULT 0,
            CONSTRAINT uq_room_type_property_code UNIQUE (property_id, code),
            CONSTRAINT uq_room_type_property_id UNIQUE (property_id, id),
            CONSTRAINT ck_room_type_capacity CHECK (max_occupancy >= 1)
        )
        """
    )

    # ---- property.rooms ----
    op.execute(
        """
        CREATE TABLE property.rooms (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            organization_id uuid NOT NULL,
            property_id uuid NOT NULL,
            room_type_id uuid NOT NULL,
            code varchar(30) NOT NULL,
            active_from date,
            retired_on date,
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now(),
            version bigint NOT NULL DEFAULT 0,
            CONSTRAINT uq_room_property_code UNIQUE (property_id, code),
            CONSTRAINT fk_room_room_type
                FOREIGN KEY (property_id, room_type_id)
                REFERENCES property.room_types (property_id, id)
        )
        """
    )

    # ---- booking.room_type_inventory_days (inventory authority, §4) ----
    op.execute(
        """
        CREATE TABLE booking.room_type_inventory_days (
            organization_id uuid NOT NULL,
            property_id uuid NOT NULL,
            room_type_id uuid NOT NULL,
            stay_date date NOT NULL,
            physical_capacity int NOT NULL DEFAULT 0,
            out_of_service int NOT NULL DEFAULT 0,
            held_units int NOT NULL DEFAULT 0,
            reserved_units int NOT NULL DEFAULT 0,
            allotment_units int NOT NULL DEFAULT 0,
            PRIMARY KEY (property_id, room_type_id, stay_date),
            CONSTRAINT ck_inv_nonneg CHECK (
                physical_capacity >= 0 AND out_of_service >= 0
                AND held_units >= 0 AND reserved_units >= 0
                AND allotment_units >= 0
            )
        )
        """
    )

    # ---- booking.reservations ----
    op.execute(
        """
        CREATE TABLE booking.reservations (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            organization_id uuid NOT NULL,
            property_id uuid NOT NULL,
            number varchar(30) NOT NULL,
            status varchar(20) NOT NULL DEFAULT 'draft',
            currency varchar(3) NOT NULL DEFAULT 'INR',
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now(),
            version bigint NOT NULL DEFAULT 0,
            CONSTRAINT uq_reservation_property_number UNIQUE (property_id, number),
            CONSTRAINT uq_reservation_property_id UNIQUE (property_id, id),
            CONSTRAINT ck_reservation_status CHECK (
                status IN ('draft','held','confirmed','cancelled','completed')
            )
        )
        """
    )

    # ---- booking.reservation_units ----
    op.execute(
        """
        CREATE TABLE booking.reservation_units (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            organization_id uuid NOT NULL,
            property_id uuid NOT NULL,
            reservation_id uuid NOT NULL,
            room_type_id uuid NOT NULL,
            arrival_date date NOT NULL,
            departure_date date NOT NULL,
            adults int NOT NULL DEFAULT 1,
            children int NOT NULL DEFAULT 0,
            status varchar(20) NOT NULL DEFAULT 'reserved',
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now(),
            version bigint NOT NULL DEFAULT 0,
            CONSTRAINT fk_unit_reservation
                FOREIGN KEY (property_id, reservation_id)
                REFERENCES booking.reservations (property_id, id),
            CONSTRAINT ck_unit_dates CHECK (departure_date > arrival_date),
            CONSTRAINT ck_unit_status CHECK (
                status IN ('reserved','checked_in','checked_out','cancelled','no_show')
            )
        )
        """
    )

    # ---- booking.booking_holds ----
    op.execute(
        """
        CREATE TABLE booking.booking_holds (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            organization_id uuid NOT NULL,
            property_id uuid NOT NULL,
            reservation_id uuid NOT NULL,
            status varchar(20) NOT NULL DEFAULT 'held',
            idempotency_key varchar(200) NOT NULL,
            expires_at timestamptz NOT NULL,
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT uq_hold_idempotency UNIQUE (idempotency_key),
            CONSTRAINT ck_hold_status CHECK (
                status IN ('held','converted','expired','released')
            )
        )
        """
    )

    # ---- booking.room_calendar_entries with GiST exclusion (§4) ----
    # One shared collision authority for reservation allocations AND maintenance
    # blocks. No two active entries may overlap on the same physical room.
    op.execute(
        """
        CREATE TABLE booking.room_calendar_entries (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            organization_id uuid NOT NULL,
            property_id uuid NOT NULL,
            room_id uuid NOT NULL,
            reservation_unit_id uuid,
            kind varchar(20) NOT NULL,
            occupied_period tstzrange NOT NULL,
            status varchar(20) NOT NULL DEFAULT 'active',
            reason varchar(300),
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now(),
            version bigint NOT NULL DEFAULT 0,
            CONSTRAINT ck_calendar_kind CHECK (kind IN ('reservation','maintenance')),
            CONSTRAINT ck_calendar_status CHECK (status IN ('active','released')),
            CONSTRAINT excl_room_calendar_overlap EXCLUDE USING gist (
                organization_id WITH =,
                property_id WITH =,
                room_id WITH =,
                occupied_period WITH &&
            ) WHERE (status = 'active')
        )
        """
    )

    # ---- integration.outbox_events (§10) ----
    # IF NOT EXISTS: on the shared cluster the finance migration may create this
    # table first when services start in parallel; the guard keeps both safe.
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS integration.outbox_events (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            aggregate_type varchar(80) NOT NULL,
            aggregate_id varchar(80) NOT NULL,
            event_type varchar(120) NOT NULL,
            payload jsonb NOT NULL,
            occurred_at timestamptz NOT NULL DEFAULT now(),
            published_at timestamptz
        )
        """
    )

    # ---- indexes (§12) ----
    op.execute(
        "CREATE INDEX ix_rooms_room_type "
        "ON property.rooms(property_id, room_type_id)"
    )
    op.execute(
        "CREATE INDEX ix_units_res "
        "ON booking.reservation_units(property_id, reservation_id)"
    )
    op.execute(
        "CREATE INDEX ix_units_arrival "
        "ON booking.reservation_units(property_id, arrival_date, status)"
    )
    op.execute(
        "CREATE INDEX ix_units_departure "
        "ON booking.reservation_units(property_id, departure_date, status)"
    )
    op.execute(
        "CREATE INDEX ix_holds_res ON booking.booking_holds(reservation_id)"
    )
    # Partial index for expiring active holds (avoid now() in predicate, §4).
    op.execute(
        "CREATE INDEX ix_holds_active_expiry "
        "ON booking.booking_holds(expires_at) WHERE status = 'held'"
    )
    op.execute(
        "CREATE INDEX ix_calendar_room "
        "ON booking.room_calendar_entries(property_id, room_id)"
    )
    # Relay picks up unpublished events.
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_outbox_unpublished "
        "ON integration.outbox_events(occurred_at) WHERE published_at IS NULL"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS integration.outbox_events CASCADE")
    op.execute("DROP TABLE IF EXISTS booking.room_calendar_entries CASCADE")
    op.execute("DROP TABLE IF EXISTS booking.booking_holds CASCADE")
    op.execute("DROP TABLE IF EXISTS booking.reservation_units CASCADE")
    op.execute("DROP TABLE IF EXISTS booking.reservations CASCADE")
    op.execute("DROP TABLE IF EXISTS booking.room_type_inventory_days CASCADE")
    op.execute("DROP TABLE IF EXISTS property.rooms CASCADE")
    op.execute("DROP TABLE IF EXISTS property.room_types CASCADE")
