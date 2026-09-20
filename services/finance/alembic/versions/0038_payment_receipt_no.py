"""A stored receipt number on every payment.

Revision ID: 0038_payment_receipt_no
Revises: 0037_folio_entry_posted_by

The number printed on a payment receipt used to be computed, never stored, and
computed differently in two places: the receipt PDF handed the guest
``RCPT-713ACD36`` while the screen showed ``CBR-RCP-20260917-713ACD36`` for the
same payment -- the ``CBR-`` being one hotel's brand hardcoded into a component
every hotel on the platform renders. A guest ringing up to quote the number off
their receipt was reading one the desk could not search for, because it existed
in no column.

Both now come from one function, and this puts the answer in a column so it can
be looked up rather than recomputed.

**Why a counter and not the payment's id.** The derived form took the first
eight hex characters of the uuid -- thirty-two bits. That is fine as a label
and unusable as a key: at fifty thousand payments there is a one-in-four chance
that two of them collide, and by a few hundred thousand it is a certainty. Put
a unique index on that and a hotel's payment eventually fails to record at the
desk, for a reason nobody could act on. So the same shape the folio numbers
already use: a per-property counter advanced by one statement that takes and
increments together, so two payments taken at the same moment cannot collide.

**Why existing rows keep their old number.** Receipts already printed and
handed over carry ``RCPT-<id8>``. Renumbering them would break the only link
between the paper in a guest's wallet and the row in this table, so the
backfill copies the number each payment already had rather than issuing a new
one. Counters start above the backfill, and everything taken from here on is
sequential. Two formats in one column is the honest record of a system that
changed how it numbers; one format achieved by rewriting history is not.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0038_payment_receipt_no"
down_revision: str | None = "0037_folio_entry_posted_by"
branch_labels: str | None = None
depends_on: str | None = None

RUNTIME_ROLE = "pms_app"

#: Where each property's sequence starts. Clear of the backfilled ids, and the
#: same opening number the folios use, so a desk reading RCPT-1001 beside
#: FOL-1001 is not being told the two are related -- only that both are the
#: first of their kind at that property.
FIRST = 1001

_ALLOCATE = """
CREATE OR REPLACE FUNCTION finance.next_receipt_number(prop uuid)
RETURNS text AS $$
DECLARE
    n bigint;
BEGIN
    -- One statement takes the number and advances it, so two payments taken at
    -- the same moment cannot be handed the same receipt: the second waits on
    -- the first's row lock.
    INSERT INTO finance.receipt_number_counters AS c (property_id, next_value)
    VALUES (prop, %(first)s + 1)
    ON CONFLICT (property_id) DO UPDATE SET next_value = c.next_value + 1
    RETURNING c.next_value - 1 INTO n;
    RETURN 'RCPT-' || n::text;
END;
$$ LANGUAGE plpgsql;
""" % {"first": FIRST}

_TRIGGER_FN = """
CREATE OR REPLACE FUNCTION finance.set_receipt_number()
RETURNS trigger AS $$
BEGIN
    IF NEW.receipt_no IS NULL THEN
        NEW.receipt_no := finance.next_receipt_number(NEW.property_id);
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
"""

#: The counter carries no organisation of its own, so it reaches one through
#: the property it belongs to -- exactly as folio_number_counters does. Without
#: this the table would be the one place in finance a tenant could read another
#: tenant's row, and the numbering trigger would be the way in.
_POLICY = ("EXISTS (SELECT 1 FROM iam.properties p WHERE p.id = property_id"
           " AND tenancy.org_visible(p.organization_id))")


def upgrade() -> None:
    op.create_table(
        "receipt_number_counters",
        sa.Column("property_id", sa.dialects.postgresql.UUID(as_uuid=True),
                  primary_key=True),
        sa.Column("next_value", sa.BigInteger, nullable=False),
        schema="finance",
    )
    op.add_column("payments", sa.Column("receipt_no", sa.String(32)),
                  schema="finance")

    op.execute(_ALLOCATE)
    op.execute(_TRIGGER_FN)

    # What each payment's receipt already said, kept.
    op.execute("""
        UPDATE finance.payments
           SET receipt_no = 'RCPT-' || upper(left(id::text, 8))
         WHERE receipt_no IS NULL
    """)
    # Every property starts clear of the backfill, including ones that have
    # taken no payment yet and so are absent from the table above.
    op.execute(f"""
        INSERT INTO finance.receipt_number_counters (property_id, next_value)
        SELECT id, {FIRST} FROM iam.properties
        ON CONFLICT (property_id) DO NOTHING
    """)

    op.execute("""
        CREATE TRIGGER payments_set_receipt_number
        BEFORE INSERT ON finance.payments
        FOR EACH ROW EXECUTE FUNCTION finance.set_receipt_number()
    """)

    # Only now: the backfill had to finish first.
    op.create_unique_constraint(
        "uq_payment_receipt_no", "payments", ["property_id", "receipt_no"],
        schema="finance")

    op.execute("ALTER TABLE finance.receipt_number_counters "
               "ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE finance.receipt_number_counters "
               "FORCE ROW LEVEL SECURITY")
    op.execute("DROP POLICY IF EXISTS tenant_isolation "
               "ON finance.receipt_number_counters")
    op.execute(
        "CREATE POLICY tenant_isolation ON finance.receipt_number_counters "
        f"USING ({_POLICY}) WITH CHECK ({_POLICY})")
    op.execute(f"""
        DO $$
        BEGIN
          IF EXISTS (SELECT FROM pg_roles WHERE rolname = '{RUNTIME_ROLE}') THEN
            GRANT SELECT, INSERT, UPDATE, DELETE
              ON finance.receipt_number_counters TO {RUNTIME_ROLE};
          END IF;
        END
        $$
    """)


def downgrade() -> None:
    op.drop_constraint("uq_payment_receipt_no", "payments", schema="finance")
    op.execute("DROP TRIGGER IF EXISTS payments_set_receipt_number "
               "ON finance.payments")
    op.execute("DROP FUNCTION IF EXISTS finance.set_receipt_number()")
    op.execute("DROP FUNCTION IF EXISTS finance.next_receipt_number(uuid)")
    op.drop_column("payments", "receipt_no", schema="finance")
    op.drop_table("receipt_number_counters", schema="finance")
