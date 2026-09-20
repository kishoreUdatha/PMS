"""Runs the billing run without anybody being awake to press a button.

The same shape as finance's night audit loop, and for the same reason: a job
that only runs when somebody remembers is a job that does not run. A period
that closes on a Saturday should be invoiced on the Saturday.

**No leader election, deliberately.** Safety comes from the schema --
``billing.invoices`` has ``UNIQUE (subscription_id, period_start)`` -- so a
second replica running this loop produces nothing rather than a second
invoice. That is a stronger guarantee than a lock, because it also covers a
manual run from the console racing the scheduler.

**Off by default.** ``BILLING_RUN_ENABLED`` has to be set. Issuing invoices is
the first thing in this system that creates a financial obligation, and a
deployment should have to say out loud that it is the one doing it -- a
developer's laptop pointed at a shared database should not start billing
customers because it happened to boot.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import date

from .billing_run import run_billing
from .database import SessionFactory
from .settings import settings

# A child of uvicorn's logger so it inherits the handlers the server already
# configured. A plain module logger propagates to a bare root and prints
# nothing -- which for the one part of this service that runs with nobody
# watching is the worst possible default.
log = logging.getLogger("uvicorn.error").getChild("billing")


def _once() -> None:
    """One pass, in its own session and transaction."""
    with SessionFactory() as session:
        try:
            result = run_billing(session, as_of=date.today(), dry_run=False,
                                 actor_subject="scheduler")
            session.commit()
            if result.invoiced or result.activated or result.past_due:
                log.info(
                    "billing: %d invoice(s), %d trial(s) activated, "
                    "%d marked past due",
                    len(result.invoiced), len(result.activated),
                    len(result.past_due))
        except Exception:  # noqa: BLE001
            session.rollback()
            # Logged and swallowed: one bad pass must not kill the loop, or a
            # single malformed subscription would stop every tenant being
            # billed until somebody noticed the service was quiet.
            log.exception("billing run failed; will retry next pass")


async def billing_loop() -> None:
    """Wake, bill whatever has closed, sleep. Forever."""
    if not settings.billing_run_enabled:
        log.info("billing loop disabled (set BILLING_RUN_ENABLED=true "
                 "on exactly one deployment to turn it on)")
        return

    poll = max(60, int(settings.billing_poll_seconds))
    log.info("billing loop started, every %ds", poll)
    while True:
        try:
            await asyncio.to_thread(_once)
        except asyncio.CancelledError:
            log.info("billing loop stopped")
            raise
        except Exception:  # noqa: BLE001
            log.exception("billing loop error")
        await asyncio.sleep(poll)
