"""Send what the user wrote, even when the channel already has it

Revision ID: 0066_outbox_written_fields
Revises: 0065_oos_rooms_off_sale
Create Date: 2026-09-27

The sync sends only values that differ from what the channel manager last
accepted. That is right for anything the PMS works out -- availability after
a booking, a price moved by a rule -- and it is what keeps a busy property
inside the rate limit. It is wrong for a value somebody typed. Channex's
certification (test 7) sets "Twin, Best Available Rate, 1-10 Nov: closed to
arrival, max stay 4, min stay 1"; min stay was already 1, so it was left out
of the request, and the check failed on a restriction the user had plainly
set. From the channel's side, an edit that silently loses one of its fields
looks like a PMS that dropped it.

So the outbox now records which fields a rate-plan calendar write actually
changed at the row level (``fields``, in the channel's names), and the sync
sends those for those nights whatever the channel holds. Everything else is
still diffed: a price edit does not resend the night's minimum stay.
"""
from __future__ import annotations

from alembic import op

revision: str = "0066_outbox_written_fields"
down_revision: str | None = "0065_oos_rooms_off_sale"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE distribution.ari_outbox "
               "ADD COLUMN IF NOT EXISTS fields varchar(40)[] NOT NULL DEFAULT '{}'")

    op.execute(
        """
        CREATE OR REPLACE FUNCTION distribution.ari_note(
            p_org uuid, p_prop uuid, p_scope text, p_room uuid, p_plan uuid,
            p_from date, p_to date, p_source text, p_fields varchar[])
        RETURNS void LANGUAGE sql AS $$
            INSERT INTO distribution.ari_outbox
                (organization_id, property_id, scope, room_type_id,
                 rate_plan_id, date_from, date_to, source, fields)
            SELECT p_org, p_prop, p_scope, p_room, p_plan,
                   LEAST(p_from, p_to), GREATEST(p_from, p_to), p_source,
                   COALESCE(p_fields, '{}')
            WHERE EXISTS (SELECT 1 FROM distribution.channel_manager_links l
                          WHERE l.property_id = p_prop
                            AND l.external_property_id IS NOT NULL)
        $$
        """
    )

    # The channel's names for what each column sets. One minimum stay here is
    # both of Channex's (arrival and through), as the sync sends it.
    op.execute(
        """
        CREATE OR REPLACE FUNCTION distribution.ari_plan_calendar_changed()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE
          r record;
          f varchar[] := '{}';
        BEGIN
          IF TG_OP = 'INSERT' THEN
            IF NEW.rate IS NOT NULL THEN f := f || '{rate}'::varchar[]; END IF;
            IF NEW.min_stay IS NOT NULL THEN
              f := f || '{min_stay_arrival,min_stay_through}'::varchar[]; END IF;
            IF NEW.max_stay IS NOT NULL THEN f := f || '{max_stay}'::varchar[]; END IF;
            IF NEW.closed_to_arrival IS NOT NULL THEN
              f := f || '{closed_to_arrival}'::varchar[]; END IF;
            IF NEW.closed_to_departure IS NOT NULL THEN
              f := f || '{closed_to_departure}'::varchar[]; END IF;
            IF NEW.stop_sell IS NOT NULL THEN f := f || '{stop_sell}'::varchar[]; END IF;
          ELSIF TG_OP = 'UPDATE' THEN
            IF NEW.rate IS DISTINCT FROM OLD.rate THEN f := f || '{rate}'::varchar[]; END IF;
            IF NEW.min_stay IS DISTINCT FROM OLD.min_stay THEN
              f := f || '{min_stay_arrival,min_stay_through}'::varchar[]; END IF;
            IF NEW.max_stay IS DISTINCT FROM OLD.max_stay THEN
              f := f || '{max_stay}'::varchar[]; END IF;
            IF NEW.closed_to_arrival IS DISTINCT FROM OLD.closed_to_arrival THEN
              f := f || '{closed_to_arrival}'::varchar[]; END IF;
            IF NEW.closed_to_departure IS DISTINCT FROM OLD.closed_to_departure THEN
              f := f || '{closed_to_departure}'::varchar[]; END IF;
            IF NEW.stop_sell IS DISTINCT FROM OLD.stop_sell THEN
              f := f || '{stop_sell}'::varchar[]; END IF;
          END IF;
          -- A delete hands the night back to the defaults. What changes as a
          -- result is found by the ordinary comparison; nothing is forced.
          r := CASE WHEN TG_OP = 'DELETE' THEN OLD ELSE NEW END;
          PERFORM distribution.ari_note(r.organization_id, r.property_id,
              'rates', NULL, r.rate_plan_id, r.stay_date, r.stay_date,
              'rate_plan_calendar', f);
          RETURN NULL;
        END $$
        """
    )


def downgrade() -> None:
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
    op.execute("DROP FUNCTION IF EXISTS distribution.ari_note("
               "uuid, uuid, text, uuid, uuid, date, date, text, varchar[])")
    op.execute("ALTER TABLE distribution.ari_outbox DROP COLUMN IF EXISTS fields")
