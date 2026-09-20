"""Each tenant's own payment gateway, so their guests' money reaches them

Revision ID: 0022_payment_credentials
Revises: 0021_intent_order
Create Date: 2026-09-12

Until now there was one set of Razorpay keys for the whole deployment, read from
the environment. That is fine for one hotel and wrong for a platform: every
tenant's guest payments would settle into whichever merchant account owned those
keys. A booking engine that collects other people's money into your account is
not a feature, it is a liability.

Keyed by **organization**, not property. A gateway account belongs to a business
with a bank account, which is what a tenant is here; a group running three
resorts on one merchant account should enter its keys once, not three times and
rotate them three times. (Per-property accounts, if a group ever needs them, are
a narrowing of this rather than a rework.)

``webhook_ref`` is why this works at all. A webhook arrives carrying a signature
and nothing else — to check the signature we must already know whose secret to
check it against, and we cannot learn that from a body we have not yet
authenticated. So each tenant gets its own callback URL,
``/webhooks/razorpay/<ref>``, which they paste into their own Razorpay dashboard.
The path identifies the tenant before a single byte of the body is parsed. It is
random rather than the organisation id: internal ids should not become public
URLs, and a ref can be rotated if one leaks into a support ticket.

Secrets are sealed with Fernet (``chirala_common.secretbox``) before they get
here, so this table holds ciphertext the database cannot read. ``key_id`` is not
sealed -- it is the publishable half, the one that ships to the browser.

The second CHECK is the one that matters operationally: Razorpay cannot be
*enabled* without all three values present. A tenant half-way through entering
credentials must not be live, because the failure mode is a guest paying into
nothing.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0022_payment_credentials"
down_revision = "0021_intent_order"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "payment_credentials",
        sa.Column("organization_id", sa.Uuid(), primary_key=True),
        # 'mock' means no money moves and the caller can tell: orders come back
        # with obviously fake ids. It is the default so a new tenant is never
        # accidentally live.
        sa.Column("provider", sa.String(length=30), nullable=False,
                  server_default="mock"),
        sa.Column("webhook_ref", sa.String(length=40), nullable=False),
        sa.Column("key_id", sa.String(length=160), nullable=True),
        sa.Column("key_secret_sealed", sa.Text(), nullable=True),
        sa.Column("webhook_secret_sealed", sa.Text(), nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=False,
                  server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("updated_by", sa.Uuid(), nullable=True),
        sa.CheckConstraint("provider IN ('mock', 'razorpay')",
                           name="ck_payment_credentials_provider"),
        sa.CheckConstraint(
            "NOT enabled OR provider = 'mock' OR ("
            " key_id IS NOT NULL AND key_secret_sealed IS NOT NULL"
            " AND webhook_secret_sealed IS NOT NULL)",
            name="ck_payment_credentials_complete",
        ),
        schema="finance",
    )
    # Unique: the ref is how a callback is attributed to a tenant, and two
    # tenants sharing one would mean one tenant's payment confirming another's
    # booking.
    op.create_index("uq_payment_credentials_webhook_ref",
                    "payment_credentials", ["webhook_ref"], unique=True,
                    schema="finance")


def downgrade() -> None:
    op.drop_index("uq_payment_credentials_webhook_ref",
                  table_name="payment_credentials", schema="finance")
    op.drop_table("payment_credentials", schema="finance")
