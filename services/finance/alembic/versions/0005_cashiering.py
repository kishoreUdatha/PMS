"""Cashier shifts, and the facts a payment was missing (screen 036)

Revision ID: 0005_cashiering
Revises: 0004_deposit_schedule
Create Date: 2026-09-09

``finance.payments`` has carried a ``cashier_shift_id`` column since the first
migration, pointing at a table that was never created. Every payment taken so
far has left it null. Screen 036 is what that column was for.

**The shift.** Cash is the one payment method where the ledger and the drawer
can disagree, and the only way to find out is to count it. A shift records the
float it opened with, what the system says should be in the drawer, what the
cashier actually counted, and the difference between those two numbers. The
variance is stored rather than derived because it is a finding — the figure
someone signed off on at 6pm, not a number that quietly changes if a late
payment is backdated into the shift.

**Three facts about a payment that had nowhere to live.**

``cashier_id`` — who took the money. ``provider_transaction_id`` already
records what the payment provider called it, but that is the machine's name for
the transaction, not a person's accountability for it.

``reference`` — the UPI or card reference the guest quotes back when something
goes wrong. The stub provider generates a uuid; the number printed on the
guest's phone is a different thing entirely, and it is the one that matters when
a payment has to be traced.

``source`` — where the money was taken. Today that is the cashiering desk, a
deposit collection or a checkout settlement, all of which exist. ``pos`` is in
the list and will stay unused until SCR-013 is built; naming it now is cheaper
than migrating the check constraint later, and an empty value is honest in a way
an invented outlet would not be.
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0005_cashiering"
down_revision: str | None = "0004_deposit_schedule"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SHIFT_STATUSES = ("open", "closed")
SOURCES = ("front_desk", "deposit", "checkout", "pos", "night_audit")


def upgrade() -> None:
    q = lambda v: ", ".join(f"'{x}'" for x in v)  # noqa: E731

    op.execute(
        f"""
        CREATE TABLE finance.cashier_shifts (
            id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            organization_id   uuid NOT NULL,
            property_id       uuid NOT NULL,
            cashier_id        uuid NOT NULL,
            business_date     date NOT NULL,
            status            varchar(10) NOT NULL DEFAULT 'open',
            opened_at         timestamptz NOT NULL DEFAULT now(),
            closed_at         timestamptz,
            -- The cash put in the drawer at the start.
            opening_float     numeric(19, 4) NOT NULL DEFAULT 0,
            -- What the cashier counted at the end, and what the ledger says
            -- should have been there. Both are stored: the variance is a
            -- finding somebody signed off on, not a number that moves later.
            declared_cash     numeric(19, 4),
            expected_cash     numeric(19, 4),
            variance          numeric(19, 4),
            notes             varchar(600),
            opened_by         uuid,
            closed_by         uuid,
            created_at        timestamptz NOT NULL DEFAULT now(),
            updated_at        timestamptz NOT NULL DEFAULT now(),
            version           bigint NOT NULL DEFAULT 0,
            CONSTRAINT ck_shift_status CHECK (status IN ({q(SHIFT_STATUSES)})),
            CONSTRAINT ck_shift_float CHECK (opening_float >= 0)
        )
        """
    )
    # One open shift per cashier. A second drawer for the same person is not a
    # busy day, it is a mistake, and it makes the cash count meaningless.
    op.execute(
        "CREATE UNIQUE INDEX uq_shift_open_cashier "
        "ON finance.cashier_shifts (cashier_id) WHERE status = 'open'"
    )
    op.execute(
        "CREATE INDEX ix_shift_property_date "
        "ON finance.cashier_shifts (property_id, business_date DESC)"
    )

    op.execute("ALTER TABLE finance.payments ADD COLUMN cashier_id uuid")
    op.execute("ALTER TABLE finance.payments ADD COLUMN reference varchar(80)")
    op.execute("ALTER TABLE finance.payments ADD COLUMN notes varchar(300)")
    op.execute(
        f"""
        ALTER TABLE finance.payments
            ADD COLUMN source varchar(20) NOT NULL DEFAULT 'front_desk',
            ADD CONSTRAINT ck_payment_source CHECK (source IN ({q(SOURCES)}))
        """
    )
    op.execute(
        """
        ALTER TABLE finance.payments
            ADD CONSTRAINT fk_payment_shift
            FOREIGN KEY (cashier_shift_id) REFERENCES finance.cashier_shifts (id)
        """
    )
    op.execute(
        "CREATE INDEX ix_payments_property_received "
        "ON finance.payments (property_id, received_at DESC)"
    )

    # Method has never had a fixed case. Early payments went in as 'UPI' and
    # the screen groups by method, so two spellings would show as two payment
    # methods that split the day's takings between them.
    op.execute("UPDATE finance.payments SET method = lower(method)")

    # Payments already taken through the deposit screen came from a deposit
    # collection; say so rather than letting the column default lie about them.
    op.execute(
        """
        UPDATE finance.payments p
           SET source = 'deposit'
         WHERE EXISTS (
             SELECT 1
             FROM finance.deposit_allocations da
             WHERE da.payment_id = p.id
         )
        """
    )


def downgrade() -> None:
    op.execute("ALTER TABLE finance.payments DROP CONSTRAINT IF EXISTS fk_payment_shift")
    op.execute("DROP INDEX IF EXISTS finance.ix_payments_property_received")
    op.execute(
        "ALTER TABLE finance.payments "
        "DROP COLUMN IF EXISTS cashier_id, "
        "DROP COLUMN IF EXISTS reference, "
        "DROP COLUMN IF EXISTS notes, "
        "DROP COLUMN IF EXISTS source"
    )
    op.execute("DROP TABLE IF EXISTS finance.cashier_shifts")
