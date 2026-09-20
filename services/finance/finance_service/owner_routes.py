"""Unit owners and their management contracts.

In a managed resort a unit can belong to someone other than the operator. The
operator sells it, keeps a management fee, charges the owner for work done on
the unit, and pays the owner the rest. The Owner Statement report does that
arithmetic; this module records the facts it needs.

**The owner and the contract are separate.** One owner can hold several units
and a unit can change hands, so which room, what fee and from when to when is
a contract row, not a column on the owner.

**Current is a date question.** A contract is current when today falls between
its start and end dates. There is no status to set, so an ended contract can
never be left marked active.

**One owner per room at a time.** Two contracts for the same room may not
overlap: a night's revenue has to belong to exactly one owner, or the
statements would pay it out twice.
"""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

from chirala_common.audit import record_audit
from chirala_common.authz import (
    _GRANT_SQL, Caller, assert_property_in_org, build_authz,
)
from chirala_common.routing import TransactionalRoute
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.orm import Session

from .database import get_session
from .night_audit import local_today
from .settings import settings

_get_caller, require_permission, _require_org = build_authz(
    get_session, lambda: settings.environment,
    lambda: settings.service_token,
)

owner_router = APIRouter(tags=["unit-owners"], route_class=TransactionalRoute)


class OwnerIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    email: str | None = Field(None, max_length=200)
    phone: str | None = Field(None, max_length=60)
    pan: str | None = Field(None, max_length=20)
    gstin: str | None = Field(None, max_length=20)
    bank_details: str | None = Field(None, max_length=300)
    status: str = "active"
    notes: str | None = Field(None, max_length=2000)


class ContractIn(BaseModel):
    room_id: uuid.UUID
    management_fee_percent: Decimal = Field(ge=0, le=100)
    start_date: date
    end_date: date | None = None
    notes: str | None = Field(None, max_length=500)


class Contract(BaseModel):
    id: uuid.UUID
    owner_id: uuid.UUID
    room_id: uuid.UUID
    room_code: str | None
    room_type: str | None
    management_fee_percent: Decimal
    start_date: date
    end_date: date | None
    current: bool
    notes: str | None


class Owner(BaseModel):
    id: uuid.UUID
    name: str
    email: str | None
    phone: str | None
    pan: str | None
    gstin: str | None
    bank_details: str | None
    status: str
    notes: str | None
    contracts: list[Contract]


class RoomChoice(BaseModel):
    id: uuid.UUID
    code: str
    room_type: str | None
    #: Who holds the room today, if anyone.
    owner: str | None


class OwnerList(BaseModel):
    rows: list[Owner]
    rooms: list[RoomChoice]
    can_edit: bool


def _may_edit(db: Session, caller: Caller, property_id: uuid.UUID) -> bool:
    if caller.is_service:
        return True
    return db.execute(
        text(_GRANT_SQL),
        {"uid": caller.user_id, "res": "payments", "act": "edit",
         "prop": property_id},
    ).first() is not None


def _org(db: Session, property_id: uuid.UUID) -> uuid.UUID:
    org = db.execute(
        text("SELECT organization_id FROM iam.properties WHERE id = :p"),
        {"p": property_id},
    ).scalar()
    if org is None:
        raise HTTPException(status_code=404, detail="Property not found.")
    return org


def _list(db: Session, property_id: uuid.UUID, caller: Caller) -> OwnerList:
    today = local_today(db, property_id)
    contracts: dict[uuid.UUID, list[Contract]] = {}
    for r in db.execute(
        text("""
            SELECT c.*, rm.code AS room_code, rt.name AS room_type
            FROM finance.unit_owner_contracts c
            LEFT JOIN property.rooms rm ON rm.id = c.room_id
            LEFT JOIN property.room_types rt ON rt.id = rm.room_type_id
            WHERE c.property_id = :p
            ORDER BY rm.code, c.start_date
        """),
        {"p": property_id},
    ).mappings():
        contracts.setdefault(r["owner_id"], []).append(Contract(
            id=r["id"], owner_id=r["owner_id"], room_id=r["room_id"],
            room_code=r["room_code"], room_type=r["room_type"],
            management_fee_percent=r["management_fee_percent"],
            start_date=r["start_date"], end_date=r["end_date"],
            current=r["start_date"] <= today and (r["end_date"] is None
                                                  or r["end_date"] >= today),
            notes=r["notes"],
        ))
    owners = [
        Owner(**{k: r[k] for k in ("id", "name", "email", "phone", "pan", "gstin",
                                   "bank_details", "status", "notes")},
              contracts=contracts.get(r["id"], []))
        for r in db.execute(
            text("SELECT * FROM finance.unit_owners WHERE property_id = :p ORDER BY name"),
            {"p": property_id},
        ).mappings()
    ]
    holder = {c.room_id: o.name for o in owners for c in o.contracts if c.current}
    rooms = [
        RoomChoice(id=r[0], code=r[1], room_type=r[2], owner=holder.get(r[0]))
        for r in db.execute(
            text("""
                SELECT rm.id, rm.code, rt.name FROM property.rooms rm
                LEFT JOIN property.room_types rt ON rt.id = rm.room_type_id
                WHERE rm.property_id = :p AND rm.status = 'active'
                ORDER BY rm.code
            """),
            {"p": property_id})
    ]
    return OwnerList(rows=owners, rooms=rooms,
                     can_edit=_may_edit(db, caller, property_id))


@owner_router.get("/unit-owners", response_model=OwnerList)
def list_owners(
    property_id: uuid.UUID,
    caller: Caller = Depends(require_permission("payments", "view")),
    db: Session = Depends(get_session),
):
    """Owners at this property, with the units they hold."""
    assert_property_in_org(db, caller, property_id)
    return _list(db, property_id, caller)


def _clean_owner(body: OwnerIn) -> OwnerIn:
    body.name = body.name.strip()
    if not body.name:
        raise HTTPException(status_code=422, detail="Enter the owner's name.")
    if body.status not in ("active", "inactive"):
        raise HTTPException(status_code=422, detail="Status must be active or inactive.")
    for k in ("email", "phone", "pan", "gstin", "bank_details", "notes"):
        setattr(body, k, (getattr(body, k) or "").strip() or None)
    if body.pan:
        body.pan = body.pan.upper()
    if body.gstin:
        body.gstin = body.gstin.upper()
    return body


@owner_router.post("/unit-owners", response_model=OwnerList, status_code=201)
def create_owner(
    property_id: uuid.UUID,
    body: OwnerIn,
    caller: Caller = Depends(require_permission("payments", "edit")),
    db: Session = Depends(get_session),
):
    assert_property_in_org(db, caller, property_id)
    body = _clean_owner(body)
    org = _org(db, property_id)
    oid = uuid.uuid4()
    db.execute(
        text("""
            INSERT INTO finance.unit_owners
                (id, organization_id, property_id, name, email, phone, pan, gstin,
                 bank_details, status, notes, created_by, updated_by)
            VALUES (:id, :org, :p, :name, :email, :phone, :pan, :gstin,
                    :bank_details, :status, :notes, :who, :who)
        """),
        {"id": oid, "org": org, "p": property_id, "who": caller.user_id,
         **body.model_dump()},
    )
    record_audit(db, action="unit_owner.created", entity_type="unit_owner",
                 entity_id=str(oid), organization_id=org, property_id=property_id,
                 actor_subject=caller.subject, after={"name": body.name})
    return _list(db, property_id, caller)


@owner_router.patch("/unit-owners/{owner_id}", response_model=OwnerList)
def update_owner(
    owner_id: uuid.UUID,
    property_id: uuid.UUID,
    body: OwnerIn,
    caller: Caller = Depends(require_permission("payments", "edit")),
    db: Session = Depends(get_session),
):
    assert_property_in_org(db, caller, property_id)
    body = _clean_owner(body)
    org = _org(db, property_id)
    done = db.execute(
        text("""
            UPDATE finance.unit_owners SET
                name = :name, email = :email, phone = :phone, pan = :pan,
                gstin = :gstin, bank_details = :bank_details, status = :status,
                notes = :notes, updated_by = :who, updated_at = now(),
                version = version + 1
            WHERE id = :id AND property_id = :p
        """),
        {"id": owner_id, "p": property_id, "who": caller.user_id,
         **body.model_dump()},
    ).rowcount
    if not done:
        raise HTTPException(status_code=404, detail="Owner not found.")
    record_audit(db, action="unit_owner.updated", entity_type="unit_owner",
                 entity_id=str(owner_id), organization_id=org,
                 property_id=property_id, actor_subject=caller.subject,
                 after={"name": body.name, "status": body.status})
    return _list(db, property_id, caller)


def _check_contract(db: Session, property_id: uuid.UUID, body: ContractIn,
                    contract_id: uuid.UUID | None) -> None:
    if body.end_date and body.end_date < body.start_date:
        raise HTTPException(status_code=422, detail="The contract cannot end before it starts.")
    room = db.execute(
        text("SELECT code FROM property.rooms WHERE id = :r AND property_id = :p"),
        {"r": body.room_id, "p": property_id},
    ).scalar()
    if room is None:
        raise HTTPException(status_code=422, detail="That room is not at this property.")
    clash = db.execute(
        text("""
            SELECT o.name FROM finance.unit_owner_contracts c
            JOIN finance.unit_owners o ON o.id = c.owner_id
            WHERE c.property_id = :p AND c.room_id = :r
              AND (CAST(:id AS uuid) IS NULL OR c.id <> CAST(:id AS uuid))
              AND c.start_date <= COALESCE(CAST(:end AS date), CAST('infinity' AS date))
              AND COALESCE(c.end_date, CAST('infinity' AS date)) >= :start
            LIMIT 1
        """),
        {"p": property_id, "r": body.room_id, "id": contract_id,
         "start": body.start_date, "end": body.end_date},
    ).scalar()
    if clash:
        raise HTTPException(
            status_code=409,
            detail=f"Room {room} is already held by {clash} for part of those "
                   "dates. End that contract first.")


@owner_router.post("/unit-owners/{owner_id}/contracts", response_model=OwnerList,
                   status_code=201)
def create_contract(
    owner_id: uuid.UUID,
    property_id: uuid.UUID,
    body: ContractIn,
    caller: Caller = Depends(require_permission("payments", "edit")),
    db: Session = Depends(get_session),
):
    """Put a unit under an owner's contract."""
    assert_property_in_org(db, caller, property_id)
    org = _org(db, property_id)
    if db.execute(
        text("SELECT 1 FROM finance.unit_owners WHERE id = :o AND property_id = :p"),
        {"o": owner_id, "p": property_id},
    ).first() is None:
        raise HTTPException(status_code=404, detail="Owner not found.")
    _check_contract(db, property_id, body, None)
    cid = uuid.uuid4()
    db.execute(
        text("""
            INSERT INTO finance.unit_owner_contracts
                (id, organization_id, property_id, owner_id, room_id,
                 management_fee_percent, start_date, end_date, notes,
                 created_by, updated_by)
            VALUES (:id, :org, :p, :o, :room_id, :management_fee_percent,
                    :start_date, :end_date, :notes, :who, :who)
        """),
        {"id": cid, "org": org, "p": property_id, "o": owner_id,
         "who": caller.user_id, **body.model_dump()},
    )
    record_audit(db, action="unit_owner_contract.created",
                 entity_type="unit_owner_contract", entity_id=str(cid),
                 organization_id=org, property_id=property_id,
                 actor_subject=caller.subject,
                 after={"owner_id": str(owner_id), "room_id": str(body.room_id),
                        "fee": str(body.management_fee_percent),
                        "from": str(body.start_date), "to": str(body.end_date)})
    return _list(db, property_id, caller)


@owner_router.patch("/unit-owner-contracts/{contract_id}", response_model=OwnerList)
def update_contract(
    contract_id: uuid.UUID,
    property_id: uuid.UUID,
    body: ContractIn,
    caller: Caller = Depends(require_permission("payments", "edit")),
    db: Session = Depends(get_session),
):
    """Change a contract's fee or dates -- ending one is setting its end date."""
    assert_property_in_org(db, caller, property_id)
    org = _org(db, property_id)
    before = db.execute(
        text("SELECT * FROM finance.unit_owner_contracts "
             "WHERE id = :i AND property_id = :p FOR UPDATE"),
        {"i": contract_id, "p": property_id},
    ).mappings().first()
    if before is None:
        raise HTTPException(status_code=404, detail="Contract not found.")
    _check_contract(db, property_id, body, contract_id)
    db.execute(
        text("""
            UPDATE finance.unit_owner_contracts SET
                room_id = :room_id, management_fee_percent = :management_fee_percent,
                start_date = :start_date, end_date = :end_date, notes = :notes,
                updated_by = :who, updated_at = now(), version = version + 1
            WHERE id = :id
        """),
        {"id": contract_id, "who": caller.user_id, **body.model_dump()},
    )
    record_audit(db, action="unit_owner_contract.updated",
                 entity_type="unit_owner_contract", entity_id=str(contract_id),
                 organization_id=org, property_id=property_id,
                 actor_subject=caller.subject,
                 before={"fee": str(before["management_fee_percent"]),
                         "from": str(before["start_date"]), "to": str(before["end_date"])},
                 after={"fee": str(body.management_fee_percent),
                        "from": str(body.start_date), "to": str(body.end_date)})
    return _list(db, property_id, caller)
