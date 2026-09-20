"""Vehicle and purpose of visit, for the registration card

Revision ID: 0052_registration_details
Revises: 0051_occupancy_yield
Create Date: 2026-09-16

The guest registration card is the form a guest checks and signs at the desk,
and the one a police or excise inspection asks to see. Almost everything on it
already exists: ``engagement.guests`` holds the name, address, nationality,
date of birth, occupation and the ID type and number, and check-in already
captures a signature and the policies being accepted.

Two things it asks for had nowhere to live. Both belong to the *stay* rather
than to the person -- the same guest arrives by car in March and by train in
July, on business once and on holiday the next time -- so they sit on the
reservation unit, not on the guest record. Putting them on the guest would
have meant a second visit quietly overwriting what the first one said.

Nullable, and they stay nullable. A property that does not ask for a vehicle
number should not be made to invent one, and a card prints the line as blank
for the guest to complete by hand -- which is how a paper form has always
handled the field nobody filled in beforehand.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0052_registration_details"
down_revision: str | None = "0051_occupancy_yield"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "reservation_units",
        sa.Column("vehicle_number", sa.String(30), nullable=True),
        schema="booking",
    )
    op.add_column(
        "reservation_units",
        sa.Column("purpose_of_visit", sa.String(60), nullable=True),
        schema="booking",
    )


def downgrade() -> None:
    op.drop_column("reservation_units", "purpose_of_visit", schema="booking")
    op.drop_column("reservation_units", "vehicle_number", schema="booking")
