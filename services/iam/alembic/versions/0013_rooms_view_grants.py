"""Grant rooms.view so operational roles can see the estate

Revision ID: 0013_rooms_view_grants
Revises: 0012_approval_decide_grants
Create Date: 2026-09-10

The fifth of these, and the worst so far. ``rooms.view`` is held by exactly
two roles — Administrator and Property IT Administrator — while **37 read
endpoints** are guarded by it. Every other role is locked out of:

  * the Room Rack and the Reservation Calendar
  * room detail, status history, assignable rooms
  * the rate calendar, rate plans, rate rules, meal plans
  * packages, promo codes, add-ons, amenities, buildings and floors
  * room blocks

Proven rather than assumed: ``anita.reddy``, who holds Front Desk *and*
Reservations, gets ``403 Permission denied: rooms.view`` on ``GET /rooms``
while ``GET /reservations`` returns 200. A front desk that can list bookings
but cannot see a single room is not a front desk.

As before, the code was right and the seed was incomplete — the permission
rows have existed since the first IAM seed and were granted to nobody who
needs them.

**View only, deliberately.** ``rooms`` has become a catch-all: ``rooms.edit``
covers 27 endpoints spanning room status, room blocks, the rate calendar,
rate plans, packages and promo codes. Handing that to Housekeeping so they can
mark a room clean would also hand them pricing. They do not need it — a
housekeeping task drives ``operations.room_condition`` directly under
``housekeeping.edit``, so the department closes its own loop already.

``rooms.create``, ``rooms.edit`` and ``rooms.configure`` therefore stay with
the two administrator roles until the ``rates`` resource is real. ``rates``
has seven permission rows, zero grants and zero references in code; every rate
screen is gated on ``rooms`` instead. Splitting them is a separate change with
its own migration, not something to smuggle in here.

  Front Desk      view — sells rooms, must see which exist and which are free
  Reservations    view — same, for the booking calendar
  Housekeeping    view — needs the rack; already edits condition via its tasks
  Resort Manager  view — senior operational role, currently blind to the estate

Roles are matched by name and skipped where a deployment lacks them.
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0013_rooms_view_grants"
down_revision: str | None = "0012_approval_decide_grants"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

ROLES = ("Front Desk", "Reservations", "Housekeeping", "Resort Manager")


def upgrade() -> None:
    names = ", ".join(f"'{n}'" for n in ROLES)
    op.execute(
        f"""
        INSERT INTO iam.role_permissions
            (role_id, permission_id, record_scope)
        SELECT r.id, p.id, 'property'
        FROM iam.roles r
        JOIN iam.permissions p
          ON p.resource_code = 'rooms'
         AND p.action_code = 'view'
        WHERE r.name IN ({names})
        ON CONFLICT (role_id, permission_id) DO NOTHING
        """
    )


def downgrade() -> None:
    names = ", ".join(f"'{n}'" for n in ROLES)
    op.execute(
        f"""
        DELETE FROM iam.role_permissions rp
        USING iam.roles r, iam.permissions p
        WHERE rp.role_id = r.id
          AND rp.permission_id = p.id
          AND p.resource_code = 'rooms'
          AND p.action_code = 'view'
          AND r.name IN ({names})
        """
    )
