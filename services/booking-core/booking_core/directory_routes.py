"""Guest Directory (screen 010).

The guest list that existed before this was a searchable index: name, email,
phone. Useful for finding somebody, useless for the question the mockup is
actually asking — *who are our guests, which of them matter, and which are here
right now*.

Everything here is derived from stays and folios rather than stored on the
guest. A guest is "returning" because they have stayed more than once, not
because somebody ticked a box; lifetime value is what they were actually
billed, net of any adjustment. Nothing has to be kept in step by hand, and
nothing can be wrong in a way the ledger disagrees with.

Two things the mockup shows that are not pretended here:

* **VIP and Blacklisted.** ``engagement.guests`` has no such flag and no
  screen sets one. Those two badges have nothing behind them, so the status
  column carries only what the stays can prove — In-house, Returning, New.
* **Preferences.** Sea View, Vegetarian and the rest belong to SCR-067, which
  has neither a table nor a screen. The column is returned empty rather than
  filled with guesses, and the UI says why.

``Source`` is absent for the same reason: a guest has no acquisition source
recorded anywhere.
"""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

from chirala_common.authz import Caller, build_authz, assert_org_matches_caller
from chirala_common.routing import TransactionalRoute
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session

from .database import get_session
from .settings import settings

_get_caller, _require_permission, require_org_permission = build_authz(
    get_session, lambda: settings.environment,
    lambda: settings.service_token,
)

directory_router = APIRouter(tags=["guests"], route_class=TransactionalRoute)

# Sortable columns, named as they come out of the wrapping SELECT. Only these
# four plus status reach the ORDER BY, so nothing arbitrary can be spliced in.
SORTS = {
    "name": "full_name",
    "last_stay": "last_stay",
    "total_stays": "total_stays",
    "lifetime_value": "lifetime_value",
    "status": "CASE WHEN in_house THEN 0 WHEN total_stays > 1 THEN 1 ELSE 2 END",
}
# How far back "last stay" filters reach, in days. None means no lower bound.
STAY_WINDOWS = {"30d": 30, "90d": 90, "365d": 365}


class DirectoryRow(BaseModel):
    id: uuid.UUID
    reference: str | None
    full_name: str
    email: str | None
    phone: str | None
    city: str | None
    country: str | None
    nationality: str | None
    last_stay: date | None
    total_stays: int
    lifetime_value: Decimal
    status: str
    status_label: str
    in_house: bool
    # Empty until SCR-067 exists. Returned so the column has a shape.
    preferences: list[str]


class DirectoryKpis(BaseModel):
    total_guests: int
    in_house: int
    returning: int
    new_guests: int


class DirectoryOut(BaseModel):
    kpis: DirectoryKpis
    rows: list[DirectoryRow]
    total: int
    page: int
    page_size: int
    countries: list[str]
    can_create: bool
    can_export: bool


STATUS_LABELS = {
    "in_house": "In-house", "returning": "Returning", "new": "New",
}

# One guest, with everything the directory shows about their stays and spend.
# Kept as a single CTE so the counts, the rows and the export cannot disagree.
_STATS_SQL = """
    WITH stays AS (
        SELECT r.primary_guest_id AS guest_id,
               count(*) FILTER (
                   WHERE ru.status IN ('checked_in', 'checked_out')
               ) AS total_stays,
               -- Arrival, not departure: an in-house guest's departure is
               -- still a plan, and a "last stay" in the future reads as a bug.
               max(ru.arrival_date) FILTER (
                   WHERE ru.status IN ('checked_in', 'checked_out')
               ) AS last_stay,
               bool_or(ru.status = 'checked_in') AS in_house
        FROM booking.reservations r
        JOIN booking.reservation_units ru ON ru.reservation_id = r.id
        WHERE r.organization_id = :org
          AND r.primary_guest_id IS NOT NULL
        GROUP BY r.primary_guest_id
    ),
    spend AS (
        -- What the guest was finally billed: charges net of adjustments.
        -- Payments and refunds are how they settled it, not what it cost.
        SELECT r.primary_guest_id AS guest_id,
               COALESCE(sum(
                   CASE WHEN e.entry_type = 'debit' THEN e.amount ELSE -e.amount END
               ) FILTER (
                   WHERE COALESCE(e.source_type, '') NOT IN ('payment', 'refund',
                                                             'security_deposit')
               ), 0) AS lifetime_value
        FROM booking.reservations r
        JOIN finance.folios f ON f.reservation_id = r.id
        JOIN finance.folio_entries e ON e.folio_id = f.id
        WHERE r.organization_id = :org
          AND r.primary_guest_id IS NOT NULL
        GROUP BY r.primary_guest_id
    )
    SELECT g.id, g.reference, g.full_name, g.email, g.phone, g.city,
           g.country, g.nationality,
           COALESCE(st.total_stays, 0) AS total_stays,
           st.last_stay,
           COALESCE(st.in_house, false) AS in_house,
           COALESCE(sp.lifetime_value, 0) AS lifetime_value
    FROM engagement.guests g
    LEFT JOIN stays st ON st.guest_id = g.id
    LEFT JOIN spend sp ON sp.guest_id = g.id
    WHERE g.organization_id = :org
"""


@directory_router.get("/guests/directory", response_model=DirectoryOut)
def directory(
    organization_id: uuid.UUID,
    q: str | None = Query(None),
    status: str | None = Query(None),
    country: str | None = Query(None),
    last_stay: str | None = Query(None),
    sort: str = Query("name"),
    direction: str = Query("asc"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
    caller: Caller = Depends(require_org_permission("guests", "view")),
    db: Session = Depends(get_session),
):
    """The directory, with the counts across the top and one page of rows."""
    assert_org_matches_caller(caller, organization_id)
    from chirala_common.authz import _GRANT_SQL

    filters = """
          AND (CAST(:q AS text) IS NULL
               OR g.full_name ILIKE '%' || CAST(:q AS text) || '%'
               OR g.email     ILIKE '%' || CAST(:q AS text) || '%'
               OR g.phone     ILIKE '%' || CAST(:q AS text) || '%'
               OR g.city      ILIKE '%' || CAST(:q AS text) || '%'
               OR g.reference ILIKE '%' || CAST(:q AS text) || '%')
          AND (CAST(:country AS text) IS NULL OR g.country = CAST(:country AS text))
    """
    params: dict = {
        "org": organization_id, "q": q or None, "country": country or None,
    }

    if last_stay in STAY_WINDOWS:
        filters += (" AND st.last_stay >= CURRENT_DATE"
                    " - CAST(:days AS int)")
        params["days"] = STAY_WINDOWS[last_stay]
    elif last_stay == "never":
        filters += " AND st.last_stay IS NULL"

    if status == "in_house":
        filters += " AND COALESCE(st.in_house, false)"
    elif status == "returning":
        filters += " AND COALESCE(st.total_stays, 0) > 1"
    elif status == "new":
        filters += " AND COALESCE(st.total_stays, 0) <= 1"

    base = _STATS_SQL + filters
    order = SORTS.get(sort, "full_name")
    desc = " DESC NULLS LAST" if direction == "desc" else " ASC NULLS LAST"

    rows = db.execute(
        text(f"SELECT * FROM ({base}) d ORDER BY {order}{desc}, "
             f"full_name LIMIT :lim OFFSET :off"),
        {**params, "lim": page_size, "off": (page - 1) * page_size},
    ).mappings().all()
    total = db.execute(
        text(f"SELECT count(*) FROM ({base}) d"), params
    ).scalar_one()

    # The headline counts describe the whole directory, not the filtered page —
    # a filter should not change how many guests the property has.
    counts = db.execute(
        text(
            f"""
            SELECT count(*) AS total_guests,
                   count(*) FILTER (WHERE in_house) AS in_house,
                   count(*) FILTER (WHERE total_stays > 1) AS returning,
                   count(*) FILTER (WHERE total_stays <= 1) AS new_guests
            FROM ({_STATS_SQL}) d
            """
        ),
        {"org": organization_id, "q": None, "country": None},
    ).mappings().first()

    countries = [
        r[0] for r in db.execute(
            text("SELECT DISTINCT country FROM engagement.guests "
                 "WHERE organization_id = :org AND country IS NOT NULL "
                 "AND country <> '' ORDER BY 1"),
            {"org": organization_id},
        )
    ]

    def may(action: str) -> bool:
        return db.execute(
            text(_GRANT_SQL),
            {"uid": caller.user_id, "res": "guests", "act": action, "prop": None},
        ).first() is not None

    out = []
    for r in rows:
        state = ("in_house" if r["in_house"]
                 else "returning" if r["total_stays"] > 1 else "new")
        out.append(DirectoryRow(
            id=r["id"], reference=r["reference"],
            full_name=r["full_name"], email=r["email"],
            phone=r["phone"], city=r["city"], country=r["country"],
            nationality=r["nationality"], last_stay=r["last_stay"],
            total_stays=r["total_stays"], lifetime_value=r["lifetime_value"],
            status=state, status_label=STATUS_LABELS[state],
            in_house=r["in_house"], preferences=[],
        ))
    return DirectoryOut(
        kpis=DirectoryKpis(**counts), rows=out, total=total, page=page,
        page_size=page_size, countries=countries,
        can_create=may("create"), can_export=may("export"),
    )
