"""Raising an approval request, from wherever the decision is being made.

The approval tables live in the ``iam`` schema and screen 042 owns the queue
that reads them. But the requests themselves come from everywhere — a discount
at the desk, a folio adjustment in finance, a room upgrade in booking-core —
and every one of those services already reads across schemas in the one
database this system runs on.

So the routing logic lives here rather than behind an HTTP call. There is one
implementation of "which policy governs this, and does it need a signature",
used identically by the IAM endpoint and by any service that needs to raise a
request itself. The alternative — service-to-service auth for a single insert —
would buy no isolation the database does not already enforce, and would give
the rule two homes to drift between.

**The policy decides, not the caller.** A request whose amount sits under the
governing threshold is created already approved, carrying the rule that cleared
it. Saying plainly that no manager was needed is better than pretending one
looked, and better than blocking work that policy allows.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from decimal import Decimal

from sqlalchemy import text
from sqlalchemy.orm import Session

# How long a decision has before the queue calls it overdue, by category. The
# differences are the point: a guest standing at the desk cannot wait as long
# as a refund on money that has already left the building.
DEFAULT_DUE_HOURS = {
    "discount": 2,
    "rate_override": 2,
    "adjustment": 8,
    "reversal": 8,
    "cancellation": 4,
    "waiver": 4,
    "upgrade": 2,
    "refund": 24,
    "other": 24,
}


@dataclass
class ApprovalOutcome:
    """What happened when a request was raised."""

    request_id: uuid.UUID
    status: str  # pending | approved
    required: bool
    policy_name: str | None = None
    rule_text: str = ""
    approver_roles: list[str] = field(default_factory=list)


def resolve_policy(db: Session, organization_id, category: str):
    """The active policy governing a category, if there is one."""
    return db.execute(
        text(
            """
            SELECT id, name, applies_to, approver_roles, threshold_value,
                   threshold_unit, two_level, prohibit_self_approval
            FROM iam.approval_policies
            WHERE organization_id = :org AND category = :cat AND active = true
            ORDER BY threshold_value NULLS FIRST
            LIMIT 1
            """
        ),
        {"org": organization_id, "cat": category},
    ).mappings().first()


def rule_text(policy) -> str:
    """The sentence the queue shows: what the rule is, in words."""
    if policy is None:
        return (
            "No policy is configured for this category, so the request is "
            "routed for a manual decision."
        )
    approvers = " or ".join(policy["approver_roles"] or ["a manager"])
    threshold = policy["threshold_value"]
    unit = (policy["threshold_unit"] or "").strip()
    if threshold is None:
        return f"{policy['name']} — every request needs approval by {approvers}."
    if unit.upper() == "INR":
        return (
            f"{policy['name']} — above Rs {threshold:,.0f}, approval by "
            f"{approvers}."
        )
    return f"{policy['name']} — above {threshold:g} {unit}, approval by {approvers}."


def request_approval(
    db: Session,
    *,
    organization_id,
    category: str,
    title: str,
    actor_subject: str,
    actor_user_id=None,
    amount: Decimal = Decimal("0"),
    amount_context: str | None = None,
    entity_ref: str | None = None,
    guest_name: str | None = None,
    property_id=None,
    due_in_hours: int | None = None,
) -> ApprovalOutcome:
    """Raise a request, routed by whatever policy governs the category."""
    policy = resolve_policy(db, organization_id, category)

    required = True
    auto_reason = None
    if policy is not None and policy["threshold_value"] is not None:
        # A currency threshold is compared against the amount. A percentage or
        # hours threshold describes something the amount does not carry, so the
        # caller has already decided it applies by raising the request at all.
        if (policy["threshold_unit"] or "").upper() == "INR" and amount <= policy[
            "threshold_value"
        ]:
            required = False
            auto_reason = (
                f"Within the {policy['name']} limit of "
                f"Rs {policy['threshold_value']:,.0f}; no approval needed."
            )

    who = None
    if actor_user_id is not None:
        who = db.execute(
            text(
                """
                SELECT u.display_name,
                       (SELECT string_agg(DISTINCT r.name, ', ')
                        FROM iam.memberships m
                        JOIN iam.role_assignments ra ON ra.membership_id = m.id
                        JOIN iam.roles r ON r.id = ra.role_id
                        WHERE m.user_id = u.id AND m.status = 'active') AS roles
                FROM iam.users u WHERE u.id = :uid
                """
            ),
            {"uid": actor_user_id},
        ).mappings().first()

    hours = due_in_hours or DEFAULT_DUE_HOURS.get(category, 24)
    request_id = uuid.uuid4()
    status = "pending" if required else "approved"
    db.execute(
        text(
            """
            INSERT INTO iam.approval_requests
                (id, organization_id, property_id, category, title, entity_ref,
                 guest_name, requested_by_name, requested_by_role,
                 requested_by_subject, policy_rule_text, amount, amount_context,
                 status, due_at, decided_at, decided_by)
            VALUES (:id, :org, :prop, :cat, :title, :ref, :guest, :name, :role,
                    :sub, :rule, :amt, :ctx, CAST(:st AS varchar),
                    now() + make_interval(hours => :hrs),
                    CASE WHEN CAST(:st AS varchar) = 'pending'
                         THEN NULL ELSE now() END,
                    -- A request cleared by the threshold was decided by the
                    -- policy, not by the person who raised it. Recording the
                    -- initiator here would put "approved by" their own name
                    -- against their own adjustment, which is the one thing
                    -- prohibit_self_approval exists to prevent.
                    CASE WHEN CAST(:st AS varchar) = 'pending'
                         THEN NULL ELSE 'policy' END)
            """
        ),
        {
            "id": request_id, "org": organization_id, "prop": property_id,
            "cat": category, "title": title, "ref": entity_ref,
            "guest": guest_name,
            "name": who["display_name"] if who else None,
            "role": who["roles"] if who else None,
            "sub": actor_subject, "rule": auto_reason or rule_text(policy),
            "amt": amount, "ctx": amount_context, "hrs": hours, "st": status,
        },
    )
    if not required:
        # Recorded as a decision so the history reads honestly: the system
        # cleared it under policy, and the history says which policy.
        db.execute(
            text(
                """
                INSERT INTO iam.approval_decisions
                    (id, request_id, approver_subject, decision, comment)
                VALUES (gen_random_uuid(), :rid, 'system', 'approved', :cmt)
                """
            ),
            {"rid": request_id, "cmt": auto_reason},
        )
    return ApprovalOutcome(
        request_id=request_id, status=status, required=required,
        policy_name=policy["name"] if policy else None,
        rule_text=auto_reason or rule_text(policy),
        approver_roles=list(policy["approver_roles"] or []) if policy else [],
    )


def approval_state(db: Session, request_id) -> dict | None:
    """Where a request got to, for a screen that raised one earlier."""
    row = db.execute(
        text(
            """
            SELECT ar.id, ar.status, ar.title, ar.amount, ar.policy_rule_text,
                   ar.requested_by_name, ar.requested_by_role, ar.created_at,
                   ar.decided_at, ar.decided_by, ad.comment AS decision_comment
            FROM iam.approval_requests ar
            LEFT JOIN LATERAL (
                SELECT comment FROM iam.approval_decisions d
                WHERE d.request_id = ar.id ORDER BY d.decided_at DESC LIMIT 1
            ) ad ON TRUE
            WHERE ar.id = :id
            """
        ),
        {"id": request_id},
    ).mappings().first()
    return dict(row) if row else None
