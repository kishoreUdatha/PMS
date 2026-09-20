"""Per-property night audit settings.

The audit hour was one value for the whole installation, so every tenant in a
timezone sealed its books at the same instant whether that suited them or not.

It lives in the finance schema, next to ``invoice_settings``, rather than as a
column on ``iam.properties``. Two reasons, and the second is the important one:

* it is finance configuration, and ``iam.properties`` is tenant identity;
* ``iam.properties`` is edited with ``property:edit``, so putting it there
  would let anyone who can correct the hotel's address change when the books
  seal. The hour a financial period closes belongs behind the same permission
  that closes it.

A row is optional and ``audit_hour`` is nullable inside it: NULL means "use the
deployment default", which keeps that default in one place instead of stamping
a copy of it onto every tenant the day they are created.

Revision ID: 0016_night_audit_settings
Revises: 0015_night_audit_actor
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0016_night_audit_settings"
down_revision = "0015_night_audit_actor"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "night_audit_settings",
        sa.Column("property_id", sa.dialects.postgresql.UUID(as_uuid=True),
                  primary_key=True),
        sa.Column("organization_id", sa.dialects.postgresql.UUID(as_uuid=True),
                  nullable=False),
        # NULL = follow the deployment default.
        sa.Column("audit_hour", sa.SmallInteger(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
        sa.Column("version", sa.BigInteger(), server_default="0",
                  nullable=False),
        # An hour outside the clock is not a preference, it is a typo that
        # would quietly stop a property ever closing.
        sa.CheckConstraint(
            "audit_hour IS NULL OR (audit_hour BETWEEN 0 AND 23)",
            name="ck_night_audit_hour",
        ),
        schema="finance",
    )


def downgrade() -> None:
    op.drop_table("night_audit_settings", schema="finance")
