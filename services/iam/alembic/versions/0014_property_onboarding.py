"""Where a property is in its onboarding, and when it went live.

A multi-tenant product is judged on the first hour: a new property arrives with
nothing and has to reach a state where the front desk can actually be opened.
That is a sequence -- account, property, structure, rooms, rates, billing,
team, imported bookings, connections, go live -- and it has to survive the
person doing it closing the laptop halfway through.

Two things are stored and nothing else:

**Where they are.** ``current_step`` and a per-step record of what has been
visited or deliberately skipped. Whether a step is *complete* is not stored --
it is derived from the property's real data, because a stored flag and an empty
room list disagree the moment somebody deletes a room, and the flag is the one
that lies.

**When it went live.** ``activated_at`` is the fact everything else keys off:
before it, the property is a draft being assembled; after it, it is a working
hotel and the wizard stops being offered.

Revision ID: 0014_onboarding
Revises: 0013_rooms_view_grants
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0014_onboarding"
down_revision = "0013_rooms_view_grants"
branch_labels = None
depends_on = None

STEPS = (
    "account", "property", "structure", "rooms", "rates",
    "billing", "team", "import", "connections", "golive",
)


def upgrade() -> None:
    op.create_table(
        "property_onboarding",
        sa.Column("property_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("iam.properties.id", ondelete="CASCADE"),
                  primary_key=True),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("current_step", sa.String(length=32), nullable=False,
                  server_default="account"),
        # {"rooms": {"visited": true, "skipped": false}, ...}. Deliberately a
        # document rather than ten columns: the steps are a product decision
        # that will change, and a migration per reordering is a poor trade.
        sa.Column("steps", postgresql.JSONB, nullable=False,
                  server_default=sa.text("'{}'::jsonb")),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        # Null until the property goes live. This is the flag the rest of the
        # system should read to know whether it is dealing with a draft.
        sa.Column("activated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("activated_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("version", sa.BigInteger, nullable=False, server_default="0"),
        sa.CheckConstraint(
            "current_step IN (" + ", ".join(f"'{s}'" for s in STEPS) + ")",
            name="ck_onboarding_step",
        ),
        schema="iam",
    )
    op.create_index(
        "ix_onboarding_org", "property_onboarding", ["organization_id"],
        schema="iam",
    )

    # Every property that already exists predates onboarding and is plainly
    # already in use, so it is marked live rather than being dropped back into
    # a wizard it never went through.
    op.execute(
        """
        INSERT INTO iam.property_onboarding
            (property_id, organization_id, current_step, activated_at)
        SELECT id, organization_id, 'golive', now() FROM iam.properties
        ON CONFLICT (property_id) DO NOTHING
        """
    )


def downgrade() -> None:
    op.drop_index("ix_onboarding_org", table_name="property_onboarding",
                  schema="iam")
    op.drop_table("property_onboarding", schema="iam")
