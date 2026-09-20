"""Tie a corporate rate plan to the account it belongs to

Revision ID: 0054_corporate_rate_account
Revises: 0053_complimentary
Create Date: 2026-09-16

A corporate rate plan named the company it was for in a free-text box:
``corporate_account varchar(120)``, typed by hand. Somebody sets up "BlueWave
Technologies" while the account in Companies & Agents is "BlueWave
Technologies Pvt Ltd", and nothing anywhere notices -- the two are never
compared, because nothing holds them together.

That is why taking a corporate booking never offered the corporate rate. The
desk picks the company for *billing* (``reservations.commercial_account_id``,
a real foreign key) and then picks a rate plan from the whole list, with
nothing connecting the two. The negotiated rate is applied only if whoever is
at the desk happens to remember it exists, which is the sort of thing that is
remembered in March and forgotten in December.

So the plan now points at the account by id. The old text is kept, not
dropped: it is backfilled where a name matches and left alone where it does
not, because a plan whose text never matched any account still records what
somebody meant, and deleting that would destroy the only clue to which
company a rate was negotiated with.

ON DELETE RESTRICT, deliberately. Removing a company that has a negotiated
rate hanging off it should fail loudly rather than quietly orphan the rate --
and Companies & Agents already refuses to delete an account that is in use.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision: str = "0054_corporate_rate_account"
down_revision: str | None = "0053_complimentary"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "rate_plans",
        sa.Column("commercial_account_id", pg.UUID(as_uuid=True), nullable=True),
        schema="property",
    )
    op.create_foreign_key(
        "fk_rate_plan_account", "rate_plans", "commercial_accounts",
        ["commercial_account_id"], ["id"],
        source_schema="property", referent_schema="engagement",
        ondelete="RESTRICT",
    )
    op.create_index(
        "ix_rate_plan_account", "rate_plans", ["commercial_account_id"],
        schema="property",
        postgresql_where=sa.text("commercial_account_id IS NOT NULL"),
    )

    # Backfill by name, case- and space-insensitively, and only where exactly
    # one account matches. An ambiguous name is left for a person to resolve:
    # guessing which of two similarly-named companies a negotiated rate
    # belongs to is how the wrong company gets billed the wrong rate.
    op.execute(
        """
        UPDATE property.rate_plans rp
           SET commercial_account_id = m.id
          FROM (
            SELECT rp2.id AS plan_id, min(ca.id::text)::uuid AS id
              FROM property.rate_plans rp2
              JOIN engagement.commercial_accounts ca
                ON lower(btrim(ca.name)) = lower(btrim(rp2.corporate_account))
               AND ca.organization_id = rp2.organization_id
             WHERE rp2.plan_type = 'corporate'
               AND COALESCE(btrim(rp2.corporate_account), '') <> ''
             GROUP BY rp2.id
            HAVING count(*) = 1
          ) m
         WHERE rp.id = m.plan_id
        """
    )

    # A corporate plan must still say which company it is for -- by id now, or
    # by the old text where the backfill could not resolve it.
    op.drop_constraint("ck_rate_plan_corporate", "rate_plans",
                       schema="property", type_="check")
    op.create_check_constraint(
        "ck_rate_plan_corporate", "rate_plans",
        "plan_type <> 'corporate'"
        " OR commercial_account_id IS NOT NULL"
        " OR COALESCE(btrim(corporate_account), '') <> ''",
        schema="property",
    )


def downgrade() -> None:
    op.drop_constraint("ck_rate_plan_corporate", "rate_plans",
                       schema="property", type_="check")
    op.create_check_constraint(
        "ck_rate_plan_corporate", "rate_plans",
        "plan_type <> 'corporate' OR corporate_account IS NOT NULL",
        schema="property",
    )
    op.drop_index("ix_rate_plan_account", "rate_plans", schema="property")
    op.drop_constraint("fk_rate_plan_account", "rate_plans",
                       schema="property", type_="foreignkey")
    op.drop_column("rate_plans", "commercial_account_id", schema="property")
