"""Record what a guest actually paid with, not just "online"

Revision ID: 0024_payment_method_detail
Revises: 0023_booking_engine_source
Create Date: 2026-09-12

Every gateway payment was stored with ``method = 'online'``. That is true and
useless: the cashiering screen breaks the day's takings down by method, and the
breakdown read tens of thousands collected with nothing against Cards and
almost nothing against UPI. The number a manager opens that screen for was the
one number it could not show.

Razorpay reports the instrument in the callback -- ``card``, ``upi``,
``netbanking`` and so on -- and the whole payload has been kept in
``finance.provider_events`` since it started arriving. So the history can be
corrected rather than written off: each event names the payment it settled, and
the payload beside it names the method.

Mapped onto the desk's vocabulary, not copied across. A desk knows cash, card,
UPI, bank transfer, cheque and wallet, because each is a thing that happens at
a counter. EMI is a card underneath and netbanking settles like a transfer.
Anything the map does not recognise stays ``online``: an honest "paid online"
is better than filing a pay-later product under Wallet for want of a closer
box.

Also lowercases the column. It is free text, and a desk screen had been writing
``UPI`` while the ledger wrote ``upi`` -- two spellings of one method, which
a GROUP BY reports as two methods and a total splits in half.
"""

from __future__ import annotations

from alembic import op

revision = "0024_payment_method_detail"
down_revision = "0023_booking_engine_source"
branch_labels = None
depends_on = None

#: Kept in step with ``webhook_routes.METHOD_MAP``. Two copies because one is
#: SQL and one is Python; they encode the same decision and both are short.
_MAP = {
    "card": "card",
    "emi": "card",
    "cardless_emi": "card",
    "upi": "upi",
    "netbanking": "bank_transfer",
    "bank_transfer": "bank_transfer",
    "wallet": "wallet",
}


def upgrade() -> None:
    # One spelling per method. Done first, so the backfill below is comparing
    # against normalised values.
    op.execute("UPDATE finance.payments SET method = lower(method) "
               "WHERE method <> lower(method)")

    cases = "\n".join(
        f"                WHEN '{k}' THEN '{v}'" for k, v in _MAP.items()
    )
    # Only rows still sitting on 'online', and only where the event that
    # settled them actually names a method. Anything else is left alone.
    op.execute(
        f"""
        UPDATE finance.payments p
           SET method = CASE lower(
                   e.payload->'payload'->'payment'->'entity'->>'method')
{cases}
                   ELSE 'online'
               END
          FROM finance.provider_events e
         WHERE e.payment_id = p.id
           AND p.method = 'online'
           AND e.payload->'payload'->'payment'->'entity'->>'method'
               IS NOT NULL
        """
    )


def downgrade() -> None:
    # Put the gateway's payments back to the single opaque bucket. The desk's
    # own payments are left alone -- they were never 'online' to begin with.
    op.execute(
        "UPDATE finance.payments SET method = 'online' "
        "WHERE source = 'booking_engine'"
    )
