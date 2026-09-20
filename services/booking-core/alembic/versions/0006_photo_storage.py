"""Object-storage keys for room and room-type photos

Revision ID: 0006_photo_storage
Revises: 0005_room_types_amenities
Create Date: 2026-09-08

Photos are uploaded to MinIO; the database keeps the object key, not the image
and not a URL. URLs are presigned at read time, so they can expire and rotate
without a write, and the bucket never has to be public.

``url`` stays for photos referenced from somewhere external (a CDN, a supplier
feed). Exactly one of ``url`` / ``storage_key`` is required.
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0006_photo_storage"
down_revision: str | None = "0005_room_types_amenities"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLES = ("room_photos", "room_type_photos")


def upgrade() -> None:
    for table in TABLES:
        op.execute(
            f"""
            ALTER TABLE property.{table}
                ADD COLUMN IF NOT EXISTS storage_key   varchar(400),
                ADD COLUMN IF NOT EXISTS content_type  varchar(60),
                ADD COLUMN IF NOT EXISTS size_bytes    integer,
                ADD COLUMN IF NOT EXISTS uploaded_by   uuid
            """
        )
        # url was NOT NULL; an uploaded photo has a key instead.
        op.execute(f"ALTER TABLE property.{table} ALTER COLUMN url DROP NOT NULL")
        op.execute(
            f"""
            ALTER TABLE property.{table}
                ADD CONSTRAINT ck_{table}_source
                    CHECK (url IS NOT NULL OR storage_key IS NOT NULL)
            """
        )
        op.execute(
            f"CREATE UNIQUE INDEX IF NOT EXISTS uq_{table}_storage_key "
            f"ON property.{table} (storage_key) WHERE storage_key IS NOT NULL"
        )


def downgrade() -> None:
    for table in TABLES:
        op.execute(f"DROP INDEX IF EXISTS property.uq_{table}_storage_key")
        op.execute(
            f"ALTER TABLE property.{table} DROP CONSTRAINT IF EXISTS ck_{table}_source"
        )
        op.execute(
            f"DELETE FROM property.{table} WHERE url IS NULL"
        )
        op.execute(f"ALTER TABLE property.{table} ALTER COLUMN url SET NOT NULL")
        op.execute(
            f"""
            ALTER TABLE property.{table}
                DROP COLUMN IF EXISTS storage_key,
                DROP COLUMN IF EXISTS content_type,
                DROP COLUMN IF EXISTS size_bytes,
                DROP COLUMN IF EXISTS uploaded_by
            """
        )
