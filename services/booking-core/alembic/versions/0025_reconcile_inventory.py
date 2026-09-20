"""Reconcile inventory counters with the bookings that hold them

Revision ID: 0025_reconcile_inv
Revises: 0024_unit_line_index
Create Date: 2026-09-10

``modify_reservation`` changed a booking's dates and room type without ever
touching ``booking.room_type_inventory_days``. The counters went on describing
the stay as it was first booked, so every night added by an extension was
never taken off sale — the property could sell a room it had already given
away — and every night given back by a shortening stayed off sale, quietly
losing revenue. The code is fixed; this repairs what it already did.

The counters are recomputed from the bookings, which are the record of what
was actually promised. Two rules make that recomputation exact:

* ``held_units`` counts rooms on reservations still merely held;
  ``reserved_units`` counts everything else that is not cancelled or a
  no-show. They are separate counters and a booking sits in exactly one.

* A guest who leaves early has their remaining nights released at check-out,
  and ``reservation_units.departure_date`` is deliberately not rewritten — it
  records what was booked. So nights after the actual check-out are genuinely
  back on sale and must not be counted again here. Anything not yet checked
  out counts for its whole stay.

``physical_capacity``, ``out_of_service`` and ``allotment_units`` are left
alone: they are set by the property, not derived from bookings.
"""

from __future__ import annotations

from alembic import op

revision = "0025_reconcile_inv"
down_revision = "0024_unit_line_index"
branch_labels = None
depends_on = None


_OCCUPIED = """
    SELECT u.property_id, u.room_type_id, i.stay_date,
           count(*) FILTER (WHERE r.status = 'held') AS held,
           count(*) FILTER (
               WHERE r.status NOT IN ('held', 'cancelled', 'no_show')
           ) AS reserved
    FROM booking.reservation_units u
    JOIN booking.reservations r ON r.id = u.reservation_id
    LEFT JOIN booking.stays s ON s.reservation_unit_id = u.id
    JOIN booking.room_type_inventory_days i
      ON i.property_id = u.property_id
     AND i.room_type_id = u.room_type_id
     AND i.stay_date >= u.arrival_date
     AND i.stay_date < u.departure_date
    WHERE u.status NOT IN ('cancelled', 'no_show')
      -- An early departure hands its remaining nights back at check-out.
      -- Those nights are on sale again, so counting them here would take
      -- rooms off sale that the desk has already released.
      AND (s.actual_checkout_at IS NULL
           OR i.stay_date <= s.actual_checkout_at::date)
    GROUP BY 1, 2, 3
"""


def upgrade() -> None:
    conn = op.get_bind()

    # Report what is being corrected rather than silently rewriting counters —
    # a migration that moves inventory should say how much and where.
    drifted = conn.exec_driver_sql(
        f"""
        WITH occupied AS ({_OCCUPIED})
        SELECT count(*) FROM booking.room_type_inventory_days d
        LEFT JOIN occupied o
               ON o.property_id = d.property_id
              AND o.room_type_id = d.room_type_id
              AND o.stay_date = d.stay_date
        WHERE d.held_units <> COALESCE(o.held, 0)
           OR d.reserved_units <> COALESCE(o.reserved, 0)
        """
    ).scalar()
    print(f"  inventory days out of step with their bookings: {drifted}")

    # Rows a booking covers: set them to what it actually holds.
    conn.exec_driver_sql(
        f"""
        WITH occupied AS ({_OCCUPIED})
        UPDATE booking.room_type_inventory_days d
           SET held_units = o.held,
               reserved_units = o.reserved
          FROM occupied o
         WHERE o.property_id = d.property_id
           AND o.room_type_id = d.room_type_id
           AND o.stay_date = d.stay_date
           AND (d.held_units <> o.held OR d.reserved_units <> o.reserved)
        """
    )

    # Rows no booking covers at all, still carrying a count: a night that was
    # given back — by a shortened stay or a moved one — and never released.
    conn.exec_driver_sql(
        f"""
        WITH occupied AS ({_OCCUPIED})
        UPDATE booking.room_type_inventory_days d
           SET held_units = 0, reserved_units = 0
         WHERE (d.held_units <> 0 OR d.reserved_units <> 0)
           AND NOT EXISTS (
               SELECT 1 FROM occupied o
                WHERE o.property_id = d.property_id
                  AND o.room_type_id = d.room_type_id
                  AND o.stay_date = d.stay_date)
        """
    )


def downgrade() -> None:
    # There is nothing to go back to. The counters this replaced were wrong by
    # construction, and restoring them would put rooms back on sale that are
    # promised to a guest.
    pass
