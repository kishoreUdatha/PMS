"""Finance core: charge codes, tax rules, folios, entries, payments, invoices, night audit

Revision ID: 0001_finance_core
Revises:
Create Date: 2026-09-06

Implements schema §6 in the ``finance`` schema.

SIGN CONVENTION (documented, enforced in ledger.py):
  folio_entries.entry_type is 'debit' or 'credit'.
  A DEBIT increases the amount the guest owes (charges).
  A CREDIT decreases it (payments, allowances).
  folio balance = SUM(debit amounts) - SUM(credit amounts) over posted entries.
  A positive balance means the guest owes money; negative means credit due.

Posted entries are immutable: corrections are made with reversal entries
(reversal_of_id), never by editing. Idempotent posting is enforced by a unique
(folio_id, source_type, source_line_key) key.
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0001_finance_core"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("CREATE SCHEMA IF NOT EXISTS finance")
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")

    # Shared outbox (§10). Created here with IF NOT EXISTS so the finance service
    # is independently deployable; on the shared cluster booking-core may have
    # created it already — the guard keeps both paths safe.
    op.execute("CREATE SCHEMA IF NOT EXISTS integration")
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS integration.outbox_events (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            aggregate_type varchar(80) NOT NULL,
            aggregate_id varchar(80) NOT NULL,
            event_type varchar(120) NOT NULL,
            payload jsonb NOT NULL,
            occurred_at timestamptz NOT NULL DEFAULT now(),
            published_at timestamptz
        )
        """
    )

    # ---- tax_rules ----
    op.execute(
        """
        CREATE TABLE finance.tax_rules (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            organization_id uuid NOT NULL,
            property_id uuid NOT NULL,
            code varchar(50) NOT NULL,
            version int NOT NULL DEFAULT 1,
            effective_from date NOT NULL,
            effective_to date,
            calculation_rule jsonb NOT NULL,
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT uq_tax_rule UNIQUE (property_id, code, version)
        )
        """
    )

    # ---- charge_codes ----
    op.execute(
        """
        CREATE TABLE finance.charge_codes (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            organization_id uuid NOT NULL,
            property_id uuid NOT NULL,
            code varchar(50) NOT NULL,
            revenue_category varchar(50) NOT NULL,
            tax_rule_id uuid REFERENCES finance.tax_rules(id),
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT uq_charge_code UNIQUE (property_id, code)
        )
        """
    )

    # ---- folios ----
    op.execute(
        """
        CREATE TABLE finance.folios (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            organization_id uuid NOT NULL,
            property_id uuid NOT NULL,
            reservation_id uuid,
            group_id uuid,
            commercial_account_id uuid,
            type varchar(20) NOT NULL DEFAULT 'guest',
            currency varchar(3) NOT NULL DEFAULT 'INR',
            status varchar(20) NOT NULL DEFAULT 'open',
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now(),
            version bigint NOT NULL DEFAULT 0,
            CONSTRAINT uq_folio_prop_id UNIQUE (property_id, id),
            CONSTRAINT ck_folio_type CHECK (
                type IN ('guest','group','company','paymaster')
            ),
            CONSTRAINT ck_folio_status CHECK (
                status IN ('open','closed','settled')
            )
        )
        """
    )

    # ---- folio_entries (immutable posted debits/credits) ----
    op.execute(
        """
        CREATE TABLE finance.folio_entries (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            organization_id uuid NOT NULL,
            property_id uuid NOT NULL,
            folio_id uuid NOT NULL,
            entry_type varchar(10) NOT NULL,
            amount numeric(19,4) NOT NULL,
            currency varchar(3) NOT NULL DEFAULT 'INR',
            business_date date NOT NULL,
            charge_code_id uuid REFERENCES finance.charge_codes(id),
            reversal_of_id uuid REFERENCES finance.folio_entries(id),
            source_type varchar(40) NOT NULL,
            source_id varchar(80),
            source_line_key varchar(200) NOT NULL,
            posted_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT fk_entry_folio
                FOREIGN KEY (property_id, folio_id)
                REFERENCES finance.folios (property_id, id),
            CONSTRAINT ck_entry_type CHECK (entry_type IN ('debit','credit')),
            CONSTRAINT ck_entry_amount_positive CHECK (amount > 0),
            CONSTRAINT uq_entry_source_line
                UNIQUE (folio_id, source_type, source_line_key)
        )
        """
    )

    # ---- folio_entry_taxes ----
    op.execute(
        """
        CREATE TABLE finance.folio_entry_taxes (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            folio_entry_id uuid NOT NULL REFERENCES finance.folio_entries(id),
            tax_code varchar(50) NOT NULL,
            rate_snapshot numeric(9,4) NOT NULL,
            taxable_amount numeric(19,4) NOT NULL,
            tax_amount numeric(19,4) NOT NULL
        )
        """
    )

    # ---- payment_intents (request to collect, not proof) ----
    op.execute(
        """
        CREATE TABLE finance.payment_intents (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            organization_id uuid NOT NULL,
            property_id uuid NOT NULL,
            provider_connection_id uuid,
            reservation_id uuid,
            folio_id uuid,
            expected_amount numeric(19,4) NOT NULL,
            currency varchar(3) NOT NULL DEFAULT 'INR',
            status varchar(20) NOT NULL DEFAULT 'created',
            idempotency_key varchar(200) NOT NULL,
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT uq_payment_intent_key UNIQUE (idempotency_key),
            CONSTRAINT ck_intent_status CHECK (
                status IN ('created','processing','succeeded','failed','cancelled')
            )
        )
        """
    )

    # ---- payments (only settled/successful events affect collection) ----
    op.execute(
        """
        CREATE TABLE finance.payments (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            organization_id uuid NOT NULL,
            property_id uuid NOT NULL,
            intent_id uuid REFERENCES finance.payment_intents(id),
            provider_transaction_id varchar(120),
            method varchar(30) NOT NULL,
            amount numeric(19,4) NOT NULL,
            currency varchar(3) NOT NULL DEFAULT 'INR',
            status varchar(20) NOT NULL DEFAULT 'succeeded',
            received_at timestamptz NOT NULL DEFAULT now(),
            cashier_shift_id uuid,
            created_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT uq_payment_prop_id UNIQUE (property_id, id),
            CONSTRAINT ck_payment_amount_positive CHECK (amount > 0),
            CONSTRAINT ck_payment_status CHECK (
                status IN ('succeeded','failed','pending','reversed')
            )
        )
        """
    )

    # ---- payment_allocations (split payment across folios) ----
    op.execute(
        """
        CREATE TABLE finance.payment_allocations (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            organization_id uuid NOT NULL,
            property_id uuid NOT NULL,
            payment_id uuid NOT NULL,
            folio_id uuid NOT NULL,
            amount numeric(19,4) NOT NULL,
            folio_entry_id uuid REFERENCES finance.folio_entries(id),
            created_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT fk_alloc_payment
                FOREIGN KEY (property_id, payment_id)
                REFERENCES finance.payments (property_id, id),
            CONSTRAINT fk_alloc_folio
                FOREIGN KEY (property_id, folio_id)
                REFERENCES finance.folios (property_id, id),
            CONSTRAINT ck_alloc_amount_positive CHECK (amount > 0)
        )
        """
    )

    # ---- refunds ----
    op.execute(
        """
        CREATE TABLE finance.refunds (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            organization_id uuid NOT NULL,
            property_id uuid NOT NULL,
            payment_id uuid NOT NULL,
            amount numeric(19,4) NOT NULL,
            currency varchar(3) NOT NULL DEFAULT 'INR',
            reason varchar(300),
            approval_request_id uuid,
            provider_refund_id varchar(120),
            status varchar(20) NOT NULL DEFAULT 'succeeded',
            created_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT fk_refund_payment
                FOREIGN KEY (property_id, payment_id)
                REFERENCES finance.payments (property_id, id),
            CONSTRAINT ck_refund_amount_positive CHECK (amount > 0),
            CONSTRAINT ck_refund_status CHECK (
                status IN ('succeeded','pending','failed')
            )
        )
        """
    )

    # ---- invoices ----
    op.execute(
        """
        CREATE TABLE finance.invoices (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            organization_id uuid NOT NULL,
            property_id uuid NOT NULL,
            folio_id uuid NOT NULL,
            fiscal_series varchar(20) NOT NULL DEFAULT 'A',
            invoice_number bigint NOT NULL,
            status varchar(20) NOT NULL DEFAULT 'draft',
            issued_at timestamptz,
            customer_snapshot jsonb,
            totals_snapshot jsonb,
            created_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT uq_invoice_number
                UNIQUE (property_id, fiscal_series, invoice_number),
            CONSTRAINT ck_invoice_status CHECK (
                status IN ('draft','issued','cancelled')
            )
        )
        """
    )

    op.execute(
        """
        CREATE TABLE finance.invoice_lines (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            invoice_id uuid NOT NULL REFERENCES finance.invoices(id),
            folio_entry_id uuid NOT NULL REFERENCES finance.folio_entries(id),
            description_snapshot varchar(300) NOT NULL,
            quantity numeric(12,3) NOT NULL DEFAULT 1,
            amounts_snapshot jsonb NOT NULL,
            CONSTRAINT uq_invoice_line_entry UNIQUE (folio_entry_id)
        )
        """
    )

    op.execute(
        """
        CREATE TABLE finance.credit_notes (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            invoice_id uuid NOT NULL REFERENCES finance.invoices(id),
            number bigint NOT NULL,
            reason varchar(300),
            amount numeric(19,4) NOT NULL,
            issued_at timestamptz NOT NULL DEFAULT now()
        )
        """
    )

    # ---- business_days + night audit ----
    op.execute(
        """
        CREATE TABLE finance.business_days (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            organization_id uuid NOT NULL,
            property_id uuid NOT NULL,
            business_date date NOT NULL,
            status varchar(20) NOT NULL DEFAULT 'open',
            closed_at timestamptz,
            closed_by uuid,
            created_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT uq_business_day UNIQUE (property_id, business_date),
            CONSTRAINT ck_business_day_status CHECK (
                status IN ('open','closing','closed')
            )
        )
        """
    )

    op.execute(
        """
        CREATE TABLE finance.night_audit_runs (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            organization_id uuid NOT NULL,
            property_id uuid NOT NULL,
            business_date date NOT NULL,
            run_number int NOT NULL DEFAULT 1,
            status varchar(20) NOT NULL DEFAULT 'running',
            started_at timestamptz NOT NULL DEFAULT now(),
            completed_at timestamptz,
            CONSTRAINT ck_audit_run_status CHECK (
                status IN ('running','completed','failed')
            )
        )
        """
    )
    # Only one SUCCESSFUL close per business date (§6).
    op.execute(
        """
        CREATE UNIQUE INDEX uq_one_completed_audit_per_day
        ON finance.night_audit_runs (property_id, business_date)
        WHERE status = 'completed'
        """
    )

    op.execute(
        """
        CREATE TABLE finance.night_audit_steps (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            run_id uuid NOT NULL REFERENCES finance.night_audit_runs(id),
            step_code varchar(50) NOT NULL,
            status varchar(20) NOT NULL DEFAULT 'pending',
            error text,
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT ck_audit_step_status CHECK (
                status IN ('pending','completed','failed')
            )
        )
        """
    )

    # ---- indexes (§12) ----
    op.execute(
        "CREATE INDEX ix_entries_folio_date "
        "ON finance.folio_entries(property_id, folio_id, business_date)"
    )
    op.execute(
        "CREATE INDEX ix_alloc_payment ON finance.payment_allocations(payment_id)"
    )
    op.execute(
        "CREATE INDEX ix_alloc_folio ON finance.payment_allocations(folio_id)"
    )
    op.execute("CREATE INDEX ix_refunds_payment ON finance.refunds(payment_id)")
    op.execute("CREATE INDEX ix_invoices_folio ON finance.invoices(folio_id)")


def downgrade() -> None:
    for table in (
        "night_audit_steps",
        "night_audit_runs",
        "business_days",
        "credit_notes",
        "invoice_lines",
        "invoices",
        "refunds",
        "payment_allocations",
        "payments",
        "payment_intents",
        "folio_entry_taxes",
        "folio_entries",
        "folios",
        "charge_codes",
        "tax_rules",
    ):
        op.execute(f"DROP TABLE IF EXISTS finance.{table} CASCADE")
