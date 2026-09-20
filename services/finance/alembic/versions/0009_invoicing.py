"""Invoice and credit note: billing identity, numbering, audit

Revision ID: 0009_invoicing
Revises: 0008_payment_reversals
Create Date: 2026-09-10

``finance.invoices``, ``invoice_lines`` and ``credit_notes`` have existed since
``0001_finance_core`` and no endpoint has ever touched them. The bones are
good — ``customer_snapshot`` and ``totals_snapshot`` make an issued invoice
immutable even if the guest record or the tax rates change afterwards, and
``uq_invoice_line_entry`` means a folio entry can appear on at most one
invoice, so nothing can be billed twice. This adds what was missing to
actually issue one.

**Billing identity (``invoice_settings``).** An invoice is a document issued by
a legal entity, and ``iam.properties`` knows only a name and a one-line
address — no registered name, no state, no tax registration. Those live here,
per property, because they are a finance concern and because an invoice must
snapshot them at issue: re-printing last year's invoice must show last year's
registration, not today's.

**Nothing is invented.** ``gstin`` and ``state_code`` are nullable and start
empty. A tax registration number is not something to make up — an invoice
carrying a fabricated GSTIN is a false tax document. Until a property fills
these in, the API reports the invoice as not tax-compliant and says which
fields are missing, rather than printing a plausible-looking number.

**Numbering.** ``uq_invoice_number`` already constrains
``(property_id, fiscal_series, invoice_number)``. ``next_number`` here is the
allocator, bumped under a row lock at issue time so two cashiers issuing at
once cannot take the same number. Numbers are allocated at *issue*, never at
draft: an abandoned draft must not burn a number out of the sequence.

**Credit notes** gain the columns needed to be more than a memo — a status of
their own, the ledger entry they posted, and who did it. Issuing one posts a
credit to the folio, so the guest's balance and the invoice's outstanding
figure move together instead of being reconciled by hand.
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0009_invoicing"
down_revision: str | None = "0008_payment_reversals"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "invoice_settings",
        sa.Column("property_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        # The legal entity, which is not always the trading name.
        sa.Column("legal_name", sa.String(200), nullable=True),
        sa.Column("address_line", sa.String(300), nullable=True),
        sa.Column("city", sa.String(120), nullable=True),
        sa.Column("state", sa.String(120), nullable=True),
        # GST state code, e.g. '37' for Andhra Pradesh. Decides CGST+SGST
        # (same state) versus IGST (different), so it cannot be guessed.
        sa.Column("state_code", sa.String(4), nullable=True),
        sa.Column("postal_code", sa.String(20), nullable=True),
        sa.Column("country", sa.String(120), nullable=False,
                  server_default="India"),
        sa.Column("phone", sa.String(60), nullable=True),
        sa.Column("email", sa.String(200), nullable=True),
        sa.Column("gstin", sa.String(20), nullable=True),
        sa.Column("fiscal_series", sa.String(20), nullable=False,
                  server_default="INV"),
        sa.Column("next_number", sa.Integer, nullable=False, server_default="1"),
        sa.Column("footer_note", sa.Text, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("version", sa.BigInteger, nullable=False, server_default="1"),
        sa.CheckConstraint("next_number > 0", name="ck_invoice_next_number"),
        schema="finance",
    )

    # Who prepared, issued or cancelled it — the mockup's Approval & Audit
    # panel, and the reason a cancellation is explainable a year later.
    for col in (
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("issued_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("cancelled_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancel_reason", sa.Text, nullable=True),
        sa.Column("notes", sa.Text, nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
    ):
        op.add_column("invoices", col, schema="finance")

    op.create_index("ix_invoice_folio", "invoices", ["folio_id"], schema="finance")
    op.create_index("ix_invoice_property_status", "invoices",
                    ["property_id", "status"], schema="finance")

    # A credit note is a document in its own right, not a note on an invoice.
    for col in (
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("property_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="draft"),
        # The folio credit this note posted. Null while draft.
        sa.Column("posted_entry_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("issued_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("cancelled_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
    ):
        op.add_column("credit_notes", col, schema="finance")

    op.create_check_constraint(
        "ck_credit_note_status", "credit_notes",
        "status IN ('draft', 'issued', 'cancelled')", schema="finance",
    )
    # An issued note must have posted; a draft must not have.
    op.create_check_constraint(
        "ck_credit_note_posted", "credit_notes",
        "(status = 'issued') = (posted_entry_id IS NOT NULL)", schema="finance",
    )
    op.create_check_constraint(
        "ck_credit_note_amount", "credit_notes", "amount > 0", schema="finance",
    )
    op.create_unique_constraint(
        "uq_credit_note_number", "credit_notes", ["property_id", "number"],
        schema="finance",
    )
    op.create_index("ix_credit_note_invoice", "credit_notes", ["invoice_id"],
                    schema="finance")


def downgrade() -> None:
    op.drop_index("ix_credit_note_invoice", "credit_notes", schema="finance")
    op.drop_constraint("uq_credit_note_number", "credit_notes", schema="finance")
    for name in ("ck_credit_note_amount", "ck_credit_note_posted",
                 "ck_credit_note_status"):
        op.drop_constraint(name, "credit_notes", schema="finance")
    for name in ("created_at", "cancelled_at", "cancelled_by", "issued_by",
                 "created_by", "posted_entry_id", "status", "property_id",
                 "organization_id"):
        op.drop_column("credit_notes", name, schema="finance")

    op.drop_index("ix_invoice_property_status", "invoices", schema="finance")
    op.drop_index("ix_invoice_folio", "invoices", schema="finance")
    for name in ("updated_at", "notes", "cancel_reason", "cancelled_at",
                 "cancelled_by", "issued_by", "created_by"):
        op.drop_column("invoices", name, schema="finance")

    op.drop_table("invoice_settings", schema="finance")
