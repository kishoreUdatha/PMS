#!/usr/bin/env python
"""Watch bookings arriving from the channel manager, as they arrive.

    python scripts/watch_channel_bookings.py

Every webhook that reaches this deployment is recorded in
``distribution.channel_booking_events`` before anything is decided about it,
which is what makes this possible: a booking that could not be turned into a
reservation is still here, with the reason, rather than lost in a log.

Written for the moment somebody makes a test booking on an OTA and wants to
see whether it arrived. Watching the reservations list tells you only whether
it worked; this tells you *where* it stopped when it did not.

The outcomes, in the order they are reached:

``claimed``     the webhook arrived and was accepted for processing
``unmapped``    routed to a property, but a room or the property itself has
                no mapping — deliberately left unacknowledged so the channel
                manager offers it again once the mapping is fixed
``cancelled``   a cancellation, which the desk has to process
``failed``      the revision could not be fetched or carried no rooms
``created``     turned into a reservation; ``reservation_id`` is set
"""
from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

SQL = """
SELECT to_char(e.updated_at AT TIME ZONE 'UTC', 'HH24:MI:SS') AS at,
       coalesce(p.code, '--') AS property,
       coalesce(e.ota_name, '--') AS ota,
       coalesce(e.ota_reservation_code, e.booking_id, e.revision_id) AS ref,
       e.outcome,
       coalesce(r.number, '') AS reservation,
       coalesce(left(e.detail, 90), '') AS detail
FROM distribution.channel_booking_events e
LEFT JOIN iam.properties p ON p.id = e.property_id
LEFT JOIN booking.reservations r ON r.id = e.reservation_id
ORDER BY e.updated_at DESC
LIMIT 12
"""


def query() -> str:
    r = subprocess.run(
        ["docker", "compose", "exec", "-T", "postgres",
         "psql", "-U", "pms", "-d", "chirala_pms", "-P", "pager=off", "-c", SQL],
        cwd=ROOT, capture_output=True, text=True)
    return r.stdout if r.returncode == 0 else r.stderr


def main() -> int:
    print("Watching for bookings from the channel manager. Ctrl+C to stop.\n")
    last = ""
    try:
        while True:
            now = query()
            if now != last:
                # Reprinted whole rather than appended: an event changes
                # outcome as it is processed (claimed -> created), and a log
                # that only appends would show both and settle nothing.
                print("\033[2J\033[H", end="")
                print("Bookings from the channel manager "
                      f"(refreshed {time.strftime('%H:%M:%S')})\n")
                print(now)
                last = now
            time.sleep(3)
    except KeyboardInterrupt:
        print("\nStopped.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
