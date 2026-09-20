"""The house stationery: the look every printed document shares.

The folio and the tax invoice are the same piece of paper with different
content on it — same wordmark, same gold rule, same cream-headed panels, same
navy table. They were one file until the invoice needed its own renderer, and
splitting the drawing primitives out here is what stops the two documents
drifting apart the way the four stay lists once did.

The values are the approved template's own, read from its content stream
rather than sampled off a screenshot.
"""

from __future__ import annotations

from decimal import Decimal

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas as pdfcanvas

# Stationery, not screen. These are the template's own values, read from its
# content stream rather than sampled off a screenshot: guessing them by eye is
# what made the first two attempts look close but wrong.
NAVY = colors.HexColor("#173047")        # section headings, wordmark
NAVY_DEEP = colors.HexColor("#17212B")   # title and panel text
GOLD = colors.HexColor("#A67D2E")        # tagline, rules, footer
CREAM = colors.HexColor("#F4EFE4")
INK = colors.HexColor("#000000")         # table body is plain black
MUTED = colors.HexColor("#66737D")
FAINT = colors.HexColor("#66737D")
LINE = colors.HexColor("#C9CDD4")
ROW_ALT = colors.HexColor("#F7F8FA")
GREEN_BG = colors.HexColor("#EAF5EF")
GREEN = colors.HexColor("#1B7A4B")
AMBER_BG = colors.HexColor("#FDF6E7")
AMBER = colors.HexColor("#9A6A11")

PAGE_W, PAGE_H = A4
MARGIN = 20 * mm
BODY_W = PAGE_W - 2 * MARGIN


def rupees(value) -> str:
    """1,23,456.78 — Indian grouping, which is not what a thousands separator
    library gives you."""
    d = Decimal(value or 0).quantize(Decimal("0.01"))
    neg = d < 0
    whole, _, frac = f"{abs(d):.2f}".partition(".")
    if len(whole) > 3:
        head, tail = whole[:-3], whole[-3:]
        parts = []
        while len(head) > 2:
            parts.insert(0, head[-2:])
            head = head[:-2]
        if head:
            parts.insert(0, head)
        whole = ",".join(parts + [tail])
    return f"{'-' if neg else ''}{whole}.{frac}"


def _day(d) -> str:
    """A date the way the rest of the system writes it: 10 Sep 2026.

    Accepts the ISO strings a JSON dump produces as well as real date objects,
    because a document rendered from a serialised model was otherwise printing
    "2026-09-10T16:46:57.546046Z" where a date belonged.
    """
    if not d:
        return "-"
    if isinstance(d, str):
        try:
            from datetime import datetime
            d = datetime.fromisoformat(d.replace("Z", "+00:00"))
        except ValueError:
            return d
    return d.strftime("%d %b %Y")


class _Doc:
    """A cursor down the page, so the sections read as a document rather than
    as coordinate arithmetic."""

    def __init__(self, c: pdfcanvas.Canvas) -> None:
        self.c = c
        self.y = PAGE_H - MARGIN

    def gap(self, mm_: float) -> None:
        self.y -= mm_ * mm

    def need(self, mm_: float) -> None:
        if self.y - mm_ * mm < MARGIN + 20 * mm:
            self.c.showPage()
            self.y = PAGE_H - MARGIN


def _header(d: _Doc, sett: dict) -> None:
    """Serif wordmark left, contact block right, gold rule under both.

    The spacings are measured off the approved template rather than chosen by
    eye: 6.5mm between the two wordmark lines, the tagline 3.3mm under them,
    the rule 2.4mm under that, and 3.5mm between the contact lines. An earlier
    pass guessed at these and the header sat noticeably looser than the mock.
    """
    c = d.c
    # No fallback to any resort's name: an unconfigured property must not be
    # handed a folio wearing another tenant's identity.
    full = (sett.get("legal_name") or "").strip().upper() or "PROPERTY NAME NOT SET"
    # The wordmark sets over two lines the way the template does; a long legal
    # name breaks on its last word rather than running into the contact block.
    words = full.split()
    line1, line2 = (" ".join(words[:-1]), words[-1]) if len(words) > 1 else (full, "")

    base1 = d.y - 8 * mm
    c.setFont("Times-Bold", 22)
    c.setFillColor(NAVY)
    c.drawString(MARGIN, base1, line1)
    base2 = base1 - 6.5 * mm
    if line2:
        c.drawString(MARGIN, base2, line2)
    else:
        base2 = base1

    # The property's own strapline, tracked to about 26mm as on the
    # template. The tracking is applied by the text object rather than by
    # padding the string with spaces, so it holds its width whatever the
    # wording is.
    tag = (sett.get("tagline") or "").strip()
    tag_y = base2 - 3.3 * mm
    if tag:
        natural = c.stringWidth(tag, "Helvetica", 7.5)
        target = 26 * mm
        # Tracking lives on the text object; the canvas has no setCharSpace.
        t = c.beginText(MARGIN, tag_y)
        t.setFont("Helvetica", 7.5)
        t.setCharSpace(max((target - natural) / max(len(tag) - 1, 1), 0))
        t.setFillColor(GOLD)
        t.textOut(tag)
        c.drawText(t)
    else:
        # Nothing to set, so the rule closes up under the wordmark instead of
        # leaving a gap where another property's strapline would have been.
        tag_y = base2

    # Contact block: three tight lines, the first sitting above the wordmark's
    # own baseline exactly as the template has it.
    right = PAGE_W - MARGIN
    ry = base1 + 4.4 * mm
    addr = ", ".join(x for x in (
        sett.get("address_line"), sett.get("city"), sett.get("state"),
        sett.get("country")) if x)
    # Each line only takes space if it has something to say — a property with
    # no phone on file should not get a blank gap where one would have been.
    if addr:
        c.setFont("Helvetica-Bold", 8.5)
        c.setFillColor(NAVY_DEEP)
        c.drawRightString(right, ry, addr)
        ry -= 3.5 * mm

    contact = "     |     ".join(
        x for x in (sett.get("phone"), sett.get("email")) if x)
    if contact:
        c.setFont("Helvetica", 8.5)
        c.setFillColor(INK)
        c.drawRightString(right, ry, contact)
        ry -= 4.4 * mm

    gst = sett.get("gstin")
    if gst:
        c.setFont("Helvetica", 8.5)
        c.setFillColor(INK)
        c.drawRightString(right, ry, f"GSTIN: {gst}")
    else:
        # Said plainly and briefly. Inventing a tax registration would be far
        # worse than admitting it is not set, but it should not shout either.
        c.setFont("Helvetica", 8.5)
        c.setFillColor(FAINT)
        c.drawRightString(right, ry, "GSTIN: not configured")

    rule_y = min(tag_y, ry) - 2.4 * mm
    c.setStrokeColor(GOLD)
    c.setLineWidth(1.1)
    c.line(MARGIN, rule_y, right, rule_y)
    d.y = rule_y
    d.gap(7)


def _panel(c, x, y, width, title, rows, row_h=5.0) -> float:
    """Cream title bar, white body, labels bold inline with their values."""
    head_h = 6.8 * mm
    body_h = 3.4 * mm + len(rows) * row_h * mm
    total = head_h + body_h

    c.setFillColor(CREAM)
    c.setStrokeColor(LINE)
    c.setLineWidth(0.6)
    c.rect(x, y - head_h, width, head_h, stroke=1, fill=1)
    c.setFillColor(colors.white)
    c.rect(x, y - total, width, body_h, stroke=1, fill=1)

    c.setFont("Helvetica-Bold", 10.5)
    c.setFillColor(NAVY)
    c.drawString(x + 4 * mm, y - 5.2 * mm, title)

    ty = y - head_h - 5 * mm
    for label, value in rows:
        c.setFont("Helvetica-Bold", 8.2)
        c.setFillColor(NAVY_DEEP)
        c.drawString(x + 4 * mm, ty, f"{label}:")
        w = c.stringWidth(f"{label}:", "Helvetica-Bold", 8.2)
        c.setFont("Helvetica", 8.2)
        c.setFillColor(NAVY_DEEP)
        c.drawString(x + 4 * mm + w + 1.6 * mm,
                     ty, str(value if value not in (None, "") else "-"))
        ty -= row_h * mm
    return total


def _figures_panel(c, x, y, width, title, rows, row_h=5.0) -> float:
    """Like _panel, but the values line up in a right-hand column.

    TAX BREAKUP and PAYMENT SUMMARY are money, and money is read down a
    column. The template sets them this way; rendering them as inline
    "Label: value" pairs — which an earlier pass did — makes the figures
    impossible to scan and was the most visible difference from the mock.
    """
    head_h = 6.8 * mm
    body_h = 3.4 * mm + len(rows) * row_h * mm
    total = head_h + body_h

    c.setFillColor(CREAM)
    c.setStrokeColor(LINE)
    c.setLineWidth(0.6)
    c.rect(x, y - head_h, width, head_h, stroke=1, fill=1)
    c.setFillColor(colors.white)
    c.rect(x, y - total, width, body_h, stroke=1, fill=1)

    c.setFont("Helvetica-Bold", 10.5)
    c.setFillColor(NAVY)
    c.drawString(x + 4 * mm, y - 5.2 * mm, title)

    ty = y - head_h - 5 * mm
    for label, value, strong in rows:
        font = "Helvetica-Bold" if strong else "Helvetica"
        size = 9 if strong else 8.2
        c.setFont(font, size)
        c.setFillColor(NAVY_DEEP if strong else NAVY)
        c.drawString(x + 4 * mm, ty, label)
        c.setFont("Helvetica-Bold" if strong else "Helvetica", size)
        c.setFillColor(NAVY_DEEP if strong else NAVY)
        c.drawRightString(x + width - 4 * mm, ty, str(value))
        ty -= row_h * mm
    return total




def title_block(d: _Doc, heading: str, rows: list[tuple[str, str]]) -> None:
    """The document's name on the left, its identifying facts on the right.

    Shared because a folio and a tax invoice head the page identically and
    differ only in the words: the label sits immediately left of its value
    rather than in a fixed column, so a long invoice number pushes its own
    label along instead of colliding with it.
    """
    c = d.c
    c.setFont("Helvetica-Bold", 18)
    c.setFillColor(NAVY_DEEP)
    c.drawString(MARGIN, d.y - 2 * mm, heading)

    right = PAGE_W - MARGIN
    y = d.y
    for label, value in rows:
        value = str(value)
        c.setFont("Helvetica", 8.2)
        c.setFillColor(NAVY_DEEP)
        w = c.stringWidth(value, "Helvetica", 8.2)
        c.drawRightString(right, y, value)
        c.setFont("Helvetica-Bold", 8.2)
        c.setFillColor(NAVY_DEEP)
        c.drawRightString(right - w - 2 * mm, y, label)
        y -= 4.6 * mm
    d.y = min(d.y - 7 * mm, y)
    d.gap(1)
