"""Obligations to a channel, raised once wherever the event happens.

A no-show on an OTA booking has to be reported to that OTA within **24 hours**
or the hotel pays commission on a room nobody slept in. Two code paths can
record a no-show -- the manual screen in booking-core and the night audit in
finance -- and both must raise the same obligation, which is why this lives in
the shared library rather than in either of them.

Raising is idempotent by the table's own unique index: the audit and a clerk
can both reach the same room, the same way their penalty shares an idempotency
key, and two rows would mean two people ringing the same OTA about one booking.

A booking that did not come from a channel raises nothing. There is nobody to
tell, and a queue padded with rows that need no action is a queue people stop
reading.
"""

from __future__ import annotations

import uuid

from sqlalchemy import text
from sqlalchemy.orm import Session

#: What the hotel may owe a channel. Kept in step with ACTION_TYPES in
#: booking-core migration 0057.
ACTION_TYPES = ("no_show", "cancellation", "invalid_card")

LABELS: dict[str, str] = {
    "no_show": "Report no-show",
    "cancellation": "Report cancellation",
    "invalid_card": "Report invalid card",
}

#: How long the hotel has. Booking.com, Expedia and Agoda all work to a day
#: from the missed arrival; past it the commission stands and no amount of
#: explaining gets it back.
WINDOW_HOURS = 24


def channel_origin(db: Session, reservation_id: uuid.UUID) -> dict | None:
    """The OTA this booking came from, or None if it did not come from one.

    The extranet is searched by the channel's own reference, so a row without
    one cannot be acted on; it is still raised, because "a no-show on a
    Booking.com reservation whose code we have lost" is a thing somebody needs
    to go and find, not a thing to hide.
    """
    row = db.execute(
        text(
            """
            SELECT ota_name, ota_reservation_code
              FROM distribution.channel_booking_events
             WHERE reservation_id = :r
             ORDER BY received_at DESC
             LIMIT 1
            """
        ),
        {"r": reservation_id},
    ).mappings().first()
    return dict(row) if row else None


def raise_action(
    db: Session,
    *,
    organization_id: uuid.UUID,
    property_id: uuid.UUID,
    reservation_id: uuid.UUID,
    reservation_unit_id: uuid.UUID,
    reservation_number: str | None,
    action_type: str,
) -> bool:
    """Record that this room has to be reported to its channel.

    Returns True when a new obligation was created, False when the booking did
    not come from a channel or the obligation already existed. Callers use it
    only to decide what to say; nothing here has an effect outside the
    database, so a repeat is free.

    Must run inside the caller's transaction. An obligation that survives a
    rolled-back no-show would have somebody ring an OTA about a guest who is
    actually in the building.
    """
    if action_type not in ACTION_TYPES:
        raise ValueError(f"Unknown OTA action {action_type!r}")

    origin = channel_origin(db, reservation_id)
    if origin is None:
        return False

    done = db.execute(
        text(
            """
            INSERT INTO distribution.ota_actions
                (id, organization_id, property_id, reservation_id,
                 reservation_unit_id, ota_name, ota_reservation_code,
                 reservation_number, action_type, status, due_at)
            VALUES (:id, :org, :prop, :res, :unit, :ota, :code, :number,
                    :kind, 'open', now() + make_interval(hours => :hours))
            ON CONFLICT (reservation_unit_id, action_type) DO NOTHING
            """
        ),
        {"id": uuid.uuid4(), "org": organization_id, "prop": property_id,
         "res": reservation_id, "unit": reservation_unit_id,
         "ota": origin.get("ota_name"),
         "code": origin.get("ota_reservation_code"),
         "number": reservation_number, "kind": action_type,
         "hours": WINDOW_HOURS},
    ).rowcount
    return bool(done)
