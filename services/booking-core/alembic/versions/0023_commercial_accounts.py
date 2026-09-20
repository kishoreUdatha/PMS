"""Companies and travel agents — the customer that is not a person

Revision ID: 0023_commercial_accts
Revises: 0022_booking_capture
Create Date: 2026-09-10

``finance.folios.type`` has allowed ``company`` and ``paymaster`` since the
first migration, and ``folios.commercial_account_id`` has been sitting there
pointing at nothing, because the table it names was never built. So a booking
made by a company could be recorded against a guest and nothing else: no way
to bill the company, no way to see what it owes, no Bill To on the invoice.

This is that table. It is one table, not two, because a corporate client and a
travel agent differ in how they are settled, not in what they are — both are a
non-person customer with an address, a tax registration and terms. ``kind``
records which, so a report can separate them without the schema pretending
they are unrelated.

**Terms are recorded, not enforced.** ``credit_limit`` and ``credit_days``
describe the agreement. Nothing in this migration blocks a booking that would
breach them — refusing a booking is a policy decision, and inventing one here
would mean a front desk discovering a rule nobody agreed to. The figures are
stored so that a screen can warn, and so that ageing can be built on top.

**Commission belongs to the agent, not the booking.** ``commission_percent``
sits here because it is a property of the agreement; what any single booking
actually earned is a calculation over that booking's revenue, which the folio
already holds.

A reservation gains ``commercial_account_id``: who the stay is *for* is a
different question from who is sleeping in the room, and only the first one
decides where the bill goes.
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0023_commercial_accts"
down_revision: str | None = "0022_booking_capture"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

KINDS = ("company", "travel_agent", "ota", "government", "other")
BILL_TO = ("guest", "company")


def upgrade() -> None:
    op.create_table(
        "commercial_accounts",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        # Short code the desk types; unique per organisation so two properties
        # in one group cannot both own "BLUEWAVE".
        sa.Column("code", sa.String(30), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        # The name on the invoice, when it differs from the trading name.
        sa.Column("legal_name", sa.String(200), nullable=True),
        sa.Column("kind", sa.String(20), nullable=False, server_default="company"),

        sa.Column("gstin", sa.String(20), nullable=True),
        sa.Column("address_line", sa.String(300), nullable=True),
        sa.Column("city", sa.String(120), nullable=True),
        sa.Column("state", sa.String(120), nullable=True),
        sa.Column("state_code", sa.String(4), nullable=True),
        sa.Column("postal_code", sa.String(20), nullable=True),
        sa.Column("country", sa.String(120), nullable=False,
                  server_default="India"),

        sa.Column("contact_name", sa.String(200), nullable=True),
        sa.Column("contact_phone", sa.String(60), nullable=True),
        sa.Column("contact_email", sa.String(200), nullable=True),

        # The agreement. Recorded, not enforced — see the module docstring.
        sa.Column("credit_limit", sa.Numeric(14, 2), nullable=True),
        sa.Column("credit_days", sa.Integer, nullable=True),
        sa.Column("commission_percent", sa.Numeric(5, 2), nullable=True),
        sa.Column("payment_terms", sa.Text, nullable=True),

        sa.Column("status", sa.String(20), nullable=False,
                  server_default="active"),
        sa.Column("notes", sa.Text, nullable=True),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("updated_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("version", sa.BigInteger, nullable=False, server_default="1"),

        sa.CheckConstraint(
            "kind IN ({})".format(", ".join(f"'{k}'" for k in KINDS)),
            name="ck_account_kind"),
        sa.CheckConstraint("status IN ('active', 'inactive')",
                           name="ck_account_status"),
        sa.CheckConstraint("credit_limit IS NULL OR credit_limit >= 0",
                           name="ck_account_credit_limit"),
        sa.CheckConstraint("credit_days IS NULL OR credit_days >= 0",
                           name="ck_account_credit_days"),
        sa.CheckConstraint(
            "commission_percent IS NULL "
            "OR (commission_percent >= 0 AND commission_percent <= 100)",
            name="ck_account_commission"),
        sa.UniqueConstraint("organization_id", "code", name="uq_account_code"),
        schema="engagement",
    )
    op.create_index("ix_account_org_name", "commercial_accounts",
                    ["organization_id", "name"], schema="engagement")

    # Who the stay is *for*, as against who sleeps in the room.
    op.add_column(
        "reservations",
        sa.Column("commercial_account_id", postgresql.UUID(as_uuid=True),
                  nullable=True),
        schema="booking",
    )
    op.add_column(
        "reservations",
        sa.Column("bill_to", sa.String(20), nullable=False,
                  server_default="guest"),
        schema="booking",
    )
    op.create_check_constraint(
        "ck_reservation_bill_to", "reservations",
        "bill_to IN ({})".format(", ".join(f"'{b}'" for b in BILL_TO)),
        schema="booking",
    )
    # Billing a company with no company named is not a state worth allowing.
    op.create_check_constraint(
        "ck_reservation_bill_to_account", "reservations",
        "bill_to <> 'company' OR commercial_account_id IS NOT NULL",
        schema="booking",
    )
    op.create_foreign_key(
        "fk_reservation_commercial_account", "reservations",
        "commercial_accounts", ["commercial_account_id"], ["id"],
        source_schema="booking", referent_schema="engagement",
        ondelete="SET NULL",
    )

    # The column that has pointed at nothing since 0001 now points at this.
    op.create_foreign_key(
        "fk_folio_commercial_account", "folios",
        "commercial_accounts", ["commercial_account_id"], ["id"],
        source_schema="finance", referent_schema="engagement",
        ondelete="SET NULL",
    )
    op.create_index("ix_folio_commercial_account", "folios",
                    ["commercial_account_id"], schema="finance")


def downgrade() -> None:
    op.drop_index("ix_folio_commercial_account", "folios", schema="finance")
    op.drop_constraint("fk_folio_commercial_account", "folios",
                       schema="finance", type_="foreignkey")
    op.drop_constraint("fk_reservation_commercial_account", "reservations",
                       schema="booking", type_="foreignkey")
    op.drop_constraint("ck_reservation_bill_to_account", "reservations",
                       schema="booking")
    op.drop_constraint("ck_reservation_bill_to", "reservations", schema="booking")
    op.drop_column("reservations", "bill_to", schema="booking")
    op.drop_column("reservations", "commercial_account_id", schema="booking")
    op.drop_index("ix_account_org_name", "commercial_accounts", schema="engagement")
    op.drop_table("commercial_accounts", schema="engagement")
