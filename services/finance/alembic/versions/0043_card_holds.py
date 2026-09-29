"""Card holds: authorise an amount on a guest's card, capture it later.

Revision ID: 0043_card_holds
Revises: 0042_saved_reports

A hold is a Razorpay order created with manual capture. The guest's card is
authorised for the amount and nothing is taken. Later the hotel captures
all of it or part of it (the minibar, a damage deposit), or releases it.

Like payment links (0040), a hold is a payment intent of another kind.
The existing one-time settlement, tenant check and row security all
apply. Two states are new:

``authorized``: the bank has reserved the money on the card.
``released``: the hotel let the hold go without taking it. The bank
returns it when the authorisation lapses.

``authorized_payment_id`` is the Razorpay payment the card produced, which
is what a capture is made against.
"""
from __future__ import annotations

from alembic import op

revision: str = "0043_card_holds"
down_revision: str | None = "0042_saved_reports"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.execute("""
        ALTER TABLE finance.payment_intents
            ADD COLUMN IF NOT EXISTS authorized_payment_id varchar(120) NULL,
            ADD COLUMN IF NOT EXISTS authorized_amount numeric(19,4) NULL,
            ADD COLUMN IF NOT EXISTS captured_amount numeric(19,4) NULL;
        ALTER TABLE finance.payment_intents DROP CONSTRAINT IF EXISTS ck_intent_kind;
        ALTER TABLE finance.payment_intents ADD CONSTRAINT ck_intent_kind
            CHECK (kind IN ('order', 'link', 'hold'));
        ALTER TABLE finance.payment_intents DROP CONSTRAINT IF EXISTS ck_intent_status;
        ALTER TABLE finance.payment_intents ADD CONSTRAINT ck_intent_status
            CHECK (status IN ('created', 'processing', 'authorized', 'succeeded',
                              'failed', 'cancelled', 'expired', 'released'));
    """)


def downgrade() -> None:
    op.execute("""
        UPDATE finance.payment_intents SET status = 'cancelled'
         WHERE status IN ('authorized', 'released');
        DELETE FROM finance.payment_intents WHERE kind = 'hold' AND status = 'created';
        ALTER TABLE finance.payment_intents DROP CONSTRAINT IF EXISTS ck_intent_status;
        ALTER TABLE finance.payment_intents ADD CONSTRAINT ck_intent_status
            CHECK (status IN ('created', 'processing', 'succeeded', 'failed',
                              'cancelled', 'expired'));
        ALTER TABLE finance.payment_intents DROP CONSTRAINT IF EXISTS ck_intent_kind;
        ALTER TABLE finance.payment_intents ADD CONSTRAINT ck_intent_kind
            CHECK (kind IN ('order', 'link'));
        ALTER TABLE finance.payment_intents
            DROP COLUMN IF EXISTS authorized_payment_id,
            DROP COLUMN IF EXISTS authorized_amount,
            DROP COLUMN IF EXISTS captured_amount;
    """)
