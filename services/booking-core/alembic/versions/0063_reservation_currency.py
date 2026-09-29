"""Reservations nobody has billed yet take their property's currency

Revision ID: 0063_reservation_currency
Revises: 0062_ota_hotel_unique
Create Date: 2026-09-26

``create_hold`` wrote the literal 'INR' into every reservation, whatever the
property's currency. Folios open in the reservation's currency and postings
take the folio's, so the mistake spreads from here into the ledger.

Only rows that are still just a promise are corrected: a reservation with no
posted folio entry, and its folios that have none either. Anything already
billed is left exactly as it is -- relabelling posted money from one currency
to another is not a correction, it is a different amount, and deciding what
that money really was needs a person with the invoices in front of them. The
count of what was left alone is printed so somebody knows to look.

Nothing to undo on downgrade: the old value was wrong, not a choice.
"""
from __future__ import annotations

from alembic import op
from sqlalchemy import text

revision: str = "0063_reservation_currency"
down_revision: str | None = "0062_ota_hotel_unique"
branch_labels = None
depends_on = None


_UNBILLED = """
    NOT EXISTS (
        SELECT 1 FROM finance.folios f
          JOIN finance.folio_entries e ON e.folio_id = f.id
         WHERE f.reservation_id = r.id)
"""


def upgrade() -> None:
    bind = op.get_bind()
    # Every tenant's rows, on purpose: this is a correction to data written by
    # a bug, not a tenant's request. Transaction-local, so it ends with the
    # migration.
    bind.execute(text("SELECT set_config('app.system', 'on', true)"))

    bind.execute(text(
        f"""
        UPDATE finance.folios f
           SET currency = p.currency, version = f.version + 1
          FROM booking.reservations r
          JOIN iam.properties p ON p.id = r.property_id
         WHERE f.reservation_id = r.id
           AND r.currency IS DISTINCT FROM p.currency
           AND {_UNBILLED}
        """
    ))
    bind.execute(text(
        f"""
        UPDATE booking.reservations r
           SET currency = p.currency, version = r.version + 1
          FROM iam.properties p
         WHERE p.id = r.property_id
           AND r.currency IS DISTINCT FROM p.currency
           AND {_UNBILLED}
        """
    ))
    left = bind.execute(text(
        """
        SELECT count(*) FROM booking.reservations r
          JOIN iam.properties p ON p.id = r.property_id
         WHERE r.currency IS DISTINCT FROM p.currency
        """
    )).scalar_one()
    if left:
        print(
            f"0063_reservation_currency: {left} reservation(s) already have "
            f"postings in a currency that is not their property's. They were "
            f"left alone; review them by hand."
        )


def downgrade() -> None:
    pass
