#!/usr/bin/env python
"""Generate the missing thumbnails for photos uploaded before they existed.

New uploads get a thumbnail on the way in. Everything already in the bucket
does not, and those are exactly the photographs a live deployment is serving --
so without this, the booking page keeps sending multi-megabyte originals to
phones for every property that was set up before today.

Run it inside the booking-core container, so it reads the same object-store
settings the service does rather than your shell's::

    docker compose exec -T booking-core python /app/scripts/backfill_thumbnails.py
    docker compose exec -T booking-core python /app/scripts/backfill_thumbnails.py --dry-run

Safe to run repeatedly: it only looks at rows with no thumbnail recorded, so a
second run does nothing and an interrupted one resumes where it stopped. It is
deliberately not part of the migration -- it downloads and re-encodes every
photograph in the deployment, which is not work to do inside a schema lock
while every service waits to start.
"""
from __future__ import annotations

import sys

from chirala_common.objectstore import ObjectStoreError, put_thumbnail
from sqlalchemy import text

from booking_core.database import SessionFactory
from booking_core.rooms_routes import _STORE

TABLES = (
    ("room_type_photos", "room type"),
    ("room_photos", "room"),
)


def fetch(store, key: str) -> bytes | None:
    """The original bytes, read back from object storage."""
    from chirala_common.objectstore import _clients

    internal, _ = _clients(store)
    try:
        return internal.get_object(Bucket=store.bucket, Key=key)["Body"].read()
    except Exception as exc:  # noqa: BLE001 - a missing object is not fatal
        print(f"    ! could not read {key}: {exc}")
        return None


def main(dry_run: bool) -> int:
    made = skipped = failed = 0

    with SessionFactory() as db:
        for table, label in TABLES:
            rows = db.execute(
                text(
                    f"""
                    SELECT id, storage_key
                    FROM property.{table}
                    WHERE storage_key IS NOT NULL AND thumb_key IS NULL
                    ORDER BY created_at
                    """
                )
            ).mappings().all()

            print(f"{label} photos without a thumbnail: {len(rows)}")
            for r in rows:
                key = r["storage_key"]
                if dry_run:
                    print(f"    would resize {key}")
                    skipped += 1
                    continue

                data = fetch(_STORE, key)
                if data is None:
                    failed += 1
                    continue

                try:
                    tkey = put_thumbnail(_STORE, key, data)
                except ObjectStoreError as exc:
                    print(f"    ! {key}: {exc}")
                    tkey = None

                if tkey is None:
                    # Left with no thumbnail rather than marked done, so a
                    # later run tries again -- the cause is usually a format
                    # Pillow could not read, which a newer build may handle.
                    print(f"    ! no thumbnail for {key}")
                    failed += 1
                    continue

                db.execute(
                    text(f"UPDATE property.{table} SET thumb_key = :t "
                         f"WHERE id = :id"),
                    {"t": tkey, "id": r["id"]},
                )
                # Committed per row. A backfill that re-encodes every image in
                # the deployment can take a while, and losing an hour of work
                # to one unreadable file at the end would be its own bug.
                db.commit()
                print(f"    {len(data) // 1024:>6} KB -> {tkey}")
                made += 1

    print(f"\ndone: {made} created, {failed} failed"
          + (f", {skipped} would be created" if dry_run else ""))
    return 1 if failed and not made else 0


if __name__ == "__main__":
    sys.exit(main("--dry-run" in sys.argv))
