"""Indian states, and making a typed state name into one of them.

Every tenant can be anywhere in India, and state names arrive from many hands:
a desk agent typing "AP", a channel manager sending "TN", an old spelling like
"Orissa" or "Pondicherry", or a typo such as "AndraPradash". Stored as typed,
one tax jurisdiction becomes several and nothing that groups by state -- GST
treatment, reports, the invoice's place of supply -- can be trusted.

``normalise_state`` turns any of those into the official name when it can tell
which state was meant, and leaves the value alone when it cannot (a foreign
region, or something too far from any state to guess). ``state_code_problem``
catches the GST state code disagreeing with the state, which is the wrong tax,
not a cosmetic slip.

The browser holds the same tables (``frontend/src/lib/options.ts``).
"""
from __future__ import annotations

import difflib
import re

from .gstin import STATE_CODES

#: Official name -> GST state code, for all 36 states and union territories.
STATES: dict[str, str] = {name: code for code, name in STATE_CODES.items()}

#: Abbreviations and former or common alternative names, keyed compact
#: (lower case, "&" as "and", no spaces, dots or hyphens).
_ALIASES: dict[str, str] = {
    "an": "Andaman and Nicobar Islands", "andaman": "Andaman and Nicobar Islands",
    "andamanandnicobar": "Andaman and Nicobar Islands",
    "ap": "Andhra Pradesh",
    "ar": "Arunachal Pradesh",
    "as": "Assam",
    "br": "Bihar",
    "ch": "Chandigarh",
    "cg": "Chhattisgarh", "ct": "Chhattisgarh", "chattisgarh": "Chhattisgarh",
    "chhatisgarh": "Chhattisgarh",
    "dn": "Dadra and Nagar Haveli and Daman and Diu",
    "dd": "Dadra and Nagar Haveli and Daman and Diu",
    "dnhdd": "Dadra and Nagar Haveli and Daman and Diu",
    "dadraandnagarhaveli": "Dadra and Nagar Haveli and Daman and Diu",
    "damananddiu": "Dadra and Nagar Haveli and Daman and Diu",
    "dl": "Delhi", "newdelhi": "Delhi", "nctofdelhi": "Delhi", "nctdelhi": "Delhi",
    "ga": "Goa",
    "gj": "Gujarat",
    "hr": "Haryana",
    "hp": "Himachal Pradesh",
    "jk": "Jammu and Kashmir", "jandk": "Jammu and Kashmir", "kashmir": "Jammu and Kashmir",
    "jh": "Jharkhand",
    "ka": "Karnataka",
    "kl": "Kerala",
    "la": "Ladakh",
    "ld": "Lakshadweep",
    "mp": "Madhya Pradesh",
    "mh": "Maharashtra",
    "mn": "Manipur",
    "ml": "Meghalaya",
    "mz": "Mizoram",
    "nl": "Nagaland",
    "od": "Odisha", "or": "Odisha", "orissa": "Odisha",
    "py": "Puducherry", "pondicherry": "Puducherry", "pondy": "Puducherry",
    "pb": "Punjab",
    "rj": "Rajasthan",
    "sk": "Sikkim",
    "tn": "Tamil Nadu",
    "ts": "Telangana", "tg": "Telangana",
    "tr": "Tripura",
    "up": "Uttar Pradesh",
    "uk": "Uttarakhand", "ua": "Uttarakhand", "uttaranchal": "Uttarakhand",
    "wb": "West Bengal",
}

#: A misspelling must be this close to one state (0-1) to be read as it.
_CUTOFF = 0.8


def _compact(value: str) -> str:
    return re.sub(r"[\s.\-_,]", "", value.lower().replace("&", "and"))


_BY_COMPACT = {_compact(name): name for name in STATES}


def normalise_state(value: str | None) -> str | None:
    """The official state name ``value`` means, or ``value`` unchanged.

    Blank becomes ``None``. Abbreviations and old names are looked up; a
    misspelling is accepted only when it is close to exactly one state, so
    "AndraPradash" becomes Andhra Pradesh but nothing is forced onto a region
    outside India.
    """
    if value is None:
        return None
    raw = value.strip()
    if not raw:
        return None
    key = _compact(raw)
    if key in _BY_COMPACT:
        return _BY_COMPACT[key]
    if key in _ALIASES:
        return _ALIASES[key]
    if len(key) >= 5:
        close = difflib.get_close_matches(key, list(_BY_COMPACT), n=2, cutoff=_CUTOFF)
        if len(close) == 1:
            return _BY_COMPACT[close[0]]
        if len(close) == 2:
            a = difflib.SequenceMatcher(None, key, close[0]).ratio()
            b = difflib.SequenceMatcher(None, key, close[1]).ratio()
            if a - b >= 0.05:
                return _BY_COMPACT[close[0]]
    return raw


def state_code_problem(state: str | None, state_code: str | None) -> str | None:
    """Say so when a GST state code belongs to a different state.

    Only checked when both are given and the state is an Indian state; a
    missing code is a different question, answered where it is required.
    """
    code = (state_code or "").strip()
    name = normalise_state(state)
    if not code or name not in STATES:
        return None
    if STATES[name] == code:
        return None
    other = STATE_CODES.get(code)
    if other is None:
        return f"{code} is not a GST state code."
    return (f"GST state code {code} is {other}, but the state is {name}. One of the "
            f"two is wrong, and together they decide whether CGST+SGST or IGST applies.")
