"""Channel-manager (Channex) integration check, against a running stack.

Uses e2e/fake_channex.py in place of Channex (the real one is not reachable
from test environments, and a fake can be made to fail on demand). Drives the
integration exactly as the Channel Partners screens do, then sends bookings in
through the real webhook endpoint:

  Partners    add Agoda as an OTA partner with commission and hotel id
  Provision   property, room types, rate plans, webhook, OTA channel created
              at the channel manager; re-running creates nothing new
  Push        availability and rates reach the channel manager and match PMS
  Inbound     new booking -> reservation (right room type, dates, rate, guest,
              source), acknowledged; duplicate delivery ignored; bad secret
              refused; unmapped room and oversell reported
  Edge cases  transient fetch failure then redelivery; two-room booking with
              different room types; modification; cancellation; inventory
              counters after a channel booking; partner attribution
  Isolation   tenant B cannot claim tenant A's channel property id; tenant B's
              bookings land in tenant B

    python e2e/channel_integration_check.py --a a.json --b b.json
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import urllib.error
import urllib.request
import uuid
from datetime import date, timedelta

GATEWAY = os.environ.get("GATEWAY", "http://localhost:8000")
FAKE = os.environ.get("FAKE_CHANNEX", "http://localhost:9100")
SECRET = os.environ.get("CHANNEX_WEBHOOK_SECRET", "whsec-demo-1234567890")
results: list[dict] = []


def http(method, url, body=None, headers=None):
    req = urllib.request.Request(url, method=method)
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, data, timeout=60) as r:
            raw = r.read()
            return r.status, (json.loads(raw) if raw else None)
    except urllib.error.HTTPError as e:
        raw = e.read()
        try:
            return e.code, json.loads(raw)
        except ValueError:
            return e.code, raw.decode(errors="ignore")[:300]


def api(method, path, token, body=None):
    return http(method, GATEWAY + "/api" + path, body,
                {"Authorization": f"Bearer {token}"})


def sql(q):
    return subprocess.run(["psql", "-h", "/tmp", "-U", "pms", "-d", "chirala_pms",
                           "-tAF", "|", "-c", q], capture_output=True,
                          text=True).stdout.strip()


def check(area, name, ok, detail="", severity=None):
    results.append({"area": area, "name": name, "ok": bool(ok),
                    "detail": str(detail)[:400], "severity": severity})
    print(f"{'PASS' if ok else 'FAIL'}  [{area}] {name}  {detail}")


def login(creds):
    s, b = http("POST", GATEWAY + "/api/iam/auth/login", creds)
    if s != 200:
        sys.exit(f"login failed: {s} {b}")
    m = b["memberships"][0]
    return {"token": b["token"], "org": m["organization_id"],
            "prop": m["property_ids"][0]}


def fake_state():
    return http("GET", FAKE + "/_fake/state")[1]


def webhook(revision_id, event="booking_new", secret=SECRET):
    headers = {"X-Channex-Webhook-Secret": secret} if secret is not None else {}
    return http("POST", GATEWAY + "/api/booking/channels/channex/webhook",
                {"event": event, "payload": {"revision_id": revision_id}},
                headers)


def revision(ext_property, rooms, *, status="new", booking_id=None,
             code=None, customer=None):
    body = {"status": status, "property_id": ext_property,
            "booking_id": booking_id or str(uuid.uuid4()),
            "ota_reservation_code": code or f"AG{uuid.uuid4().hex[:8].upper()}",
            "ota_name": "Agoda", "currency": "INR",
            "arrival_date": rooms[0]["checkin_date"],
            "departure_date": rooms[0]["checkout_date"],
            "rooms": rooms,
            "customer": customer or {"name": "Emma", "surname": "Clarke",
                                     "mail": f"emma.{uuid.uuid4().hex[:6]}@example.com",
                                     "phone": "+44 7700 900123"}}
    return http("POST", FAKE + "/_fake/revisions", body)[1]["id"], body


def room(ext_room, arrive, nights, amount, adults=2):
    return {"room_type_id": ext_room, "checkin_date": arrive.isoformat(),
            "checkout_date": (arrive + timedelta(days=nights)).isoformat(),
            "occupancy": {"adults": adults, "children": 0}, "amount": str(amount)}


def event_row(rid):
    return sql("SELECT outcome, coalesce(detail,''), coalesce(reservation_id::text,''), "
               f"acknowledged FROM distribution.channel_booking_events WHERE revision_id='{rid}'")


def setup_partner(t, name, hotel_id, commission):
    s, attr = api("POST", "/booking/booking-attributes", t["token"],
                  {"organization_id": t["org"], "kind": "business_source",
                   "name": name, "code": None, "status": "active"})
    if s == 409:  # already in the registry from an earlier run
        _, rows = api("GET", f"/booking/booking-attributes?organization_id={t['org']}"
                      "&kind=business_source", t["token"])
        attr = next(r for r in rows["rows"] if r["name"] == name)
    elif s not in (200, 201):
        return s, attr, None
    api("PUT", f"/booking/channel-partners/{attr['id']}/type", t["token"],
        {"partner_type": "online_channel"})
    s2, conn = api("POST", "/booking/channel-connections", t["token"],
                   {"partner_id": attr["id"], "property_id": t["prop"],
                    "commission_percent": commission, "payment_model": "channel_collect",
                    "ota_hotel_id": hotel_id})
    if s2 == 409:  # connected on an earlier run
        cid = sql(f"SELECT id FROM distribution.channel_connections WHERE partner_id='{attr['id']}' "
                  f"AND property_id='{t['prop']}'")
        s2, conn = api("GET", f"/booking/channel-connections/{cid}", t["token"])
    return s2, conn, attr


def rate_plans_per_room(t):
    """One plan per room type -- a channel rate plan belongs to one room."""
    made = []
    for line in sql(f"SELECT id, name FROM property.room_types WHERE property_id='{t['prop']}' "
                    "AND status='active' ORDER BY name").splitlines():
        rt_id, rt_name = line.split("|")
        code = ("BAR-" + "".join(w[0] for w in rt_name.split()))[:12].upper()
        s, b = api("POST", f"/booking/rate-plans?property_id={t['prop']}", t["token"],
                   {"code": code, "name": f"Best Available — {rt_name}",
                    "room_type_ids": [rt_id], "max_guests": 3})
        made.append((code, s))
    return made


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--a", required=True)
    ap.add_argument("--b", required=True)
    ap.add_argument("--out", default="e2e/out/channel_results.json")
    args = ap.parse_args()
    http("POST", FAKE + "/_fake/reset")
    A = login(json.load(open(args.a)))
    B = login(json.load(open(args.b)))

    # ---------------------------------------------------------- partners --
    s, conn, partner = setup_partner(A, "Agoda", "96019126", 18)
    check("Partners", "Add Agoda as an OTA partner with commission 18% and hotel id",
          s in (200, 201) and conn.get("ota_hotel_id") == "96019126",
          f"HTTP {s}")
    s, lst = api("GET", f"/booking/channel-partners?organization_id={A['org']}", A["token"])
    rows = (lst.get("items") or lst.get("partners") or lst.get("rows") or []) if isinstance(lst, dict) else []
    names = [p["name"] for p in rows]
    check("Partners", "Channel Partners list shows Agoda", "Agoda" in names, names)

    # --------------------------------------------------------- provision --
    made = rate_plans_per_room(A)
    check("Setup", "One rate plan per room type (needed for OTA pricing)",
          all(code in (200, 201, 409) for _, code in made), made)
    s, prov = api("POST", f"/booking/channel-links/provision?property_id={A['prop']}", A["token"])
    st = fake_state()
    props = list(st.get("properties", {}).values())
    rts = list(st.get("room_types", {}).values())
    rps = list(st.get("rate_plans", {}).values())
    hooks = list(st.get("webhooks", {}).values())
    chans = list(st.get("channels", {}).values())
    check("Provision", "Property created at the channel manager",
          s == 200 and len(props) == 1 and props[0].get("title") == "Chirala Bay Resort",
          f"HTTP {s} status={prov.get('status') if isinstance(prov, dict) else prov}")
    check("Provision", "Both room types mirrored with room counts",
          sorted((r["title"], r.get("count_of_rooms")) for r in rts)
          == [("Deluxe Sea View", 6), ("Premium Suite", 3)],
          [(r["title"], r.get("count_of_rooms")) for r in rts])
    check("Provision", "A rate plan created per room type", len(rps) >= 2,
          [(r.get("title"), (r.get("options") or [{}])[0].get("rate") if r.get("options") else r.get("rate")) for r in rps])
    check("Provision", "Booking webhook registered with the secret header and send_data",
          len(hooks) == 1 and hooks[0].get("send_data") is True
          and (hooks[0].get("headers") or {}).get("X-Channex-Webhook-Secret") == SECRET,
          hooks[0].get("callback_url") if hooks else "none")
    check("Provision", "Agoda OTA channel created, switched off",
          len(chans) == 1 and chans[0].get("is_active") in (None, False),
          [(c.get("channel"), (c.get("settings") or {}).get("hotel_id")) for c in chans])
    if isinstance(prov, dict) and prov.get("problems"):
        # "no longer had this property ... rebuilt" is the account-swap
        # recovery path, expected whenever the fake has been reset.
        real = [p for p in prov["problems"] if "rebuilt" not in p]
        check("Provision", "No problems reported", not real, prov["problems"])
    before = {k: len(v) for k, v in fake_state().items() if k != "pushes"}
    api("POST", f"/booking/channel-links/provision?property_id={A['prop']}", A["token"])
    after = {k: len(v) for k, v in fake_state().items() if k != "pushes"}
    check("Provision", "Re-running provisioning creates nothing new (idempotent)",
          before == after, f"{before} -> {after}")

    link = sql(f"SELECT id, external_property_id FROM distribution.channel_manager_links "
               f"WHERE property_id='{A['prop']}'").split("|")
    link_id, ext_prop = link[0], link[1]
    maps = dict(r.split("|")[::-1] for r in sql(
        "SELECT rt.name, m.external_id FROM distribution.channel_room_mappings m "
        "JOIN property.room_types rt ON rt.id=m.room_type_id "
        f"WHERE m.link_id='{link_id}'").splitlines())
    ext_deluxe = next(k for k, v in maps.items() if v == "Deluxe Sea View")
    ext_suite = next(k for k, v in maps.items() if v == "Premium Suite")

    # --------------------------------------------------------------- push --
    s, push = api("POST", f"/booking/channel-links/{link_id}/push", A["token"])
    pushes = fake_state().get("pushes", {})
    avail = pushes.get("availability", {}).get("values", [])
    rest = pushes.get("restrictions", {}).get("values", [])
    arrive = date.today() + timedelta(days=30)
    def avail_on(ext_room, d, values):
        for v in values:
            if v.get("room_type_id") == ext_room and v["date_from"] <= d.isoformat() <= v["date_to"]:
                return v.get("availability")
    rates = [v for v in rest if "rate" in v]
    def sellable(name, d):
        v = sql("SELECT physical_capacity - out_of_service - held_units - reserved_units "
                "- allotment_units FROM booking.room_type_inventory_days i JOIN property.room_types rt "
                f"ON rt.id=i.room_type_id WHERE rt.name='{name}' AND i.property_id='{A['prop']}' "
                f"AND i.stay_date='{d.isoformat()}'")
        return int(v) if v else (6 if name.startswith("Deluxe") else 3)
    check("Push", "Availability pushed for both room types matches PMS sellable inventory",
          s == 200 and avail_on(ext_deluxe, arrive, avail) == sellable("Deluxe Sea View", arrive)
          and avail_on(ext_suite, arrive, avail) == sellable("Premium Suite", arrive),
          f"HTTP {s}; deluxe={avail_on(ext_deluxe, arrive, avail)}/{sellable('Deluxe Sea View', arrive)} "
          f"suite={avail_on(ext_suite, arrive, avail)}/{sellable('Premium Suite', arrive)} ({len(avail)} ranges)")
    check("Push", "Rates pushed and match the PMS base rates (5500 / 9500)",
          any(str(v.get("rate")).startswith("5500") for v in rates)
          and any(str(v.get("rate")).startswith("9500") for v in rates),
          sorted({str(v.get('rate')) for v in rates})[:6])

    # ----------------------------------------------------------- inbound --
    s, b = webhook("anything", secret="wrong-secret")
    check("Inbound", "Webhook with a wrong secret is refused", s == 401, f"HTTP {s}")
    s, b = webhook("anything", secret=None)
    check("Inbound", "Webhook with no secret is refused", s == 401, f"HTTP {s}")
    s, b = http("POST", GATEWAY + "/api/booking/channels/channex/webhook",
                {"event": "test"}, {"X-Channex-Webhook-Secret": SECRET})
    check("Inbound", "Channex 'test' event is answered OK", s == 200 and b.get("status") == "ok", b)

    inv_sql = ("SELECT held_units || '|' || reserved_units FROM booking.room_type_inventory_days d "
               "JOIN property.room_types rt ON rt.id=d.room_type_id "
               f"WHERE rt.name='Deluxe Sea View' AND d.property_id='{A['prop']}' "
               f"AND d.stay_date='{arrive.isoformat()}'")
    inv_before = [int(x) for x in (sql(inv_sql) or "0|0").split("|")]
    rid, rev = revision(ext_prop, [room(ext_deluxe, arrive, 2, 11000)])
    s, b = webhook(rid)
    ev = event_row(rid).split("|")
    res_id = ev[2] if len(ev) > 2 else ""
    check("Inbound", "New OTA booking becomes a reservation",
          s == 200 and b.get("status") == "created", b)
    if res_id:
        r = sql("SELECT r.status, r.source, r.reference, u.nightly_rate, rt.name, u.arrival_date, "
                "u.departure_date, g.full_name FROM booking.reservations r "
                "JOIN booking.reservation_units u ON u.reservation_id=r.id "
                "JOIN property.room_types rt ON rt.id=u.room_type_id "
                "LEFT JOIN engagement.guests g ON g.id=r.primary_guest_id "
                f"WHERE r.id='{res_id}'").split("|")
        check("Inbound", "Reservation has the right room type, dates, nightly rate, guest and source",
              r[0] == "confirmed" and r[1] == "ota" and r[4] == "Deluxe Sea View"
              and r[5] == arrive.isoformat() and r[3].startswith("5500") and r[7] == "Emma Clarke",
              r)
        check("Inbound", "Booking acknowledged to the channel manager after it was saved",
              ev[3] == "t" and fake_state()["revisions"][rid].get("acknowledged") is True, f"acknowledged={ev[3]}")
        inv = [int(x) for x in sql(inv_sql).split("|")]
        d_held, d_res = inv[0] - inv_before[0], inv[1] - inv_before[1]
        check("Inbound", "Inventory counts the channel booking as reserved (not left as a hold)",
              (d_held, d_res) == (0, 1), f"change: held_units {d_held:+d}, reserved_units {d_res:+d}",
              severity="High")
        bs = sql(f"SELECT coalesce(business_source_id::text,'') FROM booking.reservations WHERE id='{res_id}'")
        check("Inbound", "Channel booking is attributed to the Agoda partner (for counts and commission)",
              bs == (partner or {}).get("id"), f"business_source_id='{bs}'", severity="Medium")

    s, b = webhook(rid)
    n = sql(f"SELECT count(*) FROM booking.reservations WHERE reference='{rev['ota_reservation_code']}'")
    check("Inbound", "Same webhook delivered twice does not create a second booking",
          b.get("status") == "duplicate" and n == "1", f"{b.get('status')}, reservations={n}")

    rid_u, _ = revision(ext_prop, [room("not-a-mapped-room", arrive, 1, 5000)])
    s, b = webhook(rid_u)
    check("Inbound", "Booking for an unmapped room is reported, not dropped silently",
          b.get("status") == "unmapped" and event_row(rid_u).startswith("unmapped"), b)

    far = date.today() + timedelta(days=60)
    rid_o, _ = revision(ext_prop, [room(ext_suite, far, 1, 9500) for _ in range(4)])
    s, b = webhook(rid_o)
    check("Inbound", "Oversell (4 suites, 3 exist) is refused and reported",
          b.get("status") == "no_inventory", b)

    # -------------------------------------------------------- edge cases --
    http("POST", FAKE + "/_fake/fail_fetch", {"times": 1})
    rid_f, rev_f = revision(ext_prop, [room(ext_deluxe, arrive + timedelta(days=3), 1, 5500)])
    s1, b1 = webhook(rid_f)
    s2, b2 = webhook(rid_f)   # Channex redelivers
    n = sql(f"SELECT count(*) FROM booking.reservations WHERE reference='{rev_f['ota_reservation_code']}'")
    check("Edge cases", "Booking survives a temporary fetch failure (redelivery is processed)",
          n == "1", f"first={b1.get('status')} (HTTP {s1}), redelivery={b2.get('status')}, reservations={n}",
          severity="Critical")

    # A date nothing else in this or an earlier run has booked, so the check
    # measures the booking logic rather than leftover test data.
    fresh = date.today() + timedelta(days=120 + (uuid.uuid4().int % 250))
    rid_m, rev_m = revision(ext_prop, [room(ext_deluxe, fresh, 2, 11000),
                                       room(ext_suite, fresh, 2, 19000)])
    s, b = webhook(rid_m)
    types = sql("SELECT string_agg(rt.name, ',' ORDER BY rt.name) FROM booking.reservation_units u "
                "JOIN booking.reservations r ON r.id=u.reservation_id "
                "JOIN property.room_types rt ON rt.id=u.room_type_id "
                f"WHERE r.reference='{rev_m['ota_reservation_code']}'")
    check("Edge cases", "Two-room booking (Deluxe + Suite) books one of each",
          types == "Deluxe Sea View,Premium Suite", f"{b.get('status')}: booked [{types}]", severity="High")

    rid_n, rev_n = revision(ext_prop, [room(ext_deluxe, arrive + timedelta(days=10), 2, 11000)])
    webhook(rid_n)
    new_arrive = arrive + timedelta(days=12)
    rid_mod, _ = revision(ext_prop, [room(ext_deluxe, new_arrive, 3, 16500)], status="modified",
                          booking_id=rev_n["booking_id"], code=rev_n["ota_reservation_code"],
                          customer=rev_n["customer"])
    s, b = webhook(rid_mod, event="booking_modification")
    dates = sql("SELECT u.arrival_date || '..' || u.departure_date FROM booking.reservation_units u "
                "JOIN booking.reservations r ON r.id=u.reservation_id "
                f"WHERE r.reference='{rev_n['ota_reservation_code']}'")
    check("Edge cases", "OTA modification (new dates) updates the reservation",
          dates.startswith(new_arrive.isoformat()), f"{b.get('status')}: dates now {dates}", severity="High")

    rid_c, _ = revision(ext_prop, [room(ext_deluxe, arrive, 2, 11000)], status="cancelled",
                        booking_id=rev["booking_id"], code=rev["ota_reservation_code"],
                        customer=rev["customer"])
    s, b = webhook(rid_c, event="booking_cancellation")
    st_ = sql(f"SELECT status FROM booking.reservations WHERE reference='{rev['ota_reservation_code']}'")
    check("Edge cases", "OTA cancellation cancels the reservation and frees the room",
          st_ == "cancelled", f"{b.get('status')}: reservation status now '{st_}'", severity="High")

    # The desk cancels an OTA booking by hand (what it must do today, since
    # OTA cancellations are not applied). Does the room come back on sale?
    rid_d, rev_d = revision(ext_prop, [room(ext_suite, far + timedelta(days=5), 1, 9500)])
    webhook(rid_d)
    d_res = sql(f"SELECT id FROM booking.reservations WHERE reference='{rev_d['ota_reservation_code']}'")
    day = (far + timedelta(days=5)).isoformat()
    inv_q = ("SELECT held_units || '/' || reserved_units FROM booking.room_type_inventory_days i "
             "JOIN property.room_types rt ON rt.id=i.room_type_id WHERE rt.name='Premium Suite' "
             f"AND i.property_id='{A['prop']}' AND i.stay_date='{day}'")
    before_c = sql(inv_q)
    s, b = api("POST", f"/booking/reservations/{d_res}/cancel?property_id={A['prop']}", A["token"],
               {"reason": "guest_request", "notes": "OTA cancelled by phone", "waive_penalty": True})
    after_c = sql(inv_q)
    pre = sql(inv_q.replace("held_units || '/' || reserved_units", "0"))  # row exists
    booked = [int(x) for x in before_c.split("/")]
    freed = [int(x) for x in after_c.split("/")]
    check("Edge cases", "Desk cancelling an OTA booking puts the room back on sale",
          sum(booked) - sum(freed) == 1,
          f"cancel HTTP {s}; held/reserved with booking={before_c} after cancel={after_c}",
          severity="High")

    # --------------------------------------------------------- isolation --
    s, b = api("PUT", "/booking/channel-links", B["token"],
               {"property_id": B["prop"], "external_property_id": ext_prop, "currency": "INR"})
    owner = sql(f"SELECT string_agg(property_id::text, ',') FROM distribution.channel_manager_links "
                f"WHERE external_property_id='{ext_prop}'")
    check("Isolation", "Tenant B cannot claim tenant A's channel property id",
          s >= 400 and owner == A["prop"], f"HTTP {s}; linked to: {owner}", severity="Critical")

    setup_partner(B, "Booking.com", "BDC-7788", 15)
    rate_plans_per_room(B)
    s, prov_b = api("POST", f"/booking/channel-links/provision?property_id={B['prop']}", B["token"])
    ext_b = sql(f"SELECT external_property_id FROM distribution.channel_manager_links WHERE property_id='{B['prop']}'")
    ext_b_room = sql("SELECT m.external_id FROM distribution.channel_room_mappings m "
                     "JOIN distribution.channel_manager_links l ON l.id=m.link_id "
                     f"WHERE l.property_id='{B['prop']}' LIMIT 1")
    groups = {p.get("group_id") for p in fake_state().get("properties", {}).values()}
    check("Isolation", "Each tenant gets its own group and property at the channel manager",
          ext_b and ext_b != ext_prop and len(groups) == 2, f"B property={ext_b}, groups={len(groups)}")
    if ext_b and ext_b_room:
        rid_b, rev_b = revision(ext_b, [room(ext_b_room, arrive, 1, 4200)])
        webhook(rid_b)
        where = sql("SELECT r.property_id FROM booking.reservations r "
                    f"WHERE r.reference='{rev_b['ota_reservation_code']}'")
        check("Isolation", "Tenant B's OTA booking lands in tenant B only",
              where == B["prop"], f"landed in {where or 'nowhere'}")

    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    json.dump(results, open(args.out, "w"), indent=2)
    failed = [r for r in results if not r["ok"]]
    print(f"\n{len(results) - len(failed)}/{len(results)} channel checks passed")


if __name__ == "__main__":
    main()
