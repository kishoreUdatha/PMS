"""One OTA listing, one connection, across every tenant

Revision ID: 0062_ota_hotel_unique
Revises: 0061_blocks_off_sale
Create Date: 2026-09-26

Every tenant of this platform shares one channel-manager account, and an OTA
listing is identified there by the OTA and the hotel's id with it. Nothing
stopped a second tenant typing the first tenant's Booking.com hotel id: the
connection was accepted, and provisioning then looked that listing up across
the whole account -- a second hotel attached to somebody else's Booking.com
listing, or a provisioning failure on the unique channel id, depending on
timing. Found by running two tenants against Channex staging.

The check has to see every tenant's connections, which row-level security
exists to prevent. A unique index is the one thing that does that without
opening a read path: Postgres enforces it across all rows whatever the
policies say, and all it can reveal is that the listing is taken.

``ota_code`` names the OTA the way provisioning does (the partner's name,
lowercased, punctuation removed: "Booking.com" -> "bookingcom"). A partner row
belongs to one tenant, so the OTA has to be named by something tenants share.
A trigger fills it from the partner, so no write path has to remember to.
"""
from __future__ import annotations

from alembic import op

revision: str = "0062_ota_hotel_unique"
down_revision: str | None = "0061_blocks_off_sale"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE distribution.channel_connections "
               "ADD COLUMN IF NOT EXISTS ota_code varchar(80)")
    op.execute(
        """
        CREATE OR REPLACE FUNCTION distribution.channel_connection_ota_code()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          SELECT regexp_replace(lower(a.name), '[^a-z0-9]+', '', 'g')
            INTO NEW.ota_code
            FROM engagement.booking_attributes a
           WHERE a.id = NEW.partner_id;
          RETURN NEW;
        END $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_channel_connection_ota_code
        BEFORE INSERT OR UPDATE OF partner_id, ota_hotel_id
        ON distribution.channel_connections
        FOR EACH ROW EXECUTE FUNCTION distribution.channel_connection_ota_code()
        """
    )
    op.execute(
        """
        UPDATE distribution.channel_connections c
           SET ota_code = regexp_replace(lower(a.name), '[^a-z0-9]+', '', 'g')
          FROM engagement.booking_attributes a
         WHERE a.id = c.partner_id
        """
    )
    # Refuse to guess which of two tenants owns a listing. Somebody has to
    # look, and the message says where.
    op.execute(
        """
        DO $$
        DECLARE dup text;
        BEGIN
          SELECT string_agg(ota_code || ' ' || ota_hotel_id, ', ') INTO dup
            FROM (SELECT ota_code, lower(btrim(ota_hotel_id)) AS ota_hotel_id
                    FROM distribution.channel_connections
                   WHERE ota_hotel_id IS NOT NULL AND btrim(ota_hotel_id) <> ''
                   GROUP BY 1, 2 HAVING count(*) > 1) d;
          IF dup IS NOT NULL THEN
            RAISE EXCEPTION 'OTA hotel ids connected more than once: %. '
              'Correct them in distribution.channel_connections first.', dup;
          END IF;
        END $$
        """
    )
    op.execute(
        """
        CREATE UNIQUE INDEX uq_channel_connection_ota_hotel
            ON distribution.channel_connections (ota_code, lower(btrim(ota_hotel_id)))
         WHERE ota_hotel_id IS NOT NULL AND btrim(ota_hotel_id) <> ''
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS distribution.uq_channel_connection_ota_hotel")
    op.execute("DROP TRIGGER IF EXISTS trg_channel_connection_ota_code "
               "ON distribution.channel_connections")
    op.execute("DROP FUNCTION IF EXISTS distribution.channel_connection_ota_code()")
    op.execute("ALTER TABLE distribution.channel_connections DROP COLUMN IF EXISTS ota_code")
