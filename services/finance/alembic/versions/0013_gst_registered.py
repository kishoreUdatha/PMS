"""Whether the property is GST registered, asked rather than inferred.

Onboarding decided registration by looking at whether a GSTIN had been typed
in. That conflates two different states: "not registered" and "registered but
has not filled it in yet". A property genuinely outside GST could never finish
the billing step, because the rule demanded a number it does not have; and a
registered one that skipped the field looked settled.

So the answer is stored. ``NULL`` means nobody has said yet, which is not the
same as "no" -- the step stays incomplete and asks. Existing rows that already
carry a GSTIN are plainly registered and are backfilled to ``true``; rows
without one are left NULL to be answered rather than guessed at.

Revision ID: 0013_gst_registered
Revises: 0012_billing_setup
"""
import sqlalchemy as sa
from alembic import op

revision = "0013_gst_registered"
down_revision = "0012_billing_setup"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "invoice_settings",
        sa.Column("gst_registered", sa.Boolean(), nullable=True),
        schema="finance",
    )
    op.execute(
        """
        UPDATE finance.invoice_settings
        SET gst_registered = true
        WHERE gstin IS NOT NULL AND btrim(gstin) <> ''
        """
    )


def downgrade() -> None:
    op.drop_column("invoice_settings", "gst_registered", schema="finance")
