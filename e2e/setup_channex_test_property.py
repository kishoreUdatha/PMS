"""Create Channex's certification test property in the PMS (PMS API only).

Channex's "Setup Testing Property" asks for:

    Test Property - (Provider Name), USD
    Twin Room (occupancy 2)   - Best Available Rate 100, Bed & Breakfast Rate 120
    Double Room (occupancy 2) - Best Available Rate 100, Bed & Breakfast Rate 120

This builds exactly that inside the PMS -- property, room types, rooms (10
Twin, 5 Double, enough to reach the availability tests' values with blocks)
and the four rate plans. It does NOT talk to Channex: the PMS's own
provisioning does that when the channel partner is added, and every
certification test is then done by hand in the PMS screens.

    python e2e/setup_channex_test_property.py --creds e2e/out/creds_a.json

Local dev only: a property created through the API gives nobody access to
it until someone is assigned there (Staff screen). ``--grant-local`` copies
the logged-in user's roles from their first property via psql, which is
what an administrator would do on the Staff screen.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import urllib.error
import urllib.request

GATEWAY = os.environ.get("GATEWAY", "http://localhost:8000") + "/api"
NAME = "Test Property - Chirala PMS"
ROOMS = [("TWIN", "Twin Room", "101-110", "TW"), ("DBL", "Double Room", "201-205", "DB")]
PLANS = [("BAR", "Best Available Rate", "0"), ("BB", "Bed & Breakfast Rate", "20")]


def call(method, path, tok=None, body=None):
    req = urllib.request.Request(
        GATEWAY + path, method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"content-type": "application/json",
                 **({"authorization": "Bearer " + tok} if tok else {})})
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, json.loads(r.read() or b"null")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()[:400]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--creds", required=True)
    ap.add_argument("--grant-local", action="store_true")
    args = ap.parse_args()
    with open(args.creds) as f:
        creds = json.load(f)
    _, login = call("POST", "/iam/auth/login", body=creds)
    tok = login["token"]
    first = login["memberships"][0]["property_ids"][0]

    _, props = call("GET", "/iam/properties", tok)
    prop = next((p for p in props if p["name"] == NAME), None)
    if prop is None:
        s, prop = call("POST", "/iam/properties", tok,
                       {"name": NAME, "currency": "USD", "timezone": "Asia/Kolkata"})
        print("property", s)
    pid = prop["id"]
    print(f"{NAME}: {pid} ({prop['currency']})")

    if args.grant_local:
        subprocess.run(
            ["psql", "-h", "/tmp", "-U", "pms", "-d", "chirala_pms", "-qc",
             "INSERT INTO iam.role_assignments (membership_id, role_id, property_id, scope_type) "
             "SELECT ra.membership_id, ra.role_id, %s, ra.scope_type FROM iam.role_assignments ra "
             "JOIN iam.memberships m ON m.id = ra.membership_id "
             "JOIN iam.users u ON u.id = m.user_id "
             "WHERE u.email = %s AND ra.property_id = %s AND NOT EXISTS ("
             "SELECT 1 FROM iam.role_assignments x WHERE x.membership_id = ra.membership_id "
             "AND x.property_id = %s)" % (f"'{pid}'", f"'{creds['email']}'", f"'{first}'", f"'{pid}'")],
            check=True)

    _, existing = call("GET", f"/booking/room-types?property_id={pid}", tok)
    have = {r["name"]: r["id"] for r in (existing if isinstance(existing, list) else [])}
    for code, name, spec, prefix in ROOMS:
        rid = have.get(name)
        if rid is None:
            s, rt = call("POST", f"/booking/room-types/manage?property_id={pid}", tok,
                         {"code": code, "name": name, "max_adults": 2,
                          "max_occupancy": 2, "base_rate": "100"})
            if s != 201:
                raise SystemExit(f"{name}: {s} {rt} (no access? try --grant-local)")
            rid = rt["id"]
            s, out = call("POST", f"/booking/rooms/bulk-create?property_id={pid}", tok,
                          {"room_type_id": rid, "spec": spec})
            print(f"{name}: {out.get('created') if isinstance(out, dict) else out} rooms")
        for suffix, plan, extra in PLANS:
            s, out = call("POST", f"/booking/rate-plans?property_id={pid}", tok, {
                "code": f"{prefix}-{suffix}", "name": plan, "room_type_ids": [rid],
                "adjustment_direction": "increase", "adjustment_type": "amount",
                "adjustment_value": extra})
            print(f"  {plan}: {'created' if s == 201 else s}")
    print("Next: Distribution -> Channel Partners -> Add partner (with CHANNEX_API_KEY set).")


if __name__ == "__main__":
    main()
