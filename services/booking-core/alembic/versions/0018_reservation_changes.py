"""Cancellation policy and the record of reservation changes (screen 029)

Revision ID: 0018_reservation_changes
Revises: 0017_room_moves
Create Date: 2026-09-09

Two things were missing to make a booking changeable.

**A cancellation policy.** The mockup quotes a specific one — free up to seven
days before arrival, one night per room inside that, nothing back for a no-show
— and a penalty figure is indefensible unless the rule that produced it is
written down somewhere a person can read and change. So the policy is a row per
property, with the numbers the calculation uses and the wording the guest is
shown, rather than a constant buried in a pricing function.

**A record of the change itself.** ``reservation_changes`` is append-only. A
reservation modified three times is not one edit; it is three decisions, each
with a price attached, and the guest may well ask about any of them. The
before/after payload is stored so the question "what exactly changed on the
14th" has an answer that does not depend on reconstructing it from the current
state.

Cancellation is recorded here rather than being only a status flip, for the same
reason: ``status = 'cancelled'`` says a booking is gone but not when, why, who
did it, what was charged for it or what was owed back.
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0018_reservation_changes"
down_revision: str | None = "0017_room_moves"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

KINDS = ("modification", "cancellation")
STATUSES = ("applied", "pending_approval", "rejected")
REASONS = (
    "guest_request", "travel_plans", "date_change", "price", "duplicate",
    "no_show", "property_initiated", "other",
)


def upgrade() -> None:
    q = lambda v: ", ".join(f"'{x}'" for x in v)  # noqa: E731

    op.execute(
        """
        CREATE TABLE property.cancellation_policies (
            id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            organization_id   uuid NOT NULL,
            property_id       uuid NOT NULL,
            name              varchar(120) NOT NULL,
            -- Cancel more than this many days before arrival and nothing is
            -- charged.
            free_until_days   integer NOT NULL DEFAULT 7,
            -- Inside that window, this many nights per room are charged.
            penalty_nights    integer NOT NULL DEFAULT 1,
            no_show_refund    boolean NOT NULL DEFAULT false,
            -- Whether a manager has to sign off, and above what refund.
            requires_approval boolean NOT NULL DEFAULT false,
            approval_above    numeric(19, 4) NOT NULL DEFAULT 0,
            -- The wording the guest is shown, kept beside the numbers so the
            -- two cannot drift apart.
            policy_text       varchar(600) NOT NULL,
            is_default        boolean NOT NULL DEFAULT true,
            created_at        timestamptz NOT NULL DEFAULT now(),
            updated_at        timestamptz NOT NULL DEFAULT now(),
            version           bigint NOT NULL DEFAULT 0,
            CONSTRAINT uq_cancel_policy_prop UNIQUE (property_id, name),
            CONSTRAINT ck_cancel_policy_days CHECK (free_until_days >= 0),
            CONSTRAINT ck_cancel_policy_nights CHECK (penalty_nights >= 0)
        )
        """
    )
    op.execute(
        "CREATE UNIQUE INDEX uq_cancel_policy_default "
        "ON property.cancellation_policies (property_id) WHERE is_default"
    )

    op.execute(
        f"""
        CREATE TABLE booking.reservation_changes (
            id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            organization_id  uuid NOT NULL,
            property_id      uuid NOT NULL,
            reservation_id   uuid NOT NULL,
            kind             varchar(20) NOT NULL,
            status           varchar(20) NOT NULL DEFAULT 'applied',
            -- What it looked like before and after, so a past change is
            -- readable without replaying every later one.
            before_state     jsonb,
            after_state      jsonb,
            amount_before    numeric(19, 4) NOT NULL DEFAULT 0,
            amount_after     numeric(19, 4) NOT NULL DEFAULT 0,
            difference       numeric(19, 4) NOT NULL DEFAULT 0,
            penalty_amount   numeric(19, 4) NOT NULL DEFAULT 0,
            refund_estimate  numeric(19, 4) NOT NULL DEFAULT 0,
            folio_entry_id   uuid,
            reason           varchar(30),
            notes            varchar(600),
            policy_id        uuid,
            requires_approval boolean NOT NULL DEFAULT false,
            decided_by       uuid,
            decided_at       timestamptz,
            created_by       uuid,
            created_at       timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT ck_res_change_kind CHECK (kind IN ({q(KINDS)})),
            CONSTRAINT ck_res_change_status CHECK (status IN ({q(STATUSES)})),
            CONSTRAINT ck_res_change_reason
                CHECK (reason IS NULL OR reason IN ({q(REASONS)})),
            CONSTRAINT fk_res_change_reservation
                FOREIGN KEY (property_id, reservation_id)
                REFERENCES booking.reservations (property_id, id)
        )
        """
    )
    op.execute(
        "CREATE INDEX ix_res_changes_reservation "
        "ON booking.reservation_changes (reservation_id, created_at DESC)"
    )

    # Seed the policy the mockup states, for every property that has none.
    op.execute(
        """
        INSERT INTO property.cancellation_policies
            (organization_id, property_id, name, free_until_days,
             penalty_nights, no_show_refund, requires_approval, approval_above,
             policy_text, is_default)
        SELECT p.organization_id, p.id, 'Standard', 7, 1, false, true, 5000,
               'Free cancellation up to 7 days before arrival. Cancellations '
               'within 7 days are subject to 1 night room charge per room. '
               'No refund for no-shows.',
               true
        FROM iam.properties p
        WHERE NOT EXISTS (
            SELECT 1 FROM property.cancellation_policies c
             WHERE c.property_id = p.id
        )
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS booking.reservation_changes")
    op.execute("DROP TABLE IF EXISTS property.cancellation_policies")
