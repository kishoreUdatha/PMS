"""Cash taken out of a drawer mid-shift and banked.

Revision ID: 0039_cash_drops
Revises: 0038_payment_receipt_no

A busy desk does not leave eighty thousand rupees in a tray until midnight.
Someone lifts most of it, seals it in a bag, and puts it in the safe -- a cash
drop -- and the drawer carries on. The money has not left the property and is
not a refund; it has moved from the till to the safe, and only the till's own
count changes.

Until now there was nowhere to record that. ``expected_cash`` was float plus
cash taken less cash refunded, so the moment anybody banked cash mid-shift the
drawer expected money that was deliberately no longer in it, and the cashier
closed on a shortage they had themselves created by following procedure. The
control that exists to reduce risk read, in the system, as the loss it exists
to prevent.

A drop is its own row rather than an adjustment to the float, because the two
answer different questions: the float is what the drawer started with, and the
drops are what left it during the shift and where each bag went. An auditor
asking "where did the 60,000 go" needs the second, and a float quietly reduced
by 60,000 cannot answer it.

Amounts are positive; the direction is in the table's name. A negative drop
would be cash going back INTO the till, which is a float top-up and a
different thing again -- not modelled here rather than modelled badly.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0039_cash_drops"
down_revision: str | None = "0038_payment_receipt_no"
branch_labels: str | None = None
depends_on: str | None = None

RUNTIME_ROLE = "pms_app"

#: Both halves matter, and the second is not redundant.
#:
#: The shift is the authority on whose money this is, so visibility follows
#: it. But the row carries its own organization_id as every tenant table here
#: does, and a policy that only consulted the shift let a caller write a row
#: LABELLED as another tenant while attached to their own drawer -- invisible
#: to the tenant it named, counted for the one it did not, and a lie in any
#: report that trusts the column. The second clause makes the two agree.
_POLICY = ("tenancy.org_visible(organization_id)"
           " AND EXISTS (SELECT 1 FROM finance.cashier_shifts s"
           " WHERE s.id = cash_drops.cashier_shift_id"
           " AND s.organization_id = cash_drops.organization_id)")


def upgrade() -> None:
    op.create_table(
        "cash_drops",
        sa.Column("id", sa.dialects.postgresql.UUID(as_uuid=True),
                  primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("organization_id", sa.dialects.postgresql.UUID(as_uuid=True),
                  nullable=False),
        sa.Column("property_id", sa.dialects.postgresql.UUID(as_uuid=True),
                  nullable=False),
        sa.Column("cashier_shift_id", sa.dialects.postgresql.UUID(as_uuid=True),
                  nullable=False),
        sa.Column("amount", sa.Numeric(19, 4), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False,
                  server_default="INR"),
        #: Where the bag went and how it is identified -- a safe, a deposit
        #: slip, a bank reference. What somebody chasing this money later has
        #: to go on.
        sa.Column("destination", sa.String(40), nullable=False,
                  server_default="safe"),
        sa.Column("reference", sa.String(120)),
        sa.Column("note", sa.String(300)),
        sa.Column("dropped_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("dropped_by", sa.dialects.postgresql.UUID(as_uuid=True)),
        #: Witnessed drops are the norm where the amounts are large. Recorded
        #: when there was one, never invented when there was not.
        sa.Column("witnessed_by", sa.dialects.postgresql.UUID(as_uuid=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.CheckConstraint("amount > 0", name="ck_cash_drop_positive"),
        sa.ForeignKeyConstraint(["cashier_shift_id"],
                                ["finance.cashier_shifts.id"],
                                name="fk_cash_drop_shift"),
        schema="finance",
    )
    op.create_index("ix_cash_drop_shift", "cash_drops", ["cashier_shift_id"],
                    schema="finance")

    op.execute("ALTER TABLE finance.cash_drops ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE finance.cash_drops FORCE ROW LEVEL SECURITY")
    op.execute("DROP POLICY IF EXISTS tenant_isolation ON finance.cash_drops")
    op.execute(
        "CREATE POLICY tenant_isolation ON finance.cash_drops "
        f"USING ({_POLICY}) WITH CHECK ({_POLICY})")
    op.execute(f"""
        DO $$
        BEGIN
          IF EXISTS (SELECT FROM pg_roles WHERE rolname = '{RUNTIME_ROLE}') THEN
            GRANT SELECT, INSERT, UPDATE, DELETE
              ON finance.cash_drops TO {RUNTIME_ROLE};
          END IF;
        END
        $$
    """)


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS tenant_isolation ON finance.cash_drops")
    op.drop_index("ix_cash_drop_shift", table_name="cash_drops",
                  schema="finance")
    op.drop_table("cash_drops", schema="finance")
