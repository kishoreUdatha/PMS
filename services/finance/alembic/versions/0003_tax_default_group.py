"""Only one tax group applies to a charge (screen 118)

Revision ID: 0003_tax_default_group
Revises: 0002_tax_charges
Create Date: 2026-09-09

0002 modelled a tax group's ``applicability`` as "this applies here". That is
right for service charges and levies, which genuinely stack — a restaurant bill
carries GST *and* a service charge. It is wrong for GST groups, which are
alternatives: a charge is 5% or 12% or 28%, never their sum.

The seeded data made that concrete. Three groups list ``fnb_outlets``
(GST_STD 5, GST_RED 12, GST_28 28), so a 1,000 restaurant charge came out taxed
550 — 45% GST plus service charge. ``other_services`` came out at 46%.

The mockup already drew the distinction and this schema lost it: GST_RED is
labelled "F&B (selected)" and GST_28 "Alcohol, Tobacco". They are available in
those areas for particular items, not the rate everything there pays.

``is_default`` restores it. For a tax group, applicability now means "may be
used here" and ``is_default`` marks the one that applies when a charge does not
name a specific group. Service charges and other taxes are unaffected: they all
still apply, because stacking is what they are for.
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0003_tax_default_group"
down_revision: str | None = "0002_tax_charges"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE finance.tax_rules "
        "ADD COLUMN is_default boolean NOT NULL DEFAULT false"
    )
    # Only a tax group has alternatives to choose between.
    op.execute(
        "ALTER TABLE finance.tax_rules ADD CONSTRAINT ck_tax_default_group "
        "CHECK (NOT is_default OR charge_type = 'tax_group')"
    )
    # The standard rate is what rooms, F&B and spa pay unless a charge says
    # otherwise; banquets and other services default to 18%.
    op.execute(
        "UPDATE finance.tax_rules SET is_default = true "
        "WHERE charge_type = 'tax_group' AND code IN ('GST_STD', 'GST_18')"
    )


def downgrade() -> None:
    op.execute(
        "ALTER TABLE finance.tax_rules DROP CONSTRAINT IF EXISTS ck_tax_default_group"
    )
    op.execute("ALTER TABLE finance.tax_rules DROP COLUMN IF EXISTS is_default")
