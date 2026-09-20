"""Put back the mapping uniqueness that 0037 dropped with its column

Revision ID: 0038_restore_mapping_uniqueness
Revises: 0037_channel_manager_link
Create Date: 2026-09-13

0037 moved the mappings from the partner connection to the property's link by
adding ``link_id`` and dropping ``connection_id``. Both unique constraints were
defined over ``connection_id``, so Postgres dropped them along with it — and
nothing put them back.

The tables have been running without them since. That is worse than it sounds:

* Two of our room types could point at one of theirs. An arriving booking then
  has two possible answers, and nobody finds out until a guest is standing at
  the desk beside a room that was sold twice.
* The upsert in the provisioning code names ``ON CONFLICT (link_id,
  room_type_id)``, which requires a matching constraint. Without it every
  provisioning run fails with "there is no unique or exclusion constraint
  matching the ON CONFLICT specification" — which is how this was found.

Both directions, as before. One of ours maps to one of theirs, and one of
theirs to one of ours.
"""

from __future__ import annotations

from alembic import op

revision = "0038_restore_mapping_uniqueness"
down_revision = "0037_channel_manager_link"
branch_labels = None
depends_on = None

_TABLES = (
    ("channel_room_mappings", "room_type_id", "room"),
    ("channel_rate_mappings", "rate_plan_id", "rate"),
)


def upgrade() -> None:
    conn = op.get_bind()
    for table, col, label in _TABLES:
        # Any duplicate that crept in while the constraints were absent has to
        # be reported, not silently deleted: which of two mappings is the right
        # one is a question only the hotel can answer.
        for cols, what in ((f"link_id, {col}", "ours"),
                           ("link_id, external_id", "theirs")):
            dupes = conn.exec_driver_sql(
                f"SELECT {cols}, count(*) FROM distribution.{table} "
                f"GROUP BY {cols} HAVING count(*) > 1"
            ).fetchall()
            if dupes:
                raise RuntimeError(
                    f"{table} has {len(dupes)} duplicate mapping(s) on "
                    f"({cols}). Two of {what} point at the same counterpart, "
                    f"so an arriving booking is ambiguous. Decide which is "
                    f"correct and remove the others before applying this."
                )

        op.create_unique_constraint(f"uq_{label}_map_ours", table,
                                    ["link_id", col], schema="distribution")
        op.create_unique_constraint(f"uq_{label}_map_theirs", table,
                                    ["link_id", "external_id"],
                                    schema="distribution")


def downgrade() -> None:
    for table, _col, label in _TABLES:
        op.drop_constraint(f"uq_{label}_map_theirs", table,
                           schema="distribution", type_="unique")
        op.drop_constraint(f"uq_{label}_map_ours", table,
                           schema="distribution", type_="unique")
