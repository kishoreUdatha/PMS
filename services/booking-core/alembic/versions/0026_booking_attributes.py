"""Business source and market segment become masters

Revision ID: 0026_booking_attrs
Revises: 0025_reconcile_inv
Create Date: 2026-09-10

``reservations.business_source`` and ``reservations.market_segment`` were free
text. Free text for a classification is a report that cannot be trusted:
"MakeMyTrip", "makemytrip", "MMT" and "Make My Trip" are four sources to a
GROUP BY and one to everybody else, and nobody notices until the commission
statement disagrees with the channel's.

Both are the same shape — a short controlled vocabulary attached to a booking —
so they share a table and differ by ``kind``, the way ``commercial_accounts``
holds companies and travel agents. They are organisation-scoped: a chain
classifies its business the same way across its properties, and a segment that
means one thing at one hotel and another elsewhere is not a segment.

The text columns are dropped rather than kept alongside the new references.
Two records of the same fact drift, which is the whole problem being fixed
here. Existing values are seeded into the master first so nothing is lost.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0026_booking_attrs"
down_revision = "0025_reconcile_inv"
branch_labels = None
depends_on = None

KINDS = ("business_source", "market_segment")


def upgrade() -> None:
    op.create_table(
        "booking_attributes",
        sa.Column("id", sa.dialects.postgresql.UUID(as_uuid=True),
                  primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("organization_id", sa.dialects.postgresql.UUID(as_uuid=True),
                  nullable=False),
        # Which vocabulary this row belongs to. Business source says which OTA
        # or agent the booking came through; market segment says what kind of
        # business it is. Different questions, identical shape.
        sa.Column("kind", sa.String(30), nullable=False),
        # The short form the desk types and reports group by.
        sa.Column("code", sa.String(30), nullable=False),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("status", sa.String(20), nullable=False,
                  server_default="active"),
        # Puts the common choices at the top of the picker. Ties fall back to
        # name, so a list nobody has ordered is still alphabetical.
        sa.Column("sort_order", sa.Integer(), nullable=False,
                  server_default="0"),
        sa.Column("notes", sa.String(400), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("created_by", sa.dialects.postgresql.UUID(as_uuid=True),
                  nullable=True),
        sa.Column("updated_by", sa.dialects.postgresql.UUID(as_uuid=True),
                  nullable=True),
        sa.Column("version", sa.BigInteger(), nullable=False,
                  server_default="0"),
        sa.CheckConstraint(
            "kind IN ('business_source', 'market_segment')",
            name="ck_booking_attribute_kind",
        ),
        sa.CheckConstraint(
            "status IN ('active', 'inactive')",
            name="ck_booking_attribute_status",
        ),
        # A vocabulary with two rows meaning the same thing is the problem
        # this table exists to solve, so both the code and the name are unique
        # within their kind.
        sa.UniqueConstraint("organization_id", "kind", "code",
                            name="uq_booking_attribute_code"),
        sa.UniqueConstraint("organization_id", "kind", "name",
                            name="uq_booking_attribute_name"),
        schema="engagement",
    )
    op.create_index(
        "ix_booking_attribute_lookup", "booking_attributes",
        ["organization_id", "kind", "status"], schema="engagement",
    )

    # Seed from what bookings already say, so no existing classification is
    # lost. The code is the name upper-cased with spaces removed — good enough
    # for values a human typed, and editable afterwards.
    for kind, column in (("business_source", "business_source"),
                         ("market_segment", "market_segment")):
        op.execute(
            f"""
            INSERT INTO engagement.booking_attributes
                (organization_id, kind, code, name)
            SELECT DISTINCT r.organization_id, '{kind}',
                   upper(regexp_replace(btrim(r.{column}), '\\s+', '', 'g')),
                   btrim(r.{column})
            FROM booking.reservations r
            WHERE r.{column} IS NOT NULL AND btrim(r.{column}) <> ''
            ON CONFLICT DO NOTHING
            """
        )

    for column in ("business_source", "market_segment"):
        op.add_column(
            "reservations",
            sa.Column(f"{column}_id",
                      sa.dialects.postgresql.UUID(as_uuid=True), nullable=True),
            schema="booking",
        )
        op.create_foreign_key(
            f"fk_reservation_{column}", "reservations", "booking_attributes",
            [f"{column}_id"], ["id"],
            source_schema="booking", referent_schema="engagement",
            ondelete="RESTRICT",
        )
        # Point each booking at the row just seeded from its own text.
        op.execute(
            f"""
            UPDATE booking.reservations r
               SET {column}_id = a.id
              FROM engagement.booking_attributes a
             WHERE a.organization_id = r.organization_id
               AND a.kind = '{column}'
               AND a.name = btrim(r.{column})
               AND r.{column} IS NOT NULL AND btrim(r.{column}) <> ''
            """
        )
        op.drop_column("reservations", column, schema="booking")

    op.create_index("ix_reservation_business_source", "reservations",
                    ["business_source_id"], schema="booking",
                    postgresql_where=sa.text("business_source_id IS NOT NULL"))
    op.create_index("ix_reservation_market_segment", "reservations",
                    ["market_segment_id"], schema="booking",
                    postgresql_where=sa.text("market_segment_id IS NOT NULL"))


def downgrade() -> None:
    op.drop_index("ix_reservation_market_segment", "reservations",
                  schema="booking")
    op.drop_index("ix_reservation_business_source", "reservations",
                  schema="booking")
    for column in ("business_source", "market_segment"):
        op.add_column(
            "reservations", sa.Column(column, sa.String(120), nullable=True),
            schema="booking",
        )
        op.execute(
            f"""
            UPDATE booking.reservations r
               SET {column} = a.name
              FROM engagement.booking_attributes a
             WHERE a.id = r.{column}_id
            """
        )
        op.drop_constraint(f"fk_reservation_{column}", "reservations",
                           schema="booking", type_="foreignkey")
        op.drop_column("reservations", f"{column}_id", schema="booking")
    op.drop_index("ix_booking_attribute_lookup", "booking_attributes",
                  schema="engagement")
    op.drop_table("booking_attributes", schema="engagement")
