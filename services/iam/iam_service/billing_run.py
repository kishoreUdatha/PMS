"""Turning subscriptions into invoices, without anybody pressing a button.

A subscription that is never billed is a customer who never pays, so this is
the piece that makes the commercial half real. It follows the night audit's
scheduler in every respect that matters, because the same problems apply:

**It catches up rather than only billing today.** If the service was down for a
fortnight, every period that closed while it was down is invoiced in order,
oldest first. Billing only the latest period would silently forgive every
period behind it.

**Safety comes from the schema, not from a lock.** ``billing.invoices`` carries
``UNIQUE (subscription_id, period_start)``, so a second replica, a manual run
and a retry after a timeout can all collide and still produce one invoice per
period. There is no leader election here and none is needed.

**Nothing is charged during a trial.** A trialing subscription becomes active
on the day its trial ends and is billed from that day, not from signup --
otherwise the first invoice would quietly include the free period.

**No money moves.** This issues invoices; collecting against them is a separate
step through a payment provider, and this module deliberately cannot do it. An
invoice that marked itself paid without a provider event would be the worst
possible bug in a billing system.

The tax rate is configuration, defaulting to zero, and that default is
deliberate. Guessing 18% would produce invoices that look authoritative and
are wrong for anyone the guess does not fit; an invoice with no tax is
obviously incomplete and gets noticed.
"""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal

from chirala_common.db import system_context
from sqlalchemy import text
from sqlalchemy.orm import Session

from .audit import record_audit
from .settings import settings

log = logging.getLogger("uvicorn.error").getChild("billing_run")

MONEY = Decimal("0.01")

#: Most periods to bill for one subscription in a single pass. A subscription
#: left unbilled for years should not tie the run up; the next pass continues.
MAX_CATCH_UP = 24

LIVE = ("trialing", "active", "past_due", "grace")


def _money(v) -> Decimal:
    return Decimal(str(v or 0)).quantize(MONEY, rounding=ROUND_HALF_UP)


def _advance(start: date, cycle: str) -> date:
    """The end of a period beginning on ``start``.

    Calendar months rather than 30 days: a monthly subscription starting on
    the 15th should renew on the 15th, and adding 30 days walks the date
    backwards through the year.
    """
    if cycle == "annual":
        try:
            return start.replace(year=start.year + 1)
        except ValueError:          # 29 February
            return start.replace(year=start.year + 1, month=3, day=1)
    month = start.month + 1
    year = start.year + (month > 12)
    month = 1 if month > 12 else month
    day = start.day
    while day > 28:
        try:
            return date(year, month, day)
        except ValueError:
            day -= 1
    return date(year, month, day)


@dataclass
class RunResult:
    as_of: date
    invoiced: list[dict] = field(default_factory=list)
    activated: list[str] = field(default_factory=list)
    past_due: list[str] = field(default_factory=list)
    skipped: list[dict] = field(default_factory=list)
    dry_run: bool = False

    def summary(self) -> dict:
        return {
            "as_of": str(self.as_of), "dry_run": self.dry_run,
            "invoices_created": len(self.invoiced),
            "total_billed": str(sum((_money(i["total"]) for i in self.invoiced),
                                    Decimal("0.00"))),
            "trials_activated": len(self.activated),
            "marked_past_due": len(self.past_due),
            "invoiced": self.invoiced, "activated": self.activated,
            "past_due": self.past_due, "skipped": self.skipped,
        }


def _next_number(db: Session, series: str) -> int:
    """The next invoice number in a series, safely under concurrency.

    A transaction-scoped advisory lock on the series, the same device 0025
    uses for its function definitions. Two runs racing would otherwise read
    the same max and both insert it -- and the unique constraint would turn
    one of them into a crash rather than a wait.
    """
    db.execute(text("SELECT pg_advisory_xact_lock(hashtext(:k))"),
               {"k": f"billing:invoice-series:{series}"})
    return db.execute(
        text("SELECT coalesce(max(number), 0) + 1 FROM billing.invoices "
             "WHERE series = :s"),
        {"s": series},
    ).scalar()


def _quantity(db: Session, org_id: uuid.UUID) -> int:
    """How many units of the plan this tenant is billed for: one.

    Pricing is per tenant per month — the design pack's catalogue says so on
    every card. An earlier version of this counted active properties and
    multiplied, which billed a three-hotel group three times for a plan whose
    *limit* is three properties.

    Kept as a function returning 1 rather than deleted, because the column and
    the invoice line still carry a quantity and a future seat-priced plan
    would put a real number back here.
    """
    return 1


def _customer_snapshot(db: Session, org_id: uuid.UUID) -> tuple[dict, str | None]:
    """Who is being billed, frozen onto the invoice, and where supply lands.

    Snapshotted because an issued invoice may not change and a customer's
    address may. Falls back to the organisation's own name when no billing
    customer has been set up, so a missing record delays nothing.
    """
    c = db.execute(
        text(
            """
            SELECT c.legal_name, c.billing_email, c.address_line, c.city,
                   c.state_code, c.postal_code, c.country, c.gstin
            FROM billing.customers c WHERE c.organization_id = :o
            """
        ),
        {"o": org_id},
    ).mappings().first()
    if c:
        return dict(c), c["state_code"]
    name = db.execute(
        text("SELECT name FROM iam.organizations WHERE id = :o"),
        {"o": org_id},
    ).scalar()
    return {"legal_name": name, "billing_email": None, "gstin": None}, None


def _issue_invoice(db: Session, sub: dict, period_start: date,
                   period_end: date, as_of: date) -> dict:
    """One invoice for one closed period. Idempotent by construction."""
    org_id = sub["organization_id"]
    quantity = _quantity(db, org_id)
    unit = _money(sub["amount"])
    subtotal = _money(unit * quantity)
    rate = Decimal(str(getattr(settings, "saas_tax_rate", 0) or 0))
    tax = _money(subtotal * rate / Decimal(100))
    total = _money(subtotal + tax)
    snapshot, place = _customer_snapshot(db, org_id)

    number = _next_number(db, "SAAS")
    invoice_id = db.execute(
        text(
            """
            INSERT INTO billing.invoices
                (organization_id, subscription_id, series, number, status,
                 period_start, period_end, due_on, currency, subtotal,
                 tax_total, total, place_of_supply, customer_snapshot,
                 totals_snapshot, issued_at)
            VALUES (:org, :sub, 'SAAS', :num, 'issued', :ps, :pe, :due, :cur,
                    :sub_t, :tax, :total, :place,
                    CAST(:snap AS jsonb), CAST(:totals AS jsonb), now())
            -- The billing run's protection from itself. A second replica or a
            -- retry lands here and is discarded rather than double-billing.
            ON CONFLICT (subscription_id, period_start) DO NOTHING
            RETURNING id
            """
        ),
        {"org": org_id, "sub": sub["id"], "num": number, "ps": period_start,
         "pe": period_end,
         "due": as_of + timedelta(days=int(getattr(settings, "saas_net_days", 7))),
         "cur": sub["currency"], "sub_t": subtotal, "tax": tax, "total": total,
         "place": place,
         "snap": json.dumps(snapshot, default=str),
         "totals": json.dumps(
             {"subtotal": str(subtotal), "tax_total": str(tax),
              "total": str(total), "quantity": quantity,
              "unit_amount": str(unit), "tax_rate": str(rate)})},
    ).scalar()

    if invoice_id is None:
        # Already invoiced for this period by another pass.
        return {}

    db.execute(
        text(
            """
            INSERT INTO billing.invoice_lines
                (invoice_id, kind, description, quantity, unit_amount, amount,
                 tax_rate, tax_amount, plan_version_id)
            VALUES (:i, 'subscription', :desc, :qty, :unit, :amt, :rate, :tax,
                    :pv)
            """
        ),
        {"i": invoice_id,
         "desc": f"{sub['plan_name']} — {period_start} to {period_end}",
         "qty": quantity, "unit": unit, "amt": subtotal, "rate": rate,
         "tax": tax, "pv": sub["plan_version_id"]},
    )
    return {"invoice_id": str(invoice_id), "number": number,
            "organization": sub["organization_name"],
            "period_start": str(period_start), "period_end": str(period_end),
            "quantity": quantity, "total": str(total)}


_DUE_SQL = """
    SELECT s.id, s.organization_id, o.name AS organization_name,
           s.plan_version_id, s.status, s.amount, s.currency, s.quantity,
           s.billing_cycle, s.started_on, s.trial_ends_on,
           s.current_period_start, s.current_period_end,
           p.name AS plan_name
    FROM billing.subscriptions s
    JOIN iam.organizations o ON o.id = s.organization_id
    JOIN billing.plan_versions v ON v.id = s.plan_version_id
    JOIN billing.plans p ON p.id = v.plan_id
    WHERE s.status = ANY(:live)
    ORDER BY o.name
"""


def run_billing(db: Session, *, as_of: date | None = None,
                dry_run: bool = False,
                actor_subject: str = "scheduler") -> RunResult:
    """Bill every subscription whose period has closed, catching up.

    Runs in system context: it reads and writes across every tenant by nature,
    which is the definition of a platform job.
    """
    as_of = as_of or date.today()
    result = RunResult(as_of=as_of, dry_run=dry_run)
    system_context(db, reason=f"billing run for {as_of}")

    for sub in [dict(r) for r in db.execute(
            text(_DUE_SQL), {"live": list(LIVE)}).mappings()]:

        # A trial that has run out becomes a paying subscription, billed from
        # the day it ended rather than from signup.
        if sub["status"] == "trialing":
            ends = sub["trial_ends_on"]
            if ends is None or ends > as_of:
                result.skipped.append(
                    {"organization": sub["organization_name"],
                     "why": f"in trial until {ends}"})
                continue
            if not dry_run:
                db.execute(
                    text("UPDATE billing.subscriptions SET status = 'active', "
                         "current_period_start = :s, current_period_end = :e, "
                         "updated_at = now(), version = version + 1 "
                         "WHERE id = :id"),
                    {"s": ends, "e": _advance(ends, sub["billing_cycle"]),
                     "id": sub["id"]},
                )
            result.activated.append(sub["organization_name"])
            sub["current_period_start"] = ends
            sub["current_period_end"] = _advance(ends, sub["billing_cycle"])
            sub["status"] = "active"

        start = sub["current_period_start"] or sub["started_on"]
        end = sub["current_period_end"] or _advance(start, sub["billing_cycle"])

        billed = 0
        while end <= as_of and billed < MAX_CATCH_UP:
            if dry_run:
                result.invoiced.append({
                    "organization": sub["organization_name"],
                    "period_start": str(start), "period_end": str(end),
                    "quantity": _quantity(db, sub["organization_id"]),
                    "total": str(_money(
                        _money(sub["amount"])
                        * _quantity(db, sub["organization_id"]))),
                    "invoice_id": None, "number": None,
                })
            else:
                made = _issue_invoice(db, sub, start, end, as_of)
                if made:
                    result.invoiced.append(made)
            start, end = end, _advance(end, sub["billing_cycle"])
            billed += 1

        if billed and not dry_run:
            db.execute(
                text("UPDATE billing.subscriptions "
                     "SET current_period_start = :s, current_period_end = :e, "
                     "    updated_at = now(), version = version + 1 "
                     "WHERE id = :id"),
                {"s": start, "e": end, "id": sub["id"]},
            )

    # Anything issued and past its due date puts the subscription in arrears.
    # Suspension is deliberately NOT automatic: cutting a hotel off mid-season
    # is a commercial decision somebody makes, not one a cron job makes at 3am.
    overdue = [dict(r) for r in db.execute(
        text(
            """
            SELECT DISTINCT s.id, o.name AS organization_name
            FROM billing.invoices i
            JOIN billing.subscriptions s ON s.id = i.subscription_id
            JOIN iam.organizations o ON o.id = s.organization_id
            WHERE i.status = 'issued' AND i.due_on < :as_of
              AND s.status = 'active'
            """
        ),
        {"as_of": as_of},
    ).mappings()]
    for row in overdue:
        if not dry_run:
            db.execute(
                text("UPDATE billing.subscriptions SET status = 'past_due', "
                     "grace_until = :g, updated_at = now(), "
                     "version = version + 1 WHERE id = :id"),
                {"g": as_of + timedelta(
                    days=int(getattr(settings, "saas_grace_days", 14))),
                 "id": row["id"]},
            )
        result.past_due.append(row["organization_name"])

    if not dry_run and (result.invoiced or result.activated or result.past_due):
        record_audit(
            db, action="billing.run.completed", entity_type="billing_run",
            entity_id=str(as_of), actor_subject=actor_subject,
            after={"invoices": len(result.invoiced),
                   "activated": len(result.activated),
                   "past_due": len(result.past_due)},
        )
        log.info("billing run %s: %d invoice(s), %d activated, %d past due",
                 as_of, len(result.invoiced), len(result.activated),
                 len(result.past_due))
    return result
