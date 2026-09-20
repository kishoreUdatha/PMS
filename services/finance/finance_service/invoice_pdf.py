"""The tax invoice, as a printable document.

The folio is what a stay costs; the invoice is the legal record of that sale,
and in India it carries obligations the folio does not — a supplier GSTIN, the
place of supply, the tax split by rate, the amount in words. So it is a
separate document rather than the folio with a different heading, even though
it wears the same stationery.

Three things it will not do:

**It will not call itself a tax invoice unless it is one.** When the property
has not configured its GST registration, the heading reads INVOICE and a line
under it says plainly what is missing. Printing "TAX INVOICE" over a document
with no GSTIN would be a false claim on a legal record.

**It reports the tax that was posted**, never a rate recomputed at print time.
A document already handed to a guest must not change because somebody edited a
tax rate afterwards.

**It shows what was actually received**, including refunds and credit notes,
because a total that disagrees with its own rows is what starts arguments at
the desk.
"""

from __future__ import annotations

import io
from decimal import Decimal

from reportlab.lib import colors
from reportlab.pdfgen import canvas as pdfcanvas
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm

from .stationery import (
    AMBER, AMBER_BG, BODY_W, CREAM, GOLD, GREEN, GREEN_BG, INK, LINE, MARGIN,
    MUTED, NAVY, NAVY_DEEP, PAGE_H, PAGE_W, ROW_ALT, _Doc, _day,
    _figures_panel, _header, _panel, rupees, title_block,
)


def _money(v) -> str:
    return f"Rs. {rupees(v)}"


def _compliance_note(d: _Doc, compliance: dict) -> None:
    """Say, on the document itself, when it is not a GST tax invoice.

    A desk that cannot see this prints something that looks official and is
    not. It is a band rather than a footnote for the same reason.
    """
    if compliance.get("compliant"):
        return
    c = d.c
    note = compliance.get("note") or "This is not a GST tax invoice."
    h = 9 * mm
    c.setFillColor(AMBER_BG)
    c.setStrokeColor(LINE)
    c.setLineWidth(0.6)
    c.rect(MARGIN, d.y - h, BODY_W, h, stroke=1, fill=1)
    c.setFont("Helvetica-Bold", 8)
    c.setFillColor(AMBER)
    c.drawString(MARGIN + 4 * mm, d.y - 5.8 * mm, "NOT A GST TAX INVOICE")

    c.setFont("Helvetica", 7.6)
    c.setFillColor(NAVY_DEEP)
    # Trimmed to the width available rather than run under the page edge.
    text = note
    while c.stringWidth(text, "Helvetica", 7.6) > BODY_W - 56 * mm and len(text) > 8:
        text = text[:-2]
    if text != note:
        text = text.rstrip(" .,") + "…"
    c.drawString(MARGIN + 46 * mm, d.y - 5.8 * mm, text)
    d.y -= h
    d.gap(4)


def _parties(d: _Doc, inv: dict) -> None:
    """Supplier and customer, side by side, the way a tax invoice states them."""
    w = BODY_W / 2
    sup = inv.get("supplier") or {}
    cus = inv.get("customer") or {}

    def lines(p: dict) -> list[tuple[str, str]]:
        where = ", ".join(x for x in (p.get("city"), p.get("state")) if x)
        return [
            ("Name", p.get("name") or "-"),
            ("Address", p.get("address_line") or "-"),
            ("Place", where or "-"),
            ("GSTIN", p.get("gstin") or "Not registered"),
        ]

    h1 = _panel(d.c, MARGIN, d.y, w, "SUPPLIER", lines(sup))
    h2 = _panel(d.c, MARGIN + w, d.y, w, "BILL TO", lines(cus))
    d.y -= max(h1, h2)
    d.gap(4)


def _stay(d: _Doc, inv: dict) -> None:
    """The stay as one strip rather than a panel.

    It is four short facts, and giving them a titled four-row panel was what
    pushed the invoice onto a second page. A tax invoice belongs on one sheet.
    """
    c = d.c
    nights = inv.get("nights")
    room = " - ".join(x for x in (inv.get("room_code"), inv.get("room_type")) if x)
    bits = [
        ("Reservation", inv.get("reservation_number") or "-"),
        ("Room", room or "-"),
        ("Stay", f"{_day(inv.get('arrival_date'))} to "
                 f"{_day(inv.get('departure_date'))}"
         if inv.get("arrival_date") else "-"),
        ("Nights", f"{nights}" if nights is not None else "-"),
    ]
    h = 8 * mm
    c.setFillColor(CREAM)
    c.setStrokeColor(LINE)
    c.setLineWidth(0.6)
    c.rect(MARGIN, d.y - h, BODY_W, h, stroke=1, fill=1)

    x = MARGIN + 4 * mm
    base = d.y - 5.2 * mm
    for label, value in bits:
        c.setFont("Helvetica-Bold", 8)
        c.setFillColor(NAVY)
        c.drawString(x, base, f"{label}:")
        x += c.stringWidth(f"{label}:", "Helvetica-Bold", 8) + 1.6 * mm
        c.setFont("Helvetica", 8)
        c.setFillColor(NAVY_DEEP)
        c.drawString(x, base, str(value))
        x += c.stringWidth(str(value), "Helvetica", 8) + 6 * mm
    d.y -= h
    d.gap(4)


def _lines(d: _Doc, inv: dict) -> None:
    c = d.c
    c.setFont("Helvetica-Bold", 10.5)
    c.setFillColor(NAVY)
    c.drawString(MARGIN, d.y, "CHARGES")
    d.gap(5)

    xs = [MARGIN, MARGIN + 26 * mm, MARGIN + 108 * mm, MARGIN + 126 * mm,
          PAGE_W - MARGIN]
    heads = ["Date", "Description", "Qty", "Amount (Rs.)"]

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
    rows = inv.get("lines") or []
    for n, ln in enumerate(rows):
        d.need(16)
        if d.y > PAGE_H - MARGIN - 8 * mm:
            head_row()
        row_h = 5.8 * mm
        if n % 2:
            c.setFillColor(ROW_ALT)
            c.rect(MARGIN, d.y - row_h, BODY_W, row_h, stroke=0, fill=1)
        c.setStrokeColor(LINE)
        c.setLineWidth(0.4)
        c.line(MARGIN, d.y - row_h, PAGE_W - MARGIN, d.y - row_h)

        base = d.y - 4.3 * mm
        c.setFont("Helvetica", 8)
        c.setFillColor(INK)
        c.drawString(xs[0] + 2.5 * mm, base, _day(ln.get("business_date")))
        c.drawString(xs[1] + 2.5 * mm, base, str(ln.get("description") or "")[:52])
        qty = ln.get("quantity")
        c.drawString(xs[2] + 2.5 * mm, base,
                     f"{Decimal(qty or 1):g}" if qty is not None else "1")
        c.drawRightString(xs[-1] - 2.5 * mm, base, rupees(ln.get("amount")))
        d.y -= row_h

    if not rows:
        c.setFont("Helvetica-Oblique", 8)
        c.setFillColor(MUTED)
        c.drawString(MARGIN + 2.5 * mm, d.y - 4.5 * mm,
                     "Nothing has been charged on this invoice.")
        d.y -= 7 * mm
    d.gap(5)


def _totals(d: _Doc, inv: dict) -> None:
    c = d.c
    d.need(52)
    w = BODY_W / 2

    # --- tax break-up: what was posted, by code ---
    tax_rows: list[tuple[str, str, bool]] = []
    for t in inv.get("tax_lines") or []:
        rate = Decimal(t.get("rate") or 0)
        label = f"{t.get('code')} @ {rate:g}%" if rate else str(t.get("code"))
        tax_rows.append((label, _money(t.get("tax_amount")), False))
    if not tax_rows:
        tax_rows.append(("No tax posted", "Rs. 0.00", False))
    tax_rows.insert(0, ("Taxable value", _money(inv.get("subtotal")), False))
    h1 = _figures_panel(c, MARGIN, d.y, w, "TAX BREAKUP", tax_rows)

    # --- the money column ---
    received = Decimal(inv.get("amount_received") or 0)
    credited = Decimal(inv.get("credited") or 0)
    rows = [
        ("Subtotal", _money(inv.get("subtotal")), False),
        ("Taxes & Charges", _money(inv.get("tax_total")), False),
        ("GRAND TOTAL", _money(inv.get("total")), True),
        ("Received", f"- {_money(received)}", True),
    ]
    if credited:
        rows.append(("Credit notes", f"- {_money(credited)}", True))

    x = MARGIN + w
    head_h, row_h = 6.8 * mm, 5.0 * mm
    body_h = 3.4 * mm + len(rows) * row_h + 8.5 * mm
    h2 = head_h + body_h

    c.setFillColor(CREAM)
    c.setStrokeColor(LINE)
    c.setLineWidth(0.6)
    c.rect(x, d.y - head_h, w, head_h, stroke=1, fill=1)
    c.setFillColor(colors.white)
    c.rect(x, d.y - h2, w, body_h, stroke=1, fill=1)
    c.setFont("Helvetica-Bold", 10.5)
    c.setFillColor(NAVY)
    c.drawString(x + 4 * mm, d.y - 5.2 * mm, "SUMMARY")

    ty = d.y - head_h - 5 * mm
    for label, value, strong in rows:
        size = 9 if strong else 8.2
        c.setFont("Helvetica-Bold" if strong else "Helvetica", size)
        c.setFillColor(NAVY_DEEP if strong else NAVY)
        c.drawString(x + 4 * mm, ty, label)
        c.setFont("Helvetica-Bold", size)
        c.drawRightString(x + w - 4 * mm, ty, value)
        ty -= row_h

    bal = Decimal(inv.get("balance_due") or 0)
    settled = bal <= 0
    c.setFillColor(CREAM)
    c.setStrokeColor(LINE)
    c.rect(x, d.y - h2, w, 8.5 * mm, stroke=1, fill=1)
    c.setFont("Helvetica-Bold", 10)
    c.setFillColor(NAVY_DEEP)
    c.drawString(x + 4 * mm, d.y - h2 + 3 * mm, "BALANCE DUE")
    c.setFillColor(GREEN if settled else AMBER)
    c.drawRightString(x + w - 4 * mm, d.y - h2 + 3 * mm, _money(bal))

    d.y -= max(h1, h2)
    d.gap(4)

    # --- amount in words: required on an Indian invoice, and the line a
    #     guest actually reads to check the figure ---
    words = inv.get("amount_in_words")
    if words:
        h = 8 * mm
        c.setFillColor(CREAM)
        c.setStrokeColor(LINE)
        c.rect(MARGIN, d.y - h, BODY_W, h, stroke=1, fill=1)
        c.setFont("Helvetica-Bold", 8)
        c.setFillColor(NAVY)
        c.drawString(MARGIN + 4 * mm, d.y - 5.2 * mm, "Amount in words")
        c.setFont("Helvetica", 8.2)
        c.setFillColor(NAVY_DEEP)
        c.drawString(MARGIN + 32 * mm, d.y - 5.2 * mm, str(words))
        d.y -= h
        d.gap(4)


def _payments(d: _Doc, inv: dict) -> None:
    rows = inv.get("payments") or []
    if len(rows) < 2:
        # A single payment is already stated as "Received" in the summary;
        # repeating it in its own panel costs a block and says nothing new.
        return
    d.need(30)
    lines = [
        (f"{_day(p.get('received_at'))} · {p.get('method') or ''}".strip(" ·"),
         _money(p.get("amount")), False)
        for p in rows[:6]
    ]
    h = _figures_panel(d.c, MARGIN, d.y, BODY_W, "PAYMENTS RECEIVED", lines)
    d.y -= h
    d.gap(4)


def _closing(d: _Doc, sett: dict, inv: dict) -> None:
    c = d.c
    # Only what this block actually draws: a note line and a signature rule.
    # Reserving 30mm for 14mm of content was enough to tip an otherwise
    # one-page invoice onto a second sheet.
    d.need(16)
    note = inv.get("notes") or sett.get("footer_note")
    if note:
        c.setFont("Helvetica", 8)
        c.setFillColor(MUTED)
        c.drawString(MARGIN, d.y, str(note)[:120])
        d.gap(6)

    d.gap(4)
    c.setFont("Helvetica", 10)
    c.setFillColor(INK)
    rule = "_" * 28
    c.drawRightString(PAGE_W - MARGIN - 4 * mm, d.y, rule)
    c.setFont("Helvetica", 7.7)
    c.setFillColor(MUTED)
    c.drawRightString(PAGE_W - MARGIN - 4 * mm, d.y - 4.5 * mm,
                      "Authorized Signatory")
    c.setFont("Helvetica-Oblique", 7.7)
    c.drawString(MARGIN, d.y - 4.5 * mm,
                 "Computer generated invoice.")

    c.setStrokeColor(GOLD)
    c.setLineWidth(0.8)
    c.line(MARGIN, MARGIN + 6 * mm, PAGE_W - MARGIN, MARGIN + 6 * mm)
    strap = "  ·  ".join(x for x in (
        (sett.get("legal_name") or "").strip(),
        (sett.get("tagline") or "").strip(),
    ) if x)
    if strap:
        c.setFont("Helvetica", 7.7)
        c.setFillColor(GOLD)
        c.drawCentredString(PAGE_W / 2, MARGIN + 2 * mm, strap)


def render_invoice_pdf(*, settings: dict, invoice: dict) -> bytes:
    """Draw the tax invoice and hand back the PDF bytes."""
    compliance = invoice.get("tax_compliance") or {}
    compliant = bool(compliance.get("compliant"))
    heading = "TAX INVOICE" if compliant else "INVOICE"

    buf = io.BytesIO()
    c = pdfcanvas.Canvas(buf, pagesize=A4)
    c.setTitle(f"{heading} {invoice.get('display_number') or ''}".strip())
    c.setAuthor(settings.get("legal_name") or "")
    c.setSubject(heading)

    d = _Doc(c)
    _header(d, settings)
    title_block(d, heading, [
        ("Invoice No.", invoice.get("display_number") or "Draft"),
        ("Invoice Date", _day(invoice.get("issued_at")
                              or invoice.get("created_at"))),
        ("Status", str(invoice.get("status") or "").title()),
    ])
    _compliance_note(d, compliance)
    _parties(d, invoice)
    _stay(d, invoice)
    _lines(d, invoice)
    _totals(d, invoice)
    _payments(d, invoice)
    _closing(d, settings, invoice)

    c.showPage()
    c.save()
    return buf.getvalue()
