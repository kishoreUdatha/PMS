"""What day it is at a property -- by the calendar, and by the ledger.

Two questions that are easy to confuse, and that every service needs:

* **``local_today``** is the calendar date where the hotel is. A resort in
  Kolkata and one in Lisbon do not change date together, and the server may be
  in neither. ``CURRENT_DATE`` and ``date.today()`` answer for the database's
  or the server's timezone -- UTC in production -- which lags India by five and
  a half hours: between midnight and 05:30 IST they still say yesterday. A
  penalty window, a no-show gate or a default check-out date computed that way
  is a day out for a quarter of every night.

* **``trading_day``** is the business date the ledger is open on: the oldest
  day the night audit has not yet closed. A charge belongs to that day, not to
  the calendar's -- after midnight and before the audit runs, the hotel is still
  trading yesterday, and money stamped with today lands on a date the drawer
  was never counted against. Falls back to ``local_today`` for a property that
  has never opened a business day.

Both live here, rather than in finance where they started, because booking-core
posts to the folio and decides penalty windows too, and it had been using
``CURRENT_DATE`` for both.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import text
from sqlalchemy.orm import Session

#: What a property with no usable timezone is assumed to be in. Every
#: property this platform was built for is in India; the column is NOT NULL,
#: so this only matters for a value ZoneInfo cannot read.
DEFAULT_TZ = "Asia/Kolkata"


def property_tz(session: Session, property_id: uuid.UUID) -> ZoneInfo:
    tz_name = session.execute(
        text("SELECT timezone FROM iam.properties WHERE id = :p"),
        {"p": property_id},
    ).scalar() or DEFAULT_TZ
    try:
        return ZoneInfo(tz_name)
    except (ZoneInfoNotFoundError, ValueError):
        return ZoneInfo(DEFAULT_TZ)


def local_now(session: Session, property_id: uuid.UUID) -> datetime:
    """The property's own wall clock."""
    return datetime.now(property_tz(session, property_id))


def local_today(session: Session, property_id: uuid.UUID) -> date:
    """Today where the property is, not where the server is."""
    return local_now(session, property_id).date()


def trading_day(session: Session, property_id: uuid.UUID) -> date:
    """The business date the ledger is open on at this property.

    The OLDEST unclosed day: the night audit closes them oldest first, so a
    property cannot be selling the 18th while the 17th is still open.
    """
    day = session.execute(
        text("SELECT business_date FROM finance.business_days "
             "WHERE property_id = :p AND status = 'open' "
             "ORDER BY business_date ASC LIMIT 1"),
        {"p": property_id},
    ).scalar()
    return day if day is not None else local_today(session, property_id)
