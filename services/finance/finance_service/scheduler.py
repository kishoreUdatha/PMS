"""Runs the night audit without anybody being awake to press a button.

The audit existed but nothing ever called it, which is the same as not having
one: a day that is never closed never accrues its revenue.

**It catches up rather than only doing tonight.** If the service was down, or
the property was quiet for a week, every unclosed day before today is closed in
order, oldest first. Closing only the latest day would leave the days behind it
open forever, and their revenue never posted.

**It waits for the property's own night.** A day is closed once the property's
local clock is past the audit hour and the day is genuinely behind it -- never
the day in progress, because guests are still checking in. Properties carry
their own timezone, so a group spanning two of them closes each at its own 3am.

Safety comes from the audit itself, not from this loop: charges are idempotent
per room per night and a partial unique index allows one completed run per day.
Two schedulers, a manual run, and a retry can all collide without double
billing. That matters because there is no leader election here -- a second
replica runs this loop too.
"""

from __future__ import annotations

from chirala_common.db import bind_tenant_context, system_context

import asyncio
import logging
import uuid
from datetime import date, datetime, timedelta
from typing import NamedTuple
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import text

from .database import SessionFactory
from .settings import settings
from .mail import send_night_audit_report
from .night_audit import NightAuditError, run_night_audit

# A child of uvicorn's logger so it inherits the handlers the server
# already configured. A plain module logger propagates to a bare root
# and prints nothing -- which for the one part of the system that runs
# with nobody watching is the worst possible default.
log = logging.getLogger("uvicorn.error").getChild("night_audit")

#: Both come from settings so the schedule can be moved without a code change;
#: the names stay because callers and tests read them.
POLL_SECONDS = settings.night_audit_poll_seconds
AUDIT_HOUR = settings.night_audit_hour

#: Most days to close in one pass. A property idle for a year should not tie
#: the loop up in a single sweep; the next pass picks up where it left off.
MAX_CATCH_UP = 30


def _local_today(tz_name: str | None) -> date:
    """Today where the property is, not where the server is."""
    try:
        tz = ZoneInfo(tz_name or "Asia/Kolkata")
    except (ZoneInfoNotFoundError, ValueError):
        tz = ZoneInfo("Asia/Kolkata")
    return datetime.now(tz).date()


def _local_now(tz_name: str | None) -> datetime:
    try:
        tz = ZoneInfo(tz_name or "Asia/Kolkata")
    except (ZoneInfoNotFoundError, ValueError):
        tz = ZoneInfo("Asia/Kolkata")
    return datetime.now(tz)


#: Each property with the hour it chose, or NULL to follow the deployment
#: default. LEFT JOIN, because a tenant that has never expressed a preference
#: should not need a settings row to be audited.
_PROPERTIES_SQL = """
    SELECT p.id, p.organization_id, p.timezone, p.name,
           s.audit_hour
    FROM iam.properties p
    LEFT JOIN finance.night_audit_settings s ON s.property_id = p.id
    WHERE p.status = 'active'
"""


def jitter_minute(property_id: uuid.UUID) -> int:
    """A stable minute past the hour for this property.

    Per-property hours *permit* load spreading; they do not cause it. A
    thousand tenants that all leave the setting alone still fire at the same
    instant, and the default is the case that matters. Deriving a minute from
    the property id spreads them whether or not anybody configures anything.

    Stable, so a property closes at the same time every night rather than
    wandering, and so two replicas compute the same answer.

    The spread is only as fine as the poll interval: at the default 15 minutes
    this yields four batches an hour, not sixty. Lower the interval and the
    spread sharpens on its own.
    """
    return property_id.int % 60

#: The oldest day still open. A property that has never run one has no row at
#: all, so fall back to the first night anybody actually slept there -- closing
#: from the beginning of time would post nothing and take all day doing it.
_OLDEST_OPEN_SQL = """
    SELECT coalesce(
        (SELECT min(business_date) FROM finance.business_days
          WHERE property_id = :p AND status <> 'closed'),
        (SELECT min(arrival_date) FROM booking.reservation_units
          WHERE property_id = :p)
    ) AS d
"""


def due_days(
    session,
    property_id,
    tz_name: str,
    audit_hour: int | None = None,
) -> list[date]:
    """Business dates that are finished and still open, oldest first.

    ``audit_hour`` is the property's own choice; None follows the deployment
    default.
    """
    hour = AUDIT_HOUR if audit_hour is None else audit_hour
    minute = jitter_minute(property_id)

    now = _local_now(tz_name)
    today = now.date()
    # The day in progress is never closed; nor is yesterday until this
    # property's own moment has passed, so a 00:30 restart does not close a
    # day early.
    started = (now.hour, now.minute) >= (hour, minute)
    latest_closeable = today - timedelta(days=1 if started else 2)

    oldest = session.execute(
        text(_OLDEST_OPEN_SQL), {"p": property_id}
    ).scalar()
    if oldest is None or oldest > latest_closeable:
        return []
    span = (latest_closeable - oldest).days + 1
    return [oldest + timedelta(days=i) for i in range(min(span, MAX_CATCH_UP))]


class SweepResult(NamedTuple):
    """What one sweep found, so the loop can say something either way.

    ``run_once`` used to return only the number of days it closed, and the
    loop logged only when that was non-zero. A sweep that found nothing and a
    loop that had stopped running therefore produced identical logs -- namely
    none -- which is how this scheduler sat idle for eleven hours with two
    properties overdue and nothing anywhere saying so.
    """

    properties: int = 0
    due: int = 0
    closed: int = 0
    failed: int = 0

    def __str__(self) -> str:
        return ("%d properties, %d day(s) due, %d closed, %d failed"
                % (self.properties, self.due, self.closed, self.failed))


def run_once() -> SweepResult:
    """One sweep over every active property."""
    closed = 0
    due_total = 0
    failed = 0
    with SessionFactory() as session:
        # Listing every tenant's properties is the one cross-tenant step; each
        # audit below runs as its own property's tenant.
        system_context(session, reason="night audit sweep: list active properties")
        properties = session.execute(text(_PROPERTIES_SQL)).mappings().all()

    for prop in properties:
        try:
            with SessionFactory() as session:
                bind_tenant_context(session, organization_id=prop["organization_id"],
                                    property_id=prop["id"], is_service=True)
                days = due_days(session, prop["id"], prop["timezone"],
                                prop["audit_hour"])
            due_total += len(days)
            if days:
                # Said out loud before the work, so a sweep that then hangs
                # still leaves behind what it was about to do.
                log.info("night audit: %s has %d day(s) due (%s)",
                         prop["name"], len(days),
                         ", ".join(str(d) for d in days))
            for day in days:
                # A session per day: one property's bad day must not roll back
                # another's good one, and a partial catch-up is still progress.
                with SessionFactory() as session:
                    bind_tenant_context(session, organization_id=prop["organization_id"],
                                        property_id=prop["id"], is_service=True)
                    try:
                        res = run_night_audit(
                            session,
                            organization_id=prop["organization_id"],
                            property_id=prop["id"],
                            business_date=day,
                            # Unattended, so there is nobody to ask. An open
                            # till stops a person, who can go and count it;
                            # stopping this loop would only leave the day open
                            # and the night's revenue unposted, and by morning
                            # there would be two such days. It proceeds and
                            # says so -- the run records whose drawer it closed
                            # over, and the report shows the exception rather
                            # than a clean close.
                            allow_open_shifts=True,
                        )
                        session.commit()
                    except NightAuditError as exc:
                        session.rollback()
                        failed += 1
                        log.warning(
                            "night audit %s %s: %s", prop["name"], day, exc)
                        break
                    except Exception:
                        session.rollback()
                        failed += 1
                        log.exception(
                            "night audit failed for %s on %s",
                            prop["name"], day)
                        break
                closed += 1

                # After the commit, never inside it. The day is already sealed
                # by the time this runs, so an unreachable mail server cannot
                # turn a completed audit into a failed one -- and nobody is
                # told about a close that later rolled back.
                with SessionFactory() as session:
                    bind_tenant_context(session, organization_id=prop["organization_id"],
                                        property_id=prop["id"], is_service=True)
                    sent = send_night_audit_report(
                        session,
                        property_id=prop["id"],
                        property_name=prop["name"],
                        business_date=day,
                        rooms_charged=res.rooms_charged,
                        amount_charged=res.amount_charged,
                        no_shows=len(res.no_shows),
                        overstays=len(res.overstays),
                        open_shifts=res.open_shifts,
                        next_business_date=res.next_business_date,
                        warnings=res.warnings,
                    )
                if sent:
                    log.info("summary for %s emailed to %s", day, ", ".join(sent))

                log.info(
                    "closed %s for %s: %d rooms, %s charged, %d no-shows, "
                    "%d overstays, +%d inventory days%s",
                    day, prop["name"], res.rooms_charged,
                    res.amount_charged, len(res.no_shows),
                    len(res.overstays), res.horizon_days_added,
                    f" — OVER {res.open_shifts} OPEN TILL(S)"
                    if res.open_shifts else "",
                )
        except Exception:
            failed += 1
            log.exception("night audit sweep failed for %s", prop["name"])
    return SweepResult(properties=len(properties), due=due_total,
                       closed=closed, failed=failed)


#: How long one sweep may take before the loop stops waiting for it.
#:
#: This is the whole reason the scheduler could stop. ``asyncio.to_thread``
#: hands the sweep to a worker thread and awaits it; if that thread blocks --
#: on a connection pool with nothing free, on an SMTP server that accepts the
#: socket and never answers -- the await never returns, and the ``while True``
#: below never comes round again. The loop is still "alive" in every sense a
#: health check can see, and it will never audit anything again.
#:
#: Generously above a real sweep (four properties take well under a second,
#: and a thirty-day catch-up on a busy property is still minutes) so a timeout
#: means something is genuinely wrong rather than merely slow.
SWEEP_TIMEOUT_SECONDS = 600

#: Sweeps between heartbeats when there is nothing to do. At the default
#: 900s poll this is one line an hour: enough that silence in the log is
#: unambiguous evidence of a stopped scheduler, rare enough to read.
HEARTBEAT_EVERY = 4


async def night_audit_loop() -> None:
    """Poll forever. Cancelled on shutdown.

    Two rules, both learned from this loop going quiet for eleven hours while
    two properties sat a day behind:

    **A sweep is never waited on indefinitely.** A hung thread is abandoned,
    logged as an error, and the next poll happens anyway. One stuck sweep must
    not become a permanently stopped scheduler -- the abandoned thread is
    leaked, which is the lesser harm and is visible in the log.

    **Silence always means stopped, never idle.** Every sweep that finds work
    says so, and an idle scheduler still speaks once an hour. Before this,
    "nothing was due" and "nothing is running" looked exactly alike.
    """
    log.info(
        "night audit scheduler started (every %ds, from %02d:00 local, "
        "spread across the hour per property, %ds sweep timeout)",
        POLL_SECONDS, AUDIT_HOUR, SWEEP_TIMEOUT_SECONDS)
    sweeps = 0
    quiet = 0
    while True:
        sweeps += 1
        try:
            # The audit is blocking SQL; keep it off the event loop so health
            # checks and requests are still served while it runs.
            result = await asyncio.wait_for(
                asyncio.to_thread(run_once), timeout=SWEEP_TIMEOUT_SECONDS)
            if result.closed or result.failed:
                log.info("night audit sweep #%d: %s", sweeps, result)
                quiet = 0
            elif result.due:
                # Days were due and none closed or failed: the sweep decided
                # there was work and did none of it. That should be impossible
                # and is worth an alarm rather than a shrug.
                log.warning(
                    "night audit sweep #%d: %s -- due days were not acted on",
                    sweeps, result)
                quiet = 0
            else:
                quiet += 1
                if quiet % HEARTBEAT_EVERY == 1 or HEARTBEAT_EVERY == 1:
                    log.info("night audit sweep #%d: %s", sweeps, result)
        except asyncio.TimeoutError:
            # The thread is still running somewhere and cannot be killed. Say
            # so plainly: a leaked worker is a real cost and somebody should
            # know why the count is climbing.
            log.error(
                "night audit sweep #%d exceeded %ds and was abandoned; the "
                "worker thread is leaked and the loop is continuing. Look for "
                "a blocked database connection or an unresponsive mail server.",
                sweeps, SWEEP_TIMEOUT_SECONDS)
        except asyncio.CancelledError:
            log.info("night audit scheduler stopped after %d sweep(s)", sweeps)
            raise
        except Exception:
            log.exception("night audit sweep #%d raised", sweeps)
        await asyncio.sleep(POLL_SECONDS)
