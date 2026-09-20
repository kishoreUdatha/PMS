"""Register the four templates this platform actually sends

Revision ID: 0033_seed_message_templates
Revises: 0032_platform_operations
Create Date: 2026-09-14

``iam_service/mail.py`` builds four messages in Python. That is fine -- they
interpolate links and tables and a tenant should not be able to break them --
but it means nobody outside the codebase can see what the platform says in a
customer's name, or when the wording last changed.

These rows are the register of those four. ``body_text`` holds the shape of
each message rather than a copy of the Python, because a second copy would
drift from the first and the drift would be invisible. What is exact is the
subject line and the ``variables`` list: the subject is what people see, and
the variable names are what an editor may legitimately use -- the route that
accepts an edit checks new text against exactly this list, so a template that
declares a variable it does not receive would let somebody publish an email
with a literal ``{{guest_name}}`` in it.

Version 1 of each, published. A future wording change gets version 2 rather
than overwriting this, so "what did we send them in September" stays
answerable.
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0033_seed_message_templates"
down_revision: str | None = "0032_platform_operations"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: code, name, subject, variables, body sketch
TEMPLATES = [
    (
        "welcome",
        "Welcome / property setup complete",
        "Welcome to {{platform}} — Your property setup is complete",
        ["platform", "first_name", "property_name", "property_code", "email",
         "link", "hours", "support"],
        "Hello {{first_name}},\n\n"
        "{{property_name}} is set up on {{platform}} and your account is "
        "ready.\n\n"
        "Property: {{property_name}} ({{property_code}})\n"
        "Sign in as: {{email}}\n\n"
        "Choose your password: {{link}}\n"
        "The link is good for {{hours}} hours.\n\n"
        "{{support}}",
    ),
    (
        "password_reset",
        "Password reset link",
        "Reset your {{property_name}} password",
        ["first_name", "property_name", "property_code", "email", "link",
         "support"],
        "Hello {{first_name}},\n\n"
        "Somebody asked to reset the password for {{email}} at "
        "{{property_name}} ({{property_code}}).\n\n"
        "Set a new one: {{link}}\n\n"
        "If that was not you, nothing has changed and you can ignore this.\n\n"
        "{{support}}",
    ),
    (
        "verification",
        "Email verification code",
        "{{code}} is your {{platform}} verification code",
        ["platform", "first_name", "code", "minutes", "support"],
        "Hello {{first_name}},\n\n"
        "Your verification code is {{code}}.\n"
        "It expires in {{minutes}} minutes.\n\n"
        "The code is in the subject line too, so you can read it without "
        "opening this.\n\n"
        "{{support}}",
    ),
    (
        "property_live",
        "Property is live / sign-in details",
        "{{property_name}} is live — your sign-in details",
        ["platform", "first_name", "property_name", "property_code", "email",
         "link", "hours", "support"],
        "Hello {{first_name}},\n\n"
        "{{property_name}} ({{property_code}}) is live on {{platform}}.\n\n"
        "Sign in as: {{email}}\n"
        "Set your password: {{link}} (good for {{hours}} hours)\n\n"
        "{{support}}",
    ),
]


def _q(v: str) -> str:
    return "'" + v.replace("'", "''") + "'"


def upgrade() -> None:
    for code, name, subject, variables, body in TEMPLATES:
        vars_sql = "ARRAY[" + ", ".join(_q(v) for v in variables) + "]::text[]"
        op.execute(
            f"""
            INSERT INTO platform.message_templates
                (code, version, name, channel, subject, body_text, variables,
                 status)
            VALUES ({_q(code)}, 1, {_q(name)}, 'email', {_q(subject)},
                    {_q(body)}, {vars_sql}, 'published')
            ON CONFLICT (code, version) DO UPDATE SET
                name = EXCLUDED.name,
                subject = EXCLUDED.subject,
                variables = EXCLUDED.variables,
                updated_at = now()
            """
        )


def downgrade() -> None:
    codes = ", ".join(_q(c) for c, *_ in TEMPLATES)
    op.execute(
        f"DELETE FROM platform.message_templates "
        f"WHERE version = 1 AND code IN ({codes})"
    )
