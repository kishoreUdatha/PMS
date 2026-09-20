"""Fixed-price option for rate plans and packages (screens 033 / 035)

Revision ID: 0013_flat_rate_pricing
Revises: 0012_packages_promotions
Create Date: 2026-09-08

Rate plans and packages could only be priced as an adjustment to the room
type's base rate. That keeps everything in step when the base rate moves, but
it gives no way to simply say "this plan is 7,500 a night" — which is how most
offers are actually decided and sold.

``flat_rate`` adds that. It is nullable and nullable *means* something here:

* NULL  -> price is the room type's base rate with the plan's adjustment applied
           (the existing behaviour, and still the default)
* set   -> that is the nightly price, for every room type the plan covers

So the two modes are distinguishable without a separate mode column, and
clearing the field returns the plan to following the room type again. Existing
rows get NULL, so nothing changes for anything already configured.
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0013_flat_rate_pricing"
down_revision: str | None = "0012_packages_promotions"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    for table in ("rate_plans", "packages"):
        op.execute(
            f"ALTER TABLE property.{table} ADD COLUMN flat_rate numeric(12, 2)"
        )
        op.execute(
            f"ALTER TABLE property.{table} "
            f"ADD CONSTRAINT ck_{table}_flat_rate "
            f"CHECK (flat_rate IS NULL OR flat_rate >= 0)"
        )


def downgrade() -> None:
    for table in ("rate_plans", "packages"):
        op.execute(
            f"ALTER TABLE property.{table} DROP CONSTRAINT IF EXISTS ck_{table}_flat_rate"
        )
        op.execute(f"ALTER TABLE property.{table} DROP COLUMN IF EXISTS flat_rate")
