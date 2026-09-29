"""Guest services: the priced list, and putting an order on a guest's bill.

The ledger could already take a charge. What it could not take was *what the
charge was for*. The existing dialog asked for a department and an amount, so
a folio line said "restaurant 340" and nothing recorded that it was two dosas
and a coffee -- which is the one question a guest actually asks at check-out.

Three ideas hold this together.

**The catalogue decides the tax, not the person taking the order.** An item
belongs to a category the *property* named, and that category says which
department it bills under. The ledger turns a department into a tax category
(``tax_engine.SOURCE_CATEGORY``). So choosing "Filter Coffee" is enough, and
nobody taking an order has to know any of it.

The two halves are deliberately separate. Category names are the property's
own vocabulary and are not constrained at all -- "Tiffin", "Backwater Cruise",
"Conference Hire", whatever they actually sell. ``bills_as`` is the
accounting vocabulary and *is* constrained, because a department the tax
engine does not recognise posts a charge with no tax and tells nobody until a
GST filing. 0029 got this wrong: it wrote one property's nine categories into
a check constraint, "Tiffin" included, which is a word that means something in
Andhra and nothing in Rajasthan. 0031 separated them.

**An order is posted, not parked.** There is no open/served/cancelled state
here, because this property takes the order and puts it on the bill. A
mistake is undone the way every other folio mistake is undone -- a folio
adjustment, which already exists, already needs approval over a threshold, and
already leaves a trail. Inventing a second way to un-bill something would mean
two records of the truth.

**The bill keeps saying what it said.** Lines snapshot the item's name and
price. Re-pricing the menu in November must not rewrite an October guest's
bill, and deleting an item must not blank out a bill somebody has paid.

One folio entry per line, not one per order, so the folio shows the breakdown
without needing to know anything about orders. Each entry is keyed
``service:<order>:<line>`` and ``folio_entries`` is unique on
``(folio_id, source_type, source_line_key)``, which makes *replaying one
order* harmless.

That is not the same as making the Post button safe, and an early version of
this file claimed it was. A second tap builds a second order row, so the key
differs and the ledger has every reason to accept it -- one coffee, billed
twice. Only the client knows two submissions are the same submission, so the
client says so: ``client_key`` is generated once per Post and repeated on
every retry of it. A key already seen returns the order it created.
"""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

from chirala_common.audit import record_audit
from chirala_common.authz import (
    Caller,
    assert_property_in_org,
    build_authz,
    caller_org,
    require_property_permission,
)
from chirala_common.routing import TransactionalRoute
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.orm import Session

from .database import get_session
from .night_audit import local_today
from .ledger import LedgerError, post_charge
from .settings import settings

_get_caller, require_permission, require_org_permission = build_authz(
    get_session, lambda: settings.environment,
    lambda: settings.service_token,
)

service_router = APIRouter(tags=["guest services"],
                           route_class=TransactionalRoute)

#: The departments the ledger can price tax for -- the keys of
#: ``tax_engine.SOURCE_CATEGORY``. A property names its own categories; this
#: is the short list each one has to point at so the tax resolves. Widening it
#: here without widening the tax engine would post charges with no tax.
BILLS_AS: dict[str, str] = {
    "restaurant": "Restaurant / kitchen",
    "in_room_dining": "In-room dining",
    "minibar": "Minibar",
    "spa": "Spa & wellness",
    "laundry": "Laundry",
    "transport": "Transport",
    "other": "Other services",
}


# --------------------------------------------------------------------------
# Schemas
# --------------------------------------------------------------------------
class CategoryIn(BaseModel):
    property_id: uuid.UUID
    #: Whatever this property calls it. Not constrained, on purpose.
    name: str = Field(min_length=1, max_length=60)
    bills_as: str
    sort_order: int = 0


class CategoryPatch(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=60)
    bills_as: str | None = None
    sort_order: int | None = None
    is_active: bool | None = None


class CategoryOut(BaseModel):
    id: uuid.UUID
    name: str
    bills_as: str
    bills_as_label: str
    sort_order: int
    is_active: bool
    #: How many priced items sit in it — so the screen can say why a category
    #: cannot be deleted instead of showing a database error.
    item_count: int


class ItemIn(BaseModel):
    property_id: uuid.UUID
    code: str = Field(min_length=1, max_length=40)
    name: str = Field(min_length=1, max_length=120)
    category_id: uuid.UUID
    price: Decimal = Field(ge=0)
    is_active: bool = True
    sort_order: int = 0


class ItemPatch(BaseModel):
    code: str | None = Field(None, min_length=1, max_length=40)
    name: str | None = Field(None, min_length=1, max_length=120)
    category_id: uuid.UUID | None = None
    price: Decimal | None = Field(None, ge=0)
    is_active: bool | None = None
    sort_order: int | None = None


class ItemOut(BaseModel):
    id: uuid.UUID
    code: str
    name: str
    category_id: uuid.UUID
    category_label: str
    price: Decimal
    currency: str
    is_active: bool
    sort_order: int


class InHouseGuest(BaseModel):
    reservation_id: uuid.UUID
    reservation_number: str | None
    folio_id: uuid.UUID | None
    guest_name: str | None
    room_code: str | None
    departure_date: date | None
    balance: Decimal
    currency: str


class OrderLineIn(BaseModel):
    item_id: uuid.UUID
    quantity: Decimal = Field(gt=0, le=999)


def _trading_day(db: Session, property_id) -> date:
    """The day this property is trading, for a caller that named none.

    The oldest unclosed day, falling back to the property's own local date.
    The same rule as `routes._trading_day`; see the note there.
    """
    day = db.execute(
        text("SELECT business_date FROM finance.business_days "
             "WHERE property_id = :p AND status = 'open' "
             "ORDER BY business_date ASC LIMIT 1"),
        {"p": property_id},
    ).scalar()
    return day if day is not None else local_today(db, property_id)


class OrderIn(BaseModel):
    property_id: uuid.UUID
    folio_id: uuid.UUID
    reservation_id: uuid.UUID | None = None
    #: Omit it and the server stamps the day the property is trading. Sent, it
    #: is honoured. See the note on schemas.ChargeCreate for why the clients
    #: that filled this in were filling it in wrongly.
    business_date: date | None = None
    note: str | None = Field(None, max_length=300)
    lines: list[OrderLineIn] = Field(min_length=1, max_length=50)
    #: Generated once per Post and repeated on every retry of it, so a slow
    #: network or an impatient second tap cannot bill the guest twice.
    #: Optional, because a caller that does not send one is asking to be
    #: responsible for its own retries.
    client_key: str | None = Field(None, max_length=64)


class OrderLineOut(BaseModel):
    item_name: str
    #: The category's name as it stood when the guest was billed. Renaming a
    #: category later must not rewrite a bill that has been paid.
    category_label: str
    quantity: Decimal
    unit_price: Decimal
    amount: Decimal


class OrderOut(BaseModel):
    id: uuid.UUID
    business_date: date
    posted_at: str
    note: str | None
    total: Decimal
    currency: str
    lines: list[OrderLineOut]


# --------------------------------------------------------------------------
# Categories — the property's own vocabulary
# --------------------------------------------------------------------------
@service_router.get("/service-categories", response_model=list[CategoryOut])
def list_categories(
    property_id: uuid.UUID,
    include_inactive: bool = Query(False),
    caller: Caller = Depends(require_org_permission("payments", "view")),
    db: Session = Depends(get_session),
):
    assert_property_in_org(db, caller, property_id)
    rows = db.execute(
        text(
            """
            SELECT c.id, c.name, c.bills_as, c.sort_order, c.is_active,
                   count(i.id) AS item_count
              FROM finance.service_categories c
              LEFT JOIN finance.service_items i ON i.category_id = c.id
             WHERE c.property_id = :prop
               AND (:all OR c.is_active)
             GROUP BY c.id, c.name, c.bills_as, c.sort_order, c.is_active
             ORDER BY c.sort_order, c.name
            """
        ),
        {"prop": property_id, "all": include_inactive},
    ).mappings().all()
    return [
        CategoryOut(**dict(r),
                    bills_as_label=BILLS_AS.get(r["bills_as"], r["bills_as"]))
        for r in rows
    ]


@service_router.get("/service-categories/departments")
def list_departments(
    caller: Caller = Depends(require_org_permission("payments", "view")),
):
    """The departments a category may bill under.

    Served rather than hardcoded in the client, so the tax engine and the
    dropdown cannot drift apart.
    """
    return [{"value": k, "label": v} for k, v in BILLS_AS.items()]


@service_router.post("/service-categories", response_model=CategoryOut,
                     status_code=status.HTTP_201_CREATED)
def create_category(
    body: CategoryIn,
    caller: Caller = Depends(require_org_permission("payments", "create")),
    db: Session = Depends(get_session),
):
    require_property_permission(db, caller, body.property_id,
                                "payments", "create")
    if body.bills_as not in BILLS_AS:
        raise HTTPException(
            422, f"'{body.bills_as}' is not a department the ledger can tax.")
    row = db.execute(
        text(
            """
            INSERT INTO finance.service_categories
                (organization_id, property_id, name, bills_as, sort_order)
            VALUES (:org, :prop, :name, :bills, :sort)
            ON CONFLICT (property_id, lower(name)) DO NOTHING
            RETURNING id, name, bills_as, sort_order, is_active
            """
        ),
        {"org": caller_org(caller), "prop": body.property_id,
         "name": body.name.strip(), "bills": body.bills_as,
         "sort": body.sort_order},
    ).mappings().first()
    if row is None:
        raise HTTPException(409, f"'{body.name}' is already a category here.")
    record_audit(db, actor_subject=caller.subject,
                 action="service_category.create",
                 entity_type="service_category", entity_id=str(row["id"]),
                 organization_id=caller_org(caller),
                 property_id=body.property_id)
    return CategoryOut(**dict(row), item_count=0,
                       bills_as_label=BILLS_AS[row["bills_as"]])


@service_router.patch("/service-categories/{category_id}",
                      response_model=CategoryOut)
def update_category(
    category_id: uuid.UUID,
    body: CategoryPatch,
    property_id: uuid.UUID = Query(...),
    caller: Caller = Depends(require_org_permission("payments", "update")),
    db: Session = Depends(get_session),
):
    assert_property_in_org(db, caller, property_id)
    if body.bills_as is not None and body.bills_as not in BILLS_AS:
        raise HTTPException(
            422, f"'{body.bills_as}' is not a department the ledger can tax.")
    fields = body.model_dump(exclude_unset=True)
    if not fields:
        raise HTTPException(422, "Nothing to change.")
    sets = ", ".join(f"{k} = :{k}" for k in fields)
    row = db.execute(
        text(
            f"""
            UPDATE finance.service_categories
               SET {sets}, updated_at = now()
             WHERE id = :id AND property_id = :prop
            RETURNING id, name, bills_as, sort_order, is_active
            """
        ),
        {**fields, "id": category_id, "prop": property_id},
    ).mappings().first()
    if row is None:
        raise HTTPException(404, "No such category.")
    count = db.execute(
        text("SELECT count(*) FROM finance.service_items "
             "WHERE category_id = :id"), {"id": category_id},
    ).scalar_one()
    record_audit(db, actor_subject=caller.subject,
                 action="service_category.update",
                 entity_type="service_category", entity_id=str(category_id),
                 organization_id=caller_org(caller), property_id=property_id)
    return CategoryOut(**dict(row), item_count=int(count),
                       bills_as_label=BILLS_AS[row["bills_as"]])


@service_router.delete("/service-categories/{category_id}",
                       status_code=status.HTTP_204_NO_CONTENT)
def delete_category(
    category_id: uuid.UUID,
    property_id: uuid.UUID = Query(...),
    caller: Caller = Depends(require_org_permission("payments", "update")),
    db: Session = Depends(get_session),
):
    """Remove a category the property is not using.

    Refused while priced items still point at it, rather than cascading: a
    category disappearing must never quietly take the menu with it. Move or
    retire the items first, or deactivate the category instead.
    """
    assert_property_in_org(db, caller, property_id)
    count = db.execute(
        text("SELECT count(*) FROM finance.service_items "
             "WHERE category_id = :id"), {"id": category_id},
    ).scalar_one()
    if count:
        raise HTTPException(
            409, f"{count} item(s) are still in this category. Move them, or "
                 "deactivate the category instead of deleting it.")
    done = db.execute(
        text("DELETE FROM finance.service_categories "
             "WHERE id = :id AND property_id = :prop"),
        {"id": category_id, "prop": property_id},
    ).rowcount
    if not done:
        raise HTTPException(404, "No such category.")
    record_audit(db, actor_subject=caller.subject,
                 action="service_category.delete",
                 entity_type="service_category", entity_id=str(category_id),
                 organization_id=caller_org(caller), property_id=property_id)


# --------------------------------------------------------------------------
# The catalogue
# --------------------------------------------------------------------------
@service_router.get("/service-items", response_model=list[ItemOut])
def list_items(
    property_id: uuid.UUID,
    include_inactive: bool = Query(False),
    caller: Caller = Depends(require_org_permission("payments", "view")),
    db: Session = Depends(get_session),
):
    """The property's menu, grouped the way it will be shown."""
    assert_property_in_org(db, caller, property_id)
    rows = db.execute(
        text(
            """
            SELECT i.id, i.code, i.name, i.category_id, i.price, i.currency,
                   i.is_active, i.sort_order, c.name AS category_label
              FROM finance.service_items i
              JOIN finance.service_categories c ON c.id = i.category_id
             WHERE i.property_id = :prop
               AND (:all OR (i.is_active AND c.is_active))
             ORDER BY c.sort_order, c.name, i.sort_order, i.name
            """
        ),
        {"prop": property_id, "all": include_inactive},
    ).mappings().all()
    return [ItemOut(**dict(r)) for r in rows]


@service_router.post("/service-items", response_model=ItemOut,
                     status_code=status.HTTP_201_CREATED)
def create_item(
    body: ItemIn,
    caller: Caller = Depends(require_org_permission("payments", "create")),
    db: Session = Depends(get_session),
):
    require_property_permission(db, caller, body.property_id,
                                "payments", "create")
    label = _category_label(db, body.category_id, body.property_id)
    row = db.execute(
        text(
            """
            INSERT INTO finance.service_items
                (organization_id, property_id, code, name, category_id, price,
                 is_active, sort_order)
            VALUES (:org, :prop, :code, :name, :cat, :price, :active, :sort)
            ON CONFLICT ON CONSTRAINT uq_service_item_code DO NOTHING
            RETURNING id, code, name, category_id, price, currency, is_active,
                      sort_order
            """
        ),
        {
            "org": caller_org(caller), "prop": body.property_id,
            "code": body.code.strip(), "name": body.name.strip(),
            "cat": body.category_id, "price": body.price,
            "active": body.is_active, "sort": body.sort_order,
        },
    ).mappings().first()
    if row is None:
        # DO NOTHING rather than letting the unique index raise, so this reads
        # as "that code is taken" instead of a 500 with a constraint name in it.
        raise HTTPException(409, f"Code '{body.code}' is already in use.")
    record_audit(db, actor_subject=caller.subject, action="service_item.create",
                 entity_type="service_item", entity_id=str(row["id"]),
                 organization_id=caller_org(caller),
                 property_id=body.property_id)
    return ItemOut(**dict(row), category_label=label)


@service_router.patch("/service-items/{item_id}", response_model=ItemOut)
def update_item(
    item_id: uuid.UUID,
    body: ItemPatch,
    property_id: uuid.UUID = Query(...),
    caller: Caller = Depends(require_org_permission("payments", "update")),
    db: Session = Depends(get_session),
):
    assert_property_in_org(db, caller, property_id)
    if body.category_id is not None:
        _category_label(db, body.category_id, property_id)
    fields = body.model_dump(exclude_unset=True)
    if not fields:
        raise HTTPException(422, "Nothing to change.")
    sets = ", ".join(f"{k} = :{k}" for k in fields)
    row = db.execute(
        text(
            f"""
            UPDATE finance.service_items
               SET {sets}, updated_at = now()
             WHERE id = :id AND property_id = :prop
            RETURNING id, code, name, category_id, price, currency, is_active,
                      sort_order
            """
        ),
        {**fields, "id": item_id, "prop": property_id},
    ).mappings().first()
    if row is None:
        raise HTTPException(404, "No such item.")
    record_audit(db, actor_subject=caller.subject, action="service_item.update",
                 entity_type="service_item", entity_id=str(item_id),
                 organization_id=caller_org(caller), property_id=property_id)
    return ItemOut(**dict(row),
                   category_label=_category_label(db, row["category_id"],
                                                  property_id))


@service_router.delete("/service-items/{item_id}",
                       status_code=status.HTTP_204_NO_CONTENT)
def retire_item(
    item_id: uuid.UUID,
    property_id: uuid.UUID = Query(...),
    caller: Caller = Depends(require_org_permission("payments", "update")),
    db: Session = Depends(get_session),
):
    """Take an item off the menu.

    Deactivates; never deletes. Order lines point at the item for reporting,
    and a bill that has already been paid must keep its link to what was sold.
    """
    assert_property_in_org(db, caller, property_id)
    done = db.execute(
        text(
            """
            UPDATE finance.service_items SET is_active = false,
                   updated_at = now()
             WHERE id = :id AND property_id = :prop
            """
        ),
        {"id": item_id, "prop": property_id},
    ).rowcount
    if not done:
        raise HTTPException(404, "No such item.")
    record_audit(db, actor_subject=caller.subject, action="service_item.retire",
                 entity_type="service_item", entity_id=str(item_id),
                 organization_id=caller_org(caller), property_id=property_id)


# --------------------------------------------------------------------------
# Who is in the hotel right now
# --------------------------------------------------------------------------
@service_router.get("/service-orders/in-house",
                    response_model=list[InHouseGuest])
def in_house(
    property_id: uuid.UUID,
    q: str | None = Query(None),
    caller: Caller = Depends(require_org_permission("payments", "view")),
    db: Session = Depends(get_session),
):
    """Guests currently checked in, with the folio an order would go to.

    Deliberately not ``/open-folios``, which is the collect dialog's list and
    only returns folios already owing something. A guest who has paid up front
    owes nothing and still wants dinner.
    """
    assert_property_in_org(db, caller, property_id)
    rows = db.execute(
        text(
            """
            SELECT r.id AS reservation_id, r.number AS reservation_number,
                   f.id AS folio_id,
                   coalesce(f.currency, 'INR') AS currency,
                   g.full_name AS guest_name,
                   max(rm.code) AS room_code,
                   max(ru.departure_date) AS departure_date,
                   coalesce(sum(CASE WHEN e.entry_type = 'debit' THEN e.amount
                                     WHEN e.entry_type = 'credit' THEN -e.amount
                                     END), 0) AS balance
            FROM booking.reservations r
            JOIN booking.reservation_units ru ON ru.reservation_id = r.id
            LEFT JOIN property.rooms rm ON rm.id = ru.assigned_room_id
            LEFT JOIN engagement.guests g ON g.id = r.primary_guest_id
            LEFT JOIN finance.folios f
                   ON f.reservation_id = r.id AND f.status = 'open'
            LEFT JOIN finance.folio_entries e ON e.folio_id = f.id
            WHERE r.property_id = :prop
              AND ru.status = 'checked_in'
              AND (CAST(:q AS text) IS NULL
                   OR g.full_name ILIKE '%' || CAST(:q AS text) || '%'
                   OR r.number    ILIKE '%' || CAST(:q AS text) || '%'
                   OR rm.code     ILIKE '%' || CAST(:q AS text) || '%')
            GROUP BY r.id, r.number, f.id, f.currency, g.full_name
            ORDER BY max(rm.code) NULLS LAST, g.full_name
            LIMIT 200
            """
        ),
        {"prop": property_id, "q": q or None},
    ).mappings().all()
    return [InHouseGuest(**dict(r)) for r in rows]


# --------------------------------------------------------------------------
# Posting an order
# --------------------------------------------------------------------------
@service_router.post("/service-orders", response_model=OrderOut,
                     status_code=status.HTTP_201_CREATED)
def create_order(
    body: OrderIn,
    caller: Caller = Depends(require_org_permission("payments", "create")),
    db: Session = Depends(get_session),
):
    """Put what the guest ordered on their folio."""
    require_property_permission(db, caller, body.property_id,
                                "payments", "create")
    org = caller_org(caller)

    # Already posted under this key? Hand back what was posted. Checked before
    # anything is written, and enforced by a unique index underneath, so two
    # taps racing each other cannot both get past this point.
    if body.client_key:
        seen = db.execute(
            text(
                """
                SELECT id FROM finance.service_orders
                 WHERE property_id = :prop AND client_key = :key
                """
            ),
            {"prop": body.property_id, "key": body.client_key},
        ).scalar_one_or_none()
        if seen is not None:
            return _order(db, seen, body.property_id)

    folio = db.execute(
        text(
            """
            SELECT id, status, currency FROM finance.folios
             WHERE id = :id AND property_id = :prop
            """
        ),
        {"id": body.folio_id, "prop": body.property_id},
    ).mappings().first()
    if folio is None:
        raise HTTPException(404, "No such folio.")
    if folio["status"] != "open":
        # Checked out and settled. Adding to it now would reopen a bill the
        # guest has already been handed and paid.
        raise HTTPException(
            409, "That folio is closed — the stay has already been settled.")

    # Price from the catalogue, never from the request. A client that sends its
    # own unit price is a client that can discount a bill to zero.
    wanted = [line.item_id for line in body.lines]
    items = {
        r["id"]: r
        for r in db.execute(
            text(
                """
                SELECT i.id, i.name, i.price,
                       c.name AS category_label, c.bills_as
                  FROM finance.service_items i
                  JOIN finance.service_categories c ON c.id = i.category_id
                 WHERE i.property_id = :prop AND i.id = ANY(:ids)
                   AND i.is_active AND c.is_active
                """
            ),
            {"prop": body.property_id, "ids": wanted},
        ).mappings().all()
    }
    missing = [str(i) for i in wanted if i not in items]
    if missing:
        raise HTTPException(
            422, "Some items are not on this property's menu, or have been "
                 f"taken off it: {', '.join(missing)}")

    priced = []
    total = Decimal("0")
    for line in body.lines:
        item = items[line.item_id]
        amount = (Decimal(item["price"]) * line.quantity).quantize(
            Decimal("0.01"))
        total += amount
        priced.append((line, item, amount))

    # One day for the order and every line posted from it. The lines used to
    # be handed ``body.business_date`` directly, which the screen leaves out,
    # so each posting reached the ledger with no date and was refused.
    business_date = body.business_date or _trading_day(db, body.property_id)

    order_id = db.execute(
        text(
            """
            INSERT INTO finance.service_orders
                (organization_id, property_id, folio_id, reservation_id,
                 business_date, note, posted_by, total, currency, client_key)
            VALUES (:org, :prop, :folio, :res, :bd, :note, :by, :total, :cur,
                    :key)
            ON CONFLICT (property_id, client_key)
                WHERE client_key IS NOT NULL DO NOTHING
            RETURNING id
            """
        ),
        {
            "org": org, "prop": body.property_id, "folio": body.folio_id,
            "res": body.reservation_id,
            "bd": business_date,
            "note": (body.note or "").strip() or None,
            "by": caller.user_id, "total": total,
            "cur": folio["currency"], "key": body.client_key,
        },
    ).scalar_one_or_none()
    if order_id is None:
        # The other tap won the race between the check above and this insert.
        # Its order is the real one; nothing has been posted twice.
        return _order(
            db,
            db.execute(
                text("SELECT id FROM finance.service_orders "
                     "WHERE property_id = :prop AND client_key = :key"),
                {"prop": body.property_id, "key": body.client_key},
            ).scalar_one(),
            body.property_id,
        )

    for index, (line, item, amount) in enumerate(priced, start=1):
        # The department the property's own category points at. It is the
        # ledger's word, not theirs, and it is what resolves the tax.
        source_type = item["bills_as"]
        try:
            res = post_charge(
                db,
                organization_id=org,
                property_id=body.property_id,
                folio_id=body.folio_id,
                amount=amount,
                business_date=business_date,
                source_type=source_type,
                # Unique per order line, so a double-tapped Post button
                # re-posts nothing: the ledger's own uniqueness catches it.
                source_line_key=f"service:{order_id}:{index}",
                source_id=str(order_id),
                currency=folio["currency"],
            )
        except LedgerError as exc:
            raise HTTPException(409 if exc.conflict else 422, str(exc)) from exc

        db.execute(
            text(
                """
                INSERT INTO finance.service_order_lines
                    (organization_id, property_id, order_id, item_id,
                     item_name, category, quantity, unit_price, amount,
                     folio_entry_id)
                VALUES (:org, :prop, :oid, :item, :name, :cat, :qty, :unit,
                        :amt, :entry)
                """
            ),
            {
                "org": org, "prop": body.property_id, "oid": order_id,
                "item": item["id"], "name": item["name"],
                # Snapshot the category's name as it reads today.
                "cat": item["category_label"], "qty": line.quantity,
                "unit": item["price"], "amt": amount,
                "entry": res.entry_id,
            },
        )

    record_audit(db, actor_subject=caller.subject, action="service_order.post",
                 entity_type="service_order", entity_id=str(order_id),
                 organization_id=org, property_id=body.property_id)
    return _order(db, order_id, body.property_id)


@service_router.get("/service-orders", response_model=list[OrderOut])
def list_orders(
    property_id: uuid.UUID,
    folio_id: uuid.UUID,
    caller: Caller = Depends(require_org_permission("payments", "view")),
    db: Session = Depends(get_session),
):
    """What was ordered on this folio — the breakdown behind the charges."""
    assert_property_in_org(db, caller, property_id)
    ids = db.execute(
        text(
            """
            SELECT id FROM finance.service_orders
             WHERE property_id = :prop AND folio_id = :folio
             ORDER BY posted_at
            """
        ),
        {"prop": property_id, "folio": folio_id},
    ).scalars().all()
    return [_order(db, i, property_id) for i in ids]


def _order(db: Session, order_id: uuid.UUID,
           property_id: uuid.UUID) -> OrderOut:
    head = db.execute(
        text(
            """
            SELECT id, business_date, posted_at, note, total, currency
              FROM finance.service_orders
             WHERE id = :id AND property_id = :prop
            """
        ),
        {"id": order_id, "prop": property_id},
    ).mappings().one()
    lines = db.execute(
        text(
            """
            SELECT item_name, category AS category_label, quantity,
                   unit_price, amount
              FROM finance.service_order_lines
             WHERE order_id = :id
             ORDER BY id
            """
        ),
        {"id": order_id},
    ).mappings().all()
    return OrderOut(
        id=head["id"], business_date=head["business_date"],
        posted_at=head["posted_at"].isoformat(), note=head["note"],
        total=head["total"], currency=head["currency"],
        lines=[OrderLineOut(**dict(line)) for line in lines],
    )


def _category_label(db: Session, category_id: uuid.UUID,
                    property_id: uuid.UUID) -> str:
    """The category's name, and proof it belongs to this property.

    Checked rather than trusted: a category id from another tenant would
    otherwise file an item under a name its own staff never chose, and the
    foreign key alone would not notice because both rows are real.
    """
    name = db.execute(
        text("SELECT name FROM finance.service_categories "
             "WHERE id = :id AND property_id = :prop"),
        {"id": category_id, "prop": property_id},
    ).scalar_one_or_none()
    if name is None:
        raise HTTPException(422, "That category is not on this property's list.")
    return name
