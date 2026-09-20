"""Let each property name its own service categories

Revision ID: 0031_tenant_categories
Revises: 0030_service_order_key
Create Date: 2026-09-16

0029 fixed the categories in code: food, drinks, tiffin, in-room dining,
minibar, laundry, spa, transport, other. That is one property's idea of what a
hotel sells, written into a check constraint. A backwater resort sells cruises,
a business hotel sells conference hire, a hill property sells trek guides and
a beach one sells jet-skis -- and every one of them would have had to file it
under "Other", which is the same as not recording it.

"Tiffin" is the tell. It is a real category in Andhra and meaningless in
Rajasthan, and it was in the constraint because the first property to use this
was in Chirala.

So a category is now a row a property owns, with a name it chooses. What
*cannot* be free text is the tax treatment: the ledger resolves tax from a
department (``tax_engine.SOURCE_CATEGORY``), and a category called "Sunset
Cruise" tells it nothing. So the category names itself and then says which
department it bills under -- ``bills_as`` -- from the set the tax engine
actually knows. The property's own vocabulary sits on top of the accounting
vocabulary instead of being forced into it.

That separation is what the tax engine already asked for in its own comment:
"A property whose departments do not line up with these should map its own
charge codes instead of relying on the source type."

No defaults are seeded. A property with no categories is asked to name its
first one, because guessing the list is how "Tiffin" ended up in a constraint.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision: str = "0031_tenant_categories"
down_revision: str | None = "0030_service_order_key"
branch_labels = None
depends_on = None

RUNTIME_ROLE = "pms_app"

#: The departments the ledger can price tax for. Not a product decision -- this
#: list is ``tax_engine.SOURCE_CATEGORY``'s keys, and widening it here without
#: widening that would post a charge with no tax and tell nobody.
BILLS_AS = (
    "restaurant", "in_room_dining", "minibar",
    "spa", "laundry", "transport", "other",
)

#: How 0029's fixed categories map onto a property's own rows, so existing
#: menus keep working and keep billing to the same department.
LEGACY = {
    "food": ("Food", "restaurant"),
    "drinks": ("Drinks", "restaurant"),
    "tiffin": ("Tiffin", "restaurant"),
    "in_room_dining": ("In-room Dining", "in_room_dining"),
    "minibar": ("Minibar", "minibar"),
    "laundry": ("Laundry", "laundry"),
    "spa": ("Spa", "spa"),
    "transport": ("Transport", "transport"),
    "other": ("Other", "other"),
}


def upgrade() -> None:
    bills = ", ".join(f"'{b}'" for b in BILLS_AS)

    op.create_table(
        "service_categories",
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("organization_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("property_id", pg.UUID(as_uuid=True), nullable=False),
        # The property's own word for it. No constraint on the value: that is
        # the entire point of this migration.
        sa.Column("name", sa.String(60), nullable=False),
        # The department it bills under, which decides the tax.
        sa.Column("bills_as", sa.String(30), nullable=False),
        sa.Column("sort_order", sa.Integer, nullable=False,
                  server_default=sa.text("0")),
        sa.Column("is_active", sa.Boolean, nullable=False,
                  server_default=sa.text("true")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.CheckConstraint(f"bills_as IN ({bills})",
                           name="ck_service_category_bills_as"),
        schema="finance",
    )
    # Case-insensitive, so "Drinks" and "drinks" cannot both exist and leave
    # staff guessing which one an item is filed under.
    op.execute(
        "CREATE UNIQUE INDEX uq_service_category_name "
        "ON finance.service_categories (property_id, lower(name))"
    )

    op.add_column(
        "service_items",
        sa.Column("category_id", pg.UUID(as_uuid=True), nullable=True),
        schema="finance",
    )

    # Carry existing menus across: one category row per (property, old value)
    # actually in use, then point the items at it. Nothing is invented for a
    # property that never used a category.
    for legacy, (name, bills_as) in LEGACY.items():
        op.execute(
            f"""
            INSERT INTO finance.service_categories
                (organization_id, property_id, name, bills_as)
            SELECT DISTINCT organization_id, property_id,
                   '{name}', '{bills_as}'
              FROM finance.service_items
             WHERE category = '{legacy}'
            """
        )
        op.execute(
            f"""
            UPDATE finance.service_items i
               SET category_id = c.id
              FROM finance.service_categories c
             WHERE c.property_id = i.property_id
               AND lower(c.name) = lower('{name}')
               AND i.category = '{legacy}'
            """
        )

    # Every item must now belong to a category the property owns.
    op.execute(
        "ALTER TABLE finance.service_items "
        "ALTER COLUMN category_id SET NOT NULL"
    )
    op.create_foreign_key(
        "fk_service_item_category", "service_items", "service_categories",
        ["category_id"], ["id"],
        source_schema="finance", referent_schema="finance",
        # RESTRICT, not CASCADE: deleting a category must not silently take
        # the priced items with it.
        ondelete="RESTRICT",
    )
    op.drop_constraint("ck_service_item_category", "service_items",
                       schema="finance", type_="check")
    op.drop_index("ix_service_items_menu", "service_items", schema="finance")
    op.drop_column("service_items", "category", schema="finance")
    op.create_index("ix_service_items_menu", "service_items",
                    ["property_id", "is_active", "category_id", "sort_order"],
                    schema="finance")

    # service_order_lines.category stays a plain string on purpose: it is the
    # snapshot of what the category was called when the guest was billed, and
    # renaming a category must not rewrite a paid bill.

    op.execute("ALTER TABLE finance.service_categories "
               "ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE finance.service_categories "
               "FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY tenant_isolation ON finance.service_categories "
        "USING (tenancy.org_visible(organization_id)) "
        "WITH CHECK (tenancy.org_visible(organization_id))"
    )
    op.execute(f"""
        DO $$
        BEGIN
          IF EXISTS (SELECT FROM pg_roles WHERE rolname = '{RUNTIME_ROLE}') THEN
            GRANT SELECT, INSERT, UPDATE, DELETE
              ON finance.service_categories TO {RUNTIME_ROLE};
          END IF;
        END
        $$
    """)


def downgrade() -> None:
    categories = ", ".join(f"'{c}'" for c in LEGACY)
    op.add_column(
        "service_items",
        sa.Column("category", sa.String(30), nullable=True),
        schema="finance",
    )
    # Only the nine 0029 knew about survive; anything the property named for
    # itself has no home in the old shape and becomes 'other'.
    for legacy, (name, _) in LEGACY.items():
        op.execute(
            f"""
            UPDATE finance.service_items i SET category = '{legacy}'
              FROM finance.service_categories c
             WHERE c.id = i.category_id AND lower(c.name) = lower('{name}')
            """
        )
    op.execute("UPDATE finance.service_items SET category = 'other' "
               "WHERE category IS NULL")
    op.execute("ALTER TABLE finance.service_items "
               "ALTER COLUMN category SET NOT NULL")
    op.create_check_constraint(
        "ck_service_item_category", "service_items",
        f"category IN ({categories})", schema="finance",
    )
    op.drop_constraint("fk_service_item_category", "service_items",
                       schema="finance", type_="foreignkey")
    op.drop_index("ix_service_items_menu", "service_items", schema="finance")
    op.drop_column("service_items", "category_id", schema="finance")
    op.create_index("ix_service_items_menu", "service_items",
                    ["property_id", "is_active", "category", "sort_order"],
                    schema="finance")
    op.execute("DROP POLICY IF EXISTS tenant_isolation "
               "ON finance.service_categories")
    op.drop_table("service_categories", schema="finance")
