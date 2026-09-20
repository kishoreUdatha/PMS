"""Second-factor authentication for platform staff

Revision ID: 0029_platform_mfa
Revises: 0028_administration_grants
Create Date: 2026-09-14

The design pack's screen 02, and the one unmet acceptance criterion that is a
security gap rather than a missing feature: *"Staff sign-in requires the
configured MFA policy."* A platform operator can read every tenant on the
platform and suspend any of them, protected until now by one password.

``iam.users`` has carried an ``mfa_status`` column since the beginning and
nothing has ever written to it, because there was nowhere to keep a secret.
This is that place.

**The secret is encrypted at rest**, with ``chirala_common.secretbox`` --
Fernet under ``CREDENTIAL_ENCRYPTION_KEYS``, the same mechanism that protects
tenant payment-gateway credentials. A TOTP seed is a bearer credential:
anybody holding it can mint valid codes forever, so storing it in plain text
would make the database dump equivalent to the second factor.

**Recovery codes are hashed, never stored.** They are passwords by another
name -- single use, high entropy -- and the same argument that stops us
keeping a readable password applies to them. ``used_at`` rather than DELETE,
so a code that has been spent is visibly spent rather than missing.

Enrolment is two steps by design: ``pending`` while the operator has scanned
the QR but not yet proved they can produce a code, ``active`` only once they
have. Marking somebody enrolled the moment a secret is generated locks out
anybody whose authenticator did not actually take it.
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0029_platform_mfa"
down_revision: str | None = "0028_administration_grants"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_SYSTEM = "tenancy.system_context()"
#: A person may read their own enrolment while they are being identified.
_OWN = ("(tenancy.system_context() "
        "OR user_id = tenancy.identity_user_id() "
        "OR user_id::text = nullif(current_setting('app.user_id', true), ''))")


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS iam.user_mfa (
            user_id      uuid PRIMARY KEY REFERENCES iam.users(id)
                           ON DELETE CASCADE,
            -- Fernet ciphertext. Never a readable seed.
            secret       text NOT NULL,
            status       varchar(12) NOT NULL DEFAULT 'pending'
                           CHECK (status IN ('pending', 'active')),
            confirmed_at timestamptz NULL,
            last_used_at timestamptz NULL,
            -- The counter of the last accepted step, so a code cannot be
            -- replayed inside its own 30-second window by somebody who read
            -- it over a shoulder or off a proxy log.
            last_counter bigint NULL,
            created_at   timestamptz NOT NULL DEFAULT now(),
            updated_at   timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT ck_active_is_confirmed
                CHECK (status <> 'active' OR confirmed_at IS NOT NULL)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS iam.user_mfa_recovery (
            id         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            user_id    uuid NOT NULL REFERENCES iam.users(id)
                         ON DELETE CASCADE,
            -- sha256 of the code. The same reasoning as password_tokens: a
            -- database that leaks must not hand over working credentials.
            code_hash  varchar(64) NOT NULL,
            used_at    timestamptz NULL,
            created_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT uq_recovery_code UNIQUE (user_id, code_hash)
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_recovery_unused "
        "ON iam.user_mfa_recovery (user_id) WHERE used_at IS NULL"
    )

    for table, read in (("user_mfa", _OWN), ("user_mfa_recovery", _OWN)):
        op.execute(f"ALTER TABLE iam.{table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE iam.{table} FORCE ROW LEVEL SECURITY")
        for name in ("tenant_read", "tenant_insert", "tenant_update",
                     "tenant_delete"):
            op.execute(f"DROP POLICY IF EXISTS {name} ON iam.{table}")
        op.execute(
            f"CREATE POLICY tenant_read ON iam.{table} FOR SELECT USING ({read})")
        # Written only in system context: enrolling and spending a code both
        # happen inside sign-in, which already runs elevated.
        op.execute(
            f"CREATE POLICY tenant_insert ON iam.{table} FOR INSERT "
            f"WITH CHECK ({_SYSTEM})")
        op.execute(
            f"CREATE POLICY tenant_update ON iam.{table} FOR UPDATE "
            f"USING ({_SYSTEM}) WITH CHECK ({_SYSTEM})")
        op.execute(
            f"CREATE POLICY tenant_delete ON iam.{table} FOR DELETE "
            f"USING ({_SYSTEM})")

    op.execute(
        """
        DO $$
        BEGIN
          IF EXISTS (SELECT FROM pg_roles WHERE rolname = 'pms_app') THEN
            GRANT SELECT, INSERT, UPDATE, DELETE
              ON iam.user_mfa, iam.user_mfa_recovery TO pms_app;
          END IF;
        END
        $$
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS iam.user_mfa_recovery")
    op.execute("DROP TABLE IF EXISTS iam.user_mfa")
