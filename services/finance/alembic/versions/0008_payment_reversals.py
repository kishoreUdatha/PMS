"""Asking to undo a payment, kept apart from undoing it (screen 115)

Revision ID: 0008_payment_reversals
Revises: 0007_adj_reversal_entry
Create Date: 2026-09-09

``post_refund`` has been able to reverse a payment since the ledger was
written: it locks the payment, refuses to give back more than was taken, and
posts a debit per allocation so the folio balance rises again. What it has
never had is a reason, an approver, or a record that somebody asked.

``payment_reversals`` is the request. It exists before any money moves and
often instead of it — a request over the policy threshold sits here waiting for
a decision while the payment stays exactly as it was. ``refund_id`` is null
until the ledger actually runs, and is the only honest signal that the money
went back.

**Three kinds, because they are three different events.**

``void`` — the payment was taken today and should not have been. Same business
day, whole amount, no partial. The nearest thing to it never having happened.

``refund`` — the guest is getting money back. Can be partial, and is the only
kind where cash genuinely leaves the property.

``reversal`` — a settled payment was recorded wrongly: wrong folio, wrong
amount, duplicated. The correction is a bookkeeping one and takes the whole
amount.

All three post the same ledger movement. What differs is what they mean, who
may approve them, and how much may go. Keeping them as one field rather than
three tables means a payment's history reads in one place.

**One open request per payment.** A partial unique index enforces it. Without
it two people can each raise a refund that is individually within the
refundable balance and jointly over it — and ``post_refund`` would only catch
that at the second post, by which time the guest has been promised twice.
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0008_payment_reversals"
down_revision: str | None = "0007_adj_reversal_entry"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

KINDS = ("refund", "void", "reversal")
STATUSES = ("pending_approval", "approved", "rejected", "posted", "cancelled")
REASONS = (
    "duplicate_payment", "wrong_amount", "wrong_folio", "guest_cancelled",
    "service_not_delivered", "overpayment", "deposit_returned",
    "card_declined_later", "goodwill", "other",
)


def upgrade() -> None:
    q = lambda v: ", ".join(f"'{x}'" for x in v)  # noqa: E731

    op.execute(
        f"""
        CREATE TABLE finance.payment_reversals (
            id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            organization_id   uuid NOT NULL,
            property_id       uuid NOT NULL,
            payment_id        uuid NOT NULL REFERENCES finance.payments (id),
            kind              varchar(20) NOT NULL,
            status            varchar(20) NOT NULL DEFAULT 'pending_approval',
            amount            numeric(19, 4) NOT NULL,
            currency          varchar(3) NOT NULL DEFAULT 'INR',
            reason            varchar(30) NOT NULL,
            remarks           varchar(1000) NOT NULL,
            -- The queue entry in iam.approval_requests, when policy needed one.
            approval_request_id uuid,
            approval_required boolean NOT NULL DEFAULT true,
            policy_rule_text  varchar(600),
            -- The finance.refunds row the ledger created. Null until the money
            -- actually moved; this, not the status, is what says it did.
            refund_id         uuid REFERENCES finance.refunds (id),
            posted_at         timestamptz,
            posted_by         uuid,
            created_by        uuid,
            created_at        timestamptz NOT NULL DEFAULT now(),
            updated_at        timestamptz NOT NULL DEFAULT now(),
            version           bigint NOT NULL DEFAULT 0,
            CONSTRAINT ck_rev_kind CHECK (kind IN ({q(KINDS)})),
            CONSTRAINT ck_rev_status CHECK (status IN ({q(STATUSES)})),
            CONSTRAINT ck_rev_reason CHECK (reason IN ({q(REASONS)})),
            CONSTRAINT ck_rev_amount CHECK (amount > 0),
            CONSTRAINT ck_rev_posted CHECK (
                (status = 'posted' AND refund_id IS NOT NULL)
                OR (status <> 'posted' AND refund_id IS NULL)
            )
        )
        """
    )
    op.execute(
        "CREATE INDEX ix_rev_payment ON finance.payment_reversals "
        "(payment_id, created_at DESC)"
    )
    # One live request per payment — see the note above.
    op.execute(
        """
        CREATE UNIQUE INDEX uq_rev_open_payment
            ON finance.payment_reversals (payment_id)
         WHERE status IN ('pending_approval', 'approved')
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS finance.payment_reversals")
