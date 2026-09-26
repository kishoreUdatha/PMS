"""The three figures every stay list shows: what the stay comes to, paid, owed.

Arrivals, In-house and Departures all show the same money, so it is defined
once here rather than three times.

``total`` used to be simply the posted charges, and that was wrong in a way
that only showed up once bookings were taken in advance. A room night is
charged the night it is slept in, so a guest arriving tomorrow has nothing on
their folio -- and the list rendered their stay as 0.00 next to a payment of
several thousand, with a negative balance implying the hotel owed them money.
A desk looking at that cannot tell a prepaid booking from one with no rate
loaded.

So the total is what the stay is *expected* to come to: the rate it was sold
at, for every room, for the whole stay, plus whatever extras have reached the
folio. Once the audit starts posting room charges the two converge, and
``GREATEST`` keeps whichever is larger -- a posted charge that exceeds the
booked rate (an upgrade, a correction) is the one that actually happened.

``charged`` is still reported separately and unchanged, because "what is on
the folio right now" is a real question the folio screen answers and these
lists should not quietly redefine.

It is an aggregate rather than a fetch-and-sum: the list endpoints run this per
row, and pulling every folio entry back into Python to add it up made a
200-guest list do 200 line-by-line reads for three numbers.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

from sqlalchemy import text
from sqlalchemy.orm import Session

#: Charges that ARE the room, and would therefore be counted twice if added to
#: the stay's booked value rather than compared against it.
_ROOM_SOURCES = "('room_night', 'room_stay', 'room_upgrade')"

_SQL = text(
    f"""
    SELECT
        max(f.id::text) AS folio_id,
        count(f.id) > 0 AS has_folio,
        COALESCE(sum(fe.amount) FILTER (
            WHERE fe.entry_type = 'debit'
              AND COALESCE(fe.source_type, '') NOT IN ('refund', 'deposit_refund')
        ), 0) AS charged,
        COALESCE(sum(fe.amount) FILTER (
            WHERE fe.entry_type = 'debit'
              AND COALESCE(fe.source_type, '') IN {_ROOM_SOURCES}
        ), 0) AS room_charges,
        -- Extras: the bar, a penalty, exclusive tax. Additive to the room,
        -- never a duplicate of it.
        COALESCE(sum(fe.amount) FILTER (
            WHERE fe.entry_type = 'debit'
              AND COALESCE(fe.source_type, '') NOT IN {_ROOM_SOURCES}
              AND COALESCE(fe.source_type, '') NOT IN
                  ('payment', 'refund', 'security_deposit')
        ), 0) AS other_charges,
        -- Net of refunds. A refund is a debit, so summing credits alone
        -- counts money that has already gone back out.
        COALESCE(sum(fe.amount) FILTER (WHERE fe.entry_type = 'credit'), 0)
        - COALESCE(sum(fe.amount) FILTER (
              WHERE fe.entry_type = 'debit'
                AND COALESCE(fe.source_type, '') IN ('refund', 'deposit_refund')
          ), 0) AS paid,
        -- What the stay was sold for. Independent of the folio, so it is
        -- there before a single charge has been posted -- which is the whole
        -- reason this column exists.
        COALESCE((
            SELECT sum(COALESCE(u.nightly_rate, rtp.base_rate, 0) * GREATEST(
                LEAST(
                    u.departure_date,
                    -- Still here, or never checked in: the booking stands.
                    COALESCE(
                        (st.actual_checkout_at
                         AT TIME ZONE COALESCE(pr.timezone, 'Asia/Kolkata')
                        )::date,
                        u.departure_date)
                ) - u.arrival_date, 0))
            FROM booking.reservation_units u
            LEFT JOIN booking.stays st ON st.reservation_unit_id = u.id
            LEFT JOIN iam.properties pr ON pr.id = u.property_id
            -- A unit booked without an explicit rate falls back to its room
            -- type's. The booking screen quotes a rate and stores it, but
            -- /holds does not require one, so a reservation arriving through
            -- the API -- which is how an OTA or channel booking arrives --
            -- had a NULL rate and was therefore worth nothing on every screen
            -- that asked. The rack already falls back this way; this agrees
            -- with it rather than inventing a second answer.
            LEFT JOIN property.room_types rtp ON rtp.id = u.room_type_id
            WHERE u.reservation_id = :r
              AND u.status NOT IN ('cancelled', 'no_show')
        ), 0) AS room_value
    FROM finance.folios f
    LEFT JOIN finance.folio_entries fe ON fe.folio_id = f.id
    WHERE f.reservation_id = :r
    """
)


def folio_money(db: Session, reservation_id: uuid.UUID) -> dict:
    """``{folio_id, has_folio, total, charged, paid, balance}``.

    ``total`` is what the stay is expected to come to -- the booking's value
    while it is still ahead, what was actually slept once it is over;
    ``paid`` is net of refunds. ``charged`` is what has
    actually been posted. They differ for every booking that has not been
    stayed yet, which is most of them on an arrivals list.

    ``has_folio`` is reported separately because "no folio has been opened" and
    "a folio with nothing on it" are different facts, and a list that renders
    both as 0.00 reads as a stay worth nothing. The bookings list already drew
    that distinction; carrying it here lets every stay list draw it the same
    way.
    """
    row = db.execute(_SQL, {"r": reservation_id}).mappings().first()
    charged = Decimal(row["charged"] if row else 0)
    paid = Decimal(row["paid"] if row else 0)
    room_value = Decimal(row["room_value"] if row else 0)
    room_charges = Decimal(row["room_charges"] if row else 0)
    other_charges = Decimal(row["other_charges"] if row else 0)
    total = max(room_value, room_charges) + other_charges
    return {"folio_id": (uuid.UUID(row["folio_id"])
                        if row and row["folio_id"] else None),
            "has_folio": bool(row and row["has_folio"]),
            "total": total, "charged": charged,
            "paid": paid, "balance": total - paid}


def primary_folio(db: Session, reservation_id: uuid.UUID
                  ) -> tuple[uuid.UUID | None, Decimal]:
    """The folio a booking's own charges go to, and what has been paid on it.

    **Which folio** is decided, not left to the database. A booking can have
    several -- a group master beside each guest's, a split folio for the
    company -- and ``GROUP BY f.id LIMIT 1`` returned whichever the planner
    happened to meet first, so a cancellation fee could land on a guest's bar
    folio one day and the master the next. The rule is the night audit's: the
    group master first (the organiser agreed to pay for rooms), then the
    oldest.

    **Paid** is net of refunds. Summing credits alone counted money that had
    already gone back to the guest, so a cancellation after a refund quoted a
    second refund of the same money.
    """
    row = db.execute(
        text(
            """
            SELECT f.id AS folio_id,
                   COALESCE(SUM(e.amount) FILTER (
                       WHERE e.entry_type = 'credit'), 0)
                   - COALESCE(SUM(e.amount) FILTER (
                       WHERE e.entry_type = 'debit'
                         AND e.source_type IN ('refund', 'deposit_refund')),
                     0) AS paid
            FROM finance.folios f
            LEFT JOIN finance.folio_entries e ON e.folio_id = f.id
            WHERE f.reservation_id = :r
            GROUP BY f.id, f.type, f.created_at
            ORDER BY (f.type = 'group') DESC, f.created_at, f.id
            LIMIT 1
            """
        ),
        {"r": reservation_id},
    ).mappings().first()
    return (row["folio_id"], Decimal(row["paid"])) if row else (None, Decimal("0"))
