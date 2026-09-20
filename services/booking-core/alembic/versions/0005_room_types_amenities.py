"""Room Type Management and Amenities Management (screens 060 / 062)

Revision ID: 0005_room_types_amenities
Revises: 0004_rooms_rich
Create Date: 2026-09-08

Screens 060 and 062 are full management screens, not the thin tabs the first
pass shipped. They need:

- Amenities attached to ROOM TYPES, not only to individual rooms. The mockups
  assign an amenity to "Deluxe Room, Premium Sea View, Executive Suite", and
  the room-type editor carries its own amenity checklist. Room-level amenities
  stay (a specific room can differ from its type), so both relations exist.
- A ``guest_visible`` flag: whether an amenity is published to the website,
  booking engine and guest communications, as opposed to being internal.
- The mockups' own category vocabulary. The first pass invented one
  (general/comfort/kitchen/outdoor); the real set is In-Room, Bathroom,
  Technology, Food & Beverage, Recreation, Safety & Security.
- Merchandising and policy fields on room types (child policy, extra bed,
  room view, default rate plan) plus per-type photos.
- Actor columns, because both screens show "Created by … / Last modified by …".
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0005_room_types_amenities"
down_revision: str | None = "0004_rooms_rich"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Old vocabulary -> the vocabulary the mockups actually use.
CATEGORY_REMAP = {
    "general": "in_room",
    "comfort": "in_room",
    "bathroom": "bathroom",
    "technology": "technology",
    "kitchen": "food_beverage",
    "outdoor": "recreation",
    "accessibility": "safety_security",
}
NEW_CATEGORIES = (
    "in_room",
    "bathroom",
    "technology",
    "food_beverage",
    "recreation",
    "safety_security",
)


def upgrade() -> None:
    # ---------------- room_types: policy + merchandising --------------------
    op.execute(
        """
        ALTER TABLE property.room_types
            ADD COLUMN IF NOT EXISTS child_policy       varchar(60),
            ADD COLUMN IF NOT EXISTS extra_bed_available boolean NOT NULL DEFAULT false,
            ADD COLUMN IF NOT EXISTS extra_bed_charge   numeric(12,2),
            ADD COLUMN IF NOT EXISTS room_view          varchar(60),
            ADD COLUMN IF NOT EXISTS default_rate_plan  varchar(80),
            ADD COLUMN IF NOT EXISTS created_by         uuid,
            ADD COLUMN IF NOT EXISTS updated_by         uuid
        """
    )
    op.execute(
        """
        ALTER TABLE property.room_types
            ADD CONSTRAINT ck_room_type_extra_bed_charge
                CHECK (extra_bed_charge IS NULL OR extra_bed_charge >= 0)
        """
    )

    # ---------------- amenities: guest visibility, description, actors ------
    op.execute(
        """
        ALTER TABLE property.amenities
            ADD COLUMN IF NOT EXISTS guest_visible boolean NOT NULL DEFAULT true,
            ADD COLUMN IF NOT EXISTS description   varchar(300),
            ADD COLUMN IF NOT EXISTS created_by    uuid,
            ADD COLUMN IF NOT EXISTS updated_by    uuid
        """
    )

    # Category vocabulary swap: drop the old CHECK, remap rows, add the new one.
    op.execute(
        "ALTER TABLE property.amenities DROP CONSTRAINT IF EXISTS ck_amenity_category"
    )
    for old, new in CATEGORY_REMAP.items():
        op.execute(
            f"UPDATE property.amenities SET category = '{new}' WHERE category = '{old}'"
        )
    op.execute("ALTER TABLE property.amenities ALTER COLUMN category SET DEFAULT 'in_room'")
    allowed = ", ".join(f"'{c}'" for c in NEW_CATEGORIES)
    op.execute(
        f"""
        ALTER TABLE property.amenities
            ADD CONSTRAINT ck_amenity_category CHECK (category IN ({allowed}))
        """
    )

    # ---------------- amenities <-> room types ------------------------------
    # Composite FKs on (property_id, ...) keep the link inside one property (§1).
    op.execute(
        """
        CREATE TABLE property.room_type_amenities (
            room_type_id uuid NOT NULL,
            amenity_id   uuid NOT NULL,
            property_id  uuid NOT NULL,
            created_at   timestamptz NOT NULL DEFAULT now(),
            PRIMARY KEY (room_type_id, amenity_id),
            CONSTRAINT fk_rta_room_type
                FOREIGN KEY (property_id, room_type_id)
                REFERENCES property.room_types (property_id, id) ON DELETE CASCADE,
            CONSTRAINT fk_rta_amenity
                FOREIGN KEY (property_id, amenity_id)
                REFERENCES property.amenities (property_id, id) ON DELETE CASCADE
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_rta_amenity "
        "ON property.room_type_amenities (amenity_id)"
    )

    # ---------------- room type photos --------------------------------------
    op.execute(
        """
        CREATE TABLE property.room_type_photos (
            id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            room_type_id uuid NOT NULL,
            property_id  uuid NOT NULL,
            url          text NOT NULL,
            caption      varchar(200),
            is_primary   boolean NOT NULL DEFAULT false,
            sort_order   integer NOT NULL DEFAULT 0,
            created_at   timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT fk_rtp_room_type
                FOREIGN KEY (property_id, room_type_id)
                REFERENCES property.room_types (property_id, id) ON DELETE CASCADE
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_rtp_room_type "
        "ON property.room_type_photos (room_type_id, sort_order)"
    )
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_rtp_primary "
        "ON property.room_type_photos (room_type_id) WHERE is_primary"
    )

    # ---------------- backfill type-level amenities from room-level ----------
    # A type gets every amenity that any of its rooms carries, so the new
    # screens open with real data instead of an empty checklist.
    op.execute(
        """
        INSERT INTO property.room_type_amenities (room_type_id, amenity_id, property_id)
        SELECT DISTINCT r.room_type_id, ra.amenity_id, r.property_id
        FROM property.room_amenities ra
        JOIN property.rooms r ON r.id = ra.room_id
        ON CONFLICT DO NOTHING
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS property.room_type_photos")
    op.execute("DROP TABLE IF EXISTS property.room_type_amenities")

    op.execute(
        "ALTER TABLE property.amenities DROP CONSTRAINT IF EXISTS ck_amenity_category"
    )
    inverse = {
        "in_room": "general",
        "bathroom": "bathroom",
        "technology": "technology",
        "food_beverage": "kitchen",
        "recreation": "outdoor",
        "safety_security": "accessibility",
    }
    for new, old in inverse.items():
        op.execute(
            f"UPDATE property.amenities SET category = '{old}' WHERE category = '{new}'"
        )
    op.execute("ALTER TABLE property.amenities ALTER COLUMN category SET DEFAULT 'general'")
    op.execute(
        """
        ALTER TABLE property.amenities
            ADD CONSTRAINT ck_amenity_category CHECK (
                category IN ('general', 'comfort', 'bathroom', 'technology',
                             'kitchen', 'outdoor', 'accessibility')
            )
        """
    )
    op.execute(
        """
        ALTER TABLE property.amenities
            DROP COLUMN IF EXISTS guest_visible,
            DROP COLUMN IF EXISTS description,
            DROP COLUMN IF EXISTS created_by,
            DROP COLUMN IF EXISTS updated_by
        """
    )
    op.execute(
        """
        ALTER TABLE property.room_types
            DROP CONSTRAINT IF EXISTS ck_room_type_extra_bed_charge,
            DROP COLUMN IF EXISTS child_policy,
            DROP COLUMN IF EXISTS extra_bed_available,
            DROP COLUMN IF EXISTS extra_bed_charge,
            DROP COLUMN IF EXISTS room_view,
            DROP COLUMN IF EXISTS default_rate_plan,
            DROP COLUMN IF EXISTS created_by,
            DROP COLUMN IF EXISTS updated_by
        """
    )
