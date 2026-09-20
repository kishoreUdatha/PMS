"""The check-out record (screen 007)

Revision ID: 0016_checkout
Revises: 0015_checkin
Create Date: 2026-09-09

The mirror of ``stay_checkins``. ``flow.check_out`` already closes the stay,
releases the future nights and frees the room; what it does not record is the
*handover* — whether the key came back, whether housekeeping was told, what the
folio stood at when the guest walked out.

That last one matters most. A folio keeps changing after a departure (a late
minibar posting, a corrected charge), so "what did this guest owe when they
left" is not answerable from the folio later. It is answerable only if written
down at the time, which is what ``settled_amount`` and ``balance_at_checkout``
are for.

``balance_at_checkout`` is deliberately allowed to be non-zero. Guests do leave
with a balance — a company is billed, a dispute is open — and a system that
refuses to record that just gets lied to instead.
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0016_checkout"
down_revision: str | None = "0015_checkin"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE booking.stay_checkouts (
            id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            organization_id     uuid NOT NULL,
            property_id         uuid NOT NULL,
            reservation_unit_id uuid NOT NULL,
            stay_id             uuid,
            room_id             uuid,
            -- What the folio said at the moment of departure, not now.
            settled_amount      numeric(19, 4) NOT NULL DEFAULT 0,
            balance_at_checkout numeric(19, 4) NOT NULL DEFAULT 0,
            payment_id          uuid,
            key_returned        boolean NOT NULL DEFAULT false,
            housekeeping_notified boolean NOT NULL DEFAULT false,
            feedback_scheduled  boolean NOT NULL DEFAULT false,
            deposit_refunded    numeric(19, 4) NOT NULL DEFAULT 0,
            notes               varchar(500),
            checked_out_by      uuid,
            checked_out_at      timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT uq_stay_checkout_unit UNIQUE (reservation_unit_id),
            CONSTRAINT ck_stay_checkout_settled CHECK (settled_amount >= 0),
            CONSTRAINT ck_stay_checkout_refund CHECK (deposit_refunded >= 0),
            CONSTRAINT fk_stay_checkout_unit
                FOREIGN KEY (property_id, reservation_unit_id)
                REFERENCES booking.reservation_units (property_id, id)
        )
        """
    )
    op.execute(
        "CREATE INDEX ix_stay_checkouts_property "
        "ON booking.stay_checkouts (property_id, checked_out_at DESC)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS booking.stay_checkouts")
