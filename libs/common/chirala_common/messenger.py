"""Sending SMS and WhatsApp messages through MSG91.

The counterpart of ``mailer.py`` for a guest's phone. Standard library only
(``urllib``), for the same reason the mailer uses ``smtplib``: no dependency and
no image rebuild just to send a text.

**Both channels are template-only, by law and by policy.** An SMS to an Indian
number must match a template registered on the DLT platform, or the operator
drops it. A business-initiated WhatsApp message must use a template Meta has
approved. So nothing here sends free text. Every send names a provider template
and passes it values, and the wording lives with the provider.

* SMS goes through MSG91's Flow API. A flow is linked to one DLT template, and
  its variables are sent by name, so the names in
  ``platform.message_templates.variables`` must match the flow's.
* WhatsApp goes through MSG91's outbound template API. Meta templates number
  their placeholders ``{{1}}``, ``{{2}}``..., so values are sent in the order
  the template's ``variables`` list gives them.

**Unconfigured is loud, unless test mode says otherwise.** As with the mailer,
a send with no auth key raises :class:`MessagingNotConfigured` so a missing
setting is never mistaken for a delivered message. With ``test_mode`` on,
nothing is sent, the call returns ``None``, and the caller records the message
as suppressed. That lets development and demos walk the whole flow without an
MSG91 account or a bill.
"""
from __future__ import annotations

import json
import logging
import re
import urllib.error
import urllib.request
from dataclasses import dataclass

log = logging.getLogger("uvicorn.error").getChild("messenger")

SMS_URL = "https://control.msg91.com/api/v5/flow/"
WHATSAPP_URL = ("https://api.msg91.com/api/v5/whatsapp/"
                "whatsapp-outbound-message/bulk/")


class MessagingNotConfigured(RuntimeError):
    """No MSG91 auth key is set, and test mode is off."""


class MessagingError(RuntimeError):
    """MSG91 refused the message, or could not be reached."""


@dataclass(frozen=True)
class MessagingConfig:
    auth_key: str = ""
    #: The WhatsApp Business number MSG91 sends from, digits with country code.
    whatsapp_number: str = ""
    #: Send nothing and report success-without-delivery. See module docstring.
    test_mode: bool = False
    timeout: int = 15

    @property
    def sms_ready(self) -> bool:
        return bool(self.auth_key)

    @property
    def whatsapp_ready(self) -> bool:
        return bool(self.auth_key and self.whatsapp_number)


# --------------------------------------------------------------- numbers --

_INDIAN_MOBILE = re.compile(r"^[6-9]\d{9}$")


def normalise_mobile(raw: str | None, default_country: str = "91") -> str | None:
    """A phone number as MSG91 wants it: digits, country code first, no plus.

    Returns ``None`` for anything that cannot be a mobile number, so the caller
    skips the guest rather than paying to message a landline or a typo.

    A bare ten-digit number starting 6-9 is an Indian mobile and gets 91 in
    front. A number written with a leading ``+`` or ``00`` already carries its
    country and is kept as written, which is how a foreign guest's number
    survives. A leading 0 on an Indian number is a trunk prefix and is dropped.
    """
    if not raw:
        return None
    s = raw.strip()
    international = s.startswith("+") or s.startswith("00")
    digits = re.sub(r"\D", "", s)
    if s.startswith("00"):
        digits = digits[2:]
    if international:
        return digits if 8 <= len(digits) <= 15 else None
    if len(digits) == 11 and digits.startswith("0"):
        digits = digits[1:]
    if _INDIAN_MOBILE.match(digits):
        return default_country + digits
    if len(digits) == 12 and digits.startswith("91") and _INDIAN_MOBILE.match(digits[2:]):
        return digits
    return None


# ----------------------------------------------------------------- sends --

def _post(config: MessagingConfig, url: str, payload: dict) -> dict:
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode("utf-8"), method="POST",
        headers={"authkey": config.auth_key,
                 "Content-Type": "application/json",
                 "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=config.timeout) as resp:
            body = resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:300]
        raise MessagingError(f"MSG91 answered {exc.code}: {detail}") from None
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise MessagingError(f"MSG91 could not be reached: {exc}") from None
    try:
        data = json.loads(body) if body else {}
    except ValueError:
        data = {"raw": body[:300]}
    # MSG91 reports some refusals as 200 with type "error".
    if isinstance(data, dict) and str(data.get("type", "")).lower() == "error":
        raise MessagingError(f"MSG91 refused the message: {data.get('message')}")
    return data if isinstance(data, dict) else {"raw": data}


def sms_payload(*, to: str, template_id: str, variables: dict[str, str]) -> dict:
    recipient = {"mobiles": to, **{k: str(v) for k, v in variables.items()}}
    return {"template_id": template_id, "short_url": "0",
            "recipients": [recipient]}


def whatsapp_payload(*, from_number: str, to: str, template_name: str,
                     language: str, values: list[str]) -> dict:
    components = {f"body_{i}": {"type": "text", "value": str(v)}
                  for i, v in enumerate(values, 1)}
    return {
        "integrated_number": from_number,
        "content_type": "template",
        "payload": {
            "messaging_product": "whatsapp",
            "type": "template",
            "template": {
                "name": template_name,
                "language": {"code": language, "policy": "deterministic"},
                "namespace": None,
                "to_and_components": [{"to": [to], "components": components}],
            },
        },
    }


def send_sms(config: MessagingConfig, *, to: str, template_id: str,
             variables: dict[str, str]) -> str | None:
    """Send one DLT-templated SMS. Returns MSG91's request id.

    ``None`` means test mode: nothing left the building.
    """
    if config.test_mode:
        log.info("TEST MODE sms to %s template %s: %s", to, template_id, variables)
        return None
    if not config.sms_ready:
        raise MessagingNotConfigured(
            "No MSG91 auth key is configured, so no SMS can be sent. Set "
            "MSG91_AUTH_KEY, or MESSAGING_TEST_MODE=true to try the flow "
            "without sending.")
    data = _post(config, SMS_URL, sms_payload(
        to=to, template_id=template_id, variables=variables))
    return str(data.get("message") or data.get("request_id") or "")


def send_whatsapp(config: MessagingConfig, *, to: str, template_name: str,
                  values: list[str], language: str = "en") -> str | None:
    """Send one approved WhatsApp template. Returns MSG91's request id.

    ``None`` means test mode: nothing left the building.
    """
    if config.test_mode:
        log.info("TEST MODE whatsapp to %s template %s: %s", to, template_name, values)
        return None
    if not config.whatsapp_ready:
        raise MessagingNotConfigured(
            "WhatsApp needs MSG91_AUTH_KEY and MSG91_WHATSAPP_NUMBER. Set "
            "them, or MESSAGING_TEST_MODE=true to try the flow without "
            "sending.")
    data = _post(config, WHATSAPP_URL, whatsapp_payload(
        from_number=config.whatsapp_number, to=to, template_name=template_name,
        language=language, values=values))
    return str(data.get("request_id") or data.get("message") or "")
