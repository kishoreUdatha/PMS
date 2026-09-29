"""Drains the transactional outbox onto the message bus.

``chirala_common.outbox`` has had ``enqueue_event`` since the first migration
and its docstring has promised, all along, that "a separate relay/worker
publishes unpublished rows to NATS and marks them published". That relay was
never written. The result was 1,331 events accumulating over three days with
``published_at`` null on every one — twelve call sites faithfully recording
domain events into a queue nothing was reading.

**Publish first, mark second.** A crash between the two costs a duplicate; the
reverse order costs an event, and an event that vanishes is the precise
failure the outbox pattern exists to prevent. At-least-once, deliberately, and
consumers are expected to be idempotent.

**Oldest first, and no leader election.** Each sweep claims its batch with
``FOR UPDATE SKIP LOCKED``, so a second replica divides the backlog rather
than duplicating it — the same device the hold reaper uses.

**Off by default.** It publishes to a shared broker, and a developer's laptop
pointed at the staging database should not start emitting this deployment's
domain events because it happened to boot.
"""

from __future__ import annotations

import asyncio
import json
import logging

from chirala_common.db import system_context
from chirala_common.observability import heartbeats
from chirala_common.outbox import claim_batch, mark_published, subject_for

from .database import SessionFactory
from .settings import settings

# A child of uvicorn's logger so it inherits the handlers the server already
# configured. A plain module logger propagates to a bare root and prints
# nothing — which for a job that runs with nobody watching is the worst
# possible default.
log = logging.getLogger("uvicorn.error").getChild("outbox")


async def _connect():
    """A NATS connection, or None when the broker is unreachable.

    None rather than an exception: a broker that is down is a reason to retry
    next sweep, not a reason to take the service with it. The backlog is
    durable in Postgres and waits.
    """
    try:
        import nats
    except ImportError:  # pragma: no cover - the image always carries it
        log.error("nats-py is not installed; the outbox cannot be drained")
        return None
    try:
        return await nats.connect(
            settings.nats_url,
            name="chirala-outbox-relay",
            connect_timeout=5,
            max_reconnect_attempts=2,
        )
    except Exception:  # noqa: BLE001
        log.warning("outbox: broker unreachable at %s; will retry",
                    settings.nats_url)
        return None


async def drain_once(batch_size: int | None = None) -> int:
    """One sweep: claim, publish, mark. Returns how many were published.

    The database transaction stays open across the publish so the claimed rows
    remain locked — another relay cannot take the same batch while this one is
    still handing it to the broker.
    """
    size = batch_size or settings.outbox_batch_size
    nc = await _connect()
    if nc is None:
        return 0

    published = 0
    try:
        with SessionFactory() as session:
            # The RLS policy on this table admits reads and updates only in
            # system context: an outbox row names a tenant, and the relay acts
            # for none of them.
            system_context(session, reason="outbox relay: publish pending events")
            batch = claim_batch(session, limit=size)
            if not batch:
                return 0

            sent: list = []
            for row in batch:
                try:
                    await nc.publish(
                        subject_for(row["event_type"]),
                        json.dumps({
                            "id": str(row["id"]),
                            "aggregate_type": row["aggregate_type"],
                            "aggregate_id": str(row["aggregate_id"]),
                            "event_type": row["event_type"],
                            "occurred_at": row["occurred_at"].isoformat(),
                            "payload": row["payload"],
                        }, default=str).encode(),
                    )
                    sent.append(row["id"])
                except Exception:  # noqa: BLE001
                    # Stop at the first failure rather than skipping past it.
                    # Order matters to a consumer, and marking later events
                    # published while an earlier one is stuck would deliver a
                    # cancellation before the booking it cancels.
                    log.exception("outbox: publish failed on %s; stopping this "
                                  "sweep after %d", row["event_type"], len(sent))
                    break

            # Flush before marking: publish() only queues into the client's
            # buffer, so without this a connection that dies here would lose
            # events already recorded as delivered.
            await nc.flush(timeout=10)
            published = mark_published(session, sent)
            session.commit()
    finally:
        try:
            await nc.drain()
        except Exception:  # noqa: BLE001
            pass
    return published


async def outbox_relay_loop() -> None:
    """Wake, drain what is waiting, sleep. Forever."""
    if not settings.outbox_relay_enabled:
        log.info("outbox relay disabled (set OUTBOX_RELAY_ENABLED=true on the "
                 "deployment that should publish)")
        return

    poll = max(1, int(settings.outbox_poll_seconds))
    log.info("outbox relay started, every %ds, batches of %d",
             poll, settings.outbox_batch_size)
    while True:
        heartbeats.beat("outbox_relay", poll)
        try:
            n = await drain_once()
            if n:
                log.info("outbox: published %d event(s)", n)
                # A backlog drains at full speed rather than one batch per
                # poll: a full batch means there is probably more behind it.
                if n >= settings.outbox_batch_size:
                    continue
        except asyncio.CancelledError:
            log.info("outbox relay stopped")
            raise
        except Exception:  # noqa: BLE001
            # Logged and swallowed: one bad sweep must not kill the loop, or a
            # single malformed row would stop every event for every tenant
            # until somebody noticed the queue was growing again.
            log.exception("outbox relay sweep failed; retrying next poll")
        await asyncio.sleep(poll)
