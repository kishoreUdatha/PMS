"""Turn rate rules into the prices the rest of the system already reads.

A rate rule used to be a statement of intent that nothing carried out. The
resolution query lived only inside the rules screen, while every path that
quotes money -- the booking engine's search and its book, a reservation's
stored ``nightly_rate``, room moves, no-show penalties, the rates pushed to
OTAs -- read ``COALESCE(rate_calendar_days.rate, room_types.base_rate)``. So
"+30% at weekends" published, simulated correctly, and changed nothing anybody
paid.

This publishes rules *into* that calendar. One integration point instead of
five, and the surcharge reaches Booking.com as well as the front desk, because
both already read the table being written.

Three rules govern the writing.

**A hand-typed rate is never overwritten.** ``source`` tells the two kinds of
row apart. A manager who set 4,500 for a wedding party on the 18th meant it,
and a rule that computes 2,600 does not know about the wedding. Those days are
reported as skipped, with their reason, rather than silently left out.

**Publishing is idempotent and re-runnable.** A row this or any rule published
before is recomputed from the base rate, not from itself -- compounding a
weekend rule by re-running it is how a room ends up at 40,000 a night. The
base is always the room type's own rate.

**A night no rule touches is left out of the calendar entirely.** Not written
at the base rate -- *absent*. An explicit row pins the price: publish 2,000
for every ordinary Tuesday and the owner who later raises the room from 2,000
to 2,500 finds that every published Tuesday is still selling at 2,000, because
``COALESCE(calendar, base)`` stops looking as soon as the calendar answers.
Absence is what keeps the base rate live. The corollary is that a rule which
stops applying must take its row away with it, or a stale surcharge outlives
the rule that made it -- so those rows are cleared, not ignored.

**Occupancy is read as it stands right now.** A rule gated on how full a date
is can only be evaluated against the inventory of the moment, so a published
rate is that judgement frozen. It goes stale as rooms sell, which is why
``sweep_occupancy_rules`` re-runs on a timer -- without it "raise the price
once we are 80% full" would only ever be true at the instant somebody pressed
Publish.

**Preview and publish share one function.** ``plan()`` decides everything and
writes nothing; ``publish()`` applies exactly what ``plan()`` returned. A
preview that is computed differently from the thing it previews is worse than
no preview, because it is believed.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal

from chirala_common.db import bind_tenant_context, system_context
from sqlalchemy import text
from sqlalchemy.orm import Session

#: The largest window one publish may cover. A year of nightly rates across a
#: dozen room types is already ~4,000 rows; beyond that this wants to be a
#: background job, and quietly accepting a ten-year range would make it one
#: without anyone deciding to.
MAX_DAYS = 400


def _money(value) -> Decimal:
    return Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


@dataclass
class DayPlan:
    """What one night would become, and why.

    ``action`` is the whole decision:

    ``write``  a rule applies — store the computed rate.
    ``clear``  no rule applies any more, but one published here before. Remove
               the row so the night falls back to the live base rate.
    ``none``   no rule, and nothing stored. Correctly absent; leave it.
    ``skip``   hand-typed, or unpriceable. Reported, never touched.
    """

    stay_date: date
    room_type_id: uuid.UUID
    room_type_name: str
    base_rate: Decimal
    current_rate: Decimal | None
    new_rate: Decimal
    action: str = "none"
    #: How full the night is, as a percent of sellable rooms. None when the
    #: night has no inventory row at all.
    occupancy: int | None = None
    #: Rules that changed the number, in the order they applied.
    applied: list[str] = field(default_factory=list)
    applied_ids: list[uuid.UUID] = field(default_factory=list)
    #: Set when this night is left alone, and why.
    skipped: str | None = None

    @property
    def changes(self) -> bool:
        if self.action == "write":
            return self.current_rate != self.new_rate
        return self.action == "clear"


@dataclass
class Plan:
    days: list[DayPlan]

    @property
    def to_write(self) -> list[DayPlan]:
        return [d for d in self.days if d.action == "write"]

    @property
    def to_clear(self) -> list[DayPlan]:
        return [d for d in self.days if d.action == "clear"]

    @property
    def changed(self) -> list[DayPlan]:
        return [d for d in self.days if d.changes]

    @property
    def protected(self) -> list[DayPlan]:
        return [d for d in self.days if d.action == "skip"]


_RULES_FOR_WINDOW = """
    SELECT r.id, r.name, r.priority, r.rule_type, r.fixed_rate,
           r.adjustment_direction, r.adjustment_type, r.adjustment_value,
           r.date_from, r.date_to, r.weekdays,
           r.occupancy_min, r.occupancy_max,
           ARRAY(SELECT l.room_type_id FROM property.rate_rule_room_types l
                  WHERE l.rate_rule_id = r.id) AS room_type_ids
      FROM property.rate_rules r
     WHERE r.property_id = :prop
       AND r.status = 'published'
       -- Price-moving rules only. A rule that just sets a minimum stay has
       -- nothing to publish here; channel push already carries restrictions.
       AND (r.rule_type = 'fixed_rate' OR r.adjustment_value <> 0)
       AND r.date_from <= :end AND r.date_to >= :start
     ORDER BY r.priority, r.name
"""


def plan(
    db: Session,
    *,
    property_id: uuid.UUID,
    date_from: date,
    date_to: date,
    room_type_ids: list[uuid.UUID] | None = None,
    rule_id: uuid.UUID | None = None,
) -> Plan:
    """Work out what every night in the window would become. Writes nothing.

    ``rule_id`` narrows to one rule's own effect, for the preview shown beside
    a single rule. Left out, every published price rule is layered, which is
    what the calendar has to end up holding: a night is one number, and it is
    the number all the rules together produce.
    """
    if date_to < date_from:
        raise ValueError("The end of the range is before its start.")
    span = (date_to - date_from).days + 1
    if span > MAX_DAYS:
        raise ValueError(
            f"That is {span} days. Publish at most {MAX_DAYS} at a time.")

    rooms = db.execute(
        text(
            """
            SELECT id, name, base_rate FROM property.room_types
             WHERE property_id = :prop AND status = 'active'
               AND (:all OR id = ANY(:ids))
             ORDER BY name
            """
        ),
        {"prop": property_id, "all": not room_type_ids,
         "ids": room_type_ids or []},
    ).mappings().all()

    rules = db.execute(
        text(_RULES_FOR_WINDOW),
        {"prop": property_id, "start": date_from, "end": date_to},
    ).mappings().all()
    if rule_id is not None:
        rules = [r for r in rules if r["id"] == rule_id]

    # Existing cells, so the preview can show what changes and so a hand-typed
    # rate can be recognised and left alone.
    existing = {
        (r["room_type_id"], r["stay_date"]): r
        for r in db.execute(
            text(
                """
                SELECT room_type_id, stay_date, rate, source
                  FROM property.rate_calendar_days
                 WHERE property_id = :prop
                   AND stay_date BETWEEN :start AND :end
                """
            ),
            {"prop": property_id, "start": date_from, "end": date_to},
        ).mappings().all()
    }

    # How full each night already is, as a percentage of what can be sold.
    #
    # Sellable is capacity minus out-of-service: a room under repair cannot be
    # sold, and counting it reports a full hotel as 80% full and withholds the
    # surcharge exactly when it is due.
    #
    # Sold is confirmed reservations only. Holds are deliberately excluded --
    # they expire in fifteen minutes, and letting them move the price means a
    # browser left open on a search page raises the rate. Allotment is
    # excluded too: rooms committed to a channel are not rooms a guest has
    # paid for, and a property that always allots would otherwise read as
    # permanently full and never come off its top rate.
    occupancy: dict[tuple[uuid.UUID, date], int] = {
        (r["room_type_id"], r["stay_date"]): int(r["pct"])
        for r in db.execute(
            text(
                """
                SELECT room_type_id, stay_date,
                       round(100.0 * reserved_units
                             / NULLIF(physical_capacity - out_of_service, 0)
                       ) AS pct
                  FROM booking.room_type_inventory_days
                 WHERE property_id = :prop
                   AND stay_date BETWEEN :start AND :end
                   AND physical_capacity - out_of_service > 0
                """
            ),
            {"prop": property_id, "start": date_from, "end": date_to},
        ).mappings().all()
    }

    out: list[DayPlan] = []
    for room in rooms:
        for offset in range(span):
            day = date_from + timedelta(days=offset)
            cell = existing.get((room["id"], day))
            current = None if cell is None or cell["rate"] is None else _money(
                cell["rate"])

            if room["base_rate"] is None:
                out.append(DayPlan(
                    stay_date=day, room_type_id=room["id"],
                    room_type_name=room["name"], base_rate=Decimal(0),
                    current_rate=current, new_rate=Decimal(0), action="skip",
                    skipped="This room type has no base rate to work from.",
                ))
                continue

            # Always from the room type's own rate, never from whatever is in
            # the cell: recomputing from a rate a rule already raised is how
            # re-running "+30%" twice makes it +69%.
            base = _money(room["base_rate"])
            rate = base
            applied: list[str] = []
            applied_ids: list[uuid.UUID] = []

            # Monday is 1 and Sunday is 7 here, matching the rules screen.
            iso_day = day.isoweekday()
            for r in rules:
                if not (r["date_from"] <= day <= r["date_to"]):
                    continue
                weekdays = list(r["weekdays"] or [])
                if weekdays and iso_day not in weekdays:
                    continue
                targets = list(r["room_type_ids"] or [])
                if targets and room["id"] not in targets:
                    continue

                lo, hi = r["occupancy_min"], r["occupancy_max"]
                if lo is not None or hi is not None:
                    pct = occupancy.get((room["id"], day))
                    # No inventory row means nothing is on sale for that night,
                    # so there is no occupancy to judge. Treated as not
                    # matching rather than as zero: an empty grid would
                    # otherwise trigger every "discount when quiet" rule
                    # across dates the property has not opened yet.
                    if pct is None:
                        continue
                    if lo is not None and pct < lo:
                        continue
                    if hi is not None and pct > hi:
                        continue

                if r["rule_type"] == "fixed_rate":
                    if r["fixed_rate"] is None:
                        continue
                    rate = _money(r["fixed_rate"])
                else:
                    signed = (
                        -r["adjustment_value"]
                        if r["adjustment_direction"] == "decrease"
                        else r["adjustment_value"]
                    )
                    rate = max(Decimal(0), _money(
                        rate * (1 + signed / Decimal(100))
                        if r["adjustment_type"] == "percent"
                        else rate + signed
                    ))
                if r["occupancy_min"] is not None or r["occupancy_max"] is not None:
                    # Say the number. "Last-minute yield" on its own leaves a
                    # manager unable to tell why one Tuesday moved and the
                    # next did not.
                    pct = occupancy.get((room["id"], day), 0)
                    applied.append(f"{r['name']} ({pct}% sold)")
                else:
                    applied.append(r["name"])
                applied_ids.append(r["id"])

            day_plan = DayPlan(
                stay_date=day, room_type_id=room["id"],
                room_type_name=room["name"], base_rate=base,
                current_rate=current, new_rate=rate,
                occupancy=occupancy.get((room["id"], day)),
                applied=applied, applied_ids=applied_ids,
            )
            stored_by_rule = cell is not None and cell["source"] == "rule"
            if (cell is not None and cell["source"] == "manual"
                    and cell["rate"] is not None):
                # Somebody typed this one. Publishing must not argue.
                day_plan.action = "skip"
                day_plan.skipped = "Set by hand, left as it is."
            elif applied:
                day_plan.action = "write"
            elif stored_by_rule:
                # A rule priced this night once and no longer does. Taking
                # the row away is what lets it follow the base rate again.
                day_plan.action = "clear"
                day_plan.new_rate = base
            else:
                day_plan.action = "none"
            out.append(day_plan)

    return Plan(days=out)


def publish(
    db: Session,
    *,
    property_id: uuid.UUID,
    organization_id: uuid.UUID,
    computed: Plan,
    actor_id: uuid.UUID | None,
) -> tuple[int, int]:
    """Apply exactly what ``plan()`` decided.

    Returns ``(written, cleared)``.
    """
    cleared = 0
    for d in computed.to_clear:
        cleared += db.execute(
            text(
                """
                DELETE FROM property.rate_calendar_days
                 WHERE property_id = :prop AND room_type_id = :rt
                   AND stay_date = :d
                   -- Never a hand-typed row, whatever the plan says.
                   AND source = 'rule'
                """
            ),
            {"prop": property_id, "rt": d.room_type_id, "d": d.stay_date},
        ).rowcount

    rows = computed.to_write
    if not rows:
        return 0, cleared
    for d in rows:
        db.execute(
            text(
                """
                INSERT INTO property.rate_calendar_days
                    (organization_id, property_id, room_type_id, stay_date,
                     rate, source, rule_id, updated_by)
                VALUES (:org, :prop, :rt, :d, :rate, 'rule', :rule, :by)
                ON CONFLICT (property_id, room_type_id, stay_date) DO UPDATE
                   SET rate = EXCLUDED.rate,
                       source = 'rule',
                       rule_id = EXCLUDED.rule_id,
                       updated_by = EXCLUDED.updated_by,
                       updated_at = now(),
                       version = property.rate_calendar_days.version + 1
                 -- Belt and braces. plan() already excludes hand-typed days;
                 -- this makes it impossible to overwrite one even if a future
                 -- caller builds a Plan some other way.
                 WHERE property.rate_calendar_days.source <> 'manual'
                """
            ),
            {
                "org": organization_id, "prop": property_id,
                "rt": d.room_type_id, "d": d.stay_date, "rate": d.new_rate,
                # A night several rules touched is attributed to the last one
                # that moved it, which is the one that decided the number.
                "rule": d.applied_ids[-1] if d.applied_ids else None,
                "by": actor_id,
            },
        )
    return len(rows), cleared


#: How far ahead the occupancy sweep re-prices. Beyond a couple of months
#: almost nothing is sold yet, so every night reads as empty and the sweep
#: would spend its time confirming that.
SWEEP_DAYS = 60


def sweep_occupancy_rules(db: Session) -> list[dict]:
    """Re-publish every property that prices on how full it is.

    Occupancy pricing is the one kind that goes stale on its own. A weekend
    rule is as true tomorrow as today, but "+20% once 80% is sold" was
    evaluated against the inventory of the moment it was published, and the
    moment rooms sell it is answering an old question. Without this, the rule
    would only ever be true at the instant somebody pressed Publish, which is
    not what "raise the price as it fills" means to anyone.

    Only properties that actually have such a rule are touched. A property
    pricing purely on weekends and festivals has nothing here that changes by
    itself, and re-publishing it every few minutes would churn its calendar --
    and its OTA pushes -- for no change at all.
    """
    from datetime import date as _date

    # Finding the work crosses tenants by nature, so it is done in system
    # context and says why. Everything after is done as one tenant at a time:
    # discovering a property is above the tenant boundary, re-pricing one is
    # not, and running the write in system context would take away the very
    # check that stops one property's rates landing in another's calendar.
    system_context(db, reason="occupancy yield: find properties that price on how full they are")
    rows = db.execute(
        text(
            """
            SELECT DISTINCT r.property_id, r.organization_id
              FROM property.rate_rules r
             WHERE r.status = 'published'
               AND (r.occupancy_min IS NOT NULL OR r.occupancy_max IS NOT NULL)
               AND r.date_to >= CURRENT_DATE
            """
        )
    ).mappings().all()

    today = db.execute(text("SELECT CURRENT_DATE")).scalar_one()
    if isinstance(today, str):  # pragma: no cover - driver dependent
        today = _date.fromisoformat(today)

    results: list[dict] = []
    for row in rows:
        property_id, org = row["property_id"], row["organization_id"]
        bind_tenant_context(db, organization_id=org,
                            property_id=property_id, is_service=True)
        try:
            computed = plan(
                db, property_id=property_id, date_from=today,
                date_to=today + timedelta(days=SWEEP_DAYS),
            )
            # Nothing to do is the common case, and writing nothing is how
            # this stays cheap enough to run on a timer.
            if not computed.changed:
                results.append({"property_id": str(property_id),
                                "status": "ok", "written": 0, "cleared": 0})
                continue
            written, cleared = publish(
                db, property_id=property_id, organization_id=org,
                computed=computed,
                # Nobody pressed a button. Left null rather than attributed to
                # whoever last touched the rule, who did not decide this.
                actor_id=None,
            )
            results.append({"property_id": str(property_id), "status": "ok",
                            "written": written, "cleared": cleared})
        except Exception as exc:  # one property must not stop the rest
            results.append({"property_id": str(property_id),
                            "status": "error", "detail": str(exc)})
    return results
