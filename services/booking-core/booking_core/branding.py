"""What a hotel's own booking page looks like, and what it is not allowed to.

A tenant picks one colour. Everything else about the page's legibility is
derived from it here rather than supplied, because the two things a colour
picker reliably produces are a brand colour and an unreadable button.

**The text colour is computed, never chosen.** White on a pale gold button is
1.6:1 and invisible in daylight; the same gold with near-black text is 12:1
and fine. So the hotel says what colour it is and this decides what goes on
top, using the WCAG contrast formula rather than an opinion.

**A colour that cannot carry either is refused.** There is a band of mid-tones
-- roughly the greys and muddy mid-blues -- where neither white nor near-black
reaches 4.5:1. A page rendered in one of those is unreadable whatever we do,
so the answer is to say so at the point somebody picks it, not to ship it and
hope.

**Nothing here trusts a string.** The colour is re-validated against the same
pattern the database enforces, because this value ends up inside a CSS custom
property on a public page.
"""

from __future__ import annotations

import re

#: The page's own ink, and the alternative to white on a pale brand colour.
#: Matches the console's `pf-navy`, so a branded page and the platform's own
#: chrome are the same black rather than two nearly-equal ones.
DARK_INK = "#172B4D"
LIGHT_INK = "#FFFFFF"

#: WCAG AA for normal text. Buttons on this page carry 14-16px labels, which
#: is normal text by that standard -- the 3:1 large-text allowance does not
#: apply and using it would be choosing the weaker rule for the case that
#: matters most.
MIN_CONTRAST = 4.5

_HEX = re.compile(r"^#[0-9A-Fa-f]{6}$")


class UnusableColour(ValueError):
    """The colour cannot carry readable text of any kind."""


def _channel(value: float) -> float:
    return value / 12.92 if value <= 0.03928 else ((value + 0.055) / 1.055) ** 2.4


def relative_luminance(hex_colour: str) -> float:
    """WCAG relative luminance, 0 (black) to 1 (white)."""
    raw = hex_colour.lstrip("#")
    r, g, b = (int(raw[i:i + 2], 16) / 255 for i in (0, 2, 4))
    return (0.2126 * _channel(r) + 0.7152 * _channel(g) + 0.0722 * _channel(b))


def contrast(a: str, b: str) -> float:
    """The WCAG contrast ratio between two colours, 1:1 to 21:1."""
    la, lb = relative_luminance(a), relative_luminance(b)
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


def readable_ink(brand: str) -> str:
    """Whichever of white or near-black reads better on this colour."""
    return (LIGHT_INK if contrast(LIGHT_INK, brand) >= contrast(DARK_INK, brand)
            else DARK_INK)


def validate_colour(brand: str | None) -> str | None:
    """Normalise a brand colour, or explain why it cannot be used.

    Returns the colour upper-cased, or None when none was given. Raises
    ``UnusableColour`` with a sentence a person can act on.
    """
    if brand is None or not brand.strip():
        return None
    value = brand.strip()
    if not _HEX.match(value):
        raise UnusableColour(
            "A brand colour has to be a six-digit hex value like #0F6E5C.")

    best = max(contrast(LIGHT_INK, value), contrast(DARK_INK, value))
    if best < MIN_CONTRAST:
        raise UnusableColour(
            f"{value.upper()} cannot carry readable text -- the best it "
            f"manages is {best:.1f}:1 against either white or near-black, and "
            f"{MIN_CONTRAST}:1 is the minimum. Try a darker or lighter shade "
            f"of the same hue."
        )
    return value.upper()


def theme(brand: str | None) -> dict:
    """The full palette a page needs, derived from the one colour given.

    Returned even when nothing is branded, so the page always has the same
    shape to read and never has to decide what a missing value means.
    """
    if not brand:
        return {"brand_color": None, "ink_on_brand": None, "contrast": None}
    ink = readable_ink(brand)
    return {
        "brand_color": brand,
        "ink_on_brand": ink,
        # Reported so the value is auditable rather than merely asserted: a
        # screen can show it, and a reviewer can check the arithmetic.
        "contrast": round(contrast(ink, brand), 2),
    }
