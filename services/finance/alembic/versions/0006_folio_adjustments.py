"""The record of a correction, kept apart from the correction itself (screen 114)

Revision ID: 0006_folio_adjustments
Revises: 0005_cashiering
Create Date: 2026-09-09

A folio entry is never edited and never deleted. Charging the wrong amount is
undone by posting a *reversing* entry beside the original, which is why
``folio_entries`` has ``reversal_of_id`` and no update path. That much has been
true since the ledger was written; what has been missing is everything around
it.

``folio_adjustments`` is that surround. The reversing entry says the money
moved. This says **why** — which charge was wrong, who noticed, what reason
they gave, what evidence they attached, which policy governed it, who signed
it off, and when it was actually posted. An auditor asking "why is there a
₹5,900 credit on this folio" gets an answer from this table; the ledger alone
can only confirm that there is one.

**The row exists before the money moves.** An adjustment is created, then
routed for approval if policy says so, and only posted once it is approved.
``posted_entry_id`` is null until that happens, and it is the one honest signal
that the folio has actually changed: an adjustment can sit pending for a day
without a single rupee moving, and the folio balance stays exactly where it was.

**Tax travels with the charge it belongs to.** Removing a ₹5,000 spa treatment
that carried ₹900 of tax means removing ₹5,900, not ₹5,000 — the tax was only
ever owed because the treatment was. ``adjust_tax`` records whether the operator
chose that, and ``tax_amount`` records the part that was tax, so a later tax
return can tell the two apart.
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0006_folio_adjustments"
down_revision: str | None = "0005_cashiering"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

KINDS = ("correction", "discount", "allowance")
STATUSES = ("pending_approval", "approved", "rejected", "posted", "reversed")
REASONS = (
    "incorrect_charge", "service_not_availed", "duplicate_posting",
    "price_correction", "guest_complaint", "goodwill", "billing_error", "other",
)


def upgrade() -> None:
    q = lambda v: ", ".join(f"'{x}'" for x in v)  # noqa: E731

    op.execute(
        f"""
        CREATE TABLE finance.folio_adjustments (
            id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            organization_id   uuid NOT NULL,
            property_id       uuid NOT NULL,
            folio_id          uuid NOT NULL REFERENCES finance.folios (id),
            -- The charge being adjusted. Null for an allowance credited to the
            -- folio as a whole rather than against one line.
            folio_entry_id    uuid REFERENCES finance.folio_entries (id),
            kind              varchar(20) NOT NULL,
            status            varchar(20) NOT NULL DEFAULT 'pending_approval',
            -- The whole reduction, and the part of it that was tax.
            amount            numeric(19, 4) NOT NULL,
            tax_amount        numeric(19, 4) NOT NULL DEFAULT 0,
            adjust_tax        boolean NOT NULL DEFAULT true,
            currency          varchar(3) NOT NULL DEFAULT 'INR',
            reason            varchar(30) NOT NULL,
            remarks           varchar(1000) NOT NULL,
            -- Object-store key for the bill, guest request or void slip.
            evidence_key      varchar(500),
            evidence_name     varchar(255),
            -- The queue entry in iam.approval_requests, when policy needed one.
            approval_request_id uuid,
            approval_required boolean NOT NULL DEFAULT true,
            policy_rule_text  varchar(600),
            -- Null until the credit is actually posted. This, not the status,
            -- is what says the folio has changed.
            posted_entry_id   uuid REFERENCES finance.folio_entries (id),
            posted_at         timestamptz,
            posted_by         uuid,
            -- Set on the original when an adjustment is itself undone.
            reversal_of_id    uuid REFERENCES finance.folio_adjustments (id),
            created_by        uuid,
            created_at        timestamptz NOT NULL DEFAULT now(),
            updated_at        timestamptz NOT NULL DEFAULT now(),
            version           bigint NOT NULL DEFAULT 0,
            CONSTRAINT ck_adj_kind CHECK (kind IN ({q(KINDS)})),
            CONSTRAINT ck_adj_status CHECK (status IN ({q(STATUSES)})),
            CONSTRAINT ck_adj_reason CHECK (reason IN ({q(REASONS)})),
            CONSTRAINT ck_adj_amount CHECK (amount > 0),
            CONSTRAINT ck_adj_tax CHECK (tax_amount >= 0 AND tax_amount <= amount),
            -- A posted adjustment must point at the entry that posted it, and
            -- an unposted one must not pretend to.
            CONSTRAINT ck_adj_posted CHECK (
                (status = 'posted' AND posted_entry_id IS NOT NULL)
                OR (status <> 'posted' AND posted_entry_id IS NULL)
            )
        )
        """
    )
    op.execute(
        "CREATE INDEX ix_adj_folio ON finance.folio_adjustments "
        "(folio_id, created_at DESC)"
    )
    # One live adjustment per charge. Two people correcting the same line is
    # how a folio ends up credited twice for one mistake.
    op.execute(
        """
        CREATE UNIQUE INDEX uq_adj_open_entry
            ON finance.folio_adjustments (folio_entry_id)
         WHERE folio_entry_id IS NOT NULL
           AND status IN ('pending_approval', 'approved')
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS finance.folio_adjustments")
