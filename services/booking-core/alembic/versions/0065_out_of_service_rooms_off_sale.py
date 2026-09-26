"""A room marked out of service is a room nobody can sell

Revision ID: 0065_oos_rooms_off_sale
Revises: 0064_default_cancel_policy
Create Date: 2026-09-26

0061 made ``room_type_inventory_days.out_of_service`` follow room *blocks*.
A room can also be taken out of service directly -- the room edit screen and
the bulk status change both set ``property.rooms.service_status`` to
``out_of_service`` or ``maintenance`` -- and that reached nothing: the room
was shown out of order on every card while every availability check, and the
channel push, went on selling it.

``out_of_service`` for a night is now the number of distinct rooms of the
type that are unsellable that night, for either reason:

* an active block covers the night (0061's rule), or
* the room is active and its ``service_status`` is not ``in_service`` and the
  night is today or later in the property's own timezone.

A room that is both is counted once -- the count is of rooms, not of reasons.
(0061 counted blocks, so two overlapping blocks on one room counted twice;
that is fixed by the same change.)

``service_status`` has no dates, so it can only speak for today onwards. When
it merely mirrors an out-of-order block -- ``blocks_routes`` keeps the two in
step -- the block's own dates are the truth, and the flag is ignored for any
room that has an active out-of-order block. Otherwise a block's mirror would
outlive the block and keep the room off sale for ever.

Triggers keep it current: on blocks (0061's trigger, whose recount now uses
the combined rule), on rooms (service status, type or active status
changing), and on a new inventory night.
"""
from __future__ import annotations

from alembic import op

revision: str = "0065_oos_rooms_off_sale"
down_revision: str | None = "0064_default_cancel_policy"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE OR REPLACE FUNCTION booking.local_today(p_property uuid)
        RETURNS date LANGUAGE sql STABLE AS $$
            SELECT COALESCE(
                (SELECT (now() AT TIME ZONE p.timezone)::date
                   FROM iam.properties p WHERE p.id = p_property),
                CURRENT_DATE)
        $$
        """
    )
    # One room, one night: the rule itself, so the per-type count below and
    # anything asking about particular rooms (removing a room, say) cannot
    # disagree about what "out of service" means.
    op.execute(
        """
        CREATE OR REPLACE FUNCTION booking.room_out_of_service(
            p_room uuid, p_day date, p_today date)
        RETURNS boolean LANGUAGE sql STABLE AS $$
            SELECT EXISTS (
                       SELECT 1 FROM booking.room_blocks b
                        WHERE b.room_id = p_room AND b.status = 'active'
                          AND p_day BETWEEN b.start_date AND b.end_date)
                OR EXISTS (
                       SELECT 1 FROM property.rooms r
                        WHERE r.id = p_room
                          AND r.status = 'active'
                          AND r.service_status <> 'in_service'
                          AND p_day >= p_today
                          AND NOT EXISTS (
                              SELECT 1 FROM booking.room_blocks b
                               WHERE b.room_id = r.id AND b.status = 'active'
                                 AND b.block_type = 'out_of_order'))
        $$
        """
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION booking.out_of_service_units(
            p_type uuid, p_day date, p_today date)
        RETURNS integer LANGUAGE sql STABLE AS $$
            SELECT count(*)::integer
              FROM property.rooms r
             WHERE r.room_type_id = p_type
               AND booking.room_out_of_service(r.id, p_day, p_today)
        $$
        """
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION booking.recount_out_of_service(
            p_property uuid, p_type uuid, p_from date, p_to date)
        RETURNS void LANGUAGE plpgsql AS $$
        DECLARE v_today date := booking.local_today(p_property);
        BEGIN
          -- Only rows whose count actually changes, so a recount that finds
          -- nothing new does not write -- and does not send the channels an
          -- availability update that says nothing.
          UPDATE booking.room_type_inventory_days i
             SET out_of_service = c.n
            FROM (
                SELECT d.stay_date,
                       booking.out_of_service_units(p_type, d.stay_date,
                                                    v_today) AS n
                  FROM booking.room_type_inventory_days d
                 WHERE d.property_id = p_property AND d.room_type_id = p_type
                   AND d.stay_date BETWEEN p_from AND p_to
            ) c
           WHERE i.property_id = p_property
             AND i.room_type_id = p_type
             AND i.stay_date = c.stay_date
             AND i.out_of_service IS DISTINCT FROM c.n;
        END $$
        """
    )
    # 0061's block recount, same signature so its trigger is unchanged, now
    # applying the combined rule.
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
          PERFORM booking.recount_out_of_service(p_property, v_type,
                                                 p_from, p_to);
        END $$
        """
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION booking.room_service_changed()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          IF TG_OP IN ('UPDATE', 'DELETE') THEN
            PERFORM booking.recount_out_of_service(
                OLD.property_id, OLD.room_type_id,
                booking.local_today(OLD.property_id), DATE '9999-12-31');
          END IF;
          IF TG_OP = 'INSERT' OR (TG_OP = 'UPDATE'
                                  AND NEW.room_type_id <> OLD.room_type_id) THEN
            PERFORM booking.recount_out_of_service(
                NEW.property_id, NEW.room_type_id,
                booking.local_today(NEW.property_id), DATE '9999-12-31');
          END IF;
          RETURN NULL;
        END $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_room_service_inventory
        AFTER INSERT OR DELETE OR UPDATE OF service_status, room_type_id, status
        ON property.rooms
        FOR EACH ROW EXECUTE FUNCTION booking.room_service_changed()
        """
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION booking.inventory_day_blocked_units()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          NEW.out_of_service := GREATEST(NEW.out_of_service,
              booking.out_of_service_units(
                  NEW.room_type_id, NEW.stay_date,
                  booking.local_today(NEW.property_id)));
          RETURN NEW;
        END $$
        """
    )

    # Rooms already out of service were never counted.
    op.execute("SELECT set_config('app.system', 'on', true)")
    op.execute(
        """
        SELECT booking.recount_out_of_service(
                   t.property_id, t.id, booking.local_today(t.property_id),
                   DATE '9999-12-31')
          FROM property.room_types t
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_room_service_inventory "
               "ON property.rooms")
    op.execute("DROP FUNCTION IF EXISTS booking.room_service_changed()")
    # 0061's definitions, verbatim.
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
    op.execute("DROP FUNCTION IF EXISTS "
               "booking.recount_out_of_service(uuid, uuid, date, date)")
    op.execute("DROP FUNCTION IF EXISTS "
               "booking.out_of_service_units(uuid, date, date)")
    op.execute("DROP FUNCTION IF EXISTS "
               "booking.room_out_of_service(uuid, date, date)")
    op.execute("DROP FUNCTION IF EXISTS booking.local_today(uuid)")
