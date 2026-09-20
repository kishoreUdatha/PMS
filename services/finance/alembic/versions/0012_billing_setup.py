"""Whether rates include tax, and which payments the desk takes.

Two settings onboarding step 6 asks for that had nowhere to live.

``tax_inclusive`` decides whether a quoted rate already contains the tax or has
it added, which changes what every folio line means. It defaults to true
because that is how Indian tariffs are almost always quoted, and a wrong
default here is visible on the first invoice rather than hidden.

``payment_methods`` is the list a cashier may choose from. Stored as an array
rather than a column per method so adding one is data, not a migration.

Revision ID: 0012_billing_setup
Revises: 0011_settings_tagline
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0012_billing_setup"
down_revision = "0011_settings_tagline"
branch_labels = None
depends_on = None

DEFAULT_METHODS = ("cash", "upi", "card", "bank_transfer")


def upgrade() -> None:
    op.add_column(
        "invoice_settings",
        sa.Column("tax_inclusive", sa.Boolean, nullable=False,
                  server_default=sa.text("true")),
        schema="finance",
    )
    op.add_column(
        "invoice_settings",
        sa.Column("payment_methods", postgresql.ARRAY(sa.String(length=32)),
                  nullable=False,
                  server_default=sa.text(
                      "ARRAY[" + ", ".join(f"'{m}'" for m in DEFAULT_METHODS)
                      + "]::varchar[]")),
        schema="finance",
    )


def downgrade() -> None:
    op.drop_column("invoice_settings", "payment_methods", schema="finance")
    op.drop_column("invoice_settings", "tax_inclusive", schema="finance")
