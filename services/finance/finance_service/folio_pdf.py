"""The guest folio / tax invoice, as a printable document.

A folio is handed to a guest and filed by the property, and in India it is the
tax invoice for the stay. That makes it a document rather than a screen: it has
to look the same every time, print the same on any machine, and be reproducible
from the ledger months later. Leaving it to a browser's print dialog gives none
of those — the output changes with the window width, the browser and whatever
the user has set in the print options.

The layout follows the approved folio template: a serif wordmark over a gold
rule, cream-headed detail panels, and a navy charge table on white. It is
deliberately not the teal of the screens — this is stationery, and it reads as
a printed bill rather than as a screenshot of the software.

Every word identifying the property comes from ``finance.invoice_settings``
for that ``property_id`` -- legal name, tagline, address, phone, email, GSTIN
and the closing note. This is a multi-tenant system, so a resort's own name is
data, never a literal in here: a hard-coded fallback would print one tenant's
identity on another tenant's tax invoice, which is a compliance problem and not
merely a cosmetic one. A property that has not filled its details in gets a
document that says so rather than one that quietly borrows someone else's.

Money is formatted in the Indian convention (1,23,456.78) because that is what
a guest here reads without pausing.
"""

from __future__ import annotations

import io
from datetime import date
from decimal import Decimal

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas as pdfcanvas

# The house stationery — palette, page geometry, header, panels. Shared with
# the tax invoice so the two documents cannot drift apart.
from .stationery import (
    AMBER, AMBER_BG, BODY_W, CREAM, GOLD, GREEN, GREEN_BG, INK, LINE, MARGIN,
    MUTED, NAVY, NAVY_DEEP, PAGE_H, PAGE_W, ROW_ALT, _Doc, _day,
    _figures_panel, _header, _panel, rupees,
)
from .stationery import title_block as _title_block

def _title(d: _Doc, ctx: dict) -> None:
    # A booking can carry several folios, and on a stay split between a company
    # and its guest the reservation number is on both documents. The folio
    # number is the only thing on the page that says *which bill this is*, so
    # it is printed rather than left for somebody to work out.
    _title_block(d, "GUEST FOLIO / TAX INVOICE", [
        ("Invoice No.", ctx.get("invoice_number")
         or ctx.get("reservation_number") or "-"),
        ("Invoice Date", _day(ctx.get("invoice_date") or date.today())),
        ("Folio No.", ctx.get("folio_no") or "-"),
        ("Reservation No.", ctx.get("reservation_number") or "-"),
    ])


def _parties(d: _Doc, ctx: dict) -> None:
    gap = 0  # the two panels butt together, as on the template
    w = BODY_W / 2
    nights = ctx.get("nights")
    # Billed to whoever this folio was opened for, falling back to the guest on
    # the booking. A folio opened for "Ravi Kumar (guest)" so he can put dinner
    # on his own GSTIN must not print the company's name at the top.
    billed_to = ctx.get("sharer_name") or ctx.get("guest_name") or "-"
    # The folio's own GSTIN wins over the commercial account's. They are
    # different parties and, on a split booking, deliberately different
    # numbers: the account's belongs on the company's folio, this one on the
    # folio it was typed against.
    gstin = ctx.get("folio_gstin") or ctx.get("account_gstin") or "-"
    left = [
        ("Billed to", billed_to),
        ("Guest", ctx.get("guest_name") or "-"),
        ("Company", ctx.get("account_name") or "-"),
        ("GSTIN", gstin),
        ("Mobile", ctx.get("phone") or "-"),
    ]
    right = [
        ("Room", " - ".join(x for x in (ctx.get("room_code"), ctx.get("room_type")) if x) or "-"),
        ("Check-in", _day(ctx.get("arrival_date"))),
        ("Check-out", _day(ctx.get("departure_date"))),
        ("Length of stay", f"{nights} Night{'' if nights == 1 else 's'}" if nights else "-"),
    ]
    h1 = _panel(d.c, MARGIN, d.y, w, "GUEST DETAILS", left)
    h2 = _panel(d.c, MARGIN + w + gap, d.y, w, "STAY DETAILS", right)
    d.y -= max(h1, h2)
    d.gap(4)


def _status(d: _Doc, ctx: dict, balance: Decimal) -> None:
    c = d.c
    status = ctx.get("unit_status")
    if status == "checked_out":
        label = "CHECKED OUT"
        note = f"Room {ctx.get('room_code') or ''} has been released.".strip()
    elif status == "checked_in":
        label, note = "IN HOUSE", "The guest is currently in the room."
    else:
        label, note = "RESERVED", "The stay has not started."

    settled = balance <= 0
    h = 8.5 * mm
    c.setFillColor(GREEN_BG if settled else AMBER_BG)
    c.setStrokeColor(LINE)
    c.setLineWidth(0.6)
    c.rect(MARGIN, d.y - h, BODY_W, h, stroke=1, fill=1)

    c.setFont("Helvetica-Bold", 8.5)
    c.setFillColor(NAVY_DEEP)
    c.drawString(MARGIN + 4 * mm, d.y - 5.5 * mm, label)
    c.setFont("Helvetica", 8.5)
    c.setFillColor(INK)
    c.drawString(MARGIN + 32 * mm, d.y - 5.5 * mm, note)
    tail = f"Balance due: Rs. {rupees(balance)}"
    c.setFont("Helvetica-Bold", 8.5)
    c.setFillColor(GREEN if settled else AMBER)
    c.drawString(MARGIN + 92 * mm, d.y - 5.5 * mm, tail)
    d.y -= h
    d.gap(5)


def _table(d: _Doc, lines: list[dict]) -> None:
    c = d.c
    c.setFont("Helvetica-Bold", 10.5)
    c.setFillColor(NAVY)
    c.drawString(MARGIN, d.y, "FOLIO DETAILS")
    d.gap(5)

    xs = [MARGIN, MARGIN + 26 * mm, MARGIN + 96 * mm, MARGIN + 126 * mm,
          MARGIN + 142 * mm, PAGE_W - MARGIN]
    heads = ["Date", "Description", "Department", "Qty", "Amount (Rs.)"]

    def head_row():
        c.setFillColor(NAVY)
        c.rect(MARGIN, d.y - 7 * mm, BODY_W, 7 * mm, stroke=0, fill=1)
        c.setFont("Helvetica-Bold", 8)
        c.setFillColor(colors.white)
        for i, h in enumerate(heads[:-1]):
            c.drawString(xs[i] + 2.5 * mm, d.y - 4.8 * mm, h)
        c.drawRightString(xs[-1] - 2.5 * mm, d.y - 4.8 * mm, heads[-1])
        d.y -= 7 * mm

    head_row()
    for n, ln in enumerate(lines):
        d.need(16)
        if d.y > PAGE_H - MARGIN - 8 * mm:       # continued on a new page
            head_row()
        row_h = 5.8 * mm
        if n % 2:
            c.setFillColor(ROW_ALT)
            c.rect(MARGIN, d.y - row_h, BODY_W, row_h, stroke=0, fill=1)
        c.setStrokeColor(LINE)
        c.setLineWidth(0.4)
        c.line(MARGIN, d.y - row_h, PAGE_W - MARGIN, d.y - row_h)

        credit = ln.get("entry_type") == "credit"
        base = d.y - 4.3 * mm
        c.setFont("Helvetica", 8)
        c.setFillColor(INK)
        c.drawString(xs[0] + 2.5 * mm, base, _day(ln.get("business_date")))
        c.drawString(xs[1] + 2.5 * mm, base, str(ln.get("description") or "")[:48])
        c.drawString(xs[2] + 2.5 * mm, base, str(ln.get("department") or ""))
        c.drawString(xs[3] + 2.5 * mm, base, str(ln.get("qty") or 1))
        # A payment reduces the balance, so it prints as a negative rather
        # than as another charge.
        amt = Decimal(ln.get("amount") or 0)
        c.drawRightString(xs[-1] - 2.5 * mm, base,
                          f"-{rupees(amt)}" if credit else rupees(amt))
        d.y -= row_h

    if not lines:
        c.setFont("Helvetica-Oblique", 8)
        c.setFillColor(MUTED)
        c.drawString(MARGIN + 2.5 * mm, d.y - 4.5 * mm,
                     "Nothing has been posted to this folio.")
        d.y -= 7 * mm
    d.gap(5)


def _totals(d: _Doc, totals: dict, tax_lines: list[dict],
            itemise_tax: bool = True) -> None:
    c = d.c
    d.need(50)
    # Half the width when the tax panel sits beside it; the whole width when it
    # does not, rather than a money panel floating next to white space.
    w = BODY_W / 2 if itemise_tax else BODY_W

    h1 = 0.0
    if itemise_tax:
        tax_rows = [
            ("Taxable room value",
             f"Rs. {rupees(totals.get('subtotal'))}", False),
            ("Taxes & charges", f"Rs. {rupees(totals.get('taxes'))}", False),
        ]
        for t in tax_lines:
            tax_rows.append(
                (str(t.get("code")), f"Rs. {rupees(t.get('amount'))}", False))
        if not tax_lines:
            # What the template falls back to when nothing has been posted.
            tax_rows.append(("CGST / SGST / IGST", "As applicable", False))
        h1 = _figures_panel(c, MARGIN, d.y, BODY_W / 2, "TAX BREAKUP", tax_rows)

    x = MARGIN + (BODY_W / 2 if itemise_tax else 0)
    if itemise_tax:
        money_rows = [
            ("Subtotal", f"Rs. {rupees(totals.get('subtotal'))}", False),
            ("Taxes & Charges", f"Rs. {rupees(totals.get('taxes'))}", False),
            ("GRAND TOTAL", f"Rs. {rupees(totals.get('grand_total'))}", True),
            ("Advance / Payments",
             f"- Rs. {rupees(abs(Decimal(totals.get('advance_paid') or 0)))}",
             True),
        ]
    else:
        # One figure, tax already inside it -- and said so, because a total
        # that does not state whether tax is included is the line guests
        # query. The subtotal and the tax are deliberately absent: printing
        # them here would itemise the tax this folio was set not to itemise.
        money_rows = [
            ("TOTAL (incl. taxes)",
             f"Rs. {rupees(totals.get('grand_total'))}", True),
            ("Advance / Payments",
             f"- Rs. {rupees(abs(Decimal(totals.get('advance_paid') or 0)))}",
             True),
        ]
    head_h, row_h = 6.8 * mm, 5.0 * mm
    body_h = 3.4 * mm + len(money_rows) * row_h + 8.5 * mm
    h2 = head_h + body_h

    c.setFillColor(CREAM)
    c.setStrokeColor(LINE)
    c.setLineWidth(0.6)
    c.rect(x, d.y - head_h, w, head_h, stroke=1, fill=1)
    c.setFillColor(colors.white)
    c.rect(x, d.y - h2, w, body_h, stroke=1, fill=1)
    c.setFont("Helvetica-Bold", 10.5)
    c.setFillColor(NAVY)
    c.drawString(x + 4 * mm, d.y - 5.2 * mm, "PAYMENT SUMMARY")

    ty = d.y - head_h - 5 * mm
    for label, value, strong in money_rows:
        font = "Helvetica-Bold" if strong else "Helvetica"
        size = 9 if strong else 8.2
        c.setFont(font, size)
        c.setFillColor(NAVY_DEEP if strong else NAVY)
        c.drawString(x + 4 * mm, ty, label)
        c.setFont("Helvetica-Bold", size)
        c.drawRightString(x + w - 4 * mm, ty, value)
        ty -= row_h

    bal = Decimal(totals.get("balance") or 0)
    settled = bal <= 0
    c.setFillColor(CREAM)
    c.setStrokeColor(LINE)
    c.rect(x, d.y - h2, w, 8.5 * mm, stroke=1, fill=1)
    c.setFont("Helvetica-Bold", 10)
    c.setFillColor(NAVY_DEEP)
    c.drawString(x + 4 * mm, d.y - h2 + 3 * mm, "BALANCE DUE")
    c.setFillColor(GREEN if settled else AMBER)
    c.drawRightString(x + w - 4 * mm, d.y - h2 + 3 * mm, f"Rs. {rupees(bal)}")

    d.y -= max(h1, h2)
    d.gap(4)


def _closing(d: _Doc, sett: dict, totals: dict) -> None:
    c = d.c
    d.need(38)
    w = BODY_W / 2
    bal = Decimal(totals.get("balance") or 0)

    h1 = _panel(c, MARGIN, d.y, w, "PAYMENT DETAILS", [
        ("Method", "Advance / settled" if bal <= 0 else "Balance outstanding"),
        ("Status", "Paid" if bal <= 0 else "Outstanding"),
        ("Outstanding", f"Rs. {rupees(bal)}"),
    ])

    x = MARGIN + w
    name = (sett.get("legal_name") or "").strip()
    note = sett.get("footer_note") or (
        f"Thank you for staying with {name}. We hope to welcome you back soon."
        if name else "Thank you for your stay. We hope to welcome you back soon.")
    head_h = 7.5 * mm
    c.setFillColor(CREAM)
    c.setStrokeColor(LINE)
    c.setLineWidth(0.6)
    c.rect(x, d.y - head_h, w, head_h, stroke=1, fill=1)
    body_h = h1 - head_h
    c.setFillColor(colors.white)
    c.rect(x, d.y - h1, w, body_h, stroke=1, fill=1)
    c.setFont("Helvetica-Bold", 9)
    c.setFillColor(NAVY_DEEP)
    c.drawString(x + 4 * mm, d.y - 5.2 * mm, "NOTES")

    c.setFont("Helvetica", 8.5)
    c.setFillColor(INK)
    ty = d.y - head_h - 5 * mm
    line = ""
    for word in note.split():
        trial = f"{line} {word}".strip()
        if c.stringWidth(trial, "Helvetica", 8.5) > w - 8 * mm:
            c.drawString(x + 4 * mm, ty, line)
            ty -= 4.6 * mm
            line = word
        else:
            line = trial
    if line:
        c.drawString(x + 4 * mm, ty, line)

    d.y -= h1
    d.gap(13)

    # The template rules these with underscore glyphs rather than drawn
    # lines, which is what gives them their slightly informal weight.
    c.setFont("Helvetica", 10)
    c.setFillColor(INK)
    rule = "_" * 28
    c.drawString(MARGIN + 4 * mm, d.y, rule)
    c.drawRightString(PAGE_W - MARGIN - 4 * mm, d.y, rule)
    c.setFont("Helvetica", 7.7)
    c.setFillColor(MUTED)
    c.drawString(MARGIN + 4 * mm, d.y - 4.5 * mm, "Guest Signature")
    c.drawRightString(PAGE_W - MARGIN - 4 * mm, d.y - 4.5 * mm,
                      "Authorized Signatory")

    c.setStrokeColor(GOLD)
    c.setLineWidth(0.8)
    c.line(MARGIN, MARGIN + 6 * mm, PAGE_W - MARGIN, MARGIN + 6 * mm)
    c.setFont("Helvetica", 7.7)
    c.setFillColor(GOLD)
    # Built from what the property set, not from a strapline written here.
    strap = "  ·  ".join(x for x in (
        (sett.get("legal_name") or "").strip(),
        (sett.get("tagline") or "").strip(),
    ) if x)
    if strap:
        c.drawCentredString(PAGE_W / 2, MARGIN + 2 * mm, strap)


def render_folio_pdf(*, settings: dict, context: dict, lines: list[dict],
                     totals: dict, tax_lines: list[dict],
                     itemise_tax: bool = True) -> bytes:
    """Draw the folio and hand back the PDF bytes."""
    buf = io.BytesIO()
    c = pdfcanvas.Canvas(buf, pagesize=A4)
    c.setTitle(f"Guest Folio {context.get('reservation_number') or ''}".strip())
    c.setAuthor(settings.get("legal_name") or "")
    c.setSubject("Guest Folio / Tax Invoice")

    d = _Doc(c)
    _header(d, settings)
    _title(d, context)
    _parties(d, context)
    _status(d, context, Decimal(totals.get("balance") or 0))
    _table(d, lines)
    _totals(d, totals, tax_lines, itemise_tax)
    _closing(d, settings, totals)

    c.showPage()
    c.save()
    return buf.getvalue()
