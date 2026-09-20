"""Approval policies + requests + decisions (US-042)

Revision ID: 0006_approvals
Revises: 0005_roles_matrix
Create Date: 2026-09-07

Implements the Approval Limits and Queue (mockup 042): policies (rules/limits),
pending requests requiring authorization, and append-only decisions. Maps to
schema §2 approval_policies / §6 approval_requests + approval_decisions.
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0006_approvals"
down_revision: str | None = "0005_roles_matrix"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS iam.approval_policies (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            organization_id uuid NOT NULL,
            name varchar(150) NOT NULL,
            category varchar(30) NOT NULL,
            applies_to varchar(200),
            initiator_roles jsonb NOT NULL DEFAULT '[]',
            approver_roles jsonb NOT NULL DEFAULT '[]',
            threshold_value numeric(19,4),
            threshold_unit varchar(10) NOT NULL DEFAULT '%',
            two_level boolean NOT NULL DEFAULT false,
            prohibit_self_approval boolean NOT NULL DEFAULT true,
            active boolean NOT NULL DEFAULT true,
            created_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT ck_policy_category
                CHECK (category IN ('discount','refund','rate_override','other'))
        )
        """
    )

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS iam.approval_requests (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            organization_id uuid NOT NULL,
            property_id uuid,
            category varchar(30) NOT NULL,
            title varchar(150) NOT NULL,
            entity_ref varchar(80),
            guest_name varchar(150),
            requested_by_name varchar(150),
            requested_by_role varchar(100),
            requested_by_subject varchar(255),
            policy_rule_text varchar(300),
            amount numeric(19,4) NOT NULL DEFAULT 0,
            amount_context varchar(120),
            status varchar(20) NOT NULL DEFAULT 'pending',
            due_at timestamptz,
            created_at timestamptz NOT NULL DEFAULT now(),
            decided_at timestamptz,
            decided_by varchar(255),
            CONSTRAINT ck_request_status
                CHECK (status IN ('pending','approved','rejected'))
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_approval_requests_org_status "
        "ON iam.approval_requests(organization_id, status, due_at)"
    )

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS iam.approval_decisions (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            request_id uuid NOT NULL REFERENCES iam.approval_requests(id),
            approver_subject varchar(255) NOT NULL,
            decision varchar(20) NOT NULL,
            comment varchar(400),
            decided_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT ck_decision CHECK (decision IN ('approved','rejected'))
        )
        """
    )

    for action in ("view", "decide", "manage"):
        op.execute(
            f"""
            INSERT INTO iam.permissions (id, resource_code, action_code, description)
            VALUES (gen_random_uuid(), 'approval', '{action}', 'approval.{action}')
            ON CONFLICT (resource_code, action_code) DO NOTHING
            """
        )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS iam.approval_decisions CASCADE")
    op.execute("DROP TABLE IF EXISTS iam.approval_requests CASCADE")
    op.execute("DROP TABLE IF EXISTS iam.approval_policies CASCADE")
