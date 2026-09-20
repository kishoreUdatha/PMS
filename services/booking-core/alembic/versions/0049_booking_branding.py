"""Per-property branding for the guest booking page

Revision ID: 0049_booking_branding
Revises: 0048_outbox_rls
Create Date: 2026-09-15

Five hotels share one booking page today. The data behind it is isolated --
row-level security narrows a guest request to the one property its URL names
-- but the *presentation* is one stylesheet, so every hotel's guests see the
platform's colours and "Powered by Chirala Bay PMS" where the hotel's own
name should be. For the highest-margin channel a hotel has, that is the wrong
way round: a direct booking page should look like the hotel, not like us.

Three columns carry real risk and are constrained here rather than trusted:

**brand_color is checked in the database.** The value is interpolated into a
CSS custom property on a public page, so anything that is not exactly six hex
digits is a stylesheet injection waiting to be written. A regex constraint
means no route, present or future, can store one that is not a colour -- and
the readable text colour to pair with it is computed from this, never
supplied by the tenant.

**tagline is short and escaped at the point of use.** The page already has an
``esc()`` helper and uses it for every tenant-supplied string; this follows
the same path. The length cap is a design constraint rather than a security
one -- a headline that wraps four times looks broken.

**The keys are object-store keys, not URLs.** A tenant-supplied URL would
turn the booking page into an arbitrary-request machine pointed at whatever
host they liked. Storing a key and presigning it on the way out keeps the
platform in charge of where images come from.

Nothing here is required. A property with no row gets the platform's default
appearance, which is what every property has today.
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0049_booking_branding"
down_revision: str | None = "0048_outbox_rls"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

RUNTIME_ROLE = "pms_app"


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS property.booking_branding (
            property_id     uuid PRIMARY KEY
                              REFERENCES iam.properties(id) ON DELETE CASCADE,
            organization_id uuid NOT NULL REFERENCES iam.organizations(id),
            -- Exactly six hex digits, or nothing. See the module docstring:
            -- this reaches a public page's CSS.
            brand_color     varchar(7) NULL
                              CHECK (brand_color ~ '^#[0-9A-Fa-f]{6}$'),
            tagline         varchar(160) NULL,
            -- Object-store keys. Never URLs -- presigned on the way out, so
            -- the platform decides which host a guest's browser talks to.
            logo_key        varchar(400) NULL,
            banner_key      varchar(400) NULL,
            updated_at      timestamptz NOT NULL DEFAULT now(),
            updated_by      uuid NULL REFERENCES iam.users(id)
        )
        """
    )

    op.execute("ALTER TABLE property.booking_branding "
               "ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE property.booking_branding "
               "FORCE ROW LEVEL SECURITY")
    op.execute("DROP POLICY IF EXISTS tenant_isolation "
               "ON property.booking_branding")
    # The same policy every other table in this schema carries. A guest
    # request reaches this only after the route has bound the tenant it found
    # from the property code, so org_visible is the whole of it -- there is no
    # separate public-read path to get wrong.
    op.execute(
        """
        CREATE POLICY tenant_isolation ON property.booking_branding
        USING (tenancy.org_visible(organization_id))
        WITH CHECK (tenancy.org_visible(organization_id))
        """
    )

    op.execute(
        f"""
        DO $$
        BEGIN
          IF EXISTS (SELECT FROM pg_roles WHERE rolname = '{RUNTIME_ROLE}') THEN
            GRANT SELECT, INSERT, UPDATE, DELETE
              ON property.booking_branding TO {RUNTIME_ROLE};
          END IF;
        END
        $$
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS property.booking_branding")
