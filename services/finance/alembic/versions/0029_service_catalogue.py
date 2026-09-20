"""What a guest can order during a stay, and what they ordered

Revision ID: 0029_service_catalogue
Revises: 0028_finance_rls
Create Date: 2026-09-16

Posting a charge to a folio already worked. What was missing was *what the
charge was for*: the dialog took a department and an amount, so a folio line
read "restaurant 340" and nothing anywhere recorded that it was two dosas and
a coffee. At check-out the guest asks what the 340 was and nobody can answer.

Three tables:

``service_items``
    The priced list the property sells -- food, drinks, tiffin, laundry, spa.
    ``category`` is what staff see and sort by; it also decides which
    department the charge posts under, and therefore which tax rules apply
    (see CATEGORY_SOURCE in service_routes). Price is what it sells for today.

``service_orders``
    One posting. Not a restaurant ticket with a life cycle -- there is no
    open/served/cancelled here, because this property takes the order and puts
    it on the bill. A mistake is reversed the way every other folio mistake
    is, with a folio adjustment, which already exists and already leaves an
    audit trail.

``service_order_lines``
    The lines, each pointing at the folio entry it created. ``item_name`` and
    ``unit_price`` are snapshots, not joins: re-pricing the menu in November
    must not rewrite what an October guest was charged, and deleting an item
    must not blank out a bill that has already been paid. ``item_id`` keeps
    the link for reporting and goes null if the item is ever deleted.

RLS follows 0028: every table carries its own organisation and is policed by
``tenancy.org_visible``. 0028 raises if a finance table has no policy, so this
migration adds them here rather than leaving three tables open to every
tenant.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision: str = "0029_service_catalogue"
down_revision: str | None = "0028_finance_rls"
branch_labels = None
depends_on = None

RUNTIME_ROLE = "pms_app"

#: Kept in step with CATEGORY_SOURCE in finance_service/service_routes.py.
#: A constraint rather than free text, because the category chooses the tax
#: treatment -- an unrecognised one would post a charge with no tax at all,
#: silently, and the property would find out at its next GST filing.
CATEGORIES = (
    "food", "drinks", "tiffin", "in_room_dining", "minibar",
    "laundry", "spa", "transport", "other",
)


def _secure(table: str) -> None:
    expression = "tenancy.org_visible(organization_id)"
    op.execute(f"ALTER TABLE finance.{table} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE finance.{table} FORCE ROW LEVEL SECURITY")
    op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON finance.{table}")
    op.execute(
        f"CREATE POLICY tenant_isolation ON finance.{table} "
        f"USING ({expression}) WITH CHECK ({expression})"
    )
    op.execute(f"""
        DO $$
        BEGIN
          IF EXISTS (SELECT FROM pg_roles WHERE rolname = '{RUNTIME_ROLE}') THEN
            GRANT SELECT, INSERT, UPDATE, DELETE
              ON finance.{table} TO {RUNTIME_ROLE};
          END IF;
        END
        $$
    """)


def upgrade() -> None:
    categories = ", ".join(f"'{c}'" for c in CATEGORIES)

    op.create_table(
        "service_items",
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("organization_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("property_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("code", sa.String(40), nullable=False),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("category", sa.String(30), nullable=False),
        sa.Column("price", sa.Numeric(19, 4), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False,
                  server_default="INR"),
        sa.Column("is_active", sa.Boolean, nullable=False,
                  server_default=sa.text("true")),
        sa.Column("sort_order", sa.Integer, nullable=False,
                  server_default=sa.text("0")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.CheckConstraint("price >= 0", name="ck_service_item_price"),
        sa.CheckConstraint(f"category IN ({categories})",
                           name="ck_service_item_category"),
        sa.UniqueConstraint("property_id", "code", name="uq_service_item_code"),
        schema="finance",
    )
    # Menu of one property, grouped, in the order the property chose. Every
    # listing this screen does is exactly this.
    op.create_index("ix_service_items_menu", "service_items",
                    ["property_id", "is_active", "category", "sort_order"],
                    schema="finance")

    op.create_table(
        "service_orders",
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("organization_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("property_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("folio_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("reservation_id", pg.UUID(as_uuid=True), nullable=True),
        sa.Column("business_date", sa.Date, nullable=False),
        sa.Column("note", sa.String(300), nullable=True),
        # Who took the order. Kept as an id with no foreign key on purpose:
        # IAM is another service and another database boundary, and a staff
        # member leaving must not take the history of their orders with them.
        sa.Column("posted_by", pg.UUID(as_uuid=True), nullable=True),
        sa.Column("posted_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("total", sa.Numeric(19, 4), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False,
                  server_default="INR"),
        sa.ForeignKeyConstraint(
            ["property_id", "folio_id"],
            ["finance.folios.property_id", "finance.folios.id"],
            name="fk_service_order_folio",
        ),
        schema="finance",
    )
    op.create_index("ix_service_orders_folio", "service_orders",
                    ["folio_id", "posted_at"], schema="finance")

    op.create_table(
        "service_order_lines",
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("organization_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("property_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("order_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("item_id", pg.UUID(as_uuid=True), nullable=True),
        # Snapshots. See the module docstring: the bill must keep saying what
        # it said when it was paid.
        sa.Column("item_name", sa.String(120), nullable=False),
        sa.Column("category", sa.String(30), nullable=False),
        sa.Column("quantity", sa.Numeric(10, 2), nullable=False),
        sa.Column("unit_price", sa.Numeric(19, 4), nullable=False),
        sa.Column("amount", sa.Numeric(19, 4), nullable=False),
        sa.Column("folio_entry_id", pg.UUID(as_uuid=True), nullable=True),
        sa.CheckConstraint("quantity > 0", name="ck_service_line_qty"),
        sa.CheckConstraint("amount >= 0", name="ck_service_line_amount"),
        sa.ForeignKeyConstraint(
            ["order_id"], ["finance.service_orders.id"],
            name="fk_service_line_order", ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["item_id"], ["finance.service_items.id"],
            name="fk_service_line_item", ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["folio_entry_id"], ["finance.folio_entries.id"],
            name="fk_service_line_entry",
        ),
        schema="finance",
    )
    op.create_index("ix_service_lines_order", "service_order_lines",
                    ["order_id"], schema="finance")

    for table in ("service_items", "service_orders", "service_order_lines"):
        _secure(table)


def downgrade() -> None:
    for table in ("service_order_lines", "service_orders", "service_items"):
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON finance.{table}")
        op.drop_table(table, schema="finance")
