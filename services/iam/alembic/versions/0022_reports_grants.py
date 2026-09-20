"""Grant reports, so the ledger report can be opened

Revision ID: 0022_reports_grants
Revises: 0021_distribution_grants
Create Date: 2026-09-13

The sixth of these, and the story has not changed since 0021 told it about
distribution: all seven ``reports`` permission rows have existed since the
permission catalogue was seeded and are granted to **no role at all** —
including the role literally named Reports, and including Administrator.
Nobody noticed because nothing asked for them.

Something asks now. ``GET /reports/ledger`` is gated on ``reports:view``, and
without these grants it 403s for every user in every tenant — which is exactly
what it did the first time the screen was opened.

  Reports          view/export — the department exists to read and hand out
                   reports, and nothing more
  Resort Manager   view/export — runs the property and is asked about its
                   numbers
  Administrator    everything
  Property IT Administrator
                   everything

**Front Desk and Reservations are deliberately absent.** A ledger report is
not one guest's bill; it is the whole property's financial position and every
unsquared folio on it, including what other guests still owe. A receptionist
needs the folio in front of them, which ``payments:view`` already gives, and
has no business with the rest. Adding them later is one row each if a
deployment disagrees.

``create``, ``edit``, ``cancel`` and ``approve`` are granted to the two
administrator roles only because they come with `ALL`, not because a report
can be created or approved — reading is the only verb this resource really
has today. They are there so that whatever the reports module grows into, an
administrator is not locked out of it by this migration.

Roles are matched by name and skipped where a deployment lacks them.
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0022_reports_grants"
down_revision: str | None = "0021_distribution_grants"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

ALL = ("view", "create", "edit", "cancel", "approve", "export", "configure")
READ = ("view", "export")

GRANTS: dict[str, tuple[str, ...]] = {
    "Reports": READ,
    "Resort Manager": READ,
    "Administrator": ALL,
    "Property IT Administrator": ALL,
}


def upgrade() -> None:
    for role_name, actions in GRANTS.items():
        op.execute(
            f"""
            INSERT INTO iam.role_permissions
                (role_id, permission_id, record_scope)
            SELECT r.id, p.id, 'property'
            FROM iam.roles r
            JOIN iam.permissions p
              ON p.resource_code = 'reports'
             AND p.action_code IN ({", ".join(f"'{a}'" for a in actions)})
            WHERE r.name = '{role_name}'
            ON CONFLICT (role_id, permission_id) DO NOTHING
            """
        )


def downgrade() -> None:
    names = ", ".join(f"'{n}'" for n in GRANTS)
    op.execute(
        f"""
        DELETE FROM iam.role_permissions rp
        USING iam.roles r, iam.permissions p
        WHERE rp.role_id = r.id
          AND rp.permission_id = p.id
          AND p.resource_code = 'reports'
          AND r.name IN ({names})
        """
    )
