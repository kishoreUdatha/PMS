"""A folio number a guest can read off their bill

Revision ID: 0025_folio_number
Revises: 0024_payment_method_detail
Create Date: 2026-09-13

Every screen that shows a folio has been deriving its label from the row's
uuid -- ``FOL/C77AE395``. It is unique and stable, and it is not a folio
number: nobody reads it aloud, it sorts by nothing, and two folios opened
minutes apart look unrelated. This gives folios the real thing, FOL-1001
onwards.

The same shape as guest references (booking-core 0045), for the same reasons,
and the reasoning is worth repeating rather than cross-referencing:

**Per property, not per organisation.** A folio belongs to a stay at a hotel,
and a hotel's bill numbers are its own. A tenant running three properties
should not find its second hotel opening at FOL-4000 because the first one
was busy.

**Assigned by a trigger.** Five code paths open a folio -- check-in, the CSV
import, checkout, the night audit and the finance routes -- across two
services. A number allocated in one of them is a number missing from the other
four, and the one that gets forgotten is always the fifth.

**The counter is a row, advanced with ON CONFLICT DO UPDATE ... RETURNING.**
One atomic statement: two folios opened in the same instant take two different
numbers because the second waits on the first's row lock rather than reading a
stale maximum.

Existing folios are backfilled in the order they were created, so a property's
first folio is FOL-1001 and the numbering tells the truth about sequence.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0025_folio_number"
down_revision = "0024_payment_method_detail"
branch_labels = None
depends_on = None

FIRST = 1001

_ALLOCATE = f"""
CREATE OR REPLACE FUNCTION finance.next_folio_number(prop uuid)
RETURNS text AS $$
DECLARE
    n bigint;
BEGIN
    -- ``next_value`` always means "the number to hand out next". One
    -- statement takes it and advances it, so two folios opened at the same
    -- moment cannot collide: the second waits on the first's row lock.
    INSERT INTO finance.folio_number_counters AS c (property_id, next_value)
    VALUES (prop, {FIRST} + 1)
    ON CONFLICT (property_id) DO UPDATE SET next_value = c.next_value + 1
    RETURNING c.next_value - 1 INTO n;
    RETURN 'FOL-' || n::text;
END;
$$ LANGUAGE plpgsql;
"""

_TRIGGER_FN = """
CREATE OR REPLACE FUNCTION finance.set_folio_number()
RETURNS trigger AS $$
BEGIN
    IF NEW.folio_no IS NULL THEN
        NEW.folio_no := finance.next_folio_number(NEW.property_id);
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
"""


def upgrade() -> None:
    op.create_table(
        "folio_number_counters",
        sa.Column("property_id", sa.dialects.postgresql.UUID(as_uuid=True),
                  primary_key=True),
        sa.Column("next_value", sa.BigInteger, nullable=False),
        schema="finance",
    )
    op.add_column("folios", sa.Column("folio_no", sa.String(20)),
                  schema="finance")

    op.execute(_ALLOCATE)
    op.execute(_TRIGGER_FN)

    # Oldest folio first, so the sequence matches the order they were opened.
    op.execute(f"""
        WITH ordered AS (
            SELECT id, property_id,
                   {FIRST} + row_number() OVER (
                       PARTITION BY property_id ORDER BY created_at, id
                   ) - 1 AS n
            FROM finance.folios
        )
        UPDATE finance.folios f
           SET folio_no = 'FOL-' || o.n::text
          FROM ordered o
         WHERE o.id = f.id
    """)
    op.execute(f"""
        INSERT INTO finance.folio_number_counters (property_id, next_value)
        SELECT property_id, {FIRST} + count(*)
          FROM finance.folios
         GROUP BY property_id
        ON CONFLICT (property_id) DO UPDATE
            SET next_value = EXCLUDED.next_value
    """)

    op.execute("""
        CREATE TRIGGER folios_set_number
        BEFORE INSERT ON finance.folios
        FOR EACH ROW EXECUTE FUNCTION finance.set_folio_number()
    """)
    # Unique per property, and only now -- the backfill had to finish first.
    op.create_unique_constraint(
        "uq_folio_number", "folios", ["property_id", "folio_no"],
        schema="finance")


def downgrade() -> None:
    op.drop_constraint("uq_folio_number", "folios", schema="finance")
    op.execute("DROP TRIGGER IF EXISTS folios_set_number ON finance.folios")
    op.execute("DROP FUNCTION IF EXISTS finance.set_folio_number()")
    op.execute("DROP FUNCTION IF EXISTS finance.next_folio_number(uuid)")
    op.drop_column("folios", "folio_no", schema="finance")
    op.drop_table("folio_number_counters", schema="finance")
