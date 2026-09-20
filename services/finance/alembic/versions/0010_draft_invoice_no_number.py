"""A draft invoice has no number yet

Revision ID: 0010_draft_inv_no_number
Revises: 0009_invoicing
Create Date: 2026-09-10

``invoices.invoice_number`` was NOT NULL, which forced a number to be chosen
the moment a draft was opened. That is the wrong moment: a draft that is
abandoned would burn a number and leave a gap in the fiscal sequence, and a
gap in an invoice series is exactly what an auditor asks about.

A draft genuinely has no number. It gets one when it is issued, allocated
under a row lock so two cashiers cannot take the same one.

``uq_invoice_number`` on ``(property_id, fiscal_series, invoice_number)``
still holds: Postgres treats NULLs as distinct in a unique index, so any
number of drafts can coexist while no two issued invoices can share a number.
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0010_draft_inv_no_number"
down_revision: str | None = "0009_invoicing"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column("invoices", "invoice_number", existing_type=sa.BigInteger(),
                    nullable=True, schema="finance")
    # An issued invoice, on the other hand, must always carry one.
    op.create_check_constraint(
        "ck_invoice_number_when_issued", "invoices",
        "status <> 'issued' OR invoice_number IS NOT NULL", schema="finance",
    )


def downgrade() -> None:
    op.drop_constraint("ck_invoice_number_when_issued", "invoices",
                       schema="finance")
    op.execute("DELETE FROM finance.invoices WHERE invoice_number IS NULL")
    op.alter_column("invoices", "invoice_number", existing_type=sa.BigInteger(),
                    nullable=False, schema="finance")
