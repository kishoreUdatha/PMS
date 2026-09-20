"""Hold a set of rooms for a group, and give back what nobody takes

Revision ID: 0055_group_blocks
Revises: 0054_corporate_rate_account
Create Date: 2026-09-17

A wedding wants thirty rooms in March. Today the only way to record that is to
make thirty bookings with thirty invented guest names, or to make none and
hope. Neither is what a block is: a block holds rooms *off sale* without
selling them, lets the group take them one at a time as names arrive, and
hands back whatever is left on an agreed date.

``room_type_inventory_days.allotment_units`` was built for exactly this and
has never been written to -- it is subtracted in every sellable calculation
and has been zero everywhere since the column was created. This is what fills
it in.

Three tables, because a block is three different facts:

``group_blocks``
    The agreement: who it is for, the dates, the cut-off, and whether it is
    tentative or definite. Tentative and definite both hold rooms; the
    difference is what a forecast should believe, not what inventory does.

``group_block_lines``
    How many rooms of each type were agreed, and at what rate. The rate lives
    here so a pick-up can quote the negotiated figure instead of the desk
    remembering it -- the same mistake corporate rates made before 0054.

``group_block_nights``
    What this block still holds, per room type, per night. This is the part
    that cannot be derived. ``allotment_units`` is one counter shared by every
    block on that night, so releasing a block by subtracting "its" rooms is
    only possible if the rooms it still holds were written down. Without this
    table, two blocks on one night make each other's release wrong -- and a
    release that subtracts too much silently oversells the hotel.

A pick-up is a normal reservation carrying ``group_block_id``. The room moves
from ``allotment_units`` to ``reserved_units``: net sellable is unchanged,
which is right, because the room was already off sale. That is the whole point
of blocking it.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision: str = "0055_group_blocks"
down_revision: str | None = "0054_corporate_rate_account"
branch_labels = None
depends_on = None

RUNTIME_ROLE = "pms_app"

#: Holding rooms and accepting pick-ups. Everything else is an ending.
STATUSES = ("open", "released", "cancelled")
#: What a forecast should believe. Both hold inventory.
COMMITMENTS = ("tentative", "definite")


def _rls(table: str) -> None:
    op.execute(f"ALTER TABLE booking.{table} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE booking.{table} FORCE ROW LEVEL SECURITY")
    op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON booking.{table}")
    op.execute(
        f"""
        CREATE POLICY tenant_isolation ON booking.{table}
        USING (tenancy.org_visible(organization_id))
        WITH CHECK (tenancy.org_visible(organization_id))
        """
    )
    op.execute(
        f"""
        DO $$
        BEGIN
          IF EXISTS (SELECT FROM pg_roles WHERE rolname = '{RUNTIME_ROLE}') THEN
            GRANT SELECT, INSERT, UPDATE, DELETE
              ON booking.{table} TO {RUNTIME_ROLE};
          END IF;
        END
        $$
        """
    )


def upgrade() -> None:
    statuses = ", ".join(f"'{s}'" for s in STATUSES)
    commitments = ", ".join(f"'{c}'" for c in COMMITMENTS)

    op.create_table(
        "group_blocks",
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True),
        sa.Column("organization_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("property_id", pg.UUID(as_uuid=True), nullable=False),
        # Short and quotable. A group is referred to by name on the phone and
        # by code on paper, and both are needed.
        sa.Column("code", sa.String(20), nullable=False),
        sa.Column("name", sa.String(160), nullable=False),
        sa.Column("commercial_account_id", pg.UUID(as_uuid=True), nullable=True),
        sa.Column("arrival_date", sa.Date(), nullable=False),
        sa.Column("departure_date", sa.Date(), nullable=False),
        # Null means no cut-off was agreed: the rooms are held until somebody
        # releases them by hand. Not a default of "today", which would throw
        # the block away the moment it was created.
        sa.Column("cut_off_date", sa.Date(), nullable=True),
        sa.Column("commitment", sa.String(12), nullable=False,
                  server_default="tentative"),
        sa.Column("status", sa.String(12), nullable=False,
                  server_default="open"),
        sa.Column("notes", sa.String(500), nullable=True),
        sa.Column("created_by", pg.UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("released_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("released_by", pg.UUID(as_uuid=True), nullable=True),
        sa.Column("version", sa.BigInteger(), nullable=False,
                  server_default="1"),
        sa.CheckConstraint(f"status IN ({statuses})", name="ck_block_status"),
        sa.CheckConstraint(f"commitment IN ({commitments})",
                           name="ck_block_commitment"),
        sa.CheckConstraint("departure_date > arrival_date",
                           name="ck_block_dates"),
        # A cut-off after the group arrives releases rooms the group is
        # already sleeping in.
        sa.CheckConstraint(
            "cut_off_date IS NULL OR cut_off_date <= arrival_date",
            name="ck_block_cutoff"),
        sa.ForeignKeyConstraint(["property_id"], ["iam.properties.id"]),
        sa.ForeignKeyConstraint(["commercial_account_id"],
                                ["engagement.commercial_accounts.id"],
                                ondelete="RESTRICT"),
        schema="booking",
    )
    op.create_index("ux_block_code", "group_blocks",
                    ["property_id", "code"], unique=True, schema="booking")
    # The two questions the screen asks: what is open, and what is arriving.
    op.create_index("ix_block_open", "group_blocks",
                    ["property_id", "status", "arrival_date"], schema="booking")
    # Which blocks the cut-off sweep has to look at. Only open ones can be
    # released, and only ones that agreed a cut-off.
    op.create_index("ix_block_cutoff", "group_blocks",
                    ["cut_off_date"], schema="booking",
                    postgresql_where=sa.text(
                        "status = 'open' AND cut_off_date IS NOT NULL"))

    op.create_table(
        "group_block_lines",
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True),
        sa.Column("organization_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("property_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("block_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("room_type_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("rooms_blocked", sa.Integer(), nullable=False),
        # The agreed rate for this room type, per night, before tax. Null
        # means the room type's own price stands.
        sa.Column("nightly_rate", sa.Numeric(12, 2), nullable=True),
        sa.CheckConstraint("rooms_blocked > 0", name="ck_block_line_rooms"),
        sa.CheckConstraint("nightly_rate IS NULL OR nightly_rate >= 0",
                           name="ck_block_line_rate"),
        sa.ForeignKeyConstraint(["block_id"], ["booking.group_blocks.id"],
                                ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["room_type_id"], ["property.room_types.id"]),
        schema="booking",
    )
    # One line per room type: two lines of the same type would be two answers
    # to "how many deluxe rooms did we agree".
    op.create_index("ux_block_line_type", "group_block_lines",
                    ["block_id", "room_type_id"], unique=True, schema="booking")

    op.create_table(
        "group_block_nights",
        sa.Column("organization_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("property_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("block_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("room_type_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("stay_date", sa.Date(), nullable=False),
        # What this block still holds on this night. Decremented as rooms are
        # picked up, zeroed when the block is released. Never negative: the
        # block cannot give back more than it took.
        sa.Column("rooms_held", sa.Integer(), nullable=False),
        sa.CheckConstraint("rooms_held >= 0", name="ck_block_night_held"),
        sa.ForeignKeyConstraint(["block_id"], ["booking.group_blocks.id"],
                                ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("block_id", "room_type_id", "stay_date"),
        schema="booking",
    )
    # The release path reads this by block; the availability view by night.
    op.create_index("ix_block_night_day", "group_block_nights",
                    ["property_id", "room_type_id", "stay_date"],
                    schema="booking")

    # Which block a booking took its room from. SET NULL rather than RESTRICT:
    # a block may be deleted years later, and losing the link is a smaller
    # wrong than being unable to remove it -- the reservation itself stands on
    # its own.
    op.add_column("reservations",
                  sa.Column("group_block_id", pg.UUID(as_uuid=True),
                            nullable=True),
                  schema="booking")
    op.create_foreign_key(
        "fk_reservation_block", "reservations", "group_blocks",
        ["group_block_id"], ["id"],
        source_schema="booking", referent_schema="booking",
        ondelete="SET NULL",
    )
    op.create_index("ix_reservation_block", "reservations",
                    ["group_block_id"], schema="booking",
                    postgresql_where=sa.text("group_block_id IS NOT NULL"))

    for t in ("group_blocks", "group_block_lines", "group_block_nights"):
        _rls(t)


def downgrade() -> None:
    op.drop_index("ix_reservation_block", "reservations", schema="booking")
    op.drop_constraint("fk_reservation_block", "reservations",
                       schema="booking", type_="foreignkey")
    op.drop_column("reservations", "group_block_id", schema="booking")
    op.drop_table("group_block_nights", schema="booking")
    op.drop_table("group_block_lines", schema="booking")
    op.drop_table("group_blocks", schema="booking")
