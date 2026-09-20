"""Back office reports: the registry, and every report against a real database.

The registry test needs no database and catches the mistake this design is
most prone to -- a metric, filter or total naming a column key that does not
exist, which would otherwise surface as a 500 on one report's screen.

The run test executes every built report for every property in the database
over a wide window, and checks each row is as wide as the columns it is drawn
under. Skipped when FINANCE_DATABASE_URL is not set.
"""

from __future__ import annotations

import os
from datetime import date

import pytest
from sqlalchemy import text


def test_catalog_lists_all_39_design_reports_once():
    from finance_service.backoffice_reports import CATALOG

    numbers = sorted(r.number for r in CATALOG if r.number)
    assert numbers == list(range(1, 40))
    slugs = [r.slug for r in CATALOG]
    assert len(slugs) == len(set(slugs))


def test_every_definition_resolves():
    from finance_service.backoffice_reports import CATALOG, resolve

    for r in CATALOG:
        if r.run is None:
            assert r.href or r.unavailable_reason, r.slug
            continue
        assert r.columns and r.note, r.slug
        resolve(r)  # KeyError names the bad key


def test_indian_rupee_grouping():
    from finance_service.backoffice_reports import _inr

    assert _inr(0) == "₹0.00"
    assert _inr(1234567.5) == "₹12,34,567.50"
    assert _inr(-999) == "-₹999.00"


def test_ageing_buckets_follow_days_past_due():
    from finance_service.backoffice_reports import _bucket

    days = (-5, 0, 1, 30, 31, 60, 61, 90, 91)
    assert [_bucket(d) for d in days] == [
        "not_due", "not_due", "d30", "d30", "d60", "d60", "d90", "d90", "d90p"]


def test_meal_entitlements_by_code_then_name():
    from finance_service.backoffice_reports import _meals

    assert _meals("RO", "Room Only") == ()
    assert _meals("bb", None) == ("breakfast",)
    assert _meals("HB", "Half Board") == ("breakfast", "dinner")
    assert _meals("XYZ", "Full Board Special") == ("breakfast", "lunch", "dinner")


def test_breakfast_follows_the_night_before():
    from finance_service.backoffice_reports import _served

    stay = {"plan_code": "FB", "plan_name": None,
            "arrival_date": date(2026, 9, 14), "departure_date": date(2026, 9, 16)}
    assert not _served(stay, "breakfast", date(2026, 9, 14))
    assert _served(stay, "dinner", date(2026, 9, 14))
    assert _served(stay, "breakfast", date(2026, 9, 16))
    assert not _served(stay, "dinner", date(2026, 9, 16))


@pytest.mark.skipif(not os.getenv("FINANCE_DATABASE_URL"),
                    reason="FINANCE_DATABASE_URL not set; integration test skipped")
def test_every_built_report_runs_for_every_property():
    from chirala_common.db import make_engine
    from sqlalchemy.orm import Session

    from chirala_common.db import bind_tenant_context
    from finance_service.backoffice_reports import CATALOG, execute

    engine = make_engine(os.environ["FINANCE_DATABASE_URL"])
    with Session(engine) as db:
        properties = db.execute(text("SELECT id, organization_id FROM iam.properties")).all()
        for pid, org in properties:
            for r in CATALOG:
                if r.run is None:
                    continue
                # As the services do: the report runs as its property's tenant.
                bind_tenant_context(db, organization_id=org, property_id=pid)
                out = execute(db, r, pid, date(2026, 1, 1), date(2026, 12, 31), {})
                width = len(out.columns)
                assert all(len(row) == width for row in out.rows), r.slug
                assert len(out.links) == len(out.rows), r.slug
        db.rollback()
