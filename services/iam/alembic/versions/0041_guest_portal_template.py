"""The message that sends a guest their portal link.

Revision ID: 0041_guest_portal_template
Revises: 0040_guest_sms_whatsapp

Seeded as drafts like the others in 0040. They need registering with DLT and
Meta, and the provider's id pasted in, before anything is sent.
"""
from __future__ import annotations

from alembic import op

revision: str = "0041_guest_portal_template"
down_revision: str | None = "0040_guest_sms_whatsapp"
branch_labels: str | None = None
depends_on: str | None = None

CODE = "guest_portal_link"
VARIABLES = ["guest_name", "property_name", "reservation_number", "link"]
BODIES = {
    "sms": "Dear {{guest_name}}, check in online and manage booking "
           "{{reservation_number}} at {{property_name}}: {{link}}",
    "whatsapp": "Hello {{guest_name}}, save time at {{property_name}}: check "
                "in online, send us a request or pay for booking "
                "{{reservation_number}} here:\n{{link}}",
}


def _q(v: str) -> str:
    return "'" + v.replace("'", "''") + "'"


def upgrade() -> None:
    arr = "ARRAY[" + ", ".join(_q(v) for v in VARIABLES) + "]::text[]"
    for channel, body in BODIES.items():
        op.execute(f"""
            INSERT INTO platform.message_templates
                (code, version, name, channel, body_text, variables, status)
            VALUES ({_q(CODE)}, 1, 'Guest portal link', '{channel}', {_q(body)},
                    {arr}, 'draft')
            ON CONFLICT (code, channel, version) DO NOTHING
        """)


def downgrade() -> None:
    op.execute(f"DELETE FROM platform.message_templates WHERE code = {_q(CODE)}")
