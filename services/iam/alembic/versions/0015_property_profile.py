"""The property's own details, as onboarding step 2 asks for them.

``properties`` held a name, a code, a timezone, a currency and one free-text
``address`` line. Onboarding asks for a good deal more -- what kind of property
it is, who to contact, and a real postal address broken into its parts -- and
those cannot live in a single string: an invoice needs the state on its own to
decide the tax treatment, and a city cannot be filtered on when it is buried in
a sentence.

``address`` is kept and backfilled from the parts rather than dropped, because
existing screens read it and a migration that breaks the current product to
tidy its schema is a poor trade.

Revision ID: 0015_property_profile
Revises: 0014_onboarding
"""
import sqlalchemy as sa
from alembic import op

revision = "0015_property_profile"
down_revision = "0014_onboarding"
branch_labels = None
depends_on = None

KINDS = ("hotel", "resort", "serviced_apartment", "guest_house", "villa")

COLUMNS = (
    ("property_type", sa.String(length=32)),
    ("contact_email", sa.String(length=254)),
    ("contact_phone", sa.String(length=32)),
    ("address_line", sa.String(length=300)),
    ("city", sa.String(length=120)),
    ("state", sa.String(length=120)),
    ("postal_code", sa.String(length=20)),
    ("country", sa.String(length=80)),
    # Only the object key. The file itself lives in object storage, the same
    # way guest ID scans do.
    ("logo_key", sa.String(length=400)),
)


def upgrade() -> None:
    for name, kind in COLUMNS:
        op.add_column("properties", sa.Column(name, kind, nullable=True),
                      schema="iam")

    op.create_check_constraint(
        "ck_property_type",
        "properties",
        "property_type IS NULL OR property_type IN ("
        + ", ".join(f"'{k}'" for k in KINDS) + ")",
        schema="iam",
    )

    # What little is already known: the single address line becomes the street,
    # and the currency implies the country for the only market this runs in.
    op.execute(
        """
        UPDATE iam.properties
        SET address_line = COALESCE(address_line, address),
            country = COALESCE(country,
                               CASE WHEN currency = 'INR' THEN 'India' END)
        """
    )


def downgrade() -> None:
    op.drop_constraint("ck_property_type", "properties", schema="iam",
                       type_="check")
    for name, _ in reversed(COLUMNS):
        op.drop_column("properties", name, schema="iam")
