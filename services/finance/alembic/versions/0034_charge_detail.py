"""Let a folio line say what it was for

Revision ID: 0034_charge_detail
Revises: 0033_no_show_basis
Create Date: 2026-09-17

A folio entry records an amount and a ``source_type``, and the screen derives
its description from a lookup on that type. So every charge a desk posts by
hand reads as its department: three different things billed to a guest all
appear as "Restaurant", and the only way to tell them apart is to remember.

That is fine for a room charge the night audit posts, where the type *is* the
description. It is not fine for anything a person enters, which is where the
question "what is this ₹1,400 for?" actually gets asked -- at the desk, with
the guest holding the bill.

Four columns, and each exists because the desk types it and nothing was
keeping it:

``note``
    What it was for, in words. The one a guest queries.

``quantity`` and ``unit_amount``
    Two spa treatments at 700 is not the same fact as one at 1,400, and a
    folio that stores only the total cannot answer which it was. Nullable,
    because a charge entered as a flat figure genuinely has neither.

``discount_amount``
    What was taken off, kept separately from the net. Folding a discount into
    the amount destroys the only evidence that one was given -- and "why is
    this cheaper than the rate card" is a question somebody asks at audit,
    months later.

``amount`` stays what it has always been: the net the guest owes. Nothing here
changes any existing row or any total, so no backfill: an entry posted before
this migration has no quantity because none was recorded, and null says that
more honestly than a 1 nobody entered.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0034_charge_detail"
down_revision: str | None = "0033_no_show_basis"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("folio_entries",
                  sa.Column("note", sa.String(300), nullable=True),
                  schema="finance")
    op.add_column("folio_entries",
                  sa.Column("quantity", sa.Numeric(12, 3), nullable=True),
                  schema="finance")
    op.add_column("folio_entries",
                  sa.Column("unit_amount", sa.Numeric(12, 2), nullable=True),
                  schema="finance")
    op.add_column("folio_entries",
                  sa.Column("discount_amount", sa.Numeric(12, 2),
                            nullable=True),
                  schema="finance")

    # A quantity of zero would make the line meaningless and a negative one
    # would make it a credit wearing a debit's clothes. A discount below zero
    # is a surcharge somebody has mislabelled.
    op.create_check_constraint(
        "ck_entry_quantity", "folio_entries",
        "quantity IS NULL OR quantity > 0", schema="finance")
    op.create_check_constraint(
        "ck_entry_discount", "folio_entries",
        "discount_amount IS NULL OR discount_amount >= 0", schema="finance")


def downgrade() -> None:
    op.drop_constraint("ck_entry_discount", "folio_entries",
                       schema="finance", type_="check")
    op.drop_constraint("ck_entry_quantity", "folio_entries",
                       schema="finance", type_="check")
    for col in ("discount_amount", "unit_amount", "quantity", "note"):
        op.drop_column("folio_entries", col, schema="finance")
