"""Passwords, and the one-time links that set them.

Until now ``/auth/login`` took a subject and issued a session for it. Nothing
was checked, because there was nothing to check against: ``iam.users`` holds an
identity, not a credential. This adds the credential.

Three pieces:

``iam.users.email``
    The thing a person actually types to sign in. It lived only on the
    invitation, so a user who had been created from one could not be found by
    the address they were invited at. Backfilled from those invitations.

``iam.user_credentials``
    A hash, never a password, in its own table rather than widened onto
    ``users`` — a credential has a different lifetime from an identity and a
    much narrower audience for SELECT. Also carries the lockout counters,
    because throttling belongs next to the thing being guessed at.

``iam.password_tokens``
    Single-use, expiring links for setting or resetting a password. Only a
    hash of the token is stored: a leaked database should not hand over
    working links, exactly as it should not hand over working passwords.

Revision ID: 0017_credentials
Revises: 0016_advance_policy
"""
import sqlalchemy as sa
from alembic import op

revision = "0017_credentials"
down_revision = "0016_advance_policy"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "users", sa.Column("email", sa.String(length=254), nullable=True),
        schema="iam",
    )
    # A person is identified by their address within one organisation; the same
    # address may legitimately belong to different people in different orgs.
    op.execute(
        """
        UPDATE iam.users u
        SET email = i.email
        FROM iam.invitations i
        WHERE i.created_user_id = u.id AND u.email IS NULL
        """
    )

    op.execute(
        """
        CREATE TABLE iam.user_credentials (
            user_id         uuid PRIMARY KEY
                            REFERENCES iam.users(id) ON DELETE CASCADE,
            -- scrypt, as "scrypt$n$r$p$salt$hash", all base64. A stdlib KDF
            -- rather than a dependency: memory-hard, and shipping it costs no
            -- image rebuild.
            password_hash   varchar(400) NOT NULL,
            password_set_at timestamptz  NOT NULL DEFAULT now(),
            -- Set when a temporary credential is issued, so the next sign-in
            -- has to replace it.
            must_change     boolean      NOT NULL DEFAULT false,
            failed_attempts integer      NOT NULL DEFAULT 0,
            locked_until    timestamptz,
            updated_at      timestamptz  NOT NULL DEFAULT now(),
            version         bigint       NOT NULL DEFAULT 0,
            CONSTRAINT ck_cred_attempts CHECK (failed_attempts >= 0)
        )
        """
    )

    op.execute(
        """
        CREATE TABLE iam.password_tokens (
            id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            user_id     uuid NOT NULL REFERENCES iam.users(id) ON DELETE CASCADE,
            -- sha256 of the token that was sent. The token itself exists only
            -- in the email.
            token_hash  varchar(64) NOT NULL UNIQUE,
            purpose     varchar(20) NOT NULL,
            expires_at  timestamptz NOT NULL,
            used_at     timestamptz,
            created_at  timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT ck_token_purpose
                CHECK (purpose IN ('welcome', 'reset'))
        )
        """
    )
    op.execute(
        "CREATE INDEX ix_password_tokens_user "
        "ON iam.password_tokens (user_id) WHERE used_at IS NULL"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS iam.password_tokens")
    op.execute("DROP TABLE IF EXISTS iam.user_credentials")
    op.drop_column("users", "email", schema="iam")
