"""A guest paying online is not the front desk taking money

Revision ID: 0023_booking_engine_source
Revises: 0022_payment_credentials
Create Date: 2026-09-12

``finance.payments.source`` records where a payment came from, and its five
permitted values were all places inside the hotel: the desk, a deposit, a
checkout, the POS, the night audit. There was nowhere to put a guest who paid
on the booking engine at two in the morning, so those payments took the column
default -- ``front_desk`` -- and the cashiering screen reported them as money
taken at the desk.

That is not a cosmetic problem. The cashiering screen filters and totals by
source, and a night auditor reconciling the desk's drawer against the system
would find takings attributed to a shift that never handled them. Online
payments belong in their own column precisely so they can be left out of a cash
count.

Existing rows are corrected here, and identified rather than guessed at: a
payment with an ``intent_id`` came through a payment intent, which only the
gateway path creates, and one with no ``cashier_id`` had no person behind it.
Both together is a gateway payment and nothing else is.
"""

from __future__ import annotations

from alembic import op

revision = "0023_booking_engine_source"
down_revision = "0022_payment_credentials"
branch_labels = None
depends_on = None

_OLD = ("front_desk", "deposit", "checkout", "pos", "night_audit")
_NEW = (*_OLD, "booking_engine")


def _values(names: tuple[str, ...]) -> str:
    return ", ".join(f"'{n}'" for n in names)


def upgrade() -> None:
    op.execute("ALTER TABLE finance.payments DROP CONSTRAINT "
               "IF EXISTS ck_payment_source")
    op.execute(
        "ALTER TABLE finance.payments ADD CONSTRAINT ck_payment_source "
        f"CHECK (source IN ({_values(_NEW)}))"
    )
    # Reattribute the payments that were only ever `front_desk` because there
    # was no truer option. Narrow on purpose: an intent means the gateway
    # opened it, and no cashier means nobody was standing there.
    op.execute(
        """
        UPDATE finance.payments
           SET source = 'booking_engine'
         WHERE source = 'front_desk'
           AND intent_id IS NOT NULL
           AND cashier_id IS NULL
        """
    )


def downgrade() -> None:
    # Put them back before narrowing the constraint, or the constraint cannot
    # be added.
    op.execute("UPDATE finance.payments SET source = 'front_desk' "
               "WHERE source = 'booking_engine'")
    op.execute("ALTER TABLE finance.payments DROP CONSTRAINT "
               "IF EXISTS ck_payment_source")
    op.execute(
        "ALTER TABLE finance.payments ADD CONSTRAINT ck_payment_source "
        f"CHECK (source IN ({_values(_OLD)}))"
    )
