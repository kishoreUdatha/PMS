"""The product is called MyGuest.

Revision ID: 0038_platform_name_myguest
Revises: 0037_idle_transaction_timeout

0032 seeded ``platform.name`` with "Chirala Bay PMS", the first hotel that
used the system. The product has its own name now, so the seeded value is
replaced. This happens only while the row still holds that seed: a name that
a platform operator has set on purpose is theirs, and a migration has no
business overwriting it.
"""
from __future__ import annotations

from alembic import op

revision: str = "0038_platform_name_myguest"
down_revision: str | None = "0037_idle_transaction_timeout"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.execute(
        "UPDATE platform.settings SET value = '\"MyGuest\"'::jsonb "
        "WHERE key = 'platform.name' AND value = '\"Chirala Bay PMS\"'::jsonb"
    )


def downgrade() -> None:
    op.execute(
        "UPDATE platform.settings SET value = '\"Chirala Bay PMS\"'::jsonb "
        "WHERE key = 'platform.name' AND value = '\"MyGuest\"'::jsonb"
    )
