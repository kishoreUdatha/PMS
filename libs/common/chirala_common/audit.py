"""Append-only audit logging (schema §10).

Shared by every service so the trail is uniform no matter which service made
the change. Records who did what to which entity, with before/after snapshots,
reason and a correlation id — written in the same transaction as the change, so
the audit trail can never diverge from the data. Secrets and sensitive fields
must be redacted by the caller before passing snapshots here.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session


def record_audit(
    session: Session,
    *,
    action: str,
    entity_type: str,
    entity_id: str | None,
    organization_id: uuid.UUID | None = None,
    property_id: uuid.UUID | None = None,
    actor_subject: str | None = None,
    before: dict[str, Any] | None = None,
    after: dict[str, Any] | None = None,
    reason: str | None = None,
    correlation_id: str | None = None,
) -> uuid.UUID:
    """Insert an audit_events row inside the caller's open transaction."""
    event_id = uuid.uuid4()
    session.execute(
        text(
            """
            INSERT INTO iam.audit_events
                (id, organization_id, property_id, actor_subject, action,
                 entity_type, entity_id, redacted_before, redacted_after, reason,
                 correlation_id, occurred_at)
            VALUES
                (:id, :org, :prop, :actor, :action, :etype, :eid,
                 CAST(:before AS jsonb), CAST(:after AS jsonb), :reason,
                 :corr, now())
            """
        ),
        {
            "id": event_id,
            "org": organization_id,
            "prop": property_id,
            "actor": actor_subject,
            "action": action,
            "etype": entity_type,
            "eid": entity_id,
            "before": json.dumps(before) if before is not None else None,
            "after": json.dumps(after) if after is not None else None,
            "reason": reason,
            "corr": correlation_id,
        },
    )
    return event_id
