"""Let a folio say who it bills and how it prints

Revision ID: 0035_folio_billing_details
Revises: 0034_charge_detail
Create Date: 2026-09-17

A booking can carry several folios -- the company paying the room, the guest
paying the bar, a sharer settling their own extras -- and until now the only
thing distinguishing one from another was its type and its number. Everything
that decides what the printed bill actually *says* lived nowhere.

Four columns, each of which a desk fills in when it opens the second folio:

``sharer_name``
    Who this folio bills. There is no sharer table in this system -- only the
    primary guest is linked to a booking -- so this is the name as written on
    the bill rather than a foreign key pretending to be more than it is. A
    folio opened for the occupant of the second bed says so here.

``gstin``
    The *recipient's* GST number, which is the one thing that makes an Indian
    tax invoice a B2B document rather than a retail receipt. The property's own
    GSTIN is in ``finance.invoice_settings`` and a company's is on
    ``engagement.commercial_accounts``; neither is this. A guest whose employer
    reclaims the GST needs their employer's number on the bill, and there was
    nowhere to put it.

    Fifteen characters, because a GSTIN is fifteen characters. The shape and
    the check digit are enforced in ``chirala_common.gstin`` rather than here,
    so the message the desk reads is the same one the API refuses with.

``show_tax_on_folio``
    Whether the printed folio itemises tax or shows tax-inclusive totals. Both
    are asked for and neither is universally right: a company folio usually
    wants the tax broken out to be reclaimed, a walk-in usually wants one
    number. Defaults true, which is what the printer does today, so no existing
    folio changes behaviour.

``invoice_number_timing``
    When the invoice number is assigned. Numbering at checkout is what most
    properties want and is the default. Numbering afterwards matters where the
    bill is still moving -- a company folio awaiting a PO number, a group being
    reconciled -- because a fiscal series with gaps in it, or numbers issued
    against bills that then changed, is the kind of thing that gets asked about
    in an audit.

All four are nullable or defaulted, so every folio that exists keeps exactly
the behaviour it has.
"""
from alembic import op
import sqlalchemy as sa

revision = "0035_folio_billing_details"
down_revision = "0034_charge_detail"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "folios",
        sa.Column("sharer_name", sa.String(length=120), nullable=True),
        schema="finance",
    )
    op.add_column(
        "folios",
        sa.Column("gstin", sa.String(length=15), nullable=True),
        schema="finance",
    )
    op.add_column(
        "folios",
        sa.Column("show_tax_on_folio", sa.Boolean(), nullable=False,
                  server_default=sa.true()),
        schema="finance",
    )
    op.add_column(
        "folios",
        sa.Column("invoice_number_timing", sa.String(length=20),
                  nullable=False, server_default="on_checkout"),
        schema="finance",
    )
    # Spelled out rather than left to the application: these two values decide
    # when a number comes out of a fiscal series, and a third spelling arriving
    # from anywhere would be a silent behaviour change in numbering.
    op.create_check_constraint(
        "ck_folio_invoice_timing",
        "folios",
        "invoice_number_timing IN ('on_checkout', 'post_checkout')",
        schema="finance",
    )


def downgrade() -> None:
    op.drop_constraint("ck_folio_invoice_timing", "folios", schema="finance",
                       type_="check")
    op.drop_column("folios", "invoice_number_timing", schema="finance")
    op.drop_column("folios", "show_tax_on_folio", schema="finance")
    op.drop_column("folios", "gstin", schema="finance")
    op.drop_column("folios", "sharer_name", schema="finance")
