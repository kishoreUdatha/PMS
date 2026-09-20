"""Transactional outbox helper (§10 ``integration.outbox_events``).

Business writes and the event record are inserted in the *same* transaction. A
separate relay/worker publishes unpublished rows to NATS and marks them
published. This gives at-least-once delivery without distributed transactions.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session


def enqueue_event(
    session: Session,
    *,
    aggregate_type: str,
    aggregate_id: str,
    event_type: str,
    payload: dict[str, Any],
) -> uuid.UUID:
    """Insert an outbox row inside the caller's open transaction.

    Must be called before the transaction commits so the event and the business
    change are atomic.
    """
    event_id = uuid.uuid4()
    session.execute(
        text(
            """
            INSERT INTO integration.outbox_events
                (id, aggregate_type, aggregate_id, event_type, payload, occurred_at)
            VALUES
                (:id, :aggregate_type, :aggregate_id, :event_type,
                 CAST(:payload AS jsonb), now())
            """
        ),
        {
            "id": event_id,
            "aggregate_type": aggregate_type,
            "aggregate_id": aggregate_id,
            "event_type": event_type,
            "payload": json.dumps(payload),
        },
    )
    return event_id


def claim_batch(session: Session, *, limit: int = 200) -> list[dict[str, Any]]:
    """Take the oldest unpublished events, locking them against other relays.

    ``FOR UPDATE SKIP LOCKED`` is what makes a second relay safe: two workers
    divide the backlog rather than fighting over the same rows, which is the
    same device the hold reaper uses next door.

    Oldest first, always. Events describe things that happened in an order,
    and a consumer that receives a cancellation before the booking it cancels
    has been handed nonsense.

    The caller must already be in system context -- the RLS policy on this
    table admits reads and updates only there, deliberately, because an outbox
    row names a tenant and the relay is not acting for one.
    """
    rows = session.execute(
        text(
            """
            SELECT id, aggregate_type, aggregate_id, event_type, payload,
                   occurred_at
            FROM integration.outbox_events
            WHERE published_at IS NULL
            ORDER BY occurred_at, id
            LIMIT :lim
            FOR UPDATE SKIP LOCKED
            """
        ),
        {"lim": limit},
    ).mappings().all()
    return [dict(r) for r in rows]


def mark_published(session: Session, ids: list[uuid.UUID]) -> int:
    """Stamp the rows a relay has successfully handed to the broker.

    Only ever called for events the broker accepted. Marking first and
    publishing afterwards would turn a broker outage into silent data loss,
    which is the exact failure the outbox pattern exists to prevent -- so the
    order is publish, then mark, and a crash in between costs a duplicate
    rather than a disappearance. At-least-once, by construction.
    """
    if not ids:
        return 0
    return session.execute(
        text("UPDATE integration.outbox_events SET published_at = now() "
             "WHERE id = ANY(:ids) AND published_at IS NULL"),
        {"ids": ids},
    ).rowcount


def subject_for(event_type: str) -> str:
    """The broker subject an event is published on.

    ``finance.payment_captured`` becomes ``chirala.finance.payment_captured``.
    Prefixed so a subscriber can take ``chirala.finance.>`` and nothing else
    on the bus, and so this system's traffic is distinguishable from anything
    else sharing the broker.
    """
    return f"chirala.{event_type}"
