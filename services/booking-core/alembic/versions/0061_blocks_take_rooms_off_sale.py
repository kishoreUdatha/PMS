"""A blocked room is a room nobody can sell

Revision ID: 0061_blocks_off_sale
Revises: 0060_ari_outbox
Create Date: 2026-09-26

Blocking a room claimed the dates on that room's own calendar and nothing
else. ``room_type_inventory_days.out_of_service`` -- the figure every
availability check, and the channel push, subtracts -- was never written by
anything, so a room under maintenance stayed on sale: bookable at the desk and
still offered to every channel. The channel manager's certification tests
lower availability by blocking rooms, and nothing reached it.

Now the count is derived from the blocks themselves, by triggers:

* any insert, update or delete on ``booking.room_blocks`` recounts the active
  blocks of that room's type over the dates it covered before and after, so
  creating, editing, ending and cancelling a block all land;
* a new inventory night (the night audit opening the far edge of the window)
  starts with the blocks already in place on it rather than zero.

Writing the count moves ``room_type_inventory_days``, whose own trigger records
the availability change in the ARI outbox -- so a block reaches the channels in
the same transaction that creates it, with no code in any route.
"""
from __future__ import annotations

from alembic import op

revision: str = "0061_blocks_off_sale"
down_revision: str | None = "0060_ari_outbox"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE OR REPLACE FUNCTION booking.recount_blocked_units(
            p_property uuid, p_room uuid, p_from date, p_to date)
        RETURNS void LANGUAGE plpgsql AS $$
        DECLARE v_type uuid;
        BEGIN
          SELECT room_type_id INTO v_type FROM property.rooms WHERE id = p_room;
          IF v_type IS NULL THEN
            RETURN;
          END IF;
          UPDATE booking.room_type_inventory_days i
             SET out_of_service = (
                 SELECT count(*) FROM booking.room_blocks b
                   JOIN property.rooms r ON r.id = b.room_id
                  WHERE r.room_type_id = v_type
                    AND b.status = 'active'
                    AND i.stay_date BETWEEN b.start_date AND b.end_date)
           WHERE i.property_id = p_property
             AND i.room_type_id = v_type
             AND i.stay_date BETWEEN p_from AND p_to;
        END $$
        """
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION booking.room_block_changed()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          IF TG_OP IN ('UPDATE', 'DELETE') THEN
            PERFORM booking.recount_blocked_units(
                OLD.property_id, OLD.room_id, OLD.start_date, OLD.end_date);
          END IF;
          IF TG_OP IN ('INSERT', 'UPDATE') THEN
            PERFORM booking.recount_blocked_units(
                NEW.property_id, NEW.room_id, NEW.start_date, NEW.end_date);
          END IF;
          RETURN NULL;
        END $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_room_block_inventory
        AFTER INSERT OR UPDATE OR DELETE ON booking.room_blocks
        FOR EACH ROW EXECUTE FUNCTION booking.room_block_changed()
        """
    )

    op.execute(
        """
        CREATE OR REPLACE FUNCTION booking.inventory_day_blocked_units()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          NEW.out_of_service := GREATEST(NEW.out_of_service, (
              SELECT count(*) FROM booking.room_blocks b
                JOIN property.rooms r ON r.id = b.room_id
               WHERE r.room_type_id = NEW.room_type_id
                 AND b.status = 'active'
                 AND NEW.stay_date BETWEEN b.start_date AND b.end_date));
          RETURN NEW;
        END $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_inventory_day_blocked_units
        BEFORE INSERT ON booking.room_type_inventory_days
        FOR EACH ROW EXECUTE FUNCTION booking.inventory_day_blocked_units()
        """
    )

    # Blocks that already exist were never counted.
    op.execute(
        """
        SELECT booking.recount_blocked_units(
                   property_id, room_id, start_date, end_date)
          FROM booking.room_blocks
         WHERE status = 'active'
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_inventory_day_blocked_units "
               "ON booking.room_type_inventory_days")
    op.execute("DROP FUNCTION IF EXISTS booking.inventory_day_blocked_units()")
    op.execute("DROP TRIGGER IF EXISTS trg_room_block_inventory "
               "ON booking.room_blocks")
    op.execute("DROP FUNCTION IF EXISTS booking.room_block_changed()")
    op.execute("DROP FUNCTION IF EXISTS "
               "booking.recount_blocked_units(uuid, uuid, date, date)")
