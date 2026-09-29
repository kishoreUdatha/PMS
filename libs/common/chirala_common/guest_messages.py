"""Telling a guest something on their phone: SMS, WhatsApp, or both.

``messenger.py`` knows how to talk to MSG91. This module decides *whether* to
and *what* to send, so booking-core and finance send guest messages the same
way:

1. **The property chooses.** ``iam.properties.guest_sms_enabled`` and
   ``guest_whatsapp_enabled`` are off by default, because every message costs
   money and a hotel should turn that on knowingly. A channel that is off is
   skipped silently.
2. **The template decides the words.** A published row in
   ``platform.message_templates`` for this code and channel, with the
   provider's template id, is required. No template means no message, because
   the operator would drop an unregistered SMS anyway.
3. **Every attempt is logged.** Sent, failed or suppressed (test mode), each
   goes to ``platform.message_deliveries`` through ``record_delivery``, so the
   console can answer "did the guest get it?"

It never raises. The booking or payment that triggered a message has already
happened, and a failed text must not turn it into an error somebody has to
unpick. The outcome of each channel comes back to the caller instead.
"""
from __future__ import annotations

import logging
import re
import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from .delivery_log import record_delivery
from .messenger import (
    MessagingConfig, MessagingError, MessagingNotConfigured, normalise_mobile,
    send_sms, send_whatsapp,
)

log = logging.getLogger("uvicorn.error").getChild("guest_messages")

CHANNELS = ("sms", "whatsapp")


@dataclass(frozen=True)
class Outcome:
    channel: str
    status: str  # sent | failed | suppressed | skipped
    detail: str = ""

    def as_dict(self) -> dict[str, str]:
        return {"channel": self.channel, "status": self.status,
                "detail": self.detail}


def render(body: str, values: dict[str, Any]) -> str:
    """The template's wording with its ``{{name}}`` placeholders filled in.

    Only for the delivery log and test mode. The provider holds the registered
    wording and does its own substitution, so this text is never what is sent.
    """
    return re.sub(r"\{\{\s*([a-z_]+)\s*\}\}",
                  lambda m: str(values.get(m.group(1), "")), body or "")


def _templates(session: Session, code: str) -> dict[str, dict]:
    rows = session.execute(
        text("""
            SELECT DISTINCT ON (channel)
                   channel, status, provider_template_id, body_text,
                   variables, language
            FROM platform.message_templates
            WHERE code = :c AND channel IN ('sms', 'whatsapp')
              AND status <> 'retired'
            ORDER BY channel, version DESC
        """),
        {"c": code},
    ).mappings().all()
    return {r["channel"]: dict(r) for r in rows}


def _switches(session: Session, property_id: uuid.UUID) -> dict[str, bool]:
    row = session.execute(
        text("SELECT guest_sms_enabled, guest_whatsapp_enabled "
             "FROM iam.properties WHERE id = :p"),
        {"p": property_id},
    ).mappings().first()
    if row is None:
        return {"sms": False, "whatsapp": False}
    return {"sms": bool(row["guest_sms_enabled"]),
            "whatsapp": bool(row["guest_whatsapp_enabled"])}


def notify_guest(
    session: Session,
    session_factory: Any,
    config: MessagingConfig,
    *,
    code: str,
    organization_id: uuid.UUID | str | None,
    property_id: uuid.UUID,
    phone: str | None,
    values: dict[str, Any],
    channels: tuple[str, ...] = CHANNELS,
    force: bool = False,
) -> list[Outcome]:
    """Send ``code`` to the guest on every enabled channel. Never raises.

    ``force`` ignores the property's switches. It is for a person pressing
    "Send" on a screen, who has decided to spend the message; automatic sends
    leave it off and follow the property's choice.
    """
    try:
        return _notify(session, session_factory, config, code=code,
                       organization_id=organization_id,
                       property_id=property_id, phone=phone, values=values,
                       channels=channels, force=force)
    except Exception as exc:  # noqa: BLE001
        log.warning("guest message %s not sent: %s", code, exc)
        return [Outcome(c, "failed", "unexpected error") for c in channels]


def _notify(session, session_factory, config, *, code, organization_id,
            property_id, phone, values, channels, force) -> list[Outcome]:
    to = normalise_mobile(phone)
    switches = _switches(session, property_id)
    wanted = [c for c in channels if force or switches.get(c)]
    if not wanted:
        return [Outcome(c, "skipped", "turned off for this property")
                for c in channels]
    if to is None:
        return [Outcome(c, "skipped", "no mobile number on the booking")
                for c in wanted]

    templates = _templates(session, code)
    outcomes: list[Outcome] = []
    for channel in wanted:
        t = templates.get(channel)
        if t is None:
            outcomes.append(Outcome(channel, "skipped", "no template"))
            continue
        # Test mode walks drafts too, so the flow can be tried before any
        # template is registered. A real send needs an approved template.
        if not config.test_mode and (
                t["status"] != "published" or not t["provider_template_id"]):
            outcomes.append(Outcome(
                channel, "skipped",
                "template not published with a provider template id"))
            continue

        names = list(t["variables"] or [])
        preview = render(t["body_text"], values)
        try:
            if channel == "sms":
                ref = send_sms(config, to=to,
                               template_id=t["provider_template_id"] or "",
                               variables={n: values.get(n, "") for n in names})
            else:
                ref = send_whatsapp(config, to=to,
                                    template_name=t["provider_template_id"] or "",
                                    values=[values.get(n, "") for n in names],
                                    language=t["language"] or "en")
        except MessagingNotConfigured as exc:
            outcome = Outcome(channel, "failed", str(exc)[:200])
        except MessagingError as exc:
            outcome = Outcome(channel, "failed", str(exc)[:200])
        else:
            outcome = (Outcome(channel, "suppressed", "test mode: not sent")
                       if ref is None
                       else Outcome(channel, "sent", f"ref {ref}" if ref else ""))

        record_delivery(
            session_factory, template_code=code, recipient=to,
            subject=preview[:300] or None, status=outcome.status,
            organization_id=organization_id, detail=outcome.detail or None,
            channel=channel)
        outcomes.append(outcome)
    return outcomes


def summary(outcomes: list[Outcome]) -> str:
    """One line for a screen: what happened on each channel."""
    words = {"sent": "sent", "suppressed": "logged (test mode)",
             "failed": "failed", "skipped": "not sent"}
    return "; ".join(
        f"{o.channel.upper() if o.channel == 'sms' else 'WhatsApp'} "
        f"{words.get(o.status, o.status)}"
        + (f" ({o.detail})" if o.detail and o.status in ('failed', 'skipped') else "")
        for o in outcomes)
