"""Put the booking engine in the plan catalogue, from Growth upward

Revision ID: 0034_booking_engine_in_plans
Revises: 0033_seed_message_templates
Create Date: 2026-09-15

``booking_engine`` appeared in no plan version at all -- not Starter, not
Growth, not Pro -- while properties were nonetheless being sold through it.
The commercial answer to "may this tenant take online bookings?" simply did
not exist, so the only way a hotel went on sale was somebody setting the
``iam.property_modules`` row by hand.

This is a repair of a seeding omission rather than a change of terms. Nobody
is charged more, nothing is withdrawn, and the module is added to what the
tier was already understood to include.

**Amending the current published version rather than cutting a new one.**
Normally a published version is what somebody bought at a price and should
not move. Two things make amendment the better answer here: the module was
never in *any* version, so there is no prior sale that included or excluded
it deliberately; and cutting v3 would leave the one existing subscriber on
v2 without the module, which is precisely the gap being closed. Only the
latest published version of each plan is touched, so the older ones stay as
the record of what they were.

**Existing subscriptions are backfilled.** Entitlements are written when a
plan is applied, so adding to the catalogue alone would grant nothing to
anybody already subscribed -- the tenant would pay for Growth and still not
be entitled, which is the bug with extra steps.

**This grants permission. It does not switch anything on.** No property's
``iam.property_modules`` row is touched here, by design: whether a hotel is
*allowed* to sell online and whether it is *ready* to are different
questions, owned by different people. A plan that silently put a property on
sale would take one off sale on the next downgrade, with guests mid-booking.
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0034_booking_engine_in_plans"
down_revision: str | None = "0033_seed_message_templates"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

MODULE = "booking_engine"

#: Growth and above. Starter is deliberately excluded -- it already excludes
#: `distribution`, and online booking belongs with the tier that sells.
TIERS = ("growth", "pro")


def upgrade() -> None:
    tiers = ", ".join("'%s'" % t for t in TIERS)

    # The latest published version of each named plan, and only that one.
    op.execute(
        f"""
        INSERT INTO billing.plan_version_modules (plan_version_id, module_code)
        SELECT latest.id, '{MODULE}'
        FROM (
            SELECT DISTINCT ON (pv.plan_id) pv.id, pv.plan_id
            FROM billing.plan_versions pv
            JOIN billing.plans pl ON pl.id = pv.plan_id
            WHERE pl.code IN ({tiers})
              AND pv.status = 'published'
            ORDER BY pv.plan_id, pv.version_no DESC
        ) latest
        WHERE NOT EXISTS (
            SELECT 1 FROM billing.plan_version_modules m
             WHERE m.plan_version_id = latest.id
               AND m.module_code = '{MODULE}')
        """
    )

    # Anybody already on one of those versions is entitled from today. `source
    # = 'plan'` matches what apply-change writes, so a later plan change
    # replaces this row rather than leaving two accounts of the same fact.
    op.execute(
        f"""
        INSERT INTO billing.entitlements
            (subscription_id, kind, code, value, source, effective_from, note)
        SELECT s.id, 'module', '{MODULE}', NULL, 'plan', CURRENT_DATE,
               'Backfilled: the tier always included this, the catalogue did '
               || 'not say so.'
        FROM billing.subscriptions s
        JOIN billing.plan_version_modules m ON m.plan_version_id = s.plan_version_id
        WHERE m.module_code = '{MODULE}'
          AND s.status IN ('trialing', 'active', 'past_due', 'grace')
        ON CONFLICT (subscription_id, kind, code) DO NOTHING
        """
    )

    # A migration that silently did nothing would leave the same gap with a
    # version number on it.
    granted = op.get_bind().exec_driver_sql(
        f"""
        SELECT count(*) FROM billing.plan_version_modules m
        JOIN billing.plan_versions pv ON pv.id = m.plan_version_id
        JOIN billing.plans pl ON pl.id = pv.plan_id
        WHERE m.module_code = '{MODULE}' AND pl.code IN ({tiers})
        """
    ).scalar()
    if not granted:
        raise RuntimeError(
            "no plan version gained %s; the catalogue may have been renamed"
            % MODULE)

    # Starter must not have picked it up.
    leaked = op.get_bind().exec_driver_sql(
        f"""
        SELECT count(*) FROM billing.plan_version_modules m
        JOIN billing.plan_versions pv ON pv.id = m.plan_version_id
        JOIN billing.plans pl ON pl.id = pv.plan_id
        WHERE m.module_code = '{MODULE}' AND pl.code NOT IN ({tiers})
        """
    ).scalar()
    if leaked:
        raise RuntimeError(
            "%s reached %d plan version(s) outside %s" % (MODULE, leaked, TIERS))

    op.execute(
        """
        INSERT INTO iam.audit_events
            (id, actor_subject, action, entity_type, entity_id, reason,
             redacted_after, occurred_at)
        VALUES (gen_random_uuid(), 'migration:0034',
                'billing.catalogue.changed', 'plan_module', 'booking_engine',
                'Growth and above now include the online booking engine. '
                'Permission only -- no property was switched on.',
                jsonb_build_object('tiers', CAST('["growth","pro"]' AS jsonb)),
                now())
        """
    )


def downgrade() -> None:
    op.execute(
        f"DELETE FROM billing.entitlements "
        f"WHERE kind = 'module' AND code = '{MODULE}'"
    )
    op.execute(
        f"DELETE FROM billing.plan_version_modules WHERE module_code = '{MODULE}'"
    )
