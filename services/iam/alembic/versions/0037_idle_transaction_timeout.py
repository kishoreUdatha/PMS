"""An abandoned transaction cannot take the property offline.

Revision ID: 0037_idle_transaction_timeout
Revises: 0036_deletion_single_admin

A request that dies mid-flight -- a closed browser tab, a dropped
connection, a client that timed out and walked away -- can leave its
transaction open and its locks held. Postgres waits for that client
forever, because as far as it knows the client is simply thinking.

This is not hypothetical. One ``booking-core`` connection sat *idle in
transaction* for four minutes after an audit-event insert. The channel-push
sweep took a lock it needed and blocked; every request behind that blocked
in turn; within seconds the dashboard and the reservations list were both
timing out at sixty seconds. From the desk it looked like the hotel had
stopped working, and nothing in any log said why. Clearing it needed
somebody to find the session in ``pg_stat_activity`` and terminate it by
hand -- which is not a recovery procedure, it is a hope.

**Sixty seconds, and why not thirty.** This timeout only ends a session
sitting *idle inside* a transaction; a session running a long query is
``active`` and is never touched, however long it takes. But a batch job
doing real work in Python between two statements counts as idle by this
definition. The night audit and the background sweeps pause for a moment
per row, not for a minute, so sixty seconds clears the abandoned without
ever reaching the legitimate.

**The runtime role only.** Migrations run as the owner and are allowed to
take as long as they take -- a schema change interrupted half way is a far
worse problem than the one this solves.

The same statement is in ``infra/postgres/init/01_init.sql`` so a freshly
created local database has it from the start. It is repeated here because
that file runs only when the Postgres container initialises an empty data
directory: every database that already exists, and every managed instance
where nobody runs that script at all, gets it from this migration instead.
"""
from __future__ import annotations

from alembic import op

revision: str = "0037_idle_transaction_timeout"
down_revision: str | None = "0036_deletion_single_admin"
branch_labels: str | None = None
depends_on: str | None = None

RUNTIME_ROLE = "pms_app"

#: Long enough that no honest unit of work reaches it, short enough that a
#: property is not down for the length of a coffee break.
TIMEOUT = "60s"


def upgrade() -> None:
    # Guarded: a deployment that has not created the runtime role yet is a
    # valid state -- the grants elsewhere in this schema are written the
    # same way -- and a migration that fails on its absence would block the
    # upgrade for a reason that has nothing to do with the schema.
    op.execute(f"""
        DO $$
        BEGIN
          IF EXISTS (SELECT FROM pg_roles WHERE rolname = '{RUNTIME_ROLE}') THEN
            ALTER ROLE {RUNTIME_ROLE}
              SET idle_in_transaction_session_timeout = '{TIMEOUT}';
          END IF;
        END
        $$
    """)


def downgrade() -> None:
    op.execute(f"""
        DO $$
        BEGIN
          IF EXISTS (SELECT FROM pg_roles WHERE rolname = '{RUNTIME_ROLE}') THEN
            ALTER ROLE {RUNTIME_ROLE}
              RESET idle_in_transaction_session_timeout;
          END IF;
        END
        $$
    """)
