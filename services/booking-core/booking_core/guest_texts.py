"""The guest's phone: booking confirmation and check-in welcome.

The SMS and WhatsApp counterpart of ``guest_mail.py``. This module only
gathers what a message needs from bookings this service owns. Whether to send,
on which channel and with which template is decided in
``chirala_common.guest_messages``, so finance sends its reminders the same
way.

The same rule as the confirmation email applies: these are called after the
booking or check-in has committed, never inside it. A text cannot be unsent.
"""
from __future__ import annotations

import uuid
from datetime import date

from chirala_common.guest_messages import Outcome, notify_guest
from sqlalchemy import text
from sqlalchemy.orm import Session

from .database import SessionFactory
from .settings import settings

_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep",
           "Oct", "Nov", "Dec")


def short_date(d: date | None) -> str:
    """5 Oct 2026. Spelled, because 05/10 reads differently abroad."""
    return f"{d.day} {_MONTHS[d.month - 1]} {d.year}" if d else ""


def message_confirmation(session: Session, reservation_id: uuid.UUID,
                         *, force: bool = False) -> list[Outcome]:
    row = session.execute(
        text("""
            SELECT r.number, r.organization_id, r.property_id,
                   g.full_name AS guest_name, g.phone,
                   p.name AS property_name,
                   (SELECT min(u.arrival_date) FROM booking.reservation_units u
                     WHERE u.reservation_id = r.id AND u.status <> 'cancelled')
                     AS arrival,
                   (SELECT max(u.departure_date) FROM booking.reservation_units u
                     WHERE u.reservation_id = r.id AND u.status <> 'cancelled')
                     AS departure
            FROM booking.reservations r
            LEFT JOIN engagement.guests g ON g.id = r.primary_guest_id
            JOIN iam.properties p ON p.id = r.property_id
            WHERE r.id = :r
        """),
        {"r": reservation_id},
    ).mappings().first()
    if row is None:
        return []
    return notify_guest(
        session, SessionFactory, settings.messaging_config,
        code="guest_booking_confirmed",
        organization_id=row["organization_id"], property_id=row["property_id"],
        phone=row["phone"], force=force,
        values={
            "guest_name": row["guest_name"] or "Guest",
            "property_name": row["property_name"],
            "reservation_number": row["number"],
            "arrival": short_date(row["arrival"]),
            "departure": short_date(row["departure"]),
        },
    )


def message_welcome(session: Session, unit_id: uuid.UUID,
                    *, force: bool = False) -> list[Outcome]:
    row = session.execute(
        text("""
            SELECT r.organization_id, r.property_id,
                   g.full_name AS guest_name, g.phone,
                   p.name AS property_name, u.departure_date,
                   rm.code AS room_number
            FROM booking.reservation_units u
            JOIN booking.reservations r ON r.id = u.reservation_id
            LEFT JOIN engagement.guests g ON g.id = r.primary_guest_id
            JOIN iam.properties p ON p.id = r.property_id
            LEFT JOIN property.rooms rm ON rm.id = u.assigned_room_id
            WHERE u.id = :u
        """),
        {"u": unit_id},
    ).mappings().first()
    if row is None:
        return []
    return notify_guest(
        session, SessionFactory, settings.messaging_config,
        code="guest_checkin_welcome",
        organization_id=row["organization_id"], property_id=row["property_id"],
        phone=row["phone"], force=force,
        values={
            "guest_name": row["guest_name"] or "Guest",
            "property_name": row["property_name"],
            "room_number": row["room_number"] or "",
            "checkout_date": short_date(row["departure_date"]),
        },
    )
