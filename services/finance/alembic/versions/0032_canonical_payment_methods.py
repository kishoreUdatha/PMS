"""Store one spelling per payment method

Revision ID: 0032_canonical_payment_methods
Revises: 0031_tenant_categories
Create Date: 2026-09-17

``finance.payments`` holds ``UPI`` and ``upi`` as different methods, and
``Cash`` and ``cash`` likewise, because several screens wrote the list down
themselves in title case while the ledger's own endpoint stored what it was
given. Every report that groups by method therefore splits each one in two and
understates both: on this database, six UPI payments worth 13,224 sat apart
from six worth 25,650, and a revenue-by-method report showed neither figure as
the truth.

The code side is fixed -- the canonical list moved to ``chirala_common`` so
booking-core's check-in and check-out paths could use it too, and
``post_payment`` canonicalises at the one place every finance payment is
written -- but that only stops new rows. The rows already written are still
wrong, and they are wrong in a way nobody can see: the reports simply report
smaller numbers.

Only the spelling changes. No amount, folio, allocation, date or status is
touched, and a payment that was taken stays taken -- this does not reverse
anything, and must not be read as having reviewed whether any individual
payment was correct.

``normalise`` is mirrored here rather than imported. A migration has to keep
producing the same result years after the module it borrowed from has moved
on; pinning the rule at the moment it ran is the point.
"""

from __future__ import annotations

from alembic import op

revision: str = "0032_canonical_payment_methods"
down_revision: str | None = "0031_tenant_categories"
branch_labels = None
depends_on = None

#: The canonical spellings, as at this migration. Kept literal for the reason
#: in the docstring.
METHODS = ("cash", "card", "upi", "bank_transfer", "cheque", "wallet", "online")


def upgrade() -> None:
    # lower(), spaces and hyphens to underscores -- the same rule
    # chirala_common.payment_methods.normalise applies. Rows that already hold
    # a canonical spelling are left untouched by the WHERE clause rather than
    # rewritten to themselves.
    methods = ", ".join(f"'{m}'" for m in METHODS)
    op.execute(
        f"""
        UPDATE finance.payments
           SET method = replace(replace(lower(btrim(method)), ' ', '_'),
                                '-', '_')
         WHERE method IS NOT NULL
           AND method <> replace(replace(lower(btrim(method)), ' ', '_'),
                                 '-', '_')
           AND replace(replace(lower(btrim(method)), ' ', '_'), '-', '_')
               IN ({methods})
        """
    )


def downgrade() -> None:
    # Deliberately nothing. The old values were several different spellings of
    # one method and which row had which was never recorded, so there is no
    # "before" to go back to -- inventing a title-cased spelling for every row
    # would put back a mess that was never exactly this one.
    pass
