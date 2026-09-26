"""Channex certification-style ARI scenarios, against the running stack.

Each scenario performs the action a hotel would perform in the PMS (through the
same API the Rates & Inventory screen calls), waits for the sync to pick it up,
and checks the request that reached the channel manager (e2e/fake_channex.py):
how many requests, which endpoint, which dates, which fields, and the task id
recorded in the sync log.

    python e2e/channel_sync_check.py --creds e2e/out/creds_a.json
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(__file__))
from channel_integration_check import (FAKE, SECRET, api, http, login,  # noqa: E402
                                       revision, room, sql, webhook)

results: list[dict] = []
SYNC_WAIT = int(os.environ.get("SYNC_WAIT", "45"))


def check(name, ok, detail=""):
    results.append({"scenario": name, "ok": bool(ok), "detail": str(detail)[:600]})
    print(f"{'PASS' if ok else 'FAIL'}  {name}  {detail}")


def calls_since(n):
    return [c for c in http("GET", FAKE + "/_fake/calls")[1][n:]
            if c["method"] == "POST" and c["path"].endswith(("/availability", "/restrictions"))]


def ncalls():
    return len(http("GET", FAKE + "/_fake/calls")[1])


def wait_for_calls(since, want=1, timeout=SYNC_WAIT):
    """Wait for the sync to send, then a little longer so a batch completes."""
    end = time.time() + timeout
    while time.time() < end:
        got = calls_since(since)
        if len(got) >= want:
            time.sleep(3)
            return calls_since(since)
        time.sleep(1)
    return calls_since(since)


def values_of(call):
    return json.loads(call["body"])["values"]


def last_log(link, n=1):
    return sql("SELECT endpoint, trigger, outcome, value_count, array_to_string(task_ids, ',') "
               f"FROM distribution.channel_sync_log WHERE link_id='{link}' "
               f"ORDER BY created_at DESC LIMIT {n}").splitlines()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--creds", required=True)
    ap.add_argument("--out", default="e2e/out/channel_sync_results.json")
    args = ap.parse_args()
    T = login(json.load(open(args.creds)))
    tok, prop = T["token"], T["prop"]

    s, prov = api("POST", f"/booking/channel-links/provision?property_id={prop}", tok)
    link, ext_prop = sql("SELECT id, external_property_id FROM distribution.channel_manager_links "
                         f"WHERE property_id='{prop}'").split("|")
    rooms = dict(r.split("|") for r in sql(
        "SELECT rt.name, rt.id FROM property.room_types rt "
        f"WHERE rt.property_id='{prop}' AND rt.status='active'").splitlines())
    plan_ext = dict(r.split("|") for r in sql(
        "SELECT rp.name, m.external_id FROM distribution.channel_rate_mappings m "
        f"JOIN property.rate_plans rp ON rp.id=m.rate_plan_id WHERE m.link_id='{link}'").splitlines())
    room_ext = dict(r.split("|") for r in sql(
        "SELECT rt.name, m.external_id FROM distribution.channel_room_mappings m "
        f"JOIN property.room_types rt ON rt.id=m.room_type_id WHERE m.link_id='{link}'").splitlines())
    deluxe, suite = rooms["Deluxe Sea View"], rooms["Premium Suite"]
    dlx_plan = next(v for k, v in plan_ext.items() if "Deluxe" in k)
    ste_plan = next(v for k, v in plan_ext.items() if "Suite" in k)

    # 1. Full sync -------------------------------------------------------
    n0 = ncalls()
    s, b = api("POST", f"/booking/channel-links/{link}/push", tok)
    got = calls_since(n0)
    eps = sorted(c["path"].rsplit("/", 1)[1] for c in got)
    span_ok = all(min(v["date_from"] for v in values_of(c)) <= date.today().isoformat() and
                  max(v["date_to"] for v in values_of(c)) >= (date.today() + timedelta(days=498)).isoformat()
                  for c in got)
    log = last_log(link, 2)
    check("1. Full sync: 500 days of availability, rates and restrictions in 2 requests",
          s == 200 and eps == ["availability", "restrictions"] and span_ok
          and all(l.split("|")[4] for l in log) and all("|full_sync|sent|" in l for l in log),
          f"HTTP {s}; requests={eps}; 500-day span={span_ok}; log={log}")

    time.sleep(2)
    n0 = ncalls()
    time.sleep(SYNC_WAIT // 2)
    check("   No change -> no requests (nothing is resent on a timer)",
          not calls_since(n0), f"{len(calls_since(n0))} requests while idle")

    # A fresh date and fresh values every run: setting what is already set is
    # (correctly) not a change, and would send nothing.
    import random
    d1 = date.today() + timedelta(days=40 + random.randint(0, 300))
    bump = random.randint(1, 89)

    # 2. Single date, single rate ---------------------------------------
    n0 = ncalls()
    api("PUT", f"/booking/rate-calendar/cell?property_id={prop}", tok,
        {"room_type_id": deluxe, "stay_date": d1.isoformat(), "rate": 6300 + bump})
    got = wait_for_calls(n0)
    vals = [v for c in got for v in values_of(c)]
    check("2. One rate on one date -> one request, one value, rate only",
          len(got) == 1 and len(vals) == 1 and vals[0]["date_from"] == vals[0]["date_to"] == d1.isoformat()
          and vals[0].get("rate_plan_id") == dlx_plan and str(vals[0].get("rate")).startswith(str(6300 + bump))
          and set(vals[0]) == {"property_id", "rate_plan_id", "date_from", "date_to", "rate"},
          f"{len(got)} request(s): {vals}")

    # 3. Single dates, multiple rates (same batch window) -----------------
    n0 = ncalls()
    api("PUT", f"/booking/rate-calendar/cell?property_id={prop}", tok,
        {"room_type_id": deluxe, "stay_date": (d1 + timedelta(days=3)).isoformat(), "rate": 6400 + bump})
    api("PUT", f"/booking/rate-calendar/cell?property_id={prop}", tok,
        {"room_type_id": suite, "stay_date": (d1 + timedelta(days=7)).isoformat(), "rate": 9400 + bump + 0.21})
    got = wait_for_calls(n0)
    vals = [v for c in got for v in values_of(c)]
    check("3. Rates on two dates for two rate plans -> one request with both",
          len(got) == 1 and len(vals) == 2 and {v["rate_plan_id"] for v in vals} == {dlx_plan, ste_plan},
          f"{len(got)} request(s): {vals}")

    # 4. Date range, multiple rates -------------------------------------
    n0 = ncalls()
    for i in range(10):
        api("PUT", f"/booking/rate-calendar/cell?property_id={prop}", tok,
            {"room_type_id": deluxe, "stay_date": (d1 + timedelta(days=20 + i)).isoformat(), "rate": 5200 + bump})
    got = wait_for_calls(n0)
    vals = [v for c in got for v in values_of(c)]
    check("4. Same rate over 10 nights -> one range, not ten values",
          len(vals) == 1 and vals[0]["date_from"] == (d1 + timedelta(days=20)).isoformat()
          and vals[0]["date_to"] == (d1 + timedelta(days=29)).isoformat(),
          f"{len(got)} request(s): {vals}")

    # 5. Min stay ----------------------------------------------------------
    n0 = ncalls()
    api("PUT", f"/booking/rate-calendar/cell?property_id={prop}", tok,
        {"room_type_id": deluxe, "stay_date": (d1 + timedelta(days=45)).isoformat(), "min_stay": 3 + bump % 4})
    got = wait_for_calls(n0)
    vals = [v for c in got for v in values_of(c)]
    check("5. Minimum stay on one date -> restrictions request with min stay only",
          len(vals) == 1 and vals[0].get("min_stay_arrival") == 3 + bump % 4 and vals[0].get("min_stay_through") == 3 + bump % 4
          and "rate" not in vals[0] and "stop_sell" not in vals[0],
          f"{len(got)} request(s): {vals}")

    # 6. Stop sell ---------------------------------------------------------
    n0 = ncalls()
    api("PUT", f"/booking/rate-calendar/cell?property_id={prop}", tok,
        {"room_type_id": suite, "stay_date": (d1 + timedelta(days=46)).isoformat(), "stop_sell": True})
    got = wait_for_calls(n0)
    vals = [v for c in got for v in values_of(c)]
    check("6. Stop sell on one date -> restrictions request with stop_sell=true",
          len(vals) == 1 and vals[0].get("stop_sell") is True and vals[0]["rate_plan_id"] == ste_plan,
          f"{len(got)} request(s): {vals}")

    # 9/10. Availability: a booking takes a room ---------------------------
    n0 = ncalls()
    arrive = d1 + timedelta(days=50)
    rid, _ = revision(ext_prop, [room(room_ext["Deluxe Sea View"], arrive, 3, 16500)])
    webhook(rid)
    got = wait_for_calls(n0)
    av = [v for c in got if c["path"].endswith("/availability") for v in values_of(c)]
    check("9. A booking for 3 nights -> availability update for exactly those nights",
          len(av) == 1 and av[0]["date_from"] == arrive.isoformat()
          and av[0]["date_to"] == (arrive + timedelta(days=2)).isoformat(),
          f"{len(got)} request(s): {av}")

    # Rate limit ------------------------------------------------------------
    # The channel manager answers 429 above its limit; the fake is set to one
    # request a minute so the second update of the minute is refused.
    http("POST", FAKE + "/_fake/limit", {"limit": 1})
    n0 = ncalls()
    api("PUT", f"/booking/rate-calendar/cell?property_id={prop}", tok,
        {"room_type_id": deluxe, "stay_date": (d1 + timedelta(days=60)).isoformat(), "rate": 7000 + bump})
    wait_for_calls(n0)
    n1 = ncalls()
    api("PUT", f"/booking/rate-calendar/cell?property_id={prop}", tok,
        {"room_type_id": deluxe, "stay_date": (d1 + timedelta(days=61)).isoformat(), "rate": 7100 + bump})
    wait_for_calls(n1)
    throttled = sql(f"SELECT count(*) FROM distribution.channel_sync_log WHERE link_id='{link}' "
                    "AND outcome='throttled'")
    time.sleep(75)          # the pause the channel manager asked for, plus a pass
    delivered = sql("SELECT value FROM distribution.channel_ari_state "
                    f"WHERE link_id='{link}' AND field='rate' AND external_id='{dlx_plan}' "
                    f"AND stay_date='{(d1 + timedelta(days=61)).isoformat()}'")
    http("POST", FAKE + "/_fake/limit", {"limit": 10})
    check("Rate limit: a 429 pauses the property, and the refused update is delivered afterwards",
          int(throttled or 0) >= 1 and delivered.startswith(str(7100 + bump)),
          f"throttled requests logged={throttled}; night now accepted at={delivered or 'not yet'}")

    log = last_log(link, 1)
    check("Task id recorded for every accepted request",
          sql(f"SELECT count(*) FROM distribution.channel_sync_log WHERE link_id='{link}' "
              "AND outcome='sent' AND cardinality(task_ids)=0") == "0",
          f"latest: {log}")

    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    json.dump(results, open(args.out, "w"), indent=2)
    print(f"\n{sum(r['ok'] for r in results)}/{len(results)} sync scenarios passed")


if __name__ == "__main__":
    main()
