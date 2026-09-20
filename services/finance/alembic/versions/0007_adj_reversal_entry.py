"""A reversed adjustment keeps the entry that posted it

Revision ID: 0007_adj_reversal_entry
Revises: 0006_folio_adjustments
Create Date: 2026-09-09

``ck_adj_posted`` as written in 0006 said: a ``posted`` adjustment points at the
entry that posted it, and anything else points at nothing. That is right for a
pending or rejected adjustment, which never posted anything, and wrong for a
reversed one — which posted a credit, and then had a debit posted back against
it. Clearing ``posted_entry_id`` on reversal would erase the link to the entry
the reversal undid, which is exactly the trail this table exists to keep.

The constraint now names the two states that have posted something. Reversing
an adjustment fails outright against the old rule, so this is a fix to a
constraint that was never satisfiable rather than a change of policy.
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0007_adj_reversal_entry"
down_revision: str | None = "0006_folio_adjustments"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE finance.folio_adjustments DROP CONSTRAINT ck_adj_posted"
    )
    op.execute(
        """
        ALTER TABLE finance.folio_adjustments ADD CONSTRAINT ck_adj_posted CHECK (
            (status IN ('posted', 'reversed') AND posted_entry_id IS NOT NULL)
            OR (status NOT IN ('posted', 'reversed') AND posted_entry_id IS NULL)
        )
        """
    )


def downgrade() -> None:
    op.execute(
        "ALTER TABLE finance.folio_adjustments DROP CONSTRAINT ck_adj_posted"
    )
    op.execute(
        """
        ALTER TABLE finance.folio_adjustments ADD CONSTRAINT ck_adj_posted CHECK (
            (status = 'posted' AND posted_entry_id IS NOT NULL)
            OR (status <> 'posted' AND posted_entry_id IS NULL)
        )
        """
    )
