"""A property's own strapline, so the folio header is not one resort's.

The folio header set a hard-coded tagline ("SEA - STAY - SERENITY") under the
wordmark. On a single-tenant install that reads as branding; on a multi-tenant
one it prints the first resort's strapline on every other resort's tax invoice.
It belongs next to the rest of the billing identity.

Revision ID: 0011_settings_tagline
Revises: 0010_draft_inv_no_number
"""
from alembic import op
import sqlalchemy as sa

revision = "0011_settings_tagline"
down_revision = "0010_draft_inv_no_number"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "invoice_settings",
        sa.Column("tagline", sa.String(length=120), nullable=True),
        schema="finance",
    )


def downgrade() -> None:
    op.drop_column("invoice_settings", "tagline", schema="finance")
