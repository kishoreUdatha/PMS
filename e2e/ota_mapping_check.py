"""OTA room/rate mapping and go-live, per tenant, against fake_channex.

The hotel pairs the OTA's own rooms and rates with its rate plans and switches
the channel on from the PMS. Real Channex will only list an OTA's rooms for a
hotel that has authorised it in the OTA's extranet, which a test cannot do, so
this runs against ``e2e/fake_channex.py`` -- which also, like the real one,
accepts any rate plan on any channel. That is exactly what the checks lean on:

  Before     no OTA rooms until the hotel authorises; the reason is said
  Mapping    OTA rooms listed; pairs saved to the channel with this property's
             Channex rate plan ids; unknown OTA codes and duplicates refused
  Go live    refused with nothing paired; activates and pauses the channel
  Isolation  tenant B cannot read, map or switch A's channel; B cannot pair
             A's rate plan; a pair naming another property's plan is reported
             and blocks going live; a connection pointing at another tenant's
             channel is refused outright

Needs a stack pointed at the fake, on its own database:

    uvicorn e2e.fake_channex:app --port 9100
    # services with CHANNEX_API_URL=http://localhost:9100/api/v1 CHANNEX_API_KEY=fake-key
    python e2e/ota_mapping_check.py --a a.json --b b.json

``--psql-db`` names that database; one check points A's connection at B's
channel directly in it, which no API allows, to prove the route refuses it.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import urllib.error
import urllib.request

GATEWAY = os.environ.get("GATEWAY", "http://localhost:9000") + "/api"
FAKE = os.environ.get("FAKE_CHANNEX", "http://localhost:9100")
results: list[tuple[str, bool, str]] = []


def _req(url, method="GET", body=None, headers=None):
    req = urllib.request.Request(
        url, method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"content-type": "application/json", **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            raw = r.read()
            return r.status, json.loads(raw) if raw else None
    except urllib.error.HTTPError as e:
        raw = e.read()
        try:
            return e.code, json.loads(raw)
        except ValueError:
            return e.code, raw.decode(errors="ignore")[:300]


def fake(method, path, body=None):
    return _req(FAKE + path, method, body, {"user-api-key": "fake-key"})


def check(name, ok, detail=""):
    results.append((name, bool(ok), str(detail)[:300]))
    print(("PASS " if ok else "FAIL ") + name + (f"  -- {detail}" if not ok else ""))


class Tenant:
    def __init__(self, path, hotel_id, room, plan):
        creds = json.load(open(path))
        s, login = _req(GATEWAY + "/iam/auth/login", "POST", creds)
        assert s == 200, login
        self.tok = login["token"]
        self.org = login["memberships"][0]["organization_id"]
        self.pid = login["memberships"][0]["property_ids"][0]
        self.hotel_id, self.room, self.plan = hotel_id, room, plan

    def api(self, method, path, body=None):
        return _req(GATEWAY + path, method, body, {"authorization": "Bearer " + self.tok})

    def setup(self):
        """One room type, one rate plan, Booking.com with this hotel's id."""
        _, rts = self.api("GET", f"/booking/room-types?property_id={self.pid}")
        rt = next((r["id"] for r in rts or [] if r["name"] == self.room), None)
        if rt is None:
            s, made = self.api("POST", f"/booking/room-types/manage?property_id={self.pid}", {
                "code": self.room[:6].upper(), "name": self.room, "max_adults": 2,
                "max_occupancy": 2, "base_rate": "100"})
            assert s == 201, made
            rt = made["id"]
            self.api("POST", f"/booking/rooms/bulk-create?property_id={self.pid}",
                     {"room_type_id": rt, "spec": "1-2"})
        _, plans = self.api("GET", f"/booking/rate-plans?property_id={self.pid}")
        rows = plans.get("items", []) if isinstance(plans, dict) else plans
        self.plan_id = next((p["id"] for p in rows if p["code"] == self.plan), None)
        if self.plan_id is None:
            s, made = self.api("POST", f"/booking/rate-plans?property_id={self.pid}", {
                "code": self.plan, "name": "Best Available Rate", "room_type_ids": [rt],
                "adjustment_direction": "increase", "adjustment_type": "amount",
                "adjustment_value": "0"})
            assert s == 201, made
            self.plan_id = made["id"]
        _, partners = self.api("GET", f"/booking/channel-partners?organization_id={self.org}")
        bdc = next((r for r in partners.get("rows", [])
                    if r["name"].lower() == "booking.com"), None)
        if bdc is None:
            s, bdc = self.api("POST", "/booking/booking-attributes", {
                "organization_id": self.org, "kind": "business_source",
                "name": "Booking.com", "code": None, "status": "active"})
            assert s == 201, bdc
        self.api("PUT", f"/booking/channel-partners/{bdc['id']}/type",
                 {"partner_type": "online_channel"})
        _, conns = self.api("GET", f"/booking/channel-connections?property_id={self.pid}")
        conn = next(iter(conns or []), None)
        if conn is None:
            s, conn = self.api("POST", "/booking/channel-connections", {
                "partner_id": bdc["id"], "property_id": self.pid,
                "ota_hotel_id": self.hotel_id})
            assert s == 201, conn
        self.conn = conn["id"]
        s, prov = self.api("POST", f"/booking/channel-links/provision?property_id={self.pid}")
        assert s == 200, prov
        _, conns = self.api("GET", f"/booking/channel-connections?property_id={self.pid}")
        self.channel = conns[0]["external_channel_id"]
        _, link = self.api("GET", f"/booking/channel-links?property_id={self.pid}")
        self.plan_ext = next(r["external_id"] for r in link["rates"]
                             if r["local_id"] == self.plan_id)

    def mapping(self, path_conn=None):
        return self.api("GET", f"/booking/channel-connections/{path_conn or self.conn}/ota-mapping")

    def save(self, pairs, path_conn=None):
        return self.api("PUT", f"/booking/channel-connections/{path_conn or self.conn}/ota-mapping",
                        {"pairs": pairs})

    def live(self, on, path_conn=None):
        return self.api("POST", f"/booking/channel-connections/{path_conn or self.conn}/go-live",
                        {"live": on})


def channel(cid):
    _, out = fake("GET", f"/api/v1/channels/{cid}")
    return (out or {}).get("data", {}).get("attributes", {})


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--a", required=True)
    ap.add_argument("--b", required=True)
    ap.add_argument("--psql-db", default="chirala_pms_fake")
    args = ap.parse_args()
    sys.stdout.reconfigure(line_buffering=True)

    a = Tenant(args.a, "5550001", "Sea View Room", "SV-BAR")
    b = Tenant(args.b, "5550002", "Garden Room", "GD-BAR")
    a.setup()
    b.setup()
    # A clean start on rerun: not authorised, nothing paired, switched off.
    fake("POST", "/_fake/ota_rooms", {a.hotel_id: None, b.hotel_id: None})
    for t in (a, b):
        fake("PUT", f"/api/v1/channels/{t.channel}", {"channel": {"rate_plans": []}})
        fake("POST", f"/api/v1/channels/{t.channel}/deactivate")
    check("Each tenant's Booking.com channel was built, switched off",
          a.channel and b.channel and a.channel != b.channel
          and not channel(a.channel).get("is_active"), (a.channel, b.channel))

    # ---- before the hotel authorises ----
    s, m = a.mapping()
    check("Before authorisation: no OTA rooms, and the reason is given",
          s == 200 and m["ota_rooms"] == [] and "authoris" in (m["ota_rooms_error"] or ""),
          f"HTTP {s}: {m}")
    check("Only this property's own rate plans are offered",
          s == 200 and [p["rate_plan_id"] for p in m["plans"]] == [a.plan_id],
          m.get("plans") if isinstance(m, dict) else m)
    s, m = a.live(True)
    check("Going live with nothing paired is refused", s == 409, f"HTTP {s}: {m}")

    # ---- the hotel authorises; the OTA's rooms appear ----
    fake("POST", "/_fake/ota_rooms", {
        a.hotel_id: [{"id": 111, "title": "Sea View Double",
                      "rates": [{"id": 901, "title": "Standard", "max_persons": 2},
                                {"id": 902, "title": "Non-refundable", "max_persons": 2}]}],
        b.hotel_id: [{"id": 222, "title": "Garden Twin",
                      "rates": [{"id": 801, "title": "Standard", "max_persons": 2}]}]})
    s, m = a.mapping()
    codes = [(r["code"], [x["code"] for x in r["rates"]]) for r in m.get("ota_rooms", [])]
    check("After authorisation the OTA's rooms and rates are listed",
          s == 200 and codes == [("111", ["901", "902"])] and not m["ota_rooms_error"], codes)

    s, m = a.save([{"ota_room_code": "111", "ota_rate_code": "999", "rate_plan_id": a.plan_id}])
    check("A rate the OTA does not have is refused", s == 422, f"HTTP {s}: {m}")
    dup = {"ota_room_code": "111", "ota_rate_code": "901", "rate_plan_id": a.plan_id}
    s, m = a.save([dup, dup])
    check("The same OTA rate paired twice is refused", s == 422, f"HTTP {s}: {m}")

    s, m = a.save([{"ota_room_code": "111", "ota_rate_code": "901", "rate_plan_id": a.plan_id}])
    rps = channel(a.channel).get("rate_plans") or []
    check("Pairs are saved to the channel with this property's Channex rate plan",
          s == 200 and len(rps) == 1 and rps[0]["rate_plan_id"] == a.plan_ext
          and rps[0]["settings"]["room_type_code"] == 111
          and rps[0]["settings"]["rate_plan_code"] == 901, rps)
    check("The saved pairs read back", s == 200 and len(m["pairs"]) == 1
          and m["pairs"][0]["rate_plan_id"] == a.plan_id, m)

    # ---- isolation ----
    s, m = b.mapping(a.conn)
    check("B cannot read A's OTA mapping", s == 404, f"HTTP {s}")
    s, m = b.save([{"ota_room_code": "111", "ota_rate_code": "901",
                    "rate_plan_id": b.plan_id}], a.conn)
    check("B cannot write A's OTA mapping", s == 404, f"HTTP {s}")
    s, m = b.live(True, a.conn)
    check("B cannot switch A's channel on", s == 404, f"HTTP {s}")
    s, m = b.save([{"ota_room_code": "222", "ota_rate_code": "801",
                    "rate_plan_id": a.plan_id}])
    check("B cannot pair A's rate plan on B's own channel", s == 422, f"HTTP {s}: {m}")
    check("A's channel is untouched by B's attempts",
          [r["rate_plan_id"] for r in channel(a.channel).get("rate_plans") or []]
          == [a.plan_ext], channel(a.channel).get("rate_plans"))

    # A pair naming another property's plan, put there behind the PMS's back
    # (the channel manager accepts it -- checked on staging).
    fake("PUT", f"/api/v1/channels/{a.channel}", {"channel": {"rate_plans": [
        {"rate_plan_id": a.plan_ext, "settings": {"room_type_code": 111, "rate_plan_code": 901}},
        {"rate_plan_id": b.plan_ext, "settings": {"room_type_code": 111, "rate_plan_code": 902}}]}})
    s, m = a.mapping()
    check("A pair naming another property's plan is reported, not shown",
          s == 200 and m["foreign_pairs"] == 1 and len(m["pairs"]) == 1
          and b.plan_id not in str(m), m)
    s, m = a.live(True)
    check("Going live is refused while such a pair is on the channel", s == 409,
          f"HTTP {s}: {m}")
    s, m = a.save([{"ota_room_code": "111", "ota_rate_code": "901", "rate_plan_id": a.plan_id}])
    check("Saving again removes it",
          s == 200 and m["foreign_pairs"] == 0
          and [r["rate_plan_id"] for r in channel(a.channel)["rate_plans"]] == [a.plan_ext], m)

    # ---- go live ----
    s, m = a.live(True)
    check("A goes live: the channel is activated", s == 200 and m["live"]
          and channel(a.channel).get("is_active") is True, f"HTTP {s}: {m}")
    check("B's channel stays off", channel(b.channel).get("is_active") is False)
    s, m = a.live(False)
    check("A pauses: the channel is deactivated", s == 200 and not m["live"]
          and channel(a.channel).get("is_active") is False, f"HTTP {s}")

    # ---- a connection pointing at another tenant's channel ----
    # Not B's own channel id: the database already refuses one channel id on
    # two connections. A channel on B's property that no connection holds --
    # made at the channel manager directly -- is what the route itself must
    # refuse.
    _, bch = fake("GET", f"/api/v1/channels/{b.channel}")
    _, made = fake("POST", "/api/v1/channels", {"channel": {
        "channel": "BookingCom", "title": "stray",
        "group_id": bch["data"]["relationships"]["group"]["data"]["id"],
        "properties": bch["data"]["attributes"]["properties"],
        "settings": {"hotel_id": "5550099"}}})
    stray = made["data"]["id"]
    subprocess.run(["psql", "-h", "/tmp", "-U", "pms", "-d", args.psql_db, "-qc",
                    f"UPDATE distribution.channel_connections SET external_channel_id = "
                    f"'{stray}' WHERE id = '{a.conn}'"], check=True)
    try:
        s, m = a.mapping()
        check("A connection pointing at another tenant's channel is refused",
              s == 409, f"HTTP {s}: {m}")
        s, m = a.live(True)
        check("... and cannot switch that channel on",
              s == 409 and channel(stray).get("is_active") is False, f"HTTP {s}")
    finally:
        subprocess.run(["psql", "-h", "/tmp", "-U", "pms", "-d", args.psql_db, "-qc",
                        f"UPDATE distribution.channel_connections SET external_channel_id = "
                        f"'{a.channel}' WHERE id = '{a.conn}'"], check=True)

    passed = sum(ok for _, ok, _ in results)
    print(f"\n{passed}/{len(results)} passed")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
