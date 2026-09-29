"""Two tenants on one OTA, against the REAL Channex staging.

``channel_integration_check.py`` proves the same things against
``fake_channex.py``. This one runs them against Channex itself, because the
questions that matter for isolation are about the far side: which group a
property lands in, which property a booking names, and whether one tenant's
edit ever moves another tenant's prices.

Every tenant of this platform shares ONE Channex account (one API key, one
webhook secret, one booking feed). What keeps them apart:

* a Channex group per organisation, and a Channex property per PMS property;
* ``channel_manager_links.external_property_id`` is unique, so one Channex
  property can belong to one PMS property only;
* a booking is routed by the property id on the revision fetched from Channex
  (never by anything in the webhook body), and from then on the transaction is
  bound to that tenant, under row-level security;
* ARI is sent per link, with that link's property, room and rate ids only.

Tenant A must already be connected (``setup_channex_test_property.py`` plus a
channel partner). Tenant B is set up here, through the PMS API, idempotently.

    python e2e/channel_isolation_live_check.py \\
        --a e2e/out/creds_owner.json --b e2e/out/creds_b.json

Needs the stack running (services connected as the runtime role, so RLS is
enforced) and CHANNEX_API_KEY in the environment: it reads Channex to verify
what arrived, and creates the OTA bookings through Booking CRS, which stands
in for the OTA. It never pushes ARI to Channex itself -- the PMS does that.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
import uuid

GATEWAY = os.environ.get("GATEWAY", "http://localhost:8000") + "/api"
CHANNEX = os.environ.get("CHANNEX_API_URL", "https://staging.channex.io/api/v1")
B_ROOM = ("GARDEN", "Garden Room", "301-303")
B_PLAN = ("GD-BAR", "Best Available Rate")
B_HOTEL_ID = "7654321"
PROBE_DAY = "2027-06-15"
BOOK_IN, BOOK_OUT = "2027-01-10", "2027-01-12"
results: list[tuple[str, bool, str]] = []


def _req(url, method, body, headers):
    req = urllib.request.Request(
        url, method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"content-type": "application/json", **headers})
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            raw = r.read()
            return r.status, json.loads(raw) if raw else None
    except urllib.error.HTTPError as e:
        raw = e.read()
        try:
            return e.code, json.loads(raw)
        except ValueError:
            return e.code, raw.decode(errors="ignore")[:300]


def pms(method, path, tok, body=None):
    return _req(GATEWAY + path, method, body, {"authorization": "Bearer " + tok})


def cx(method, path, body=None):
    return _req(CHANNEX + path, method, body,
                {"user-api-key": os.environ["CHANNEX_API_KEY"]})


def check(name, ok, detail=""):
    results.append((name, bool(ok), str(detail)[:300]))
    print(("PASS " if ok else "FAIL ") + name + (f"  -- {detail}" if detail and not ok else ""))


class Tenant:
    def __init__(self, creds_path, property_name=None):
        with open(creds_path) as f:
            creds = json.load(f)
        s, login = _req(GATEWAY + "/iam/auth/login", "POST", creds, {})
        if s != 200:
            raise SystemExit(f"login {creds_path}: {s} {login}")
        self.tok = login["token"]
        self.org = login["memberships"][0]["organization_id"]
        _, props = pms("GET", "/iam/properties", self.tok)
        if property_name:
            self.pid = next(p["id"] for p in props if p["name"] == property_name)
        else:
            self.pid = login["memberships"][0]["property_ids"][0]
        self.link = None

    def get_link(self):
        _, self.link = pms("GET", f"/booking/channel-links?property_id={self.pid}", self.tok)
        return self.link

    def sync_rows(self):
        s, rows = pms("GET", f"/booking/channel-links/{self.link['id']}/sync-log?limit=500", self.tok)
        return rows if s == 200 else []

    def events(self):
        s, rows = pms("GET", f"/booking/channels/events?property_id={self.pid}&limit=200", self.tok)
        return rows if s == 200 else []


def ensure_partner(t: Tenant) -> str:
    _, partners = pms("GET", f"/booking/channel-partners?organization_id={t.org}", t.tok)
    rows = partners.get("rows", []) if isinstance(partners, dict) else partners
    found = next((r for r in rows or [] if isinstance(r, dict)
                  and r.get("name", "").lower() == "booking.com"), None)
    if found is None:
        s, found = pms("POST", "/booking/booking-attributes", t.tok, {
            "organization_id": t.org, "kind": "business_source",
            "name": "Booking.com", "code": None, "status": "active"})
        assert s == 201, found
    pms("PUT", f"/booking/channel-partners/{found['id']}/type", t.tok,
        {"partner_type": "online_channel"})
    return found["id"]


def setup_b(b: Tenant) -> tuple[str, str]:
    """Garden Room x3 and one rate plan, as the Property Setup screens do."""
    _, rts = pms("GET", f"/booking/room-types?property_id={b.pid}", b.tok)
    rt = next((r["id"] for r in rts or [] if r["name"] == B_ROOM[1]), None)
    if rt is None:
        s, made = pms("POST", f"/booking/room-types/manage?property_id={b.pid}", b.tok, {
            "code": B_ROOM[0], "name": B_ROOM[1], "max_adults": 2,
            "max_occupancy": 2, "base_rate": "80"})
        assert s == 201, made
        rt = made["id"]
        pms("POST", f"/booking/rooms/bulk-create?property_id={b.pid}", b.tok,
            {"room_type_id": rt, "spec": B_ROOM[2]})
    _, plans = pms("GET", f"/booking/rate-plans?property_id={b.pid}", b.tok)
    rows = plans.get("items", plans) if isinstance(plans, dict) else plans
    rp = next((p["id"] for p in rows or [] if p.get("code") == B_PLAN[0]), None)
    if rp is None:
        s, made = pms("POST", f"/booking/rate-plans?property_id={b.pid}", b.tok, {
            "code": B_PLAN[0], "name": B_PLAN[1], "room_type_ids": [rt],
            "adjustment_direction": "increase", "adjustment_type": "amount",
            "adjustment_value": "0"})
        assert s == 201, made
        rp = made["id"]
    return rt, rp


def channex_rate(prop, rate_plan, day):
    _, out = cx("GET", f"/restrictions?filter[property_id]={prop}&filter[date][gte]={day}"
                       f"&filter[date][lte]={day}&filter[restrictions]=rate")
    return (((out or {}).get("data") or {}).get(rate_plan) or {}).get(day, {}).get("rate")


def wait_for(fn, timeout, every=5):
    end = time.time() + timeout
    while time.time() < end:
        v = fn()
        if v:
            return v
        time.sleep(every)
    return None


def crs_booking(prop, room, plan, code):
    s, out = cx("POST", "/bookings", {"booking": {
        "status": "new", "property_id": prop, "ota_name": "Booking.com",
        "ota_reservation_code": code, "arrival_date": BOOK_IN,
        "departure_date": BOOK_OUT, "currency": "USD",
        "customer": {"name": "Isolation", "surname": code, "mail": "guest@example.com",
                     "country": "US"},
        "rooms": [{"room_type_id": room, "rate_plan_id": plan,
                   "days": {BOOK_IN: "100.00", "2027-01-11": "100.00"},
                   "occupancy": {"adults": 2, "children": 0, "infants": 0}}]}})
    return s, out


def install_booking_crs(prop):
    """Booking CRS stands in for the OTA; Channex takes bookings through it
    only on properties it is installed on."""
    _, apps = cx("GET", f"/applications/installed?filter[property_id]={prop}")
    if any((r.get("attributes") or {}).get("application_code") == "booking_crs"
           for r in (apps or {}).get("data") or []):
        return
    _, catalogue = cx("GET", "/applications")
    app = next(r["id"] for r in catalogue["data"]
               if r["attributes"].get("code") == "booking_crs")
    cx("POST", "/applications/install", {"application_installation": {
        "property_id": prop, "application_id": app}})


def plan_id(t: Tenant, code):
    _, plans = pms("GET", f"/booking/rate-plans?property_id={t.pid}", t.tok)
    rows = plans.get("items", plans) if isinstance(plans, dict) else plans
    return next(p["id"] for p in rows if p.get("code") == code)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--a", required=True)
    ap.add_argument("--a-property", default="Test Property - Chirala PMS")
    ap.add_argument("--b", required=True)
    ap.add_argument("--out")
    args = ap.parse_args()
    sys.stdout.reconfigure(line_buffering=True)

    a, b = Tenant(args.a, args.a_property), Tenant(args.b)
    check("Tenants are different organisations", a.org != b.org, (a.org, b.org))
    link_a = a.get_link()
    if not link_a or not link_a.get("external_property_id"):
        raise SystemExit("Tenant A is not connected to Channex yet.")
    ext_a = link_a["external_property_id"]
    _, conns_a = pms("GET", f"/booking/channel-connections?property_id={a.pid}", a.tok)
    a_hotel = next((c.get("ota_hotel_id") for c in (conns_a or []) if isinstance(c, dict)
                    and c.get("ota_hotel_id")), None)

    # ---- B sets up, and tries to reach for A's identifiers on the way ----
    b_room, b_plan = setup_b(b)
    partner_b = ensure_partner(b)
    _, conns_b = pms("GET", f"/booking/channel-connections?property_id={b.pid}", b.tok)
    conn_b = next((c for c in conns_b or [] if isinstance(c, dict)), None)
    if conn_b is None and a_hotel:
        s, out = pms("POST", "/booking/channel-connections", b.tok, {
            "partner_id": partner_b, "property_id": b.pid, "ota_hotel_id": a_hotel})
        check("B cannot connect Booking.com with A's Booking.com hotel id",
              s == 409, f"HTTP {s}: {out}")
        if s in (200, 201):
            conn_b = out
    if conn_b is None:
        s, conn_b = pms("POST", "/booking/channel-connections", b.tok, {
            "partner_id": partner_b, "property_id": b.pid, "ota_hotel_id": B_HOTEL_ID})
        assert s == 201, conn_b
    elif a_hotel and conn_b.get("ota_hotel_id") != a_hotel:
        # Already connected: try to move B's connection onto A's listing.
        s, out = pms("PUT", f"/booking/channel-connections/{conn_b['id']}", b.tok, {
            "partner_id": partner_b, "property_id": b.pid, "ota_hotel_id": a_hotel})
        check("B cannot connect Booking.com with A's Booking.com hotel id",
              s == 409, f"HTTP {s}: {out}")
    if conn_b.get("ota_hotel_id") != B_HOTEL_ID:
        pms("PUT", f"/booking/channel-connections/{conn_b['id']}", b.tok, {
            "partner_id": partner_b, "property_id": b.pid, "ota_hotel_id": B_HOTEL_ID})

    s, out = pms("PUT", "/booking/channel-links", b.tok,
                 {"property_id": b.pid, "external_property_id": ext_a})
    check("B cannot claim A's Channex property id", s == 409, f"HTTP {s}: {out}")

    s, prov = pms("POST", f"/booking/channel-links/provision?property_id={b.pid}", b.tok)
    ext_b = (prov or {}).get("external_property_id") if isinstance(prov, dict) else None
    check("B is provisioned to its own Channex property",
          s == 200 and ext_b and ext_b != ext_a, f"HTTP {s}: {prov}")
    b.get_link()

    # ---- what Channex holds ----
    _, pa = cx("GET", f"/properties/{ext_a}")
    _, pb = cx("GET", f"/properties/{ext_b}")

    def groups(p):
        return {g["id"] for g in ((p or {}).get("data", {}).get("relationships", {})
                                  .get("groups", {}).get("data") or [])}
    check("A's and B's Channex properties are in different groups",
          groups(pa) and groups(pb) and not groups(pa) & groups(pb), (groups(pa), groups(pb)))
    _, rta = cx("GET", f"/room_types?filter[property_id]={ext_a}")
    _, rtb = cx("GET", f"/room_types?filter[property_id]={ext_b}")
    titles_a = sorted(r["attributes"]["title"] for r in rta["data"])
    titles_b = sorted(r["attributes"]["title"] for r in rtb["data"])
    check("B's Channex property holds only B's rooms", titles_b == [B_ROOM[1]], titles_b)
    check("A's Channex property still holds only A's rooms",
          B_ROOM[1] not in titles_a and titles_a, titles_a)

    # ---- B's credentials against A's data (read through the API) ----
    probes = {
        "channel link": f"/booking/channel-links?property_id={a.pid}",
        "sync log": f"/booking/channel-links/{link_a['id']}/sync-log",
        "booking deliveries": f"/booking/channels/events?property_id={a.pid}",
        "channel connections": f"/booking/channel-connections?property_id={a.pid}",
        "reservations": f"/booking/reservations?property_id={a.pid}",
    }
    for what, path in probes.items():
        s, out = pms("GET", path, b.tok)
        leaked = s == 200 and out not in (None, [], {})
        check(f"B cannot read A's {what}", not leaked, f"HTTP {s}: {str(out)[:150]}")
    s, out = pms("POST", f"/booking/channel-links/provision?property_id={a.pid}", b.tok)
    check("B cannot provision A's property", s in (403, 404), f"HTTP {s}")
    s, out = pms("PUT", f"/booking/rate-plan-calendar?property_id={a.pid}", b.tok, {
        "changes": [{"rate_plan_id": plan_id(a, "TW-BAR"), "date_from": PROBE_DAY,
                     "date_to": PROBE_DAY, "rate": 1}]})
    check("B cannot change A's rates", s in (403, 404, 422), f"HTTP {s}: {out}")

    # ---- ARI: an edit in one tenant moves only that tenant's Channex property ----
    b_plan_ext = next(r["external_id"] for r in b.link["rates"] if r["local_id"] == b_plan)
    a_plan_ext = next(r["external_id"] for r in link_a["rates"]
                      if r["local_id"] == plan_id(a, "TW-BAR"))
    wait_for(lambda: channex_rate(ext_b, b_plan_ext, PROBE_DAY), 120)
    b_before = channex_rate(ext_b, b_plan_ext, PROBE_DAY)
    b_rows_before = len(b.sync_rows())
    new_a = "211.00" if channex_rate(ext_a, a_plan_ext, PROBE_DAY) != "211.00" else "212.00"
    s, _ = pms("PUT", f"/booking/rate-plan-calendar?property_id={a.pid}", a.tok, {
        "changes": [{"rate_plan_id": plan_id(a, "TW-BAR"), "date_from": PROBE_DAY,
                     "date_to": PROBE_DAY, "rate": float(new_a)}]})
    got = wait_for(lambda: channex_rate(ext_a, a_plan_ext, PROBE_DAY) == new_a, 150)
    check("A's price edit reaches A's Channex property", s == 200 and got, new_a)
    check("A's price edit leaves B's Channex price alone",
          channex_rate(ext_b, b_plan_ext, PROBE_DAY) == b_before, b_before)
    check("A's price edit adds nothing to B's sync log",
          len(b.sync_rows()) == b_rows_before, "")
    a_now = channex_rate(ext_a, a_plan_ext, PROBE_DAY)
    s, _ = pms("PUT", f"/booking/rate-plan-calendar?property_id={b.pid}", b.tok, {
        "changes": [{"rate_plan_id": b_plan, "date_from": PROBE_DAY,
                     "date_to": PROBE_DAY, "rate": 88}]})
    got = wait_for(lambda: channex_rate(ext_b, b_plan_ext, PROBE_DAY) == "88.00", 150)
    check("B's price edit reaches B's Channex property", s == 200 and got, "")
    check("B's price edit leaves A's Channex price alone",
          channex_rate(ext_a, a_plan_ext, PROBE_DAY) == a_now, a_now)

    # ---- bookings: one Channex account, one feed, two tenants ----
    tag = uuid.uuid4().hex[:6].upper()
    b_room_ext = next(r["external_id"] for r in b.link["rooms"] if r["local_id"] == b_room)
    a_room_ext = next(r["external_id"] for r in link_a["rooms"]
                      if r["external_name"] and "Double" in r["external_name"])
    a_dbl_plan = next(r["external_id"] for r in link_a["rates"]
                      if r["local_id"] == plan_id(a, "DB-BAR"))
    install_booking_crs(ext_a)
    install_booking_crs(ext_b)
    s1, bk_b = crs_booking(ext_b, b_room_ext, b_plan_ext, f"ISO-B-{tag}")
    s2, bk_a = crs_booking(ext_a, a_room_ext, a_dbl_plan, f"ISO-A-{tag}")
    rev_b = (bk_b or {}).get("data", {}).get("attributes", {}).get("revision_id")
    rev_a = (bk_a or {}).get("data", {}).get("attributes", {}).get("revision_id")
    check("Channex accepted one OTA booking per tenant", rev_a and rev_b, (s1, s2))
    print("waiting for the PMS to receive both bookings (feed every 5 minutes)...")

    def landed():
        ea = {e["revision_id"]: e for e in a.events()}
        eb = {e["revision_id"]: e for e in b.events()}
        return (ea, eb) if rev_a in ea and rev_b in eb else None
    got = wait_for(landed, 420, every=10)
    ea = {e["revision_id"]: e for e in a.events()}
    eb = {e["revision_id"]: e for e in b.events()}
    check("B's OTA booking became a reservation in B",
          rev_b in eb and eb[rev_b].get("reservation_id"), eb.get(rev_b))
    check("A's OTA booking became a reservation in A",
          rev_a in ea and ea[rev_a].get("reservation_id"), ea.get(rev_a))
    check("B's booking is invisible to A, and A's to B",
          rev_b not in ea and rev_a not in eb, "")
    if got:
        res_b = eb[rev_b].get("reservation_id")
        s, _ = pms("GET", f"/booking/reservations/{res_b}/detail", a.tok)
        check("A cannot open B's channel reservation", s in (403, 404), f"HTTP {s}")

    passed = sum(ok for _, ok, _ in results)
    print(f"\n{passed}/{len(results)} passed")
    if args.out:
        with open(args.out, "w") as f:
            json.dump([{"check": n, "ok": ok, "detail": d} for n, ok, d in results], f, indent=1)
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
