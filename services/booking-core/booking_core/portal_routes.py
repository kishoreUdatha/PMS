"""The guest portal: a guest's own booking, from a private link.

Two halves:

* **Staff** (``portal_router``) create the link and text it to the guest, see
  what the guest submitted for online check-in, and work the queue of guest
  requests.
* **The guest** (``portal_public_router``) open the link with no login and see
  their booking, check in online, upload their ID, send a request, and pay
  anything outstanding through an open payment link.

The public half follows the booking engine's rules (see public_routes.py):

* **The property comes from the URL.** Its code decides whose records the
  request may read, and the tenant is bound from it before the token is
  looked up.
* **The token is the credential.** It is stored only as a SHA-256 hash, a
  new link revokes the old one, and it stops working a week after departure.
  A wrong, revoked or expired token is a 404, the same as a link that never
  existed.
* **What the guest types is not verified.** Online check-in waits in
  ``booking.web_checkins`` until a person at the desk checks the ID, because
  at the desk typing an ID number is the verification and here it is not.
  The migration explains this in full.
* **Rate limited,** like everything under ``/public``.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import re
import secrets
import uuid
from datetime import date, datetime, timedelta, timezone

from chirala_common.audit import record_audit
from chirala_common.authz import Caller, assert_property_in_org, build_authz
from chirala_common.db import bind_tenant_context, property_code_context
from chirala_common.guest_messages import notify_guest, summary
from chirala_common.objectstore import (
    ObjectStoreError, build_key, put_object,
)
from chirala_common.routing import TransactionalRoute
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import text
from sqlalchemy.orm import Session

from .checkin_routes import ID_TYPES, _STORE, _read_doc
from .database import SessionFactory, get_session
from .ratelimit import rate_limit
from .settings import settings

_get_caller, require_permission, _require_org = build_authz(
    get_session, lambda: settings.environment,
    lambda: settings.service_token,
)

portal_router = APIRouter(tags=["guest-portal"], route_class=TransactionalRoute)
portal_public_router = APIRouter(
    prefix="/public", tags=["guest-portal"], dependencies=[Depends(rate_limit)],
    route_class=TransactionalRoute)

#: How long after departure a link keeps working: long enough to look up a
#: receipt or settle a balance, not forever.
DAYS_AFTER_DEPARTURE = 7
REQUEST_KINDS = ("housekeeping", "amenity", "maintenance", "late_checkout",
                 "transport", "food", "other")
REQUEST_STATUSES = ("open", "in_progress", "done", "declined")
#: A guest cannot flood the desk: this many open requests at once.
MAX_OPEN_REQUESTS = 10


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def portal_url(property_code: str, token: str) -> str:
    base = (settings.app_base_url or "").rstrip("/")
    return f"{base}/stay/{property_code}/{token}"


# =================================================================== staff ==

class LinkIn(BaseModel):
    send: bool = True


class LinkOut(BaseModel):
    url: str
    expires_at: datetime
    message: str | None = None


def _res_for_staff(db: Session, reservation_id: uuid.UUID,
                   property_id: uuid.UUID):
    row = db.execute(
        text("""
            SELECT r.id, r.number, r.status, r.organization_id,
                   p.code AS property_code, p.name AS property_name,
                   g.full_name AS guest_name, g.phone,
                   (SELECT max(u.departure_date) FROM booking.reservation_units u
                     WHERE u.reservation_id = r.id AND u.status <> 'cancelled')
                     AS departure
            FROM booking.reservations r
            JOIN iam.properties p ON p.id = r.property_id
            LEFT JOIN engagement.guests g ON g.id = r.primary_guest_id
            WHERE r.id = :r AND r.property_id = :p
        """),
        {"r": reservation_id, "p": property_id},
    ).mappings().first()
    if row is None:
        raise HTTPException(status_code=404, detail="Reservation not found")
    return row


@portal_router.post("/reservations/{reservation_id}/portal-link",
                    response_model=LinkOut)
def create_portal_link(
    reservation_id: uuid.UUID,
    property_id: uuid.UUID,
    body: LinkIn,
    caller: Caller = Depends(require_permission("front_desk", "edit")),
    db: Session = Depends(get_session),
):
    """A fresh link for this booking. Any earlier link stops working."""
    assert_property_in_org(db, caller, property_id)
    res = _res_for_staff(db, reservation_id, property_id)
    if res["status"] in ("cancelled", "no_show"):
        raise HTTPException(status_code=409,
                            detail=f"{res['number']} is {res['status']}.")
    departure = res["departure"] or date.today()
    expires = datetime.combine(departure + timedelta(days=DAYS_AFTER_DEPARTURE),
                               datetime.min.time(), tzinfo=timezone.utc)

    db.execute(
        text("UPDATE booking.guest_portal_links SET revoked_at = now() "
             "WHERE reservation_id = :r AND revoked_at IS NULL"),
        {"r": reservation_id},
    )
    token = secrets.token_urlsafe(24)
    db.execute(
        text("""
            INSERT INTO booking.guest_portal_links
                (organization_id, property_id, reservation_id, token_hash,
                 expires_at, created_by)
            VALUES (:org, :prop, :res, :h, :exp, :by)
        """),
        {"org": res["organization_id"], "prop": property_id,
         "res": reservation_id, "h": _hash(token), "exp": expires,
         "by": caller.subject},
    )
    url = portal_url(res["property_code"], token)

    message = None
    if body.send:
        if not settings.app_base_url:
            message = ("Not sent: APP_BASE_URL is not set, so the link would "
                       "point nowhere a guest can reach.")
        else:
            message = summary(notify_guest(
                db, SessionFactory, settings.messaging_config,
                code="guest_portal_link",
                organization_id=res["organization_id"], property_id=property_id,
                phone=res["phone"],
                values={"guest_name": res["guest_name"] or "Guest",
                        "property_name": res["property_name"],
                        "reservation_number": res["number"], "link": url},
            ))
    record_audit(
        db, action="guest_portal.link_created", entity_type="reservation",
        entity_id=str(reservation_id), organization_id=res["organization_id"],
        property_id=property_id, actor_subject=caller.subject,
        after={"expires_at": expires.isoformat(), "message": message},
    )
    return LinkOut(url=url, expires_at=expires, message=message)


class WebCheckinOut(BaseModel):
    status: str
    submitted_at: datetime
    applied_at: datetime | None
    details: dict


@portal_router.get("/reservations/{reservation_id}/web-checkin",
                   response_model=WebCheckinOut | None)
def get_web_checkin(
    reservation_id: uuid.UUID,
    property_id: uuid.UUID,
    caller: Caller = Depends(require_permission("front_desk", "view")),
    db: Session = Depends(get_session),
):
    """What the guest submitted online, for the desk to check and apply."""
    assert_property_in_org(db, caller, property_id)
    row = db.execute(
        text("SELECT status, submitted_at, applied_at, details "
             "FROM booking.web_checkins "
             "WHERE reservation_id = :r AND property_id = :p"),
        {"r": reservation_id, "p": property_id},
    ).mappings().first()
    return WebCheckinOut(**row) if row else None


class RequestOut(BaseModel):
    id: uuid.UUID
    reservation_id: uuid.UUID
    reservation_number: str | None = None
    guest_name: str | None = None
    room: str | None = None
    kind: str
    message: str
    status: str
    staff_note: str | None
    created_at: datetime
    updated_at: datetime


@portal_router.get("/guest-requests", response_model=list[RequestOut])
def list_guest_requests(
    property_id: uuid.UUID,
    status: str | None = None,
    caller: Caller = Depends(require_permission("front_desk", "view")),
    db: Session = Depends(get_session),
):
    assert_property_in_org(db, caller, property_id)
    if status is not None and status not in REQUEST_STATUSES:
        raise HTTPException(status_code=422, detail="Unknown status")
    rows = db.execute(
        text("""
            SELECT q.id, q.reservation_id, r.number AS reservation_number,
                   g.full_name AS guest_name, q.kind, q.message, q.status,
                   q.staff_note, q.created_at, q.updated_at,
                   (SELECT string_agg(rm.code, ', ')
                      FROM booking.reservation_units u
                      JOIN property.rooms rm ON rm.id = u.assigned_room_id
                     WHERE u.reservation_id = r.id
                       AND u.status <> 'cancelled') AS room
            FROM booking.guest_requests q
            JOIN booking.reservations r ON r.id = q.reservation_id
            LEFT JOIN engagement.guests g ON g.id = r.primary_guest_id
            WHERE q.property_id = :p
              AND (CAST(:st AS text) IS NULL OR q.status = CAST(:st AS text))
            ORDER BY CASE q.status WHEN 'open' THEN 0 WHEN 'in_progress' THEN 1
                                   ELSE 2 END, q.created_at
            LIMIT 300
        """),
        {"p": property_id, "st": status},
    ).mappings().all()
    return [RequestOut(**r) for r in rows]


class RequestUpdateIn(BaseModel):
    status: str | None = None
    staff_note: str | None = Field(default=None, max_length=300)


@portal_router.put("/guest-requests/{request_id}", response_model=RequestOut)
def update_guest_request(
    request_id: uuid.UUID,
    property_id: uuid.UUID,
    body: RequestUpdateIn,
    caller: Caller = Depends(require_permission("front_desk", "edit")),
    db: Session = Depends(get_session),
):
    assert_property_in_org(db, caller, property_id)
    if body.status is not None and body.status not in REQUEST_STATUSES:
        raise HTTPException(status_code=422, detail="Unknown status")
    before = db.execute(
        text("SELECT organization_id, status FROM booking.guest_requests "
             "WHERE id = :i AND property_id = :p"),
        {"i": request_id, "p": property_id},
    ).mappings().first()
    if before is None:
        raise HTTPException(status_code=404, detail="Request not found")
    db.execute(
        text("""
            UPDATE booking.guest_requests SET
                status = COALESCE(:st, status),
                staff_note = COALESCE(:note, staff_note),
                handled_by = :who, updated_at = now()
            WHERE id = :i AND property_id = :p
        """),
        {"st": body.status, "note": body.staff_note, "who": caller.user_id,
         "i": request_id, "p": property_id},
    )
    record_audit(
        db, action="guest_request.updated", entity_type="guest_request",
        entity_id=str(request_id), organization_id=before["organization_id"],
        property_id=property_id, actor_subject=caller.subject,
        before={"status": before["status"]},
        after={k: v for k, v in body.model_dump().items() if v is not None},
    )
    row = db.execute(
        text("SELECT q.*, NULL AS reservation_number, NULL AS guest_name, "
             "NULL AS room FROM booking.guest_requests q "
             "WHERE q.id = :i AND q.property_id = :p"),
        {"i": request_id, "p": property_id},
    ).mappings().first()
    return RequestOut(**{k: row[k] for k in RequestOut.model_fields})


# ================================================================== guest ==

def _open(db: Session, property_code: str, token: str, *, touch: bool = False):
    """The booking this link opens, or 404. Binds the tenant first."""
    if not re.fullmatch(r"[A-Za-z0-9_-]{20,64}", token or ""):
        raise HTTPException(status_code=404, detail="This link is not valid.")
    property_code_context(db, code=property_code)
    prop = db.execute(
        text("SELECT id, organization_id FROM iam.properties "
             "WHERE code = :c AND status = 'active'"),
        {"c": property_code},
    ).mappings().first()
    if prop is None:
        raise HTTPException(status_code=404, detail="This link is not valid.")
    bind_tenant_context(db, organization_id=prop["organization_id"],
                        property_id=prop["id"])
    link = db.execute(
        text("""
            SELECT l.id, l.token_hash, l.reservation_id, r.status
            FROM booking.guest_portal_links l
            JOIN booking.reservations r ON r.id = l.reservation_id
            WHERE l.token_hash = :h AND l.property_id = :p
              AND l.revoked_at IS NULL AND l.expires_at > now()
        """),
        {"h": _hash(token), "p": prop["id"]},
    ).mappings().first()
    if (link is None or link["status"] in ("cancelled", "no_show")
            or not hmac.compare_digest(link["token_hash"], _hash(token))):
        raise HTTPException(status_code=404, detail="This link is not valid.")
    if touch:
        db.execute(text("UPDATE booking.guest_portal_links "
                        "SET last_opened_at = now() WHERE id = :i"),
                   {"i": link["id"]})
    return prop, link["reservation_id"]


class StayRoom(BaseModel):
    room_type: str
    adults: int | None
    children: int | None
    room: str | None


class StayRequest(BaseModel):
    kind: str
    message: str
    status: str
    staff_note: str | None
    created_at: datetime


class StayPayment(BaseModel):
    amount: float
    currency: str
    url: str | None
    expires_at: datetime | None
    purpose: str | None


class StayOut(BaseModel):
    property_name: str
    property_phone: str | None
    property_email: str | None
    property_address: str | None
    checkin_time: str | None
    checkout_time: str | None
    reservation_number: str
    status: str
    arrival: date | None
    departure: date | None
    guest_name: str | None
    rooms: list[StayRoom]
    #: What the hotel already has, to start the form from. Never the ID.
    prefill: dict
    web_checkin: str  # none | submitted | applied
    documents: list[str]
    requests: list[StayRequest]
    payment: StayPayment | None
    paid: bool


@portal_public_router.get("/{property_code}/stay/{token}",
                          response_model=StayOut)
def open_stay(property_code: str, token: str,
              db: Session = Depends(get_session)):
    prop, reservation_id = _open(db, property_code, token, touch=True)
    head = db.execute(
        text("""
            SELECT r.number, r.status, r.primary_guest_id,
                   p.name, p.contact_phone, p.contact_email, p.checkin_time,
                   p.checkout_time,
                   concat_ws(', ', nullif(p.address_line, ''), nullif(p.city, ''),
                             nullif(p.state, '')) AS address,
                   g.full_name, g.email, g.phone, g.nationality, g.city AS g_city,
                   g.state AS g_state, g.country, g.address_line AS g_addr,
                   g.postal_code
            FROM booking.reservations r
            JOIN iam.properties p ON p.id = r.property_id
            LEFT JOIN engagement.guests g ON g.id = r.primary_guest_id
            WHERE r.id = :r
        """),
        {"r": reservation_id},
    ).mappings().first()
    units = db.execute(
        text("""
            SELECT rt.name AS room_type, u.adults, u.children, u.arrival_date,
                   u.departure_date, rm.code AS room
            FROM booking.reservation_units u
            JOIN property.room_types rt ON rt.id = u.room_type_id
            LEFT JOIN property.rooms rm ON rm.id = u.assigned_room_id
            WHERE u.reservation_id = :r AND u.status <> 'cancelled'
            ORDER BY u.arrival_date, u.line_index
        """),
        {"r": reservation_id},
    ).mappings().all()
    wc = db.execute(
        text("SELECT status FROM booking.web_checkins WHERE reservation_id = :r"),
        {"r": reservation_id},
    ).scalar()
    docs = [] if head["primary_guest_id"] is None else list(db.execute(
        text("SELECT kind FROM engagement.guest_documents "
             "WHERE guest_id = :g AND kind IN ('id_front', 'id_back')"),
        {"g": head["primary_guest_id"]},
    ).scalars())
    requests = db.execute(
        text("SELECT kind, message, status, staff_note, created_at "
             "FROM booking.guest_requests WHERE reservation_id = :r "
             "ORDER BY created_at DESC LIMIT 20"),
        {"r": reservation_id},
    ).mappings().all()
    link = db.execute(
        text("""
            SELECT expected_amount AS amount, currency, link_url AS url,
                   expires_at, purpose
            FROM finance.payment_intents
            WHERE reservation_id = :r AND kind = 'link'
              AND status IN ('created', 'processing')
              AND (expires_at IS NULL OR expires_at > now())
              AND link_url IS NOT NULL
            ORDER BY created_at DESC LIMIT 1
        """),
        {"r": reservation_id},
    ).mappings().first()
    paid = bool(db.execute(
        text("SELECT 1 FROM finance.payment_intents "
             "WHERE reservation_id = :r AND status = 'succeeded' LIMIT 1"),
        {"r": reservation_id},
    ).first())
    return StayOut(
        property_name=head["name"], property_phone=head["contact_phone"],
        property_email=head["contact_email"],
        property_address=head["address"] or None,
        checkin_time=head["checkin_time"], checkout_time=head["checkout_time"],
        reservation_number=head["number"], status=head["status"],
        arrival=min((u["arrival_date"] for u in units), default=None),
        departure=max((u["departure_date"] for u in units), default=None),
        guest_name=head["full_name"],
        rooms=[StayRoom(room_type=u["room_type"], adults=u["adults"],
                        children=u["children"], room=u["room"]) for u in units],
        prefill={k: v for k, v in {
            "full_name": head["full_name"], "email": head["email"],
            "phone": head["phone"], "nationality": head["nationality"],
            "address_line": head["g_addr"], "city": head["g_city"],
            "state": head["g_state"], "postal_code": head["postal_code"],
            "country": head["country"],
        }.items() if v},
        web_checkin=wc or "none", documents=docs,
        requests=[StayRequest(**r) for r in requests],
        payment=StayPayment(**{**link, "amount": float(link["amount"])}) if link else None,
        paid=paid,
    )


_DATE = r"^\d{4}-\d{2}-\d{2}$"


class WebCheckinIn(BaseModel):
    full_name: str = Field(min_length=2, max_length=200)
    email: str | None = Field(default=None, max_length=200)
    phone: str = Field(min_length=7, max_length=40)
    nationality: str = Field(min_length=2, max_length=80)
    address_line: str | None = Field(default=None, max_length=300)
    city: str | None = Field(default=None, max_length=120)
    state: str | None = Field(default=None, max_length=120)
    postal_code: str | None = Field(default=None, max_length=20)
    country: str | None = Field(default=None, max_length=80)
    id_type: str | None = None
    id_number: str | None = Field(default=None, max_length=60)
    arrival_time: str | None = Field(default=None, pattern=r"^\d{2}:\d{2}$")
    purpose_of_visit: str | None = Field(default=None, max_length=120)
    special_requests: str | None = Field(default=None, max_length=500)
    # Form C, for a guest who is not an Indian national. Optional here; the
    # desk sees what is missing before filing.
    sex: str | None = Field(default=None, max_length=10)
    date_of_birth: str | None = Field(default=None, pattern=_DATE)
    passport_number: str | None = Field(default=None, max_length=40)
    passport_issue_place: str | None = Field(default=None, max_length=120)
    passport_issue_date: str | None = Field(default=None, pattern=_DATE)
    passport_expiry_date: str | None = Field(default=None, pattern=_DATE)
    visa_number: str | None = Field(default=None, max_length=40)
    visa_type: str | None = Field(default=None, max_length=60)
    visa_issue_place: str | None = Field(default=None, max_length=120)
    visa_issue_date: str | None = Field(default=None, pattern=_DATE)
    visa_expiry_date: str | None = Field(default=None, pattern=_DATE)
    arrived_in_india_on: str | None = Field(default=None, pattern=_DATE)
    arrived_in_india_at: str | None = Field(default=None, max_length=120)
    next_destination: str | None = Field(default=None, max_length=200)
    #: The guest ticked that the hotel's policies were read and accepted.
    policies_accepted: bool = False

    @field_validator("id_type")
    @classmethod
    def _id_type(cls, v: str | None) -> str | None:
        if v is not None and v not in ID_TYPES:
            raise ValueError("Unknown ID type")
        return v


@portal_public_router.post("/{property_code}/stay/{token}/checkin")
def submit_web_checkin(property_code: str, token: str, body: WebCheckinIn,
                       db: Session = Depends(get_session)):
    prop, reservation_id = _open(db, property_code, token)
    if not body.policies_accepted:
        raise HTTPException(status_code=422,
                            detail="Please accept the hotel's policies.")
    status = db.execute(
        text("SELECT status FROM booking.web_checkins WHERE reservation_id = :r"),
        {"r": reservation_id},
    ).scalar()
    if status == "applied":
        raise HTTPException(
            status_code=409,
            detail="You are already checked in. Speak to the front desk "
                   "to change your details.")
    details = {k: (v.strip() if isinstance(v, str) else v)
               for k, v in body.model_dump().items() if v not in (None, "")}
    db.execute(
        text("""
            INSERT INTO booking.web_checkins
                (organization_id, property_id, reservation_id, details)
            VALUES (:org, :prop, :r, CAST(:d AS jsonb))
            ON CONFLICT (reservation_id) DO UPDATE
               SET details = EXCLUDED.details, submitted_at = now(),
                   status = 'submitted'
        """),
        {"org": prop["organization_id"], "prop": prop["id"], "r": reservation_id,
         "d": json.dumps(details)},
    )
    record_audit(
        db, action="guest_portal.web_checkin", entity_type="reservation",
        entity_id=str(reservation_id), organization_id=prop["organization_id"],
        property_id=prop["id"], actor_subject="guest",
        after={"fields": sorted(details)},
    )
    return {"detail": "Thank you. Your details are with the front desk, "
                      "and check-in will be quicker when you arrive."}


@portal_public_router.post("/{property_code}/stay/{token}/documents")
async def upload_stay_document(property_code: str, token: str,
                               kind: str = Form(...),
                               file: UploadFile = File(...),
                               db: Session = Depends(get_session)):
    """The guest's ID scan. Stored like a desk scan, and not marked verified."""
    if kind not in ("id_front", "id_back"):
        raise HTTPException(status_code=422, detail="Upload the front or back of your ID.")
    prop, reservation_id = _open(db, property_code, token)
    guest_id = db.execute(
        text("SELECT primary_guest_id FROM booking.reservations WHERE id = :r"),
        {"r": reservation_id},
    ).scalar()
    if guest_id is None:
        raise HTTPException(status_code=409,
                            detail="Please submit your details first.")
    # A scan the desk already took is the one that was checked. The guest's
    # upload never replaces it.
    if db.execute(
        text("SELECT 1 FROM engagement.guest_documents "
             "WHERE guest_id = :g AND kind = :k"),
        {"g": guest_id, "k": kind},
    ).first():
        raise HTTPException(status_code=409,
                            detail="The hotel already has this document.")
    data, content_type = await _read_doc(file)
    key = build_key("guest-docs", str(guest_id), kind, content_type=content_type)
    try:
        put_object(_STORE, key, data, content_type)
    except ObjectStoreError as exc:
        raise HTTPException(status_code=502,
                            detail="The upload failed. Please try again.") from exc
    db.execute(
        text("""
            INSERT INTO engagement.guest_documents
                (id, organization_id, guest_id, kind, storage_key, content_type,
                 size_bytes, original_name, uploaded_by)
            VALUES (gen_random_uuid(), :org, :g, :k, :key, :ct, :size, :name, NULL)
        """),
        {"org": prop["organization_id"], "g": guest_id, "k": kind, "key": key,
         "ct": content_type, "size": len(data),
         "name": (file.filename or kind)[:200]},
    )
    record_audit(
        db, action="guest_portal.document_uploaded", entity_type="guest",
        entity_id=str(guest_id), organization_id=prop["organization_id"],
        property_id=prop["id"], actor_subject="guest", after={"kind": kind},
    )
    return {"detail": "Uploaded. The front desk will check it when you arrive."}


class GuestRequestIn(BaseModel):
    kind: str
    message: str = Field(min_length=2, max_length=500)

    @field_validator("kind")
    @classmethod
    def _kind(cls, v: str) -> str:
        if v not in REQUEST_KINDS:
            raise ValueError("Unknown request type")
        return v


@portal_public_router.post("/{property_code}/stay/{token}/requests",
                           status_code=201)
def create_guest_request(property_code: str, token: str, body: GuestRequestIn,
                         db: Session = Depends(get_session)):
    prop, reservation_id = _open(db, property_code, token)
    open_count = db.execute(
        text("SELECT count(*) FROM booking.guest_requests "
             "WHERE reservation_id = :r AND status IN ('open', 'in_progress')"),
        {"r": reservation_id},
    ).scalar_one()
    if open_count >= MAX_OPEN_REQUESTS:
        raise HTTPException(
            status_code=429,
            detail="You have several requests open already. Please call the "
                   "front desk.")
    db.execute(
        text("""
            INSERT INTO booking.guest_requests
                (organization_id, property_id, reservation_id, kind, message)
            VALUES (:org, :prop, :r, :k, :m)
        """),
        {"org": prop["organization_id"], "prop": prop["id"],
         "r": reservation_id, "k": body.kind, "m": body.message.strip()},
    )
    return {"detail": "Sent to the front desk."}
