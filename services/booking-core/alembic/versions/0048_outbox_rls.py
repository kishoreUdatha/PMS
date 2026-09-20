"""Row-level security on the transactional outbox

Revision ID: 0048_outbox_rls
Revises: 0047_tenant_rls
Create Date: 2026-09-14

The last table without row security. ``integration.outbox_events`` records a
domain event in the same transaction as the business write that caused it --
a hold taken, a payment posted -- so that a relay can publish it later. Its
payloads carry tenant data, and it has no organisation column: every service
writes to it and no request ever reads it.

So the policy follows what it is for. Any transaction bound to a tenant (or
running in system context) may **insert** an event; only system context -- the
relay that publishes events for every tenant -- may read, mark published or
delete them. ``enqueue_event`` inserts without ``RETURNING``, which is what
lets an insert-only policy work.
"""

from __future__ import annotations

from alembic import op

revision = "0048_outbox_rls"
down_revision = "0047_tenant_rls"
branch_labels = None
depends_on = None

TABLE = "integration.outbox_events"
_SYSTEM = "tenancy.system_context()"
_BOUND = ("(tenancy.system_context() OR "
          "nullif(current_setting('app.organization_id', true), '') IS NOT NULL)")


def upgrade() -> None:
    op.execute(f"ALTER TABLE {TABLE} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {TABLE} FORCE ROW LEVEL SECURITY")
    for name in ("outbox_read", "outbox_insert", "outbox_update", "outbox_delete"):
        op.execute(f"DROP POLICY IF EXISTS {name} ON {TABLE}")
    op.execute(f"CREATE POLICY outbox_read ON {TABLE} FOR SELECT USING ({_SYSTEM})")
    op.execute(f"CREATE POLICY outbox_insert ON {TABLE} FOR INSERT WITH CHECK ({_BOUND})")
    op.execute(f"CREATE POLICY outbox_update ON {TABLE} FOR UPDATE "
               f"USING ({_SYSTEM}) WITH CHECK ({_SYSTEM})")
    op.execute(f"CREATE POLICY outbox_delete ON {TABLE} FOR DELETE USING ({_SYSTEM})")


def downgrade() -> None:
    for name in ("outbox_read", "outbox_insert", "outbox_update", "outbox_delete"):
        op.execute(f"DROP POLICY IF EXISTS {name} ON {TABLE}")
    op.execute(f"ALTER TABLE {TABLE} NO FORCE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {TABLE} DISABLE ROW LEVEL SECURITY")
