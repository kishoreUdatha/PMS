"""The one list of ways money can be taken.

Lives in the shared library, not in finance, because booking-core writes
payments too: the deposit taken at check-in and the balance taken at
check-out are both inserted into ``finance.payments`` directly by
booking-core, which had no access to this module and so wrote whatever
spelling the screen sent. That is where the surviving ``UPI`` and ``Cash``
rows came from -- finance's own endpoint had been normalising for a while,
but two of the paths that write payments do not go through it.

There were five. ``cashiering_routes`` knew six methods including a wallet,
``invoice_routes`` knew five without it, the onboarding screen offered four in
lower case, and three collection screens each kept their own in title case --
one of them offering "Link", which is a way of *asking* for money rather than
a way of receiving it.

That is not a tidiness problem. ``finance.payments`` held ``upi`` and ``UPI``
as separate values, and ``cash`` and ``Cash`` likewise, because different
screens wrote different spellings of the same method. The back-office reports
normalised on the way out and so read correctly, but the rows disagreed, and
the paths that wrote them did not all check. Migration 0032 merged them; every
write path now canonicalises before storing.

So: one tuple, one set of labels, one rule about which need a reference, in a
module with no dependencies of its own so anything may import it. Anything
that offers a choice of method serves it from here rather than restating it,
which is what stops the sixth list appearing.
"""

from __future__ import annotations

#: What a person at a desk can take money by. Concrete actions, not a payment
#: provider's taxonomy.
METHODS: tuple[str, ...] = (
    "cash", "card", "upi", "bank_transfer", "cheque", "wallet",
)

LABELS: dict[str, str] = {
    "cash": "Cash",
    "card": "Card",
    "upi": "UPI",
    "bank_transfer": "Bank Transfer",
    "cheque": "Cheque",
    "wallet": "Wallet",
    # Not offered as a choice: a cashier cannot select it. It is what a gateway
    # payment falls back to when the instrument is one the desk has no name
    # for, and it still needs a label so a row does not render a raw code.
    "online": "Online",
}

#: A reference is what the guest can quote back if the payment is questioned.
#: Cash has none to quote.
NEEDS_REFERENCE: tuple[str, ...] = (
    "card", "upi", "bank_transfer", "cheque", "wallet",
)


def normalise(value: str) -> str:
    """Accept what the old screens sent, store what the ledger expects.

    "UPI", "Bank Transfer" and "bank transfer" all mean the same method, and
    for a while every one of them could be written. New callers should send
    the canonical spelling; this exists so the ones that have not been updated
    yet stop adding to the mess rather than being rejected outright at a desk
    with a guest standing there.
    """
    key = value.strip().lower().replace(" ", "_").replace("-", "_")
    return key


def is_valid(value: str) -> bool:
    return normalise(value) in METHODS
