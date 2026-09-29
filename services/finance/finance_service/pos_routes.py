"""Restaurant POS: open a check, send rounds to the kitchen, settle at the end.

Built on Guest Services' menu (service categories and items) so there is one
menu and one set of tax rules. See migration 0041 for the data model.

The shape of a meal:

1. **Open a check** at an outlet, on a table or not (a takeaway, the bar).
2. **Add lines.** The price comes from the menu, never from the request, as
   everywhere else money is entered: a client that sends its own price can
   discount a bill to nothing.
3. **Send a KOT.** Everything added since the last ticket goes to the kitchen
   on one numbered ticket. A line on a ticket can still be voided, but only
   with a reason, because the kitchen has already started it.
4. **Settle,** one of two ways:
   * **Pay:** a ``pos`` folio is opened for the check, the lines and their
     tax are posted to it, and the payment is taken against the folio
     balance, tax included. The folio closes as settled at once. Cash goes to
     the settling cashier's open drawer, like cash anywhere else.
   * **Post to room:** the lines go on an in-house guest's open folio and are
     paid at check-out with the rest of the stay.

Settling is where money moves, so it is the step that must not happen twice.
The check is locked for the whole settle, and a check that is not open is
refused.
"""
from __future__ import annotations

import uuid
from decimal import Decimal

from chirala_common import payment_methods
from chirala_common.audit import record_audit
from chirala_common.authz import Caller, assert_property_in_org, build_authz
from chirala_common.routing import TransactionalRoute
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.orm import Session

from .database import get_session
from .ledger import Allocation, LedgerError, folio_balance, post_charge, post_payment
from .routes import _open_shift_of, _trading_day
from .settings import settings

_get_caller, require_permission, _require_org = build_authz(
    get_session, lambda: settings.environment,
    lambda: settings.service_token,
)

pos_router = APIRouter(prefix="/pos", tags=["pos"], route_class=TransactionalRoute)

OUTLET_KINDS = ("restaurant", "bar", "cafe", "room_service", "other")
Q = Decimal("0.01")


def _rows(db: Session, sql: str, params: dict) -> list[dict]:
    return [dict(r) for r in db.execute(text(sql), params).mappings()]


def _one(db: Session, sql: str, params: dict) -> dict | None:
    r = db.execute(text(sql), params).mappings().first()
    return dict(r) if r else None


def _org_of(db: Session, property_id: uuid.UUID) -> uuid.UUID:
    return db.execute(text("SELECT organization_id FROM iam.properties WHERE id = :p"),
                      {"p": property_id}).scalar_one()


# ============================================================== outlets =====

class OutletIn(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    kind: str = "restaurant"
    tables: int = Field(default=0, ge=0, le=100)


class OutletPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=80)
    kind: str | None = None
    is_active: bool | None = None


class TableIn(BaseModel):
    label: str = Field(min_length=1, max_length=20)
    seats: int = Field(default=4, ge=1, le=50)


@pos_router.get("/outlets")
def list_outlets(property_id: uuid.UUID,
                 caller: Caller = Depends(require_permission("payments", "view")),
                 db: Session = Depends(get_session)):
    assert_property_in_org(db, caller, property_id)
    outlets = _rows(db, """
        SELECT o.id, o.name, o.kind, o.is_active, o.sort_order
        FROM finance.pos_outlets o WHERE o.property_id = :p
        ORDER BY o.sort_order, o.name
    """, {"p": property_id})
    tables = _rows(db, """
        SELECT t.id, t.outlet_id, t.label, t.seats, t.is_active
        FROM finance.pos_tables t WHERE t.property_id = :p
        ORDER BY t.outlet_id, length(t.label), t.label
    """, {"p": property_id})
    for o in outlets:
        o["tables"] = [t for t in tables if t["outlet_id"] == o["id"]]
    return outlets


@pos_router.post("/outlets", status_code=201)
def create_outlet(property_id: uuid.UUID, body: OutletIn,
                  caller: Caller = Depends(require_permission("payments", "configure")),
                  db: Session = Depends(get_session)):
    assert_property_in_org(db, caller, property_id)
    if body.kind not in OUTLET_KINDS:
        raise HTTPException(422, "Unknown outlet kind")
    org = _org_of(db, property_id)
    if _one(db, "SELECT 1 AS y FROM finance.pos_outlets WHERE property_id = :p "
                "AND lower(name) = lower(:n)", {"p": property_id, "n": body.name.strip()}):
        raise HTTPException(409, "An outlet with that name already exists.")
    oid = db.execute(text("""
        INSERT INTO finance.pos_outlets (organization_id, property_id, name, kind, sort_order)
        VALUES (:org, :p, :n, :k,
                (SELECT coalesce(max(sort_order), 0) + 1 FROM finance.pos_outlets
                  WHERE property_id = :p))
        RETURNING id
    """), {"org": org, "p": property_id, "n": body.name.strip(), "k": body.kind}).scalar_one()
    for i in range(1, body.tables + 1):
        db.execute(text("""
            INSERT INTO finance.pos_tables (organization_id, property_id, outlet_id, label)
            VALUES (:org, :p, :o, :l)
        """), {"org": org, "p": property_id, "o": oid, "l": f"T{i}"})
    record_audit(db, action="pos.outlet.created", entity_type="pos_outlet",
                 entity_id=str(oid), organization_id=org, property_id=property_id,
                 actor_subject=caller.subject, after=body.model_dump())
    return {"id": oid}


@pos_router.patch("/outlets/{outlet_id}")
def update_outlet(outlet_id: uuid.UUID, property_id: uuid.UUID, body: OutletPatch,
                  caller: Caller = Depends(require_permission("payments", "configure")),
                  db: Session = Depends(get_session)):
    assert_property_in_org(db, caller, property_id)
    if body.kind is not None and body.kind not in OUTLET_KINDS:
        raise HTTPException(422, "Unknown outlet kind")
    before = _one(db, "SELECT organization_id, name, kind, is_active FROM finance.pos_outlets "
                      "WHERE id = :o AND property_id = :p", {"o": outlet_id, "p": property_id})
    if before is None:
        raise HTTPException(404, "Outlet not found")
    db.execute(text("""
        UPDATE finance.pos_outlets SET name = coalesce(:n, name),
               kind = coalesce(:k, kind), is_active = coalesce(:a, is_active)
        WHERE id = :o AND property_id = :p
    """), {"n": body.name, "k": body.kind, "a": body.is_active,
           "o": outlet_id, "p": property_id})
    record_audit(db, action="pos.outlet.updated", entity_type="pos_outlet",
                 entity_id=str(outlet_id), organization_id=before["organization_id"],
                 property_id=property_id, actor_subject=caller.subject,
                 before={k: before[k] for k in ("name", "kind", "is_active")},
                 after={k: v for k, v in body.model_dump().items() if v is not None})
    return {"id": outlet_id}


@pos_router.post("/outlets/{outlet_id}/tables", status_code=201)
def add_table(outlet_id: uuid.UUID, property_id: uuid.UUID, body: TableIn,
              caller: Caller = Depends(require_permission("payments", "configure")),
              db: Session = Depends(get_session)):
    assert_property_in_org(db, caller, property_id)
    outlet = _one(db, "SELECT organization_id FROM finance.pos_outlets "
                      "WHERE id = :o AND property_id = :p", {"o": outlet_id, "p": property_id})
    if outlet is None:
        raise HTTPException(404, "Outlet not found")
    if _one(db, "SELECT 1 AS y FROM finance.pos_tables WHERE outlet_id = :o AND label = :l",
            {"o": outlet_id, "l": body.label.strip()}):
        raise HTTPException(409, "That table already exists in this outlet.")
    tid = db.execute(text("""
        INSERT INTO finance.pos_tables (organization_id, property_id, outlet_id, label, seats)
        VALUES (:org, :p, :o, :l, :s) RETURNING id
    """), {"org": outlet["organization_id"], "p": property_id, "o": outlet_id,
           "l": body.label.strip(), "s": body.seats}).scalar_one()
    return {"id": tid}


# =============================================================== checks =====

def _next(db: Session, property_id: uuid.UUID, org, field: str) -> int:
    """The next check or KOT number for this property, under a row lock."""
    db.execute(text("""
        INSERT INTO finance.pos_counters (property_id, organization_id)
        VALUES (:p, :org) ON CONFLICT (property_id) DO NOTHING
    """), {"p": property_id, "org": org})
    return db.execute(text(
        f"UPDATE finance.pos_counters SET {field} = {field} + 1 "
        f"WHERE property_id = :p RETURNING {field} - 1"), {"p": property_id}).scalar_one()


def _check(db: Session, check_id: uuid.UUID, property_id: uuid.UUID, *,
           lock: bool = False) -> dict:
    row = _one(db, "SELECT * FROM finance.pos_checks WHERE id = :c AND property_id = :p"
               + (" FOR UPDATE" if lock else ""), {"c": check_id, "p": property_id})
    if row is None:
        raise HTTPException(404, "Check not found")
    return row


def _view(db: Session, check_id: uuid.UUID, property_id: uuid.UUID) -> dict:
    c = _one(db, """
        SELECT c.*, o.name AS outlet_name, t.label AS table_label
        FROM finance.pos_checks c
        JOIN finance.pos_outlets o ON o.id = c.outlet_id
        LEFT JOIN finance.pos_tables t ON t.id = c.table_id
        WHERE c.id = :c AND c.property_id = :p
    """, {"c": check_id, "p": property_id})
    if c is None:
        raise HTTPException(404, "Check not found")
    c["lines"] = _rows(db, """
        SELECT l.id, l.item_id, l.item_name, l.category, l.quantity, l.unit_price,
               l.amount, l.note, l.status, l.void_reason, l.created_at,
               k.number AS kot_number
        FROM finance.pos_check_lines l
        LEFT JOIN finance.pos_kots k ON k.id = l.kot_id
        WHERE l.check_id = :c ORDER BY l.created_at
    """, {"c": check_id})
    c["unsent"] = sum(1 for l in c["lines"] if l["status"] == "active" and l["kot_number"] is None)
    if c["folio_id"]:
        # The bill as the ledger posted it: each line and the tax it attracted.
        c["tax"] = db.execute(text("""
            SELECT coalesce(sum(amount), 0) FROM finance.folio_entries
            WHERE folio_id = :f AND source_id = :c AND entry_type = 'debit'
              AND source_line_key LIKE 'pos:%#tax'
        """), {"f": c["folio_id"], "c": str(check_id)}).scalar()
    return c


class CheckIn(BaseModel):
    outlet_id: uuid.UUID
    table_id: uuid.UUID | None = None
    covers: int | None = Field(default=None, ge=1, le=200)
    guest_label: str | None = Field(default=None, max_length=120)


@pos_router.get("/checks")
def list_checks(property_id: uuid.UUID, status: str = "open",
                outlet_id: uuid.UUID | None = None,
                caller: Caller = Depends(require_permission("payments", "view")),
                db: Session = Depends(get_session)):
    assert_property_in_org(db, caller, property_id)
    return _rows(db, """
        SELECT c.id, c.number, c.status, c.outlet_id, o.name AS outlet_name,
               c.table_id, t.label AS table_label, c.covers, c.guest_label,
               c.subtotal, c.total, c.method, c.opened_at, c.closed_at,
               (SELECT count(*) FROM finance.pos_check_lines l
                 WHERE l.check_id = c.id AND l.status = 'active') AS items
        FROM finance.pos_checks c
        JOIN finance.pos_outlets o ON o.id = c.outlet_id
        LEFT JOIN finance.pos_tables t ON t.id = c.table_id
        WHERE c.property_id = :p
          AND (CAST(:st AS text) = 'all' OR c.status = CAST(:st AS text))
          AND (CAST(:o AS uuid) IS NULL OR c.outlet_id = CAST(:o AS uuid))
        ORDER BY c.opened_at DESC LIMIT 200
    """, {"p": property_id, "st": status, "o": outlet_id})


@pos_router.post("/checks", status_code=201)
def open_check(property_id: uuid.UUID, body: CheckIn,
               caller: Caller = Depends(require_permission("payments", "create")),
               db: Session = Depends(get_session)):
    assert_property_in_org(db, caller, property_id)
    outlet = _one(db, "SELECT organization_id, is_active FROM finance.pos_outlets "
                      "WHERE id = :o AND property_id = :p",
                  {"o": body.outlet_id, "p": property_id})
    if outlet is None or not outlet["is_active"]:
        raise HTTPException(404, "Outlet not found")
    if body.table_id is not None:
        if not _one(db, "SELECT 1 AS y FROM finance.pos_tables WHERE id = :t "
                        "AND outlet_id = :o AND is_active", {"t": body.table_id, "o": body.outlet_id}):
            raise HTTPException(404, "Table not found in this outlet")
        busy = _one(db, "SELECT number FROM finance.pos_checks WHERE table_id = :t "
                        "AND status = 'open'", {"t": body.table_id})
        if busy:
            raise HTTPException(409, f"That table already has check {busy['number']} open.")
    org = outlet["organization_id"]
    number = f"C{_next(db, property_id, org, 'next_check'):05d}"
    currency = db.execute(text("SELECT currency FROM iam.properties WHERE id = :p"),
                          {"p": property_id}).scalar() or "INR"
    cid = db.execute(text("""
        INSERT INTO finance.pos_checks
            (organization_id, property_id, outlet_id, table_id, number, covers,
             guest_label, business_date, currency, opened_by)
        VALUES (:org, :p, :o, :t, :n, :cov, :g, :bd, :cur, :by) RETURNING id
    """), {"org": org, "p": property_id, "o": body.outlet_id, "t": body.table_id,
           "n": number, "cov": body.covers, "g": (body.guest_label or "").strip() or None,
           "bd": _trading_day(db, property_id), "cur": currency,
           "by": caller.user_id}).scalar_one()
    return _view(db, cid, property_id)


@pos_router.get("/checks/{check_id}")
def get_check(check_id: uuid.UUID, property_id: uuid.UUID,
              caller: Caller = Depends(require_permission("payments", "view")),
              db: Session = Depends(get_session)):
    assert_property_in_org(db, caller, property_id)
    return _view(db, check_id, property_id)


class LineIn(BaseModel):
    item_id: uuid.UUID
    quantity: int = Field(default=1, ge=1, le=999)
    note: str | None = Field(default=None, max_length=200)


class LinesIn(BaseModel):
    lines: list[LineIn] = Field(min_length=1, max_length=100)


def _recompute(db: Session, check_id: uuid.UUID) -> None:
    db.execute(text("""
        UPDATE finance.pos_checks SET subtotal = coalesce((
            SELECT sum(amount) FROM finance.pos_check_lines
             WHERE check_id = :c AND status = 'active'), 0)
        WHERE id = :c
    """), {"c": check_id})


@pos_router.post("/checks/{check_id}/lines")
def add_lines(check_id: uuid.UUID, property_id: uuid.UUID, body: LinesIn,
              caller: Caller = Depends(require_permission("payments", "create")),
              db: Session = Depends(get_session)):
    assert_property_in_org(db, caller, property_id)
    c = _check(db, check_id, property_id, lock=True)
    if c["status"] != "open":
        raise HTTPException(409, f"Check {c['number']} is {c['status']}.")
    ids = [l.item_id for l in body.lines]
    items = {r["id"]: r for r in _rows(db, """
        SELECT i.id, i.name, i.price, c.name AS category, c.bills_as
        FROM finance.service_items i
        JOIN finance.service_categories c ON c.id = i.category_id
        WHERE i.property_id = :p AND i.id = ANY(:ids) AND i.is_active AND c.is_active
    """, {"p": property_id, "ids": ids})}
    missing = [str(i) for i in ids if i not in items]
    if missing:
        raise HTTPException(422, "Some items are not on this property's menu: "
                                 + ", ".join(missing))
    for line in body.lines:
        item = items[line.item_id]
        db.execute(text("""
            INSERT INTO finance.pos_check_lines
                (organization_id, property_id, check_id, item_id, item_name,
                 category, bills_as, quantity, unit_price, amount, note, created_by)
            VALUES (:org, :p, :c, :i, :n, :cat, :b, :q, :u, :a, :note, :by)
        """), {"org": c["organization_id"], "p": property_id, "c": check_id,
               "i": item["id"], "n": item["name"], "cat": item["category"],
               "b": item["bills_as"], "q": line.quantity, "u": item["price"],
               "a": (Decimal(item["price"]) * line.quantity).quantize(Q),
               "note": (line.note or "").strip() or None, "by": caller.user_id})
    _recompute(db, check_id)
    return _view(db, check_id, property_id)


class VoidIn(BaseModel):
    reason: str | None = Field(default=None, max_length=200)


@pos_router.post("/checks/{check_id}/lines/{line_id}/void")
def void_line(check_id: uuid.UUID, line_id: uuid.UUID, property_id: uuid.UUID,
              body: VoidIn,
              caller: Caller = Depends(require_permission("payments", "create")),
              db: Session = Depends(get_session)):
    assert_property_in_org(db, caller, property_id)
    c = _check(db, check_id, property_id, lock=True)
    if c["status"] != "open":
        raise HTTPException(409, f"Check {c['number']} is {c['status']}.")
    line = _one(db, "SELECT id, kot_id, status, item_name FROM finance.pos_check_lines "
                    "WHERE id = :l AND check_id = :c", {"l": line_id, "c": check_id})
    if line is None or line["status"] != "active":
        raise HTTPException(404, "Line not found")
    reason = (body.reason or "").strip()
    if line["kot_id"] and not reason:
        # The kitchen already has it. Why it came off the bill is the question
        # a manager asks at the end of the night.
        raise HTTPException(422, "This item has gone to the kitchen. Give a reason to void it.")
    db.execute(text("UPDATE finance.pos_check_lines SET status = 'void', void_reason = :r "
                    "WHERE id = :l"), {"r": reason or None, "l": line_id})
    _recompute(db, check_id)
    if line["kot_id"]:
        record_audit(db, action="pos.line.voided", entity_type="pos_check",
                     entity_id=str(check_id), organization_id=c["organization_id"],
                     property_id=property_id, actor_subject=caller.subject,
                     after={"item": line["item_name"], "reason": reason})
    return _view(db, check_id, property_id)


@pos_router.post("/checks/{check_id}/kot")
def send_kot(check_id: uuid.UUID, property_id: uuid.UUID,
             caller: Caller = Depends(require_permission("payments", "create")),
             db: Session = Depends(get_session)):
    """One ticket for everything added since the last one."""
    assert_property_in_org(db, caller, property_id)
    c = _check(db, check_id, property_id, lock=True)
    if c["status"] != "open":
        raise HTTPException(409, f"Check {c['number']} is {c['status']}.")
    pending = _rows(db, "SELECT id FROM finance.pos_check_lines WHERE check_id = :c "
                        "AND status = 'active' AND kot_id IS NULL", {"c": check_id})
    if not pending:
        raise HTTPException(409, "Nothing new to send to the kitchen.")
    number = _next(db, property_id, c["organization_id"], "next_kot")
    kid = db.execute(text("""
        INSERT INTO finance.pos_kots (organization_id, property_id, check_id, number, created_by)
        VALUES (:org, :p, :c, :n, :by) RETURNING id
    """), {"org": c["organization_id"], "p": property_id, "c": check_id, "n": number,
           "by": caller.user_id}).scalar_one()
    db.execute(text("UPDATE finance.pos_check_lines SET kot_id = :k WHERE id = ANY(:ids)"),
               {"k": kid, "ids": [r["id"] for r in pending]})
    view = _view(db, check_id, property_id)
    view["kot"] = {"number": number,
                   "lines": [l for l in view["lines"] if l["kot_number"] == number]}
    return view


class SettleIn(BaseModel):
    mode: str = Field(pattern="^(pay|room)$")
    #: For "pay": how the diner paid.
    method: str | None = None
    reference: str | None = Field(default=None, max_length=120)
    #: For "room": the in-house guest's open folio.
    folio_id: uuid.UUID | None = None


def _post_lines(db: Session, c: dict, folio_id: uuid.UUID, currency: str,
                property_id: uuid.UUID, caller: Caller) -> None:
    lines = _rows(db, "SELECT id, item_name, quantity, unit_price, amount, bills_as "
                      "FROM finance.pos_check_lines WHERE check_id = :c AND status = 'active' "
                      "ORDER BY created_at", {"c": c["id"]})
    if not lines:
        raise HTTPException(409, "There is nothing on this check to settle.")
    for n, l in enumerate(lines, start=1):
        try:
            post_charge(
                db, organization_id=c["organization_id"], property_id=property_id,
                folio_id=folio_id, amount=Decimal(l["amount"]),
                business_date=c["business_date"], source_type=l["bills_as"],
                source_line_key=f"pos:{c['id']}:{n}", source_id=str(c["id"]),
                currency=currency, note=f"{l['item_name']} ({c['number']})",
                quantity=Decimal(l["quantity"]), unit_amount=Decimal(l["unit_price"]),
                posted_by=caller.subject)
        except LedgerError as exc:
            raise HTTPException(409 if exc.conflict else 422, str(exc)) from exc


@pos_router.post("/checks/{check_id}/settle")
def settle(check_id: uuid.UUID, property_id: uuid.UUID, body: SettleIn,
           caller: Caller = Depends(require_permission("payments", "create")),
           db: Session = Depends(get_session)):
    assert_property_in_org(db, caller, property_id)
    c = _check(db, check_id, property_id, lock=True)
    if c["status"] != "open":
        raise HTTPException(409, f"Check {c['number']} is already {c['status']}.")
    currency = c["currency"]

    if body.mode == "room":
        if body.folio_id is None:
            raise HTTPException(422, "Choose the guest's folio.")
        folio = _one(db, "SELECT id, status, reservation_id FROM finance.folios "
                         "WHERE id = :f AND property_id = :p",
                     {"f": body.folio_id, "p": property_id})
        if folio is None or folio["status"] != "open" or folio["reservation_id"] is None:
            raise HTTPException(409, "That guest's bill is not open.")
        _post_lines(db, c, folio["id"], currency, property_id, caller)
        db.execute(text("""
            UPDATE finance.pos_checks SET status = 'room', folio_id = :f,
                   reservation_id = :r, total = subtotal, closed_by = :by, closed_at = now()
            WHERE id = :c
        """), {"f": folio["id"], "r": folio["reservation_id"], "by": caller.user_id,
               "c": check_id})
        record_audit(db, action="pos.check.posted_to_room", entity_type="pos_check",
                     entity_id=str(check_id), organization_id=c["organization_id"],
                     property_id=property_id, actor_subject=caller.subject,
                     after={"number": c["number"], "folio": str(folio["id"])})
        return _view(db, check_id, property_id)

    method = payment_methods.normalise(body.method or "")
    if method not in payment_methods.METHODS:
        raise HTTPException(422, "Choose how the bill was paid.")
    if method in payment_methods.NEEDS_REFERENCE and not (body.reference or "").strip():
        raise HTTPException(422, "Add the payment reference.")

    folio_id = uuid.uuid4()
    db.execute(text("""
        INSERT INTO finance.folios (id, organization_id, property_id, type, currency, status)
        VALUES (:id, :org, :p, 'pos', :cur, 'open')
    """), {"id": folio_id, "org": c["organization_id"], "p": property_id, "cur": currency})
    _post_lines(db, c, folio_id, currency, property_id, caller)
    due = folio_balance(db, folio_id)
    try:
        paid = post_payment(
            db, organization_id=c["organization_id"], property_id=property_id,
            method=method, business_date=c["business_date"],
            allocations=[Allocation(folio_id=folio_id, amount=due)],
            currency=currency, source="pos",
            reference=(body.reference or "").strip() or None,
            note=f"POS check {c['number']}", posted_by=caller.subject,
            cashier_shift_id=_open_shift_of(db, caller, property_id))
    except LedgerError as exc:
        raise HTTPException(409 if exc.conflict else 422, str(exc)) from exc
    db.execute(text("UPDATE finance.folios SET status = 'settled' WHERE id = :f"),
               {"f": folio_id})
    db.execute(text("""
        UPDATE finance.pos_checks SET status = 'settled', folio_id = :f, payment_id = :pay,
               method = :m, total = :t, closed_by = :by, closed_at = now()
        WHERE id = :c
    """), {"f": folio_id, "pay": paid.payment_id, "m": method, "t": due,
           "by": caller.user_id, "c": check_id})
    record_audit(db, action="pos.check.settled", entity_type="pos_check",
                 entity_id=str(check_id), organization_id=c["organization_id"],
                 property_id=property_id, actor_subject=caller.subject,
                 after={"number": c["number"], "total": str(due), "method": method})
    return _view(db, check_id, property_id)


@pos_router.post("/checks/{check_id}/void")
def void_check(check_id: uuid.UUID, property_id: uuid.UUID, body: VoidIn,
               caller: Caller = Depends(require_permission("payments", "create")),
               db: Session = Depends(get_session)):
    assert_property_in_org(db, caller, property_id)
    c = _check(db, check_id, property_id, lock=True)
    if c["status"] != "open":
        raise HTTPException(409, f"Check {c['number']} is {c['status']}.")
    sent = _one(db, "SELECT 1 AS y FROM finance.pos_check_lines WHERE check_id = :c "
                    "AND status = 'active' AND kot_id IS NOT NULL", {"c": check_id})
    reason = (body.reason or "").strip()
    if sent and not reason:
        raise HTTPException(422, "Food has gone to the kitchen. Give a reason to void the check.")
    db.execute(text("UPDATE finance.pos_checks SET status = 'void', void_reason = :r, "
                    "closed_by = :by, closed_at = now() WHERE id = :c"),
               {"r": reason or None, "by": caller.user_id, "c": check_id})
    record_audit(db, action="pos.check.voided", entity_type="pos_check",
                 entity_id=str(check_id), organization_id=c["organization_id"],
                 property_id=property_id, actor_subject=caller.subject,
                 after={"number": c["number"], "reason": reason})
    return _view(db, check_id, property_id)
