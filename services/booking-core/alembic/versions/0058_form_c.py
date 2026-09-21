"""Form C — reporting a foreign guest to the Bureau of Immigration.

Revision ID: 0058_form_c
Revises: 0057_ota_actions

Every hotel, guest house, lodge and homestay in India must report the arrival
of a foreign national to the Bureau of Immigration within 24 hours of
check-in. It is Rule 14 of the Registration of Foreigners Rules 1992, under
the Foreigners Act 1946, it is filed on the FRRO portal, and failing to file
is an offence -- this is the record a police or immigration inspection asks
to see.

The system already knew about the obligation and printed it on the
registration card:

    "Foreign national -- passport, visa and arrival details recorded.
     Form C is to be filed with the Bureau of Immigration within 24 hours
     of arrival."

The card was wrong. Visa details were *not* recorded, because no visa column
existed anywhere in the database; a passport was only ``id_type = 'passport'``
with a bare number, without the place and date of issue or the expiry the
form asks for. So a clerk was told at the desk that the data was captured,
and then had to re-key it into the FRRO portal from the passport -- if the
guest was still standing there, and if anyone remembered at all, because
nothing listed who was due.

**Why a table and not columns on the guest.** A visa is not a property of a
person the way a date of birth is. The same guest returns next winter on a
new visa, with a renewed passport, having entered India at a different
airport, going on somewhere else afterwards. Columns on ``engagement.guests``
would let the second visit quietly overwrite what was filed for the first --
and what was filed is the only evidence the property has that it complied.
So this is one row per STAY, snapshotted at the time, and it stays what it
was. ``0052_registration_details`` made the same call for vehicle and purpose
of visit, for the same reason.

**Why the filing lives here too.** There is exactly one filing per stay, and
splitting "the data" from "whether it was sent" across two tables would mean
the register has to join to answer the only question anyone asks of it --
who is due, and who is done.

**Nothing here is required of an Indian guest.** The row is created only for
a foreign national, and the columns are nullable because a desk collects a
passport in stages: the number when the guest hands it over, the rest as it
is typed. A half-filled Form C that says so is more useful than a refusal
that loses what was collected.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0058_form_c"
down_revision: str | None = "0057_ota_actions"
branch_labels = None
depends_on = None

RUNTIME_ROLE = "pms_app"

#: Filed or not, and nothing in between that pretends to be progress.
#: ``exempt`` is for the row somebody opened and then found was an Indian
#: passport holder -- deleting it would lose the note saying why.
STATUSES = ("pending", "filed", "exempt")


def upgrade() -> None:
    op.create_table(
        "form_c",
        sa.Column("id", sa.dialects.postgresql.UUID(as_uuid=True),
                  primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("organization_id", sa.dialects.postgresql.UUID(as_uuid=True),
                  nullable=False),
        sa.Column("property_id", sa.dialects.postgresql.UUID(as_uuid=True),
                  nullable=False),
        #: The stay, not the booking: a family on one reservation in three
        #: rooms is three arrivals to report, and the form is per person.
        sa.Column("reservation_unit_id",
                  sa.dialects.postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("guest_id", sa.dialects.postgresql.UUID(as_uuid=True)),

        # ---- who, as the form asks it -----------------------------------
        sa.Column("full_name", sa.String(200)),
        sa.Column("sex", sa.String(10)),
        sa.Column("date_of_birth", sa.Date),
        sa.Column("nationality", sa.String(80)),

        # ---- passport ----------------------------------------------------
        sa.Column("passport_number", sa.String(60)),
        sa.Column("passport_issue_place", sa.String(120)),
        sa.Column("passport_issue_date", sa.Date),
        sa.Column("passport_expiry_date", sa.Date),

        # ---- visa ---------------------------------------------------------
        #: None of this existed anywhere before. A Form C cannot be filed
        #: without it, which is why the card's claim that it was "recorded"
        #: was the most misleading line in the product.
        sa.Column("visa_number", sa.String(60)),
        sa.Column("visa_type", sa.String(60)),
        sa.Column("visa_issue_place", sa.String(120)),
        sa.Column("visa_issue_date", sa.Date),
        sa.Column("visa_expiry_date", sa.Date),

        # ---- the journey ----------------------------------------------------
        #: Arrival in INDIA, which is not arrival at the hotel and routinely
        #: differs by days. The form asks for the country's border, not the
        #: property's front door, and conflating them is the commonest way a
        #: Form C is filed wrong.
        sa.Column("arrived_in_india_on", sa.Date),
        sa.Column("arrived_in_india_at", sa.String(120)),
        sa.Column("address_in_india", sa.String(300)),
        sa.Column("permanent_address", sa.String(300)),
        sa.Column("purpose_of_visit", sa.String(120)),
        sa.Column("next_destination", sa.String(160)),

        # ---- the filing ------------------------------------------------------
        sa.Column("status", sa.String(20), nullable=False,
                  server_default="pending"),
        sa.Column("filed_at", sa.DateTime(timezone=True)),
        sa.Column("filed_by", sa.dialects.postgresql.UUID(as_uuid=True)),
        #: What the FRRO portal gave back. The only thing that proves the
        #: filing happened, so it is stored rather than trusted to a memory
        #: of having clicked submit.
        sa.Column("acknowledgement_no", sa.String(80)),
        sa.Column("notes", sa.String(500)),

        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("version", sa.BigInteger, nullable=False,
                  server_default="0"),

        sa.CheckConstraint(
            "status IN " + str(STATUSES), name="ck_form_c_status"),
        sa.ForeignKeyConstraint(["reservation_unit_id"],
                                ["booking.reservation_units.id"],
                                name="fk_form_c_unit"),
        #: One report per stay. Two rows for one arrival is two filings for
        #: one guest, which is how a register starts disagreeing with itself.
        sa.UniqueConstraint("reservation_unit_id", name="uq_form_c_unit"),
        schema="booking",
    )
    #: The register's own query: this property, due first.
    op.create_index("ix_form_c_property_status", "form_c",
                    ["property_id", "status"], schema="booking")

    op.execute("ALTER TABLE booking.form_c ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE booking.form_c FORCE ROW LEVEL SECURITY")
    op.execute("DROP POLICY IF EXISTS tenant_isolation ON booking.form_c")
    op.execute(
        """
        CREATE POLICY tenant_isolation ON booking.form_c
        USING (tenancy.org_visible(organization_id))
        WITH CHECK (tenancy.org_visible(organization_id))
        """
    )
    op.execute(f"""
        DO $$
        BEGIN
          IF EXISTS (SELECT FROM pg_roles WHERE rolname = '{RUNTIME_ROLE}') THEN
            GRANT SELECT, INSERT, UPDATE, DELETE
              ON booking.form_c TO {RUNTIME_ROLE};
          END IF;
        END
        $$
    """)


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS tenant_isolation ON booking.form_c")
    op.drop_index("ix_form_c_property_status", table_name="form_c",
                  schema="booking")
    op.drop_table("form_c", schema="booking")
