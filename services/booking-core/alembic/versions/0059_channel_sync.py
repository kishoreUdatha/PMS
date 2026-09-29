"""Send the channel manager what changed, and keep the receipt

Revision ID: 0059_channel_sync
Revises: 0058_form_c
Create Date: 2026-09-26

The push sent a full year of rates, availability and stay rules for every
property every minute, whatever had changed. That is wrong for the channel
manager -- its limit is ten availability and ten rate/restriction requests a
minute per property, and its certification checks that a price changed on one
night produces one small update for that night -- and it threw away the task
id each accepted request returns, which is the only receipt there is.

Two tables:

``channel_ari_state`` is what was last *accepted* for each room or rate plan,
per night and per field. A sync computes what it would publish now, sends only
the difference, and writes the new values here once the channel manager has
said yes. A refused or throttled request writes nothing, so the same
difference is simply offered again on the next pass -- retry without a queue.
Forgetting a link's state is a full sync.

``channel_sync_log`` is one row per request: which endpoint, why it was sent
(a change, a full sync, a retry), what range it covered, the answer, and the
task ids. It is what a hotel reads when an OTA shows the wrong price, and what
the certification form asks for.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision: str = "0059_channel_sync"
down_revision: str | None = "0058_form_c"
branch_labels = None
depends_on = None

RUNTIME_ROLE = "pms_app"
TABLES = ("channel_ari_state", "channel_sync_log")


def upgrade() -> None:
    op.create_table(
        "channel_ari_state",
        sa.Column("link_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("organization_id", pg.UUID(as_uuid=True), nullable=False),
        # 'availability' (per room type) or a rate/restriction field name
        # (per rate plan): 'rate', 'min_stay_arrival', 'stop_sell', ...
        sa.Column("field", sa.String(40), nullable=False),
        sa.Column("external_id", sa.String(80), nullable=False),
        sa.Column("stay_date", sa.Date(), nullable=False),
        sa.Column("value", sa.String(40), nullable=False),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.PrimaryKeyConstraint("link_id", "field", "external_id", "stay_date"),
        sa.ForeignKeyConstraint(["link_id"],
                                ["distribution.channel_manager_links.id"],
                                ondelete="CASCADE"),
        schema="distribution",
    )

    op.create_table(
        "channel_sync_log",
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("link_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("organization_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("property_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("endpoint", sa.String(40), nullable=False),
        sa.Column("trigger", sa.String(20), nullable=False),
        sa.Column("value_count", sa.Integer(), nullable=False),
        sa.Column("date_from", sa.Date(), nullable=True),
        sa.Column("date_to", sa.Date(), nullable=True),
        sa.Column("status_code", sa.Integer(), nullable=True),
        sa.Column("outcome", sa.String(20), nullable=False),
        sa.Column("task_ids", pg.ARRAY(sa.String(80)), nullable=False,
                  server_default=sa.text("'{}'")),
        sa.Column("summary", sa.String(400), nullable=True),
        sa.Column("error", sa.String(600), nullable=True),
        sa.Column("request_excerpt", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.CheckConstraint(
            "trigger IN ('change', 'full_sync', 'manual')",
            name="ck_sync_log_trigger"),
        sa.CheckConstraint(
            "outcome IN ('sent', 'failed', 'throttled')",
            name="ck_sync_log_outcome"),
        sa.ForeignKeyConstraint(["link_id"],
                                ["distribution.channel_manager_links.id"],
                                ondelete="CASCADE"),
        schema="distribution",
    )
    op.create_index("ix_sync_log_link_time", "channel_sync_log",
                    ["link_id", "created_at"], schema="distribution")

    # Backing off after a 429 is the channel manager's instruction, and it
    # holds across restarts.
    op.add_column("channel_manager_links",
                  sa.Column("sync_paused_until", sa.DateTime(timezone=True),
                            nullable=True),
                  schema="distribution")

    for t in TABLES:
        op.execute(f"ALTER TABLE distribution.{t} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE distribution.{t} FORCE ROW LEVEL SECURITY")
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON distribution.{t}")
        op.execute(
            f"""
            CREATE POLICY tenant_isolation ON distribution.{t}
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
                  ON distribution.{t} TO {RUNTIME_ROLE};
              END IF;
            END
            $$
            """
        )


def downgrade() -> None:
    op.drop_column("channel_manager_links", "sync_paused_until",
                   schema="distribution")
    for t in reversed(TABLES):
        op.drop_table(t, schema="distribution")
