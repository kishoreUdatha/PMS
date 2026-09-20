"""Guest identity, ID documents and the check-in record (screen 005)

Revision ID: 0015_checkin
Revises: 0014_rate_rules
Create Date: 2026-09-09

Checking a guest in is the moment a booking stops being a plan and becomes a
person in a room, and it is the moment a property is legally obliged to know
who that person is. ``engagement.guests`` held only a name, email, phone and
nationality, which is not enough to check anyone in.

Three things are added:

* **Identity on the guest** — address and a government ID (type + number).
  These live on the guest, not the stay: the same person checking in a second
  time should not have to produce their passport again.
* **``engagement.guest_documents``** — the scans themselves, stored in object
  storage exactly like room photos, with only the key kept in the database.
  ID images are the most sensitive thing this system holds, so they are never
  inlined into a row and are reached through a presigned URL that expires.
* **``booking.stay_checkins``** — what was true at the point of check-in:
  who did it, what was verified, what deposit was taken. A stay already records
  that someone is in house; this records the *act*, and it is append-only for
  the same reason an audit row is.

The deposit amount is recorded here rather than invented as a new money
concept: the payment itself goes through ``post_payment`` onto the folio like
every other payment, and this column only says how much of that was a
refundable deposit rather than a payment against the bill.
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0015_checkin"
down_revision: str | None = "0014_rate_rules"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

ID_TYPES = ("aadhaar", "passport", "driving_licence", "voter_id", "pan", "other")
DOC_KINDS = ("id_front", "id_back", "signature", "other")


def upgrade() -> None:
    quoted = lambda vals: ", ".join(f"'{v}'" for v in vals)  # noqa: E731

    op.execute(
        f"""
        ALTER TABLE engagement.guests
            ADD COLUMN IF NOT EXISTS address_line varchar(300),
            ADD COLUMN IF NOT EXISTS city varchar(120),
            ADD COLUMN IF NOT EXISTS state varchar(120),
            ADD COLUMN IF NOT EXISTS postal_code varchar(20),
            ADD COLUMN IF NOT EXISTS country varchar(80),
            ADD COLUMN IF NOT EXISTS id_type varchar(30),
            ADD COLUMN IF NOT EXISTS id_number varchar(60),
            ADD COLUMN IF NOT EXISTS id_verified_at timestamptz,
            ADD COLUMN IF NOT EXISTS id_verified_by uuid
        """
    )
    op.execute(
        f"""
        ALTER TABLE engagement.guests
            ADD CONSTRAINT ck_guest_id_type
            CHECK (id_type IS NULL OR id_type IN ({quoted(ID_TYPES)}))
        """
    )

    # Only the storage key lives here. The bytes stay in object storage and are
    # served through a URL that expires, because these are identity documents.
    op.execute(
        f"""
        CREATE TABLE engagement.guest_documents (
            id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            organization_id uuid NOT NULL,
            guest_id        uuid NOT NULL REFERENCES engagement.guests(id)
                              ON DELETE CASCADE,
            kind            varchar(20) NOT NULL,
            storage_key     varchar(400) NOT NULL,
            content_type    varchar(60),
            size_bytes      integer,
            original_name   varchar(200),
            uploaded_by     uuid,
            created_at      timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT ck_guest_doc_kind CHECK (kind IN ({quoted(DOC_KINDS)}))
        )
        """
    )
    op.execute(
        "CREATE INDEX ix_guest_documents_guest "
        "ON engagement.guest_documents (guest_id, kind)"
    )
    # One current front and one current back per guest; replacing one deletes
    # the old row, so the table cannot quietly accumulate stale scans.
    op.execute(
        "CREATE UNIQUE INDEX uq_guest_doc_kind "
        "ON engagement.guest_documents (guest_id, kind) "
        "WHERE kind IN ('id_front', 'id_back')"
    )

    # Every other table that gets referenced across tenants carries a
    # (property_id, id) unique so a child row cannot point at a parent in
    # another property. reservation_units was missing it, which is why the FK
    # below could not be created until now.
    op.execute(
        """
        ALTER TABLE booking.reservation_units
            ADD CONSTRAINT uq_unit_property_id UNIQUE (property_id, id)
        """
    )

    op.execute(
        """
        CREATE TABLE booking.stay_checkins (
            id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            organization_id     uuid NOT NULL,
            property_id         uuid NOT NULL,
            reservation_unit_id uuid NOT NULL,
            stay_id             uuid,
            room_id             uuid,
            -- What the deposit was, not where the money went: the payment
            -- itself is a folio credit like any other.
            deposit_amount      numeric(19, 4) NOT NULL DEFAULT 0,
            deposit_method      varchar(30),
            deposit_payment_id  uuid,
            id_verified         boolean NOT NULL DEFAULT false,
            signature_captured  boolean NOT NULL DEFAULT false,
            policies_accepted   boolean NOT NULL DEFAULT false,
            welcome_sent        boolean NOT NULL DEFAULT false,
            key_issued          boolean NOT NULL DEFAULT false,
            notes               varchar(500),
            checked_in_by       uuid,
            checked_in_at       timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT uq_stay_checkin_unit UNIQUE (reservation_unit_id),
            CONSTRAINT ck_stay_checkin_deposit CHECK (deposit_amount >= 0),
            CONSTRAINT fk_stay_checkin_unit
                FOREIGN KEY (property_id, reservation_unit_id)
                REFERENCES booking.reservation_units (property_id, id)
        )
        """
    )
    op.execute(
        "CREATE INDEX ix_stay_checkins_property "
        "ON booking.stay_checkins (property_id, checked_in_at DESC)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS booking.stay_checkins")
    op.execute(
        "ALTER TABLE booking.reservation_units "
        "DROP CONSTRAINT IF EXISTS uq_unit_property_id"
    )
    op.execute("DROP TABLE IF EXISTS engagement.guest_documents")
    op.execute(
        "ALTER TABLE engagement.guests DROP CONSTRAINT IF EXISTS ck_guest_id_type"
    )
    for col in ("address_line", "city", "state", "postal_code", "country",
                "id_type", "id_number", "id_verified_at", "id_verified_by"):
        op.execute(f"ALTER TABLE engagement.guests DROP COLUMN IF EXISTS {col}")
