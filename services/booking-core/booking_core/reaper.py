"""Give back the rooms that abandoned holds are sitting on.

A hold takes rooms off sale so a guest can finish booking without the last one
being sold from under them. It carries an ``expires_at`` for exactly that
reason — and nothing has ever acted on it. The column was there, the partial
index was there, and no code anywhere read either.

Today that costs nothing, because the only thing creating holds is a member of
staff who confirms seconds later. It stops being free the moment a booking
engine exists: most guests abandon at the payment step, and every one of them
would take a room out of inventory permanently. A hotel would slowly sell out
while standing empty, and nothing in the system would say why.

So this is a latent bug being fixed before the thing that triggers it is built,
rather than after.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import date

from chirala_common.db import system_context
from sqlalchemy import text
from sqlalchemy.orm import Session

from .database import SessionFactory
from .settings import settings

#: A child of uvicorn's logger so it inherits handlers the server configured.
#: A plain module logger propagates to a bare root and prints nothing, which
#: for a job that runs unattended is the worst possible default.
log = logging.getLogger("uvicorn.error").getChild("hold_reaper")

#: Expired holds, oldest first, locked so two workers cannot reap the same one.
#: SKIP LOCKED rather than waiting: a hold another process is already dealing
#: with is not this pass's problem, and blocking on it would stall the sweep.
_STALE_SQL = """
    SELECT h.id, h.reservation_id, h.property_id
    FROM booking.booking_holds h
    WHERE h.status = 'held' AND h.expires_at < now()
    ORDER BY h.expires_at
    LIMIT :lim
    FOR UPDATE SKIP LOCKED
"""

#: The nights each unit of the hold is holding. Half-open, as everywhere else:
#: the departure date is not a night.
_UNITS_SQL = """
    SELECT u.id, u.room_type_id, u.arrival_date, u.departure_date
    FROM booking.reservation_units u
    WHERE u.reservation_id = :res AND u.status = 'reserved'
"""


def expire_stale_holds(session: Session, *, limit: int = 200) -> int:
    """Release every hold whose time is up. Returns how many were released.

    The counter matters: a hold sits in ``held_units``, not ``reserved_units``,
    so this returns the rooms to sale by decrementing the one it actually
    occupies. Decrementing the wrong counter would take the rooms off sale
    permanently instead — the opposite of the bug being fixed.

    GREATEST(...,0) guards the floor. It should never bite; if a counter has
    already been returned by another path, driving it negative would make
    every future availability check wrong.
    """
    stale = session.execute(text(_STALE_SQL), {"lim": limit}).mappings().all()
    if not stale:
        return 0

    for hold in stale:
        units = session.execute(
            text(_UNITS_SQL), {"res": hold["reservation_id"]}
        ).mappings().all()

        for u in units:
            session.execute(
                text(
                    """
                    UPDATE booking.room_type_inventory_days
                       SET held_units = GREATEST(held_units - 1, 0)
                     WHERE property_id = :prop
                       AND room_type_id = :rt
                       AND stay_date >= :arr AND stay_date < :dep
                    """
                ),
                {"prop": hold["property_id"], "rt": u["room_type_id"],
                 "arr": u["arrival_date"], "dep": u["departure_date"]},
            )

        session.execute(
            text("UPDATE booking.reservation_units SET status = 'cancelled', "
                 "version = version + 1 "
                 "WHERE reservation_id = :res AND status = 'reserved'"),
            {"res": hold["reservation_id"]},
        )
        # Only a booking still merely held is cancelled. A confirmed one has a
        # 'converted' hold and is never selected here, but the guard says so
        # out loud rather than relying on that.
        session.execute(
            text("UPDATE booking.reservations SET status = 'cancelled', "
                 "version = version + 1 "
                 "WHERE id = :res AND status = 'held'"),
            {"res": hold["reservation_id"]},
        )
        session.execute(
            text("UPDATE booking.booking_holds SET status = 'expired', "
                 "updated_at = now() WHERE id = :id"),
            {"id": hold["id"]},
        )

    return len(stale)


def run_once() -> int:
    """One sweep, in its own transaction."""
    with SessionFactory() as session:
        try:
            # Holds expire whoever owns them; this sweep is one of the few jobs
            # above the tenant boundary by nature.
            system_context(session, reason="hold reaper: release expired holds")
            n = expire_stale_holds(session)
            session.commit()
        except Exception:
            session.rollback()
            log.exception("hold reaper sweep failed")
            return 0
    if n:
        log.info("released %d expired hold(s)", n)
    return n


async def hold_reaper_loop() -> None:
    """Poll forever. Cancelled on shutdown.

    Runs far more often than the night audit because it is answering a
    different question: a hold lives for minutes, so a nightly sweep would
    leave rooms off sale all day.
    """
    log.info("hold reaper started (every %ds, holds live %d min)",
             settings.hold_reaper_seconds, settings.hold_ttl_minutes)
    while True:
        try:
            # Blocking SQL; keep it off the event loop so requests are still
            # served while a large sweep runs.
            await asyncio.to_thread(run_once)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("hold reaper raised")
        await asyncio.sleep(settings.hold_reaper_seconds)


async def channel_push_loop() -> None:
    """Push rates and availability to every connected channel, forever.

    On a timer rather than on every change. Channex asks for batching — their
    guide suggests thirty to sixty seconds per property — because a hotel
    editing a week of rates produces a burst of changes that should reach the
    channel as one push, not fifty.

    The first sweep is delayed. Everything a push reads has to be there to read,
    and a container that starts pushing before migrations finish sends a year
    of zero availability to every OTA the hotel sells on.
    """
    from . import channel_push

    log.info("channel ARI push started (every %ds, %d day window)",
             settings.channel_push_seconds, channel_push.WINDOW_DAYS)
    await asyncio.sleep(30)
    while True:
        try:
            with SessionFactory() as session:
                try:
                    results = channel_push.push_all(session)
                    session.commit()
                except Exception:
                    session.rollback()
                    raise
            bad = [r for r in results if r["status"] != "ok"]
            if bad:
                log.warning("channel push: %d of %d connections not fully "
                            "sent", len(bad), len(results))
        except asyncio.CancelledError:
            raise
        except Exception:
            # Never let one failure end the loop. A channel that cannot be
            # reached this minute is reachable next minute, and a dead loop is
            # a hotel silently out of sync until somebody restarts it.
            log.exception("channel push sweep failed")
        await asyncio.sleep(settings.channel_push_seconds)


async def channel_provision_loop() -> None:
    """Keep every property in step with the channel manager, forever.

    The counterpart to the push loop, and a slower one. The push loop answers
    "are the numbers current"; this answers "does the far side know about this
    property, and all of its rooms, at all" — which changes rarely but breaks
    silently when it does. A room type added last Tuesday is simply missing
    from every OTA, and nothing anywhere says so.

    Only properties that need work are visited, so a settled estate costs one
    query per pass. Delayed start for the same reason as the push loop:
    nothing should talk to a channel manager before migrations have finished.
    """
    from . import channel_provision

    log.info("channel provisioning sweep started (every %ds, up to %d "
             "properties, retry after %dm)",
             settings.channel_provision_seconds,
             settings.channel_provision_batch,
             settings.channel_provision_retry_minutes)
    await asyncio.sleep(45)
    while True:
        try:
            # Blocking HTTP and SQL, and a batch of it — emphatically not on
            # the event loop, or the service stops answering requests for as
            # long as the channel manager takes to reply.
            results = await asyncio.to_thread(
                channel_provision.provision_all,
                SessionFactory,
                retry_minutes=settings.channel_provision_retry_minutes,
                limit=settings.channel_provision_batch,
            )
            if results:
                bad = [r for r in results if r.status != "ok"]
                log.info("channel provisioning: %d propert%s reconciled, "
                         "%d needing attention", len(results),
                         "y" if len(results) == 1 else "ies", len(bad))
                for r in bad:
                    # Each one named. A count tells somebody there is a
                    # problem; this tells them which hotel and what to do.
                    log.warning("channel provisioning %s for %s: %s",
                                r.status, r.property_id,
                                "; ".join(r.problems) or "no detail")
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("channel provisioning sweep failed")
        await asyncio.sleep(settings.channel_provision_seconds)


async def occupancy_sweep_loop() -> None:
    """Re-price dates whose rules depend on how full they are, forever.

    Every other kind of rule is as true tomorrow as it was today. An
    occupancy rule is not: it was judged against the inventory of the moment
    it was published, and rooms sell. On a timer rather than on every booking
    because a rate change fans out to every connected channel, and re-pushing
    a year of rates on each reservation would be a burst nobody needs -- the
    price being a few minutes behind the sale costs nothing.

    Delayed first sweep for the same reason as the channel push: a container
    that starts writing rates before migrations finish is writing them against
    a schema that is still moving.
    """
    from . import rate_publish

    log.info("occupancy yield sweep started (every %ds, %d day window)",
             settings.occupancy_sweep_seconds, rate_publish.SWEEP_DAYS)
    await asyncio.sleep(45)
    while True:
        try:
            with SessionFactory() as session:
                try:
                    results = rate_publish.sweep_occupancy_rules(session)
                    session.commit()
                except Exception:
                    session.rollback()
                    raise
            moved = sum(r.get("written", 0) + r.get("cleared", 0)
                        for r in results if r["status"] == "ok")
            bad = [r for r in results if r["status"] != "ok"]
            if moved or bad:
                log.info("occupancy sweep: %d propert(ies), %d night(s) "
                         "re-priced, %d failed",
                         len(results), moved, len(bad))
            for r in bad:
                log.warning("occupancy sweep failed for %s: %s",
                            r["property_id"], r.get("detail"))
        except asyncio.CancelledError:
            raise
        except Exception:
            # Never let one bad sweep end the loop: a property that is
            # mispriced for ten minutes is a problem, a property that stops
            # being re-priced at all and nobody notices is a worse one.
            log.exception("occupancy sweep errored; continuing")
        await asyncio.sleep(settings.occupancy_sweep_seconds)


async def block_cutoff_loop() -> None:
    """Put group-block rooms back on sale when their cut-off arrives, forever.

    A cut-off is the date a group agreed to stop holding what it has not
    taken. Nobody is at a desk at midnight to do it, and rooms held past their
    cut-off are rooms the hotel cannot sell and does not know it cannot sell --
    the failure is invisible, which is exactly why it needs a loop rather than
    a reminder.

    Idempotent by construction: releasing reads what each block still holds and
    zeroes it, so a second pass over an already-released block gives back
    nothing. Delayed first run for the same reason as every other sweep here --
    a container that starts moving inventory before migrations finish is
    moving it against a schema that is still changing.
    """
    from . import group_blocks

    log.info("group block cut-off sweep started (every %ds)",
             settings.block_cutoff_sweep_seconds)
    await asyncio.sleep(60)
    while True:
        try:
            with SessionFactory() as session:
                try:
                    results = group_blocks.sweep_cut_offs(
                        session, today=date.today())
                    session.commit()
                except Exception:
                    session.rollback()
                    raise
            done = [r for r in results if r["status"] == "ok"]
            bad = [r for r in results if r["status"] != "ok"]
            if done or bad:
                log.info("block cut-off sweep: %d released (%d rooms), %d failed",
                         len(done), sum(r.get("released", 0) for r in done),
                         len(bad))
            for r in bad:
                log.warning("block %s could not be released: %s",
                            r.get("code"), r.get("detail"))
        except Exception:
            log.exception("block cut-off sweep failed")
        await asyncio.sleep(settings.block_cutoff_sweep_seconds)
