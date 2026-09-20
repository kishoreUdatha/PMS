"""Room move and upgrade (screen 053)

Revision ID: 0017_room_moves
Revises: 0016_checkout
Create Date: 2026-09-09

A room move is not an edit of where a guest is staying — it is a fact that they
were in one room until a moment and another room after it. The schema already
supports that: ``stay_room_segments`` gives a stay one segment per room, and
``room_calendar_entries`` is ranged, so the old room's entry is shortened rather
than deleted and the new room gets its own. The nights already slept stay
attached to the room they were slept in, which is what housekeeping, minibar
disputes and any later audit actually need.

This table records the *decision*: who moved whom, why, when it took effect, and
what it cost. The rate difference is captured at the time because room rates
change; asking six months later what a guest was charged for an upgrade is only
answerable if the numbers were written down when the move was made.

Upgrades may need managerial approval, and the mockup says so on the screen.
Approval routing still does not exist (screen 042 has a queue but no path into
it), so a move needing approval is recorded as ``pending_approval`` and — this
is the important part — **is not performed**. The guest does not silently end up
in the better room while the paperwork is imagined.
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0017_room_moves"
down_revision: str | None = "0016_checkout"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

REASONS = (
    "upgrade_guest_request", "upgrade_complimentary", "downgrade",
    "maintenance", "guest_complaint", "operational", "overbooking",
)
STATUSES = ("completed", "pending_approval", "rejected", "cancelled")


def upgrade() -> None:
    quoted = lambda v: ", ".join(f"'{x}'" for x in v)  # noqa: E731

    op.execute(
        f"""
        CREATE TABLE booking.room_moves (
            id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            organization_id     uuid NOT NULL,
            property_id         uuid NOT NULL,
            -- RMU-YYYYMMDD-NNN, unique inside a property so two desks cannot
            -- mint the same reference on the same day.
            reference           varchar(30) NOT NULL,
            reservation_unit_id uuid NOT NULL,
            stay_id             uuid,
            from_room_id        uuid NOT NULL,
            to_room_id          uuid NOT NULL,
            from_room_type_id   uuid,
            to_room_type_id     uuid,
            from_entry_id       uuid,
            to_entry_id         uuid,
            effective_at        timestamptz NOT NULL,
            reason              varchar(30) NOT NULL,
            remarks             varchar(500),
            -- The money, as it stood when the move was decided.
            rate_current        numeric(12, 2) NOT NULL DEFAULT 0,
            rate_new            numeric(12, 2) NOT NULL DEFAULT 0,
            rate_difference     numeric(12, 2) NOT NULL DEFAULT 0,
            nights_applicable   integer NOT NULL DEFAULT 0,
            total_additional    numeric(19, 4) NOT NULL DEFAULT 0,
            folio_entry_id      uuid,
            notify_housekeeping boolean NOT NULL DEFAULT false,
            reissue_key         boolean NOT NULL DEFAULT false,
            guest_consent       boolean NOT NULL DEFAULT false,
            status              varchar(20) NOT NULL DEFAULT 'completed',
            requires_approval   boolean NOT NULL DEFAULT false,
            decided_by          uuid,
            decided_at          timestamptz,
            decision_note       varchar(300),
            created_by          uuid,
            created_at          timestamptz NOT NULL DEFAULT now(),
            updated_at          timestamptz NOT NULL DEFAULT now(),
            version             bigint NOT NULL DEFAULT 0,
            CONSTRAINT uq_room_move_reference UNIQUE (property_id, reference),
            CONSTRAINT ck_room_move_reason CHECK (reason IN ({quoted(REASONS)})),
            CONSTRAINT ck_room_move_status CHECK (status IN ({quoted(STATUSES)})),
            -- Moving a guest into the room they are already in is not a move.
            CONSTRAINT ck_room_move_rooms CHECK (from_room_id <> to_room_id),
            CONSTRAINT ck_room_move_nights CHECK (nights_applicable >= 0),
            CONSTRAINT fk_room_move_unit
                FOREIGN KEY (property_id, reservation_unit_id)
                REFERENCES booking.reservation_units (property_id, id),
            CONSTRAINT fk_room_move_from
                FOREIGN KEY (property_id, from_room_id)
                REFERENCES property.rooms (property_id, id),
            CONSTRAINT fk_room_move_to
                FOREIGN KEY (property_id, to_room_id)
                REFERENCES property.rooms (property_id, id)
        )
        """
    )
    op.execute(
        "CREATE INDEX ix_room_moves_unit ON booking.room_moves "
        "(reservation_unit_id, created_at DESC)"
    )
    op.execute(
        "CREATE INDEX ix_room_moves_property ON booking.room_moves "
        "(property_id, status, created_at DESC)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS booking.room_moves")
