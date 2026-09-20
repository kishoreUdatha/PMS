"""Buildings and floors as managed entities (screen 061)

Revision ID: 0007_buildings_floors
Revises: 0006_photo_storage
Create Date: 2026-09-08

Until now a room's building and floor were free text typed on the Add/Edit Room
form, so "1st Floor", "1" and "First Floor" were three different floors and
nothing could own a room range or a display order.

This makes them real: buildings contain floors, floors declare a room-number
range and an ordering, and rooms point at both. The old text columns stay and
are kept in step, so the Rooms screen keeps working while callers migrate.

Existing values are backfilled into buildings/floors and linked, so the screen
opens with the property's real structure rather than an empty tree.
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0007_buildings_floors"
down_revision: str | None = "0006_photo_storage"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE property.buildings (
            id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            organization_id uuid NOT NULL,
            property_id     uuid NOT NULL,
            code            varchar(30)  NOT NULL,
            name            varchar(120) NOT NULL,
            display_order   integer NOT NULL DEFAULT 0,
            status          varchar(20) NOT NULL DEFAULT 'active',
            created_by      uuid,
            updated_by      uuid,
            created_at      timestamptz NOT NULL DEFAULT now(),
            updated_at      timestamptz NOT NULL DEFAULT now(),
            version         bigint NOT NULL DEFAULT 0,
            CONSTRAINT uq_building_property_code UNIQUE (property_id, code),
            CONSTRAINT uq_building_property_id   UNIQUE (property_id, id),
            CONSTRAINT ck_building_status CHECK (status IN ('active', 'inactive'))
        )
        """
    )

    # from_room_no / to_room_no are the *declared* range shown on the screen.
    # The room count is always derived from the rooms actually linked, so the
    # two can be compared rather than silently disagreeing.
    op.execute(
        """
        CREATE TABLE property.floors (
            id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            organization_id uuid NOT NULL,
            property_id     uuid NOT NULL,
            building_id     uuid NOT NULL,
            code            varchar(10)  NOT NULL,
            name            varchar(120) NOT NULL,
            from_room_no    varchar(30),
            to_room_no      varchar(30),
            display_order   integer NOT NULL DEFAULT 0,
            status          varchar(20) NOT NULL DEFAULT 'active',
            created_by      uuid,
            updated_by      uuid,
            created_at      timestamptz NOT NULL DEFAULT now(),
            updated_at      timestamptz NOT NULL DEFAULT now(),
            version         bigint NOT NULL DEFAULT 0,
            CONSTRAINT uq_floor_building_code UNIQUE (building_id, code),
            CONSTRAINT uq_floor_property_id   UNIQUE (property_id, id),
            CONSTRAINT ck_floor_status CHECK (status IN ('active', 'inactive')),
            CONSTRAINT fk_floor_building
                FOREIGN KEY (property_id, building_id)
                REFERENCES property.buildings (property_id, id) ON DELETE CASCADE
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_floors_building "
        "ON property.floors (building_id, display_order)"
    )

    # Rooms point at the managed entities. Composite FKs keep a room's floor
    # inside the room's own property (§1).
    op.execute(
        """
        ALTER TABLE property.rooms
            ADD COLUMN IF NOT EXISTS building_id uuid,
            ADD COLUMN IF NOT EXISTS floor_id    uuid
        """
    )
    op.execute(
        """
        ALTER TABLE property.rooms
            ADD CONSTRAINT fk_room_building
                FOREIGN KEY (property_id, building_id)
                REFERENCES property.buildings (property_id, id),
            ADD CONSTRAINT fk_room_floor
                FOREIGN KEY (property_id, floor_id)
                REFERENCES property.floors (property_id, id)
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_rooms_floor_id ON property.rooms (floor_id)"
    )

    # ---------------- backfill from the existing free text -------------------
    op.execute(
        """
        INSERT INTO property.buildings
            (organization_id, property_id, code, name, display_order)
        SELECT DISTINCT ON (r.property_id, r.building)
               r.organization_id, r.property_id,
               upper(left(regexp_replace(r.building, '[^A-Za-z0-9]', '', 'g'), 10)),
               r.building,
               row_number() OVER (PARTITION BY r.property_id ORDER BY r.building)
        FROM property.rooms r
        WHERE r.building IS NOT NULL AND r.building <> ''
        ORDER BY r.property_id, r.building
        """
    )
    op.execute(
        """
        INSERT INTO property.floors
            (organization_id, property_id, building_id, code, name,
             from_room_no, to_room_no, display_order)
        SELECT r.organization_id, r.property_id, b.id,
               r.floor,
               CASE r.floor
                   WHEN '0' THEN 'Ground Floor'
                   WHEN 'G' THEN 'Ground Floor'
                   WHEN '1' THEN 'First Floor'
                   WHEN '2' THEN 'Second Floor'
                   WHEN '3' THEN 'Third Floor'
                   WHEN '4' THEN 'Fourth Floor'
                   ELSE 'Floor ' || r.floor
               END,
               min(r.code), max(r.code),
               row_number() OVER (PARTITION BY r.property_id, b.id ORDER BY r.floor)
        FROM property.rooms r
        JOIN property.buildings b
          ON b.property_id = r.property_id AND b.name = r.building
        WHERE r.floor IS NOT NULL AND r.floor <> ''
        GROUP BY r.organization_id, r.property_id, b.id, r.floor
        """
    )
    op.execute(
        """
        UPDATE property.rooms r
           SET building_id = b.id, floor_id = f.id
        FROM property.buildings b
        JOIN property.floors f ON f.building_id = b.id
        WHERE b.property_id = r.property_id
          AND b.name = r.building
          AND f.code = r.floor
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS property.ix_rooms_floor_id")
    op.execute(
        """
        ALTER TABLE property.rooms
            DROP CONSTRAINT IF EXISTS fk_room_building,
            DROP CONSTRAINT IF EXISTS fk_room_floor,
            DROP COLUMN IF EXISTS building_id,
            DROP COLUMN IF EXISTS floor_id
        """
    )
    op.execute("DROP TABLE IF EXISTS property.floors")
    op.execute("DROP TABLE IF EXISTS property.buildings")
