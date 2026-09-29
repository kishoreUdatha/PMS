"""Reservation lifecycle: confirm -> assign room -> check-in -> checkout (§4).

All functions run inside the caller's transaction (the ``get_session``
dependency commits on success / rolls back on exception), and use explicit SQL
so locking and the GiST collision constraint behave exactly as designed.

State transitions:
  reservation:      held -> confirmed -> completed
  reservation_unit: reserved -> checked_in -> checked_out
Inventory:
  hold moves held_units -> reserved_units on confirm.
  Physical room assignment does NOT decrement type inventory again (§4).
Room collision:
  room_calendar_entries GiST exclusion rejects overlapping active entries.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone

from chirala_common.outbox import enqueue_event
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .history_routes import record_status_event


class FlowError(Exception):
    """Business-rule violation in the reservation flow (maps to HTTP 409/422)."""

    def __init__(self, message: str, *, conflict: bool = False) -> None:
        self.conflict = conflict
        super().__init__(message)


class RoomCollision(FlowError):
    """The requested room is already occupied/blocked for the period (§4)."""

    def __init__(self, room_id: uuid.UUID) -> None:
        super().__init__(
            f"Room {room_id} is not available for the requested period",
            conflict=True,
        )


def _nights(arrival: date, departure: date) -> list[date]:
    n = (departure - arrival).days
    return [arrival + timedelta(days=i) for i in range(n)]


def _unit_row(session: Session, reservation_unit_id: uuid.UUID):
    row = session.execute(
        text(
            """
            SELECT ru.id, ru.organization_id, ru.property_id, ru.reservation_id,
                   ru.room_type_id, ru.arrival_date, ru.departure_date,
                   ru.status AS unit_status, ru.assigned_room_id,
                   r.status AS reservation_status, r.number AS reservation_number
            FROM booking.reservation_units ru
            JOIN booking.reservations r ON r.id = ru.reservation_id
            WHERE ru.id = :id
            FOR UPDATE OF ru
            """
        ),
        {"id": reservation_unit_id},
    ).first()
    if row is None:
        raise FlowError("Reservation unit not found")
    return row


# --------------------------------------------------------------------------
# 1) Confirm a held reservation: held_units -> reserved_units
# --------------------------------------------------------------------------
def confirm_reservation(
    session: Session, *, reservation_id: uuid.UUID
) -> bool:
    """Turn a held reservation into a booking.

    Returns True when this call performed the transition, False when the
    reservation was already confirmed and there was nothing to do. Callers use
    that to decide whether anything with a side effect outside the database --
    telling the guest, above all -- should happen: confirming twice is
    harmless, but a second "your booking is confirmed" email is not.
    """
    res = session.execute(
        text(
            """
            SELECT id, organization_id, property_id, status, number,
                   group_block_id
            FROM booking.reservations
            WHERE id = :id
            FOR UPDATE
            """
        ),
        {"id": reservation_id},
    ).first()
    if res is None:
        raise FlowError("Reservation not found")
    if res.status == "confirmed":
        return False  # idempotent, and nothing new happened
    if res.status == "cancelled" and _expired_hold(session, reservation_id):
        # The guest paid after the reaper had already given the rooms back.
        # That used to end as money on a folio for a booking that no longer
        # existed ("paid_unconfirmed"), with nobody told why. If the rooms are
        # still there the guest gets them; if they have been sold since, the
        # answer is a clear refusal the payment webhook can act on.
        _revive_expired_hold(session, res)
        return True
    if res.status != "held":
        raise FlowError(f"Cannot confirm reservation in status '{res.status}'")
    # A hold past its expiry that the reaper has not reached yet is still a
    # hold: its rooms are still counted in held_units, and the reaper skips a
    # booking that is locked -- as this one now is -- so confirming it here is
    # safe and is exactly what a guest paying at 14:59:59 deserves.

    units = session.execute(
        text(
            """
            SELECT id, room_type_id, arrival_date, departure_date
            FROM booking.reservation_units
            WHERE reservation_id = :rid AND status = 'reserved'
            """
        ),
        {"rid": reservation_id},
    ).all()

    # Every night the booking holds, per room type, locked once and in
    # (room_type, date) order -- the order create_hold and shift_inventory
    # take them in. This used to lock unit by unit in whatever order the units
    # came back, so confirming a two-type booking could take the second
    # type's rows first and deadlock against a new booking for both.
    need: dict[tuple[uuid.UUID, date], int] = {}
    for unit in units:
        for day in _nights(unit.arrival_date, unit.departure_date):
            key = (unit.room_type_id, day)
            need[key] = need.get(key, 0) + 1
    keys = sorted(need, key=lambda k: (str(k[0]), k[1]))
    if keys:
        session.execute(
            text(
                """
                SELECT 1 FROM booking.room_type_inventory_days
                WHERE property_id = :prop
                  AND (room_type_id, stay_date) IN (
                      SELECT * FROM unnest(CAST(:rts AS uuid[]),
                                           CAST(:days AS date[])))
                ORDER BY room_type_id, stay_date
                FOR UPDATE
                """
            ),
            {"prop": res.property_id, "rts": [k[0] for k in keys],
             "days": [k[1] for k in keys]},
        )
    for rt, day in keys:
        session.execute(
            text(
                """
                UPDATE booking.room_type_inventory_days
                SET held_units = GREATEST(held_units - :n, 0),
                    reserved_units = reserved_units + :n
                WHERE property_id = :prop AND room_type_id = :rt
                  AND stay_date = :d
                """
            ),
            {"n": need[(rt, day)], "prop": res.property_id, "rt": rt,
             "d": day},
        )

    session.execute(
        text(
            "UPDATE booking.reservations "
            "SET status = 'confirmed', version = version + 1 WHERE id = :id"
        ),
        {"id": reservation_id},
    )
    session.execute(
        text(
            "UPDATE booking.booking_holds SET status = 'converted' "
            "WHERE reservation_id = :id AND status = 'held'"
        ),
        {"id": reservation_id},
    )
    enqueue_event(
        session,
        aggregate_type="reservation",
        aggregate_id=str(reservation_id),
        event_type="booking.reservation_confirmed",
        payload={"reservation_id": str(reservation_id)},
    )
    return True


def _expired_hold(session: Session, reservation_id: uuid.UUID) -> bool:
    """Whether this booking was cancelled by the hold reaper, and nothing else.

    Only a hold the reaper expired is eligible to be brought back. A booking
    the desk cancelled (its hold is 'released'), or one a guest cancelled, is
    a decision somebody made; a payment arriving late does not overturn it.
    """
    return session.execute(
        text("SELECT 1 FROM booking.booking_holds "
             "WHERE reservation_id = :r AND status = 'expired' LIMIT 1"),
        {"r": reservation_id},
    ).first() is not None


def _revive_expired_hold(session: Session, res) -> None:
    """Confirm a booking whose hold expired, if its rooms are still free.

    The reaper gave the rooms back and cancelled the units. They are taken
    again straight into ``reserved_units`` -- with the same oversell check a
    new booking gets -- and the units and the booking are restored. If any
    night has been sold in the meantime the whole thing is refused and nothing
    changes: the guest cannot be given a room that is now somebody else's.
    """
    from .inventory import InventoryOversold, occupancy, shift_inventory
    from .settings import settings

    if res.group_block_id is not None:
        # The reaper handed this booking's rooms back to its block, and
        # taking them again would have to draw the block down under its own
        # rules. Safer to say so than to half-do it.
        raise FlowError(
            f"The hold on {res.number} expired before payment arrived, and it "
            f"was drawn from a group block. Rebook it against the block; the "
            f"payment needs refunding or moving to the new booking.",
            conflict=True)
    units = session.execute(
        text(
            """
            SELECT id, room_type_id, arrival_date, departure_date
            FROM booking.reservation_units
            WHERE reservation_id = :rid AND status = 'cancelled'
            """
        ),
        {"rid": res.id},
    ).mappings().all()
    if not units:
        raise FlowError(f"{res.number} has no rooms left to confirm.",
                        conflict=True)
    try:
        shift_inventory(
            session, property_id=res.property_id,
            organization_id=res.organization_id, counter="reserved_units",
            delta=occupancy(units),
            overbooking_allowance=settings.overbooking_allowance)
    except InventoryOversold as exc:
        raise FlowError(
            f"The hold on {res.number} expired before payment arrived, and "
            f"{exc} The booking cannot be confirmed; the payment needs "
            f"refunding.",
            conflict=True) from exc

    session.execute(
        text("UPDATE booking.reservation_units SET status = 'reserved', "
             "version = version + 1 WHERE reservation_id = :r "
             "AND status = 'cancelled'"),
        {"r": res.id},
    )
    session.execute(
        text("UPDATE booking.reservations SET status = 'confirmed', "
             "version = version + 1 WHERE id = :id AND status = 'cancelled'"),
        {"id": res.id},
    )
    session.execute(
        text("UPDATE booking.booking_holds SET status = 'converted', "
             "updated_at = now() WHERE reservation_id = :id "
             "AND status = 'expired'"),
        {"id": res.id},
    )
    enqueue_event(
        session,
        aggregate_type="reservation",
        aggregate_id=str(res.id),
        event_type="booking.reservation_confirmed",
        payload={"reservation_id": str(res.id), "revived_expired_hold": True},
    )


# --------------------------------------------------------------------------
# 2) Assign a physical room via the room calendar (GiST-guarded)
# --------------------------------------------------------------------------
@dataclass
class AssignResult:
    room_calendar_entry_id: uuid.UUID
    room_id: uuid.UUID


def _local_period(
    arrival: date, departure: date, checkin_t: str | None, checkout_t: str | None
) -> tuple[datetime, datetime]:
    """Build a UTC half-open [start,end) period from planned dates + times.

    Times are property-local wall clocks; for the scaffold we treat them as UTC.
    A production build converts from the property IANA timezone (§4).
    """
    ci = time.fromisoformat(checkin_t) if checkin_t else time(14, 0)
    co = time.fromisoformat(checkout_t) if checkout_t else time(11, 0)
    start = datetime.combine(arrival, ci, tzinfo=timezone.utc)
    end = datetime.combine(departure, co, tzinfo=timezone.utc)
    return start, end


def assign_room(
    session: Session,
    *,
    reservation_unit_id: uuid.UUID,
    room_id: uuid.UUID,
    checkin_time: str | None = None,
    checkout_time: str | None = None,
) -> AssignResult:
    unit = _unit_row(session, reservation_unit_id)
    if unit.reservation_status not in ("confirmed", "held"):
        raise FlowError(
            f"Cannot assign room to reservation in status "
            f"'{unit.reservation_status}'"
        )
    if unit.assigned_room_id is not None:
        raise FlowError("Reservation unit already has a room assigned")

    # Room must exist, belong to the same property, and match the room type (§4).
    room = session.execute(
        text(
            """
            SELECT id FROM property.rooms
            WHERE id = :rid AND property_id = :prop AND room_type_id = :rt
            """
        ),
        {"rid": room_id, "prop": unit.property_id, "rt": unit.room_type_id},
    ).first()
    if room is None:
        raise FlowError(
            "Room not found, or does not match the unit's property/room type"
        )

    start, end = _local_period(
        unit.arrival_date, unit.departure_date, checkin_time, checkout_time
    )
    entry_id = uuid.uuid4()
    try:
        session.execute(
            text(
                """
                INSERT INTO booking.room_calendar_entries
                    (id, organization_id, property_id, room_id,
                     reservation_unit_id, kind, occupied_period, status, reason)
                VALUES (:id, :org, :prop, :room, :unit, 'reservation',
                        tstzrange(:start, :end, '[)'), 'active', NULL)
                """
            ),
            {
                "id": entry_id,
                "org": unit.organization_id,
                "prop": unit.property_id,
                "room": room_id,
                "unit": reservation_unit_id,
                "start": start,
                "end": end,
            },
        )
        # Force constraint evaluation now so we can translate to a clean 409.
        session.flush()
    except IntegrityError as exc:
        # GiST exclusion (or overlapping maintenance block) rejected the insert.
        raise RoomCollision(room_id) from exc

    session.execute(
        text(
            "UPDATE booking.reservation_units "
            "SET assigned_room_id = :room, version = version + 1 WHERE id = :id"
        ),
        {"room": room_id, "id": reservation_unit_id},
    )
    enqueue_event(
        session,
        aggregate_type="reservation_unit",
        aggregate_id=str(reservation_unit_id),
        event_type="booking.room_assigned",
        payload={
            "reservation_unit_id": str(reservation_unit_id),
            "room_id": str(room_id),
            "room_calendar_entry_id": str(entry_id),
        },
    )
    return AssignResult(room_calendar_entry_id=entry_id, room_id=room_id)


# --------------------------------------------------------------------------
# 3) Check-in: create the stay
# --------------------------------------------------------------------------
@dataclass
class CheckInResult:
    stay_id: uuid.UUID


def check_in(
    session: Session,
    *,
    reservation_unit_id: uuid.UUID,
    checked_in_by: uuid.UUID | None = None,
) -> CheckInResult:
    unit = _unit_row(session, reservation_unit_id)
    if unit.unit_status == "checked_in":
        raise FlowError("Reservation unit is already checked in")
    if unit.unit_status != "reserved":
        raise FlowError(
            f"Cannot check in a unit in status '{unit.unit_status}'"
        )
    if unit.reservation_status != "confirmed":
        raise FlowError("Reservation must be confirmed before check-in")
    if unit.assigned_room_id is None:
        raise FlowError("A physical room must be assigned before check-in")

    # Locate the active calendar entry for this unit to seed the stay segment.
    entry = session.execute(
        text(
            """
            SELECT id FROM booking.room_calendar_entries
            WHERE reservation_unit_id = :unit AND status = 'active'
            ORDER BY created_at DESC LIMIT 1
            """
        ),
        {"unit": reservation_unit_id},
    ).first()
    if entry is None:
        raise FlowError("No active room calendar entry for this unit")

    stay_id = uuid.uuid4()
    session.execute(
        text(
            """
            INSERT INTO booking.stays
                (id, organization_id, property_id, reservation_unit_id,
                 actual_checkin_at, checked_in_by, status)
            VALUES (:id, :org, :prop, :unit, now(), :by, 'in_house')
            """
        ),
        {
            "id": stay_id,
            "org": unit.organization_id,
            "prop": unit.property_id,
            "unit": reservation_unit_id,
            "by": checked_in_by,
        },
    )
    session.execute(
        text(
            """
            INSERT INTO booking.stay_room_segments
                (organization_id, property_id, stay_id, room_calendar_entry_id,
                 actual_start_at)
            VALUES (:org, :prop, :stay, :entry, now())
            """
        ),
        {
            "org": unit.organization_id,
            "prop": unit.property_id,
            "stay": stay_id,
            "entry": entry.id,
        },
    )
    session.execute(
        text(
            "UPDATE booking.reservation_units "
            "SET status = 'checked_in', version = version + 1 WHERE id = :id"
        ),
        {"id": reservation_unit_id},
    )
    # Room becomes occupied+dirty-on-use: mark as occupied via room_condition.
    session.execute(
        text(
            """
            INSERT INTO operations.room_condition
                (room_id, organization_id, property_id, cleanliness)
            VALUES (:room, :org, :prop, 'clean')
            ON CONFLICT (room_id) DO NOTHING
            """
        ),
        {
            "room": unit.assigned_room_id,
            "org": unit.organization_id,
            "prop": unit.property_id,
        },
    )
    record_status_event(
        session, organization_id=unit.organization_id, property_id=unit.property_id,
        room_id=unit.assigned_room_id, status="occupied",
        source="reservation_checkin", changed_by=checked_in_by,
        source_reference=unit.reservation_number,
        remarks="Guest checked in",
    )
    enqueue_event(
        session,
        aggregate_type="stay",
        aggregate_id=str(stay_id),
        event_type="stay.checked_in",
        payload={
            "stay_id": str(stay_id),
            "reservation_unit_id": str(reservation_unit_id),
            "room_id": str(unit.assigned_room_id),
        },
    )
    return CheckInResult(stay_id=stay_id)


# --------------------------------------------------------------------------
# 4) Checkout: end the stay, release future nights, emit room-dirty
# --------------------------------------------------------------------------
@dataclass
class CheckOutResult:
    stay_id: uuid.UUID
    nights_released: int


def check_out(
    session: Session,
    *,
    reservation_unit_id: uuid.UUID,
    checked_out_by: uuid.UUID | None = None,
    business_date: date | None = None,
) -> CheckOutResult:
    unit = _unit_row(session, reservation_unit_id)
    if unit.unit_status == "checked_out":
        raise FlowError("Reservation unit is already checked out")
    if unit.unit_status != "checked_in":
        raise FlowError(
            f"Cannot check out a unit in status '{unit.unit_status}'"
        )

    stay = session.execute(
        text(
            """
            SELECT id FROM booking.stays
            WHERE reservation_unit_id = :unit AND status = 'in_house'
            FOR UPDATE
            """
        ),
        {"unit": reservation_unit_id},
    ).first()
    if stay is None:
        raise FlowError("No in-house stay found for this unit")

    # The property's calendar date when the caller names none. UTC's date
    # lagged India by 5.5 hours, so an early check-out between midnight and
    # 05:30 kept the night that had just begun off sale.
    if business_date is None:
        from chirala_common.property_time import local_today

        business_date = local_today(session, unit.property_id)
    today = business_date

    # Early checkout: return the nights the guest did not stay, keeping the
    # ones they did as occupied history (§4).
    #
    # Nights are half-open -- ``_nights`` runs [arrival, departure), so the
    # departure date is never an occupied night. A guest leaving on date D has
    # therefore stayed [arrival, D), and night D is nobody's. This released
    # only ``d > today``, keeping the checkout night itself off sale: a guest
    # who left this morning still held a unit tonight, so the room type showed
    # one fewer than the rack, whose calendar entry was already released.
    future_nights = [
        d
        for d in _nights(unit.arrival_date, unit.departure_date)
        if d >= today
    ]
    nights_released = 0
    if future_nights:
        session.execute(
            text(
                """
                SELECT stay_date FROM booking.room_type_inventory_days
                WHERE property_id = :prop AND room_type_id = :rt
                  AND stay_date = ANY(:dates)
                ORDER BY stay_date
                FOR UPDATE
                """
            ),
            {"prop": unit.property_id, "rt": unit.room_type_id, "dates": future_nights},
        )
        session.execute(
            text(
                """
                UPDATE booking.room_type_inventory_days
                SET reserved_units = GREATEST(reserved_units - 1, 0)
                WHERE property_id = :prop AND room_type_id = :rt
                  AND stay_date = ANY(:dates)
                """
            ),
            {"prop": unit.property_id, "rt": unit.room_type_id, "dates": future_nights},
        )
        nights_released = len(future_nights)

    # Close stay + segment.
    session.execute(
        text(
            "UPDATE booking.stays SET status = 'checked_out', "
            "actual_checkout_at = now(), checked_out_by = :by, "
            "version = version + 1 WHERE id = :id"
        ),
        {"by": checked_out_by, "id": stay.id},
    )
    session.execute(
        text(
            "UPDATE booking.stay_room_segments SET actual_end_at = now() "
            "WHERE stay_id = :id AND actual_end_at IS NULL"
        ),
        {"id": stay.id},
    )
    # Release the room calendar entry so the room frees up for future bookings.
    session.execute(
        text(
            "UPDATE booking.room_calendar_entries SET status = 'released', "
            "version = version + 1 "
            "WHERE reservation_unit_id = :unit AND status = 'active'"
        ),
        {"unit": reservation_unit_id},
    )
    session.execute(
        text(
            "UPDATE booking.reservation_units SET status = 'checked_out', "
            "version = version + 1 WHERE id = :id"
        ),
        {"id": reservation_unit_id},
    )

    # Room becomes dirty on checkout (§5), emitted atomically (§5 outbox).
    if unit.assigned_room_id is not None:
        session.execute(
            text(
                """
                INSERT INTO operations.room_condition
                    (room_id, organization_id, property_id, cleanliness)
                VALUES (:room, :org, :prop, 'dirty')
                ON CONFLICT (room_id) DO UPDATE
                    SET cleanliness = 'dirty', updated_at = now(),
                        version = operations.room_condition.version + 1
                """
            ),
            {
                "room": unit.assigned_room_id,
                "org": unit.organization_id,
                "prop": unit.property_id,
            },
        )
        record_status_event(
            session, organization_id=unit.organization_id,
            property_id=unit.property_id, room_id=unit.assigned_room_id,
            status="dirty", source="reservation_checkout",
            changed_by=checked_out_by, source_reference=unit.reservation_number,
            remarks="Guest checked out",
        )
        enqueue_event(
            session,
            aggregate_type="room",
            aggregate_id=str(unit.assigned_room_id),
            event_type="housekeeping.room_dirty",
            payload={
                "room_id": str(unit.assigned_room_id),
                "reservation_unit_id": str(reservation_unit_id),
            },
        )

    # Complete the reservation when all its units are checked out/cancelled.
    remaining = session.execute(
        text(
            """
            SELECT count(*) FROM booking.reservation_units
            WHERE reservation_id = :rid
              AND status NOT IN ('checked_out','cancelled','no_show')
            """
        ),
        {"rid": unit.reservation_id},
    ).scalar_one()
    if remaining == 0:
        session.execute(
            text(
                "UPDATE booking.reservations SET status = 'completed', "
                "version = version + 1 WHERE id = :id"
            ),
            {"id": unit.reservation_id},
        )

    enqueue_event(
        session,
        aggregate_type="stay",
        aggregate_id=str(stay.id),
        event_type="stay.checked_out",
        payload={
            "stay_id": str(stay.id),
            "reservation_unit_id": str(reservation_unit_id),
            "nights_released": nights_released,
        },
    )
    return CheckOutResult(stay_id=stay.id, nights_released=nights_released)
