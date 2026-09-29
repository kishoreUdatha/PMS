"""Guest SMS and WhatsApp, through MSG91.

Revision ID: 0040_guest_sms_whatsapp
Revises: 0039_demo_requests

Until now the product could only email, and several screens said so: the
check-in "welcome sent" box and the deposit reminder both recorded that a
person had sent something, because the system could not. This migration is
the database half of changing that. The sending is chirala_common.messenger
and guest_messages.

**Templates carry the provider's id.** Indian SMS must match a DLT-registered
template and WhatsApp a Meta-approved one, so a template row is only sendable
once somebody has registered it and pasted the id MSG91 gave back into
``provider_template_id``. Everything is seeded as a draft without one. Nothing
goes out until the platform team has done that and published the row.

**One template per code per channel.** The SMS and WhatsApp versions of a
booking confirmation share a code, because they are the same message, so the
uniqueness rule moves from (code, version) to (code, channel, version).

**Off by default, per property.** Every message costs money, so a hotel turns
each channel on knowingly, from its property settings.
"""
from __future__ import annotations

from alembic import op

revision: str = "0040_guest_sms_whatsapp"
down_revision: str | None = "0039_demo_requests"
branch_labels: str | None = None
depends_on: str | None = None

#: code -> (name, variables, sms wording, whatsapp wording)
#:
#: The wording is what gets registered with DLT and Meta, and what the
#: delivery log shows. It stays short, because an SMS over 160 characters is
#: billed as two.
TEMPLATES = [
    ("guest_booking_confirmed", "Booking confirmed",
     ["guest_name", "property_name", "reservation_number", "arrival", "departure"],
     "Dear {{guest_name}}, your stay at {{property_name}} is confirmed. "
     "Booking {{reservation_number}}, {{arrival}} to {{departure}}.",
     "Hello {{guest_name}}, your stay at {{property_name}} is confirmed.\n"
     "Booking: {{reservation_number}}\nCheck-in: {{arrival}}\n"
     "Check-out: {{departure}}\nWe look forward to welcoming you."),
    ("guest_checkin_welcome", "Welcome at check-in",
     ["guest_name", "property_name", "room_number", "checkout_date"],
     "Welcome to {{property_name}}, {{guest_name}}. You are in room "
     "{{room_number}} until {{checkout_date}}.",
     "Welcome to {{property_name}}, {{guest_name}}!\nYour room: "
     "{{room_number}}\nCheck-out: {{checkout_date}}\nMessage the front desk "
     "any time you need something."),
    ("guest_deposit_reminder", "Deposit reminder",
     ["guest_name", "amount", "due_date", "reservation_number", "property_name"],
     "Dear {{guest_name}}, a payment of {{amount}} for booking "
     "{{reservation_number}} at {{property_name}} is due {{due_date}}.",
     "Hello {{guest_name}}, a friendly reminder: {{amount}} for booking "
     "{{reservation_number}} at {{property_name}} is due {{due_date}}."),
    ("guest_payment_link", "Payment link",
     ["guest_name", "amount", "reservation_number", "property_name", "link"],
     "Dear {{guest_name}}, pay {{amount}} for booking {{reservation_number}} "
     "at {{property_name}} securely: {{link}}",
     "Hello {{guest_name}}, you can pay {{amount}} for booking "
     "{{reservation_number}} at {{property_name}} securely here:\n{{link}}"),
]


def _q(v: str) -> str:
    return "'" + v.replace("'", "''") + "'"


def upgrade() -> None:
    op.execute("""
        ALTER TABLE platform.message_templates
            DROP CONSTRAINT IF EXISTS message_templates_channel_check;
        ALTER TABLE platform.message_templates
            ADD CONSTRAINT message_templates_channel_check
            CHECK (channel IN ('email', 'sms', 'whatsapp'));
        ALTER TABLE platform.message_templates
            ADD COLUMN IF NOT EXISTS provider_template_id varchar(100) NULL,
            ADD COLUMN IF NOT EXISTS language varchar(10) NOT NULL DEFAULT 'en';
        ALTER TABLE platform.message_templates
            DROP CONSTRAINT IF EXISTS uq_template_version;
        ALTER TABLE platform.message_templates
            ADD CONSTRAINT uq_template_version UNIQUE (code, channel, version);

        ALTER TABLE iam.properties
            ADD COLUMN IF NOT EXISTS guest_sms_enabled boolean NOT NULL DEFAULT false,
            ADD COLUMN IF NOT EXISTS guest_whatsapp_enabled boolean NOT NULL DEFAULT false;
    """)

    for code, name, variables, sms, whatsapp in TEMPLATES:
        arr = "ARRAY[" + ", ".join(_q(v) for v in variables) + "]::text[]"
        for channel, body in (("sms", sms), ("whatsapp", whatsapp)):
            op.execute(
                f"""
                INSERT INTO platform.message_templates
                    (code, version, name, channel, body_text, variables, status)
                VALUES ({_q(code)}, 1, {_q(name)}, '{channel}', {_q(body)},
                        {arr}, 'draft')
                ON CONFLICT (code, channel, version) DO NOTHING
                """
            )


def downgrade() -> None:
    op.execute("""
        DELETE FROM platform.message_templates WHERE channel = 'whatsapp'
           OR code IN ('guest_booking_confirmed', 'guest_checkin_welcome',
                       'guest_deposit_reminder', 'guest_payment_link');
        ALTER TABLE iam.properties
            DROP COLUMN IF EXISTS guest_sms_enabled,
            DROP COLUMN IF EXISTS guest_whatsapp_enabled;
        ALTER TABLE platform.message_templates
            DROP CONSTRAINT IF EXISTS uq_template_version;
        ALTER TABLE platform.message_templates
            ADD CONSTRAINT uq_template_version UNIQUE (code, version);
        ALTER TABLE platform.message_templates
            DROP COLUMN IF EXISTS provider_template_id,
            DROP COLUMN IF EXISTS language;
        ALTER TABLE platform.message_templates
            DROP CONSTRAINT IF EXISTS message_templates_channel_check;
        ALTER TABLE platform.message_templates
            ADD CONSTRAINT message_templates_channel_check
            CHECK (channel IN ('email', 'sms'));
    """)
