"""A phone number on the user.

Sign-up asks for a mobile number and had nowhere to put it, so it was
collected and dropped on the floor. Asking somebody for something and then
discarding it is worse than not asking: they believe it is on file, and the
first time anybody needs to reach them it is not there.

Revision ID: 0019_user_phone
Revises: 0018_property_code
"""
import sqlalchemy as sa
from alembic import op

revision = "0019_user_phone"
down_revision = "0018_property_code"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "users", sa.Column("phone", sa.String(length=32), nullable=True),
        schema="iam",
    )


def downgrade() -> None:
    op.drop_column("users", "phone", schema="iam")
