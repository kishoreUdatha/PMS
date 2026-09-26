"""Every property has a default cancellation policy

Revision ID: 0064_default_cancel_policy
Revises: 0063_reservation_currency
Create Date: 2026-09-26

0018 seeded a policy for the properties that existed then. Nothing seeded one
for any property created afterwards -- sign-up and the platform's create
tenant both skipped it -- so on those properties every cancellation quote was
refused with "no cancellation policy configured". That included the OTA's
own cancellations, which the channel webhook could not record and so retried
for ever while the room stayed off sale.

Sign-up now seeds one; this gives the same default to every property that has
no default policy. Approval is off, as at sign-up: the policy is a starting
point the hotel edits in onboarding, not a queue for managers who may not
exist yet.

Downgrade removes only rows this migration wrote, identified by their exact
text, and only while nothing refers to them.
"""
from __future__ import annotations

from alembic import op
from sqlalchemy import text

revision: str = "0064_default_cancel_policy"
down_revision: str | None = "0063_reservation_currency"
branch_labels = None
depends_on = None

_TEXT = ("Free cancellation up to 7 days before arrival. Cancellations within "
         "7 days are charged 1 night per room. No refund for no-shows.")


def upgrade() -> None:
    bind = op.get_bind()
    # Every tenant's properties, on purpose. Transaction-local.
    bind.execute(text("SELECT set_config('app.system', 'on', true)"))
    bind.execute(text(
        """
        INSERT INTO property.cancellation_policies
            (organization_id, property_id, name, free_until_days,
             penalty_nights, no_show_refund, requires_approval,
             approval_above, policy_text, is_default)
        SELECT p.organization_id, p.id, 'Moderate', 7, 1, false, false, 0,
               :txt, true
          FROM iam.properties p
         WHERE NOT EXISTS (
               SELECT 1 FROM property.cancellation_policies c
                WHERE c.property_id = p.id AND c.is_default)
        -- A property that already has a non-default policy called 'Moderate'
        -- keeps it; that is somebody's choice, not a gap.
        ON CONFLICT DO NOTHING
        """
    ), {"txt": _TEXT})


def downgrade() -> None:
    bind = op.get_bind()
    bind.execute(text("SELECT set_config('app.system', 'on', true)"))
    bind.execute(text(
        """
        DELETE FROM property.cancellation_policies c
         WHERE c.name = 'Moderate' AND c.policy_text = :txt
           AND c.version = 0
           AND NOT EXISTS (SELECT 1 FROM booking.reservations r
                            WHERE r.cancellation_policy_id = c.id)
        """
    ), {"txt": _TEXT})
