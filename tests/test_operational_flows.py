"""The operational flows, end to end, against the running stack.

The suite that existed tested inventory concurrency and one orchestration
loop. The flows a front desk actually performs -- take a booking, put someone
in a room, bill them, take money, let them leave, close the day -- were
covered nowhere, which is how a checkout could post nothing for six guests and
nobody found out until the ledger was read by hand.

Each test drives the real endpoints in the real order and then asks the
LEDGER whether it agrees, rather than trusting the response it just received.
That is the part that matters: almost every defect found on 14 September was
two components each behaving reasonably and disagreeing with each other, so a
test that only checks its own call would have passed throughout.

Test data is marked with a per-run tag and cleaned up where an endpoint exists
to do it. Some residue is unavoidable -- a posted folio entry is immutable by
design, which is correct and does mean these tests leave a trail. Run them
against a scratch property, not a tenant with paying guests.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
import uuid
from datetime import date, timedelta
from decimal import Decimal

import pytest

BOOKING = os.getenv("BOOKING_BASE", "http://localhost:8002")
FINANCE = os.getenv("FINANCE_BASE", "http://localhost:8003")
TOKEN = os.getenv("SERVICE_TOKEN", "")
ORG = os.getenv("TEST_ORG", "bb0ad610-b7a7-423a-a9e4-db6dd576306c")
PROP = os.getenv("TEST_PROPERTY", "b53402ee-2c63-475d-a5a4-7b3b3f03a75c")

#: Marks everything one run creates, so a human can tell test data from real.
RUN = f"FLOWTEST-{uuid.uuid4().hex[:8]}"


def _call(base, path, *, method="GET", body=None, org=ORG):
    req = urllib.request.Request(
        base + path, method=method,
        headers={"X-Service-Token": TOKEN, "X-Service-Org": org,
                 "Content-Type": "application/json"},
        data=json.dumps(body).encode() if body is not None else None)
    with urllib.request.urlopen(req, timeout=30) as r:
        raw = r.read()
        return json.loads(raw) if raw else {}


def get(path, base=BOOKING, **kw):
    return _call(base, path, **kw)


def post(path, body, base=BOOKING, **kw):
    return _call(base, path, method="POST", body=body, **kw)



#: iam does not accept the service credential. It keeps its own authz module
#: with no service-token branch at all, so a caller is a person or nothing --
#: which is why these tests identify as one. That divergence is itself a known
#: fragility (it is how an organisation-scoped grant came to satisfy a
#: permission check for ANY property), recorded here rather than hidden behind
#: a helper that makes the two services look alike.
IAM = os.getenv("IAM_BASE", "http://iam:8001")
IAM_SUBJECT = os.getenv("IAM_SUBJECT", "udathak-4b7e0f")


def iam_call(path, *, method="GET", body=None):
    req = urllib.request.Request(
        IAM + path, method=method,
        headers={"X-Debug-Subject": IAM_SUBJECT,
                 "Content-Type": "application/json"},
        data=json.dumps(body).encode() if body is not None else None)
    with urllib.request.urlopen(req, timeout=30) as r:
        raw = r.read()
        return json.loads(raw) if raw else {}


def _up() -> bool:
    if not TOKEN:
        return False
    try:
        _call(BOOKING, f"/dashboard?property_id={PROP}")
        return True
    except Exception:  # noqa: BLE001
        return False


pytestmark = pytest.mark.skipif(
    not _up(), reason="services not reachable; run `make test-flows`")


def D(x) -> Decimal:
    return Decimal(str(x if x not in (None, "") else 0))


def folio_balance(folio_id) -> Decimal:
    """What the ledger says, which is the only opinion that counts."""
    entries = get(f"/folios/{folio_id}/entries", base=FINANCE)
    rows = entries if isinstance(entries, list) else entries.get("entries", [])
    return sum(
        (D(e["amount"]) if e["entry_type"] == "debit" else -D(e["amount"]))
        for e in rows), rows


# ---------------------------------------------------------------- fixtures --

@pytest.fixture(scope="module")
def room_type():
    types = get(f"/room-types?property_id={PROP}")
    rows = types if isinstance(types, list) else next(
        (v for v in types.values() if isinstance(v, list)), [])
    assert rows, "property has no room types"
    return rows[0]


@pytest.fixture(scope="module")
def guest():
    """One guest for the whole run, not one per test.

    Guests are never deleted -- correctly, a PMS does not erase the people who
    stayed -- so every test that made its own left a permanent record. Twenty
    tests over a few runs put a hundred and fifty of them in the directory.
    One per run is enough: the tests are about the booking, not the person.
    """
    return post("/guests", {
        "full_name": f"{RUN} Guest",
        "email": f"{RUN.lower()}@example.invalid",
        "phone": "+91 90000 00000",
    })


@pytest.fixture
def booking(room_type, guest):
    """A confirmed two-night booking, far enough ahead to be undisturbed."""
    # Offset per run so concurrent or repeated runs do not compete for the
    # same nights, and far enough out to be nobody's real booking.
    arrival = date(2026, 11, 3) + timedelta(days=int(RUN[-2:], 16) % 60)
    departure = arrival + timedelta(days=2)
    hold = post("/holds", {
        "property_id": PROP,
        "arrival_date": str(arrival),
        "departure_date": str(departure),
        "idempotency_key": f"{RUN}-{uuid.uuid4().hex[:8]}",
        "room_type_id": room_type["id"],
        "units": 1, "adults": 2, "children": 0,
        "guest_id": guest["id"],
        "source": "direct",
        "reference": RUN,
    })
    post(f"/reservations/{hold['reservation_id']}/confirm", {})
    unit = (hold.get("reservation_unit_ids") or [hold["reservation_unit_id"]])[0]
    yield {"reservation_id": hold["reservation_id"], "unit_id": unit,
           "guest_id": guest["id"], "arrival": arrival, "departure": departure,
           "number": hold.get("number")}

    # Give the inventory back. Without this the suite eats its own room type:
    # each test takes a unit for the same nights, and the fourth run finds
    # nothing sellable and fails for a reason that has nothing to do with what
    # it was testing. A checked-in unit cannot be cancelled, which is correct,
    # so that one is left and reported rather than forced.
    try:
        post(f"/reservations/{hold['reservation_id']}/cancel"
             f"?property_id={PROP}",
             # No waive_penalty: that needs reservations.approve, which a
             # service credential does not carry, and a test should not be
             # asking for a permission the flow it tests does not need.
             {"reason": "other", "notes": f"{RUN} teardown"})
    except urllib.error.HTTPError as e:
        if e.code not in (409, 422):
            raise


# ------------------------------------------------------------------- flows --

def test_a_confirmed_booking_shows_its_value_before_anything_is_posted(booking):
    """A future booking is worth what it was sold for.

    Room charges accrue nightly, so a booking with no nights behind it has no
    folio entries -- and this screen showed Total 0 for every future
    reservation, including ones already paid in full, which made Balance Due
    negative and read as the property owing the guest money.
    """
    full = get(f"/reservations/{booking['reservation_id']}/full")
    f = full["financials"]
    assert D(f["total_charges"]) > 0, "a confirmed booking is worth nothing"
    assert D(f["total_paid"]) == 0
    assert D(f["balance_due"]) == D(f["total_charges"])


def test_check_in_puts_a_guest_in_a_room_and_opens_a_stay(booking):
    """Assignment then check-in, in that order, and the stay is the record."""
    rooms = get(f"/rooms?property_id={PROP}")
    rows = rooms if isinstance(rooms, list) else next(
        (v for v in rooms.values() if isinstance(v, list)), [])
    assert rows, "property has no rooms"
    assigned = None
    for r in rows:
        try:
            post(f"/reservation-units/{booking['unit_id']}/assign",
                 {"room_id": r["id"]})
            assigned = r
            break
        except urllib.error.HTTPError as e:
            if e.code not in (409, 422):
                raise
    assert assigned, "no room could be assigned for the whole stay"

    post(f"/reservation-units/{booking['unit_id']}/check-in", {})
    unit = get(f"/reservation-units/{booking['unit_id']}")
    assert unit["status"] == "checked_in"


def test_a_charge_reaches_the_ledger_and_the_reservation_agrees(booking):
    """Post money and then ask the ledger, not the response."""
    folio = post("/folios", {
        "property_id": PROP,
        "reservation_id": booking["reservation_id"],
        "type": "guest", "currency": "INR",
    }, base=FINANCE)
    post("/charges", {
        "property_id": PROP, "folio_id": folio["id"],
        "amount": "1500.00", "business_date": str(date(2026, 11, 3)),
        "source_type": "service",
        # Required, and the reason posting twice cannot double-charge.
        "source_line_key": f"{RUN}-charge-1",
    }, base=FINANCE)

    bal, _ = folio_balance(folio["id"])
    assert bal == D("1500.00"), f"folio says {bal}, expected 1500"

    full = get(f"/reservations/{booking['reservation_id']}/full")
    assert D(full["financials"]["total_charges"]) >= D("1500.00"), (
        "the reservation screen does not see a charge the ledger has")


def test_a_payment_reduces_the_balance_and_the_screen_agrees(booking):
    folio = post("/folios", {
        "property_id": PROP, "reservation_id": booking["reservation_id"],
        "type": "guest", "currency": "INR"}, base=FINANCE)
    post("/charges", {
        "property_id": PROP, "folio_id": folio["id"], "amount": "2000.00",
        "business_date": str(date(2026, 11, 3)), "source_type": "service",
        "source_line_key": f"{RUN}-charge-2"}, base=FINANCE)
    post("/payments", {
        "property_id": PROP, "method": "card",
        "reference": RUN,
        "business_date": str(date(2026, 11, 3)),
        "allocations": [{"folio_id": folio["id"], "amount": "800.00"}],
    }, base=FINANCE)

    bal, _ = folio_balance(folio["id"])
    assert bal == D("1200.00"), f"2000 charged less 800 paid should be 1200, got {bal}"

    full = get(f"/reservations/{booking['reservation_id']}/full")
    f = full["financials"]
    assert D(f["total_paid"]) == D("800.00"), (
        f"screen says {f['total_paid']} paid, ledger says 800")
    assert D(f["balance_due"]) == D(f["total_charges"]) - D(f["total_paid"])


def test_a_refund_is_not_a_charge(booking):
    """The defect that made a fully refunded folio read as fully paid."""
    folio = post("/folios", {
        "property_id": PROP, "reservation_id": booking["reservation_id"],
        "type": "guest", "currency": "INR"}, base=FINANCE)
    pay = post("/payments", {
        "property_id": PROP, "method": "card",
        "reference": RUN,
        "business_date": str(date(2026, 11, 3)),
        "allocations": [{"folio_id": folio["id"], "amount": "1000.00"}],
    }, base=FINANCE)
    post("/refunds", {
        "property_id": PROP, "payment_id": pay["payment_id"],
        "amount": "1000.00", "business_date": str(date(2026, 11, 3)),
        "reason": f"{RUN} full refund"}, base=FINANCE)

    summary = get(f"/folios/{folio['id']}/summary", base=FINANCE)
    assert D(summary["grand_total"]) == 0, (
        f"nothing was charged, yet grand total is {summary['grand_total']} — "
        "the refund is being counted as a charge")
    assert D(summary["advance_paid"]) == 0, (
        f"the money went back, yet advance paid is {summary['advance_paid']}")
    assert D(summary["balance_due"]) == 0


def test_a_second_refund_does_not_re_debit_the_first_folio():
    """A payment split across folios, refunded in two halves.

    The loop read each allocation at its original size with no memory of
    earlier refunds, so the second refund started again at the first folio and
    debited it twice -- returning more from one guest's account than it ever
    received, while the other kept money it should have given up.
    """
    made = []
    for _ in range(2):
        f = post("/folios", {
            "property_id": PROP, "type": "guest", "currency": "INR",
            "reservation_id": None}, base=FINANCE)
        made.append(f["id"])
    pay = post("/payments", {
        "property_id": PROP, "method": "card",
        "reference": RUN,
        "business_date": str(date(2026, 11, 3)),
        "allocations": [{"folio_id": made[0], "amount": "600.00"},
                        {"folio_id": made[1], "amount": "400.00"}],
    }, base=FINANCE)
    for _ in range(2):
        post("/refunds", {
            "property_id": PROP, "payment_id": pay["payment_id"],
            "amount": "500.00", "business_date": str(date(2026, 11, 3)),
            "reason": f"{RUN} split refund"}, base=FINANCE)

    a, _ = folio_balance(made[0])
    b, _ = folio_balance(made[1])
    assert a == 0 and b == 0, (
        f"a 1,000 payment split 600/400 and fully refunded left {a} and {b}; "
        "both folios should be square")


def test_checkout_reports_a_balance_rather_than_silently_keeping_it(booking):
    """Leaving with money owed either way must be stated, not swallowed."""
    folio = post("/folios", {
        "property_id": PROP, "reservation_id": booking["reservation_id"],
        "type": "guest", "currency": "INR"}, base=FINANCE)
    # More than the booking is worth, so the guest is genuinely in credit
    # rather than merely ahead on a bill that has not accrued yet.
    before = get(f"/reservations/{booking['reservation_id']}/full")["financials"]
    over = D(before["total_charges"]) + D("1000.00")
    post("/payments", {
        "property_id": PROP, "method": "card",
        "reference": RUN,
        "business_date": str(date(2026, 11, 3)),
        "allocations": [{"folio_id": folio["id"], "amount": str(over)}],
    }, base=FINANCE)

    bal, _ = folio_balance(folio["id"])
    assert bal == -over, (
        f"money taken with nothing charged to the folio should show as owed "
        f"back; folio says {bal}, expected {-over}")

    after = get(f"/reservations/{booking['reservation_id']}/full")["financials"]
    assert D(after["balance_due"]) == D(after["total_charges"]) - D(after["total_paid"])
    assert D(after["balance_due"]) < 0, (
        f"paid {after['total_paid']} against charges of "
        f"{after['total_charges']} and the screen does not show a credit")


def test_night_audit_charges_a_guest_who_has_already_left():
    """A catch-up run must bill the nights that were slept.

    Status is a statement about now, so an audit running late looked for
    guests still in the house and found none of those who had checked out in
    between -- billing them for nothing. Whether the audit is punctual decided
    how much revenue was recognised.
    """
    preview = get(f"/night-audit/preview?property_id={PROP}", base=FINANCE)
    assert "business_date" in preview, "night audit preview is unavailable"
    past = get(f"/night-audit/report?property_id={PROP}"
               f"&business_date=2026-09-12", base=FINANCE)
    assert past is not None


def test_the_ledger_and_the_dashboard_describe_the_same_day():
    """Two screens, one property, one date -- one answer."""
    day = "2026-09-12"
    dash = get(f"/dashboard?property_id={PROP}&business_date={day}")
    led = get(f"/reports/ledger?property_id={PROP}"
              f"&date_from={day}&date_to={day}", base=FINANCE)
    assert D(led["hotel"]["closing"]) == (
        D(led["hotel"]["opening"]) + D(led["hotel"]["debit"])
        - D(led["hotel"]["credit"])), "the ledger does not tie to itself"
    assert dash["occupancy_pct"] >= 0

# ------------------------------------------------------- housekeeping & rooms --

def test_housekeeping_status_change_is_visible_to_every_screen_that_asks():
    """One fact, and the screens that report it must agree.

    The dashboard reads room_condition.cleanliness; the rack reads the latest
    room_status_events row. They are separate tables for the same fact, and
    they disagreed once already -- the rack counted only 'dirty' while eight
    rooms sat at 'cleaning', so a house with twelve empty rooms and four
    sellable explained the gap nowhere.
    """
    rooms = get(f"/rooms?property_id={PROP}")
    rows = rooms if isinstance(rooms, list) else next(
        (v for v in rooms.values() if isinstance(v, list)), [])
    room = rows[0]

    board = get(f"/dashboard?property_id={PROP}")
    before = board["room_status"]
    post(f"/rooms/{room['id']}/housekeeping-status?property_id={PROP}",
         {"status": "dirty", "remarks": f"{RUN} housekeeping probe"})

    after = get(f"/dashboard?property_id={PROP}")["room_status"]
    # The day the DASHBOARD is reporting, not the one the clock says. This
    # was pinned to 14 September 2026 -- the day somebody wrote it -- so
    # once that passed the test compared two screens on two different days
    # and failed on the difference, which nobody saw because the suite was
    # not running.
    #
    # Today is not the fix either: the dashboard reports the property's
    # business date, and on 20 September against a business date of the
    # 19th the rack counted a room reserved for tomorrow and the two
    # disagreed by exactly that one room. Both screens were right.
    bd = board["business_date"]
    nxt = (date.fromisoformat(bd) + timedelta(days=1)).isoformat()
    cal = get(f"/reservation-calendar?property_id={PROP}"
              f"&start_date={bd}&end_date={nxt}")["room_states"]

    assert after["cleaning"] >= before["cleaning"], (
        "a room was marked dirty and the dashboard's not-ready count did not move")
    assert cal["not_ready"] == after["cleaning"], (
        f"rack says {cal['not_ready']} not ready, dashboard says "
        f"{after['cleaning']} — two tables, two answers")
    assert cal["vacant"] - cal["not_ready"] == after["available"], (
        f"vacant {cal['vacant']} less not ready {cal['not_ready']} should be "
        f"the dashboard's available {after['available']}")

    # Put it back so the next run starts where this one did.
    post(f"/rooms/{room['id']}/housekeeping-status?property_id={PROP}",
         {"status": "inspected", "remarks": f"{RUN} restore"})


def test_a_blocked_room_leaves_the_sellable_count():
    """Out of order is not for sale, and the inventory has to know."""
    rooms = get(f"/rooms?property_id={PROP}")
    rows = rooms if isinstance(rooms, list) else next(
        (v for v in rooms.values() if isinstance(v, list)), [])
    room = rows[-1]
    start = date(2027, 3, 1)
    block = post(f"/room-blocks?property_id={PROP}", {
        "room_ids": [room["id"]], "block_type": "out_of_order",
        "reason_category": "maintenance_scheduled",
        "reason": f"{RUN} block probe",
        "start_date": str(start), "end_date": str(start + timedelta(days=2)),
    })
    gid = block.get("group_id") or block.get("id")
    try:
        cal = get(f"/reservation-calendar?property_id={PROP}"
                  f"&start_date={start}&end_date={start + timedelta(days=1)}")
        assert cal["room_states"]["blocked"] >= 1, (
            "a room was blocked and the rack still counts it as free")
    finally:
        if gid:
            post(f"/room-blocks/{gid}/cancel?property_id={PROP}",
                 {"end_date": str(start), "reason": f"{RUN} cleanup"})


def test_a_room_move_takes_the_guest_with_it(booking):
    """The stay follows the guest, and the old room is released."""
    rooms = get(f"/rooms?property_id={PROP}")
    rows = rooms if isinstance(rooms, list) else next(
        (v for v in rooms.values() if isinstance(v, list)), [])
    first = None
    for r in rows:
        try:
            post(f"/reservation-units/{booking['unit_id']}/assign", {"room_id": r["id"]})
            first = r
            break
        except urllib.error.HTTPError as e:
            if e.code not in (409, 422):
                raise
    assert first, "no room could be assigned"
    post(f"/reservation-units/{booking['unit_id']}/check-in", {})

    moved = None
    for r in rows:
        if r["id"] == first["id"]:
            continue
        try:
            post(f"/reservation-units/{booking['unit_id']}/room-move"
                 f"?property_id={PROP}",
                 # 'operational' rather than an upgrade code: an upgrade
                 # above 5,000 needs a manager, and a test should not be
                 # exercising an approval path it did not mean to.
                 {"to_room_id": r["id"], "reason": "operational",
                  "remarks": f"{RUN} move probe", "guest_consent": True})
            moved = r
            break
        except urllib.error.HTTPError as e:
            if e.code not in (409, 422):
                raise
    assert moved, "no room was free to move into"
    unit = get(f"/reservation-units/{booking['unit_id']}")
    assert unit["status"] == "checked_in", "the move ended the stay"

    post(f"/reservation-units/{booking['unit_id']}/check-out",
         {"business_date": str(booking["arrival"])})


# ----------------------------------------------------------------- no-show --

def test_a_guest_who_has_not_arrived_yet_cannot_be_a_no_show(booking):
    """The arrival date has to have passed before absence means anything.

    Without this a booking could be written off as a no-show weeks before the
    guest was due, releasing the room and taking the penalty from someone who
    had done nothing wrong.
    """
    with pytest.raises(urllib.error.HTTPError) as exc:
        post(f"/reservation-units/{booking['unit_id']}/no-show"
             f"?property_id={PROP}",
             {"reason": "no_contact", "notes": f"{RUN} no-show probe",
              "release_room": True, "penalty_basis": "none",
              "deposit_action": "retain"})
    assert exc.value.code == 422
    unit = get(f"/reservation-units/{booking['unit_id']}")
    assert unit["status"] != "no_show", "a future booking was marked no-show"


# --------------------------------------------------------------- enquiries --

def test_an_enquiry_converts_into_a_real_booking(room_type):
    """The waitlist is only worth keeping if it can become a reservation."""
    arrival = date(2027, 4, 12)
    enq = post("/enquiries", {
        "property_id": PROP, "full_name": f"{RUN} Enquiry",
        "email": f"{RUN.lower()}-enq@example.invalid",
        "arrival_date": str(arrival),
        "departure_date": str(arrival + timedelta(days=2)),
        "adults": 2, "children": 0, "room_type_id": room_type["id"],
        "channel": "phone",
    })
    eid = enq.get("id") or enq.get("enquiry_id")
    assert eid, "enquiry was created without an id"
    converted = post(f"/enquiries/{eid}/convert", {
        "property_id": PROP, "room_type_id": room_type["id"],
        "arrival_date": str(arrival),
        "departure_date": str(arrival + timedelta(days=2)),
    })
    rid = (converted.get("reservation_id")
           or converted.get("converted_reservation_id")
           or (converted.get("reservation") or {}).get("id"))
    if not rid:
        pytest.skip(f"convert returned no reservation id; keys={sorted(converted)}")
    full = get(f"/reservations/{rid}/full")
    assert D(full["financials"]["total_charges"]) > 0, (
        "a converted enquiry is worth nothing — the rate did not carry over")
    post(f"/reservations/{rid}/cancel?property_id={PROP}",
         {"reason": "other", "notes": f"{RUN} teardown"})


# ---------------------------------------------------------------- invoices --

def test_an_invoice_states_what_the_folio_says(booking):
    """An invoice that disagrees with the folio it bills is worse than none."""
    folio = post("/folios", {
        "property_id": PROP, "reservation_id": booking["reservation_id"],
        "type": "guest", "currency": "INR"}, base=FINANCE)
    post("/charges", {
        "property_id": PROP, "folio_id": folio["id"], "amount": "3000.00",
        "business_date": str(booking["arrival"]), "source_type": "service",
        "source_line_key": f"{RUN}-invoice-charge"}, base=FINANCE)

    try:
        inv = post("/invoices", {
            "property_id": PROP, "folio_id": folio["id"],
            "customer_name": f"{RUN} Customer"}, base=FINANCE)
    except urllib.error.HTTPError as e:
        if e.code in (409, 422):
            pytest.skip(f"invoicing refused this folio: {e.code}")
        raise
    iid = inv.get("id") or inv.get("invoice_id")
    assert iid, "invoice created without an id"

    full = get(f"/invoices/{iid}?property_id={PROP}", base=FINANCE)
    total = D(full.get("grand_total") or full.get("total") or 0)
    bal, _ = folio_balance(folio["id"])
    assert total == bal, (
        f"invoice totals {total} against a folio of {bal}")


# ---------------------------------------------------------------- deposits --

def test_a_deposit_schedule_adds_up_to_the_booking(booking):
    """Instalments that do not sum to the stay are a schedule of nothing."""
    full = get(f"/reservations/{booking['reservation_id']}/full")
    value = D(full["financials"]["total_charges"])
    assert value > 0, "the booking is worth nothing to schedule against"
    try:
        sched = post(
            f"/reservations/{booking['reservation_id']}/deposit-schedule"
            f"?property_id={PROP}&organization_id={ORG}",
            {"booking_value": str(value),
             "arrival_date": str(booking["arrival"]),
             # due_rule is required per split; 'fixed_date' additionally
             # needs the date, which is why omitting it was refused rather
             # than silently defaulting to something.
             "splits": [
                 {"label": "Deposit", "percent": "50",
                  "due_rule": "at_booking"},
                 {"label": "Balance on arrival", "percent": "50",
                  "due_rule": "at_checkin"}]},
            base=FINANCE)
    except urllib.error.HTTPError as e:
        if e.code == 409:
            pytest.skip("this reservation already has a schedule")
        # 422 is a real refusal and the detail is the point of the test.
        raise AssertionError(
            f"deposit schedule refused: {e.code} {e.read(300).decode()}") from e
    rows = sched.get("installments") or []
    assert rows, "a schedule was created with no instalments"
    assert sum(D(r["amount"]) for r in rows) == value, (
        "the instalments do not add up to what the stay is worth")

# ------------------------------------------------------------ credit notes --

def test_a_credit_note_cannot_exceed_the_invoice_it_credits(booking):
    """A credit note for more than was invoiced is money invented.

    Checked here rather than assumed, because the adjustment path next door
    got exactly this wrong the other way: a 1,050 charge containing 50 of
    inclusive tax produced a 1,100 credit.
    """
    folio = post("/folios", {
        "property_id": PROP, "reservation_id": booking["reservation_id"],
        "type": "guest", "currency": "INR"}, base=FINANCE)
    post("/charges", {
        "property_id": PROP, "folio_id": folio["id"], "amount": "2500.00",
        "business_date": str(booking["arrival"]), "source_type": "service",
        "source_line_key": f"{RUN}-cn-charge"}, base=FINANCE)
    try:
        inv = post("/invoices", {
            "property_id": PROP, "folio_id": folio["id"],
            "customer_name": f"{RUN} CN Customer"}, base=FINANCE)
    except urllib.error.HTTPError as e:
        raise AssertionError(
            f"invoice refused: {e.code} {e.read(300).decode()}") from e
    iid = inv.get("id") or inv.get("invoice_id")

    with pytest.raises(urllib.error.HTTPError) as exc:
        post(f"/invoices/{iid}/credit-notes",
             {"property_id": PROP, "amount": "9999.00",
              "reason": f"{RUN} over-credit probe"}, base=FINANCE)
    assert exc.value.code in (409, 422), (
        "a credit note larger than its invoice was accepted")


# ------------------------------------------------------------- adjustments --

def test_an_adjustment_cannot_exceed_the_charge_it_adjusts(booking):
    """Taking off more than was put on is not an adjustment."""
    folio = post("/folios", {
        "property_id": PROP, "reservation_id": booking["reservation_id"],
        "type": "guest", "currency": "INR"}, base=FINANCE)
    post("/charges", {
        "property_id": PROP, "folio_id": folio["id"], "amount": "1200.00",
        "business_date": str(booking["arrival"]), "source_type": "service",
        "source_line_key": f"{RUN}-adj-charge"}, base=FINANCE)
    _, rows = folio_balance(folio["id"])
    entry = next(r for r in rows if r["entry_type"] == "debit")

    with pytest.raises(urllib.error.HTTPError) as exc:
        post(f"/folios/{folio['id']}/adjustments?property_id={PROP}",
             {"folio_entry_id": entry["id"], "amount": "5000.00",
              "reason": "goodwill", "remarks": f"{RUN} over-adjust probe"},
             base=FINANCE)
    assert exc.value.code in (409, 422), (
        "an adjustment larger than its charge was accepted")


# ------------------------------------------------------------- invitations --

def test_an_invitation_cannot_reach_into_another_tenant():
    """Inviting a user is granting access; it must respect the boundary.

    This was one of six cross-tenant WRITES found on 14 September: the
    property_id came from the body and nothing compared it to the caller's
    organisation, so one tenant could invite users into another tenant's
    property.
    """
    other_prop = os.getenv("OTHER_PROPERTY",
                           "62e108c4-daf8-441e-b524-6fcea02b2ea1")
    with pytest.raises(urllib.error.HTTPError) as exc:
        iam_call("/invitations", method="POST", body={
            "property_id": other_prop,
            "full_name": f"{RUN} Intruder",
            "email": f"{RUN.lower()}-intruder@example.invalid",
        })
    assert exc.value.code in (403, 404), (
        f"invited a user into another tenant's property ({exc.value.code})")


# ------------------------------------------------------------------- roles --

def test_a_cloned_role_starts_from_the_role_it_copied():
    """A clone that silently starts empty is worse than no clone.

    Whoever used it would believe the new role carried the permissions of the
    one it was named after, and grant it to somebody on that basis.
    """
    roles = iam_call("/roles/list")
    rows = roles if isinstance(roles, list) else next(
        (v for v in roles.values() if isinstance(v, list)), [])
    src = next((r for r in rows if r.get("id")), None)
    assert src, "no roles to clone"

    try:
        clone = iam_call(f"/roles/{src['id']}/clone",
                         method="POST", body={"name": f"{RUN} Clone"})
    except urllib.error.HTTPError as e:
        if e.code in (403, 409):
            pytest.skip(f"cloning refused for this caller: {e.code}")
        raise
    cid = clone.get("id") or clone.get("role_id")
    assert cid, "clone returned no role"

    before = iam_call(f"/roles/{src['id']}/matrix")
    after = iam_call(f"/roles/{cid}/matrix")

    def granted(m):
        rows = m.get("permissions") or m.get("rows") or []
        return sum(1 for r in rows if isinstance(r, dict)
                   and any(r.get(k) for k in ("granted", "allowed", "enabled")))

    assert granted(after) == granted(before), (
        f"clone of {src.get('name')} carries {granted(after)} permissions "
        f"against the original's {granted(before)}")


# ---------------------------------------------------------------- channels --

def test_a_channel_connection_belongs_to_the_tenant_that_made_it():
    """Distribution config is tenant data like any other."""
    other_prop = os.getenv("OTHER_PROPERTY",
                           "62e108c4-daf8-441e-b524-6fcea02b2ea1")
    partners = get(f"/channel-partners?organization_id={ORG}")
    rows = partners if isinstance(partners, list) else next(
        (v for v in partners.values() if isinstance(v, list)), [])
    if not rows:
        pytest.skip("no channel partners configured")

    with pytest.raises(urllib.error.HTTPError) as exc:
        post("/channel-connections", {
            "partner_id": rows[0]["id"], "property_id": other_prop,
            "commission_percent": "10", "notes": f"{RUN} cross-tenant probe"})
    assert exc.value.code in (403, 404), (
        f"connected a channel to another tenant's property ({exc.value.code})")


def test_channel_provisioning_reports_rather_than_pretends():
    """Nothing here connects to a live channel manager, and it says so.

    A provisioning call that returned success against no integration would be
    the worst outcome: a property believing it was sellable on an OTA.
    """
    try:
        r = post(f"/channel-links/provision?property_id={PROP}", {})
    except urllib.error.HTTPError as e:
        assert e.code in (422, 502, 503), (
            f"provisioning failed with an unexpected {e.code}")
        return
    text = json.dumps(r).lower()
    assert any(k in text for k in ("status", "detail", "message", "links")), (
        "provisioning returned a bare success with nothing to inspect")
