"""Tie a payment intent to the gateway order the guest actually pays.

A webhook arrives naming the gateway's own order, not ours. Without that id
stored against the intent, there is no way to tell which booking a payment
belongs to — and a payment that cannot be matched is money received against
nothing, which is worse than a payment that failed.

Unique, so two intents can never claim the same order. If that ever happened
one booking would be confirmed by another's money.

Revision ID: 0021_intent_order
Revises: 0020_provider_events
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0021_intent_order"
down_revision = "0020_provider_events"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "payment_intents",
        sa.Column("provider_order_id", sa.String(length=120), nullable=True),
        schema="finance",
    )
    op.create_index(
        "uq_intent_provider_order", "payment_intents", ["provider_order_id"],
        unique=True, schema="finance",
        postgresql_where=sa.text("provider_order_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_intent_provider_order", table_name="payment_intents",
                  schema="finance")
    op.drop_column("payment_intents", "provider_order_id", schema="finance")
