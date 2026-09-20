"""Invitations + invitation grants (US-040 Invite or Edit User Access)

Revision ID: 0004_invitations
Revises: 0003_users_rich
Create Date: 2026-09-07

Implements schema §2 invitations + invitation_grants for the Invite User screen
(mockup 040): who is invited, to which property/outlet, with which roles and
access settings (MFA, temporary access expiry, discount/refund approval limits).
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0004_invitations"
down_revision: str | None = "0003_users_rich"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS iam.invitations (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            organization_id uuid NOT NULL,
            property_id uuid NOT NULL,
            email varchar(200) NOT NULL,
            full_name varchar(200) NOT NULL,
            phone varchar(40),
            employee_code varchar(30),
            department_id uuid REFERENCES iam.departments(id),
            mfa_required boolean NOT NULL DEFAULT false,
            temporary_access boolean NOT NULL DEFAULT false,
            access_expiry date,
            max_discount_approval numeric(19,4),
            refund_approval boolean NOT NULL DEFAULT false,
            status varchar(20) NOT NULL DEFAULT 'invited',
            token_hash varchar(200),
            expires_at timestamptz,
            created_by varchar(255),
            created_user_id uuid,
            created_at timestamptz NOT NULL DEFAULT now(),
            accepted_at timestamptz,
            CONSTRAINT ck_invitation_status
                CHECK (status IN ('invited','accepted','revoked','expired'))
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_invitations_org "
        "ON iam.invitations(organization_id, status)"
    )

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS iam.invitation_grants (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            invitation_id uuid NOT NULL REFERENCES iam.invitations(id),
            role_id uuid NOT NULL REFERENCES iam.roles(id),
            outlet_id uuid,
            scope_type varchar(20) NOT NULL DEFAULT 'property',
            created_at timestamptz NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_invitation_grants_inv "
        "ON iam.invitation_grants(invitation_id)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS iam.invitation_grants CASCADE")
    op.execute("DROP TABLE IF EXISTS iam.invitations CASCADE")
