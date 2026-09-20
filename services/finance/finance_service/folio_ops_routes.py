"""Splitting, transferring and cutting a folio.

Three operations a front desk does constantly and this system could not do at
all: move a charge onto a second folio because the company pays the room and
the guest pays the bar; open that second folio in the first place; and close a
folio mid-stay so what follows goes onto a fresh one.

All three are the same underlying act -- money moving between two folios on
one booking -- so they share one implementation.

**Entries are never moved.** A folio entry is immutable; the ledger's whole
credibility rests on that. Re-pointing a row's ``folio_id`` would make the
source folio's history silently different from what it was when the guest was
shown it, and there would be no record that anything had happened. So a
transfer posts a *pair*: a credit on the folio giving the money up and a debit
on the folio taking it, each naming the other. Both folios still add up, both
still show what was on them, and the movement is visible on both.

**Nothing is taxed.** The tax was settled when the original charge was posted.
Taxing the transfer would tax one supply twice, so the pair is written
directly rather than through ``post_charge`` -- which is also why neither
transfer type has a tax category.

**Cutting is not closing and reopening the same folio.** A cut ends a folio
and starts its successor, so the first can be settled and invoiced while the
stay continues. The old folio keeps every line it had.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

from chirala_common import gstin as gstin_rule
from chirala_common.audit import record_audit
from chirala_common.authz import Caller, assert_property_in_org, build_authz
from chirala_common.routing import TransactionalRoute
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.orm import Session

from .database import get_session
from .routes import _trading_day
from .settings import settings

_get_caller, require_permission, require_org_permission = build_authz(
    get_session, lambda: settings.environment,
    lambda: settings.service_token,
)

folio_ops_router = APIRouter(tags=["folio operations"],
                             route_class=TransactionalRoute)

#: A folio a person opened, as against the guest folio a booking starts with.
EXTRA_TYPES = ("guest", "company", "paymaster")


#: When the invoice number comes out of the fiscal series. Mirrors the check
#: constraint on the column -- one list, so a value the API accepts is one the
#: database will take.
INVOICE_TIMINGS = ("on_checkout", "post_checkout")


class FolioRow(BaseModel):
    id: uuid.UUID
    folio_no: str | None
    type: str
    status: str
    currency: str
    balance: Decimal
    entry_count: int
    #: Who the folio bills, how it prints, and when it is numbered. All
    #: optional: a folio opened before these existed reports the defaults.
    sharer_name: str | None = None
    gstin: str | None = None
    show_tax_on_folio: bool = True
    invoice_number_timing: str = "on_checkout"
    #: Null on a master folio; on a child, the folio it hangs off. One level
    #: only -- see the migration.
    parent_folio_id: uuid.UUID | None = None


def _folio(db: Session, folio_id: uuid.UUID, property_id: uuid.UUID) -> dict:
    row = db.execute(
        text("SELECT id, reservation_id, organization_id, property_id, type, "
             "       status, currency, folio_no "
             "  FROM finance.folios WHERE id = :f AND property_id = :p"),
        {"f": folio_id, "p": property_id},
    ).mappings().first()
    if row is None:
        raise HTTPException(404, "No such folio at this property.")
    return dict(row)


def _rows(db: Session, reservation_id: uuid.UUID) -> list[FolioRow]:
    rs = db.execute(
        text(
            """
            SELECT f.id, f.folio_no, f.type, f.status, f.currency,
                   f.sharer_name, f.gstin, f.show_tax_on_folio,
                   f.invoice_number_timing, f.parent_folio_id,
                   COALESCE(sum(CASE WHEN e.entry_type = 'debit' THEN e.amount
                                     ELSE -e.amount END), 0) AS balance,
                   count(e.id) AS entry_count
              FROM finance.folios f
              LEFT JOIN finance.folio_entries e ON e.folio_id = f.id
             WHERE f.reservation_id = :r
             GROUP BY f.id, f.folio_no, f.type, f.status, f.currency,
                      f.sharer_name, f.gstin, f.show_tax_on_folio,
                      f.invoice_number_timing, f.parent_folio_id
             -- Masters first, each before its own children, so the caller can
             -- draw the tree by walking the list once.
             ORDER BY (f.parent_folio_id IS NOT NULL), f.created_at
            """
        ),
        {"r": reservation_id},
    ).mappings().all()
    return [FolioRow(**dict(x)) for x in rs]


@folio_ops_router.get("/reservations/{reservation_id}/folios",
                      response_model=list[FolioRow])
def list_folios(
    reservation_id: uuid.UUID,
    property_id: uuid.UUID,
    caller: Caller = Depends(require_permission("payments", "view")),
    db: Session = Depends(get_session),
):
    """Every folio on this booking, with what is on each."""
    assert_property_in_org(db, caller, property_id)
    return _rows(db, reservation_id)


class OpenIn(BaseModel):
    reservation_id: uuid.UUID
    type: str = "guest"
    label: str | None = Field(default=None, max_length=80)
    #: The name this folio bills. Free text because this system has no sharer
    #: table -- only the primary guest is linked to a booking -- so a key here
    #: would claim a relationship that does not exist.
    sharer_name: str | None = Field(default=None, max_length=120)
    #: The *recipient's* GSTIN, which is what makes the printed bill a B2B tax
    #: invoice. Validated below against the shared rule, not merely length-
    #: capped: a malformed GSTIN on an invoice is the guest's problem months
    #: later, when they cannot reclaim the tax.
    gstin: str | None = Field(default=None, max_length=20)
    show_tax_on_folio: bool = True
    invoice_number_timing: str = "on_checkout"
    #: Which folio this one hangs off. Omitted means "the booking's master",
    #: which is what the desk means every time it opens a second folio from a
    #: booking that has one.
    parent_folio_id: uuid.UUID | None = None


# A folio's number is assigned by the database, in `finance.set_folio_number`,
# from the per-property counter in `finance.folio_number_counters`. Nothing
# here sets `folio_no`, and nothing here should.
#
# There used to be a second allocator in this module: MAX(folio_no) + 1, read
# and written in Python. Because it set the column explicitly the trigger never
# fired, so the counter was never advanced -- every split folio left it one
# behind, silently, and the drift only surfaced when the counter came back
# round to a number that already existed. At that point the unique index did
# the only thing it can and refused the insert, so the NEXT CHECK-IN at that
# property failed with an integrity error. The property had done nothing
# wrong; it had simply split a folio once.
#
# Two allocators for one sequence cannot be kept in step, so there is one.


@folio_ops_router.post("/folios/open", response_model=FolioRow,
                       status_code=status.HTTP_201_CREATED)
def open_folio(
    property_id: uuid.UUID,
    body: OpenIn,
    caller: Caller = Depends(require_permission("payments", "create")),
    db: Session = Depends(get_session),
):
    """Open another folio on a booking — the split half of Split Folio."""
    assert_property_in_org(db, caller, property_id)
    if body.type not in EXTRA_TYPES:
        raise HTTPException(422, f"Unknown folio type {body.type!r}.")
    if body.invoice_number_timing not in INVOICE_TIMINGS:
        raise HTTPException(
            422,
            f"Unknown invoice numbering {body.invoice_number_timing!r}. "
            "Expected one of: " + ", ".join(INVOICE_TIMINGS) + ".")

    # The same rule the field applies while it is being typed, and the same
    # sentence back. `chirala_common.gstin` knows every GST state code in
    # India, so this holds wherever the property is.
    gstin = gstin_rule.normalise(body.gstin)
    if gstin is not None:
        wrong = gstin_rule.problem(gstin)
        if wrong:
            raise HTTPException(422, wrong)

    head = db.execute(
        text("SELECT organization_id, currency FROM booking.reservations "
             " WHERE id = :r AND property_id = :p"),
        {"r": body.reservation_id, "p": property_id},
    ).mappings().first()
    if head is None:
        raise HTTPException(404, "No such booking at this property.")

    # A child of a child is a hierarchy nobody at a desk can hold in their
    # head, and no billing arrangement needs one. So a parent that is itself a
    # child resolves to *its* master, and the tree stays two deep whatever the
    # caller asks for.
    parent = body.parent_folio_id
    if parent is not None:
        row = db.execute(
            text("SELECT id, parent_folio_id, reservation_id "
                 "  FROM finance.folios WHERE id = :f AND property_id = :p"),
            {"f": parent, "p": property_id},
        ).mappings().first()
        if row is None:
            raise HTTPException(404, "No such folio at this property.")
        if row["reservation_id"] != body.reservation_id:
            raise HTTPException(
                422, "That folio belongs to a different booking.")
        parent = row["parent_folio_id"] or row["id"]
    else:
        # Default: hang it off whatever this booking already has.
        parent = db.execute(
            text("SELECT id FROM finance.folios "
                 " WHERE reservation_id = :r AND parent_folio_id IS NULL "
                 " ORDER BY created_at LIMIT 1"),
            {"r": body.reservation_id},
        ).scalar()

    fid = uuid.uuid4()
    db.execute(
        text(
            """
            INSERT INTO finance.folios
                (id, organization_id, property_id, reservation_id, type,
                 currency, status, sharer_name, gstin,
                 show_tax_on_folio, invoice_number_timing, parent_folio_id)
            VALUES (:id, :org, :prop, :res, :type, :cur, 'open',
                    :sharer, :gstin, :show_tax, :timing, :parent)
            """
        ),
        {"id": fid, "org": head["organization_id"], "prop": property_id,
         "res": body.reservation_id, "type": body.type,
         "cur": head["currency"] or "INR",
         "sharer": (body.sharer_name or "").strip() or None,
         "gstin": gstin,
         "show_tax": body.show_tax_on_folio,
         "timing": body.invoice_number_timing,
         "parent": parent},
    )
    record_audit(
        db, actor_subject=caller.subject, action="folio.opened",
        entity_type="folio", entity_id=str(fid), property_id=property_id,
        after={"reservation_id": str(body.reservation_id), "type": body.type,
               "sharer_name": body.sharer_name, "gstin": gstin,
               "show_tax_on_folio": body.show_tax_on_folio,
               "invoice_number_timing": body.invoice_number_timing,
               "parent_folio_id": str(parent) if parent else None},
    )
    return next(f for f in _rows(db, body.reservation_id) if f.id == fid)


class TransferIn(BaseModel):
    to_folio_id: uuid.UUID
    #: The entries to move. Empty means the whole balance, which is what Cut
    #: Folio does.
    entry_ids: list[uuid.UUID] = Field(default_factory=list)
    reason: str | None = Field(default=None, max_length=300)


class TransferOut(BaseModel):
    moved: Decimal
    lines: int
    folios: list[FolioRow]


def _pair(db: Session, *, src: dict, dst: dict, amount: Decimal,
          note: str, key: str) -> None:
    """The two halves of a transfer, written together or not at all.

    Both halves take ONE business date, resolved once before the loop. A
    transfer is a single event and its two entries have to agree: read
    per-row, the pair could straddle a date boundary and leave one folio
    credited on a day the other was never debited on -- the two sides of a
    transfer landing on different days is the one thing a transfer must never
    do.
    """
    # The trading day, not CURRENT_DATE. This was UTC, which is neither the
    # property's today nor the day the ledger has open.
    day = _trading_day(db, src["property_id"])
    for folio, kind, source in ((src, "credit", "folio_transfer_out"),
                                (dst, "debit", "folio_transfer_in")):
        db.execute(
            text(
                """
                INSERT INTO finance.folio_entries
                    (id, organization_id, property_id, folio_id, entry_type,
                     amount, currency, business_date, source_type,
                     source_line_key, note)
                VALUES (:id, :org, :prop, :fid, :etype, :amt, :cur,
                        :bd, :st, :slk, :note)
                """
            ),
            {"id": uuid.uuid4(), "org": folio["organization_id"],
             "prop": folio["property_id"], "fid": folio["id"], "bd": day,
             "etype": kind, "amt": amount, "cur": folio["currency"] or "INR",
             "st": source, "slk": f"{key}:{source}", "note": note},
        )


@folio_ops_router.post("/folios/{folio_id}/transfer",
                       response_model=TransferOut)
def transfer(
    folio_id: uuid.UUID,
    property_id: uuid.UUID,
    body: TransferIn,
    caller: Caller = Depends(require_permission("payments", "create")),
    db: Session = Depends(get_session),
):
    """Move charges from this folio to another on the same booking."""
    assert_property_in_org(db, caller, property_id)
    src = _folio(db, folio_id, property_id)
    dst = _folio(db, body.to_folio_id, property_id)

    if src["id"] == dst["id"]:
        raise HTTPException(422, "That is the same folio.")
    # Two folios on different bookings are two guests' money, and moving one
    # to the other is not a transfer, it is a mistake.
    if src["reservation_id"] != dst["reservation_id"]:
        raise HTTPException(
            422, "Both folios have to belong to the same booking.")
    if src["status"] != "open" or dst["status"] != "open":
        raise HTTPException(
            409, "Both folios have to be open to move anything between them.")

    if body.entry_ids:
        rows = db.execute(
            text(
                """
                SELECT id, entry_type, amount, source_type, note
                  FROM finance.folio_entries
                 WHERE folio_id = :f AND id = ANY(:ids)
                """
            ),
            {"f": folio_id, "ids": body.entry_ids},
        ).mappings().all()
        if len(rows) != len(set(body.entry_ids)):
            raise HTTPException(
                404, "Some of those lines are not on this folio.")
        # Signed, so transferring a charge and its later credit nets correctly
        # rather than moving the gross twice.
        amount = sum((r["amount"] if r["entry_type"] == "debit"
                      else -r["amount"]) for r in rows)
        lines = len(rows)
        note = body.reason or (
            f"{lines} line{'s' if lines != 1 else ''} moved "
            f"from {src['folio_no'] or 'folio'}")
    else:
        amount = db.execute(
            text("SELECT COALESCE(sum(CASE WHEN entry_type = 'debit' "
                 "       THEN amount ELSE -amount END), 0) "
                 "  FROM finance.folio_entries WHERE folio_id = :f"),
            {"f": folio_id},
        ).scalar_one()
        lines = 1
        note = body.reason or f"Balance moved from {src['folio_no'] or 'folio'}"

    if amount == 0:
        raise HTTPException(422, "There is nothing to move — that comes to nil.")
    if amount < 0:
        # Moving a credit balance means the *other* folio gives money up, and
        # writing the pair the same way round would put the credit on the
        # wrong side of both.
        raise HTTPException(
            422, "That comes to a credit. Move it the other way round.")

    _pair(db, src=src, dst=dst, amount=Decimal(str(amount)), note=note,
          key=f"transfer:{uuid.uuid4()}")

    record_audit(
        db, actor_subject=caller.subject, action="folio.transferred",
        entity_type="folio", entity_id=str(folio_id), property_id=property_id,
        after={"to_folio_id": str(dst["id"]), "amount": str(amount),
               "lines": lines, "reason": body.reason},
    )
    return TransferOut(moved=Decimal(str(amount)), lines=lines,
                       folios=_rows(db, src["reservation_id"]))


class CutIn(BaseModel):
    reason: str | None = Field(default=None, max_length=300)


class CutOut(BaseModel):
    closed_folio_id: uuid.UUID
    new_folio_id: uuid.UUID
    folios: list[FolioRow]


@folio_ops_router.post("/folios/{folio_id}/cut", response_model=CutOut)
def cut(
    folio_id: uuid.UUID,
    property_id: uuid.UUID,
    body: CutIn,
    caller: Caller = Depends(require_permission("payments", "create")),
    db: Session = Depends(get_session),
):
    """Close this folio and start its successor.

    For a stay that has to be billed in parts — a long stay invoiced weekly, a
    company paying to a date. The closed folio keeps every line it had and can
    be settled and invoiced; anything posted from now on lands on the new one.

    A folio still owing money is not cut: closing it would leave a debt on a
    folio nobody looks at again. Settle it, or move the balance across first.
    """
    assert_property_in_org(db, caller, property_id)
    src = _folio(db, folio_id, property_id)
    if src["status"] != "open":
        raise HTTPException(409, "This folio is already closed.")

    balance = db.execute(
        text("SELECT COALESCE(sum(CASE WHEN entry_type = 'debit' "
             "       THEN amount ELSE -amount END), 0) "
             "  FROM finance.folio_entries WHERE folio_id = :f"),
        {"f": folio_id},
    ).scalar_one()
    if balance != 0:
        raise HTTPException(
            409, f"This folio still stands at {balance}. Settle it or move the "
                 f"balance to another folio before cutting.")

    new_id = uuid.uuid4()
    db.execute(
        text(
            """
            INSERT INTO finance.folios
                (id, organization_id, property_id, reservation_id, type,
                 currency, status)
            VALUES (:id, :org, :prop, :res, :type, :cur, 'open')
            """
        ),
        {"id": new_id, "org": src["organization_id"], "prop": property_id,
         "res": src["reservation_id"], "type": src["type"],
         "cur": src["currency"] or "INR"},
    )
    db.execute(
        text("UPDATE finance.folios SET status = 'closed', updated_at = now(), "
             "    version = version + 1 WHERE id = :f"),
        {"f": folio_id},
    )
    record_audit(
        db, actor_subject=caller.subject, action="folio.cut",
        entity_type="folio", entity_id=str(folio_id), property_id=property_id,
        after={"new_folio_id": str(new_id), "reason": body.reason},
    )
    return CutOut(closed_folio_id=folio_id, new_folio_id=new_id,
                  folios=_rows(db, src["reservation_id"]))
