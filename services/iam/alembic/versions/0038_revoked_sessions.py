"""Sessions that have been signed out before they expired.

Revision ID: 0038_revoked_sessions
Revises: 0037_idle_transaction_timeout

Session tokens are now signed and carry their own expiry, which means nothing
the server holds is needed to accept one -- and also that nothing the client
does can end one. Forgetting a token in the browser leaves every other copy
of it working: another tab, a proxy log, a screenshot in a support ticket.
Signing out has to be something the server remembers.

So each token carries a ``jti`` and signing out records it here. Every
service checks this table on every authenticated request, which is why it is
keyed on ``jti`` alone and holds nothing else of use: one primary-key probe,
against a table that only ever contains sessions that were revoked and have
not yet expired on their own. Rows past ``expires_at`` are refused by the
token's own expiry and are deleted opportunistically at the next sign-out.

**Readable from any context.** The check runs before the caller is known --
that is its whole purpose -- so there is no tenant or identity to bind yet. A
``jti`` is a random identifier of a session that no longer works; seeing one
tells nobody anything. Writes are system context only, which is what
``/auth/logout`` runs in.
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0038_revoked_sessions"
down_revision: str | None = "0037_idle_transaction_timeout"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_SYSTEM = "tenancy.system_context()"


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS iam.revoked_sessions (
            jti         varchar(64) PRIMARY KEY,
            -- For whoever is reading the table during an incident, not for
            -- the check itself.
            subject     varchar(255) NOT NULL,
            expires_at  timestamptz NOT NULL,
            revoked_at  timestamptz NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_revoked_sessions_expiry "
        "ON iam.revoked_sessions (expires_at)"
    )
    op.execute("ALTER TABLE iam.revoked_sessions ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE iam.revoked_sessions FORCE ROW LEVEL SECURITY")
    for name in ("tenant_read", "tenant_insert", "tenant_update", "tenant_delete"):
        op.execute(f"DROP POLICY IF EXISTS {name} ON iam.revoked_sessions")
    op.execute("CREATE POLICY tenant_read ON iam.revoked_sessions "
               "FOR SELECT USING (true)")
    op.execute(f"CREATE POLICY tenant_insert ON iam.revoked_sessions "
               f"FOR INSERT WITH CHECK ({_SYSTEM})")
    op.execute(f"CREATE POLICY tenant_update ON iam.revoked_sessions "
               f"FOR UPDATE USING ({_SYSTEM}) WITH CHECK ({_SYSTEM})")
    op.execute(f"CREATE POLICY tenant_delete ON iam.revoked_sessions "
               f"FOR DELETE USING ({_SYSTEM})")
    op.execute(
        """
        DO $$
        BEGIN
          IF EXISTS (SELECT FROM pg_roles WHERE rolname = 'pms_app') THEN
            GRANT SELECT, INSERT, UPDATE, DELETE
              ON iam.revoked_sessions TO pms_app;
          END IF;
        END
        $$
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS iam.revoked_sessions")
