"""Finance ledger: charges, payments, allocations, refunds, balances (§6).

SIGN CONVENTION (see migration 0001 header):
  DEBIT increases the amount owed; CREDIT decreases it.
  folio balance = SUM(debit) - SUM(credit) over posted entries.

Invariants enforced transactionally (not row CHECKs), per §6:
  - Payment allocations across folios cannot exceed the payment's captured
    amount. The payment row is locked FOR UPDATE while allocating.
  - Refunds (successful + pending) cannot exceed the payment's refundable
    balance (captured - already refunded). The payment is locked FOR UPDATE.
  - Charge posting is idempotent on (folio_id, source_type, source_line_key):
    a duplicate post is a no-op returning the existing entry.
  - Posted entries are never edited; corrections use reversal entries.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from chirala_common.outbox import enqueue_event
from sqlalchemy import text
from sqlalchemy.orm import Session

from chirala_common.folio_posting import (  # noqa: F401 - EntryResult re-exported
    EntryResult,
    PostingError,
    post_charge as _common_post_charge,
    resolve_currency as _common_resolve_currency,
)

from .provider import PaymentProvider, ProviderResult, default_provider
from chirala_common import payment_methods


class LedgerError(Exception):
    def __init__(self, message: str, *, conflict: bool = False) -> None:
        self.conflict = conflict
        super().__init__(message)


# --------------------------------------------------------------------------
# Currency
# --------------------------------------------------------------------------
def _resolve_currency(
    session: Session, *, stated: str | None, folio_ids=(), payment_id=None,
) -> str:
    """The currency a posting is in; see ``folio_posting.resolve_currency``."""
    try:
        return _common_resolve_currency(session, stated=stated,
                                        folio_ids=folio_ids,
                                        payment_id=payment_id)
    except PostingError as exc:
        raise LedgerError(str(exc), conflict=exc.conflict) from exc


# --------------------------------------------------------------------------
# Balance
# --------------------------------------------------------------------------
def folio_balance(session: Session, folio_id: uuid.UUID) -> Decimal:
    """Return posted debits - credits for a folio (positive = guest owes)."""
    row = session.execute(
        text(
            """
            SELECT coalesce(sum(
                CASE WHEN entry_type = 'debit' THEN amount ELSE -amount END
            ), 0) AS balance
            FROM finance.folio_entries
            WHERE folio_id = :fid
            """
        ),
        {"fid": folio_id},
    ).scalar_one()
    return Decimal(row)


# --------------------------------------------------------------------------
# Post a charge (debit) — idempotent
# --------------------------------------------------------------------------
def post_charge(session: Session, **kwargs) -> EntryResult:
    """Post an immutable debit, with the tax the rules require.

    The implementation is ``chirala_common.folio_posting.post_charge``, shared
    with booking-core so a cancellation fee or a no-show penalty posted from
    the desk is taxed, dated and labelled exactly as one posted here. This
    wrapper only keeps finance's error type.
    """
    try:
        return _common_post_charge(session, **kwargs)
    except PostingError as exc:
        raise LedgerError(str(exc), conflict=exc.conflict) from exc


# --------------------------------------------------------------------------
# Post a payment with allocations — one credit entry per allocation
# --------------------------------------------------------------------------
@dataclass
class Allocation:
    folio_id: uuid.UUID
    amount: Decimal


@dataclass
class PaymentResult:
    payment_id: uuid.UUID
    credit_entry_ids: list[uuid.UUID]


def post_payment(
    session: Session,
    *,
    organization_id: uuid.UUID,
    property_id: uuid.UUID,
    method: str,
    business_date: date,
    allocations: list[Allocation],
    currency: str | None = None,
    provider: PaymentProvider | None = None,
    intent_id: uuid.UUID | None = None,
    settled_transaction_id: str | None = None,
    source: str = "front_desk",
    reference: str | None = None,
    note: str | None = None,
    posted_by: str | None = None,
    #: The drawer this money went into, for cash.
    #:
    #: This function had no such parameter for a long time, and the column it
    #: writes stayed NULL on every payment it created. The Cashiering Centre
    #: patched it on with an UPDATE afterwards, so a cash payment taken there
    #: reached the drawer count and the identical payment taken from a folio
    #: did not -- the till then held money the shift did not expect, and the
    #: cashier declared a surplus they could not explain. Same shape as the
    #: bug the refund path fixed from the other side, where a refund missing
    #: from the count produced a phantom shortage.
    #:
    #: Optional because a card or gateway payment has no drawer to name.
    cashier_shift_id: uuid.UUID | None = None,
) -> PaymentResult:
    """Capture a payment and allocate it across folios.

    Posts exactly ONE credit folio_entry per allocation (§6). The sum of
    allocations equals the captured amount; over-allocation is impossible
    because the payment amount is derived from the allocation total and each
    allocation posts its own credit atomically.

    ``settled_transaction_id`` says the money has **already** moved and names
    the transaction that moved it. A gateway webhook is the case: by the time
    it arrives the card has been charged, so there is nothing left to capture
    and calling a provider again would be asking for the money twice. It also
    fixes what gets recorded -- without it the row stored whatever the default
    provider handed back, which on a live deployment meant a synthetic id in
    place of the gateway's own. Nobody could then match a payment here against
    a settlement there, which is the one thing reconciliation is.
    """
    if not allocations:
        raise LedgerError("At least one allocation is required")
    for a in allocations:
        if a.amount <= 0:
            raise LedgerError("Allocation amounts must be positive")

    # Canonicalised here, at the one place every finance payment is written,
    # rather than trusting each caller to have done it. ``PaymentCreate``
    # already validates what arrives over HTTP, but deposits, invoice
    # settlements and reversals reach this function by other routes, and a
    # method spelled two ways is one method that every report counts twice.
    method = payment_methods.normalise(method)
    if method not in payment_methods.METHODS:
        raise LedgerError(
            f"Unknown payment method {method!r}. Known methods: "
            + ", ".join(payment_methods.METHODS))

    # Before the money is captured, not after: refusing a payment already
    # taken off a card would leave the guest charged and the folio unpaid.
    _assert_drawer_can_take_cash(
        session,
        property_id=property_id,
        cashier_shift_id=cashier_shift_id,
        method=method,
        direction="in",
    )

    currency = _resolve_currency(
        session, stated=currency,
        folio_ids=sorted({a.folio_id for a in allocations}, key=str))

    total = sum((a.amount for a in allocations), Decimal("0"))
    if settled_transaction_id:
        # Already paid. Recorded, not captured -- the gateway did that, and
        # doing it again would be a second charge.
        result = ProviderResult(True, settled_transaction_id)
    else:
        prov = provider or default_provider
        result = prov.capture(
            amount=total, currency=currency, method=method,
            reference=str(uuid.uuid4()),
        )
        if not result.success:
            raise LedgerError(f"Payment capture failed: {result.detail}")

    payment_id = uuid.uuid4()
    session.execute(
        text(
            """
            INSERT INTO finance.payments
                (id, organization_id, property_id, intent_id,
                 provider_transaction_id, method, amount, currency, status,
                 received_at, source, reference, cashier_shift_id)
            VALUES (:id, :org, :prop, :intent, :ptxn, :method, :amt, :cur,
                    'succeeded', now(), :source, :reference, :shift)
            """
        ),
        {
            "id": payment_id,
            "org": organization_id,
            "prop": property_id,
            "intent": intent_id,
            "ptxn": result.provider_transaction_id,
            "method": method,
            "amt": total,
            "cur": currency,
            # Where the money came from. Defaulted rather than required so
            # every existing caller keeps the behaviour it had, but a gateway
            # payment must say so -- it is not the desk's takings and must not
            # land in the desk's drawer count.
            "source": source,
            # What the guest can quote back. For an online payment that is the
            # gateway's own transaction id, which is also the only string that
            # finds it in the gateway's dashboard.
            "reference": reference or (result.provider_transaction_id or None),
            "shift": cashier_shift_id,
        },
    )

    credit_ids: list[uuid.UUID] = []
    for a in allocations:
        # One credit entry per allocation.
        entry_id = uuid.uuid4()
        session.execute(
            text(
                """
                INSERT INTO finance.folio_entries
                    (id, organization_id, property_id, folio_id, entry_type,
                     amount, currency, business_date, source_type, source_id,
                     source_line_key, note, posted_by)
                VALUES (:id, :org, :prop, :fid, 'credit', :amt, :cur, :bd,
                        'payment', :pid, :slk, :note, :by)
                """
            ),
            {
                "id": entry_id,
                "org": organization_id,
                "prop": property_id,
                "fid": a.folio_id,
                "amt": a.amount,
                "cur": currency,
                "bd": business_date,
                "pid": str(payment_id),
                "slk": f"payment:{payment_id}:{a.folio_id}",
                "by": posted_by,
                # What this payment was for, in the words of whoever took it.
                # A charge has carried one of these since the folio learned to
                # describe its own lines; a payment could not, so every one of
                # them read "Payment" and a desk taking three in a day had no
                # way to tell them apart on the folio. Null leaves the row
                # described by its source type exactly as before.
                "note": (note or None),
            },
        )
        session.execute(
            text(
                """
                INSERT INTO finance.payment_allocations
                    (organization_id, property_id, payment_id, folio_id, amount,
                     folio_entry_id)
                VALUES (:org, :prop, :pid, :fid, :amt, :eid)
                """
            ),
            {
                "org": organization_id,
                "prop": property_id,
                "pid": payment_id,
                "fid": a.folio_id,
                "amt": a.amount,
                "eid": entry_id,
            },
        )
        credit_ids.append(entry_id)

    enqueue_event(
        session,
        aggregate_type="payment",
        aggregate_id=str(payment_id),
        event_type="finance.payment_captured",
        payload={"payment_id": str(payment_id), "amount": str(total)},
    )
    return PaymentResult(payment_id=payment_id, credit_entry_ids=credit_ids)


# --------------------------------------------------------------------------
# Allocate an existing payment to a folio (guarded against over-allocation)
# --------------------------------------------------------------------------
def allocate_payment(
    session: Session,
    *,
    organization_id: uuid.UUID,
    property_id: uuid.UUID,
    payment_id: uuid.UUID,
    folio_id: uuid.UUID,
    amount: Decimal,
    business_date: date,
    currency: str | None = None,
) -> uuid.UUID:
    """Add an allocation to an already-captured payment.

    Locks the payment FOR UPDATE and rejects allocation totals exceeding the
    captured amount (§6). Posts one credit entry.
    """
    if amount <= 0:
        raise LedgerError("Allocation amount must be positive")

    pay = session.execute(
        text(
            """
            SELECT amount FROM finance.payments
            WHERE id = :pid AND property_id = :prop AND status = 'succeeded'
            FOR UPDATE
            """
        ),
        {"pid": payment_id, "prop": property_id},
    ).first()
    if pay is None:
        raise LedgerError("Payment not found or not settled")
    currency = _resolve_currency(
        session, stated=currency, folio_ids=[folio_id], payment_id=payment_id)

    already = session.execute(
        text(
            "SELECT coalesce(sum(amount),0) FROM finance.payment_allocations "
            "WHERE payment_id = :pid"
        ),
        {"pid": payment_id},
    ).scalar_one()
    if Decimal(already) + amount > Decimal(pay.amount):
        raise LedgerError(
            "Allocation exceeds captured payment amount", conflict=True
        )

    entry_id = uuid.uuid4()
    session.execute(
        text(
            """
            INSERT INTO finance.folio_entries
                (id, organization_id, property_id, folio_id, entry_type, amount,
                 currency, business_date, source_type, source_id, source_line_key)
            VALUES (:id, :org, :prop, :fid, 'credit', :amt, :cur, :bd,
                    'payment', :pid, :slk)
            """
        ),
        {
            "id": entry_id,
            "org": organization_id,
            "prop": property_id,
            "fid": folio_id,
            "amt": amount,
            "cur": currency,
            "bd": business_date,
            "pid": str(payment_id),
            "slk": f"payment:{payment_id}:{folio_id}:{entry_id}",
        },
    )
    session.execute(
        text(
            """
            INSERT INTO finance.payment_allocations
                (organization_id, property_id, payment_id, folio_id, amount,
                 folio_entry_id)
            VALUES (:org, :prop, :pid, :fid, :amt, :eid)
            """
        ),
        {
            "org": organization_id,
            "prop": property_id,
            "pid": payment_id,
            "fid": folio_id,
            "amt": amount,
            "eid": entry_id,
        },
    )
    return entry_id


# --------------------------------------------------------------------------
# Refund — bounded by refundable balance
# --------------------------------------------------------------------------
@dataclass
class RefundResult:
    refund_id: uuid.UUID
    debit_entry_ids: list[uuid.UUID]


#: Wording for each direction cash can cross the counter. The rule below is
#: one rule; only the sentence a cashier reads differs, and keeping the two
#: side by side is what stops one door being guarded and the other not.
_CASH_NEEDS_DRAWER = {
    "in": ("Cash has to go into an open drawer. Open a cashier shift first, "
           "then take the money."),
    "out": ("A cash refund has to come out of an open drawer. Open a cashier "
            "shift first, then give the money back."),
}
_CASH_DRAWER_CLOSED = {
    "in": ("That drawer has already been closed and counted. Money cannot be "
           "added to it afterwards — open a new shift to take this payment."),
    "out": ("That drawer has already been closed and counted. Money cannot be "
            "taken out of it afterwards — open a new shift to give this "
            "refund."),
}


def _assert_drawer_can_take_cash(
    session: Session,
    *,
    property_id: uuid.UUID,
    cashier_shift_id: uuid.UUID | None,
    method: str | None,
    direction: str,
) -> None:
    """Cash can only cross a drawer that is open.

    Two rules, and both exist because the drawer count is only worth running
    if nothing can change the drawer after it has been counted.

    **A closed shift cannot take money either way.** ``expected_cash`` and
    ``variance`` are frozen when a cashier counts their drawer and signs off;
    anything added or removed afterwards moves real cash through a till whose
    count has already been agreed, and the shift row silently stops adding up
    -- float plus taken less refunded no longer equals the expected it was
    closed on.

    **Cash needs an open drawer at all.** Without one the money still
    physically enters or leaves some till, and no shift records it, so no
    count will ever catch the difference. Refusing is the only honest answer:
    the cash is moving whether or not the system is told.

    This guarded refunds only for a while, and the asymmetry cost exactly what
    you would expect. The Cashiering Centre grew its own copy of the check for
    payments; the folio's Add payment dialog and the deposit screen never had
    one, so the same cash refused at one desk was accepted at the next. It is
    here now because this is the single function every payment and refund in
    finance passes through, and a rule written once cannot be half-applied.

    Card and UPI are unaffected in both directions — they travel their own
    rails and never touch a till.
    """
    if cashier_shift_id is None:
        # Only cash needs a drawer. A card payment names no shift, and a
        # gateway webhook has no cashier at all.
        if (method or "").lower() == "cash":
            raise LedgerError(_CASH_NEEDS_DRAWER[direction], conflict=True)
        return

    # A shift was named, so it is checked whatever the method: a drawer
    # belonging to another property, or one already counted and signed off,
    # is wrong to attach money to even when that money never touched a till.
    shift = session.execute(
        text(
            "SELECT status FROM finance.cashier_shifts "
            "WHERE id = :s AND property_id = :prop"
        ),
        {"s": cashier_shift_id, "prop": property_id},
    ).first()
    if shift is None:
        raise LedgerError("That cashier shift does not belong to this property")
    if shift.status != "open":
        raise LedgerError(_CASH_DRAWER_CLOSED[direction], conflict=True)


def post_refund(
    session: Session,
    *,
    organization_id: uuid.UUID,
    property_id: uuid.UUID,
    payment_id: uuid.UUID,
    amount: Decimal,
    business_date: date,
    reason: str | None = None,
    currency: str | None = None,
    provider: PaymentProvider | None = None,
    #: The drawer the money physically came out of, and how it left. Only
    #: cash touches a till -- a card refund goes back down the rails it came
    #: up -- but both are recorded so the count can tell them apart.
    cashier_shift_id: uuid.UUID | None = None,
    method: str | None = None,
    #: What the folio line should call itself, e.g. "Void - Overpayment".
    #:
    #: A void, a refund and a reversal all settle through this one function,
    #: and the entry it wrote named the mechanism rather than the act: every
    #: one of them read "Refund" on the bill, including the voids. The system
    #: knew better -- finance.refunds.reason holds "Void: ..." -- but the
    #: ledger line never got told, because nothing carried it this far.
    #:
    #: Left None by callers that genuinely are refunds; the entry then falls
    #: back to the source type's label exactly as before.
    note: str | None = None,
) -> RefundResult:
    """Refund part/all of a payment, bounded by its refundable balance (§6).

    Locks the payment FOR UPDATE; the sum of successful+pending refunds cannot
    exceed the captured amount. Posts a DEBIT (reversal of the credit) per
    allocation so the folio balance rises back — never a negative payment.
    """
    if amount <= 0:
        raise LedgerError("Refund amount must be positive")

    pay = session.execute(
        text(
            """
            SELECT amount, provider_transaction_id, method
            FROM finance.payments
            WHERE id = :pid AND property_id = :prop AND status = 'succeeded'
            FOR UPDATE
            """
        ),
        {"pid": payment_id, "prop": property_id},
    ).first()
    if pay is None:
        raise LedgerError("Payment not found or not settled")

    # Money goes back the way it came unless the caller says otherwise, and
    # taking it from the payment is the only way this is reliable: neither
    # route passed `method`, so every refund ever written here stored NULL --
    # and the drawer count filters refunds on `method = 'cash'`, so the
    # subtraction it exists to make has never once fired. A cashier who
    # refunded cash mid-shift showed a shortage they did not cause.
    method = method or pay.method
    # Refunded in the currency it was paid in -- the payment row says which.
    currency = _resolve_currency(session, stated=currency,
                                 payment_id=payment_id)

    _assert_drawer_can_take_cash(
        session,
        property_id=property_id,
        cashier_shift_id=cashier_shift_id,
        method=method,
        direction="out",
    )

    refunded = session.execute(
        text(
            "SELECT coalesce(sum(amount),0) FROM finance.refunds "
            "WHERE payment_id = :pid AND status IN ('succeeded','pending')"
        ),
        {"pid": payment_id},
    ).scalar_one()
    refundable = Decimal(pay.amount) - Decimal(refunded)
    if amount > refundable:
        raise LedgerError(
            f"Refund {amount} exceeds refundable balance {refundable}",
            conflict=True,
        )

    prov = provider or default_provider
    result = prov.refund(
        amount=amount,
        currency=currency,
        provider_transaction_id=pay.provider_transaction_id or "",
    )
    if not result.success:
        raise LedgerError(f"Refund failed at provider: {result.detail}")

    refund_id = uuid.uuid4()
    session.execute(
        text(
            """
            INSERT INTO finance.refunds
                (id, organization_id, property_id, payment_id, amount, currency,
                 reason, provider_refund_id, status, cashier_shift_id, method)
            VALUES (:id, :org, :prop, :pid, :amt, :cur, :reason, :prid,
                    'succeeded', :shift, :method)
            """
        ),
        {
            "id": refund_id,
            "org": organization_id,
            "prop": property_id,
            "pid": payment_id,
            "amt": amount,
            "cur": currency,
            "reason": reason,
            "prid": result.provider_transaction_id,
            "shift": cashier_shift_id,
            # Already resolved above, against the payment row this function
            # locked. It used to be worked out here with a second SELECT of
            # the same row — harmless, but it meant the drawer guard higher up
            # could not see which method was about to be used.
            "method": method,
        },
    )

    # Post a debit reversing the payment credit on each allocated folio, up to
    # the refund amount: taken in order until the refund is exhausted.
    #
    # ``remaining_alloc`` is the allocation less what previous refunds of this
    # payment already took off that folio. Using the original figure meant a
    # second refund began at the first folio again and debited it twice --
    # 1,000 returned from a folio that received 600, and nothing from the one
    # holding the other 400. The payment's own arithmetic still balanced,
    # which is why it could happen quietly.
    #
    # This refund's own row exists in finance.refunds by now, but none of its
    # folio entries do -- they are what this loop writes -- so the sum below
    # sees only earlier refunds.
    allocations = session.execute(
        text(
            """
            SELECT pa.folio_id,
                   pa.amount - COALESCE((
                       SELECT sum(e.amount)
                         FROM finance.folio_entries e
                        WHERE e.folio_id = pa.folio_id
                          AND e.entry_type = 'debit'
                          AND e.source_type = 'refund'
                          AND e.source_id IN (
                              SELECT r.id::text FROM finance.refunds r
                               WHERE r.payment_id = :pid
                          )
                   ), 0) AS amount
              FROM finance.payment_allocations pa
             WHERE pa.payment_id = :pid
             ORDER BY pa.created_at
            """
        ),
        {"pid": payment_id},
    ).all()

    remaining = amount
    debit_ids: list[uuid.UUID] = []
    for alloc in allocations:
        if remaining <= 0:
            break
        left_on_folio = Decimal(alloc.amount)
        if left_on_folio <= 0:
            # Fully given back by an earlier refund; nothing owed from here.
            continue
        apply_amt = min(remaining, left_on_folio)
        entry_id = uuid.uuid4()
        session.execute(
            text(
                """
                INSERT INTO finance.folio_entries
                    (id, organization_id, property_id, folio_id, entry_type,
                     amount, currency, business_date, source_type, source_id,
                     source_line_key, note)
                VALUES (:id, :org, :prop, :fid, 'debit', :amt, :cur, :bd,
                        'refund', :rid, :slk, :note)
                """
            ),
            {
                "id": entry_id,
                "org": organization_id,
                "prop": property_id,
                "fid": alloc.folio_id,
                "amt": apply_amt,
                "cur": currency,
                "bd": business_date,
                "rid": str(refund_id),
                "slk": f"refund:{refund_id}:{alloc.folio_id}",
                "note": note,
            },
        )
        debit_ids.append(entry_id)
        remaining -= apply_amt

    enqueue_event(
        session,
        aggregate_type="refund",
        aggregate_id=str(refund_id),
        event_type="finance.refund_posted",
        payload={
            "refund_id": str(refund_id),
            "payment_id": str(payment_id),
            "amount": str(amount),
        },
    )
    return RefundResult(refund_id=refund_id, debit_entry_ids=debit_ids)
