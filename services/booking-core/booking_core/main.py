"""Booking-core service FastAPI application."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator

from chirala_common.observability import install_observability
from chirala_common.config import refuse_unsafe_boot
from fastapi import FastAPI

from .channel_routes import channel_router
from .database import engine
from .public_routes import public_router
from .reaper import (
    channel_feed_loop, channel_provision_loop, channel_push_loop,
    hold_reaper_loop,
    block_cutoff_loop,
    occupancy_sweep_loop,
)
from .outbox_relay import outbox_relay_loop
from .settings import settings
from .blocks_routes import blocks_router
from .calendar_routes import calendar_router
from .rate_plan_calendar_routes import plan_calendar_router
from .change_routes import change_router
from .checkin_routes import checkin_router
from .formc_routes import formc_router
from .checkout_routes import checkout_router
from .account_routes import account_router
from .attribute_routes import attribute_router
from .ota_mapping_routes import ota_mapping_router
from .assign_routes import assign_router
from .detail_routes import detail_router
from .enquiry_routes import enquiry_router
from .directory_routes import directory_router
from .guestprofile_routes import guestprofile_router
from .resdetail_routes import resdetail_router
from .roomrack_routes import roomrack_router
from .estate_routes import estate_router
from .import_routes import import_router
from .history_routes import history_router
from .housekeeping_routes import housekeeping_router
from .workorder_routes import workorder_router
from .move_routes import move_router
from .noshow_routes import noshow_router
from .package_routes import package_router
from .rack_routes import rack_router
from .rate_routes import rate_router
from .rooms_routes import rooms_router
from .comp_routes import comp_router
from .group_routes import group_router
from .ota_action_routes import ota_router
from .rule_routes import rule_router
from .routes import router

log = logging.getLogger("uvicorn.error").getChild("booking-core")

@contextlib.asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    """Own the hold reaper for as long as the service is up.

    In-process rather than a cron container so the schedule cannot drift away
    from the code it runs. Safe to have more than one: each sweep locks the
    holds it takes with SKIP LOCKED, so two workers divide the work instead of
    fighting over it.
    """
    refuse_unsafe_boot(settings, "booking-core")
    # Said once, at start, because the difference between staging and
    # production Channex is the difference between test bookings and a
    # real guest's room being sold twice, and nothing else shows which.
    log.info("Channex API: %s (%s)", settings.channex_api_url,
             "enabled" if settings.channex_api_key else "no API key; integration off")
    tasks = []
    if settings.hold_reaper_enabled:
        tasks.append(asyncio.create_task(hold_reaper_loop()))
    # The outbound half of the channel integration. Separate task, because a
    # push failing must not stop holds expiring — one is a channel out of
    # date, the other is rooms permanently off sale.
    if settings.channel_push_enabled and settings.channex_api_key:
        tasks.append(asyncio.create_task(channel_push_loop()))
        tasks.append(asyncio.create_task(channel_feed_loop()))
    # Structural rather than numerical: this is what makes sure the far side
    # knows about every property and every room type in the first place. Its
    # own task again — a provisioning sweep stalling on one slow property must
    # not stop the others' rates going out.
    if settings.channel_provision_enabled and settings.channex_api_key:
        tasks.append(asyncio.create_task(channel_provision_loop()))
    # The transactional outbox's other half. Its own task for the same reason
    # as the two above: a broker outage must not stop holds expiring, and a
    # slow channel must not stop domain events reaching anything else that
    # listens for them.
    if settings.outbox_relay_enabled:
        tasks.append(asyncio.create_task(outbox_relay_loop()))
    # Occupancy pricing is the one kind of rule that goes stale on its own:
    # it was judged against the inventory of the moment it was published, and
    # rooms sell. Its own task again, so a slow re-price cannot hold up holds
    # expiring.
    if settings.occupancy_sweep_enabled:
        tasks.append(asyncio.create_task(occupancy_sweep_loop()))
    # A group block holds rooms off sale until its cut-off. Nobody is at a
    # desk at midnight to hand them back, and rooms held past their cut-off
    # are unsellable without anyone being able to see why.
    if settings.block_cutoff_sweep_enabled:
        tasks.append(asyncio.create_task(block_cutoff_loop()))
    try:
        yield
    finally:
        for task in tasks:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task


app = FastAPI(
    title="Chirala Bay PMS — Booking Core",
    version="0.1.0",
    description="Property inventory, reservations, holds, and room calendar.",
    lifespan=lifespan,
)


@app.get("/health", tags=["meta"])
def health() -> dict[str, str]:
    return {"status": "ok", "service": "booking-core"}


# Request ids, the shared log format at settings.log_level, and GET /ready.
# 503 when the database is unreachable. Redis and the background loops only
# DEGRADE it (200, "status": "degraded"): the rate limiter is Redis's one user
# here and fails open by design, and a loop with no cycle in 3x its interval
# is a stuck job to alert on, not a reason to pull every replica out of
# rotation. /health above stays liveness.
install_observability(app, service="booking-core", engine=engine,
                      log_level=settings.log_level,
                      redis_url=settings.redis_url, report_heartbeats=True)


# Rooms/room-types/amenities routes come first: their paths are more specific
# than the legacy /room-types collection in `router`.
app.include_router(public_router)
app.include_router(channel_router)
app.include_router(ota_mapping_router)
app.include_router(blocks_router)
app.include_router(calendar_router)
app.include_router(plan_calendar_router)
app.include_router(change_router)
app.include_router(checkin_router)
app.include_router(formc_router)
app.include_router(checkout_router)
app.include_router(account_router)
app.include_router(attribute_router)
app.include_router(assign_router)
app.include_router(detail_router)
app.include_router(enquiry_router)
app.include_router(directory_router)
app.include_router(guestprofile_router)
app.include_router(resdetail_router)
app.include_router(roomrack_router)
app.include_router(estate_router)
app.include_router(import_router)
app.include_router(history_router)
app.include_router(housekeeping_router)
app.include_router(workorder_router)
app.include_router(move_router)
app.include_router(noshow_router)
app.include_router(package_router)
app.include_router(rack_router)
app.include_router(rate_router)
app.include_router(rule_router)
app.include_router(comp_router)
app.include_router(group_router)
app.include_router(ota_router)
app.include_router(rooms_router)
app.include_router(router)
