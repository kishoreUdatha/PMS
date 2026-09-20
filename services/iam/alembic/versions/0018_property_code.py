"""A property code that identifies a property across the whole platform.

The code was unique *per organisation*. On a single-tenant install that is
indistinguishable from unique; on a multi-tenant one it is a hole. Sign-in asks
for a property code and looks it up on its own — two tenants both choosing
"CBR" and the query picks whichever row comes back first, matching a person
against somebody else's property. Uniqueness has to hold wherever the lookup
happens, and the lookup happens before anyone knows which tenant is asking.

So the constraint moves to the code alone, and the code becomes six digits,
generated rather than chosen:

* **Digits, not letters.** It is read down a phone and typed by somebody who
  has been on shift for nine hours. There is no O/0 or I/1 to confuse, and no
  chance of a tenant picking a code that is rude, trademarked, or already the
  obvious choice for four other resorts.
* **Random inside 100000-999999.** Sequential codes would publish how many
  properties are on the platform and make a neighbour's code guessable by
  adding one. 900,000 values is ample, and a collision simply retries.
* **Generated, never asked for.** A chosen code cannot be guaranteed unique at
  the moment of choosing without showing one tenant something about another.

Existing codes are replaced, which changes what people type to sign in. There
is no way to both keep "CBR" and make codes globally unique, and the second
matters more than the first.

Revision ID: 0018_property_code
Revises: 0017_credentials
"""
from alembic import op

revision = "0018_property_code"
down_revision = "0017_credentials"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Give every property a six-digit code before the constraints demand one.
    # Looping rather than one UPDATE because each row needs its own draw, and
    # a draw that collides needs another.
    op.execute(
        """
        DO $$
        DECLARE
            prop  record;
            draft text;
        BEGIN
            FOR prop IN SELECT id FROM iam.properties LOOP
                LOOP
                    draft := lpad((100000 + floor(random() * 900000))::int::text,
                                  6, '0');
                    EXIT WHEN NOT EXISTS (
                        SELECT 1 FROM iam.properties WHERE code = draft
                    );
                END LOOP;
                UPDATE iam.properties SET code = draft WHERE id = prop.id;
            END LOOP;
        END $$;
        """
    )

    op.execute(
        "ALTER TABLE iam.properties DROP CONSTRAINT IF EXISTS uq_property_org_code"
    )
    op.execute(
        "ALTER TABLE iam.properties "
        "ADD CONSTRAINT uq_property_code UNIQUE (code)"
    )
    op.execute(
        "ALTER TABLE iam.properties ADD CONSTRAINT ck_property_code_shape "
        "CHECK (code ~ '^[0-9]{6}$')"
    )


def downgrade() -> None:
    op.execute(
        "ALTER TABLE iam.properties DROP CONSTRAINT IF EXISTS ck_property_code_shape"
    )
    op.execute(
        "ALTER TABLE iam.properties DROP CONSTRAINT IF EXISTS uq_property_code"
    )
    op.execute(
        "ALTER TABLE iam.properties "
        "ADD CONSTRAINT uq_property_org_code UNIQUE (organization_id, code)"
    )
