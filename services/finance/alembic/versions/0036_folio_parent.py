"""Master and child folios

Revision ID: 0036_folio_parent
Revises: 0035_folio_billing_details
Create Date: 2026-09-17

A booking taken in a company's name is two bills, not one. The company agreed
to the room and nothing else; the food, the bar and the spa are the guest's,
and the guest wants those on their own GSTIN because their employer is not
paying for dinner. Two payers, two tax identities, two documents -- and one
stay.

The folios to carry that already existed. What did not exist was the statement
that they belong together and which of them is the principal one, so a booking
with two folios was two unrelated accounts that happened to share a
reservation_id. Nothing said the company's folio was the master and the guest's
extras folio hung off it, which is the fact the desk works from and the fact a
printed bill needs in order to reference the other one.

``parent_folio_id``
    Null on a master. Set, on a child, to the folio it was opened from. One
    level only, deliberately: a child of a child is a hierarchy nobody at a
    desk can hold in their head, and there is no billing arrangement that needs
    one. The application enforces the depth; this column only records the link.

    ON DELETE SET NULL rather than CASCADE. Deleting a master must never take
    the child's charges with it -- those are a different party's money and a
    different tax document. The child becoming a master in its own right is the
    correct outcome of losing its parent.

Every folio that exists becomes a master, which is what each of them already
was in effect.
"""
from alembic import op
import sqlalchemy as sa

revision = "0036_folio_parent"
down_revision = "0035_folio_billing_details"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "folios",
        sa.Column("parent_folio_id", sa.dialects.postgresql.UUID(as_uuid=True),
                  nullable=True),
        schema="finance",
    )
    op.create_foreign_key(
        "fk_folio_parent", "folios", "folios",
        ["parent_folio_id"], ["id"],
        source_schema="finance", referent_schema="finance",
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_folio_parent", "folios", ["parent_folio_id"], schema="finance",
    )


def downgrade() -> None:
    op.drop_index("ix_folio_parent", table_name="folios", schema="finance")
    op.drop_constraint("fk_folio_parent", "folios", schema="finance",
                       type_="foreignkey")
    op.drop_column("folios", "parent_folio_id", schema="finance")
