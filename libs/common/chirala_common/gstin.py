"""Checking a GSTIN, so an invoice cannot claim a registration it has not got.

A GSTIN is fifteen characters with a fixed shape -- ``37ABCDE1234F1ZZ``:

===========  =========  ==================================================
Part         Position   Rule
===========  =========  ==================================================
State code   1-2        The state the registration belongs to
PAN          3-12       five letters, four digits, one letter
Entity       13         which registration this is for that PAN in that state
Literal Z    14         always ``Z``
Check digit  15         mod-36 checksum over the first fourteen
===========  =========  ==================================================

The checksum is the part that earns its keep. A format check alone passes a
great many typos -- swap two characters of a real GSTIN and it still looks
exactly like a GSTIN -- whereas the check digit catches a single wrong or
transposed character almost every time.

What this deliberately does **not** do is ask the GSTN whether the number is
issued, or to whom. That is a live government integration with credentials and
a rate limit, and pretending to have checked when nothing was checked would be
worse than saying plainly that the shape is right and the registration itself
is unverified.

This lives in the shared library because both a property's own GSTIN and a
corporate customer's are the same kind of thing, held by different services. A
second copy of the rule is a second answer to the same question.
"""
from __future__ import annotations

import re

#: Value of each character in the checksum, by position in this string.
_ALPHABET = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"

#: Two digits, PAN, entity character, a literal Z, then the check digit.
_SHAPE = re.compile(r"^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][0-9A-Z]Z[0-9A-Z]$")

#: GST state codes, as issued. 25 (Daman and Diu) was merged into 26 and 28
#: (undivided Andhra Pradesh) became 37, so neither is valid on a new number.
STATE_CODES: dict[str, str] = {
    "01": "Jammu and Kashmir", "02": "Himachal Pradesh", "03": "Punjab",
    "04": "Chandigarh", "05": "Uttarakhand", "06": "Haryana", "07": "Delhi",
    "08": "Rajasthan", "09": "Uttar Pradesh", "10": "Bihar", "11": "Sikkim",
    "12": "Arunachal Pradesh", "13": "Nagaland", "14": "Manipur",
    "15": "Mizoram", "16": "Tripura", "17": "Meghalaya", "18": "Assam",
    "19": "West Bengal", "20": "Jharkhand", "21": "Odisha",
    "22": "Chhattisgarh", "23": "Madhya Pradesh", "24": "Gujarat",
    "26": "Dadra and Nagar Haveli and Daman and Diu", "27": "Maharashtra",
    "29": "Karnataka", "30": "Goa", "31": "Lakshadweep", "32": "Kerala",
    "33": "Tamil Nadu", "34": "Puducherry",
    "35": "Andaman and Nicobar Islands", "36": "Telangana",
    "37": "Andhra Pradesh", "38": "Ladakh",
}


def normalise(value: str | None) -> str | None:
    """Upper-case and strip a GSTIN; blank becomes ``None``.

    A number pasted out of an email arrives with spaces in it and sometimes in
    lower case. Neither is a mistake worth refusing over.
    """
    if value is None:
        return None
    cleaned = re.sub(r"\s+", "", value).upper()
    return cleaned or None


def check_digit(first_fourteen: str) -> str:
    """The fifteenth character the first fourteen imply.

    Each character is weighted alternately by 1 and 2; the quotient and
    remainder of that product against 36 are both added to the running total,
    and the digit is whatever brings the total to a multiple of 36.
    """
    total = 0
    for i, ch in enumerate(first_fourteen):
        product = _ALPHABET.index(ch) * (2 if i % 2 else 1)
        total += product // 36 + product % 36
    return _ALPHABET[(36 - total % 36) % 36]


def problem(value: str | None, *, state_code: str | None = None) -> str | None:
    """Say what is wrong with a GSTIN, or ``None`` when it looks right.

    ``state_code`` is the place of supply it is expected to belong to. The two
    must agree: the leading digits of the GSTIN are what decides CGST+SGST
    against IGST, so a mismatch is not cosmetic -- it is the wrong tax.
    """
    gstin = normalise(value)
    if gstin is None:
        return None                       # absence is a different question

    if len(gstin) != 15:
        return (f"A GSTIN is 15 characters; this one is {len(gstin)}.")
    if not _SHAPE.match(gstin):
        return ("That is not the shape of a GSTIN — two digits, then a PAN "
                "(ABCDE1234F), an entity character, a Z, and a check digit, "
                "as in 37ABCDE1234F1ZZ.")
    if gstin[:2] not in STATE_CODES:
        return f"{gstin[:2]} is not a GST state code."
    expected = check_digit(gstin[:14])
    if gstin[14] != expected:
        return ("The check digit does not match the rest of the number, so "
                "something in it is mistyped.")
    if state_code and gstin[:2] != state_code.strip():
        belongs = STATE_CODES.get(gstin[:2], "another state")
        return (f"This GSTIN is registered in {belongs} ({gstin[:2]}), but the "
                f"place of supply is {state_code}. One of the two is wrong, "
                f"and together they decide whether CGST+SGST or IGST applies.")
    return None
