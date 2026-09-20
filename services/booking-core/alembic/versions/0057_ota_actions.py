"""Things the hotel owes an OTA, with the clock running

Revision ID: 0057_ota_actions
Revises: 0056_room_guest
Create Date: 2026-09-17

When a booking that came from an OTA turns into a no-show, the hotel has to
report it to that OTA or it pays commission on a room nobody slept in. The
window is **24 hours**. Nothing in this system said so, and nothing recorded
whether anyone had done it: a clerk marked the no-show, the room went back on
sale, and the commission quietly stayed owed.

That is the worst shape a failure can take -- invisible, recurring, and paid
for in money rather than in complaints. Nobody notices until a monthly
statement, by which time the window has closed on every one of them.

So an obligation becomes a row. Raised when the no-show is recorded, carrying
the OTA's own reservation code because that is what the extranet asks for,
with a deadline 24 hours out, and closed only when somebody says it was done
and what reference they got back.

Deliberately not automatic reporting. Whether the channel manager can report a
no-show depends on the channel and on the plan, and a queue that silently
fails to send is worse than one a person works through. The row is the record
either way: if an API is wired up later it closes these rows instead of a
person doing it.

``UNIQUE (reservation_unit_id, action_type)`` because an obligation is a fact
about a room, not an event. The night audit and the manual screen can both
reach the same no-show -- the same reason their penalty shares an idempotency
key -- and two rows would mean two people ringing the same OTA about the same
booking.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision: str = "0057_ota_actions"
down_revision: str | None = "0056_room_guest"
branch_labels = None
depends_on = None

RUNTIME_ROLE = "pms_app"

#: What the hotel may owe a channel.
ACTION_TYPES = ("no_show", "cancellation", "invalid_card")
STATUSES = ("open", "reported", "dismissed")


def upgrade() -> None:
    types = ", ".join(f"'{t}'" for t in ACTION_TYPES)
    statuses = ", ".join(f"'{s}'" for s in STATUSES)

    op.create_table(
        "ota_actions",
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True),
        sa.Column("organization_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("property_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("reservation_id", pg.UUID(as_uuid=True), nullable=True),
        sa.Column("reservation_unit_id", pg.UUID(as_uuid=True), nullable=False),
        # The channel's own name for the booking. Without it the row cannot be
        # acted on: an extranet is searched by its reference, not by ours.
        sa.Column("ota_name", sa.String(80), nullable=True),
        sa.Column("ota_reservation_code", sa.String(120), nullable=True),
        sa.Column("reservation_number", sa.String(40), nullable=True),
        sa.Column("action_type", sa.String(20), nullable=False),
        sa.Column("status", sa.String(20), nullable=False,
                  server_default="open"),
        # 24 hours from the moment the obligation arose, not from midnight:
        # a no-show recorded at 23:50 does not get ten minutes.
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reported_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reported_by", pg.UUID(as_uuid=True), nullable=True),
        # What the OTA gave back. A waiver nobody can evidence is a waiver the
        # hotel argues about later and loses.
        sa.Column("reference", sa.String(160), nullable=True),
        sa.Column("note", sa.String(400), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("version", sa.BigInteger(), nullable=False,
                  server_default="1"),
        sa.CheckConstraint(f"action_type IN ({types})", name="ck_ota_action_type"),
        sa.CheckConstraint(f"status IN ({statuses})", name="ck_ota_action_status"),
        # Closed rows say who closed them and when.
        sa.CheckConstraint(
            "status <> 'reported' OR reported_at IS NOT NULL",
            name="ck_ota_action_reported"),
        sa.ForeignKeyConstraint(["reservation_unit_id"],
                                ["booking.reservation_units.id"],
                                ondelete="CASCADE"),
        schema="distribution",
    )
    # One obligation per room per kind. Both the audit and the manual screen
    # can record the same no-show; neither should raise a second row.
    op.create_index("ux_ota_action_unit", "ota_actions",
                    ["reservation_unit_id", "action_type"], unique=True,
                    schema="distribution")
    # The only question the screen asks: what is still open, soonest first.
    op.create_index("ix_ota_action_open", "ota_actions",
                    ["property_id", "due_at"], schema="distribution",
                    postgresql_where=sa.text("status = 'open'"))

    op.execute("ALTER TABLE distribution.ota_actions "
               "ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE distribution.ota_actions "
               "FORCE ROW LEVEL SECURITY")
    op.execute("DROP POLICY IF EXISTS tenant_isolation "
               "ON distribution.ota_actions")
    op.execute(
        """
        CREATE POLICY tenant_isolation ON distribution.ota_actions
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
              ON distribution.ota_actions TO {RUNTIME_ROLE};
          END IF;
        END
        $$
        """
    )


def downgrade() -> None:
    op.drop_table("ota_actions", schema="distribution")
