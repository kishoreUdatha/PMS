"""Tenant isolation check against a RUNNING stack, from the outside in.

Two real tenants sign in through the gateway exactly as the browser does.
Tenant B then tries to reach tenant A's data every way a curious or hostile
user could:

  1. List screens with A's property id in the query string.
  2. A's records by id (reservation, guest, room, invoice, user), asked for
     under B's own property -- the "guess an id" attack.
  3. A write into A's property.
  4. No credentials at all.
  5. The database itself, connected as the application's runtime role with
     row-level security bound to B's organisation.
  6. A token minted offline for A's user -- tokens are unsigned today, so
     this one is EXPECTED to get through and is reported as a known risk.

Every check prints PASS/FAIL, and the whole run is written as JSON (and an
HTML page the demo recording opens).

    python e2e/tenant_isolation_check.py --a a.json --b b.json \
        --out e2e/out/isolation.json --html e2e/out/isolation.html

where a.json / b.json hold {"email", "password", "property_code"}.
"""
from __future__ import annotations

import argparse
import base64
import html
import json
import os
import sys
import urllib.error
import urllib.request

GATEWAY = os.environ.get("GATEWAY", "http://localhost:8000")
APP_DB = os.environ.get(
    "APP_DB_DSN",
    "postgresql://pms_app:pms_app_dev_password@localhost:5432/chirala_pms")


def call(method: str, path: str, token: str | None = None,
         body: dict | None = None) -> tuple[int, object]:
    req = urllib.request.Request(GATEWAY + "/api" + path, method=method)
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, data, timeout=20) as r:
            raw = r.read()
            return r.status, (json.loads(raw) if raw else None)
    except urllib.error.HTTPError as e:
        raw = e.read()
        try:
            return e.code, json.loads(raw)
        except ValueError:
            return e.code, raw.decode(errors="ignore")[:200]


def login(creds: dict) -> dict:
    status, body = call("POST", "/iam/auth/login", body={
        "property_code": creds["property_code"], "email": creds["email"],
        "password": creds["password"]})
    if status != 200:
        sys.exit(f"login failed for {creds['email']}: {status} {body}")
    m = body["memberships"][0]
    return {"token": body["token"], "subject": body["subject"],
            "org": m["organization_id"], "prop": m["property_ids"][0],
            "email": creds["email"]}


def first_id(rows, *keys):
    if isinstance(rows, dict):
        for k in ("items", "rows", "results", "data", "guests", "reservations"):
            if isinstance(rows.get(k), list):
                rows = rows[k]
                break
    if not isinstance(rows, list) or not rows:
        return None
    for k in keys:
        if rows[0].get(k):
            return rows[0][k]
    return None


def harvest(t: dict) -> dict:
    """Ids of a few of this tenant's own records, read as that tenant."""
    p = t["prop"]
    ids = {}
    _, r = call("GET", f"/booking/reservations?property_id={p}", t["token"])
    ids["reservation"] = first_id(r, "id")
    _, g = call("GET", f"/booking/guests?organization_id={t['org']}"
                        f"&property_id={p}", t["token"])
    ids["guest"] = first_id(g, "id")
    _, rooms = call("GET", f"/booking/room-rack?property_id={p}", t["token"])
    if isinstance(rooms, dict):
        for k in ("rooms", "rows"):
            if rooms.get(k):
                ids["room"] = rooms[k][0].get("id") or rooms[k][0].get("room_id")
    _, inv = call("GET", f"/finance/invoices?property_id={p}", t["token"])
    ids["invoice"] = first_id(inv, "id")
    _, users = call("GET", f"/iam/users?property_id={p}", t["token"])
    ids["user"] = first_id(users, "id", "user_id")
    return ids


LISTS = [
    ("Reservations list", "/booking/reservations"),
    ("Guest directory", "/booking/guests?organization_id={org}"),
    ("Room rack", "/booking/room-rack"),
    ("Housekeeping board", "/booking/housekeeping/board"),
    ("Rate plans / room types", "/booking/room-types"),
    ("Form C register", "/booking/form-c/register"),
    ("Dashboard", "/dashboard"),
    ("Cashiering transactions", "/finance/cashiering/transactions"),
    ("Day book", "/finance/daybook"),
    ("Invoices", "/finance/invoices"),
    ("Expense vouchers", "/finance/expenses"),
    ("Night audit preview", "/finance/night-audit/preview"),
    ("Users", "/iam/users"),
    ("Audit log", "/iam/audit/events"),
    ("Approvals queue", "/iam/approvals/queue"),
]


def run(a: dict, b: dict) -> list[dict]:
    results: list[dict] = []

    def check(group, name, ok, detail, expected="refused"):
        ok = None if expected == "skipped" else bool(ok)
        results.append({"group": group, "name": name, "ok": ok,
                        "detail": detail, "expected": expected})
        tag = "SKIP" if ok is None else ("PASS" if ok else "FAIL")
        print(f"{tag}  [{group}] {name} -> {detail}")

    a_ids = harvest(a)
    b_ids = harvest(b)

    # 0. Sanity: each tenant can read its own data, or the refusals below
    # would prove nothing.
    for label, t in (("A", a), ("B", b)):
        s, _ = call("GET", f"/booking/reservations?property_id={t['prop']}",
                    t["token"])
        check("Baseline", f"Tenant {label} reads its own reservations",
              s == 200, f"HTTP {s}", expected="allowed")

    # 1. B asks for A's property on every list screen.
    for name, path in LISTS:
        url = path.format(org=a["org"])
        url += ("&" if "?" in url else "?") + f"property_id={a['prop']}"
        s, body = call("GET", url, b["token"])
        check("Cross-property list", name, s in (401, 403, 404),
              f"HTTP {s}")

    # 2. A's records by id, asked for under B's own property and under A's.
    objects = [
        ("Reservation detail", "reservation",
         "/booking/reservations/{id}/detail"),
        ("Guest profile", "guest", "/booking/guests/{id}?organization_id=%s"),
        ("Room", "room", "/booking/rooms/{id}"),
        ("Invoice", "invoice", "/finance/invoices/{id}"),
        ("User access", "user", "/iam/users/{id}/access"),
    ]
    for name, kind, tmpl in objects:
        rid = a_ids.get(kind)
        if not rid:
            check("Direct object (IDOR)", f"{name} of tenant A", False,
                  "no record of this kind in tenant A to test with",
                  expected="skipped")
            continue
        for prop_label, prop in (("B's property", b["prop"]),
                                 ("A's property", a["prop"])):
            org = b["org"] if prop == b["prop"] else a["org"]
            url = tmpl.format(id=rid).replace("%s", org)
            url += ("&" if "?" in url else "?") + f"property_id={prop}"
            s, body = call("GET", url, b["token"])
            leaked = s == 200
            check("Direct object (IDOR)",
                  f"{name} of tenant A, asked under {prop_label}",
                  not leaked, f"HTTP {s}")

    # 3. A write into A's property.
    s, _ = call("POST", f"/booking/guests?property_id={a['prop']}",
                b["token"], {"first_name": "Intruder", "last_name": "Test",
                             "phone": "9000000000"})
    check("Cross-tenant write", "Create a guest in tenant A's property",
          s in (401, 403, 404, 422), f"HTTP {s}")

    # 4. No credentials.
    s, _ = call("GET", f"/booking/reservations?property_id={a['prop']}")
    check("Authentication", "Anonymous read of tenant A reservations",
          s == 401, f"HTTP {s}")

    # 5. The database: runtime role, RLS bound to B's organisation.
    try:
        import psycopg
        with psycopg.connect(APP_DB) as conn:
            for table in ("booking.reservations", "booking.reservation_units",
                          "property.rooms", "finance.folios",
                          "finance.folio_entries", "finance.payments",
                          "finance.invoices", "iam.memberships"):
                def count_a_rows(bound_org):
                    with conn.transaction():
                        conn.execute(
                            "SELECT set_config('app.organization_id', %s, true),"
                            " set_config('app.system', 'off', true)",
                            (bound_org,))
                        return conn.execute(
                            f"SELECT count(*) FROM {table} "
                            "WHERE organization_id = %s",
                            (a["org"],)).fetchone()[0]
                # Control: A's own session sees A's rows, so a zero below
                # means the policy hid them, not that there were none.
                own, seen = count_a_rows(a["org"]), count_a_rows(b["org"])
                check("Database row-level security",
                      f"{table}: tenant A rows visible to tenant B's session",
                      own > 0 and seen == 0,
                      f"{seen} of {own} rows" if own else
                      "tenant A has no rows here to hide (inconclusive)")
    except Exception as exc:  # noqa: BLE001
        check("Database row-level security", "connect as runtime role",
              False, f"could not run: {exc}")

    # 6. Known risk: unsigned tokens.
    forged = base64.urlsafe_b64encode(f"dev:{a['subject']}".encode()).decode()
    s, _ = call("GET", f"/booking/reservations?property_id={a['prop']}", forged)
    check("Known risk: token signing",
          "Token minted offline for tenant A's user is rejected",
          s in (401, 403), f"HTTP {s}"
          + (" - ACCEPTED: tokens are unsigned base64" if s == 200 else ""))

    return results


def to_html(results: list[dict], a: dict, b: dict) -> str:
    rows = []
    group = None
    for r in results:
        if r["group"] != group:
            group = r["group"]
            rows.append(f'<tr class="g"><td colspan="3">{html.escape(group)}</td></tr>')
        badge = ("skip", "SKIP") if r["ok"] is None else (
            ("pass", "PASS") if r["ok"] else ("fail", "FAIL"))
        rows.append(
            f'<tr><td><span class="b {badge[0]}">{badge[1]}</span></td>'
            f'<td>{html.escape(r["name"])}</td>'
            f'<td class="d">{html.escape(r["detail"])}</td></tr>')
    passed = sum(1 for r in results if r["ok"] is True)
    failed = sum(1 for r in results if r["ok"] is False)
    return f"""<!doctype html><html><head><meta charset="utf-8">
<title>Tenant isolation results</title><style>
body{{font:15px system-ui,sans-serif;margin:0;background:#f4f7f8;color:#123}}
header{{background:#0b4f57;color:#fff;padding:22px 32px}}
h1{{margin:0;font-size:24px}} .sub{{opacity:.8;margin-top:4px}}
.sum{{display:flex;gap:16px;padding:18px 32px}}
.k{{background:#fff;border-radius:12px;padding:12px 18px;box-shadow:0 1px 3px #0002}}
.k b{{font-size:26px;display:block}}
table{{margin:0 32px 40px;border-collapse:collapse;width:calc(100% - 64px);background:#fff;border-radius:12px;overflow:hidden}}
td{{padding:8px 12px;border-bottom:1px solid #e6ecee}} tr.g td{{background:#e9f3f4;font-weight:600}}
.d{{color:#556;font-family:ui-monospace,monospace;font-size:13px}}
.b{{display:inline-block;min-width:44px;text-align:center;border-radius:6px;padding:2px 6px;font-weight:700;font-size:12px;color:#fff}}
.pass{{background:#159a6b}} .fail{{background:#c8343b}} .skip{{background:#999}}
</style></head><body><header><h1>Tenant isolation: API and database checks</h1>
<div class="sub">Tenant A: {html.escape(a['email'])} &nbsp;·&nbsp; Tenant B (attacker): {html.escape(b['email'])}</div></header>
<div class="sum"><div class="k"><b>{passed}</b>passed</div><div class="k"><b>{failed}</b>failed</div>
<div class="k"><b>{len(results)}</b>checks</div></div>
<table>{''.join(rows)}</table></body></html>"""


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--a", required=True)
    ap.add_argument("--b", required=True)
    ap.add_argument("--out", default="e2e/out/isolation.json")
    ap.add_argument("--html", default="e2e/out/isolation.html")
    args = ap.parse_args()
    a = login(json.load(open(args.a)))
    b = login(json.load(open(args.b)))
    results = run(a, b)
    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    json.dump({"tenant_a": a["email"], "tenant_b": b["email"],
               "results": results}, open(args.out, "w"), indent=2)
    open(args.html, "w").write(to_html(results, a, b))
    failed = [r for r in results if r["ok"] is False]
    print(f"\n{len(results) - len(failed)}/{len(results)} checks passed")


if __name__ == "__main__":
    main()
