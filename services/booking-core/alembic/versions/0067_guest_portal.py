"""The guest portal: one private link per booking.

Revision ID: 0067_guest_portal
Revises: 0066_outbox_written_fields

A guest gets a link to their own booking. From it they check in online before
they arrive, send the front desk a request, and pay anything outstanding.
There is no login, so the link is the credential, and three tables hold what
it can do.

**guest_portal_links.** A token per booking, stored only as its SHA-256 hash,
like a hold's payment token. A new link revokes the last one, so a link sent to
a wrong number can be replaced, and it dies a week after departure.

**web_checkins.** What the guest typed, kept apart from the guest record on
purpose. At the desk, typing an ID number *is* the verification: somebody
read the document. A number typed by the guest proves nothing of the kind, so
it waits here until a person at the desk checks the ID and applies it. Form C
details for a foreign guest wait here too, because Form C is filed per stay
and there is no stay until check-in.

**guest_requests.** Towels, a late checkout, a cab. A queue the front desk
works, with the guest able to see where each request has got to.
"""
from __future__ import annotations

from alembic import op

revision: str = "0067_guest_portal"
down_revision: str | None = "0066_outbox_written_fields"
branch_labels: str | None = None
depends_on: str | None = None

RUNTIME_ROLE = "pms_app"
TABLES = ("guest_portal_links", "web_checkins", "guest_requests")


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS booking.guest_portal_links (
            id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            organization_id uuid NOT NULL,
            property_id     uuid NOT NULL,
            reservation_id  uuid NOT NULL REFERENCES booking.reservations(id),
            token_hash      char(64) NOT NULL UNIQUE,
            expires_at      timestamptz NOT NULL,
            revoked_at      timestamptz NULL,
            last_opened_at  timestamptz NULL,
            created_by      varchar(200) NULL,
            created_at      timestamptz NOT NULL DEFAULT now()
        );
        CREATE INDEX IF NOT EXISTS ix_portal_links_reservation
            ON booking.guest_portal_links (reservation_id, created_at DESC);

        CREATE TABLE IF NOT EXISTS booking.web_checkins (
            id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            organization_id uuid NOT NULL,
            property_id     uuid NOT NULL,
            reservation_id  uuid NOT NULL UNIQUE REFERENCES booking.reservations(id),
            -- Guest details, arrival time and Form C fields, exactly as typed.
            details         jsonb NOT NULL,
            status          varchar(12) NOT NULL DEFAULT 'submitted'
                              CHECK (status IN ('submitted', 'applied')),
            submitted_at    timestamptz NOT NULL DEFAULT now(),
            applied_at      timestamptz NULL,
            applied_by      uuid NULL
        );

        CREATE TABLE IF NOT EXISTS booking.guest_requests (
            id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            organization_id uuid NOT NULL,
            property_id     uuid NOT NULL,
            reservation_id  uuid NOT NULL REFERENCES booking.reservations(id),
            kind            varchar(20) NOT NULL
                              CHECK (kind IN ('housekeeping', 'amenity',
                                              'maintenance', 'late_checkout',
                                              'transport', 'food', 'other')),
            message         varchar(500) NOT NULL,
            status          varchar(12) NOT NULL DEFAULT 'open'
                              CHECK (status IN ('open', 'in_progress', 'done',
                                                'declined')),
            -- Shown to the guest, so it is written for them.
            staff_note      varchar(300) NULL,
            handled_by      uuid NULL,
            created_at      timestamptz NOT NULL DEFAULT now(),
            updated_at      timestamptz NOT NULL DEFAULT now()
        );
        CREATE INDEX IF NOT EXISTS ix_guest_requests_queue
            ON booking.guest_requests (property_id, status, created_at);
        CREATE INDEX IF NOT EXISTS ix_guest_requests_reservation
            ON booking.guest_requests (reservation_id, created_at DESC);
    """)
    for t in TABLES:
        op.execute(f"ALTER TABLE booking.{t} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE booking.{t} FORCE ROW LEVEL SECURITY")
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON booking.{t}")
        op.execute(f"""
            CREATE POLICY tenant_isolation ON booking.{t}
            USING (tenancy.org_visible(organization_id))
            WITH CHECK (tenancy.org_visible(organization_id))
        """)
        op.execute(f"""
            DO $$
            BEGIN
              IF EXISTS (SELECT FROM pg_roles WHERE rolname = '{RUNTIME_ROLE}') THEN
                GRANT SELECT, INSERT, UPDATE, DELETE
                  ON booking.{t} TO {RUNTIME_ROLE};
              END IF;
            END
            $$
        """)


def downgrade() -> None:
    for t in reversed(TABLES):
        op.execute(f"DROP TABLE IF EXISTS booking.{t}")
