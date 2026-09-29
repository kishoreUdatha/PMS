"""Record every availability, rate and restriction change as it is made

Revision ID: 0060_ari_outbox
Revises: 0059_channel_sync
Create Date: 2026-09-26

Two things the channel manager's certification requires that the sync did not
have:

**An outbox.** Changes were found by comparing 500 days of computed values
with what was last sent, every twenty seconds -- a polling loop over the
database, which the channel manager rejects outright. Now every write that can
move what a channel sells records *what* moved, in the same transaction as the
write: ``distribution.ari_outbox`` gets a row naming the room or rate plan and
the dates. A worker drains it, batches it and sends it. A save that rolls back
leaves no row; a save that commits cannot be missed.

The rows are written by triggers on the tables themselves rather than by each
save handler. Availability alone moves from bookings, holds expiring, room
blocks, group blocks, check-outs and night audits -- a dozen code paths -- and
a hook in each is a dozen chances to forget one. A trigger is one place, and it
fires for every path that exists now and every one added later.

**Prices and restrictions per rate plan and date.** A night's price lived on
the room type, and a rate plan's price was the room's plus the plan's
adjustment, so Best Available and Bed & Breakfast could not be priced
independently for one night, nor could one of them be closed to arrival while
the other stayed open. ``property.rate_plan_calendar_days`` holds those
per-plan, per-night overrides; any field left NULL falls through to the room
calendar, the rules and the plan's defaults as before.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision: str = "0060_ari_outbox"
down_revision: str | None = "0059_channel_sync"
branch_labels = None
depends_on = None

RUNTIME_ROLE = "pms_app"

#: How far ahead a change to a whole plan or room (its default price) reaches.
#: The worker clips to the property's own window; this only has to cover it.
HORIZON = "interval '510 days'"


def _rls(table: str) -> None:
    op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
    op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {table}")
    op.execute(
        f"""
        CREATE POLICY tenant_isolation ON {table}
        USING (tenancy.org_visible(organization_id))
        WITH CHECK (tenancy.org_visible(organization_id))
        """
    )
    op.execute(
        f"""
        DO $$
        BEGIN
          IF EXISTS (SELECT FROM pg_roles WHERE rolname = '{RUNTIME_ROLE}') THEN
            GRANT SELECT, INSERT, UPDATE, DELETE ON {table} TO {RUNTIME_ROLE};
          END IF;
        END
        $$
        """
    )


def upgrade() -> None:
    # ------------------------------------------------------------ outbox --
    op.create_table(
        "ari_outbox",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("organization_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("property_id", pg.UUID(as_uuid=True), nullable=False),
        # 'availability' goes to /availability; 'rates' (price and stay rules)
        # to /restrictions.
        sa.Column("scope", sa.String(20), nullable=False),
        # Narrowest known: a rate plan, else a room type (every plan on it),
        # else neither (every plan at the property -- a rule for all plans).
        sa.Column("room_type_id", pg.UUID(as_uuid=True), nullable=True),
        sa.Column("rate_plan_id", pg.UUID(as_uuid=True), nullable=True),
        sa.Column("date_from", sa.Date(), nullable=False),
        sa.Column("date_to", sa.Date(), nullable=False),
        sa.Column("source", sa.String(60), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("clock_timestamp()")),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("last_error", sa.String(400), nullable=True),
        sa.CheckConstraint("scope IN ('availability', 'rates')",
                           name="ck_ari_outbox_scope"),
        schema="distribution",
    )
    op.create_index("ix_ari_outbox_property", "ari_outbox",
                    ["property_id", "next_attempt_at"], schema="distribution")
    _rls("distribution.ari_outbox")

    # ------------------------------------------- per-plan night overrides --
    op.create_table(
        "rate_plan_calendar_days",
        sa.Column("organization_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("property_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("rate_plan_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("stay_date", sa.Date(), nullable=False),
        sa.Column("rate", sa.Numeric(12, 2), nullable=True),
        sa.Column("min_stay", sa.Integer(), nullable=True),
        sa.Column("max_stay", sa.Integer(), nullable=True),
        sa.Column("closed_to_arrival", sa.Boolean(), nullable=True),
        sa.Column("closed_to_departure", sa.Boolean(), nullable=True),
        sa.Column("stop_sell", sa.Boolean(), nullable=True),
        sa.Column("updated_by", pg.UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.PrimaryKeyConstraint("rate_plan_id", "stay_date"),
        sa.ForeignKeyConstraint(["rate_plan_id"], ["property.rate_plans.id"],
                                ondelete="CASCADE"),
        sa.CheckConstraint("rate IS NULL OR rate >= 0", name="ck_rpcd_rate"),
        sa.CheckConstraint("min_stay IS NULL OR min_stay >= 1",
                           name="ck_rpcd_min_stay"),
        sa.CheckConstraint("max_stay IS NULL OR max_stay >= 0",
                           name="ck_rpcd_max_stay"),
        schema="property",
    )
    op.create_index("ix_rpcd_property_date", "rate_plan_calendar_days",
                    ["property_id", "stay_date"], schema="property")
    _rls("property.rate_plan_calendar_days")

    # ---------------------------------------------------------- triggers --
    # One function writes the row; the per-table functions only say what to
    # write. SECURITY INVOKER: the row is written as whoever made the change,
    # under the same tenant binding, so it cannot land in another tenant.
    op.execute(
        """
        CREATE OR REPLACE FUNCTION distribution.ari_note(
            p_org uuid, p_prop uuid, p_scope text, p_room uuid, p_plan uuid,
            p_from date, p_to date, p_source text) RETURNS void
        LANGUAGE sql AS $$
            INSERT INTO distribution.ari_outbox
                (organization_id, property_id, scope, room_type_id,
                 rate_plan_id, date_from, date_to, source)
            SELECT p_org, p_prop, p_scope, p_room, p_plan,
                   LEAST(p_from, p_to), GREATEST(p_from, p_to), p_source
            -- Only properties that sell through a channel manager. Every
            -- other property's bookings and rate edits cost nothing.
            WHERE EXISTS (SELECT 1 FROM distribution.channel_manager_links l
                          WHERE l.property_id = p_prop
                            AND l.external_property_id IS NOT NULL)
        $$
        """
    )

    op.execute(
        """
        CREATE OR REPLACE FUNCTION distribution.ari_inventory_changed()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          IF TG_OP = 'UPDATE' AND
             (NEW.physical_capacity, NEW.out_of_service, NEW.held_units,
              NEW.reserved_units, NEW.allotment_units)
             IS NOT DISTINCT FROM
             (OLD.physical_capacity, OLD.out_of_service, OLD.held_units,
              OLD.reserved_units, OLD.allotment_units) THEN
            RETURN NEW;
          END IF;
          PERFORM distribution.ari_note(NEW.organization_id, NEW.property_id,
              'availability', NEW.room_type_id, NULL, NEW.stay_date,
              NEW.stay_date, 'inventory');
          RETURN NEW;
        END $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_ari_inventory
        AFTER INSERT OR UPDATE ON booking.room_type_inventory_days
        FOR EACH ROW EXECUTE FUNCTION distribution.ari_inventory_changed()
        """
    )

    op.execute(
        """
        CREATE OR REPLACE FUNCTION distribution.ari_room_calendar_changed()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE r record;
        BEGIN
          r := CASE WHEN TG_OP = 'DELETE' THEN OLD ELSE NEW END;
          PERFORM distribution.ari_note(r.organization_id, r.property_id,
              'rates', r.room_type_id, NULL, r.stay_date, r.stay_date,
              'rate_calendar');
          RETURN NULL;
        END $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_ari_room_calendar
        AFTER INSERT OR UPDATE OR DELETE ON property.rate_calendar_days
        FOR EACH ROW EXECUTE FUNCTION distribution.ari_room_calendar_changed()
        """
    )

    op.execute(
        """
        CREATE OR REPLACE FUNCTION distribution.ari_plan_calendar_changed()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE r record;
        BEGIN
          r := CASE WHEN TG_OP = 'DELETE' THEN OLD ELSE NEW END;
          PERFORM distribution.ari_note(r.organization_id, r.property_id,
              'rates', NULL, r.rate_plan_id, r.stay_date, r.stay_date,
              'rate_plan_calendar');
          RETURN NULL;
        END $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_ari_plan_calendar
        AFTER INSERT OR UPDATE OR DELETE ON property.rate_plan_calendar_days
        FOR EACH ROW EXECUTE FUNCTION distribution.ari_plan_calendar_changed()
        """
    )

    # A rule covers a range for all plans, or for the plans/rooms linked to
    # it; the worker narrows it. Both the old and the new range are noted: a
    # rule moved from March to April changes both.
    op.execute(
        """
        CREATE OR REPLACE FUNCTION distribution.ari_rule_changed()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          IF TG_OP IN ('UPDATE', 'DELETE') THEN
            PERFORM distribution.ari_note(OLD.organization_id, OLD.property_id,
                'rates', NULL, NULL, OLD.date_from, OLD.date_to, 'rate_rule');
          END IF;
          IF TG_OP IN ('INSERT', 'UPDATE') AND
             (TG_OP = 'INSERT' OR NEW.date_from <> OLD.date_from
              OR NEW.date_to <> OLD.date_to) THEN
            PERFORM distribution.ari_note(NEW.organization_id, NEW.property_id,
                'rates', NULL, NULL, NEW.date_from, NEW.date_to, 'rate_rule');
          END IF;
          RETURN NULL;
        END $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_ari_rule
        AFTER INSERT OR UPDATE OR DELETE ON property.rate_rules
        FOR EACH ROW EXECUTE FUNCTION distribution.ari_rule_changed()
        """
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION distribution.ari_rule_link_changed()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE r record; rule record;
        BEGIN
          r := CASE WHEN TG_OP = 'DELETE' THEN OLD ELSE NEW END;
          SELECT organization_id, property_id, date_from, date_to INTO rule
            FROM property.rate_rules WHERE id = r.rate_rule_id;
          IF FOUND THEN
            PERFORM distribution.ari_note(rule.organization_id,
                rule.property_id, 'rates', NULL, NULL, rule.date_from,
                rule.date_to, 'rate_rule');
          END IF;
          RETURN NULL;
        END $$
        """
    )
    for t in ("rate_rule_rate_plans", "rate_rule_room_types"):
        op.execute(
            f"""
            CREATE TRIGGER trg_ari_{t}
            AFTER INSERT OR DELETE ON property.{t}
            FOR EACH ROW EXECUTE FUNCTION distribution.ari_rule_link_changed()
            """
        )

    # A plan's or room's default price reaches every night that has no
    # override of its own.
    op.execute(
        f"""
        CREATE OR REPLACE FUNCTION distribution.ari_plan_changed()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          IF (NEW.adjustment_direction, NEW.adjustment_type,
              NEW.adjustment_value, NEW.flat_rate, NEW.min_stay, NEW.status)
             IS DISTINCT FROM
             (OLD.adjustment_direction, OLD.adjustment_type,
              OLD.adjustment_value, OLD.flat_rate, OLD.min_stay, OLD.status)
          THEN
            PERFORM distribution.ari_note(NEW.organization_id,
                NEW.property_id, 'rates', NULL, NEW.id, CURRENT_DATE - 1,
                (CURRENT_DATE + {HORIZON})::date, 'rate_plan');
          END IF;
          RETURN NULL;
        END $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_ari_plan
        AFTER UPDATE ON property.rate_plans
        FOR EACH ROW EXECUTE FUNCTION distribution.ari_plan_changed()
        """
    )
    op.execute(
        f"""
        CREATE OR REPLACE FUNCTION distribution.ari_room_type_changed()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          IF NEW.base_rate IS DISTINCT FROM OLD.base_rate THEN
            PERFORM distribution.ari_note(NEW.organization_id,
                NEW.property_id, 'rates', NEW.id, NULL, CURRENT_DATE - 1,
                (CURRENT_DATE + {HORIZON})::date, 'room_type');
          END IF;
          RETURN NULL;
        END $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_ari_room_type
        AFTER UPDATE ON property.room_types
        FOR EACH ROW EXECUTE FUNCTION distribution.ari_room_type_changed()
        """
    )


def downgrade() -> None:
    for trg, tbl in [("trg_ari_room_type", "property.room_types"),
                     ("trg_ari_plan", "property.rate_plans"),
                     ("trg_ari_rate_rule_room_types", "property.rate_rule_room_types"),
                     ("trg_ari_rate_rule_rate_plans", "property.rate_rule_rate_plans"),
                     ("trg_ari_rule", "property.rate_rules"),
                     ("trg_ari_plan_calendar", "property.rate_plan_calendar_days"),
                     ("trg_ari_room_calendar", "property.rate_calendar_days"),
                     ("trg_ari_inventory", "booking.room_type_inventory_days")]:
        op.execute(f"DROP TRIGGER IF EXISTS {trg} ON {tbl}")
    for fn in ("ari_room_type_changed", "ari_plan_changed",
               "ari_rule_link_changed", "ari_rule_changed",
               "ari_plan_calendar_changed", "ari_room_calendar_changed",
               "ari_inventory_changed"):
        op.execute(f"DROP FUNCTION IF EXISTS distribution.{fn}()")
    op.execute("DROP FUNCTION IF EXISTS distribution.ari_note(uuid, uuid, text, "
               "uuid, uuid, date, date, text)")
    op.drop_table("rate_plan_calendar_days", schema="property")
    op.drop_table("ari_outbox", schema="distribution")
