"""One vocabulary for what a no-show costs

Revision ID: 0033_no_show_basis
Revises: 0032_canonical_payment_methods
Create Date: 2026-09-17

There were three lists.

The No-Show Processing screen offers ``one_night_tax``, ``one_night``,
``full_stay`` and ``none``. The night audit defaulted to ``first_night`` --
a value the screen never produces. And this column's own check constraint
allowed only ``first_night`` or ``none``, which is why the audit branched on
nothing but ``none``: the other bases could not reach it. A property could
choose "Full Stay Amount" on the screen for a no-show processed by hand, and
had no way at all to say the same thing about one processed automatically.

So the column now speaks the same four words as the screen and as
``chirala_common.no_show``, and ``first_night`` is rewritten to the basis it
actually charged at -- one night plus tax.

The default changes too, and this is the part that bills real guests.
``first_night`` was what an unconfigured property got, and no property had
ever configured one, so every property was charging a night's room and tax
automatically for every missed arrival. ``find_no_shows`` states the principle
its sibling was breaking: "a background job should not take a guest's money
while nobody is watching." Existing rows are therefore left exactly as they
are -- a property that has ``first_night`` written down keeps charging, since
that is what it has been doing and stopping it silently would be its own kind
of surprise -- but NULL now means ``none``, and the column gets no server
default, so a new property charges nothing until somebody says otherwise.
"""

from __future__ import annotations

from alembic import op

revision: str = "0033_no_show_basis"
down_revision: str | None = "0032_canonical_payment_methods"
branch_labels = None
depends_on = None

#: Kept in step with BASES in chirala_common/no_show.py.
BASES = ("one_night_tax", "one_night", "full_stay", "none")


def upgrade() -> None:
    op.drop_constraint("ck_no_show_penalty", "night_audit_settings",
                       schema="finance", type_="check")

    # The old value meant one night's room plus tax. Rewritten rather than
    # dropped: a property that chose it chose to charge, and that decision
    # survives.
    op.execute(
        """
        UPDATE finance.night_audit_settings
           SET no_show_penalty = 'one_night_tax'
         WHERE no_show_penalty = 'first_night'
        """
    )

    bases = ", ".join(f"'{b}'" for b in BASES)
    op.create_check_constraint(
        "ck_no_show_penalty", "night_audit_settings",
        f"no_show_penalty IS NULL OR no_show_penalty IN ({bases})",
        schema="finance",
    )


def downgrade() -> None:
    op.drop_constraint("ck_no_show_penalty", "night_audit_settings",
                       schema="finance", type_="check")
    # Anything the old constraint could not hold becomes NULL rather than
    # blocking the downgrade. NULL is "not configured", which is a truthful
    # thing to say about a setting the old schema had no word for.
    op.execute(
        """
        UPDATE finance.night_audit_settings
           SET no_show_penalty = CASE
                 WHEN no_show_penalty IN ('one_night_tax', 'one_night')
                      THEN 'first_night'
                 WHEN no_show_penalty = 'none' THEN 'none'
                 ELSE NULL
               END
        """
    )
    op.create_check_constraint(
        "ck_no_show_penalty", "night_audit_settings",
        "no_show_penalty IS NULL"
        " OR no_show_penalty IN ('first_night', 'none')",
        schema="finance",
    )
