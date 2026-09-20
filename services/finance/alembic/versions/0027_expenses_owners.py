"""Expense vouchers, unit owners and owner contracts

Revision ID: 0027_expenses_owners
Revises: 0026_tax_apply_as
Create Date: 2026-09-14

Two things the back-office reports ask about had nowhere to be recorded.

**Expense vouchers.** Money going out that is not a guest refund: the
plumber, the diesel, the laundry supplier. A voucher is raised, approved,
then paid, and each step is a column rather than a separate log so the Expense
Voucher report reads one row per voucher. ``room_id`` is optional and is how
an operating charge lands on an owned unit's statement.

**Unit owners and their contracts.** A unit in a managed resort can belong to
someone other than the operator, who keeps a management fee and pays the owner
the rest. The owner is one table and the contract -- which room, what fee,
from when to when -- is another, because an owner can hold several units and a
unit can change hands. There is no status column on a contract: whether it is
current is decided by its dates, so the two can never disagree.

No foreign key to ``property.rooms``: that table belongs to booking-core, and
this service does not reach across a schema boundary for integrity. The routes
check the room belongs to the property instead.
"""

from __future__ import annotations

from alembic import op

revision: str = "0027_expenses_owners"
down_revision: str | None = "0026_tax_apply_as"
branch_labels = None
depends_on = None

CATEGORIES = ("maintenance", "utilities", "housekeeping_supplies",
              "food_beverage", "staff", "marketing", "commission", "transport",
              "office", "other")
METHODS = ("cash", "bank_transfer", "upi", "card", "cheque")
STATUSES = ("pending_approval", "approved", "rejected", "paid", "cancelled")


def _quoted(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{v}'" for v in values)


def upgrade() -> None:
    op.execute(f"""
        CREATE TABLE finance.expense_vouchers (
            id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            organization_id uuid NOT NULL,
            property_id     uuid NOT NULL,
            voucher_number  integer NOT NULL,
            expense_date    date NOT NULL,
            payee           varchar(200) NOT NULL,
            category        varchar(40) NOT NULL,
            description     varchar(500),
            amount          numeric(14, 2) NOT NULL,
            tax_amount      numeric(14, 2) NOT NULL DEFAULT 0,
            method          varchar(30) NOT NULL DEFAULT 'cash',
            reference       varchar(100),
            room_id         uuid,
            status          varchar(20) NOT NULL DEFAULT 'pending_approval',
            decided_by      uuid,
            decided_at      timestamptz,
            decision_note   varchar(300),
            paid_at         timestamptz,
            created_by      uuid,
            updated_by      uuid,
            created_at      timestamptz NOT NULL DEFAULT now(),
            updated_at      timestamptz NOT NULL DEFAULT now(),
            version         bigint NOT NULL DEFAULT 1,
            CONSTRAINT uq_expense_voucher_number UNIQUE (property_id, voucher_number),
            CONSTRAINT ck_expense_category CHECK (category IN ({_quoted(CATEGORIES)})),
            CONSTRAINT ck_expense_method CHECK (method IN ({_quoted(METHODS)})),
            CONSTRAINT ck_expense_status CHECK (status IN ({_quoted(STATUSES)})),
            CONSTRAINT ck_expense_amount CHECK (amount > 0),
            CONSTRAINT ck_expense_tax CHECK (tax_amount >= 0)
        )
    """)
    op.execute("CREATE INDEX ix_expense_property_date "
               "ON finance.expense_vouchers (property_id, expense_date)")
    op.execute("CREATE INDEX ix_expense_room "
               "ON finance.expense_vouchers (room_id) WHERE room_id IS NOT NULL")

    op.execute("""
        CREATE TABLE finance.unit_owners (
            id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            organization_id uuid NOT NULL,
            property_id     uuid NOT NULL,
            name            varchar(200) NOT NULL,
            email           varchar(200),
            phone           varchar(60),
            pan             varchar(20),
            gstin           varchar(20),
            bank_details    varchar(300),
            status          varchar(20) NOT NULL DEFAULT 'active',
            notes           text,
            created_by      uuid,
            updated_by      uuid,
            created_at      timestamptz NOT NULL DEFAULT now(),
            updated_at      timestamptz NOT NULL DEFAULT now(),
            version         bigint NOT NULL DEFAULT 1,
            CONSTRAINT ck_unit_owner_status CHECK (status IN ('active', 'inactive'))
        )
    """)
    op.execute("CREATE INDEX ix_unit_owner_property ON finance.unit_owners (property_id, name)")

    op.execute("""
        CREATE TABLE finance.unit_owner_contracts (
            id                     uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            organization_id        uuid NOT NULL,
            property_id            uuid NOT NULL,
            owner_id               uuid NOT NULL REFERENCES finance.unit_owners (id),
            room_id                uuid NOT NULL,
            management_fee_percent numeric(5, 2) NOT NULL DEFAULT 0,
            start_date             date NOT NULL,
            end_date               date,
            notes                  varchar(500),
            created_by             uuid,
            updated_by             uuid,
            created_at             timestamptz NOT NULL DEFAULT now(),
            updated_at             timestamptz NOT NULL DEFAULT now(),
            version                bigint NOT NULL DEFAULT 1,
            CONSTRAINT ck_owner_contract_fee
                CHECK (management_fee_percent >= 0 AND management_fee_percent <= 100),
            CONSTRAINT ck_owner_contract_dates
                CHECK (end_date IS NULL OR end_date >= start_date)
        )
    """)
    op.execute("CREATE INDEX ix_owner_contract_room "
               "ON finance.unit_owner_contracts (property_id, room_id, start_date)")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS finance.unit_owner_contracts")
    op.execute("DROP TABLE IF EXISTS finance.unit_owners")
    op.execute("DROP TABLE IF EXISTS finance.expense_vouchers")
