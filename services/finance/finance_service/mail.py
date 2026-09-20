"""The morning summary of a night audit.

The close happens at 3am with nobody watching, which is the point of it — and
also the problem: the person who cares about the numbers is asleep. This is how
an unattended job stays accountable. It is the first thing about the audit a
tenant sees, so it leads with what the night earned and what needs attention,
not with the fact that a job ran.

**It is sent after the day is committed, never as part of closing it.** A
mailbox that is down must not stop a hotel's books closing, and an email about
a close that later rolled back is worse than no email at all.
"""

from __future__ import annotations

import logging
import uuid
from datetime import date
from decimal import Decimal

from chirala_common.mailer import MailNotConfigured, send as send_mail
from sqlalchemy import text
from sqlalchemy.orm import Session

from .settings import settings

log = logging.getLogger("uvicorn.error").getChild("night_audit.mail")


def _money(v: Decimal | str, currency: str = "₹") -> str:
    return f"{currency}{Decimal(str(v)):,.2f}"


def recipients(session: Session, property_id: uuid.UUID) -> list[str]:
    """Who gets the summary for this property.

    An explicit setting wins; the property's contact address is the fallback,
    because a tenant who never chose still gave us an address. An empty setting
    is a decision -- "send nothing" -- and is respected rather than treated as
    unset.
    """
    row = session.execute(
        text(
            """
            SELECT s.report_email, p.contact_email
            FROM iam.properties p
            LEFT JOIN finance.night_audit_settings s ON s.property_id = p.id
            WHERE p.id = :p
            """
        ),
        {"p": property_id},
    ).mappings().first()
    if row is None:
        return []
    chosen = row["report_email"]
    if chosen is not None:
        chosen = chosen.strip()
        return [a.strip() for a in chosen.split(",") if a.strip()]
    fallback = (row["contact_email"] or "").strip()
    return [fallback] if fallback else []


def night_audit_email(
    *,
    property_name: str,
    business_date: date,
    rooms_charged: int,
    amount_charged: Decimal,
    no_shows: int,
    overstays: int,
    open_shifts: int,
    next_business_date: date,
    warnings: list[str],
    report_url: str | None = None,
) -> tuple[str, str, str]:
    """Subject, plain text, and HTML for one night's summary."""
    day = f"{business_date:%d %b %Y}"
    attention = []
    if no_shows:
        attention.append(f"{no_shows} no-show{'s' if no_shows != 1 else ''}")
    if overstays:
        attention.append(f"{overstays} overstay{'s' if overstays != 1 else ''}")
    if open_shifts:
        attention.append(
            f"{open_shifts} cashier shift{'s' if open_shifts != 1 else ''} "
            f"left open")

    # The subject carries the outcome, because for most of the year this mail
    # is read in a list and never opened.
    subject = (
        f"{property_name}: {day} closed — {_money(amount_charged)}"
        + (f", {attention[0]}" if len(attention) == 1 else "")
        + (f", {len(attention)} items need attention" if len(attention) > 1
           else "")
    )

    rows = [
        ("Business date closed", day),
        ("Rooms charged", str(rooms_charged)),
        ("Room revenue posted", _money(amount_charged)),
        ("No-shows processed", str(no_shows)),
        ("Overstays", str(overstays)),
        ("Cashier shifts left open", str(open_shifts)),
        ("Next business date", f"{next_business_date:%d %b %Y}"),
    ]
    width = max(len(k) for k, _ in rows)
    lines = [f"  {k.ljust(width)}   {v}" for k, v in rows]

    text_body = (
        f"{property_name}\nNight audit — {day}\n\n"
        + "\n".join(lines)
        + ("\n\nNeeds attention:\n"
           + "\n".join(f"  - {w}" for w in warnings) if warnings else "")
        + (f"\n\nFull report: {report_url}" if report_url else "")
        + "\n\nThis day is now closed; nothing further can be posted to it.\n"
    )

    tr = "".join(
        f'<tr><td style="padding:6px 14px 6px 0;color:#64748b">{k}</td>'
        f'<td style="padding:6px 0;font-weight:600;color:#0f172a">{v}</td></tr>'
        for k, v in rows
    )
    warn_html = ""
    if warnings:
        items = "".join(f"<li>{w}</li>" for w in warnings)
        warn_html = (
            '<p style="margin:16px 0 4px;font-weight:600;color:#92400e">'
            "Needs attention</p>"
            f'<ul style="margin:0;padding-left:18px;color:#92400e">{items}</ul>'
        )
    link_html = (
        f'<p style="margin:18px 0 0"><a href="{report_url}" '
        'style="color:#0f766e;font-weight:600">View the full report</a></p>'
        if report_url else ""
    )
    html_body = (
        '<div style="font-family:system-ui,-apple-system,Segoe UI,sans-serif;'
        'max-width:560px;color:#0f172a">'
        f'<p style="margin:0;font-size:12px;letter-spacing:.14em;'
        f'text-transform:uppercase;color:#94a3b8">Night audit</p>'
        f'<h2 style="margin:2px 0 2px;font-size:20px">{property_name}</h2>'
        f'<p style="margin:0 0 14px;color:#64748b">{day} is closed.</p>'
        f'<table style="border-collapse:collapse;font-size:14px">{tr}</table>'
        f"{warn_html}{link_html}"
        '<p style="margin:18px 0 0;font-size:12px;color:#94a3b8">'
        "Nothing further can be posted to this date.</p>"
        "</div>"
    )
    return subject, text_body, html_body


def send_night_audit_report(
    session: Session,
    *,
    property_id: uuid.UUID,
    property_name: str,
    business_date: date,
    rooms_charged: int,
    amount_charged: Decimal,
    no_shows: int,
    overstays: int,
    open_shifts: int,
    next_business_date: date,
    warnings: list[str],
) -> list[str]:
    """Mail the summary. Never raises.

    A failure here is logged and swallowed on purpose: the day is already
    closed and committed by the time this runs, and an unreachable mail server
    must not turn a completed audit into an error somebody has to investigate.
    """
    to = recipients(session, property_id)
    if not to:
        return []

    base = settings.app_base_url.rstrip("/") if settings.app_base_url else ""
    subject, text_body, html_body = night_audit_email(
        property_name=property_name, business_date=business_date,
        rooms_charged=rooms_charged, amount_charged=amount_charged,
        no_shows=no_shows, overstays=overstays, open_shifts=open_shifts,
        next_business_date=next_business_date, warnings=warnings,
        report_url=f"{base}/night-audit/history" if base else None,
    )

    sent: list[str] = []
    for address in to:
        try:
            send_mail(settings.mail_config, to=address, subject=subject,
                      text=text_body, html=html_body)
            sent.append(address)
        except (MailNotConfigured, OSError, Exception) as exc:  # noqa: BLE001
            log.warning("night audit summary to %s failed: %s", address, exc)
    return sent
