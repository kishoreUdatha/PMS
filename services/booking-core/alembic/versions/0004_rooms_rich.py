"""Rich room attributes, amenities and photos (screens 008 / 059 / 060 / 062)

Revision ID: 0004_rooms_rich
Revises: 0003_guests
Create Date: 2026-09-08

property.rooms started as the bare minimum the inventory path needed (code +
room type). The Rooms & Villas Inventory and Add/Edit Room screens need the
physical and merchandising attributes as well: where the room is (building,
floor, housekeeping zone), how it is laid out (bed setup, view, occupancy),
what it sells for, whether it is currently sellable, and its amenities and
photos.

Design notes:
- ``status`` is the *administrative* lifecycle (active / inactive / draft).
  ``service_status`` is the *operational* one (in_service / out_of_service /
  maintenance). They are independent: a room can be active but out of service.
  Neither replaces the derived occupancy state, which comes from the booking
  tables at query time.
- Amenities are a per-property catalogue joined to rooms, not a text blob, so
  the Amenities Management screen (062) can rename one centrally and so rooms
  stay filterable by amenity.
- Exactly one primary photo per room is enforced by a partial unique index
  rather than a flag we have to remember to clear.
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0004_rooms_rich"
down_revision: str | None = "0003_guests"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ---------------- property.rooms: physical + merchandising attributes ----
    op.execute(
        """
        ALTER TABLE property.rooms
            ADD COLUMN IF NOT EXISTS building          varchar(100),
            ADD COLUMN IF NOT EXISTS floor             varchar(20),
            ADD COLUMN IF NOT EXISTS bed_setup         varchar(60),
            ADD COLUMN IF NOT EXISTS view_type         varchar(60),
            ADD COLUMN IF NOT EXISTS max_adults        integer NOT NULL DEFAULT 2,
            ADD COLUMN IF NOT EXISTS max_children      integer NOT NULL DEFAULT 0,
            ADD COLUMN IF NOT EXISTS base_rate         numeric(12,2),
            ADD COLUMN IF NOT EXISTS housekeeping_zone varchar(100),
            ADD COLUMN IF NOT EXISTS accessibility     varchar(30) NOT NULL DEFAULT 'none',
            ADD COLUMN IF NOT EXISTS near_elevator     boolean NOT NULL DEFAULT false,
            ADD COLUMN IF NOT EXISTS status            varchar(20) NOT NULL DEFAULT 'active',
            ADD COLUMN IF NOT EXISTS service_status    varchar(20) NOT NULL DEFAULT 'in_service',
            ADD COLUMN IF NOT EXISTS notes             text
        """
    )
    op.execute(
        """
        ALTER TABLE property.rooms
            ADD CONSTRAINT ck_room_status
                CHECK (status IN ('active', 'inactive', 'draft')),
            ADD CONSTRAINT ck_room_service_status
                CHECK (service_status IN ('in_service', 'out_of_service', 'maintenance')),
            ADD CONSTRAINT ck_room_accessibility
                CHECK (accessibility IN ('none', 'wheelchair', 'hearing', 'visual')),
            ADD CONSTRAINT ck_room_occupancy
                CHECK (max_adults >= 1 AND max_children >= 0),
            ADD CONSTRAINT ck_room_base_rate
                CHECK (base_rate IS NULL OR base_rate >= 0)
        """
    )
    # rooms.(property_id, id) must be unique before the composite FKs below can
    # reference it.
    op.execute(
        """
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_constraint WHERE conname = 'uq_room_property_id'
            ) THEN
                ALTER TABLE property.rooms
                    ADD CONSTRAINT uq_room_property_id UNIQUE (property_id, id);
            END IF;
        END $$
        """
    )

    # The inventory screen filters by floor/status constantly.
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_rooms_floor ON property.rooms (property_id, floor)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_rooms_status "
        "ON property.rooms (property_id, status, service_status)"
    )

    # ---------------- property.room_types: merchandising attributes ----------
    op.execute(
        """
        ALTER TABLE property.room_types
            ADD COLUMN IF NOT EXISTS description varchar(500),
            ADD COLUMN IF NOT EXISTS base_rate   numeric(12,2),
            ADD COLUMN IF NOT EXISTS bed_setup   varchar(60),
            ADD COLUMN IF NOT EXISTS size_sqft   integer,
            ADD COLUMN IF NOT EXISTS status      varchar(20) NOT NULL DEFAULT 'active'
        """
    )
    op.execute(
        """
        ALTER TABLE property.room_types
            ADD CONSTRAINT ck_room_type_status
                CHECK (status IN ('active', 'inactive')),
            ADD CONSTRAINT ck_room_type_base_rate
                CHECK (base_rate IS NULL OR base_rate >= 0),
            ADD CONSTRAINT ck_room_type_size
                CHECK (size_sqft IS NULL OR size_sqft > 0)
        """
    )

    # ---------------- property.amenities: per-property catalogue -------------
    op.execute(
        """
        CREATE TABLE property.amenities (
            id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            organization_id uuid NOT NULL,
            property_id     uuid NOT NULL,
            code            varchar(40)  NOT NULL,
            name            varchar(120) NOT NULL,
            category        varchar(40)  NOT NULL DEFAULT 'general',
            icon            varchar(40),
            is_chargeable   boolean NOT NULL DEFAULT false,
            status          varchar(20) NOT NULL DEFAULT 'active',
            created_at      timestamptz NOT NULL DEFAULT now(),
            updated_at      timestamptz NOT NULL DEFAULT now(),
            version         bigint NOT NULL DEFAULT 0,
            CONSTRAINT uq_amenity_property_code UNIQUE (property_id, code),
            CONSTRAINT uq_amenity_property_id   UNIQUE (property_id, id),
            CONSTRAINT ck_amenity_status CHECK (status IN ('active', 'inactive')),
            CONSTRAINT ck_amenity_category CHECK (
                category IN ('general', 'comfort', 'bathroom', 'technology',
                             'kitchen', 'outdoor', 'accessibility')
            )
        )
        """
    )

    # Composite FKs on (property_id, ...) keep a room in property A from ever
    # referencing an amenity in property B (§1).
    op.execute(
        """
        CREATE TABLE property.room_amenities (
            room_id     uuid NOT NULL,
            amenity_id  uuid NOT NULL,
            property_id uuid NOT NULL,
            created_at  timestamptz NOT NULL DEFAULT now(),
            PRIMARY KEY (room_id, amenity_id),
            CONSTRAINT fk_ra_room
                FOREIGN KEY (property_id, room_id)
                REFERENCES property.rooms (property_id, id) ON DELETE CASCADE,
            CONSTRAINT fk_ra_amenity
                FOREIGN KEY (property_id, amenity_id)
                REFERENCES property.amenities (property_id, id) ON DELETE CASCADE
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_room_amenities_amenity "
        "ON property.room_amenities (amenity_id)"
    )

    op.execute(
        """
        CREATE TABLE property.room_photos (
            id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            room_id     uuid NOT NULL,
            property_id uuid NOT NULL,
            url         text NOT NULL,
            caption     varchar(200),
            is_primary  boolean NOT NULL DEFAULT false,
            sort_order  integer NOT NULL DEFAULT 0,
            created_at  timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT fk_room_photo_room
                FOREIGN KEY (property_id, room_id)
                REFERENCES property.rooms (property_id, id) ON DELETE CASCADE
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_room_photos_room "
        "ON property.room_photos (room_id, sort_order)"
    )
    # At most one primary photo per room, enforced by the database.
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_room_photo_primary "
        "ON property.room_photos (room_id) WHERE is_primary"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS property.room_photos")
    op.execute("DROP TABLE IF EXISTS property.room_amenities")
    op.execute("DROP TABLE IF EXISTS property.amenities")
    op.execute(
        """
        ALTER TABLE property.room_types
            DROP CONSTRAINT IF EXISTS ck_room_type_status,
            DROP CONSTRAINT IF EXISTS ck_room_type_base_rate,
            DROP CONSTRAINT IF EXISTS ck_room_type_size,
            DROP COLUMN IF EXISTS description,
            DROP COLUMN IF EXISTS base_rate,
            DROP COLUMN IF EXISTS bed_setup,
            DROP COLUMN IF EXISTS size_sqft,
            DROP COLUMN IF EXISTS status
        """
    )
    op.execute(
        """
        ALTER TABLE property.rooms
            DROP CONSTRAINT IF EXISTS ck_room_status,
            DROP CONSTRAINT IF EXISTS ck_room_service_status,
            DROP CONSTRAINT IF EXISTS ck_room_accessibility,
            DROP CONSTRAINT IF EXISTS ck_room_occupancy,
            DROP CONSTRAINT IF EXISTS ck_room_base_rate,
            DROP CONSTRAINT IF EXISTS uq_room_property_id,
            DROP COLUMN IF EXISTS building,
            DROP COLUMN IF EXISTS floor,
            DROP COLUMN IF EXISTS bed_setup,
            DROP COLUMN IF EXISTS view_type,
            DROP COLUMN IF EXISTS max_adults,
            DROP COLUMN IF EXISTS max_children,
            DROP COLUMN IF EXISTS base_rate,
            DROP COLUMN IF EXISTS housekeeping_zone,
            DROP COLUMN IF EXISTS accessibility,
            DROP COLUMN IF EXISTS near_elevator,
            DROP COLUMN IF EXISTS status,
            DROP COLUMN IF EXISTS service_status,
            DROP COLUMN IF EXISTS notes
        """
    )
