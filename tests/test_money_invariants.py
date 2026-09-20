"""The invariants every money screen has to keep, checked against live data.

Written after a day in which fifteen money-affecting defects were found by
reading screens against figures somebody already knew, in code that was marked
Done. Not one of them would have been caught by the suite that existed. Each
test below is one of those defects, turned into a question the stack has to
keep answering.

They run against the RUNNING services rather than a fixture, because that is
where the defects lived: every one was a disagreement between two things that
were individually reasonable -- a report that tied against itself, a summary
whose labels described different numbers than its arithmetic, a KPI compared
against a differently-derived KPI. A fixture reproduces the code's own idea of
the world, which is exactly the idea that was wrong.

Skipped when the stack is not up, and that is a real weakness: a skipped test
protects nothing. `make test-money` brings the stack up first. The check that
CANNOT skip is tests/test_tenant_guards.py, which parses source.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from datetime import date, timedelta
from decimal import Decimal

import pytest

BOOKING = os.getenv("BOOKING_BASE", "http://localhost:8002")
FINANCE = os.getenv("FINANCE_BASE", "http://localhost:8003")
TOKEN = os.getenv("SERVICE_TOKEN", "")
ORG = os.getenv("TEST_ORG", "bb0ad610-b7a7-423a-a9e4-db6dd576306c")
PROP = os.getenv("TEST_PROPERTY", "b53402ee-2c63-475d-a5a4-7b3b3f03a75c")
OTHER_ORG = os.getenv("OTHER_ORG", "315391c2-043f-4713-8493-34dc653bd99a")
OTHER_PROP = os.getenv("OTHER_PROPERTY", "62e108c4-daf8-441e-b524-6fcea02b2ea1")


def _get(base: str, path: str, org: str = ORG):
    req = urllib.request.Request(
        base + path, headers={"X-Service-Token": TOKEN, "X-Service-Org": org})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.load(r)


def _status(base: str, path: str, org: str = ORG) -> int:
    try:
        _get(base, path, org)
        return 200
    except urllib.error.HTTPError as e:
        return e.code


def _up() -> bool:
    if not TOKEN:
        return False
    try:
        _get(BOOKING, f"/dashboard?property_id={PROP}")
        return True
    except Exception:  # noqa: BLE001
        return False


pytestmark = pytest.mark.skipif(
    not _up(),
    reason="services not reachable; run `make test-money` or set SERVICE_TOKEN",
)


def D(x) -> Decimal:
    return Decimal(str(x if x not in (None, "") else 0))


# --------------------------------------------------------------- dashboard --

@pytest.mark.parametrize("days_back", [1, 2, 3, 4])
def test_yesterdays_occupancy_is_yesterdays_occupancy(days_back):
    """The same date must not answer differently depending on who asks.

    Today's count tested status='checked_in'; the previous-day count tested
    status IN ('checked_in','checked_out'). The same date read 0% as today and
    75% as yesterday, and the dashboard showed 0% under a -75% delta computed
    on the other definition. Every past date read 0%, because once a house
    empties nothing is 'checked_in' any more.
    """
    day = date(2026, 9, 14) - timedelta(days=days_back)
    today = _get(BOOKING, f"/dashboard?property_id={PROP}&business_date={day}")
    tomorrow = _get(
        BOOKING,
        f"/dashboard?property_id={PROP}&business_date={day + timedelta(days=1)}")
    assert today["occupancy_pct"] == tomorrow["prev_occupancy_pct"], (
        f"{day} reports {today['occupancy_pct']}% as today but "
        f"{tomorrow['prev_occupancy_pct']}% as yesterday — two definitions")


def test_departures_count_guests_who_left_not_bookings_that_ended():
    """A guest who leaves early has still left.

    Departures matched departure_date = business date, so six guests who left
    on 13 Sep against booked departures of 15 Sep, 16 Sep and 14 Oct showed as
    zero departures on a day seven rooms emptied.
    """
    d = _get(BOOKING, f"/dashboard?property_id={PROP}&business_date=2026-09-13")
    assert d["departures"] > 0, (
        "13 Sep 2026 had seven recorded checkouts and reports "
        f"{d['departures']} departures")


# ------------------------------------------------------------------ ledger --

def test_ledger_scopes_partition_every_entry():
    """Hotel Balance is the plain sum of the ledgers, so they must partition.

    Package is a property of the reservation, not the folio, so a guest folio
    on a package satisfied the guest scope AND the package scope and was
    counted once in each. A 1,000 package charge produced a 2,000 hotel total,
    and the tie check could not see it because it sums the same categories.
    """
    r = _get(FINANCE, f"/reports/ledger?property_id={PROP}")
    parts = sum(D(x["debit"]) for x in r["ledgers"])
    assert parts == D(r["hotel"]["debit"]), (
        f"ledger debits sum to {parts} but Hotel Balance says "
        f"{r['hotel']['debit']} — a scope is counted twice")
    parts_c = sum(D(x["credit"]) for x in r["ledgers"])
    assert parts_c == D(r["hotel"]["credit"])


def test_ledger_default_window_reaches_the_open_business_day():
    """A ledger that cannot show today's postings is not a ledger.

    The window ended at date.today() in UTC -- after 18:30 already tomorrow in
    Chirala -- and once the business date ran ahead of the calendar it stopped
    a day short of the open day. A hotel balance of -59,224 was shown for money
    that had been refunded an hour earlier.
    """
    r = _get(FINANCE, f"/reports/ledger?property_id={PROP}")
    days = _get(FINANCE, f"/night-audit/preview?property_id={PROP}")
    open_day = days.get("business_date")
    if open_day:
        assert str(r["date_to"]) >= str(open_day), (
            f"default window ends {r['date_to']} but the open business day is "
            f"{open_day} — entries posted today would be invisible")


def test_filtered_ledger_describes_one_population():
    """Opening and movements must be about the same guests.

    Opening carried no filters while movements carried all of them, so
    filtering to one reservation showed its movements on top of the whole
    property's opening balance. Figures tie stayed green, because the check
    compares four numbers drawn from the same mismatched pair.
    """
    full = _get(FINANCE, f"/reports/ledger?property_id={PROP}")
    folio_no = next(
        (row["folio_no"] for row in full["journal"] if row.get("folio_no")), None)
    if folio_no is None:
        pytest.skip("no journal rows to filter on")
    one = _get(FINANCE, f"/reports/ledger?property_id={PROP}&folio_no={folio_no}")
    for line in one["ledgers"]:
        assert D(line["closing"]) == D(line["opening"]) + D(line["debit"]) - D(line["credit"])
    assert abs(D(one["hotel"]["closing"])) <= abs(D(full["hotel"]["closing"])) + D(1), (
        "one folio's closing exceeds the whole property's — the opening "
        "balance is describing a different population than the movements")


def test_journal_balance_belongs_to_its_own_folio():
    """A running total across folios lands on a number that is nobody's.

    One accumulator walked the whole list, so a credit on one guest's folio and
    a credit on the next added together: balu paid 2,000 and his row read
    -6,000, which was his payment plus the two guests above him.
    """
    r = _get(FINANCE, f"/reports/ledger?property_id={PROP}")
    seen: dict[str, Decimal] = {}
    for row in r["journal"]:
        fid = row["folio_id"]
        running = D(row["running"])
        delta = D(row["debit"]) - D(row["credit"])
        if fid in seen:
            assert running == seen[fid] + delta, (
                f"folio {row['folio_no']}: balance moved "
                f"{seen[fid]} -> {running} on a {delta} entry")
        seen[fid] = running


# ------------------------------------------------------------------ folios --

def test_folio_summary_does_not_count_refunds_as_charges():
    """A refund is money going back, not a charge going on.

    Splitting by entry_type alone made a refund raise the bill: a folio with
    9,000 collected and all 9,000 returned read Subtotal 9,000, Grand Total
    9,000, Advance Paid 9,000 -- a fully refunded account shown as fully paid.
    The balance was right throughout, which is why it survived.
    """
    r = _get(FINANCE, f"/reports/ledger?property_id={PROP}")
    refunded = {(row["folio_id"], row["folio_no"]) for row in r["journal"]
                if row["source_type"] in ("refund", "deposit_refund")}
    if not refunded:
        pytest.skip("no refunds on this property")
    for fid, folio_no in list(refunded)[:5]:
        s = _get(FINANCE, f"/folios/{fid}/summary")
        assert D(s["balance_due"]) == D(s["grand_total"]) - D(s["advance_paid"])
        # The folio's own entries over every date it has one, not the default
        # 30-day window used above to find the refunds.
        #
        # A folio summary is the whole bill; the hotel ledger is a window onto
        # business dates. Comparing one against the other compares two
        # different populations, and the difference is not hypothetical: a
        # charge is stamped with the business date it belongs to, which for a
        # stay booked months ahead is the stay's date, not the date somebody
        # typed it. A folio holding one spa charge dated at the December stay
        # and refunds taken in September read "grand total 100, charges 0" --
        # the test failing not because a refund had been counted as a charge
        # but because the charge was outside the window it was looking in.
        j = _get(FINANCE, f"/reports/ledger?property_id={PROP}"
                          f"&folio_no={folio_no}"
                          f"&date_from=1900-01-01&date_to=2999-12-31&limit=5000")
        # A truncated journal undercounts the charges and would fail this for
        # the wrong reason. Scoped to one folio it should never happen; say so
        # rather than assert on half a bill.
        if j.get("journal_truncated"):
            pytest.skip(f"folio {folio_no}: journal truncated at the limit")
        charged = sum(
            D(row["debit"]) for row in j["journal"]
            if row["folio_id"] == fid
            and row["source_type"] not in ("refund", "deposit_refund"))
        assert D(s["grand_total"]) == charged, (
            f"folio {fid}: grand total {s['grand_total']} but charges excluding "
            f"refunds are {charged} — refunds are being counted as charges")


def test_adjustment_screen_does_not_count_refunds_as_charges():
    """The same rule as the test above, on the screen that adjusts a charge.

    That test guards /folios/{id}/summary. This one guards the adjustment
    context, which has its OWN totals query and its own idea of which entries
    are charges -- so it went on counting refunds as charges long after the
    summary stopped. A folio whose guest was charged 100 and refunded 23,000
    reported "Total charges 23,100", and offered each of those refunds for
    adjustment on a screen headed Posted Charges.
    """
    r = _get(FINANCE, f"/reports/ledger?property_id={PROP}")
    refunded = {row["folio_id"] for row in r["journal"]
                if row["source_type"] in ("refund", "deposit_refund")}
    if not refunded:
        pytest.skip("no refunds on this property")
    for fid in list(refunded)[:5]:
        c = _get(FINANCE, f"/folios/{fid}/adjustment-context?property_id={PROP}")
        # Nothing offered for adjustment may be a refund.
        bad = [x for x in c["charges"]
               if x["source_type"] in ("refund", "deposit_refund")]
        assert not bad, (
            f"folio {fid}: {len(bad)} refund(s) offered as adjustable charges")
        # And the balance still has to be the folio's real one, which includes
        # those refunds as the debits they are. Taking them out of "charges"
        # broke this once, because the balance was derived from that figure.
        f = c["folio"]
        assert D(f["balance"]) == (D(f["total_charges"])
                                   - D(f["total_payments"])
                                   - D(f["total_adjustments"])
                                   + sum(D(row["debit"]) for row in r["journal"]
                                         if row["folio_id"] == fid
                                         and row["source_type"]
                                         in ("refund", "deposit_refund"))), (
            f"folio {fid}: balance {f['balance']} does not reconcile once "
            f"refunds are excluded from charges")


def test_reservation_financials_agree_with_the_folio():
    """The screen and the ledger cannot disagree about what a guest paid.

    `paid` summed credits and ignored refunds, so a guest refunded 4,000 of
    8,000 still showed 8,000 paid and a balance due that no folio contained.
    """
    lst = _get(BOOKING, f"/reservations?property_id={PROP}")
    rows = lst if isinstance(lst, list) else next(
        (v for v in lst.values() if isinstance(v, list)), [])
    checked = 0
    for row in rows[:12]:
        rid = row.get("id") or row.get("reservation_id")
        if not rid:
            continue
        full = _get(BOOKING, f"/reservations/{rid}/full")
        f = full["financials"]
        assert D(f["balance_due"]) == D(f["total_charges"]) - D(f["total_paid"]), (
            f"{full.get('number')}: balance is not total less paid")
        assert D(f["total_paid"]) >= 0, f"{full.get('number')}: negative paid"
        checked += 1
    assert checked, "no reservations examined"


# ------------------------------------------------------------------ tenancy --

@pytest.mark.parametrize("path", [
    "/guests/directory?organization_id=" + OTHER_ORG,
    "/reservations?property_id=" + OTHER_PROP,
    "/dashboard?property_id=" + OTHER_PROP,
    "/room-types?property_id=" + OTHER_PROP,
])
def test_one_tenant_cannot_read_another(path):
    """Asked with our credentials for their data, the answer is no.

    Sixty-two handlers accepted a tenant and never checked it, including six
    that WROTE -- one tenant could invite users into another tenant's property.
    """
    assert _status(BOOKING, path) in (403, 404), (
        f"{path} answered a cross-tenant request")


def test_finance_reports_refuse_another_tenants_property():
    assert _status(FINANCE, f"/reports/ledger?property_id={OTHER_PROP}") in (403, 404)
    assert _status(FINANCE, f"/cashiering/summary?property_id={OTHER_PROP}") in (403, 404)
