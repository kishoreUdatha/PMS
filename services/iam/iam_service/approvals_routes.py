"""Approval Limits and Queue routes (US-042, mockup 042).

Lists pending approval requests, exposes policies/rules, and records approve/
reject decisions (append-only), honouring prohibit-self-approval. RBAC:
approval.view / approval.decide. All decisions audited.
"""

from __future__ import annotations

import uuid

from chirala_common.approvals import request_approval
from chirala_common.routing import TransactionalRoute
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import text
from sqlalchemy.orm import Session

from . import schemas
from .audit import record_audit
from .authz import Caller, require_org_permission, require_property_permission
from .database import get_session

approvals_router = APIRouter(
    prefix="/approvals", tags=["approvals"], route_class=TransactionalRoute
)


def _due_in(due_at) -> str | None:
    if due_at is None:
        return None
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc)
    delta = due_at - now
    secs = int(delta.total_seconds())
    if secs <= 0:
        return "overdue"
    h, m = divmod(secs // 60, 60)
    return f"{h}h {m}m" if h else f"{m}m"


@approvals_router.get("/queue", response_model=list[schemas.ApprovalRequestOut])
def approval_queue(
    category: str | None = None,
    db: Session = Depends(get_session),
    caller: Caller = Depends(require_org_permission("approval", "view")),
):
    category = category or None
    sql = """
        SELECT id, category, title, entity_ref, guest_name, requested_by_name,
               requested_by_role, policy_rule_text, amount, amount_context, status,
               to_char(due_at, 'DD Mon YYYY HH24:MI') AS due_at, due_at AS due_raw
        FROM iam.approval_requests
        WHERE organization_id = :org AND status = 'pending'
    """
    params: dict = {"org": caller.organization_id}
    if category:
        sql += " AND category = :cat"
        params["cat"] = category
    sql += " ORDER BY due_raw ASC"
    rows = db.execute(text(sql), params).mappings().all()
    return [
        schemas.ApprovalRequestOut(
            id=r["id"], category=r["category"], title=r["title"],
            entity_ref=r["entity_ref"], guest_name=r["guest_name"],
            requested_by_name=r["requested_by_name"],
            requested_by_role=r["requested_by_role"],
            policy_rule_text=r["policy_rule_text"], amount=r["amount"],
            amount_context=r["amount_context"], status=r["status"],
            due_at=r["due_at"], due_in=_due_in(r["due_raw"]),
        )
        for r in rows
    ]


@approvals_router.get("/policies", response_model=list[schemas.ApprovalPolicyOut])
def approval_policies(
    category: str | None = None,
    db: Session = Depends(get_session),
    caller: Caller = Depends(require_org_permission("approval", "view")),
):
    category = category or None
    sql = """
        SELECT id, name, category, applies_to, initiator_roles, approver_roles,
               threshold_value, threshold_unit, two_level, prohibit_self_approval
        FROM iam.approval_policies
        WHERE organization_id = :org AND active = true
    """
    params: dict = {"org": caller.organization_id}
    if category:
        sql += " AND category = :cat"
        params["cat"] = category
    sql += " ORDER BY name"
    rows = db.execute(text(sql), params).mappings().all()
    return [schemas.ApprovalPolicyOut(**r) for r in rows]


def _request_or_404(db: Session, caller: Caller, request_id: uuid.UUID):
    row = db.execute(
        text(
            """
            SELECT ar.*, ap.prohibit_self_approval
            FROM iam.approval_requests ar
            LEFT JOIN iam.approval_policies ap
              ON ap.organization_id = ar.organization_id AND ap.category = ar.category
                 AND ap.active = true
            WHERE ar.id = :id AND ar.organization_id = :org
            LIMIT 1
            """
        ),
        {"id": request_id, "org": caller.organization_id},
    ).mappings().first()
    if row is None:
        raise HTTPException(status_code=404, detail="Approval request not found")
    return row


def _decide(
    db: Session, caller: Caller, request_id: uuid.UUID, decision: str, comment: str | None
):
    req = _request_or_404(db, caller, request_id)
    if req["status"] != "pending":
        raise HTTPException(status_code=409, detail=f"Request already {req['status']}")
    # Prohibit self-approval where the policy requires it.
    if (
        decision == "approved"
        and req.get("prohibit_self_approval")
        and req.get("requested_by_subject") == caller.subject
    ):
        raise HTTPException(
            status_code=409, detail="Initiator cannot approve their own request"
        )

    db.execute(
        text(
            """
            INSERT INTO iam.approval_decisions
                (id, request_id, approver_subject, decision, comment)
            VALUES (gen_random_uuid(), :rid, :sub, :dec, :cmt)
            """
        ),
        {"rid": request_id, "sub": caller.subject, "dec": decision, "cmt": comment},
    )
    db.execute(
        text(
            """
            UPDATE iam.approval_requests
            SET status = :st, decided_at = now(), decided_by = :sub
            WHERE id = :rid
            """
        ),
        {"st": decision, "sub": caller.subject, "rid": request_id},
    )
    record_audit(
        db,
        action=f"approval.{decision}",
        entity_type="approval_request",
        entity_id=str(request_id),
        organization_id=caller.organization_id,
        actor_subject=caller.subject,
        after={"decision": decision, "title": req["title"], "amount": str(req["amount"])},
        reason=comment,
    )
    return {"id": str(request_id), "status": decision}


@approvals_router.post("/{request_id}/approve")
def approve(
    request_id: uuid.UUID,
    body: schemas.ApprovalDecisionIn,
    db: Session = Depends(get_session),
    caller: Caller = Depends(require_org_permission("approval", "decide")),
):
    return _decide(db, caller, request_id, "approved", body.comment)


@approvals_router.post("/{request_id}/reject")
def reject(
    request_id: uuid.UUID,
    body: schemas.ApprovalDecisionIn,
    db: Session = Depends(get_session),
    caller: Caller = Depends(require_org_permission("approval", "decide")),
):
    return _decide(db, caller, request_id, "rejected", body.comment)


@approvals_router.get("/history", response_model=list[schemas.ApprovalHistoryOut])
def approval_history(
    db: Session = Depends(get_session),
    caller: Caller = Depends(require_org_permission("approval", "view")),
):
    rows = db.execute(
        text(
            """
            SELECT id, category, title, entity_ref, amount, status, decided_by,
                   to_char(decided_at, 'DD Mon YYYY HH24:MI') AS decided_at
            FROM iam.approval_requests
            WHERE organization_id = :org AND status IN ('approved','rejected')
            ORDER BY decided_at DESC LIMIT 100
            """
        ),
        {"org": caller.organization_id},
    ).mappings().all()
    return [schemas.ApprovalHistoryOut(**r) for r in rows]


# --------------------------------------------------------------------------
# Raising a request
# --------------------------------------------------------------------------
@approvals_router.post(
    "", response_model=schemas.ApprovalRequestCreated,
    status_code=status.HTTP_201_CREATED,
)
def create_request(
    body: schemas.ApprovalRequestIn,
    db: Session = Depends(get_session),
    caller: Caller = Depends(require_org_permission("approval", "create")),
):
    """Raise an approval request, routed by whatever policy governs it.

    Screen 042 has listed and decided approval requests since it was built, but
    nothing could ever *raise* one — so every screen that needed a sign-off
    could record the intention and then stop. This is the missing half.

    The routing itself lives in ``chirala_common.approvals`` because finance
    and booking-core raise requests too, and the rule that decides whether a
    manager is needed must not have two homes to drift between.
    """
    # Named in the body, where guard_request_tenancy cannot see it,
    # and optional -- a request that names no property is org-wide.
    if body.property_id is not None:
        require_property_permission(db, caller, body.property_id,
                                    "approval", "create")
    out = request_approval(
        db,
        organization_id=caller.organization_id,
        category=body.category,
        title=body.title,
        actor_subject=caller.subject,
        actor_user_id=caller.user_id,
        amount=body.amount,
        amount_context=body.amount_context,
        entity_ref=body.entity_ref,
        guest_name=body.guest_name,
        property_id=body.property_id,
        due_in_hours=body.due_in_hours,
    )
    record_audit(
        db,
        action="approval.requested" if out.required else "approval.auto_cleared",
        entity_type="approval_request", entity_id=str(out.request_id),
        organization_id=caller.organization_id, actor_subject=caller.subject,
        after={"category": body.category, "title": body.title,
               "amount": str(body.amount), "entity_ref": body.entity_ref,
               "policy": out.policy_name},
    )
    return schemas.ApprovalRequestCreated(
        id=out.request_id, status=out.status, category=body.category,
        policy_name=out.policy_name, policy_rule_text=out.rule_text,
        approver_roles=out.approver_roles, required=out.required,
    )


@approvals_router.get(
    "/requests/{request_id}", response_model=schemas.ApprovalRequestOut
)
def one_request(
    request_id: uuid.UUID,
    db: Session = Depends(get_session),
    caller: Caller = Depends(require_org_permission("approval", "view")),
):
    """One request, so a screen that raised it can show where it got to."""
    r = _request_or_404(db, caller, request_id)
    return schemas.ApprovalRequestOut(
        id=r["id"], category=r["category"], title=r["title"],
        entity_ref=r["entity_ref"], guest_name=r["guest_name"],
        requested_by_name=r["requested_by_name"],
        requested_by_role=r["requested_by_role"],
        policy_rule_text=r["policy_rule_text"], amount=r["amount"],
        amount_context=r["amount_context"], status=r["status"],
        due_at=r["due_at"].strftime("%d %b %Y %H:%M") if r["due_at"] else None,
        due_in=_due_in(r["due_at"]),
    )
