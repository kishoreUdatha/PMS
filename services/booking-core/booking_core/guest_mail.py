"""The confirmation a guest gets when their booking becomes real.

This is the only email in the product written for someone who does not work
here. Everything else -- a welcome, an audit summary -- goes to staff who
already know what the system is. A guest has one question, and it is not "did a
transition succeed": it is *am I actually booked, and where do I turn up*. So
the confirmation number, the dates, the address and a phone number come first,
and the vocabulary of the system stays out of it.

Three decisions worth keeping:

**It is sent from the confirm endpoint, never from ``confirm_reservation``.**
Two of that function's callers are bulk importers. Hanging a mail off the flow
helper would, the first time a tenant loaded their history, mail several
hundred people about stays they took last year.

**It is sent after the transaction commits.** Mail cannot be un-sent. Telling a
guest they are booked and then rolling back the booking is the one failure here
with no remedy, so the send happens in a background task on its own session,
once the row is durable.

**It says nothing about what was paid.** Booking-core does not own the folio.
It could reach across into ``finance`` -- the database is shared -- but a
confirmation that states a balance from a schema this service does not own is a
confidently wrong number in a guest's inbox, and "paid in full" when they still
owe is the worst version of that. The stay total is stated because this service
knows the rates; payment is left to the property.
"""

from __future__ import annotations

import html as html_escape
import logging
import uuid
from datetime import date, time
from decimal import Decimal

from chirala_common.mailer import MailNotConfigured, send as send_mail
from sqlalchemy import text
from sqlalchemy.orm import Session

from .settings import settings

log = logging.getLogger("uvicorn.error").getChild("guest_mail")

_MONTHS = ("January", "February", "March", "April", "May", "June", "July",
           "August", "September", "October", "November", "December")
_DAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday",
         "Sunday")


def _long_date(d: date) -> str:
    """Spell the date out, because 05/09 is ambiguous.

    A guest reading 09/05 in one country and 05/09 in another turns up four
    months late. The day name is there so the reader notices a weekend.
    """
    return f"{_DAYS[d.weekday()]}, {d.day} {_MONTHS[d.month - 1]} {d.year}"


def _clock(raw: str | time | None) -> str:
    """Render a check-in time for a guest: "11 am", not "11:00:00".

    The column is a varchar holding whatever the property typed -- usually
    "11:00", sometimes empty. Anything that does not parse as a time is passed
    through unchanged rather than dropped: it is the property's own words about
    its own front desk, and showing it is better than silently losing it.
    """
    if raw is None:
        return ""
    if isinstance(raw, time):
        hour, minute = raw.hour, raw.minute
    else:
        cleaned = str(raw).strip()
        if not cleaned:
            return ""
        try:
            parts = cleaned.split(":")
            hour, minute = int(parts[0]), int(parts[1]) if len(parts) > 1 else 0
        except (ValueError, IndexError):
            return cleaned
        if not (0 <= hour <= 23 and 0 <= minute <= 59):
            return cleaned
    suffix = "am" if hour < 12 else "pm"
    display = hour % 12 or 12
    return f"{display}:{minute:02d} {suffix}" if minute else f"{display} {suffix}"


def _esc(value: object) -> str:
    """Escape a value before it reaches the HTML.

    Not a formality. ``special_requests`` and the guest's own name arrive from
    the *unauthenticated* public booking API, and whoever books also chooses the
    address the confirmation goes to -- so an unescaped template lets a stranger
    have a hotel-branded email, carrying markup of their choosing, delivered to
    somebody else's inbox. Mail clients drop scripts; they render links and
    text perfectly well, which is all a convincing phish needs.

    Property-owned fields are escaped too. An ampersand in a resort's name
    should not depend on who typed it.
    """
    return html_escape.escape("" if value is None else str(value), quote=True)


def _money(v: Decimal, symbol: str = "₹") -> str:
    return f"{symbol}{Decimal(str(v)):,.2f}"


def _plural(n: int, word: str, suffix: str = "s") -> str:
    return f"{n} {word}" if n == 1 else f"{n} {word}{suffix}"


def gather(session: Session, reservation_id: uuid.UUID) -> dict | None:
    """Everything the email needs, or ``None`` if it cannot be sent.

    Returns ``None`` rather than raising when there is no guest email address.
    That is not an error: a walk-in booked over the phone may never have given
    one, and a booking is perfectly valid without it.
    """
    head = session.execute(
        text(
            """
            SELECT r.number, r.currency, r.special_requests, r.source,
                   g.full_name AS guest_name, g.email AS guest_email,
                   p.name AS property_name, p.checkin_time, p.checkout_time,
                   p.contact_email, p.contact_phone,
                   p.address_line, p.city, p.state, p.postal_code,
                   cp.policy_text, cp.free_until_days,
                   cp.penalty_nights
            FROM booking.reservations r
            LEFT JOIN engagement.guests g ON g.id = r.primary_guest_id
            JOIN iam.properties p ON p.id = r.property_id
            LEFT JOIN property.cancellation_policies cp
                   ON cp.id = coalesce(
                        r.cancellation_policy_id,
                        (SELECT d.id FROM property.cancellation_policies d
                          WHERE d.property_id = r.property_id AND d.is_default
                          LIMIT 1))
            WHERE r.id = :r
            """
        ),
        {"r": reservation_id},
    ).mappings().first()
    if head is None:
        return None
    email = (head["guest_email"] or "").strip()
    if not email:
        return None

    units = session.execute(
        text(
            """
            SELECT rt.name AS room_type, u.arrival_date, u.departure_date,
                   u.adults, u.children, u.nightly_rate,
                   (u.departure_date - u.arrival_date) AS nights
            FROM booking.reservation_units u
            JOIN property.room_types rt ON rt.id = u.room_type_id
            WHERE u.reservation_id = :r AND u.status <> 'cancelled'
            ORDER BY u.arrival_date, u.line_index
            """
        ),
        {"r": reservation_id},
    ).mappings().all()
    if not units:
        return None

    # A total only when every line has a rate. A partial sum looks like a whole
    # one, and a guest quoted less than they will be charged has been misled by
    # us, not by a missing value.
    priced = [u for u in units if u["nightly_rate"] is not None]
    total = None
    if len(priced) == len(units):
        total = sum(
            (Decimal(str(u["nightly_rate"])) * u["nights"] for u in units),
            Decimal("0"),
        )

    return {
        "to": email,
        "head": dict(head),
        "units": [dict(u) for u in units],
        "total": total,
        "arrival": min(u["arrival_date"] for u in units),
        "departure": max(u["departure_date"] for u in units),
    }


def _describe_rooms(units: list[dict]) -> str:
    parts = []
    for u in units:
        who = _plural(u["adults"], "adult")
        if u["children"]:
            who += ", " + _plural(u["children"], "child", "ren")
        parts.append(f"{u['room_type']} — {who}")
    return "; ".join(parts)


def _policy_line(head: dict) -> str:
    """The cancellation terms, in the property's own words where it has them.

    Where it has none, the terms are described from the same two numbers the
    cancel quote does its arithmetic with, and described to mean the same
    thing. ``change_routes`` charges a penalty only when
    ``days_before < free_until_days``, so ``free_until_days = 0`` is the most
    generous setting there is -- free right up to arrival -- and not, as it
    reads at a glance, the least. Getting that backwards would tell every guest
    on a policy named "Flexible" that they had no way out.
    """
    written = (head["policy_text"] or "").strip()
    if written:
        return written
    days = head["free_until_days"]
    if days is None:
        return ""

    nights = head.get("penalty_nights") or 0
    charge = (f" After that, {_plural(nights, 'night')} is charged."
              if nights == 1 else
              f" After that, {_plural(nights, 'night')} are charged."
              if nights else "")
    if days == 0:
        # Nothing is ever inside the window, so there is no "after that".
        return "You may cancel free of charge any time before you arrive."
    return (f"Free cancellation until {_plural(days, 'day')} before you "
            f"arrive.{charge}")


def compose(data: dict) -> tuple[str, str, str]:
    """Build ``(subject, text, html)``."""
    h, units = data["head"], data["units"]
    arrival, departure = data["arrival"], data["departure"]
    symbol = "₹" if (h["currency"] or "INR") == "INR" else ""
    nights = (departure - arrival).days
    first_name = (h["guest_name"] or "").split()[0] if h["guest_name"] else ""
    greeting = f"Hello {first_name}," if first_name else "Hello,"

    subject = (f"Booking confirmed — {h['number']} · "
               f"{h['property_name']}, {arrival.day} "
               f"{_MONTHS[arrival.month - 1][:3]}")

    where = ", ".join(x for x in (h["address_line"], h["city"], h["state"],
                                  h["postal_code"]) if x)
    reach = " · ".join(
        x for x in (h["contact_phone"], h["contact_email"]) if x)
    checkin, checkout = _clock(h["checkin_time"]), _clock(h["checkout_time"])
    rooms = _describe_rooms(units)
    policy = _policy_line(h)

    # ---- plain text. Sent alongside the HTML and written to be read: some
    # clients show it, and it is what a screen reader reaches for.
    lines = [
        greeting,
        "",
        f"Your booking at {h['property_name']} is confirmed. Please keep the "
        f"number below — it is how we find your booking.",
        "",
        f"Confirmation number: {h['number']}",
        "",
        f"Check in:  {_long_date(arrival)}"
        + (f", from {checkin}" if checkin else ""),
        f"Check out: {_long_date(departure)}"
        + (f", by {checkout}" if checkout else ""),
        f"Length:    {_plural(nights, 'night')}",
        f"Room:      {rooms}",
    ]
    if data["total"] is not None:
        lines.append(f"Stay total: {_money(data['total'], symbol)}")
    if h["special_requests"]:
        lines += [
            "",
            f"You asked us for: {h['special_requests']}",
            "We will do our best, though we cannot promise it in advance.",
        ]
    if where:
        lines += ["", "Getting here:", where]
    if reach:
        lines += ["", f"Reach us on {reach}."]
    if policy:
        lines += ["", "Cancellation:", policy]
    lines += ["", "We look forward to having you.", h["property_name"]]
    body = "\n".join(lines)

    # ---- HTML. Tables and inline styles on purpose: mail clients strip
    # <style> blocks and know nothing of flexbox, so a modern layout here
    # arrives as one long column of unstyled text.
    def row(label: str, value: str) -> str:
        return (
            "<tr>"
            '<td style="padding:7px 16px 7px 0;color:#64748b;font-size:13px;'
            'white-space:nowrap;vertical-align:top">' + label + "</td>"
            '<td style="padding:7px 0;color:#0f172a;font-size:14px;'
            'font-weight:500">' + value + "</td></tr>"
        )

    def faint(s: str) -> str:
        return ('<br><span style="color:#64748b;font-weight:400">' + s
                + "</span>")

    rows = [
        row("Check in", _esc(_long_date(arrival))
            + (faint("from " + _esc(checkin)) if checkin else "")),
        row("Check out", _esc(_long_date(departure))
            + (faint("by " + _esc(checkout)) if checkout else "")),
        row("Length", _plural(nights, "night")),
        row("Room", _esc(rooms)),
    ]
    if data["total"] is not None:
        rows.append(row("Stay total", _esc(_money(data["total"], symbol))))

    def section(title: str, inner: str) -> str:
        return (
            '<tr><td style="padding:22px 28px 0">'
            '<div style="font-size:12px;text-transform:uppercase;'
            'letter-spacing:.06em;color:#64748b;font-weight:700">' + title
            + "</div>" + inner + "</td></tr>"
        )

    blocks = ""
    if h["special_requests"]:
        blocks += (
            '<tr><td style="padding:20px 28px 0">'
            '<div style="background:#f8fafc;border-left:3px solid #0f766e;'
            'padding:12px 14px;border-radius:0 6px 6px 0">'
            '<div style="font-size:12px;text-transform:uppercase;'
            'letter-spacing:.06em;color:#0f766e;font-weight:700">'
            "Your request</div>"
            '<div style="font-size:14px;color:#334155;margin-top:5px">'
            + _esc(h["special_requests"]) + "</div>"
            '<div style="font-size:12px;color:#94a3b8;margin-top:6px">'
            "We will do our best, though we cannot promise it in advance."
            "</div></div></td></tr>"
        )
    if where or reach:
        inner = ('<div style="font-size:14px;color:#0f172a;margin-top:6px;'
                 'line-height:1.55">' + _esc(where) + "</div>") if where else ""
        if reach:
            inner += ('<div style="font-size:14px;color:#334155;'
                      'margin-top:8px">' + _esc(reach) + "</div>")
        blocks += section("Getting here", inner)
    if policy:
        blocks += section(
            "Cancellation",
            '<div style="font-size:13px;color:#475569;margin-top:6px;'
            'line-height:1.6">' + _esc(policy) + "</div>")

    html = (
        '<div style="margin:0;padding:24px 12px;background:#f1f5f9">'
        '<table role="presentation" cellpadding="0" cellspacing="0" border="0"'
        ' style="max-width:560px;margin:0 auto;background:#ffffff;'
        "border-radius:12px;overflow:hidden;font-family:-apple-system,"
        "BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;"
        'box-shadow:0 1px 3px rgba(15,23,42,.1)">'
        '<tr><td style="background:#003a49;padding:26px 28px">'
        '<div style="color:#7fd8cc;font-size:12px;text-transform:uppercase;'
        'letter-spacing:.1em;font-weight:700">Booking confirmed</div>'
        '<div style="color:#ffffff;font-size:21px;font-weight:600;'
        'margin-top:6px">' + _esc(h["property_name"]) + "</div></td></tr>"
        '<tr><td style="padding:24px 28px 0">'
        '<p style="margin:0;font-size:15px;color:#0f172a;line-height:1.6">'
        + _esc(greeting) + " your stay is confirmed. Please keep the number "
        "below "
        "— it is how we find your booking.</p></td></tr>"
        '<tr><td style="padding:16px 28px 0">'
        '<div style="border:1px solid #dcf9f5;background:#f0fdfa;'
        'border-radius:8px;padding:14px 16px;text-align:center">'
        '<div style="font-size:11px;text-transform:uppercase;'
        'letter-spacing:.08em;color:#0f766e;font-weight:700">'
        "Confirmation number</div>"
        '<div style="font-size:24px;font-weight:700;color:#003a49;'
        'letter-spacing:.04em;margin-top:4px">' + _esc(h["number"])
        + "</div></div></td></tr>"
        '<tr><td style="padding:20px 28px 0">'
        '<table role="presentation" cellpadding="0" cellspacing="0" border="0"'
        ' width="100%">' + "".join(rows) + "</table></td></tr>"
        + blocks
        + '<tr><td style="padding:26px 28px 28px">'
        '<p style="margin:0;font-size:14px;color:#334155">We look forward to '
        "having you.</p>"
        '<p style="margin:4px 0 0;font-size:14px;color:#0f172a;'
        'font-weight:600">' + _esc(h["property_name"]) + "</p></td></tr>"
        "</table></div>"
    )
    return subject, body, html


def send_confirmation(session: Session,
                      reservation_id: uuid.UUID) -> str | None:
    """Mail the guest. Never raises; returns the address written to, or None.

    Swallowing the failure is deliberate. The booking is already committed and
    the guest's money may already be taken; an unreachable mail server must not
    turn a confirmed stay into an error somebody has to unpick. It is logged at
    warning level, and the audit trail records only what actually went out.
    """
    try:
        data = gather(session, reservation_id)
    except Exception as exc:  # noqa: BLE001
        log.warning("could not assemble confirmation for %s: %s",
                    reservation_id, exc)
        return None
    if data is None:
        return None

    number = data["head"]["number"]
    try:
        subject, body, html = compose(data)
        send_mail(settings.mail_config, to=data["to"], subject=subject,
                  text=body, html=html)
    except MailNotConfigured as exc:
        log.warning("guest confirmation for %s not sent: %s", number, exc)
        return None
    except Exception as exc:  # noqa: BLE001
        log.warning("guest confirmation for %s to %s failed: %s",
                    number, data["to"], exc)
        return None
    log.info("guest confirmation for %s sent to %s", number, data["to"])
    return data["to"]
