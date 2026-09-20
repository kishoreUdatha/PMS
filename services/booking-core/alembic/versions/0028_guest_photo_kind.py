"""Allow a guest photo among the check-in documents.

The desk photographs the guest at check-in as well as their ID -- it is how a
housekeeper or a night porter later confirms the person in the room is the
person who registered. The document store already holds ID scans; this only
widens the allowed kinds so a face photo has somewhere honest to live rather
than being filed as 'other'.

Revision ID: 0028_guest_photo
Revises: 0027_unit_rate
"""
from alembic import op

revision = "0028_guest_photo"
down_revision = "0027_unit_rate"
branch_labels = None
depends_on = None

_OLD = ("id_front", "id_back", "signature", "other")
_NEW = ("id_front", "id_back", "guest_photo", "signature", "other")


def _recreate(kinds: tuple[str, ...]) -> None:
    op.drop_constraint(
        "ck_guest_doc_kind", "guest_documents", schema="engagement", type_="check"
    )
    listed = ", ".join(f"'{k}'" for k in kinds)
    op.create_check_constraint(
        "ck_guest_doc_kind", "guest_documents",
        f"kind IN ({listed})", schema="engagement",
    )


def upgrade() -> None:
    _recreate(_NEW)


def downgrade() -> None:
    # Anything filed under the kind being withdrawn has to go somewhere the
    # constraint accepts, or the constraint cannot be recreated.
    op.execute(
        "UPDATE engagement.guest_documents SET kind = 'other' "
        "WHERE kind = 'guest_photo'"
    )
    _recreate(_OLD)
