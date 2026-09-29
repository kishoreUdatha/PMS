"""Demo requests from the public landing page.

Revision ID: 0039_demo_requests
Revises: 0038_platform_name_myguest

The landing page offers two ways in: sign in, or book a demo. A hotel that
has not bought the product yet has no organisation, so a demo request belongs
to nobody on the tenant side. It is a sales lead, and it lives in the
platform schema, where the console's staff follow it up.

Read and written only in system context, like tenant_deletions. The public
form writes through a named system context, and the console reads through the
platform capability guard. No tenant can see another hotel's enquiry, and no
tenant can see its own either, because a tenant has no rows here.
"""
from __future__ import annotations

from alembic import op

revision: str = "0039_demo_requests"
down_revision: str | None = "0038_platform_name_myguest"
branch_labels: str | None = None
depends_on: str | None = None

TABLE = """
CREATE TABLE IF NOT EXISTS platform.demo_requests (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    full_name     varchar(120) NOT NULL,
    email         varchar(254) NOT NULL,
    phone         varchar(20)  NOT NULL,
    property_name varchar(160) NOT NULL,
    city          varchar(80)  NULL,
    state         varchar(80)  NULL,
    rooms         integer      NULL CHECK (rooms IS NULL OR rooms BETWEEN 1 AND 5000),
    message       varchar(1000) NULL,
    status        varchar(20)  NOT NULL DEFAULT 'new'
                    CHECK (status IN ('new', 'contacted', 'scheduled',
                                      'converted', 'closed')),
    -- The console's own working notes. Never shown to the requester.
    notes         varchar(2000) NULL,
    assigned_to   uuid NULL REFERENCES iam.users(id),
    created_at    timestamptz NOT NULL DEFAULT now(),
    updated_at    timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_demo_requests_status
    ON platform.demo_requests (status, created_at DESC);
CREATE INDEX IF NOT EXISTS ix_demo_requests_email
    ON platform.demo_requests (lower(email), created_at DESC);

ALTER TABLE platform.demo_requests ENABLE ROW LEVEL SECURITY;
ALTER TABLE platform.demo_requests FORCE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS demo_requests_read ON platform.demo_requests;
CREATE POLICY demo_requests_read ON platform.demo_requests
    FOR SELECT USING (tenancy.system_context());

DROP POLICY IF EXISTS demo_requests_write ON platform.demo_requests;
CREATE POLICY demo_requests_write ON platform.demo_requests
    FOR ALL USING (tenancy.system_context())
    WITH CHECK (tenancy.system_context());
"""

CAPABILITIES = [
    ("demo.view", "See demo requests from the public landing page"),
    ("demo.manage", "Follow up demo requests: status, owner and notes"),
]

#: Who follows up a lead. The owner holds every capability. Onboarding
#: specialists get the new hotel set up after the demo, so they see the lead
#: from the start.
GRANTS = {
    "platform_owner": ["demo.view", "demo.manage"],
    "onboarding_specialist": ["demo.view", "demo.manage"],
}


def upgrade() -> None:
    op.execute(TABLE)

    for code, description in CAPABILITIES:
        op.execute(
            f"""
            INSERT INTO iam.platform_permissions (code, description)
            VALUES ('{code}', '{description}')
            ON CONFLICT (code) DO UPDATE SET description = EXCLUDED.description
            """
        )

    for role, codes in GRANTS.items():
        for code in codes:
            op.execute(
                f"""
                INSERT INTO iam.platform_role_permissions (role_id, permission_id)
                SELECT r.id, p.id
                FROM iam.platform_roles r, iam.platform_permissions p
                WHERE r.code = '{role}' AND p.code = '{code}'
                ON CONFLICT DO NOTHING
                """
            )

    # The runtime login reaches new platform tables through 0032's default
    # privileges, but only for tables created by the same owner. Granting
    # here as well costs nothing and does not depend on who ran what.
    op.execute(
        """
        DO $$
        BEGIN
          IF EXISTS (SELECT FROM pg_roles WHERE rolname = 'pms_app') THEN
            GRANT SELECT, INSERT, UPDATE, DELETE
              ON platform.demo_requests TO pms_app;
          END IF;
        END
        $$
        """
    )

    # A migration that silently granted nothing would leave the console
    # screen answering 403 and look like a permissions bug later.
    op.execute(
        """
        DO $$
        DECLARE granted int;
        BEGIN
            SELECT count(*) INTO granted
            FROM iam.platform_role_permissions rp
            JOIN iam.platform_permissions p ON p.id = rp.permission_id
            JOIN iam.platform_roles r ON r.id = rp.role_id
            WHERE p.code IN ('demo.view', 'demo.manage')
              AND r.code = 'platform_owner';
            IF granted <> 2 THEN
                RAISE EXCEPTION
                    'demo capabilities not granted to platform_owner (got %)',
                    granted;
            END IF;
        END $$;
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS platform.demo_requests")
    op.execute(
        """
        DELETE FROM iam.platform_role_permissions
        WHERE permission_id IN (SELECT id FROM iam.platform_permissions
                                WHERE code IN ('demo.view', 'demo.manage'))
        """
    )
    op.execute(
        "DELETE FROM iam.platform_permissions "
        "WHERE code IN ('demo.view', 'demo.manage')"
    )
