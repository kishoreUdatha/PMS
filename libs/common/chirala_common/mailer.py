"""Sending mail, over real SMTP.

``smtplib`` from the standard library, so no dependency and no image rebuild.

**It refuses loudly when it is not configured.** An unconfigured mailer that
quietly returns success is the worst outcome available here: the welcome email
is the only thing standing between a new user and an account they cannot get
into, and "sent" appearing in a log while nothing left the building is how that
goes unnoticed for a week. With no host set, :func:`send` raises, and the
caller decides whether that fails the request or is recorded and carried.
"""
from __future__ import annotations

import smtplib
import ssl
from dataclasses import dataclass
from email.message import EmailMessage
from email.utils import formataddr


class MailNotConfigured(RuntimeError):
    """No SMTP host is set, so nothing can be sent."""


@dataclass(frozen=True)
class MailConfig:
    host: str = ""
    port: int = 587
    username: str = ""
    password: str = ""
    #: The address mail appears to come from.
    sender: str = ""
    sender_name: str = ""
    #: STARTTLS on the usual submission port; implicit TLS on 465.
    starttls: bool = True
    timeout: int = 20

    @property
    def configured(self) -> bool:
        return bool(self.host and self.sender)


def send(
    config: MailConfig, *, to: str, subject: str, text: str,
    html: str | None = None,
    attachments: list[tuple[str, str, bytes]] | None = None,
) -> None:
    """Send one message, or raise.

    Both a plain-text and an HTML part are sent when HTML is given, because a
    welcome mail that renders as raw markup in somebody's client is a welcome
    mail that gets deleted.

    ``attachments`` are ``(filename, mime_type, content)``. A receipt mailed
    without the receipt on it is a mail that makes the guest ask again, and
    this module could not carry one until the folio screen needed to send one.
    """
    if not config.configured:
        raise MailNotConfigured(
            "No SMTP host is configured, so no mail can be sent. Set "
            "SMTP_HOST and SMTP_SENDER (and credentials if the server needs "
            "them) on the service."
        )

    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = formataddr((config.sender_name or "", config.sender))
    message["To"] = to
    message.set_content(text)
    if html:
        message.add_alternative(html, subtype="html")

    for filename, mime, content in attachments or ():
        # Split once from the right so "application/pdf" survives and anything
        # with a parameter does not confuse the maintype.
        maintype, _, subtype = mime.partition("/")
        message.add_attachment(content, maintype=maintype or "application",
                               subtype=subtype or "octet-stream",
                               filename=filename)

    context = ssl.create_default_context()
    if config.port == 465:
        with smtplib.SMTP_SSL(config.host, config.port,
                              timeout=config.timeout, context=context) as smtp:
            if config.username:
                smtp.login(config.username, config.password)
            smtp.send_message(message)
        return

    with smtplib.SMTP(config.host, config.port, timeout=config.timeout) as smtp:
        smtp.ehlo()
        if config.starttls:
            smtp.starttls(context=context)
            smtp.ehlo()
        if config.username:
            smtp.login(config.username, config.password)
        smtp.send_message(message)
