"""Audit Log routes (US-043, mockup 043).

Read-only views over iam.audit_events, populated by every write across the
Administration (and other) screens. RBAC: audit.view. Risk level is derived
from the action code.
"""

from __future__ import annotations

import uuid

from chirala_common.routing import TransactionalRoute
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import text
from sqlalchemy.orm import Session

from . import schemas
from .authz import Caller, require_org_permission
from .database import get_session

audit_router = APIRouter(
    prefix="/audit", tags=["audit"], route_class=TransactionalRoute
)


def _risk(action: str) -> str:
    a = action.lower()
    if "permission" in a or a.startswith("role.") or "access" in a:
        return "Permission Changed"
    if a.endswith(".view") or "read" in a or a.endswith("activity"):
        return "Sensitive Data Viewed"
    if "export" in a:
        return "Export"
    if "refund" in a:
        return "Refund"
    if "reject" in a or "deactivate" in a or "revoke" in a:
        return "Elevated"
    return "Normal"


def _summary(action: str, has_before: bool, has_after: bool) -> str:
    if has_before and has_after:
        return "Changed (before/after captured)"
    if has_after:
        return "Created / recorded"
    return action.replace(".", " ").title()


@audit_router.get("/events", response_model=list[schemas.AuditEventOut])
def list_audit_events(
    action: str | None = None,
    entity_type: str | None = None,
    entity_id: str | None = None,
    query: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    db: Session = Depends(get_session),
    caller: Caller = Depends(require_org_permission("audit", "view")),
):
    action = action or None
    entity_type = entity_type or None
    query = query or None
    # A comma-separated set, because one *thing* in the system is often several
    # rows: a reservation's trail is the reservation plus its instalments.
    entity_ids = [i.strip() for i in (entity_id or "").split(",") if i.strip()]
    sql = """
        SELECT id, to_char(occurred_at, 'DD Mon YYYY HH24:MI') AS occurred_at,
               actor_subject, action, entity_type, entity_id, reason,
               correlation_id,
               (redacted_before IS NOT NULL) AS has_before,
               (redacted_after IS NOT NULL) AS has_after
        FROM iam.audit_events
        WHERE (organization_id = :org OR organization_id IS NULL)
    """
    params: dict = {"org": caller.organization_id}
    if action:
        sql += " AND action = :action"
        params["action"] = action
    if entity_type:
        sql += " AND entity_type = :et"
        params["et"] = entity_type
    if entity_ids:
        sql += " AND entity_id = ANY(:eids)"
        params["eids"] = entity_ids
    if query:
        sql += " AND (action ILIKE :q OR actor_subject ILIKE :q OR entity_id ILIKE :q)"
        params["q"] = f"%{query}%"
    if date_from:
        sql += " AND occurred_at >= :df"
        params["df"] = date_from
    if date_to:
        sql += " AND occurred_at <= (:dt::date + 1)"
        params["dt"] = date_to
    sql += " ORDER BY occurred_at DESC LIMIT 250"
    rows = db.execute(text(sql), params).mappings().all()
    return [
        schemas.AuditEventOut(
            id=r["id"], occurred_at=r["occurred_at"], actor_subject=r["actor_subject"],
            action=r["action"], entity_type=r["entity_type"], entity_id=r["entity_id"],
            summary=_summary(r["action"], r["has_before"], r["has_after"]),
            risk=_risk(r["action"]), result="Success",
            correlation_id=r["correlation_id"],
        )
        for r in rows
    ]


@audit_router.get("/events/{event_id}", response_model=schemas.AuditEventDetail)
def audit_event_detail(
    event_id: uuid.UUID,
    db: Session = Depends(get_session),
    caller: Caller = Depends(require_org_permission("audit", "view")),
):
    r = db.execute(
        text(
            """
            SELECT id, to_char(occurred_at, 'DD Mon YYYY, HH24:MI') AS occurred_at,
                   actor_subject, action, entity_type, entity_id, reason,
                   correlation_id, redacted_before, redacted_after, organization_id
            FROM iam.audit_events WHERE id = :id
            """
        ),
        {"id": event_id},
    ).mappings().first()
    if r is None or (r["organization_id"] not in (None, caller.organization_id)):
        raise HTTPException(status_code=404, detail="Audit event not found")
    return schemas.AuditEventDetail(
        id=r["id"], occurred_at=r["occurred_at"], actor_subject=r["actor_subject"],
        action=r["action"], entity_type=r["entity_type"], entity_id=r["entity_id"],
        reason=r["reason"], correlation_id=r["correlation_id"],
        risk=_risk(r["action"]), result="Success",
        before=r["redacted_before"], after=r["redacted_after"],
    )


@audit_router.get("/actions", response_model=list[str])
def audit_actions(
    db: Session = Depends(get_session),
    caller: Caller = Depends(require_org_permission("audit", "view")),
):
    rows = db.execute(
        text(
            "SELECT DISTINCT action FROM iam.audit_events "
            "WHERE organization_id = :org OR organization_id IS NULL ORDER BY action"
        ),
        {"org": caller.organization_id},
    ).scalars().all()
    return list(rows)
