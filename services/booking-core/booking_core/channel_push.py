"""Sending rates and availability out to the channels.

The other half of the channel integration, and the half nobody watches. A
booking that fails to arrive is noticed within a day because a guest turns up.
A push that fails is silent: the OTA carries on selling yesterday's prices and
yesterday's availability, and the first symptom is an overbooking or a room
sold at last season's rate.

Two things go out, and they are not the same shape:

* **Availability** belongs to a room type. What is sellable is capacity less
  everything already taken — out of service, held, reserved, allotted — which
  is the same arithmetic the booking engine sells against. Anything else and
  the OTA is selling rooms this hotel does not have.

* **Rates** belong to a rate plan at the channel, and to a *room type* here:
  ``rate_calendar_days`` is keyed by room type, not by plan. So a rate plan
  can only be priced if it is tied to exactly one room type. A plan spanning
  three room types has three different nightly prices and no single answer to
  send — that is reported rather than guessed, because guessing publishes a
  suite at a standard double's price.

Nothing here is clever about *what* changed. It pushes a rolling window on a
schedule, which is more traffic than a change feed would be and is correct on
the first run, after a restart, and after any period where a change was missed.
A dirty-date tracker is the optimisation to make once this is proven, not
before: an ARI push that is occasionally wrong is worse than one that is
occasionally redundant.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from decimal import Decimal

import httpx
from chirala_common.db import bind_tenant_context, system_context
from sqlalchemy import text
from sqlalchemy.orm import Session

from .settings import settings

log = logging.getLogger("uvicorn.error").getChild("channel-push")

#: How far ahead to publish. An OTA sells a year out; a shorter window means a
#: guest searching next summer finds nothing and books elsewhere.
WINDOW_DAYS = 500

#: Channex asks for batches rather than a request per date, and a year of
#: dates across several room types is more than one request should carry.
BATCH = 400


class PushError(Exception):
    pass


def _client() -> httpx.Client:
    return httpx.Client(
        base_url=settings.channex_api_url.rstrip("/"),
        headers={"user-api-key": settings.channex_api_key,
                 "Content-Type": "application/json"},
        timeout=60.0,
    )


def _send(client: httpx.Client, path: str, values: list[dict]) -> None:
    """Post one collection of values, in batches."""
    for i in range(0, len(values), BATCH):
        chunk = values[i:i + BATCH]
        resp = client.post(path, json={"values": chunk})
        if resp.status_code >= 400:
            raise PushError(f"{path} returned {resp.status_code}: "
                            f"{resp.text[:300]}")


def availability_values(db: Session, conn, start: date,
                        end: date) -> list[dict]:
    """What is sellable per mapped room type, per night.

    Read from the same inventory the booking engine sells against, so the two
    cannot disagree about what is left. A night with no inventory row is a
    night the hotel never put on sale, and is sent as zero rather than skipped
    — skipping it leaves whatever the channel had before, which is the stale
    number this whole exercise exists to prevent.
    """
    rows = db.execute(
        text(
            """
            SELECT m.external_id,
                   d.stay_date,
                   GREATEST(0, i.physical_capacity - i.out_of_service
                               - i.held_units - i.reserved_units
                               - i.allotment_units) AS sellable
            FROM distribution.channel_room_mappings m
            CROSS JOIN generate_series(CAST(:start AS date),
                                       CAST(:end AS date),
                                       interval '1 day') AS d(stay_date)
            LEFT JOIN booking.room_type_inventory_days i
                   ON i.room_type_id = m.room_type_id
                  AND i.stay_date = d.stay_date::date
            WHERE m.link_id = :c
            ORDER BY m.external_id, d.stay_date
            """
        ),
        {"c": conn["id"], "start": start, "end": end},
    ).mappings().all()

    # Collapsed into ranges: a year of identical availability is one value,
    # not three hundred and sixty-five. Channex accepts a date range, and
    # sending every day individually is the difference between one request and
    # a hundred.
    return _collapse(rows, conn, "room_type_id", "availability",
                     lambda r: int(r["sellable"]))


def rate_values(db: Session, conn, start: date,
                end: date) -> tuple[list[dict], list[str]]:
    """Nightly rate per mapped rate plan, and what could not be priced.

    A rate plan is priced from its room type's calendar, falling back to the
    room type's list price for nights the calendar does not cover. A plan tied
    to more than one room type has no single price and is skipped with a
    reason — see the module docstring.
    """
    plans = db.execute(
        text(
            """
            SELECT m.external_id, m.rate_plan_id, rp.name,
                   count(rprt.room_type_id) AS room_types,
                   min(rprt.room_type_id::text) AS room_type_id,
                   rp.flat_rate, rp.adjustment_direction, rp.adjustment_type,
                   rp.adjustment_value
            FROM distribution.channel_rate_mappings m
            JOIN property.rate_plans rp ON rp.id = m.rate_plan_id
            LEFT JOIN property.rate_plan_room_types rprt
                   ON rprt.rate_plan_id = rp.id
            WHERE m.link_id = :c
            GROUP BY m.external_id, m.rate_plan_id, rp.name, rp.flat_rate,
                     rp.adjustment_direction, rp.adjustment_type,
                     rp.adjustment_value
            """
        ),
        {"c": conn["id"]},
    ).mappings().all()

    values: list[dict] = []
    skipped: list[str] = []

    for p in plans:
        if p["flat_rate"] is None and p["room_types"] != 1:
            skipped.append(
                f"{p['name']}: tied to {p['room_types']} room types, so it "
                f"has no single nightly rate. Give it a flat rate, or map one "
                f"channel rate plan per room type.")
            continue
        nightly = plan_rates(db, p, start, end)
        if not nightly:
            skipped.append(
                f"{p['name']}: no rate anywhere in the window, and the room "
                f"type has no list price either.")
            continue
        days = [{"stay_date": d, "rate": v} for d, v in sorted(nightly.items())]
        values.extend(_collapse(days, conn, "rate_plan_id", "rate",
                                lambda r: str(r["rate"]),
                                external_id=p["external_id"]))

    return values, skipped


def plan_rates(db: Session, plan, start: date, end: date) -> dict:
    """{night: price} one rate plan sells for, for the nights it has one.

    A price set on the plan for that night wins; otherwise a flat rate; else
    the room type's price that night (calendar, falling back to its list
    price) with the plan's adjustment applied. ``plan`` carries rate_plan_id,
    room_type_id, flat_rate and the adjustment fields.
    """
    overrides = _plan_overrides(db, plan["rate_plan_id"], start, end, "rate")
    out: dict = {}
    if plan["flat_rate"] is not None:
        flat = Decimal(str(plan["flat_rate"])).quantize(Decimal("0.01"))
        for i in range((end - start).days + 1):
            day = start + timedelta(days=i)
            out[day] = overrides.get(day, flat)
        return out
    if not plan["room_type_id"]:
        return dict(overrides)
    rows = db.execute(
        text(
            """
            SELECT d.stay_date, COALESCE(rc.rate, rt.base_rate) AS rate
            FROM generate_series(CAST(:start AS date), CAST(:end AS date),
                                 interval '1 day') AS d(stay_date)
            CROSS JOIN property.room_types rt
            LEFT JOIN property.rate_calendar_days rc
                   ON rc.room_type_id = rt.id
                  AND rc.stay_date = d.stay_date::date
            WHERE rt.id = CAST(:rt AS uuid)
            ORDER BY d.stay_date
            """
        ),
        {"start": start, "end": end, "rt": str(plan["room_type_id"])},
    ).mappings().all()
    for r in rows:
        day = _as_date(r["stay_date"])
        if day in overrides:
            out[day] = overrides[day]
        elif r["rate"] is not None:
            out[day] = _plan_rate(plan, Decimal(str(r["rate"])))
    return out


def _plan_overrides(db: Session, rate_plan_id, start: date, end: date,
                    field: str) -> dict:
    """{night: value} this plan has set for itself on the rate plan calendar."""
    assert field in {"rate", "min_stay", "max_stay", "closed_to_arrival",
                     "closed_to_departure", "stop_sell"}
    rows = db.execute(
        text(f"SELECT stay_date, {field} AS v FROM property.rate_plan_calendar_days "
             f"WHERE rate_plan_id = CAST(:p AS uuid) AND stay_date BETWEEN :s AND :e "
             f"AND {field} IS NOT NULL"),
        {"p": str(rate_plan_id), "s": start, "e": end},
    ).mappings().all()
    if field == "rate":
        return {_as_date(r["stay_date"]):
                Decimal(str(r["v"])).quantize(Decimal("0.01")) for r in rows}
    return {_as_date(r["stay_date"]): r["v"] for r in rows}


def _plan_rate(plan, base: Decimal) -> Decimal:
    """What this plan charges for a night the room lists at ``base``.

    The plan's own adjustment, which this did not apply at all. Every rate
    plan on a room was sent the room's list price: Advance Purchase went out
    at full price with its ten per cent discount silently dropped, and Bed &
    Breakfast went out without the breakfast. Invisible while a room had one
    plan -- there was nothing to differ from -- and wrong for every hotel the
    moment it had two.

    Deliberately the same arithmetic as the rate calendar (see
    ``calendar_routes``), because a price quoted on an OTA and the same price
    quoted at the desk must agree; two implementations of one rule is how
    they stop agreeing.
    """
    signed = (-plan["adjustment_value"]
              if plan["adjustment_direction"] == "decrease"
              else plan["adjustment_value"])
    signed = Decimal(str(signed or 0))
    priced = (base * (1 + signed / Decimal(100))
              if plan["adjustment_type"] == "percent"
              else base + signed)
    # Never negative. A discount larger than the rate is a configuration
    # mistake, and sending a negative price to an OTA is a worse one.
    return max(Decimal(0), priced).quantize(Decimal("0.01"))


def _as_date(value) -> date:
    """A calendar date, whatever the driver handed back.

    This is not defensive tidying; it is a bug fix. ``generate_series`` over
    dates with an interval returns *timestamps* in Postgres, so a stay date
    arrived here as a ``datetime`` and ``isoformat()`` produced
    ``2026-09-13T00:00:00+00:00``. The channel manager took that push, replied
    200, and applied none of it -- every rate stayed 0.00 and every room
    stayed at zero availability while this service reported "ok".

    A silent failure like that is the worst shape this code can take: a hotel
    that believes it is on sale, is not, and has nothing anywhere saying so.
    """
    if isinstance(value, str):
        return date.fromisoformat(value[:10])
    if isinstance(value, datetime):
        return value.date()
    return value


def _collapse(rows, conn, id_field: str, value_field: str, value_of,
              external_id: str | None = None) -> list[dict]:
    """Turn per-day rows into date ranges of equal value.

    Emits plain ``YYYY-MM-DD``. The channel manager silently ignores a range
    whose bounds carry a time, so the format is load-bearing.
    """
    out: list[dict] = []
    run_key = run_from = run_to = run_val = None

    for r in rows:
        key = external_id or r["external_id"]
        val = value_of(r)
        day = _as_date(r["stay_date"])
        if (run_key == key and run_val == val
                and run_to is not None and day == run_to + timedelta(days=1)):
            run_to = day
            continue
        if run_key is not None:
            out.append({"property_id": conn["external_property_id"],
                        id_field: run_key,
                        "date_from": run_from.isoformat(),
                        "date_to": run_to.isoformat(),
                        value_field: run_val})
        run_key, run_from, run_to, run_val = key, day, day, val

    if run_key is not None:
        out.append({"property_id": conn["external_property_id"],
                    id_field: run_key,
                    "date_from": run_from.isoformat(),
                    "date_to": run_to.isoformat(),
                    value_field: run_val})
    return out


def _collapse_many(rows, conn, id_field: str, values_of,
                   external_id: str | None = None) -> list[dict]:
    """As ``_collapse``, but for a set of values that travel together.

    A restriction is never one number: a night carries a minimum stay, a
    maximum, and three flags, and they change on different days. Collapsing
    each separately would send five overlapping sets of ranges for the same
    plan and let them disagree at the edges. Collapsed together, a range ends
    the moment any one of them changes, which is the only honest boundary.
    """
    out: list[dict] = []
    run_key = run_from = run_to = run_val = None

    for r in rows:
        key = external_id or r["external_id"]
        val = values_of(r)
        day = _as_date(r["stay_date"])
        if (run_key == key and run_val == val
                and run_to is not None and day == run_to + timedelta(days=1)):
            run_to = day
            continue
        if run_key is not None:
            out.append({"property_id": conn["external_property_id"],
                        id_field: run_key,
                        "date_from": run_from.isoformat(),
                        "date_to": run_to.isoformat(), **run_val})
        run_key, run_from, run_to, run_val = key, day, day, val

    if run_key is not None:
        out.append({"property_id": conn["external_property_id"],
                    id_field: run_key,
                    "date_from": run_from.isoformat(),
                    "date_to": run_to.isoformat(), **run_val})
    return out


#: A night's stay rules for one mapped rate plan.
#:
#: Built from the rate plan's own minimum stay, then overlaid with whatever
#: published rate rules cover that night -- lowest priority first, so the most
#: specific rule is applied last and wins. That is the same order the rules
#: screen states, and the two must agree: a minimum stay that holds at the
#: desk and not on the OTA is how a hotel takes a one-night booking in the
#: middle of the three-night weekend it meant to protect.
_RESTRICTION_SQL = """
    SELECT d.stay_date,
           rp.min_stay AS plan_min_stay,
           r.min_stay, r.max_stay, r.closed_to_arrival,
           r.closed_to_departure, r.stop_sell, r.priority
    FROM generate_series(CAST(:start AS date), CAST(:end AS date),
                         interval '1 day') AS d(stay_date)
    CROSS JOIN property.rate_plans rp
    LEFT JOIN property.rate_rules r
           ON r.property_id = rp.property_id
          AND r.status = 'published'
          AND d.stay_date::date BETWEEN r.date_from AND r.date_to
          -- Empty weekdays means every day. Postgres numbers Sunday 0, and
          -- this product numbers it 7, which is why it is not compared raw.
          AND (cardinality(r.weekdays) = 0
               OR (CASE WHEN EXTRACT(DOW FROM d.stay_date) = 0 THEN 7
                        ELSE EXTRACT(DOW FROM d.stay_date)::int END)
                   = ANY (r.weekdays))
          -- A rule aimed at particular plans or rooms only counts for those.
          AND (r.applicable_for = 'all_rate_plans'
               OR EXISTS (SELECT 1 FROM property.rate_rule_rate_plans l
                           WHERE l.rate_rule_id = r.id
                             AND l.rate_plan_id = rp.id))
          AND (NOT EXISTS (SELECT 1 FROM property.rate_rule_room_types l
                            WHERE l.rate_rule_id = r.id)
               OR EXISTS (SELECT 1 FROM property.rate_rule_room_types l
                           WHERE l.rate_rule_id = r.id
                             AND l.room_type_id = CAST(:rt AS uuid)))
          -- Scope is only ever 'all' today, but a rule meant for the desk
          -- alone must never reach a channel if that changes.
          AND r.channel_scope <> 'direct'
    WHERE rp.id = CAST(:plan AS uuid)
    ORDER BY d.stay_date, r.priority NULLS FIRST
"""


def plan_restrictions(db: Session, rate_plan_id, room_type_id, start: date,
                      end: date) -> dict:
    """{night: stay rules} for one rate plan priced from one room type.

    The plan's own minimum stay, then published rate rules in priority order,
    then the room's per-night grid settings, then the plan's own per-night
    settings -- most specific last. One implementation for the channel sync
    and the Rates & Inventory grid, so the screen shows what the OTA gets.
    """
    rows = db.execute(
        text(_RESTRICTION_SQL),
        {"start": start, "end": end, "plan": str(rate_plan_id),
         "rt": str(room_type_id)},
    ).mappings().all()

    # Fold the rules for each night, in priority order.
    by_day: dict = {}
    for r in rows:
        day = _as_date(r["stay_date"])
        cur = by_day.setdefault(day, {
            "stay_date": day,
            "min_stay_arrival": int(r["plan_min_stay"] or 1),
            "min_stay_through": int(r["plan_min_stay"] or 1),
            "max_stay": 0,
            "closed_to_arrival": False,
            "closed_to_departure": False,
            "stop_sell": False,
        })
        if r["min_stay"] is not None:
            cur["min_stay_arrival"] = int(r["min_stay"])
            cur["min_stay_through"] = int(r["min_stay"])
        if r["max_stay"] is not None:
            cur["max_stay"] = int(r["max_stay"])
        # Flags are ORed rather than overwritten: a later rule may close
        # arrivals, but nothing should quietly re-open what an earlier
        # one shut. Re-opening is an explicit act, not a side effect of
        # priority.
        for flag in ("closed_to_arrival", "closed_to_departure",
                     "stop_sell"):
            cur[flag] = cur[flag] or bool(r[flag])

    # Then the grid. A minimum stay or stop-sell typed into Rates &
    # Inventory for one night is the most specific instruction there is,
    # and it was never sent at all: the grid saved it, the desk honoured
    # it, and the OTA kept selling one-night stays into it.
    cells = db.execute(
        text("SELECT stay_date, min_stay, stop_sell "
             "FROM property.rate_calendar_days "
             "WHERE room_type_id = CAST(:rt AS uuid) "
             "AND stay_date BETWEEN :s AND :e "
             "AND (min_stay IS NOT NULL OR stop_sell)"),
        {"rt": str(room_type_id), "s": start, "e": end},
    ).mappings().all()
    for c in cells:
        cur = by_day.get(_as_date(c["stay_date"]))
        if cur is None:
            continue
        if c["min_stay"] is not None:
            cur["min_stay_arrival"] = int(c["min_stay"])
            cur["min_stay_through"] = int(c["min_stay"])
        cur["stop_sell"] = cur["stop_sell"] or bool(c["stop_sell"])

    # And last, whatever this rate plan has set for itself on a night --
    # the most specific setting there is. An explicit value wins either
    # way, so a plan can be re-opened on a night a rule closed.
    plan_nights = db.execute(
        text("SELECT stay_date, min_stay, max_stay, closed_to_arrival, "
             "closed_to_departure, stop_sell "
             "FROM property.rate_plan_calendar_days "
             "WHERE rate_plan_id = CAST(:p AS uuid) "
             "AND stay_date BETWEEN :s AND :e"),
        {"p": str(rate_plan_id), "s": start, "e": end},
    ).mappings().all()
    for o in plan_nights:
        cur = by_day.get(_as_date(o["stay_date"]))
        if cur is None:
            continue
        if o["min_stay"] is not None:
            cur["min_stay_arrival"] = int(o["min_stay"])
            cur["min_stay_through"] = int(o["min_stay"])
        if o["max_stay"] is not None:
            cur["max_stay"] = int(o["max_stay"])
        for flag in ("closed_to_arrival", "closed_to_departure",
                     "stop_sell"):
            if o[flag] is not None:
                cur[flag] = bool(o[flag])
    return by_day


def restriction_values(db: Session, conn, start: date,
                       end: date) -> list[dict]:
    """Stay rules per mapped rate plan, per night, as date ranges."""
    plans = db.execute(
        text(
            """
            SELECT m.external_id, m.rate_plan_id,
                   min(rprt.room_type_id::text) AS room_type_id,
                   count(rprt.room_type_id) AS room_types
            FROM distribution.channel_rate_mappings m
            LEFT JOIN property.rate_plan_room_types rprt
                   ON rprt.rate_plan_id = m.rate_plan_id
            WHERE m.link_id = :c
            GROUP BY m.external_id, m.rate_plan_id
            """
        ),
        {"c": conn["id"]},
    ).mappings().all()

    values: list[dict] = []
    for p in plans:
        if p["room_types"] != 1:
            # Cannot be sold on a channel at all, so it has no rules to send.
            # Reported once, by the rate half of the push.
            continue
        by_day = plan_restrictions(db, p["rate_plan_id"], p["room_type_id"],
                                   start, end)
        ordered = [by_day[d] for d in sorted(by_day)]
        values.extend(_collapse_many(
            ordered, conn, "rate_plan_id",
            lambda r: {
                "min_stay_arrival": r["min_stay_arrival"],
                "min_stay_through": r["min_stay_through"],
                "max_stay": r["max_stay"],
                "closed_to_arrival": r["closed_to_arrival"],
                "closed_to_departure": r["closed_to_departure"],
                "stop_sell": r["stop_sell"],
            },
            external_id=p["external_id"]))
    return values


def push(db: Session, link_id, *, days: int = WINDOW_DAYS) -> dict:
    """Send one connection's rates and availability. Records what happened."""
    conn = db.execute(
        text("SELECT id, organization_id, property_id, external_property_id, "
             "send_availability, send_rates, send_restrictions "
             "FROM distribution.channel_manager_links WHERE id = :i"),
        {"i": link_id},
    ).mappings().first()
    if conn is None:
        raise PushError("No such channel manager link.")
    if not conn["external_property_id"]:
        return _record(db, link_id, "failed",
                       "No channel manager property id on this property, so "
                       "there is nowhere to send.")
    if not settings.channex_api_key:
        return _record(db, link_id, "failed",
                       "No CHANNEX_API_KEY configured.")

    start = date.today()
    end = start + timedelta(days=days)

    # Honour what the property asked for. Computed only when it is going to
    # be sent: a hotel that has switched rates off should not pay for the
    # calendar queries that price them.
    avail = (availability_values(db, conn, start, end)
             if conn["send_availability"] else [])
    rates, skipped = (rate_values(db, conn, start, end)
                      if conn["send_rates"] else ([], []))
    limits = (restriction_values(db, conn, start, end)
              if conn["send_restrictions"] else [])

    problems: list[str] = list(skipped)
    sent_avail = sent_rates = sent_limits = 0
    try:
        with _client() as c:
            if avail:
                _send(c, "/availability", avail)
                sent_avail = len(avail)
            if rates:
                _send(c, "/restrictions", rates)
                sent_rates = len(rates)
            if limits:
                # Same endpoint as rates: the channel manager takes a rate and
                # a night's stay rules through one door, keyed on rate plan
                # and date range.
                _send(c, "/restrictions", limits)
                sent_limits = len(limits)
    except (PushError, httpx.HTTPError) as exc:
        return _record(db, link_id, "failed", str(exc)[:1000])

    if not avail and not rates and not limits:
        # Switched off is not failure. A property that has deliberately
        # stopped sending should not sit on a red status for ever, and the
        # sweep should not keep retrying something nobody wants sent.
        if not conn["send_availability"] and not conn["send_rates"]:
            return _record(db, link_id, "ok",
                           "Nothing sent: availability and rates are both "
                           "switched off for this property.")
        return _record(db, link_id, "failed",
                       "Nothing to send: no rooms or rate plans are mapped.")

    # Partial is its own state. Availability going out while some rates could
    # not be priced is not success — it is a channel selling rooms at whatever
    # price it had before.
    status = "partial" if problems else "ok"
    detail = (f"Sent {sent_avail} availability, {sent_rates} rate and "
              f"{sent_limits} stay-rule ranges for {start}..{end}.")
    if problems:
        detail += " Not sent: " + " ".join(problems)
    return _record(db, link_id, status, detail[:1000])


def _record(db: Session, link_id, status: str, detail: str) -> dict:
    db.execute(
        text("UPDATE distribution.channel_manager_links "
             "SET last_pushed_at = now(), last_push_status = :s, "
             "last_push_detail = :d WHERE id = :i"),
        {"s": status, "d": detail, "i": link_id},
    )
    if status != "ok":
        log.warning("channel push %s: %s", status, detail)
    return {"status": status, "detail": detail}


def push_all(db: Session) -> list[dict]:
    """Every property with somewhere to send to.

    One push per property, not per OTA: rates and availability go to the
    channel manager, which fans them out to whichever channels that property
    has connected. Pushing per partner would send the same numbers three
    times.
    """
    system_context(db, reason="channel push: list connected properties")
    links = db.execute(
        text("SELECT id, organization_id, property_id "
             "FROM distribution.channel_manager_links "
             "WHERE external_property_id IS NOT NULL")
    ).mappings().all()
    results = []
    for link in links:
        # Each property's rates and availability are read as that tenant only.
        bind_tenant_context(db, organization_id=link["organization_id"],
                            property_id=link["property_id"], is_service=True)
        results.append(push(db, link["id"]))
    return results
