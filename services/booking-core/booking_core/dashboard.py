"""Operations dashboard metrics (read model) for booking-core.

Computes the operational figures the dashboard needs from live tables:
rooms, reservation_units, stays, room_type_inventory_days, room_condition.
All queries are scoped by property_id and the given business date. These are
read-only aggregates; they never mutate state.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import date, timedelta

from sqlalchemy import text
from sqlalchemy.orm import Session


@dataclass
class ArrivalRow:
    guest_name: str | None
    room_no: str
    arrival_time: str
    reservation_no: str
    status: str


@dataclass
class DashboardMetrics:
    #: The day these figures describe.
    #:
    #: Every number below is computed against the property's business date,
    #: and the response did not say which day that was -- so a caller
    #: comparing the dashboard against any other screen had to guess, and
    #: guessing "today" is wrong for the whole of the night and for as long
    #: as the audit is lagging. That mismatch is exactly what made the
    #: cross-screen test compare one day's rack with another day's
    #: dashboard and report a disagreement neither screen had.
    business_date: date
    occupancy_pct: int
    arrivals: int
    departures: int
    available_rooms: int
    total_rooms: int
    room_status: dict[str, int]
    housekeeping: dict[str, int]
    # The same three figures for the day before, so a caller can show each KPI
    # against yesterday rather than as a number with nothing to compare it to.
    prev_occupancy_pct: int = 0
    prev_arrivals: int = 0
    prev_departures: int = 0
    occupancy_series: list[dict] = field(default_factory=list)
    todays_arrivals: list[ArrivalRow] = field(default_factory=list)


#: Rooms with somebody in them on one night, counted the same way whether the
#: night is tonight or a night last month.
#:
#: Two ways to qualify, and both are needed:
#:
#:   still checked in -- the booking stands, and an overstay is exactly the
#:   case where the booking has run out and the guest has not;
#:
#:   gone, but was here -- checked in on or before that day and not checked
#:   out until after it. Read from booking.stays, because the booked
#:   departure says nothing about a guest who left early, and most of
#:   Lekhana's departures were early.
#:
#: A guest who arrives and leaves the same day was asleep in the house on no
#: night and is counted for none, which is what an empty accrual says too.
_OCCUPIED_ON = """
    SELECT count(*)
      FROM booking.reservation_units ru
      LEFT JOIN booking.stays st ON st.reservation_unit_id = ru.id
      LEFT JOIN iam.properties pr ON pr.id = ru.property_id
     WHERE ru.property_id = :prop
       AND ru.arrival_date <= :d
       AND (
             (ru.status = 'checked_in' AND ru.departure_date > :d)
          OR (st.actual_checkin_at IS NOT NULL
              AND (st.actual_checkin_at
                   AT TIME ZONE COALESCE(pr.timezone, 'Asia/Kolkata')
                  )::date <= :d
              AND (st.actual_checkout_at IS NULL
                   OR (st.actual_checkout_at
                       AT TIME ZONE COALESCE(pr.timezone, 'Asia/Kolkata')
                      )::date > :d))
       )
"""


def get_dashboard(
    session: Session, *, property_id: uuid.UUID, business_date: date
) -> DashboardMetrics:
    # Total active physical rooms for the property.
    total_rooms = session.execute(
        text(
            """
            SELECT count(*) FROM property.rooms
            WHERE property_id = :prop
              -- Sellable inventory only. A deactivated or draft room is not a
              -- room the property can put anyone in, and counting it makes
              -- occupancy and availability read low against a total that is
              -- not real. The rack applies the same test.
              AND status = 'active'
              AND (retired_on IS NULL OR retired_on > :bd)
            """
        ),
        {"prop": property_id, "bd": business_date},
    ).scalar_one()

    # Rooms with somebody in them tonight.
    occupied = session.execute(
        text(_OCCUPIED_ON), {"prop": property_id, "d": business_date},
    ).scalar_one()

    # The same question about yesterday. Asking it a different way is what put
    # a -75% delta under a 0% figure: the two were not comparable.
    prev_date = business_date - timedelta(days=1)
    prev_occupied = session.execute(
        text(_OCCUPIED_ON), {"prop": property_id, "d": prev_date},
    ).scalar_one()

    # Arrivals / departures for the business date.
    arrivals = session.execute(
        text(
            """
            SELECT count(*) FROM booking.reservation_units
            WHERE property_id = :prop AND arrival_date = :bd
              AND status IN ('reserved','checked_in','checked_out')
            """
        ),
        {"prop": property_id, "bd": business_date},
    ).scalar_one()
    departures = session.execute(
        text(
            """

            -- Who left, not who was booked to leave. The union counts a unit
            -- once: already gone today, or still in the house and due out.
            SELECT count(*) FROM (
                SELECT s.reservation_unit_id AS id
                  FROM booking.stays s
                  JOIN iam.properties p ON p.id = s.property_id
                 WHERE s.property_id = :prop
                   AND s.actual_checkout_at IS NOT NULL
                   AND (s.actual_checkout_at AT TIME ZONE COALESCE(p.timezone, 'Asia/Kolkata'))::date = :bd
                UNION
                SELECT u.id
                  FROM booking.reservation_units u
                 WHERE u.property_id = :prop
                   AND u.departure_date = :bd
                   AND u.status = 'checked_in'
            ) d
            """
        ),
        {"prop": property_id, "bd": business_date},
    ).scalar_one()

    prev_arrivals = session.execute(
        text(
            """
            SELECT count(*) FROM booking.reservation_units
            WHERE property_id = :prop AND arrival_date = :pd
              AND status IN ('reserved','checked_in','checked_out')
            """
        ),
        {"prop": property_id, "pd": prev_date},
    ).scalar_one()
    prev_departures = session.execute(
        text(
            """

            -- Who left, not who was booked to leave. The union counts a unit
            -- once: already gone today, or still in the house and due out.
            SELECT count(*) FROM (
                SELECT s.reservation_unit_id AS id
                  FROM booking.stays s
                  JOIN iam.properties p ON p.id = s.property_id
                 WHERE s.property_id = :prop
                   AND s.actual_checkout_at IS NOT NULL
                   AND (s.actual_checkout_at AT TIME ZONE COALESCE(p.timezone, 'Asia/Kolkata'))::date = :pd
                UNION
                SELECT u.id
                  FROM booking.reservation_units u
                 WHERE u.property_id = :prop
                   AND u.departure_date = :pd
                   AND u.status = 'checked_in'
            ) d
            """
        ),
        {"prop": property_id, "pd": prev_date},
    ).scalar_one()

    # Room condition breakdown (cleaning / maintenance / clean).
    cond_rows = session.execute(
        text(
            """
            SELECT COALESCE(rc.cleanliness, 'clean') AS cleanliness,
                   count(*) AS c
            FROM operations.room_condition rc
            JOIN property.rooms r ON r.id = rc.room_id
            WHERE rc.property_id = :prop
              AND r.status = 'active'
              AND (r.retired_on IS NULL OR r.retired_on > :bd)
            GROUP BY COALESCE(rc.cleanliness, 'clean')
            """
        ),
        {"prop": property_id, "bd": business_date},
    ).all()
    cond = {r.cleanliness: r.c for r in cond_rows}
    cleaning = cond.get("cleaning", 0) + cond.get("dirty", 0)

    # Maintenance: active maintenance calendar entries covering the date.
    maintenance = session.execute(
        text(
            """
            SELECT count(DISTINCT e.room_id)
            FROM booking.room_calendar_entries e
            JOIN property.rooms r ON r.id = e.room_id
            WHERE e.property_id = :prop AND e.kind = 'maintenance'
              AND e.status = 'active'
              AND r.status = 'active'
              AND (r.retired_on IS NULL OR r.retired_on > :bd)
            """
        ),
        {"prop": property_id, "bd": business_date},
    ).scalar_one()

    available = max(total_rooms - occupied - cleaning - maintenance, 0)
    occupancy_pct = round((occupied / total_rooms) * 100) if total_rooms else 0
    prev_occupancy_pct = (
        round((prev_occupied / total_rooms) * 100) if total_rooms else 0
    )

    room_status = {
        "occupied": int(occupied),
        "available": int(available),
        "cleaning": int(cleaning),
        "maintenance": int(maintenance),
    }

    # Housekeeping counts (by cleanliness).
    housekeeping = {
        "rooms_cleaned": cond.get("clean", 0) + cond.get("inspected", 0),
        "in_progress": cond.get("cleaning", 0),
        "pending": cond.get("dirty", 0),
        "maintenance": int(maintenance),
    }

    # Today's arrivals list.
    arr_rows = session.execute(
        text(
            """
            SELECT r.number AS reservation_no,
                   ru.arrival_date, ru.status,
                   COALESCE(rm.code, '-') AS room_no,
                   g.full_name AS guest_name
            FROM booking.reservation_units ru
            JOIN booking.reservations r ON r.id = ru.reservation_id
            LEFT JOIN property.rooms rm ON rm.id = ru.assigned_room_id
            LEFT JOIN engagement.guests g ON g.id = r.primary_guest_id
            WHERE ru.property_id = :prop AND ru.arrival_date = :bd
            ORDER BY r.number
            LIMIT 20
            """
        ),
        {"prop": property_id, "bd": business_date},
    ).all()
    todays_arrivals = [
        ArrivalRow(
            guest_name=row.guest_name,
            room_no=row.room_no,
            arrival_time="--:--",
            reservation_no=row.reservation_no,
            status="Checked In" if row.status == "checked_in" else "Arriving",
        )
        for row in arr_rows
    ]

    # 7-day occupancy series ending on business_date.
    start = business_date - timedelta(days=6)
    series_rows = session.execute(
        text(
            """
            SELECT stay_date,
                   CASE WHEN sum(physical_capacity) > 0
                        THEN round(100.0 * sum(reserved_units + held_units)
                             / sum(physical_capacity))
                        ELSE 0 END AS occ
            FROM booking.room_type_inventory_days
            WHERE property_id = :prop
              AND stay_date >= :start AND stay_date <= :bd
            GROUP BY stay_date
            ORDER BY stay_date
            """
        ),
        {"prop": property_id, "start": start, "bd": business_date},
    ).all()
    by_date = {r.stay_date: int(r.occ) for r in series_rows}
    occupancy_series = [
        {
            "date": (start + timedelta(days=i)).isoformat(),
            "occupancy": by_date.get(start + timedelta(days=i), 0),
        }
        for i in range(7)
    ]

    return DashboardMetrics(
        business_date=business_date,
        occupancy_pct=occupancy_pct,
        arrivals=int(arrivals),
        departures=int(departures),
        available_rooms=int(available),
        total_rooms=int(total_rooms),
        room_status=room_status,
        housekeeping=housekeeping,
        prev_occupancy_pct=int(prev_occupancy_pct),
        prev_arrivals=int(prev_arrivals),
        prev_departures=int(prev_departures),
        occupancy_series=occupancy_series,
        todays_arrivals=todays_arrivals,
    )
