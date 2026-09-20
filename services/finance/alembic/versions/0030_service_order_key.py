"""Make posting an order idempotent from the client's side

Revision ID: 0030_service_order_key
Revises: 0029_service_catalogue
Create Date: 2026-09-16

0029 claimed a double-tapped Post could not bill twice, because each folio
entry is keyed ``service:<order>:<line>`` and ``folio_entries`` is unique on
``(folio_id, source_type, source_line_key)``. Testing it showed the claim was
false: the second tap creates a *new* order row, so the key is different and
the ledger has no reason to refuse it. A guest ordering one coffee was billed
for two -- 6,530 became 7,060.

The ledger key protects against replaying one order. It cannot protect against
two orders that happen to be the same submission, because only the client
knows they are the same submission. So the client says so: it generates a key
once per Post and sends it with every retry of that Post. A second arrival
with a key already seen returns the order that was already created instead of
posting another.

Scoped to the property, not the folio: the same key must never be reusable
anywhere, or a retry aimed at the wrong folio would silently succeed.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0030_service_order_key"
down_revision: str | None = "0029_service_catalogue"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "service_orders",
        sa.Column("client_key", sa.String(64), nullable=True),
        schema="finance",
    )
    # Partial: orders posted before this existed have no key, and several
    # nulls must not collide with each other.
    op.create_index(
        "uq_service_order_client_key", "service_orders",
        ["property_id", "client_key"], unique=True,
        postgresql_where=sa.text("client_key IS NOT NULL"),
        schema="finance",
    )


def downgrade() -> None:
    op.drop_index("uq_service_order_client_key", "service_orders",
                  schema="finance")
    op.drop_column("service_orders", "client_key", schema="finance")
