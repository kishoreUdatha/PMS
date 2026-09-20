"""Price per tenant, not per property, and match the published catalogue

Revision ID: 0031_per_tenant_pricing
Revises: 0030_tenant_code
Create Date: 2026-09-14

0027 priced plans **per property** and said so in its docstring, reasoning from
the design pack's sample figures. That reasoning was wrong, and the pack's own
artboard says so plainly: screen 11 labels every price "per tenant / month ·
before tax", and its Catalog controls panel reads "Pricing basis — Per tenant
/ month".

The sample data confirms it rather than contradicting it. Blue Way runs two
properties on Growth for 9,999; Sea Breeze also runs two on Growth for 9,999;
Coral Coast runs three on Pro for 19,999; Palm Grove runs one on Starter for
4,999. The figure does not move with the property count, and the six rows sum
to the 44,996 the overview reports. A property count is a **limit** the plan
imposes, which is what ``plan_version_limits`` already holds -- it was never a
multiplier.

So ``subscriptions.quantity`` stops meaning "properties billed" and becomes 1.
It is kept rather than dropped: a future seat-based or room-based plan would
want it back, and a column that exists and reads 1 is easier to explain than a
schema change under an invoice table.

The catalogue is also brought in line with the pack: Starter / Growth / Pro at
4,999 / 9,999 / 19,999 with the limits its cards list. The plan seeded as
"scale" is renamed "pro", which is safe because nothing is subscribed to it.

Existing subscriptions are repriced to their plan's new published version.
That is a **commercial change** and is deliberately loud: it is recorded in
the audit log by the migration itself, because a tenant's bill changing
without a trace is exactly the thing the rest of this schema is built to
prevent.
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0031_per_tenant_pricing"
down_revision: str | None = "0030_tenant_code"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: code, name, summary, order, amount, trial, limits, modules
PLANS = [
    ("starter", "Starter", "For a single property.", 1, "4999.00", 14,
     {"properties": 1, "rooms": 30, "active_users": 10},
     ("front_desk", "reservations", "housekeeping", "guests", "reports")),
    ("growth", "Growth", "For growing resort groups.", 2, "9999.00", 14,
     {"properties": 3, "rooms": 100, "active_users": 30},
     ("front_desk", "reservations", "housekeeping", "guests", "reports",
      "distribution", "rates", "pos")),
    ("pro", "Pro", "For larger property groups.", 3, "19999.00", 0,
     {"properties": 10, "rooms": 300, "active_users": 100},
     ("front_desk", "reservations", "housekeeping", "guests", "reports",
      "distribution", "rates", "pos", "ai_center", "administration")),
]


def _q(v: str) -> str:
    return "'" + v.replace("'", "''") + "'"


def upgrade() -> None:
    # The plan seeded as "scale" is the pack's "Pro". Renamed rather than
    # replaced so any subscription pointing at it keeps working.
    op.execute(
        "UPDATE billing.plans SET code = 'pro', name = 'Pro', "
        "updated_at = now() WHERE code = 'scale'"
    )

    for code, name, summary, order, amount, trial, limits, modules in PLANS:
        op.execute(
            f"""
            INSERT INTO billing.plans (code, name, summary, sort_order)
            VALUES ({_q(code)}, {_q(name)}, {_q(summary)}, {order})
            ON CONFLICT (code) DO UPDATE
               SET name = EXCLUDED.name, summary = EXCLUDED.summary,
                   sort_order = EXCLUDED.sort_order, updated_at = now()
            """
        )
        # A new published version rather than an edit: the whole point of
        # versioning is that a price change is a new row, and this migration
        # must not be the one thing that breaks that rule.
        op.execute(
            f"""
            INSERT INTO billing.plan_versions
                (plan_id, version_no, amount, currency, billing_cycle,
                 trial_days, status, published_at)
            SELECT p.id,
                   coalesce((SELECT max(v.version_no) FROM billing.plan_versions v
                              WHERE v.plan_id = p.id), 0) + 1,
                   {amount}, 'INR', 'monthly', {trial}, 'published', now()
            FROM billing.plans p WHERE p.code = {_q(code)}
            """
        )
        for metric, value in limits.items():
            op.execute(
                f"""
                INSERT INTO billing.plan_version_limits
                    (plan_version_id, metric_code, limit_value)
                SELECT v.id, {_q(metric)}, {value}
                FROM billing.plan_versions v
                JOIN billing.plans p ON p.id = v.plan_id
                WHERE p.code = {_q(code)}
                  AND v.version_no = (SELECT max(version_no)
                                        FROM billing.plan_versions
                                       WHERE plan_id = p.id)
                ON CONFLICT DO NOTHING
                """
            )
        for module in modules:
            op.execute(
                f"""
                INSERT INTO billing.plan_version_modules
                    (plan_version_id, module_code)
                SELECT v.id, {_q(module)}
                FROM billing.plan_versions v
                JOIN billing.plans p ON p.id = v.plan_id
                WHERE p.code = {_q(code)}
                  AND v.version_no = (SELECT max(version_no)
                                        FROM billing.plan_versions
                                       WHERE plan_id = p.id)
                ON CONFLICT DO NOTHING
                """
            )

    # Quantity is no longer a property count.
    op.execute(
        "UPDATE billing.subscriptions SET quantity = 1, updated_at = now(), "
        "version = version + 1 WHERE quantity <> 1"
    )

    # Move live subscriptions onto their plan's newest published version and
    # re-snapshot the agreed amount, so the contract and the catalogue agree.
    op.execute(
        """
        WITH latest AS (
            SELECT DISTINCT ON (v.plan_id) v.plan_id, v.id, v.amount
            FROM billing.plan_versions v
            WHERE v.status = 'published'
            ORDER BY v.plan_id, v.version_no DESC
        )
        UPDATE billing.subscriptions s
           SET plan_version_id = latest.id, amount = latest.amount,
               updated_at = now(), version = s.version + 1
          FROM billing.plan_versions old, latest
         WHERE old.id = s.plan_version_id
           AND latest.plan_id = old.plan_id
           AND s.status IN ('trialing', 'active', 'past_due', 'grace')
           AND s.plan_version_id <> latest.id
        """
    )

    # Loud, not silent: a bill that changed leaves a row saying so.
    op.execute(
        """
        INSERT INTO iam.audit_events
            (id, organization_id, actor_subject, action, entity_type,
             entity_id, reason, redacted_after, occurred_at)
        SELECT gen_random_uuid(), s.organization_id, 'migration',
               'billing.subscription.repriced', 'subscription', s.id::text,
               'Pricing basis corrected to per tenant per month (0031)',
               jsonb_build_object('amount', s.amount::text, 'quantity', 1),
               now()
        FROM billing.subscriptions s
        WHERE s.status IN ('trialing', 'active', 'past_due', 'grace')
        """
    )


def downgrade() -> None:
    # The versions this added stay: unpublishing a price somebody may have
    # been sold on is worse than leaving a superseded row in the catalogue.
    op.execute(
        "UPDATE billing.plans SET code = 'scale', name = 'Scale' "
        "WHERE code = 'pro'"
    )
