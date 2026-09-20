"""Six-digit codes for verifying an address before an account exists.

Sign-up asks for an email and, until now, took it on trust. That is not only a
typo risk: the address becomes the username, it is where the welcome link and
every password reset goes, and a property set up against an address nobody
owns is a property nobody can get back into.

The row cannot hang off ``iam.users`` because the user does not exist yet —
verification happens *before* the account. So it is keyed by the address, and
swept once used or expired.

Only a hash of the code is stored. Six digits is a small space and hashing it
is not real protection on its own; the defence is the short expiry and the
attempt counter beside it. The hash simply means a glance at the table does
not hand over live codes.

Revision ID: 0020_email_verification
Revises: 0019_user_phone
"""
from alembic import op

revision = "0020_email_verification"
down_revision = "0019_user_phone"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE iam.email_verifications (
            id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            email       varchar(254) NOT NULL,
            code_hash   varchar(64)  NOT NULL,
            expires_at  timestamptz  NOT NULL,
            verified_at timestamptz,
            -- Guessing six digits is 900,000 tries; five is plenty of rope
            -- for somebody mistyping their own code.
            attempts    integer      NOT NULL DEFAULT 0,
            created_at  timestamptz  NOT NULL DEFAULT now(),
            CONSTRAINT ck_email_verif_attempts CHECK (attempts >= 0)
        )
        """
    )
    op.execute(
        "CREATE INDEX ix_email_verifications_email "
        "ON iam.email_verifications (lower(email), created_at DESC)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS iam.email_verifications")
