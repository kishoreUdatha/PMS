"""Currency, tax rounding and money formatting in the finance ledger.

Each test reproduces a defect found in review and pins the fix. Requires
PostgreSQL with finance migrations at head; skipped without
``FINANCE_DATABASE_URL``.
"""

from __future__ import annotations

import os
import uuid
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import text

DB_URL = os.getenv("FINANCE_DATABASE_URL")
OWNER_URL = os.getenv("FINANCE_MIGRATION_DATABASE_URL") or DB_URL
pytestmark = pytest.mark.skipif(
    not DB_URL, reason="FINANCE_DATABASE_URL not set; integration test skipped"
)


@pytest.fixture(scope="module")
def engine():
    from chirala_common.db import make_engine

    # Ledger arithmetic, not tenancy: run as the owner, as test_finance does.
    return make_engine(OWNER_URL)


@pytest.fixture()
def scratch(engine):
    org, prop = uuid.uuid4(), uuid.uuid4()
    with engine.begin() as c:
        c.execute(text("INSERT INTO iam.organizations (id, name) "
                       "VALUES (:o, :n)"), {"o": org, "n": f"t-{org.hex[:8]}"})
        c.execute(text(
            "INSERT INTO iam.properties (id, organization_id, code, name, "
            "timezone, currency) VALUES (:p, :o, :code, 'T', 'Asia/Kolkata', "
            "'USD')"),
            {"p": prop, "o": org,
             "code": f"{uuid.uuid4().int % 1_000_000:06d}"})
    try:
        yield org, prop
    finally:
        with engine.begin() as c:
            c.execute(text(
                "DELETE FROM finance.folio_entry_taxes WHERE folio_entry_id IN "
                "(SELECT id FROM finance.folio_entries WHERE property_id = :p)"),
                {"p": prop})
            for t in ("finance.refunds", "finance.payment_allocations",
                      "finance.folio_entries", "finance.payments",
                      "finance.folios"):
                c.execute(text(f"DELETE FROM {t} WHERE property_id = :p"),
                          {"p": prop})
            c.execute(text("DELETE FROM iam.properties WHERE id = :p"),
                      {"p": prop})
            c.execute(text("DELETE FROM iam.organizations WHERE id = :o"),
                      {"o": org})


def _folio(engine, org, prop, currency):
    fid = uuid.uuid4()
    with engine.begin() as c:
        c.execute(text(
            "INSERT INTO finance.folios (id, organization_id, property_id, "
            "type, currency, status) VALUES (:id, :o, :p, 'guest', :c, 'open')"),
            {"id": fid, "o": org, "p": prop, "c": currency})
    return fid


def test_a_charge_takes_its_folios_currency(engine, scratch):
    """The night audit never passed a currency, and the ledger defaulted to INR.

    So every room night on a dollar folio was written as rupees. Now an
    unstated currency is the folio's, and a contradicting one is refused.
    """
    from chirala_common.db import make_session_factory
    from finance_service.ledger import (
        Allocation, LedgerError, post_charge, post_payment,
    )

    org, prop = scratch
    fid = _folio(engine, org, prop, "USD")
    s = make_session_factory(engine)()
    try:
        res = post_charge(
            s, organization_id=org, property_id=prop, folio_id=fid,
            amount=Decimal("100.00"), business_date=date.today(),
            source_type="room_night", tax_category="rooms",
            source_line_key=f"room_night:{uuid.uuid4()}")
        cur = s.execute(text("SELECT currency FROM finance.folio_entries "
                             "WHERE id = :e"), {"e": res.entry_id}).scalar_one()
        assert cur == "USD"

        with pytest.raises(LedgerError, match="kept in USD"):
            post_charge(
                s, organization_id=org, property_id=prop, folio_id=fid,
                amount=Decimal("5.00"), business_date=date.today(),
                source_type="misc", source_line_key="misc:1", currency="INR")

        pay = post_payment(
            s, organization_id=org, property_id=prop, method="card",
            business_date=date.today(),
            allocations=[Allocation(folio_id=fid, amount=Decimal("50.00"))])
        currencies = s.execute(text(
            "SELECT DISTINCT currency FROM finance.payments WHERE id = :p "
            "UNION SELECT DISTINCT currency FROM finance.folio_entries "
            "WHERE source_id = :ps"),
            {"p": pay.payment_id, "ps": str(pay.payment_id)}).scalars().all()
        assert currencies == ["USD"]
        s.commit()
    finally:
        s.close()


# --------------------------------------------------------------------------
# The payment webhook's call to booking-core
# --------------------------------------------------------------------------
def test_confirming_after_payment_is_async_and_tells_refusal_from_failure(
        monkeypatch):
    """The webhook called booking-core with a blocking httpx.post.

    Inside an async handler that stalled the event loop for up to fifteen
    seconds. And every failure read "paid_unconfirmed", whether booking-core
    was unreachable (retry later) or had refused because the hold expired
    and the room was resold (refund the guest).
    """
    import asyncio
    import inspect

    import httpx

    from finance_service import webhook_routes
    from finance_service.settings import settings

    assert inspect.iscoroutinefunction(webhook_routes.confirm_booking)

    answers = iter([
        httpx.Response(200, json={"reservation_id": "x"}),
        httpx.Response(409, json={"detail": "The hold expired and the room "
                                            "was sold. Refund it."}),
        httpx.Response(503, text="unavailable"),
    ])
    transport = httpx.MockTransport(lambda request: next(answers))
    real = httpx.AsyncClient
    monkeypatch.setattr(webhook_routes.httpx, "AsyncClient",
                        lambda **kw: real(transport=transport, **kw))
    monkeypatch.setattr(settings, "service_token", "t0ken")

    async def run():
        return [await webhook_routes.confirm_booking(uuid.uuid4(),
                                                     uuid.uuid4())
                for _ in range(3)]

    ok, refused, down = asyncio.run(run())
    assert ok == (True, "", False)
    assert refused[0] is False and refused[2] is True
    assert "Refund" in refused[1]
    assert down[0] is False and down[2] is False
