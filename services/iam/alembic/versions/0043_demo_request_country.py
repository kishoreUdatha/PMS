"""Demo requests carry a country.

Revision ID: 0043_demo_request_country
Revises: 0042_guest_portal_template

MyGuest is sold to hotels in any country, so the landing page's demo form
asks for a country rather than an Indian state. ``state`` stays for the
requests already received with one.
"""
from __future__ import annotations

from alembic import op

revision: str = "0043_demo_request_country"
down_revision: str | None = "0042_guest_portal_template"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.execute("ALTER TABLE platform.demo_requests "
               "ADD COLUMN IF NOT EXISTS country varchar(80) NULL")


def downgrade() -> None:
    op.execute("ALTER TABLE platform.demo_requests DROP COLUMN IF EXISTS country")
