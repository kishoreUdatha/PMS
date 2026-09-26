"""Tell the channel manager what changed -- and only that.

The push this replaces sent a year of every rate, availability and stay rule
for every property every minute. The channel manager allows ten availability
and ten rate/restriction requests a minute per property, and expects a price
changed on one night to arrive as one small update for that night. So:

1. **Compute** what this property would publish for the next
   ``channel_sync_days`` nights, per room (availability) and per rate plan
   (rate and each stay rule), with the same arithmetic the old push used --
   ``channel_push`` still owns that.
2. **Compare** it, night by night and field by field, with what the channel
   manager last *accepted* (``distribution.channel_ari_state``).
3. **Send** only the difference, folded into date ranges, as at most one
   ``/availability`` and one ``/restrictions`` request per pass (more only when
   a batch overflows).
4. **Remember** what was accepted, and log every request with its task ids
   (``distribution.channel_sync_log``).

A refused, failed or throttled request writes no state, so the next pass finds
the same difference and offers it again: retries come from the design, not
from a queue that can lose entries. Forgetting a link's state is a full sync,
which is how go-live, recovery and the Full sync button all work.

Detecting change by comparison rather than by hooking every write is
deliberate. Availability moves when a booking is taken, a hold expires, a room
is blocked, a group block is released; rates move from the calendar, the rate
plan screen, occupancy rules. Every one of those paths would need a hook, and
the one that was missed would be the one that oversold a room.
"""

from __future__ import annotations

import json
import logging
from datetime import date, timedelta

import httpx
from chirala_common.db import bind_tenant_context, system_context
from sqlalchemy import text
from sqlalchemy.orm import Session

from . import channel_push as calc
from .settings import settings

log = logging.getLogger("uvicorn.error").getChild("channel-sync")

#: Stay-rule fields, in the order the channel manager documents them.
RESTRICTION_FIELDS = ("min_stay_arrival", "min_stay_through", "max_stay",
                      "closed_to_arrival", "closed_to_departure", "stop_sell")
BOOL_FIELDS = {"closed_to_arrival", "closed_to_departure", "stop_sell"}
INT_FIELDS = {"min_stay_arrival", "min_stay_through", "max_stay",
              "availability"}


def _client() -> httpx.Client:
    return httpx.Client(
        base_url=settings.channex_api_url.rstrip("/"),
        headers={"user-api-key": settings.channex_api_key,
                 "Content-Type": "application/json"},
        timeout=60.0,
    )


# ------------------------------------------------------------ computing --
def _days(v: dict):
    d = date.fromisoformat(v["date_from"])
    end = date.fromisoformat(v["date_to"])
    while d <= end:
        yield d
        d += timedelta(days=1)


def _enc(v) -> str:
    """One stored representation per value, so '1' and 1 never differ."""
    if isinstance(v, bool):
        return "true" if v else "false"
    return str(v)


def current_values(db: Session, conn, start: date, end: date):
    """{(field, external_id, night): value} this property would publish now.

    Returns the values and anything that could not be priced.
    """
    out: dict[tuple, str] = {}
    problems: list[str] = []
    if conn["send_availability"]:
        for v in calc.availability_values(db, conn, start, end):
            for d in _days(v):
                out[("availability", v["room_type_id"], d)] = _enc(v["availability"])
    if conn["send_rates"]:
        rates, skipped = calc.rate_values(db, conn, start, end)
        problems.extend(skipped)
        for v in rates:
            for d in _days(v):
                out[("rate", v["rate_plan_id"], d)] = _enc(v["rate"])
    if conn["send_restrictions"]:
        for v in calc.restriction_values(db, conn, start, end):
            for d in _days(v):
                for f in RESTRICTION_FIELDS:
                    out[(f, v["rate_plan_id"], d)] = _enc(v[f])
    return out, problems


def _stored(db: Session, link_id, start: date, end: date) -> dict[tuple, str]:
    rows = db.execute(
        text("SELECT field, external_id, stay_date, value "
             "FROM distribution.channel_ari_state "
             "WHERE link_id = :l AND stay_date BETWEEN :s AND :e"),
        {"l": link_id, "s": start, "e": end},
    ).all()
    return {(r[0], r[1], r[2]): r[3] for r in rows}


def _dec(field: str, value: str):
    if field in BOOL_FIELDS:
        return value == "true"
    if field in INT_FIELDS:
        return int(value)
    return value


# -------------------------------------------------------------- payloads --
def build_requests(conn, changed: dict[tuple, str]):
    """Fold per-night changes into the two requests the channel manager takes.

    Returns ``{endpoint: [(value, [state keys it carries]), ...]}``. A range
    ends wherever the set of changed fields or any value changes, so a range
    never claims a night whose numbers differ from its neighbours'.
    """
    ext_prop = conn["external_property_id"]
    avail: dict[str, dict[date, str]] = {}
    plans: dict[str, dict[date, dict]] = {}
    for (field, ext, day), val in changed.items():
        if field == "availability":
            avail.setdefault(ext, {})[day] = val
        else:
            plans.setdefault(ext, {}).setdefault(day, {})[field] = val

    def ranges(per_day: dict[date, object]):
        run = None
        for day in sorted(per_day):
            val = per_day[day]
            if run and run["val"] == val and day == run["to"] + timedelta(days=1):
                run["to"] = day
                continue
            if run:
                yield run
            run = {"from": day, "to": day, "val": val}
        if run:
            yield run

    requests: dict[str, list] = {"/availability": [], "/restrictions": []}
    for ext, per_day in sorted(avail.items()):
        for r in ranges(per_day):
            keys = [("availability", ext, r["from"] + timedelta(days=i))
                    for i in range((r["to"] - r["from"]).days + 1)]
            requests["/availability"].append((
                {"property_id": ext_prop, "room_type_id": ext,
                 "date_from": r["from"].isoformat(),
                 "date_to": r["to"].isoformat(),
                 "availability": _dec("availability", r["val"])},
                [(k, r["val"]) for k in keys]))
    for ext, per_day in sorted(plans.items()):
        frozen = {d: tuple(sorted(v.items())) for d, v in per_day.items()}
        for r in ranges(frozen):
            fields = dict(r["val"])
            value = {"property_id": ext_prop, "rate_plan_id": ext,
                     "date_from": r["from"].isoformat(),
                     "date_to": r["to"].isoformat()}
            for f, v in fields.items():
                value[f] = _dec(f, v)
            keys = []
            for i in range((r["to"] - r["from"]).days + 1):
                day = r["from"] + timedelta(days=i)
                keys.extend(((f, ext, day), v) for f, v in fields.items())
            requests["/restrictions"].append((value, keys))
    return requests


# --------------------------------------------------------------- sending --
def _task_ids(body) -> list[str]:
    data = (body or {}).get("data") if isinstance(body, dict) else None
    items = data if isinstance(data, list) else ([data] if data else [])
    return [str(i["id"]) for i in items if isinstance(i, dict) and i.get("id")]


def _requests_last_minute(db: Session, link_id, endpoint: str) -> int:
    return db.execute(
        text("SELECT count(*) FROM distribution.channel_sync_log "
             "WHERE link_id = :l AND endpoint = :e AND outcome = 'sent' "
             "AND created_at > now() - interval '60 seconds'"),
        {"l": link_id, "e": endpoint},
    ).scalar() or 0


def _log(db, conn, endpoint, trigger, values, *, status_code=None,
         outcome, task_ids=(), error=None, summary=None) -> None:
    froms = [v["date_from"] for v in values]
    tos = [v["date_to"] for v in values]
    db.execute(
        text(
            """
            INSERT INTO distribution.channel_sync_log
                (link_id, organization_id, property_id, endpoint, trigger,
                 value_count, date_from, date_to, status_code, outcome,
                 task_ids, summary, error, request_excerpt)
            VALUES (:l, :o, :p, :e, :t, :n, :df, :dt, :sc, :oc, :ti, :su,
                    :er, :rq)
            """
        ),
        {"l": conn["id"], "o": conn["organization_id"],
         "p": conn["property_id"], "e": endpoint, "t": trigger,
         "n": len(values), "df": min(froms) if froms else None,
         "dt": max(tos) if tos else None, "sc": status_code, "oc": outcome,
         "ti": list(task_ids), "su": (summary or "")[:400] or None,
         "er": (error or "")[:600] or None,
         "rq": json.dumps({"values": values})[:4000]},
    )


def _remember(db, conn, keys: list[tuple]) -> None:
    """Record what the channel manager accepted, in one statement."""
    if not keys:
        return
    db.execute(
        text(
            """
            INSERT INTO distribution.channel_ari_state
                (link_id, organization_id, field, external_id, stay_date,
                 value)
            SELECT :l, :o, f, e, d, v
            FROM unnest(CAST(:f AS text[]), CAST(:e AS text[]),
                        CAST(:d AS date[]), CAST(:v AS text[])) AS t(f, e, d, v)
            ON CONFLICT (link_id, field, external_id, stay_date)
            DO UPDATE SET value = EXCLUDED.value, sent_at = now()
            """
        ),
        {"l": conn["id"], "o": conn["organization_id"],
         "f": [k[0][0] for k in keys], "e": [k[0][1] for k in keys],
         "d": [k[0][2] for k in keys], "v": [k[1] for k in keys]},
    )


def _describe(endpoint: str, values: list[dict]) -> str:
    kinds = sorted({f for v in values for f in v
                    if f not in ("property_id", "room_type_id", "rate_plan_id",
                                 "date_from", "date_to")})
    first = values[0]
    span = (first["date_from"] if len(values) == 1
            and first["date_from"] == first["date_to"]
            else f"{min(v['date_from'] for v in values)}..{max(v['date_to'] for v in values)}")
    return f"{len(values)} range(s), {', '.join(kinds)}, {span}"


def _dirty_scope(db: Session, conn, rows):
    """What the outbox rows name, as channel ids and date ranges.

    Returns (first night, last night, predicate over state keys). An
    availability row names a room type; a rates row names a rate plan, or a
    room type (every plan priced from it), or neither (every plan -- a rule
    for all plans).
    """
    rooms = dict(db.execute(
        text("SELECT room_type_id::text, external_id FROM "
             "distribution.channel_room_mappings WHERE link_id = :l"),
        {"l": conn["id"]}).all())
    plans = db.execute(
        text("SELECT m.rate_plan_id::text, m.external_id, "
             "min(x.room_type_id::text) AS room_type_id "
             "FROM distribution.channel_rate_mappings m "
             "LEFT JOIN property.rate_plan_room_types x "
             "ON x.rate_plan_id = m.rate_plan_id "
             "WHERE m.link_id = :l GROUP BY 1, 2"),
        {"l": conn["id"]}).all()
    avail: dict[str, list] = {}
    rates: dict[str, list] = {}
    lo = hi = None
    for r in rows:
        span = (r["date_from"], r["date_to"])
        lo = span[0] if lo is None else min(lo, span[0])
        hi = span[1] if hi is None else max(hi, span[1])
        if r["scope"] == "availability":
            ext = rooms.get(str(r["room_type_id"]))
            if ext:
                avail.setdefault(ext, []).append(span)
            continue
        for plan_id, ext, room in plans:
            if (r["rate_plan_id"] is not None and str(r["rate_plan_id"]) != plan_id):
                continue
            if (r["rate_plan_id"] is None and r["room_type_id"] is not None
                    and str(r["room_type_id"]) != room):
                continue
            rates.setdefault(ext, []).append(span)

    def wanted(key) -> bool:
        field, ext, day = key
        spans = (avail if field == "availability" else rates).get(ext, ())
        return any(a <= day <= b for a, b in spans)
    return lo, hi, wanted


def sync(db: Session, link_id, *, full: bool = False,
         trigger: str | None = None, dirty=None) -> dict:
    """Send one property's changes (or everything, if ``full``).

    ``dirty`` is the outbox rows being drained: only the rooms, plans and
    nights they name are computed and compared. Returns, per endpoint, what
    became of it -- 'ok', 'failed', 'throttled' or 'idle' -- so the drainer
    knows which rows to clear and which to retry.
    """
    trigger = trigger or ("full_sync" if full else "change")
    conn = db.execute(
        text("SELECT l.id, l.organization_id, l.property_id, "
             "l.external_property_id, l.send_availability, l.send_rates, "
             "l.send_restrictions, l.sync_paused_until, "
             "(now() AT TIME ZONE COALESCE(p.timezone, 'UTC'))::date AS today "
             "FROM distribution.channel_manager_links l "
             "JOIN iam.properties p ON p.id = l.property_id WHERE l.id = :i"),
        {"i": link_id},
    ).mappings().first()
    if conn is None:
        return {"status": "failed", "detail": "No such channel manager link."}
    if not conn["external_property_id"]:
        return _status(db, link_id, "failed",
                       "No channel manager property id on this property, so "
                       "there is nowhere to send.")
    if not settings.channex_api_key:
        return _status(db, link_id, "failed", "No CHANNEX_API_KEY configured.")
    paused = db.execute(
        text("SELECT sync_paused_until > now() FROM "
             "distribution.channel_manager_links WHERE id = :i"),
        {"i": link_id}).scalar()
    if paused and not full:
        return {"status": "deferred",
                "detail": "Paused: the channel manager asked us to slow down."}

    # The property's own today, not the server's: a hotel in India is a day
    # ahead of a UTC server for five and a half hours every night.
    start = conn["today"]
    end = start + timedelta(days=settings.channel_sync_days - 1)
    wanted = None
    if dirty is not None and not full:
        if not db.execute(text("SELECT 1 FROM distribution.channel_ari_state "
                               "WHERE link_id = :l LIMIT 1"),
                          {"l": link_id}).first():
            # Nothing accepted yet for this property -- a new link, or one
            # rebuilt after the channel manager lost it. Everything goes, and
            # it is recorded as the full sync it is.
            full, trigger = True, "full_sync"
        else:
            lo, hi, wanted = _dirty_scope(db, conn, dirty)
            if lo is None:
                return {"status": "ok", "detail": "Nothing to send.",
                        "endpoints": {}}
            start, end = max(start, lo), min(end, hi)
            if start > end:
                return {"status": "ok", "detail": "Outside the window.",
                        "endpoints": {}}
    now_values, problems = current_values(db, conn, start, end)
    if wanted is not None:
        now_values = {k: v for k, v in now_values.items() if wanted(k)}
    before = {} if full else _stored(db, link_id, start, end)
    changed = {k: v for k, v in now_values.items() if before.get(k) != v}

    if not changed:
        return {"status": "ok", "detail": "Nothing changed.", "sent": 0,
                "endpoints": {}}

    requests = build_requests(conn, changed)
    sent = 0
    failures: list[str] = []
    outcome: dict[str, str] = {e: ("idle" if not items else "ok")
                               for e, items in requests.items()}
    limit = settings.channex_requests_per_minute
    with _client() as client:
        for endpoint, items in requests.items():
            for i in range(0, len(items), calc.BATCH):
                chunk = items[i:i + calc.BATCH]
                values = [c[0] for c in chunk]
                if _requests_last_minute(db, link_id, endpoint) >= limit:
                    # Not an error and not logged per pass: the difference is
                    # still there and goes out when the minute allows.
                    failures.append(f"{endpoint}: waiting for the rate limit")
                    outcome[endpoint] = "throttled"
                    break
                try:
                    resp = client.post(endpoint, json={"values": values})
                except httpx.HTTPError as exc:
                    _log(db, conn, endpoint, trigger, values, outcome="failed",
                         error=f"{type(exc).__name__}: {exc}")
                    failures.append(f"{endpoint}: {exc}")
                    outcome[endpoint] = "failed"
                    break
                body = None
                try:
                    body = resp.json()
                except ValueError:
                    pass
                if resp.status_code == 429:
                    db.execute(
                        text("UPDATE distribution.channel_manager_links SET "
                             "sync_paused_until = now() + interval '60 seconds' "
                             "WHERE id = :i"), {"i": link_id})
                    _log(db, conn, endpoint, trigger, values,
                         status_code=429, outcome="throttled",
                         error="Rate limited by the channel manager; pausing "
                               "for a minute.")
                    failures.append(f"{endpoint}: rate limited (429)")
                    outcome[endpoint] = "throttled"
                    break
                if resp.status_code >= 400:
                    _log(db, conn, endpoint, trigger, values,
                         status_code=resp.status_code, outcome="failed",
                         error=resp.text[:600])
                    failures.append(f"{endpoint}: {resp.status_code}")
                    outcome[endpoint] = "failed"
                    break
                ids = _task_ids(body)
                _log(db, conn, endpoint, trigger, values,
                     status_code=resp.status_code, outcome="sent",
                     task_ids=ids, summary=_describe(endpoint, values))
                _remember(db, conn, [k for c in chunk for k in c[1]])
                sent += len(values)

    if failures:
        return _status(db, link_id, "failed" if not sent else "partial",
                       f"Sent {sent} range(s); not sent: " + "; ".join(failures),
                       endpoints=outcome)
    detail = (f"{'Full sync' if full else 'Update'}: sent {sent} range(s) "
              f"for {start}..{end}.")
    if problems:
        detail += " Not priced: " + " ".join(problems)
    return _status(db, link_id, "partial" if problems else "ok", detail,
                   sent=sent, endpoints=outcome)


def _status(db, link_id, status: str, detail: str, **extra) -> dict:
    db.execute(
        text("UPDATE distribution.channel_manager_links "
             "SET last_pushed_at = now(), last_push_status = :s, "
             "last_push_detail = :d WHERE id = :i"),
        {"s": status, "d": detail[:1000], "i": link_id},
    )
    if status not in ("ok", "deferred"):
        log.warning("channel sync %s: %s", status, detail)
    return {"status": status, "detail": detail, **extra}


#: Which endpoint carries each outbox scope.
ENDPOINT = {"availability": "/availability", "rates": "/restrictions"}


def _backoff_seconds(attempts: int) -> int:
    """5s, 10s, 20s ... capped at five minutes."""
    return min(300, 5 * 2 ** max(0, attempts))


def drain_outbox(db_factory) -> list[dict]:
    """Send what the outbox says changed, property by property.

    A property's changes wait until it has been quiet for
    ``channel_sync_quiet_seconds`` (or until the oldest has waited
    ``channel_sync_max_wait_seconds``), so a person entering three prices in a
    row produces one request, not three. Rows are removed only once the
    channel manager accepts what they describe; a refused or failed request
    leaves them in place with a backoff, and a 429 pauses the property for a
    minute. Each property is its own transaction.
    """
    quiet = settings.channel_sync_quiet_seconds
    max_wait = settings.channel_sync_max_wait_seconds
    with db_factory() as session:
        system_context(session, reason="channel sync: properties with changes")
        due = session.execute(
            text(
                """
                SELECT property_id, organization_id
                FROM distribution.ari_outbox
                WHERE next_attempt_at <= now()
                GROUP BY property_id, organization_id
                HAVING max(created_at) < now() - make_interval(secs => :q)
                    OR min(created_at) < now() - make_interval(secs => :w)
                """
            ),
            {"q": quiet, "w": max_wait},
        ).mappings().all()

    results = []
    for p in due:
        with db_factory() as db:
            try:
                bind_tenant_context(db, organization_id=p["organization_id"],
                                    property_id=p["property_id"],
                                    is_service=True)
                rows = db.execute(
                    text(
                        """
                        SELECT id, scope, room_type_id, rate_plan_id,
                               date_from, date_to, attempts
                        FROM distribution.ari_outbox
                        WHERE property_id = :p AND next_attempt_at <= now()
                        ORDER BY id
                        FOR UPDATE SKIP LOCKED
                        """
                    ),
                    {"p": p["property_id"]},
                ).mappings().all()
                if not rows:
                    db.rollback()
                    continue
                link = db.execute(
                    text("SELECT id FROM distribution.channel_manager_links "
                         "WHERE property_id = :p AND external_property_id "
                         "IS NOT NULL"),
                    {"p": p["property_id"]},
                ).scalar()
                if link is None:
                    db.execute(text("DELETE FROM distribution.ari_outbox "
                                    "WHERE id = ANY(:ids)"),
                               {"ids": [r["id"] for r in rows]})
                    db.commit()
                    continue
                res = sync(db, link, dirty=rows)
                endpoints = res.get("endpoints", {})
                for scope, endpoint in ENDPOINT.items():
                    ids = [r["id"] for r in rows if r["scope"] == scope]
                    if not ids:
                        continue
                    state = endpoints.get(endpoint) or (
                        "throttled" if res["status"] == "deferred"
                        else "ok" if res["status"] in ("ok", "partial")
                        else "failed")
                    if state in ("ok", "idle"):
                        db.execute(text("DELETE FROM distribution.ari_outbox "
                                        "WHERE id = ANY(:ids)"), {"ids": ids})
                    else:
                        attempts = max(r["attempts"] for r in rows
                                       if r["scope"] == scope)
                        wait = 60 if state == "throttled" else \
                            _backoff_seconds(attempts)
                        db.execute(
                            text("UPDATE distribution.ari_outbox SET "
                                 "attempts = attempts + 1, next_attempt_at = "
                                 "now() + make_interval(secs => :w), "
                                 "last_error = :e WHERE id = ANY(:ids)"),
                            {"w": wait, "e": res["detail"][:400], "ids": ids})
                db.commit()
                results.append(res)
            except Exception:
                db.rollback()
                log.exception("channel sync failed for property %s",
                              p["property_id"])
                results.append({"status": "failed", "detail": "exception"})
    return results
