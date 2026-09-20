#!/usr/bin/env python
"""Drive a channel booking through the PMS without a channel manager.

The webhook half of this integration is testable against the real thing — a
secret is either right or wrong, a retry is either suppressed or it is not. The
half that is not testable that way is everything after the fetch: resolving the
channel's room code to one of ours, finding or creating the guest, taking the
room out of inventory, pricing it at what the channel sold it for, and
recording what happened. That work only runs when a real booking arrives, and
waiting for one to be possible is not a reason to leave it unexercised.

So this builds a revision shaped exactly as Channex documents one and hands it
to the same function the webhook hands its fetched payload to. Nothing is
stubbed past that point: the reservation, the guest, the inventory movement and
the event row are all real.

    docker compose exec -T booking-core python /app/scripts/simulate_channel_booking.py
    docker compose exec -T booking-core python /app/scripts/simulate_channel_booking.py --room "Suite"
    docker compose exec -T booking-core python /app/scripts/simulate_channel_booking.py --unmapped

``--unmapped`` sends a room code that maps to nothing, which is the failure a
person has to act on: the booking exists at the OTA and does not exist here.
Worth being able to produce on demand, because it is the one nobody tests until
it happens at four in the morning.

**Not a substitute for a real booking.** It proves our side handles a revision
correctly; it cannot prove Channex is pointed at us, which is the other way
this fails.
"""
from __future__ import annotations

import argparse
import sys
import uuid
from datetime import date, timedelta

from sqlalchemy import text

from booking_core.channel_routes import _apply
from booking_core.database import SessionFactory


def revision(db, room_name: str | None, unmapped: bool, nights: int,
             days_out: int) -> dict:
    """A Channex booking revision, in their documented shape."""
    conn = db.execute(
        text("SELECT id, property_id, external_property_id "
             "FROM distribution.channel_connections "
             "WHERE external_property_id IS NOT NULL LIMIT 1"),
    ).mappings().first()
    if conn is None:
        raise SystemExit(
            "No channel connection with a channel property id. Create one on "
            "/channels/add first.")

    if unmapped:
        external_room = "not-a-mapped-room-" + uuid.uuid4().hex[:8]
        label = "(deliberately unmapped)"
    else:
        sql = ("SELECT m.external_id, rt.name FROM "
               "distribution.channel_room_mappings m "
               "JOIN property.room_types rt ON rt.id = m.room_type_id "
               "WHERE m.connection_id = :c")
        params = {"c": conn["id"]}
        if room_name:
            sql += " AND rt.name = :n"
            params["n"] = room_name
        row = db.execute(text(sql + " LIMIT 1"), params).mappings().first()
        if row is None:
            raise SystemExit(
                f"No mapped room{' called ' + room_name if room_name else ''}. "
                f"Map one in Setup first.")
        external_room, label = row["external_id"], row["name"]

    arrival = date.today() + timedelta(days=days_out)
    departure = arrival + timedelta(days=nights)
    print(f"  room     {label}  ->  {external_room}")
    print(f"  stay     {arrival} .. {departure}  ({nights} night"
          f"{'s' if nights > 1 else ''})")

    return {
        "id": f"sim-{uuid.uuid4()}",
        "property_id": conn["external_property_id"],
        "booking_id": f"sim-booking-{uuid.uuid4().hex[:12]}",
        "ota_reservation_code": f"TEST-{uuid.uuid4().hex[:8].upper()}",
        "ota_name": "Booking.com",
        "status": "new",
        "arrival_date": arrival.isoformat(),
        "departure_date": departure.isoformat(),
        "amount": str(2000 * nights),
        "currency": "INR",
        "occupancy": {"adults": 2, "children": 0, "infants": 0},
        "rooms": [{
            "room_type_id": external_room,
            "rate_plan_id": "sim-rate-plan",
            "checkin_date": arrival.isoformat(),
            "checkout_date": departure.isoformat(),
            "occupancy": {"adults": 2, "children": 0, "infants": 0},
            "amount": str(2000 * nights),
            "guests": [{"name": "Simulated", "surname": "Guest"}],
        }],
        "customer": {
            "name": "Simulated", "surname": "Guest",
            "mail": "sim.guest@example.com", "phone": "9876500000",
        },
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--room", help="Map this room type by name")
    ap.add_argument("--unmapped", action="store_true",
                    help="Send a room code that maps to nothing")
    ap.add_argument("--nights", type=int, default=2)
    ap.add_argument("--days-out", type=int, default=30)
    args = ap.parse_args()

    with SessionFactory() as db:
        rev = revision(db, args.room, args.unmapped, args.nights,
                       args.days_out)
        rid = rev["id"]

        # Claimed exactly as the webhook claims it, so the duplicate guard is
        # part of what is being tested rather than bypassed.
        db.execute(
            text("INSERT INTO distribution.channel_booking_events "
                 "(revision_id, provider, event_type, outcome) "
                 "VALUES (:r, 'channex', 'booking_new', 'claimed')"),
            {"r": rid},
        )

        print("\n  applying...")
        result = _apply(db, rid, rev)
        db.commit()

        print(f"  -> {result}")

        row = db.execute(
            text("SELECT outcome, detail, reservation_id, acknowledged "
                 "FROM distribution.channel_booking_events "
                 "WHERE revision_id = :r"),
            {"r": rid},
        ).mappings().first()
        print(f"\n  outcome        {row['outcome']}")
        print(f"  detail         {row['detail']}")
        print(f"  acknowledged   {row['acknowledged']}")

        if row["reservation_id"]:
            res = db.execute(
                text("SELECT r.number, r.status, g.full_name, "
                     "min(u.arrival_date) AS arr, max(u.departure_date) AS dep,"
                     " sum(u.nightly_rate) AS rate "
                     "FROM booking.reservations r "
                     "LEFT JOIN engagement.guests g ON g.id = r.primary_guest_id "
                     "LEFT JOIN booking.reservation_units u "
                     "ON u.reservation_id = r.id "
                     "WHERE r.id = :i GROUP BY r.number, r.status, g.full_name"),
                {"i": row["reservation_id"]},
            ).mappings().first()
            if res:
                print(f"\n  reservation    {res['number']}  ({res['status']})")
                print(f"  guest          {res['full_name']}")
                print(f"  stay           {res['arr']} .. {res['dep']}")
                print(f"  nightly rate   {res['rate']}")
        return 0 if row["outcome"] in ("created", "unmapped") else 1


if __name__ == "__main__":
    sys.exit(main())
