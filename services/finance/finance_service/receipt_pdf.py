"""A receipt for one payment.

The folio is the bill for the stay; this is proof that one particular payment
was made. They are different documents answering different questions, and the
folio could not stand in for this one: a guest paying an advance in October for
a stay in December has nothing to be handed, because the folio at that moment is
a list of things not yet charged.

It is also what gets asked for after the fact. A company reimbursing an
employee wants the receipt for the ₹20,000, not the whole bill showing what else
was eaten; a guest disputing a card charge wants the reference on paper.

Built on the same stationery as the folio and the tax invoice, so the three
cannot drift apart -- one wordmark, one palette, one set of panels. The page is
deliberately short: a receipt that runs to a second page is a receipt somebody
has to staple.
"""

from __future__ import annotations

import io
import uuid
from decimal import Decimal


def receipt_number(payment_id: uuid.UUID | str,
                   stored: str | None = None) -> str:
    """The number printed on a payment receipt, defined once.

    ``stored`` is ``finance.payments.receipt_no``, which is where the answer
    lives: assigned by the database on insert from a per-property counter, and
    therefore the only version that is unique and can be searched for. Pass it
    whenever the row is already in hand.

    The fallback derives the old form from the id. It is kept for a payment
    read by id alone, and because the column is only as old as migration 0038 --
    it is not a second source of truth, and a row that has a number returns it.

    It was defined three times: twice here in the service, and a third time in
    the browser, which built ``CBR-RCP-20260917-713ACD36`` while the PDF handed
    the guest ``RCPT-713ACD36`` for the same payment. A guest ringing up to
    quote the number off their receipt was reading one the screen could not
    find.

    The browser's version also carried a hardcoded ``CBR-`` for Chirala Bay,
    which on a platform with many hotels printed one tenant's brand on every
    other tenant's receipt. Nothing in a number should name a property; the
    stationery around it already does.

    Deliberately NOT drawn from the invoice fiscal series: that series is for
    tax invoices, and spending a number from it on every advance would leave
    gaps in the sequence that an audit asks about. The payment's own id,
    shortened, is unique, stable, and owes nothing to a counter.
    """
    return stored or f"RCPT-{str(payment_id)[:8].upper()}"

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas as pdfcanvas

from .stationery import (
    BODY_W, CREAM, GREEN, GREEN_BG, INK, LINE, MARGIN, MUTED, NAVY, NAVY_DEEP,
    _Doc, _day, _header, _panel, rupees,
)
from .stationery import title_block as _title_block


def render_receipt_pdf(*, settings: dict, payment: dict,
                       context: dict) -> bytes:
    """Draw the receipt and hand back the PDF bytes.

    ``payment`` is the row from ``finance.payments`` plus its method label and
    the folios it was allocated across; ``context`` is who and which stay.
    """
    buf = io.BytesIO()
    c = pdfcanvas.Canvas(buf, pagesize=A4)
    c.setTitle(f"Payment Receipt {payment.get('receipt_no') or ''}".strip())
    c.setAuthor(settings.get("legal_name") or "")
    c.setSubject("Payment Receipt")

    d = _Doc(c)
    _header(d, settings)
    _title_block(d, "PAYMENT RECEIPT", [
        ("Receipt No.", payment.get("receipt_no") or "-"),
        ("Received On", _day(payment.get("received_at"))),
        ("Reservation No.", context.get("reservation_number") or "-"),
    ])

    w = BODY_W / 2
    left = [
        ("Received from", context.get("guest_name") or "-"),
        ("Room", " - ".join(
            x for x in (context.get("room_code"), context.get("room_type"))
            if x) or "-"),
        ("Folio", payment.get("folio_no") or "-"),
    ]
    # The reference is the whole point of a card or UPI receipt -- it is the
    # string the guest quotes when the payment is questioned -- so it is a
    # field here rather than a footnote. Cash has none and says so.
    right = [
        ("Method", payment.get("method_label") or "-"),
        ("Reference", payment.get("reference") or "Not applicable"),
        ("Status", (payment.get("status") or "succeeded").title()),
    ]
    h1 = _panel(c, MARGIN, d.y, w, "PAYMENT DETAILS", left)
    h2 = _panel(c, MARGIN + w, d.y, w, "INSTRUMENT", right)
    d.y -= max(h1, h2)
    d.gap(6)

    # The figure, given the room a receipt's one number deserves.
    amount = Decimal(str(payment.get("amount") or 0))
    box_h = 20 * mm
    c.setFillColor(GREEN_BG)
    c.setStrokeColor(LINE)
    c.setLineWidth(0.6)
    c.rect(MARGIN, d.y - box_h, BODY_W, box_h, stroke=1, fill=1)
    c.setFont("Helvetica", 9)
    c.setFillColor(MUTED)
    c.drawString(MARGIN + 5 * mm, d.y - 7 * mm, "AMOUNT RECEIVED")
    c.setFont("Helvetica-Bold", 20)
    c.setFillColor(GREEN)
    c.drawString(MARGIN + 5 * mm, d.y - 15 * mm, f"Rs. {rupees(amount)}")

    # In words, because that is what makes a receipt hard to alter after it has
    # been handed over -- a figure can have a digit added to it, a sentence
    # cannot.
    c.setFont("Helvetica-Oblique", 8.5)
    c.setFillColor(NAVY)
    words = _in_words(amount)
    c.drawRightString(MARGIN + BODY_W - 5 * mm, d.y - 15 * mm, words)
    d.y -= box_h
    d.gap(6)

    if payment.get("voided"):
        # A voided payment still has a receipt -- somebody was handed one --
        # and reprinting it silently as though it stood would be the document
        # lying. It says so across the face instead.
        c.setFillColor(colors.HexColor("#fef2f2"))
        c.setStrokeColor(colors.HexColor("#fecaca"))
        c.rect(MARGIN, d.y - 10 * mm, BODY_W, 10 * mm, stroke=1, fill=1)
        c.setFont("Helvetica-Bold", 11)
        c.setFillColor(colors.HexColor("#b91c1c"))
        c.drawString(MARGIN + 5 * mm, d.y - 6.5 * mm,
                     "VOIDED — this payment has been reversed")
        d.y -= 10 * mm
        d.gap(6)

    note = (settings.get("footer_note") or "").strip()
    if note:
        c.setFillColor(CREAM)
        c.setStrokeColor(LINE)
        c.rect(MARGIN, d.y - 12 * mm, BODY_W, 12 * mm, stroke=1, fill=1)
        c.setFont("Helvetica", 8.5)
        c.setFillColor(INK)
        c.drawString(MARGIN + 5 * mm, d.y - 7.5 * mm, note[:120])
        d.y -= 12 * mm
        d.gap(10)

    c.setFont("Helvetica", 7.5)
    c.setFillColor(MUTED)
    c.drawString(MARGIN, d.y,
                 "This is a computer-generated receipt and is valid without a "
                 "signature.")

    c.showPage()
    c.save()
    return buf.getvalue()


_ONES = ("", "One", "Two", "Three", "Four", "Five", "Six", "Seven", "Eight",
         "Nine", "Ten", "Eleven", "Twelve", "Thirteen", "Fourteen", "Fifteen",
         "Sixteen", "Seventeen", "Eighteen", "Nineteen")
_TENS = ("", "", "Twenty", "Thirty", "Forty", "Fifty", "Sixty", "Seventy",
         "Eighty", "Ninety")


def _under_hundred(n: int) -> str:
    if n < 20:
        return _ONES[n]
    return (_TENS[n // 10] + (" " + _ONES[n % 10] if n % 10 else "")).strip()


def _in_words(amount: Decimal) -> str:
    """The amount in the Indian convention -- lakh and crore, not million.

    A receipt printed in India that says "one million two hundred thousand" is
    a receipt nobody here reads without converting it, which defeats the point
    of writing it out.
    """
    whole = int(amount)
    paise = int(round((amount - whole) * 100))
    if whole == 0 and paise == 0:
        return "Rupees Zero Only"

    parts: list[str] = []
    for divisor, name in ((10_000_000, "Crore"), (100_000, "Lakh"),
                          (1_000, "Thousand"), (100, "Hundred")):
        if whole >= divisor:
            count = whole // divisor
            whole %= divisor
            parts.append(f"{_under_hundred(count)} {name}")
    if whole:
        parts.append(_under_hundred(whole))

    words = "Rupees " + " ".join(parts)
    if paise:
        words += f" and {_under_hundred(paise)} Paise"
    return words + " Only"
