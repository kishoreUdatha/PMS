#!/usr/bin/env python
"""Prove the mail configuration works, before anyone depends on it.

Run it inside the iam container so it reads exactly the settings the service
reads — not your shell's, not your laptop's::

    docker compose exec -T iam python /app/scripts/check_mail.py you@example.com

It reports what is configured, connects, authenticates, and sends one real
message. Failures are translated: Google's SMTP errors say things like
``535-5.7.8 Username and Password not accepted`` with a help URL, which is
accurate and tells you almost nothing about which of the four likely causes
you have hit.

Nothing here touches the database. A welcome email is the only thing standing
between a new user and an account they cannot get into, so it is worth being
able to test the pipe on its own.
"""
from __future__ import annotations

import smtplib
import ssl
import sys
from datetime import datetime, timezone

sys.path.insert(0, "/app/services/iam")

from iam_service.settings import settings  # noqa: E402


def explain(exc: Exception) -> str:
    """Turn an SMTP failure into the thing to go and change."""
    text = str(exc)
    if isinstance(exc, smtplib.SMTPAuthenticationError) or "535" in text:
        return (
            "Authentication was refused. With Google Workspace that is almost\n"
            "always one of:\n"
            "  * SMTP_PASSWORD is your normal account password. It must be an\n"
            "    App Password (Google Account > Security > App passwords),\n"
            "    which needs 2-Step Verification switched on first.\n"
            "  * The app password was pasted with the spaces Google displays.\n"
            "    Remove them — it is 16 characters, no spaces.\n"
            "  * SMTP_USERNAME is not the full address. It must be\n"
            "    name@yourdomain.com, not 'name'.\n"
            "  * The Workspace admin has SMTP/IMAP access switched off for\n"
            "    that account or organisational unit."
        )
    if isinstance(exc, smtplib.SMTPSenderRefused) or "553" in text or "530" in text:
        return (
            "The server refused the From address. Gmail only lets you send as\n"
            "the authenticated account, or an address it owns. Either set\n"
            "SMTP_SENDER to the same address as SMTP_USERNAME, or add it under\n"
            "Gmail > Settings > Accounts > 'Send mail as' and verify it, or\n"
            "use smtp-relay.gmail.com with the relay configured in the admin\n"
            "console."
        )
    if isinstance(exc, (TimeoutError, OSError)) and "timed out" in text.lower():
        return (
            "The connection timed out. Port 587 outbound is often blocked by\n"
            "hosting providers and office networks. Try port 465 (set\n"
            "SMTP_PORT=465; TLS is implicit there and STARTTLS is not used),\n"
            "or ask whoever runs the network to open 587."
        )
    if "certificate" in text.lower():
        return (
            "TLS verification failed. The host is probably not what you think\n"
            "it is — check SMTP_HOST for a typo."
        )
    return "No specific advice for this one; the server's own words are above."


def main() -> int:
    if len(sys.argv) < 2:
        print("Usage: check_mail.py recipient@example.com")
        return 2
    to = sys.argv[1]
    config = settings.mail_config

    print("Configuration the iam service is using")
    print("-" * 56)
    print(f"  SMTP_HOST       {config.host or '(not set)'}")
    print(f"  SMTP_PORT       {config.port}")
    print(f"  SMTP_USERNAME   {config.username or '(none — unauthenticated)'}")
    print(f"  SMTP_PASSWORD   {'set, ' + str(len(config.password)) + ' chars'
                               if config.password else '(not set)'}")
    print(f"  SMTP_SENDER     {config.sender or '(not set)'}")
    print(f"  SMTP_STARTTLS   {config.starttls}")
    print(f"  APP_BASE_URL    {settings.app_base_url}")
    print()

    if not config.configured:
        print("FAIL: SMTP_HOST and SMTP_SENDER must both be set. Put them in")
        print("      .env at the repo root, then: docker compose up -d iam")
        return 1

    if config.password and " " in config.password:
        print("WARNING: the password contains spaces. Google displays app")
        print("         passwords in groups of four, but they are entered")
        print("         without spaces. This will almost certainly fail.")
        print()

    if (config.sender and config.username
            and config.sender.lower() != config.username.lower()
            and "relay" not in config.host.lower()):
        print(f"NOTE: sending as {config.sender} while authenticated as")
        print(f"      {config.username}. Gmail will rewrite the From address")
        print("      unless that account owns it under 'Send mail as'.")
        print()

    stamp = datetime.now(timezone.utc).strftime("%d %b %Y %H:%M UTC")
    try:
        context = ssl.create_default_context()
        if config.port == 465:
            print(f"Connecting to {config.host}:465 over implicit TLS…")
            smtp = smtplib.SMTP_SSL(config.host, 465, timeout=config.timeout,
                                    context=context)
        else:
            print(f"Connecting to {config.host}:{config.port}…")
            smtp = smtplib.SMTP(config.host, config.port,
                                timeout=config.timeout)
        with smtp:
            smtp.ehlo()
            if config.port != 465 and config.starttls:
                print("Starting TLS…")
                smtp.starttls(context=context)
                smtp.ehlo()
            if config.username:
                print("Authenticating…")
                smtp.login(config.username, config.password)
            print(f"Sending to {to}…")
            from email.message import EmailMessage
            message = EmailMessage()
            message["Subject"] = "Chirala Bay PMS — mail configuration test"
            message["From"] = config.sender
            message["To"] = to
            message.set_content(
                "This is a test from the Chirala Bay PMS iam service.\n\n"
                f"Sent at {stamp}.\n\n"
                "If you can read this, welcome emails and password reset\n"
                "links will send. Nothing else was changed.\n"
            )
            smtp.send_message(message)
    except Exception as exc:  # noqa: BLE001 — the point is to report anything
        print()
        print("FAILED")
        print("-" * 56)
        print(f"  {type(exc).__name__}: {exc}")
        print()
        print(explain(exc))
        return 1

    print()
    print(f"SENT. Check {to} — including the spam folder the first time.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
