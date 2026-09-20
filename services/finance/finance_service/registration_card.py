"""The guest registration card, as a printable document.

The form a guest checks and signs at the desk on arrival, and the one a police
or excise inspection asks the property to produce. It is pre-filled from what
is already known so the guest corrects rather than writes: the name, address,
nationality, date of birth, occupation and ID already sit on the guest record,
and the room, dates and rate on the reservation.

Three things shape the layout, and each is a rule about paper rather than
about screens.

**A blank is a line to write on, not missing data.** Everywhere else in this
system an empty value prints as an em dash, because a dash says "there is
nothing here" and stops somebody hunting for it. On a form that is exactly
wrong: the guest is holding a pen, and a dash tells them the field is closed.
So the card rules a line and leaves it, which is how the paper version has
always handled the field nobody filled in beforehand.

**The declaration and the signature are the point.** Everything above them is
convenience; the card exists so there is a signed record that this person
stayed on these dates and gave this identity. Both are laid out so they cannot
be pushed onto a second page by a long address.

**The ID number is printed as it is held.** Masking it here would defeat the
document -- an inspection is checking the number against the guest's card --
but this is also why the route that renders it is behind ``front_desk`` and
why it is not a link anyone can share.

It shares ``stationery`` with the folio and the tax invoice, so the property's
letterhead is the same on all three and its identity is read from settings
rather than written into any of them. A hard-coded fallback would print one
tenant's name on another tenant's legal record.
"""

from __future__ import annotations

import io
from datetime import date

from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas as pdfcanvas

from .stationery import (
    BODY_W,
    CREAM,
    FAINT,
    GOLD,
    LINE,
    MARGIN,
    MUTED,
    NAVY,
    NAVY_DEEP,
    PAGE_W,
    _Doc,
    _day,
    _header,
    title_block,
)

#: What the guest is asked to confirm by signing. Deliberately plain: a
#: declaration nobody reads protects nobody, and this one is short enough to
#: be read at a desk with a queue behind you.
DECLARATION = (
    "I confirm that the details above are correct, that the identification "
    "presented is my own, and that I accept the property's house rules, "
    "tariff and cancellation policy for this stay."
)

FOREIGN_NOTE = (
    "Foreign national — passport, visa and arrival details recorded. "
    "Form C is to be filed with the Bureau of Immigration within 24 hours "
    "of arrival."
)


#: Vertical pitch of one field row. Tuned so the whole card is one page:
#: the declaration and the signature belong with the details the guest is
#: signing for, and a form that breaks across two sheets gets one of them
#: filed and the other lost.
ROW = 9.0


def _line_field(c, x, y, width, label, value):
    """One labelled field, with a rule under it whether or not it is filled."""
    c.setFont("Helvetica", 6.6)
    c.setFillColor(MUTED)
    c.drawString(x, y, label.upper())
    c.setFont("Helvetica", 9)
    c.setFillColor(NAVY_DEEP)
    if value:
        c.drawString(x, y - 4.6 * mm, str(value)[:70])
    # The rule is drawn either way: an empty field is somewhere to write.
    c.setStrokeColor(LINE)
    c.setLineWidth(0.4)
    c.line(x, y - 5.8 * mm, x + width, y - 5.8 * mm)


def _section(d: _Doc, title: str, fields: list[tuple[str, str | None]],
             per_row: int = 2) -> None:
    """A titled block of fields, laid out in columns."""
    c = d.c
    d.need(34)
    c.setFillColor(CREAM)
    c.rect(MARGIN, d.y - 6.4 * mm, BODY_W, 6.4 * mm, stroke=0, fill=1)
    c.setFont("Helvetica-Bold", 8.4)
    c.setFillColor(NAVY)
    c.drawString(MARGIN + 3 * mm, d.y - 4.6 * mm, title.upper())
    d.gap(10)

    gap = 6 * mm
    width = (BODY_W - gap * (per_row - 1)) / per_row
    for i in range(0, len(fields), per_row):
        row = fields[i:i + per_row]
        for j, (label, value) in enumerate(row):
            _line_field(c, MARGIN + j * (width + gap), d.y, width, label, value)
        d.gap(ROW)
    d.gap(1.5)


def _declaration(d: _Doc, foreign: bool) -> None:
    c = d.c
    # What this block actually uses, not a round number: heading and gap,
    # two wrapped lines, the signature rules and their labels — plus the
    # foreign-national note only when there is one. Asking for more than it
    # needs is what pushed the signature onto a second sheet.
    d.need(46 if foreign else 39)
    if foreign:
        c.setFont("Helvetica-Oblique", 7.6)
        c.setFillColor(GOLD)
        c.drawString(MARGIN, d.y, FOREIGN_NOTE)
        d.gap(7)

    c.setFont("Helvetica-Bold", 8.4)
    c.setFillColor(NAVY)
    c.drawString(MARGIN, d.y, "DECLARATION")
    d.gap(6)

    c.setFont("Helvetica", 7.8)
    c.setFillColor(NAVY_DEEP)
    # Wrapped by hand rather than with a Paragraph: the rest of this document
    # is drawn on a cursor, and mixing flowables in would make the vertical
    # arithmetic two different things.
    words, line = DECLARATION.split(), ""
    for word in words:
        trial = f"{line} {word}".strip()
        if c.stringWidth(trial, "Helvetica", 7.8) > BODY_W:
            c.drawString(MARGIN, d.y, line)
            d.gap(4.4)
            line = word
        else:
            line = trial
    if line:
        c.drawString(MARGIN, d.y, line)
        d.gap(4.4)
    d.gap(10)

    # Two signature rules, far enough apart that neither is signed by mistake.
    half = (BODY_W - 20 * mm) / 2
    c.setStrokeColor(NAVY_DEEP)
    c.setLineWidth(0.6)
    for i, label in enumerate(("Guest signature", "For the property")):
        x = MARGIN + i * (half + 20 * mm)
        c.line(x, d.y, x + half, d.y)
        c.setFont("Helvetica", 7)
        c.setFillColor(MUTED)
        c.drawString(x, d.y - 4 * mm, label)
    d.gap(12)


def render_registration_card(*, settings: dict, context: dict) -> bytes:
    """Draw the registration card and hand back the PDF bytes."""
    buf = io.BytesIO()
    c = pdfcanvas.Canvas(buf, pagesize=A4)
    number = context.get("reservation_number") or ""
    c.setTitle(f"Guest Registration Card {number}".strip())
    c.setAuthor(settings.get("legal_name") or "")
    c.setSubject("Guest Registration Card")

    d = _Doc(c)
    _header(d, settings)
    title_block(d, "Guest Registration Card", [
        ("Booking", number or "—"),
        ("Room", context.get("room_code") or "Not assigned"),
        ("Printed", _day(date.today())),
    ])

    _section(d, "Guest", [
        ("Full name", context.get("full_name")),
        ("Date of birth", _day(context.get("date_of_birth"))
            if context.get("date_of_birth") else None),
        ("Nationality", context.get("nationality")),
        ("Occupation", context.get("occupation")),
        ("Phone", context.get("phone")),
        ("Email", context.get("email")),
    ])

    _section(d, "Address", [
        ("Address", context.get("address_line")),
        ("City", context.get("city")),
        ("State", context.get("state")),
        ("Postal code", context.get("postal_code")),
        ("Country", context.get("country")),
        ("", None),
    ])

    _section(d, "Identification", [
        ("ID type", context.get("id_type_label")),
        ("ID number", context.get("id_number")),
        ("Verified on", _day(context.get("id_verified_at"))
            if context.get("id_verified_at") else None),
        ("Documents on file", context.get("documents_label")),
    ])

    _section(d, "Stay", [
        ("Arrival", _day(context.get("arrival_date"))),
        ("Departure", _day(context.get("departure_date"))),
        ("Nights", context.get("nights")),
        ("Room type", context.get("room_type")),
        ("Adults", context.get("adults")),
        ("Children", context.get("children")),
        # Blank on most cards, and that is the point: the desk writes them in.
        ("Vehicle number", context.get("vehicle_number")),
        ("Purpose of visit", context.get("purpose_of_visit")),
    ])

    _declaration(d, bool(context.get("foreign_national")))

    c.setFont("Helvetica", 6.6)
    c.setFillColor(FAINT)
    c.drawCentredString(
        PAGE_W / 2, MARGIN - 4 * mm,
        "Retained by the property as the record of this stay.")

    c.showPage()
    c.save()
    return buf.getvalue()
