"""A guest reference somebody can read down a phone

Revision ID: 0045_guest_reference
Revises: 0044_guest_profile
Create Date: 2026-09-13

The guest profile was showing the first eight characters of the record's uuid
as a "Guest ID" -- EB59625E. It is unique and it is short, and it is still not
something a receptionist reads aloud to a guest without stumbling. This adds
the real thing: G-1001, G-1002, and so on.

**Per organisation, not global.** Each tenant counts from its own G-1001. A
single shared sequence would leave every tenant's numbering full of gaps where
other tenants took values -- and the size of those gaps says how many guests
the other tenants are adding, which is nobody else's business.

**Assigned by a trigger, not by the application.** Six different code paths
insert a guest: the desk, check-in, an enquiry converting, the booking engine,
a channel booking, and the CSV import. A reference allocated in one of them is
a reference missing from the other five, and the sixth path is always the one
nobody remembers. The trigger cannot be bypassed.

**The counter is a row, updated with ON CONFLICT DO UPDATE ... RETURNING.**
That is one atomic statement: two guests created in the same instant take two
different numbers, because the second waits on the first's row lock rather
than reading a stale maximum. Computing ``max(reference) + 1`` is the version
of this that works until the day two people press Save together.

Numbering starts at 1001 rather than 1. G-1 reads as a test record, and the
first real guest of a property deserves better than looking like one.

Existing guests are backfilled in the order they were created, so the oldest
guest of each organisation gets G-1001.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0045_guest_reference"
down_revision = "0044_guest_profile"
branch_labels = None
depends_on = None

FIRST = 1001

# One row per organisation holding the next number to hand out. A table rather
# than a Postgres sequence because sequences are global objects: one per tenant
# would mean creating and dropping DDL as tenants come and go.
_ALLOCATE = f"""
CREATE OR REPLACE FUNCTION engagement.next_guest_reference(org uuid)
RETURNS text AS $$
DECLARE
    n bigint;
BEGIN
    -- ``next_value`` always means "the number to hand out next". One
    -- statement takes it and advances it, so two guests created in the same
    -- instant cannot take the same number: the second waits on the first's
    -- row lock instead of reading a value that is already stale.
    INSERT INTO engagement.guest_reference_counters AS c
        (organization_id, next_value)
    VALUES (org, {FIRST} + 1)
    ON CONFLICT (organization_id) DO UPDATE SET next_value = c.next_value + 1
    RETURNING c.next_value - 1 INTO n;
    RETURN 'G-' || n::text;
END;
$$ LANGUAGE plpgsql;
"""

# BEFORE INSERT, and only when the row does not already carry one -- so the
# backfill below, and any future migration that needs to set a specific value,
# are not fought by the trigger.
_TRIGGER_FN = """
CREATE OR REPLACE FUNCTION engagement.set_guest_reference()
RETURNS trigger AS $$
BEGIN
    IF NEW.reference IS NULL THEN
        NEW.reference := engagement.next_guest_reference(NEW.organization_id);
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
"""


def upgrade() -> None:
    op.create_table(
        "guest_reference_counters",
        sa.Column("organization_id", sa.dialects.postgresql.UUID(as_uuid=True),
                  primary_key=True),
        sa.Column("next_value", sa.BigInteger, nullable=False),
        schema="engagement",
    )
    op.add_column("guests", sa.Column("reference", sa.String(20)),
                  schema="engagement")

    op.execute(_ALLOCATE)
    op.execute(_TRIGGER_FN)

    # Backfill in creation order, oldest first, so the numbering tells the
    # truth about who was a guest here longest.
    op.execute(f"""
        WITH ordered AS (
            SELECT id, organization_id,
                   {FIRST} + row_number() OVER (
                       PARTITION BY organization_id ORDER BY created_at, id
                   ) - 1 AS n
            FROM engagement.guests
        )
        UPDATE engagement.guests g
           SET reference = 'G-' || o.n::text
          FROM ordered o
         WHERE o.id = g.id
    """)
    # Start each tenant's counter after whatever the backfill used.
    op.execute(f"""
        INSERT INTO engagement.guest_reference_counters
            (organization_id, next_value)
        SELECT organization_id, {FIRST} + count(*)
          FROM engagement.guests
         GROUP BY organization_id
        ON CONFLICT (organization_id) DO UPDATE
            SET next_value = EXCLUDED.next_value
    """)

    op.execute("""
        CREATE TRIGGER guests_set_reference
        BEFORE INSERT ON engagement.guests
        FOR EACH ROW EXECUTE FUNCTION engagement.set_guest_reference()
    """)
    # Unique per tenant, and only now -- the backfill had to finish first.
    op.create_unique_constraint(
        "uq_guest_reference", "guests", ["organization_id", "reference"],
        schema="engagement")


def downgrade() -> None:
    op.drop_constraint("uq_guest_reference", "guests", schema="engagement")
    op.execute("DROP TRIGGER IF EXISTS guests_set_reference "
               "ON engagement.guests")
    op.execute("DROP FUNCTION IF EXISTS engagement.set_guest_reference()")
    op.execute("DROP FUNCTION IF EXISTS engagement.next_guest_reference(uuid)")
    op.drop_column("guests", "reference", schema="engagement")
    op.drop_table("guest_reference_counters", schema="engagement")
