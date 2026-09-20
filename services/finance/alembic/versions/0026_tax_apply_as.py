"""Record whether a posted tax was inside the charge or added to it

Revision ID: 0026_tax_apply_as
Revises: 0025_folio_number
Create Date: 2026-09-14

``finance.tax_rules`` has always distinguished ``apply_as = 'inclusive'`` from
``'exclusive'``, and ``compute_tax`` has always honoured it -- an inclusive
rule goes to ``inclusive_total`` and is never posted as its own debit, because
it is already inside the price. But ``folio_entry_taxes`` recorded only the
code, the rate, the taxable amount and the tax, so once a charge was posted
nothing downstream could tell the two apart.

That matters when a charge is taken back off. An adjustment credits
``amount + tax_amount``, which is right for exclusive tax -- the charge and
its separate tax debit both have to be reversed -- and wrong for inclusive,
where the tax is already part of ``amount``. A 1,050 charge containing 50 of
tax produced a 1,100 credit against it: the guest is 50 better off and the
folio no longer reconciles to anything.

The column has no backfill because it needs none here: this deployment has no
tax rules configured and no rows in ``folio_entry_taxes``. A deployment that
does have history should decide deliberately rather than inherit a guess --
``exclusive`` is the safe reading of an existing row, since that is what every
consumer assumed when it was written, and it is the default below.
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0026_tax_apply_as"
down_revision: str | None = "0025_folio_number"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "folio_entry_taxes",
        sa.Column("apply_as", sa.String(10), nullable=False,
                  server_default="exclusive"),
        schema="finance",
    )
    # The same two values the rules table allows, so a row cannot describe a
    # treatment no rule could have produced.
    op.create_check_constraint(
        "ck_entry_tax_apply_as",
        "folio_entry_taxes",
        "apply_as IN ('exclusive', 'inclusive')",
        schema="finance",
    )


def downgrade() -> None:
    op.drop_constraint("ck_entry_tax_apply_as", "folio_entry_taxes",
                       schema="finance")
    op.drop_column("folio_entry_taxes", "apply_as", schema="finance")
