"""Display exchange rates for the booking engine.

Revision ID: 0044_display_rates
Revises: 0043_demo_request_country

A guest from abroad can see the booking page's prices converted to their own
currency. The conversion is for reading only: they pay in the property's
currency. The rates are the platform team's to maintain in the console, as
``{"base": "USD", "as_of": "2026-09-29", "rates": {"USD": 1, "INR": 83.2,
"EUR": 0.92}}``, where each rate is units of that currency per one unit of
the base.

Declared empty. Rates written into a migration would be wrong within a
week, and nobody would notice. Until somebody sets them, the booking page
shows no currency picker.
"""
from __future__ import annotations

from alembic import op

revision: str = "0044_display_rates"
down_revision: str | None = "0043_demo_request_country"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.execute("""
        INSERT INTO platform.settings (key, value, description)
        VALUES ('booking_engine.display_rates',
                '{"base": "USD", "as_of": null, "rates": {}}'::jsonb,
                'Approximate exchange rates for the booking page''s currency picker. '
                'Units of each currency per one unit of the base. For display only; '
                'guests pay in the property''s currency.')
        ON CONFLICT (key) DO NOTHING
    """)


def downgrade() -> None:
    op.execute("DELETE FROM platform.settings WHERE key = 'booking_engine.display_rates'")
