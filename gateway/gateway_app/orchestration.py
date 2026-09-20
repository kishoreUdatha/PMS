"""BFF orchestration: coordinate booking-core and finance (Option A link).

These flows call the services over HTTP and compose their results for the
frontend. Each downstream call is transactional within its own service; the
orchestration is synchronous and idempotent where it matters (folio creation is
keyed by reservation_id; room-night charges are keyed per night in finance).

The eventual async version publishes/consumes outbox events over NATS; this
direct flow is the first, testable step and keeps the loop working end-to-end.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

import httpx


class OrchestrationError(Exception):
    def __init__(self, status_code: int, detail: str) -> None:
        self.status_code = status_code
        self.detail = detail
        super().__init__(detail)


def _auth_headers(client: httpx.AsyncClient, org: str | None) -> dict[str, str]:
    """Credentials for one downstream call.

    A flow either acts for a signed-in person or for the platform itself, and
    it must not claim both. When the caller's token was forwarded, that is the
    identity the downstream service should apply its permission and tenancy
    checks to; adding a service credential on top overrides the person
    entirely -- ``get_caller`` prefers the service branch -- and, with no
    organisation named alongside it, produces a caller belonging to no tenant.
    Every tenancy check then refuses, which is how the checkout folio view
    came back 404 for its own reservation.

    The service credential lives on the client, put there by ``_flow_client``
    only when the *caller* proved it holds it. That distinction is the whole
    security of this path: the gateway's token is not a thing an anonymous
    caller gets the use of by naming a tenant. All that is added here is the
    organisation for this particular call.
    """
    if client.headers.get("authorization"):
        return {}
    if not client.headers.get("x-service-token"):
        return {}
    return {"X-Service-Org": str(org)} if org else {}


async def _get(client: httpx.AsyncClient, base: str, path: str, **params) -> dict:
    org = params.pop("_org", None)
    resp = await client.get(f"{base}{path}", params=params or None,
                            headers=_auth_headers(client, org))
    if resp.status_code >= 400:
        raise OrchestrationError(resp.status_code, resp.text)
    return resp.json()


async def _post(client: httpx.AsyncClient, base: str, path: str, json: dict,
                *, _org: str | None = None) -> dict:
    # Stated, not sniffed. This used to read the organisation out of the body
    # being posted, which stopped working the moment the services dropped
    # organization_id from their request models -- the field is the caller's
    # own organisation now and no longer travels in bodies at all.
    resp = await client.post(f"{base}{path}", json=json,
                             headers=_auth_headers(client, _org))
    if resp.status_code >= 400:
        raise OrchestrationError(resp.status_code, resp.text)
    return resp.json()


async def ensure_folio_for_reservation(
    client: httpx.AsyncClient,
    *,
    finance_url: str,
    organization_id: str,
    property_id: str,
    reservation_id: str,
    currency: str = "INR",
) -> dict:
    """Return the reservation's folio, creating one if absent (Option A).

    Idempotent: if a folio already exists for the reservation we reuse it,
    so repeated check-ins do not create duplicate folios.
    """
    existing = await _get(
        client, finance_url, "/folios", reservation_id=reservation_id,
        _org=organization_id,
    )
    if existing:
        return existing[0]
    return await _post(
        client,
        finance_url,
        "/folios",
        {
            "property_id": property_id,
            "reservation_id": reservation_id,
            "type": "guest",
            "currency": currency,
        },
        _org=organization_id,
    )


def _point_delta(current: float, previous: float) -> float | None:
    """Absolute movement, or None when there is nothing to compare against."""
    if not previous and not current:
        return None
    return round(current - previous, 2)


def _pct_delta(current: float, previous: float) -> float | None:
    """Percentage change, or None when yesterday was zero.

    A jump from nothing to something is not "+100%" or "+inf" -- there is no
    meaningful rate of change off a zero base, so the card shows no indicator.
    """
    if not previous:
        return None
    return round(((current - previous) / previous) * 100, 1)


async def get_dashboard(
    client: httpx.AsyncClient,
    *,
    booking_url: str,
    finance_url: str,
    property_id: str,
    business_date: str | None = None,
    organization_id: str | None = None,
) -> dict:
    """Aggregate the operations dashboard from booking-core + finance.

    Merges operational metrics (occupancy, arrivals, room status, housekeeping,
    7-day occupancy) with finance revenue (today + 7-day series) into a single
    payload for the frontend. Degrades gracefully if finance is unavailable.
    """
    params = {"property_id": property_id}
    if business_date:
        params["business_date"] = business_date

    ops = await _get(client, booking_url, "/dashboard",
                     _org=organization_id, **params)

    revenue: dict = {"today_revenue": 0.0, "series": []}
    try:
        revenue = await _get(client, finance_url, "/dashboard/revenue",
                             _org=organization_id, **params)
    except OrchestrationError:
        # Finance optional for the dashboard; show ops metrics regardless.
        pass

    # Combine occupancy + revenue into one 7-day chart series keyed by date.
    rev_by_date = {r["date"]: r["amount"] for r in revenue.get("series", [])}
    chart = [
        {
            "date": pt["date"],
            "occupancy": pt["occupancy"],
            "revenue": rev_by_date.get(pt["date"], 0.0),
        }
        for pt in ops.get("occupancy_series", [])
    ]

    # Yesterday's revenue is the second-to-last point of the 7-day series,
    # which already ends on the business date -- finance need not compute it
    # twice.
    series = revenue.get("series", [])
    prev_revenue = float(series[-2]["amount"]) if len(series) >= 2 else 0.0
    today_revenue = revenue.get("today_revenue", 0.0)

    return {
        "kpis": {
            "occupancy_pct": ops["occupancy_pct"],
            "arrivals": ops["arrivals"],
            "departures": ops["departures"],
            "available_rooms": ops["available_rooms"],
            "today_revenue": today_revenue,
        },
        # Movement against yesterday, so each KPI reads as a trend and not just
        # a number. Occupancy is in percentage points (it is already a
        # percentage); revenue is a percentage change; arrivals and departures
        # are plain counts. `None` means there is nothing to compare against --
        # no prior activity at all -- and the card then shows no indicator
        # rather than a confident "+0".
        "deltas": {
            "occupancy_pct": _point_delta(
                ops["occupancy_pct"], ops.get("prev_occupancy_pct", 0)
            ),
            "arrivals": _point_delta(ops["arrivals"], ops.get("prev_arrivals", 0)),
            "departures": _point_delta(
                ops["departures"], ops.get("prev_departures", 0)
            ),
            "today_revenue": _pct_delta(today_revenue, prev_revenue),
        },
        "room_status": ops["room_status"],
        "total_rooms": ops["total_rooms"],
        "housekeeping": ops["housekeeping"],
        "todays_arrivals": ops["todays_arrivals"],
        "chart": chart,
    }


async def settle_reservation(
    client: httpx.AsyncClient,
    *,
    booking_url: str,
    finance_url: str,
    reservation_id: str,
    room_charge: str,
    business_date: str,
    method: str | None = None,
    advance_amount: str | None = None,
    organization_id: str | None = None,
) -> dict:
    """Create the folio and, if one is offered, take the advance.

    **It does not charge for the stay.** Room revenue accrues nightly: each
    night the guest is in the house, that night's rate is posted by that
    night's audit. Charging the whole stay here as well would bill every guest
    twice, and would put the revenue on the day the booking was made rather
    than the nights it was earned -- so an early departure would keep money it
    never earned, and a mid-stay rate change could not be applied at all.

    An advance is a different thing and still belongs here: it is money taken
    against a stay that has not happened, and sits on the folio as a credit
    until the nights accrue against it.

    - Ensures a folio exists for the reservation (idempotent).
    - If an advance is provided, captures a payment allocated to the folio.
    Returns the folio id, amounts, and the resulting balance.
    """
    # The property comes from the reservation; the organisation has to be
    # supplied, because this lookup is itself tenancy-guarded. Passing it is
    # only required on the service path -- when a user's token was forwarded,
    # the service resolves the tenant from the person instead.
    unit_scope = await _get(
        client, booking_url, f"/reservations/{reservation_id}/scope",
        _org=organization_id,
    )
    org = unit_scope["organization_id"]
    prop = unit_scope["property_id"]

    folio = await ensure_folio_for_reservation(
        client,
        finance_url=finance_url,
        organization_id=org,
        property_id=prop,
        reservation_id=reservation_id,
    )

    # No room charge here -- see the docstring. The nights post themselves.

    advance_paid = "0"
    if method and advance_amount and float(advance_amount) > 0:
        await _post(
            client,
            finance_url,
            "/payments",
            {
                "property_id": prop,
                "method": method,
                "business_date": business_date,
                "allocations": [{"folio_id": folio["id"], "amount": advance_amount}],
            },
            _org=org,
        )
        advance_paid = advance_amount

    balance = await _get(client, finance_url, f"/folios/{folio['id']}/balance",
                         _org=org)

    return {
        "reservation_id": reservation_id,
        "folio_id": folio["id"],
        "room_charge": room_charge,
        "advance_paid": advance_paid,
        "balance": balance["balance"],
    }


async def get_folio_view(
    client: httpx.AsyncClient,
    *,
    booking_url: str,
    finance_url: str,
    reservation_id: str,
    organization_id: str | None = None,
) -> dict:
    """Aggregate a reservation's folio view for the Checkout Folio screen.

    Resolves the reservation scope + number/guest, ensures a folio exists, and
    returns the folio id, entries and summary in one call.
    """
    scope = await _get(client, booking_url,
                       f"/reservations/{reservation_id}/scope",
                       _org=organization_id)
    # From here the scope answer carries the tenant, so the rest of the fan-out
    # does not depend on the caller having named it.
    org = scope["organization_id"]
    detail = await _get(
        client, booking_url, f"/reservations/{reservation_id}/detail", _org=org
    )
    folio = await ensure_folio_for_reservation(
        client,
        finance_url=finance_url,
        organization_id=scope["organization_id"],
        property_id=scope["property_id"],
        reservation_id=reservation_id,
    )
    entries = await _get(client, finance_url, f"/folios/{folio['id']}/entries",
                         _org=org)
    summary = await _get(client, finance_url, f"/folios/{folio['id']}/summary",
                         _org=org)
    return {
        "reservation_id": reservation_id,
        "number": scope.get("number"),
        "status": scope.get("status"),
        "guest_name": detail.get("guest_name"),
        "organization_id": scope["organization_id"],
        "property_id": scope["property_id"],
        "folio_id": folio["id"],
        "entries": entries,
        "summary": summary,
        "units": detail.get("units", []),
    }


async def checkout_with_billing(
    client: httpx.AsyncClient,
    *,
    booking_url: str,
    finance_url: str,
    reservation_unit_id: str,
    nightly_rate: Decimal,
    business_date: str | None = None,
    organization_id: str | None = None,
) -> dict:
    """Orchestrate checkout: post room charges to the folio, then check out.

    Steps:
      1. Fetch reservation-unit detail (nights, reservation_id, org/property).
      2. Ensure a folio exists for the reservation.
      3. Post the room charge (nights * nightly_rate) idempotently to the folio.
      4. Perform the booking-core checkout (releases future nights, room dirty).
      5. Return the folio id and post-checkout balance.
    """
    unit = await _get(
        client, booking_url, f"/reservation-units/{reservation_unit_id}",
        _org=organization_id,
    )
    org = unit["organization_id"]

    folio = await ensure_folio_for_reservation(
        client,
        finance_url=finance_url,
        organization_id=unit["organization_id"],
        property_id=unit["property_id"],
        reservation_id=unit["reservation_id"],
    )

    nights = int(unit["nights"])
    room_charge = (Decimal(str(nightly_rate)) * nights).quantize(Decimal("0.0001"))
    bd = business_date or unit["arrival_date"]

    # No room charge here either. By the time a guest checks out, every night
    # they slept has already been posted by that night's audit; posting the
    # whole stay again would double every folio. A guest who arrives and leaves
    # the same day stayed no nights and owes nothing for the room, which is
    # what an empty accrual correctly says.
    #
    # This does mean the folio is short if an audit was missed. That shows up
    # as a business day still open, which is the thing the audit screen leads
    # with -- a visible unclosed day, rather than a silently wrong bill.

    # Perform the booking checkout.
    checkout = await _post(
        client,
        booking_url,
        f"/reservation-units/{reservation_unit_id}/check-out",
        {"business_date": bd},
        _org=org,
    )

    balance = await _get(client, finance_url, f"/folios/{folio['id']}/balance",
                         _org=org)

    return {
        "reservation_unit_id": reservation_unit_id,
        "folio_id": folio["id"],
        "room_charge": str(room_charge),
        "nights": nights,
        "stay_id": checkout.get("stay_id"),
        "nights_released": checkout.get("nights_released"),
        "balance": balance["balance"],
    }
