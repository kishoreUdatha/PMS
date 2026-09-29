"""Payment links: collect from a guest who is not at the desk.

Revision ID: 0040_payment_links
Revises: 0039_cash_drops

A pay-at-hotel booking, a deposit before arrival, a balance after an early
departure: money the hotel is owed by somebody who is not standing at the
counter. A payment link is a Razorpay page for one amount, sent to the guest's
phone. The guest pays there, and the webhook credits the folio, exactly as it
does for the booking engine.

**A link is a payment intent of a second kind.** The intent is already what
the webhook settles against, with row security, one-time settlement and the
tenant check. Links reuse all of that rather than growing a parallel table
that would need every one of those rules written again. ``kind`` tells the two
apart. A link is matched by ``provider_link_id``, because Razorpay creates the
link's order on its side.

**Its own payment source.** Money paid through a link never passed through a
cashier's hands, so like the booking engine it must be left out of a drawer
count. ``payment_link`` joins ``booking_engine`` as an unattended source.
"""
from __future__ import annotations

from alembic import op

revision: str = "0040_payment_links"
down_revision: str | None = "0039_cash_drops"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.execute("""
        ALTER TABLE finance.payment_intents
            ADD COLUMN IF NOT EXISTS kind varchar(12) NOT NULL DEFAULT 'order',
            ADD COLUMN IF NOT EXISTS provider_link_id varchar(120) NULL,
            ADD COLUMN IF NOT EXISTS link_url varchar(500) NULL,
            ADD COLUMN IF NOT EXISTS expires_at timestamptz NULL,
            ADD COLUMN IF NOT EXISTS purpose varchar(200) NULL,
            ADD COLUMN IF NOT EXISTS created_by varchar(200) NULL;

        ALTER TABLE finance.payment_intents
            DROP CONSTRAINT IF EXISTS ck_intent_kind;
        ALTER TABLE finance.payment_intents
            ADD CONSTRAINT ck_intent_kind CHECK (kind IN ('order', 'link'));

        -- An expired link is its own state: nobody cancelled it, and it was
        -- never paid. Folding it into 'cancelled' would lose who did what.
        ALTER TABLE finance.payment_intents
            DROP CONSTRAINT IF EXISTS ck_intent_status;
        ALTER TABLE finance.payment_intents
            ADD CONSTRAINT ck_intent_status CHECK (status IN (
                'created', 'processing', 'succeeded', 'failed', 'cancelled',
                'expired'));

        CREATE UNIQUE INDEX IF NOT EXISTS uq_intent_provider_link
            ON finance.payment_intents (provider_link_id)
            WHERE provider_link_id IS NOT NULL;
        CREATE INDEX IF NOT EXISTS ix_intent_reservation_kind
            ON finance.payment_intents (reservation_id, kind, created_at DESC);

        ALTER TABLE finance.payments
            DROP CONSTRAINT IF EXISTS ck_payment_source;
        ALTER TABLE finance.payments
            ADD CONSTRAINT ck_payment_source CHECK (source IN (
                'front_desk', 'deposit', 'checkout', 'pos', 'night_audit',
                'booking_engine', 'payment_link'));
    """)


def downgrade() -> None:
    op.execute("""
        ALTER TABLE finance.payments
            DROP CONSTRAINT IF EXISTS ck_payment_source;
        ALTER TABLE finance.payments
            ADD CONSTRAINT ck_payment_source CHECK (source IN (
                'front_desk', 'deposit', 'checkout', 'pos', 'night_audit',
                'booking_engine'));
        DROP INDEX IF EXISTS finance.ix_intent_reservation_kind;
        DROP INDEX IF EXISTS finance.uq_intent_provider_link;
        ALTER TABLE finance.payment_intents
            DROP CONSTRAINT IF EXISTS ck_intent_status;
        ALTER TABLE finance.payment_intents
            ADD CONSTRAINT ck_intent_status CHECK (status IN (
                'created', 'processing', 'succeeded', 'failed', 'cancelled'));
        ALTER TABLE finance.payment_intents
            DROP CONSTRAINT IF EXISTS ck_intent_kind;
        ALTER TABLE finance.payment_intents
            DROP COLUMN IF EXISTS kind,
            DROP COLUMN IF EXISTS provider_link_id,
            DROP COLUMN IF EXISTS link_url,
            DROP COLUMN IF EXISTS expires_at,
            DROP COLUMN IF EXISTS purpose,
            DROP COLUMN IF EXISTS created_by;
    """)
