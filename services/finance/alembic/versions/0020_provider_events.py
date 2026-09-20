"""Every gateway webhook we have already acted on.

A payment gateway guarantees *at least* once delivery, not exactly once. It
retries on a timeout, on a 500, and sometimes for no visible reason — so the
same "payment captured" arrives two or three times, and each one is a request
to credit a folio. Without a record of what has been handled, a guest who paid
once is credited twice and the hotel is out the difference.

The provider's own event id is the primary key. That is the only identifier
both sides agree on: our own ids are assigned after we have already decided to
act, which is too late to notice we have acted before.

``payload`` is kept because a webhook is the only evidence of what the gateway
actually said. When a payment is disputed months later, "what did they send us"
is the question, and a summary written by the code that misread it is no help.

Revision ID: 0020_provider_events
Revises: 0019_night_audit_report_email
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0020_provider_events"
down_revision = "0019_night_audit_report_email"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "provider_events",
        sa.Column("provider", sa.String(length=30), primary_key=True),
        sa.Column("event_id", sa.String(length=120), primary_key=True),
        sa.Column("event_type", sa.String(length=80), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()),
                  nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
        # What we did about it. A delivery we deliberately ignored is still a
        # delivery we have seen, and must not be reprocessed on the retry.
        sa.Column("outcome", sa.String(length=30), nullable=False),
        sa.Column("payment_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("detail", sa.Text(), nullable=True),
        schema="finance",
    )
    op.create_index(
        "ix_provider_events_received", "provider_events", ["received_at"],
        schema="finance",
    )


def downgrade() -> None:
    op.drop_index("ix_provider_events_received", table_name="provider_events",
                  schema="finance")
    op.drop_table("provider_events", schema="finance")
