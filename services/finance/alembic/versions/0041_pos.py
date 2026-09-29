"""Restaurant POS: outlets, tables, checks and kitchen tickets.

Revision ID: 0041_pos
Revises: 0040_payment_links

Guest Services (0029, 0031) already knows the menu (categories that decide
the tax, items with prices) and how to put an order on a guest's bill. What
a restaurant needs on top of that is a check that stays open through a meal,
kitchen tickets as each round goes in, and a way to settle at the end, either
paid at the table or posted to a room. The POS reuses the menu rather than
growing a second one, so the room-service price of a coffee and the
restaurant's are the same item.

**A walk-in's check gets its own folio.** The ledger only knows folios:
every charge and every payment belongs to one, and that is what keeps the
books balanced. A diner who is not staying has no folio, so settling a
walk-in check opens one of the new type ``pos``, posts the lines and their
tax to it, takes the payment against it, and closes it as settled in the
same transaction. It is never open, so it never appears on a list of open
folios. A check posted to a room uses the guest's own folio instead.

**Lines snapshot the menu,** as service order lines do. Repricing the menu
must not change a bill already on the table.

**Numbering per property.** ``pos_counters`` hands out check and KOT numbers
under a row lock, so two terminals cannot print the same number.
"""
from __future__ import annotations

from alembic import op

revision: str = "0041_pos"
down_revision: str | None = "0040_payment_links"
branch_labels: str | None = None
depends_on: str | None = None

RUNTIME_ROLE = "pms_app"
TABLES = ("pos_outlets", "pos_tables", "pos_counters", "pos_checks",
          "pos_kots", "pos_check_lines")


def upgrade() -> None:
    op.execute("""
        ALTER TABLE finance.folios DROP CONSTRAINT IF EXISTS ck_folio_type;
        ALTER TABLE finance.folios ADD CONSTRAINT ck_folio_type
            CHECK (type IN ('guest', 'group', 'company', 'paymaster', 'pos'));

        CREATE TABLE IF NOT EXISTS finance.pos_outlets (
            id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            organization_id uuid NOT NULL,
            property_id     uuid NOT NULL,
            name            varchar(80) NOT NULL,
            kind            varchar(20) NOT NULL DEFAULT 'restaurant'
                              CHECK (kind IN ('restaurant', 'bar', 'cafe',
                                              'room_service', 'other')),
            is_active       boolean NOT NULL DEFAULT true,
            sort_order      integer NOT NULL DEFAULT 0,
            created_at      timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT uq_pos_outlet_name UNIQUE (property_id, name)
        );

        CREATE TABLE IF NOT EXISTS finance.pos_tables (
            id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            organization_id uuid NOT NULL,
            property_id     uuid NOT NULL,
            outlet_id       uuid NOT NULL REFERENCES finance.pos_outlets(id),
            label           varchar(20) NOT NULL,
            seats           integer NOT NULL DEFAULT 4 CHECK (seats BETWEEN 1 AND 50),
            is_active       boolean NOT NULL DEFAULT true,
            CONSTRAINT uq_pos_table_label UNIQUE (outlet_id, label)
        );

        CREATE TABLE IF NOT EXISTS finance.pos_counters (
            property_id     uuid PRIMARY KEY,
            organization_id uuid NOT NULL,
            next_check      integer NOT NULL DEFAULT 1,
            next_kot        integer NOT NULL DEFAULT 1
        );

        CREATE TABLE IF NOT EXISTS finance.pos_checks (
            id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            organization_id uuid NOT NULL,
            property_id     uuid NOT NULL,
            outlet_id       uuid NOT NULL REFERENCES finance.pos_outlets(id),
            table_id        uuid NULL REFERENCES finance.pos_tables(id),
            number          varchar(20) NOT NULL,
            status          varchar(16) NOT NULL DEFAULT 'open'
                              CHECK (status IN ('open', 'settled', 'room', 'void')),
            covers          integer NULL CHECK (covers IS NULL OR covers BETWEEN 1 AND 200),
            guest_label     varchar(120) NULL,
            -- Set when the check is posted to a room.
            reservation_id  uuid NULL,
            -- The folio it was settled against: a pos folio, or a guest's.
            folio_id        uuid NULL REFERENCES finance.folios(id),
            payment_id      uuid NULL,
            method          varchar(30) NULL,
            business_date   date NOT NULL,
            subtotal        numeric(14,2) NOT NULL DEFAULT 0,
            total           numeric(14,2) NULL,
            currency        char(3) NOT NULL DEFAULT 'INR',
            note            varchar(300) NULL,
            void_reason     varchar(300) NULL,
            opened_by       uuid NULL,
            opened_at       timestamptz NOT NULL DEFAULT now(),
            closed_by       uuid NULL,
            closed_at       timestamptz NULL,
            CONSTRAINT uq_pos_check_number UNIQUE (property_id, number)
        );
        CREATE INDEX IF NOT EXISTS ix_pos_checks_open
            ON finance.pos_checks (property_id, status, outlet_id);

        CREATE TABLE IF NOT EXISTS finance.pos_kots (
            id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            organization_id uuid NOT NULL,
            property_id     uuid NOT NULL,
            check_id        uuid NOT NULL REFERENCES finance.pos_checks(id),
            number          integer NOT NULL,
            created_by      uuid NULL,
            created_at      timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT uq_pos_kot_number UNIQUE (property_id, number)
        );

        CREATE TABLE IF NOT EXISTS finance.pos_check_lines (
            id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            organization_id uuid NOT NULL,
            property_id     uuid NOT NULL,
            check_id        uuid NOT NULL REFERENCES finance.pos_checks(id),
            item_id         uuid NOT NULL,
            item_name       varchar(150) NOT NULL,
            category        varchar(120) NOT NULL,
            bills_as        varchar(40) NOT NULL,
            quantity        integer NOT NULL CHECK (quantity BETWEEN 1 AND 999),
            unit_price      numeric(14,2) NOT NULL,
            amount          numeric(14,2) NOT NULL,
            note            varchar(200) NULL,
            -- NULL until the line goes to the kitchen on a KOT.
            kot_id          uuid NULL REFERENCES finance.pos_kots(id),
            status          varchar(8) NOT NULL DEFAULT 'active'
                              CHECK (status IN ('active', 'void')),
            void_reason     varchar(200) NULL,
            created_by      uuid NULL,
            created_at      timestamptz NOT NULL DEFAULT now()
        );
        CREATE INDEX IF NOT EXISTS ix_pos_lines_check
            ON finance.pos_check_lines (check_id);
    """)
    for t in TABLES:
        op.execute(f"ALTER TABLE finance.{t} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE finance.{t} FORCE ROW LEVEL SECURITY")
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON finance.{t}")
        op.execute(f"""
            CREATE POLICY tenant_isolation ON finance.{t}
            USING (tenancy.org_visible(organization_id))
            WITH CHECK (tenancy.org_visible(organization_id))
        """)
        op.execute(f"""
            DO $$
            BEGIN
              IF EXISTS (SELECT FROM pg_roles WHERE rolname = '{RUNTIME_ROLE}') THEN
                GRANT SELECT, INSERT, UPDATE, DELETE ON finance.{t} TO {RUNTIME_ROLE};
              END IF;
            END
            $$
        """)


def downgrade() -> None:
    for t in reversed(TABLES):
        op.execute(f"DROP TABLE IF EXISTS finance.{t}")
    op.execute("""
        ALTER TABLE finance.folios DROP CONSTRAINT IF EXISTS ck_folio_type;
        ALTER TABLE finance.folios ADD CONSTRAINT ck_folio_type
            CHECK (type IN ('guest', 'group', 'company', 'paymaster'));
    """)
