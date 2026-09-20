"""Booking-core service settings."""

from __future__ import annotations

from chirala_common.config import BaseServiceSettings


class BookingSettings(BaseServiceSettings):
    #: Drain integration.outbox_events onto the message bus.
    #:
    #: Off by default and deliberately so: it publishes this deployment's
    #: domain events onto a shared broker, and a laptop pointed at a shared
    #: database should not start emitting them because it happened to boot.
    #: Safe to enable on more than one replica -- each sweep claims its batch
    #: with SKIP LOCKED, so they divide the work.
    outbox_relay_enabled: bool = False
    #: How often to sweep. Seconds, not minutes: these events are what other
    #: services react to, and a minute's lag is a minute of stale channel
    #: inventory.
    outbox_poll_seconds: int = 5
    #: Events per sweep. A full batch makes the loop go round again
    #: immediately, so a backlog drains at speed rather than 200 per poll.
    outbox_batch_size: int = 200

    #: How far ahead rooms are put on sale, in days. Every room type keeps an
    #: inventory row per night inside this window; a night with no row is a
    #: night with nothing to sell, so this is effectively the booking horizon.
    #: Just over a year, so next season is always bookable.
    inventory_horizon_days: int = 400

    booking_database_url: str = (
        "postgresql+psycopg://pms:pms_dev_password@localhost:5432/chirala_pms"
    )
    #: The schema owner, for migrations and grants only. The app itself uses
    #: the URL above, which in a deployment is the runtime login that owns
    #: nothing and cannot bypass row-level security. Empty means "use the URL
    #: above", which keeps a service run straight from the host working.
    booking_migration_database_url: str = ""
    booking_port: int = 8002
    # Deliberate overbooking allowance; zero initially per schema §4.
    overbooking_allowance: int = 0
    # Default hold time-to-live in minutes.
    hold_ttl_minutes: int = 15
    #: Whether this process returns abandoned holds to inventory. On by
    #: default: a hold nothing expires is a room permanently off sale.
    hold_reaper_enabled: bool = True
    #: How often to sweep. A hold lives for minutes, so this has to be minutes
    #: too -- a nightly pass would leave rooms unsellable all day.
    hold_reaper_seconds: int = 120

    # --- the public booking API ---------------------------------------
    redis_url: str = "redis://localhost:6379/0"
    public_rate_limit_enabled: bool = True
    #: Requests per window per caller. Generous for a person browsing dates,
    #: tight for anything walking a year of the rate calendar.
    public_rate_limit: int = 60
    public_rate_limit_seconds: int = 60
    #: Peers whose X-Forwarded-For may be believed. Only our own gateway; a
    #: header from anyone else is a claim, not evidence.
    trusted_proxies: str = ""
    # --- channel manager (Channex) ------------------------------------
    #: Where Channex lives. Staging by default: a deployment that has not been
    #: told otherwise must not be talking to a production channel manager.
    channex_api_url: str = "https://staging.channex.io/api/v1"
    #: Our API key, for pulling a booking revision and acknowledging it.
    #: Unset, the integration is simply off — a receiver that accepts webhooks
    #: it cannot follow up on would collect bookings it can never fetch.
    channex_api_key: str = ""
    #: The secret we ask Channex to send back on every webhook. Channex does
    #: not sign its payloads, so this header is the whole of the check: unset,
    #: the endpoint refuses everything rather than accepting anonymous POSTs
    #: that create bookings.
    channex_webhook_secret: str = ""
    #: Whether this process pushes rates and availability out to the channels.
    #: On by default: a channel nothing pushes to is a channel selling stale
    #: prices, which is worse than one that is off.
    channel_push_enabled: bool = True
    #: Re-price dates whose rules depend on how full they are. Off
    #: turns occupancy yield into a manual Publish, which is a
    #: reasonable choice for a property that wants to see every move.
    occupancy_sweep_enabled: bool = True
    occupancy_sweep_seconds: int = 600
    #: Group blocks whose cut-off has arrived go back on sale. Hourly rather
    #: than by the minute: a cut-off is a date, so being an hour late costs
    #: nothing, and the sweep writes inventory that channels then re-push.
    block_cutoff_sweep_enabled: bool = True
    block_cutoff_sweep_seconds: int = 3600
    #: How often. Channex asks for batching rather than a call per change —
    #: their guide suggests thirty to sixty seconds per property — because a
    #: hotel editing a week of rates should reach the channel as one push.
    channel_push_seconds: int = 60

    #: Whether this process keeps every property in step with the channel
    #: manager by itself. On by default: provisioning on go-live and a button
    #: for everything after works for three properties and quietly fails for a
    #: hundred — a room type added on Tuesday is unsellable online until
    #: somebody remembers a hotel they do not work at.
    channel_provision_enabled: bool = True
    #: How often to sweep. Far slower than the ARI push on purpose. Rates
    #: change hourly; room types and rate plans change a few times a year, and
    #: each property costs several calls to the far side to check.
    channel_provision_seconds: int = 900
    #: How many properties per sweep. A ceiling, not a target — it stops a
    #: first run against a large estate turning into a thousand API calls in
    #: one burst. The rest are picked up next pass, oldest first.
    channel_provision_batch: int = 25
    #: How long to leave a property alone after an attempt. The usual cause of
    #: a failure is a person's to fix (a room type with no rate plan), so
    #: retrying every quarter of an hour achieves nothing but load.
    channel_provision_retry_minutes: int = 60

    #: Where to open a payment intent when a guest pays for their own hold.
    #:
    #: Opening one means creating an order at the tenant's gateway, which is
    #: finance's job and needs their sealed credentials — this service has no
    #: business holding those. So it asks, over HTTP with the platform's own
    #: credential, exactly as finance asks this service to confirm a booking
    #: once the money arrives. The two halves of that conversation are
    #: symmetrical on purpose.
    finance_url: str = "http://localhost:8003"


settings = BookingSettings()
