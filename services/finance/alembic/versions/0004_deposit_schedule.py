"""Deposit schedule (screen 058)

Revision ID: 0004_deposit_schedule
Revises: 0003_tax_default_group
Create Date: 2026-09-09

A deposit schedule is the plan for *when* a booking gets paid — 20% at booking,
10% a few days out, the balance on arrival. The money itself already has a home:
``post_payment`` captures a payment and posts a credit to the folio. This adds
only the plan in front of it, and the link that says which installment a payment
settled.

So ``paid_amount`` is deliberately **not** a column. It is the sum of the
allocations recorded against the installment, which means the schedule can never
claim someone paid something the ledger has no record of. A stored total would
be a second source of truth for money, and money is the last place to keep two.

``due_rule`` carries the part of a due date that is not a date: "at time of
booking" and "on arrival" move with the reservation, while a fixed instalment
does not. The mockup shows all three in the same column.

Waivers are recorded with the amount, the reason and who asked, because a waiver
is a decision someone has to answer for later. Approval routing does not exist
yet (screen 042 has a queue but no way to raise a request), so the request is
stored and surfaced rather than pretended to be sent.
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0004_deposit_schedule"
down_revision: str | None = "0003_tax_default_group"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

DUE_RULES = ("at_booking", "fixed_date", "at_checkin")
STATUSES = ("pending", "partially_paid", "paid", "waived", "cancelled")
WAIVER_STATUSES = ("none", "requested", "approved", "rejected")
REMINDER_STATES = ("none", "scheduled", "sent")


def upgrade() -> None:
    quoted = lambda values: ", ".join(f"'{v}'" for v in values)  # noqa: E731

    op.execute(
        f"""
        CREATE TABLE finance.deposit_installments (
            id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            organization_id  uuid NOT NULL,
            property_id      uuid NOT NULL,
            reservation_id   uuid NOT NULL,
            folio_id         uuid,
            seq              integer NOT NULL,
            label            varchar(120) NOT NULL,
            amount           numeric(19, 4) NOT NULL,
            -- The "(20%)" on the label, kept so the schedule can be rebuilt
            -- if the booking value changes.
            percent          numeric(6, 3),
            due_rule         varchar(20) NOT NULL DEFAULT 'fixed_date',
            due_date         date,
            status           varchar(20) NOT NULL DEFAULT 'pending',
            waived_amount    numeric(19, 4) NOT NULL DEFAULT 0,
            waiver_status    varchar(20) NOT NULL DEFAULT 'none',
            waiver_reason    varchar(300),
            waiver_requested_by uuid,
            waiver_requested_at timestamptz,
            reminder_state   varchar(20) NOT NULL DEFAULT 'none',
            reminder_sent_at timestamptz,
            reminder_due_on  date,
            notes            varchar(300),
            created_by       uuid,
            updated_by       uuid,
            created_at       timestamptz NOT NULL DEFAULT now(),
            updated_at       timestamptz NOT NULL DEFAULT now(),
            version          bigint NOT NULL DEFAULT 0,
            CONSTRAINT uq_deposit_seq UNIQUE (reservation_id, seq),
            CONSTRAINT uq_deposit_prop_id UNIQUE (property_id, id),
            CONSTRAINT ck_deposit_amount CHECK (amount > 0),
            CONSTRAINT ck_deposit_percent
                CHECK (percent IS NULL OR (percent >= 0 AND percent <= 100)),
            CONSTRAINT ck_deposit_due_rule CHECK (due_rule IN ({quoted(DUE_RULES)})),
            CONSTRAINT ck_deposit_status CHECK (status IN ({quoted(STATUSES)})),
            CONSTRAINT ck_deposit_waiver
                CHECK (waiver_status IN ({quoted(WAIVER_STATUSES)})),
            CONSTRAINT ck_deposit_reminder
                CHECK (reminder_state IN ({quoted(REMINDER_STATES)})),
            CONSTRAINT ck_deposit_waived
                CHECK (waived_amount >= 0 AND waived_amount <= amount),
            -- A fixed instalment needs its date; the other two take theirs from
            -- the reservation.
            CONSTRAINT ck_deposit_due_date
                CHECK (due_rule <> 'fixed_date' OR due_date IS NOT NULL)
        )
        """
    )
    op.execute(
        "CREATE INDEX ix_deposit_reservation "
        "ON finance.deposit_installments (reservation_id, seq)"
    )
    op.execute(
        "CREATE INDEX ix_deposit_due "
        "ON finance.deposit_installments (property_id, status, due_date)"
    )

    # What was actually collected against an instalment. Each row points at the
    # payment that carried the money, so the schedule and the ledger agree by
    # construction rather than by reconciliation.
    op.execute(
        """
        CREATE TABLE finance.deposit_allocations (
            id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            organization_id uuid NOT NULL,
            property_id     uuid NOT NULL,
            installment_id  uuid NOT NULL,
            payment_id      uuid,
            folio_entry_id  uuid,
            amount          numeric(19, 4) NOT NULL,
            method          varchar(30) NOT NULL,
            reference       varchar(120),
            received_on     date NOT NULL,
            -- A refund or reversal is stored as the negative of what it undoes,
            -- so the outstanding balance is one SUM over this table.
            reversal_of_id  uuid,
            reason          varchar(300),
            recorded_by     uuid,
            recorded_at     timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT ck_dep_alloc_amount CHECK (amount <> 0),
            CONSTRAINT fk_dep_alloc_installment
                FOREIGN KEY (property_id, installment_id)
                REFERENCES finance.deposit_installments (property_id, id)
                ON DELETE CASCADE,
            CONSTRAINT fk_dep_alloc_payment
                FOREIGN KEY (property_id, payment_id)
                REFERENCES finance.payments (property_id, id),
            CONSTRAINT fk_dep_alloc_reversal
                FOREIGN KEY (reversal_of_id)
                REFERENCES finance.deposit_allocations (id)
        )
        """
    )
    op.execute(
        "CREATE INDEX ix_dep_alloc_installment "
        "ON finance.deposit_allocations (installment_id, received_on)"
    )
    # A payment line can only be reversed once.
    op.execute(
        "CREATE UNIQUE INDEX uq_dep_alloc_reversal "
        "ON finance.deposit_allocations (reversal_of_id) "
        "WHERE reversal_of_id IS NOT NULL"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS finance.deposit_allocations")
    op.execute("DROP TABLE IF EXISTS finance.deposit_installments")
