"""Where a property's night audit summary is sent.

The close happens at 3am with nobody watching, which is the point of it — but
it also means the one person who cares about the numbers is asleep. A summary
in the morning is how an unattended job stays accountable.

Nullable, and falls back to the property's contact address: a tenant who has
never expressed a preference still gets the mail, and the address they already
gave is the right guess. Empty string is a real answer meaning "send nothing",
distinct from NULL meaning "use the contact address".

Revision ID: 0019_night_audit_report_email
Revises: 0018_no_show_policy
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0019_night_audit_report_email"
down_revision = "0018_no_show_policy"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "night_audit_settings",
        sa.Column("report_email", sa.String(length=320), nullable=True),
        schema="finance",
    )


def downgrade() -> None:
    op.drop_column("night_audit_settings", "report_email", schema="finance")
