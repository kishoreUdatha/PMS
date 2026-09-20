"""Checking a postal code, so an address on a registration card or an invoice
is one a letter could reach.

In India a postal code is a PIN code: six digits, the first never 0 (it names
one of the nine postal zones, 1-9). That is checked exactly. Elsewhere formats
range from four digits to "SW1A 1AA", so only the shape is checked -- letters,
digits, spaces and hyphens, two to ten of them.

An empty code is never a problem here; whether one is required is the caller's
question, as with ``chirala_common.gstin``.

The browser applies the same rule (``frontend/src/lib/options.ts``), but the
screen is not the only way in, so the services check it too. The one
difference is deliberate: with no country given, the browser assumes India
because its forms default to India, while an API caller that sends no country
gets the looser shape check rather than a refusal of a foreign code.
"""
from __future__ import annotations

import re

_PIN = re.compile(r"^[1-9][0-9]{5}$")
_ANY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 -]{1,9}$")


def problem(code: str | None, country: str | None) -> str | None:
    """Say what is wrong with a postal code, or ``None`` when nothing is."""
    value = (code or "").strip()
    if not value:
        return None
    if (country or "").strip().lower() == "india":
        if _PIN.match(value):
            return None
        return (f"{value} is not a PIN code. An Indian PIN code is 6 digits "
                f"and does not start with 0.")
    if _ANY.match(value):
        return None
    return "A postal code is 2 to 10 letters, digits, spaces or hyphens."
