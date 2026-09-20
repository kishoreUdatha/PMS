"""Full-loop integration test across booking-core and finance via orchestration.

Exercises: seed inventory -> hold -> confirm -> assign -> check-in ->
orchestrated checkout (folio ensured, room charge posted, booking checkout,
balance returned). Also verifies the checkout flow is idempotent on the room
charge (re-billing the same unit posts once).

Both services share one PostgreSQL database here (schema-per-service), matching
the initial deployment model. Requires BOTH BOOKING_DATABASE_URL and
FINANCE_DATABASE_URL to point at the same migrated database. Skipped otherwise.

The orchestration normally talks HTTP; this test mounts the booking and finance
FastAPI apps in-process and routes httpx calls to them by base URL, so the real
orchestration code path is exercised without network servers.
"""

from __future__ import annotations

import os
import uuid
from datetime import date, timedelta
from decimal import Decimal

import httpx
import pytest
from sqlalchemy import text

BOOKING_URL_ENV = os.getenv("BOOKING_DATABASE_URL")
FINANCE_URL_ENV = os.getenv("FINANCE_DATABASE_URL")

pytestmark = pytest.mark.skipif(
    not (BOOKING_URL_ENV and FINANCE_URL_ENV),
    reason="BOOKING_DATABASE_URL and FINANCE_DATABASE_URL required",
)

BOOKING_BASE = "http://booking.local"
FINANCE_BASE = "http://finance.local"


class _MultiAppTransport(httpx.AsyncBaseTransport):
    """Route requests to the right in-process ASGI app by URL host."""

    def __init__(self) -> None:
        from booking_core.main import app as booking_app
        from finance_service.main import app as finance_app

        self._booking = httpx.ASGITransport(app=booking_app)
        self._finance = httpx.ASGITransport(app=finance_app)

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        if request.url.host == "booking.local":
            return await self._booking.handle_async_request(request)
        if request.url.host == "finance.local":
            return await self._finance.handle_async_request(request)
        raise RuntimeError(f"Unexpected host {request.url.host}")


@pytest.mark.anyio
async def test_full_loop_checkout_bills_folio():
    from chirala_common.db import make_engine, make_session_factory

    from booking_core.flow import assign_room, check_in, confirm_reservation
    from booking_core.inventory import create_hold
    from gateway_app.orchestration import checkout_with_billing

    engine = make_engine(BOOKING_DATABASE_URL := (
        os.getenv("BOOKING_MIGRATION_DATABASE_URL") or os.environ["BOOKING_DATABASE_URL"]))
    factory = make_session_factory(engine)

    org, prop, rt = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    arrival = date.today() + timedelta(days=120)
    nights = 3
    departure = arrival + timedelta(days=nights)

    # Seed room type, room, inventory.
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO property.room_types
                    (id, organization_id, property_id, code, name,
                     max_adults, max_children, max_occupancy)
                VALUES (:rt, :org, :prop, :code, 'Deluxe', 2, 1, 3)
                """
            ),
            {"rt": rt, "org": org, "prop": prop, "code": f"RT{uuid.uuid4().hex[:4]}"},
        )
        room_id = uuid.uuid4()
        conn.execute(
            text(
                """
                INSERT INTO property.rooms
                    (id, organization_id, property_id, room_type_id, code)
                VALUES (:id, :org, :prop, :rt, :code)
                """
            ),
            {"id": room_id, "org": org, "prop": prop, "rt": rt,
             "code": f"R{uuid.uuid4().hex[:5]}"},
        )
        for i in range(nights):
            conn.execute(
                text(
                    """
                    INSERT INTO booking.room_type_inventory_days
                        (organization_id, property_id, room_type_id, stay_date,
                         physical_capacity, out_of_service, held_units,
                         reserved_units, allotment_units)
                    VALUES (:org, :prop, :rt, :d, 5, 0, 0, 0, 0)
                    """
                ),
                {"org": org, "prop": prop, "rt": rt, "d": arrival + timedelta(days=i)},
            )

    # Booking flow up to check-in (direct service calls).
    s = factory()
    hold = create_hold(
        s, organization_id=org, property_id=prop, room_type_id=rt,
        arrival_date=arrival, departure_date=departure, units=1, adults=2,
        children=0, idempotency_key=f"loop-{uuid.uuid4()}",
        hold_ttl_minutes=15, overbooking_allowance=0,
    )
    s.commit(); s.close()
    unit_id = hold.reservation_unit_id

    s = factory()
    confirm_reservation(s, reservation_id=hold.reservation_id)
    s.commit(); s.close()
    s = factory()
    assign_room(s, reservation_unit_id=unit_id, room_id=room_id)
    s.commit(); s.close()
    s = factory()
    check_in(s, reservation_unit_id=unit_id)
    s.commit(); s.close()

    # Orchestrated checkout via the real orchestration code path.
    transport = _MultiAppTransport()
    async with httpx.AsyncClient(transport=transport) as client:
        result = await checkout_with_billing(
            client,
            booking_url=BOOKING_BASE,
            finance_url=FINANCE_BASE,
            reservation_unit_id=str(unit_id),
            nightly_rate=Decimal("5000.00"),
            business_date=arrival.isoformat(),
        )

    assert result["nights"] == nights
    # 3 nights * 5000 = 15000 owed; no payment yet -> balance 15000.
    assert Decimal(result["balance"]) == Decimal("15000.0000")
    folio_id = result["folio_id"]

    # Idempotent re-bill: run the charge again, balance unchanged.
    async with httpx.AsyncClient(transport=transport) as client:
        # Re-post the same room charge directly (same source_line_key).
        resp = await client.post(
            f"{FINANCE_BASE}/charges",
            json={
                "organization_id": str(org),
                "property_id": str(prop),
                "folio_id": folio_id,
                "amount": "15000.00",
                "business_date": arrival.isoformat(),
                "source_type": "room_stay",
                "source_line_key": f"room_stay:{unit_id}",
            },
        )
        assert resp.status_code == 201
        assert resp.json()["created"] is False  # idempotent no-op
        bal = await client.get(f"{FINANCE_BASE}/folios/{folio_id}/balance")

    assert Decimal(bal.json()["balance"]) == Decimal("15000.0000")


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"
