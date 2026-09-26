"""Guest Check-In API (screen 005).

Check-in is one action made of four that each already exist: know who the guest
is, put them in a room, take what is owed, and open the stay. This module does
not reimplement any of them — it sequences them inside one transaction so a
half-finished check-in cannot exist.

Order matters and is not arbitrary:

1. **Identity first.** The guest record is updated before anything else, because
   the rest is refused if there is no room, and a property that took a payment
   but never recorded who paid is worse off than one that recorded nothing.
2. **Room next**, through ``flow.assign_room`` — so the GiST exclusion
   constraint decides, not this code. If the room was taken in the seconds
   since the screen loaded, the whole check-in fails and nothing is left behind.
3. **Money after the room**, so a payment is never taken for a stay that cannot
   happen.
4. **``flow.check_in`` last**, which opens the stay and flips the room to
   occupied.

Two things on the mockup are recorded rather than performed, and the screen says
so plainly:

* **"Issue Key"** — there is no door-lock integration. The flag records that a
  key was handed over; nothing cuts one.
* **"Welcome message sent"** — there is no mail or SMS transport. The flag
  records that someone sent a welcome, not that the system did.

ID scans are the most sensitive thing here. Only the object-storage key is kept
in the database, and reads go out as presigned URLs that expire.
"""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

from chirala_common import payment_methods
from chirala_common.audit import record_audit
from chirala_common.india import normalise_state
from chirala_common.postal import problem as postal_problem
from chirala_common.authz import Caller, assert_property_in_org, build_authz
from chirala_common.objectstore import (
    ObjectStoreConfig,
    ObjectStoreError,
    build_key,
    delete_object,
    presigned_url,
    put_object,
)
from chirala_common.routing import TransactionalRoute
from chirala_common.folio_posting import resolve_currency
from chirala_common.property_time import trading_day
from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.orm import Session

from .database import get_session
from .resdetail_routes import local_today
from .folio_money import folio_money
from .flow import FlowError, RoomCollision, assign_room, check_in
from .settings import settings

_get_caller, require_permission, _require_org = build_authz(
    get_session, lambda: settings.environment,
    lambda: settings.service_token,
)

checkin_router = APIRouter(tags=["check-in"], route_class=TransactionalRoute)

_STORE = ObjectStoreConfig(
    endpoint=settings.minio_endpoint,
    public_endpoint=settings.minio_public_endpoint,
    access_key=settings.minio_access_key,
    secret_key=settings.minio_secret_key,
    bucket=settings.minio_bucket,
    secure=settings.minio_secure,
    url_ttl_seconds=settings.minio_url_ttl_seconds,
)

ID_TYPES = ("aadhaar", "passport", "driving_licence", "voter_id", "pan", "other")
ID_TYPE_LABELS = {
    "aadhaar": "Aadhaar Card", "passport": "Passport",
    "driving_licence": "Driving Licence", "voter_id": "Voter ID",
    "pan": "PAN Card", "other": "Other",
}
DOC_KINDS = ("id_front", "id_back", "guest_photo", "signature", "other")
MAX_DOC_BYTES = 8 * 1024 * 1024
ALLOWED_DOC_TYPES = {"image/jpeg", "image/png", "image/webp", "application/pdf"}


# --------------------------------------------------------------------------
# Schemas
# --------------------------------------------------------------------------
class ArrivalRow(BaseModel):
    reservation_unit_id: uuid.UUID
    reservation_id: uuid.UUID
    number: str
    guest_name: str | None
    room_type: str
    room_type_id: uuid.UUID
    arrival_date: date
    departure_date: date
    nights: int
    adults: int
    children: int
    unit_status: str
    reservation_status: str
    assigned_room: str | None
    assigned_room_id: uuid.UUID | None
    checked_in: bool
    #: Board or rate basis, whichever the booking carries.
    plan: str | None = None
    # The same three figures Departures and In-house show. An arrival with an
    # unpaid balance is worth seeing before the guest reaches the desk, not
    # after — and a list that changes its columns per tab is a list a desk has
    # to relearn three times.
    # Identifiers the row-level actions need: charge and payment are
    # posted against a folio and scoped by organisation, and the
    # housekeeping actions address the room by id, not by its code.
    organization_id: uuid.UUID | None = None
    folio_id: uuid.UUID | None = None
    #: False where no folio has been opened at all.
    has_folio: bool = False
    total: Decimal = Decimal("0")
    paid: Decimal = Decimal("0")
    balance: Decimal = Decimal("0")


class GuestDoc(BaseModel):
    id: uuid.UUID
    kind: str
    url: str | None
    original_name: str | None
    content_type: str | None


class RoomOption(BaseModel):
    id: uuid.UUID
    code: str
    floor: str | None
    bed_setup: str | None
    view_type: str | None


class CheckInView(BaseModel):
    reservation_unit_id: uuid.UUID
    reservation_id: uuid.UUID
    number: str
    reservation_status: str
    unit_status: str
    already_checked_in: bool

    guest_id: uuid.UUID | None
    guest_name: str | None
    email: str | None
    phone: str | None
    nationality: str | None
    address_line: str | None
    city: str | None
    state: str | None
    postal_code: str | None
    country: str | None
    id_type: str | None
    id_type_label: str | None
    id_number: str | None
    id_verified: bool
    documents: list[GuestDoc]

    room_type: str
    room_type_id: uuid.UUID
    room_description: str | None
    assigned_room: str | None
    assigned_room_id: uuid.UUID | None
    available_rooms: list[RoomOption]
    arrival_date: date
    departure_date: date
    nights: int
    adults: int
    children: int

    total_stay_amount: Decimal
    amount_paid: Decimal
    balance: Decimal
    folio_id: uuid.UUID | None
    currency: str

    # Said out loud so the screen never implies a key was cut or a mail sent.
    key_integration_available: bool = False
    messaging_available: bool = False


class GuestIn(BaseModel):
    full_name: str = Field(min_length=1, max_length=200)
    email: str | None = Field(default=None, max_length=200)
    phone: str | None = Field(default=None, max_length=40)
    nationality: str | None = Field(default=None, max_length=80)
    address_line: str | None = Field(default=None, max_length=300)
    city: str | None = Field(default=None, max_length=120)
    state: str | None = Field(default=None, max_length=120)
    postal_code: str | None = Field(default=None, max_length=20)
    country: str | None = Field(default=None, max_length=80)
    id_type: str | None = None
    id_number: str | None = Field(default=None, max_length=60)


class CompleteIn(BaseModel):
    guest: GuestIn
    room_id: uuid.UUID | None = None
    adults: int | None = Field(default=None, ge=1)
    children: int | None = Field(default=None, ge=0)
    deposit_amount: Decimal = Field(default=Decimal("0"), ge=0)
    deposit_method: str | None = Field(default=None, max_length=30)
    id_verified: bool = False
    signature_captured: bool = False
    policies_accepted: bool = False
    welcome_sent: bool = False
    key_issued: bool = False
    notes: str | None = Field(default=None, max_length=500)


class CompleteOut(BaseModel):
    reservation_unit_id: uuid.UUID
    stay_id: uuid.UUID
    room_id: uuid.UUID
    room_code: str
    guest_id: uuid.UUID
    deposit_amount: Decimal
    deposit_payment_id: uuid.UUID | None
    warnings: list[str]


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------
def _doc_url(key: str) -> str | None:
    try:
        return presigned_url(_STORE, key)
    except Exception:  # noqa: BLE001 — a broken store must not break the screen
        return None


def _documents(db: Session, guest_id: uuid.UUID | None) -> list[GuestDoc]:
    if guest_id is None:
        return []
    rows = db.execute(
        text(
            "SELECT id, kind, storage_key, original_name, content_type "
            "FROM engagement.guest_documents WHERE guest_id = :g ORDER BY kind"
        ),
        {"g": guest_id},
    ).mappings().all()
    return [
        GuestDoc(id=r["id"], kind=r["kind"], url=_doc_url(r["storage_key"]),
                 original_name=r["original_name"], content_type=r["content_type"])
        for r in rows
    ]


_UNIT_SQL = """
    SELECT ru.id AS unit_id, ru.reservation_id, ru.room_type_id,
           ru.arrival_date, ru.departure_date, ru.adults, ru.children,
           ru.status AS unit_status, ru.assigned_room_id,
           ru.organization_id, ru.property_id,
           r.number, r.status AS reservation_status, r.currency,
           r.primary_guest_id, ru.guest_id,
           rt.name AS room_type, rt.description AS room_description,
           rt.base_rate,
           -- Board basis, so the room cell reads the same here as it does on
           -- In-house and Departures.
           COALESCE(mp.name, rp.name) AS plan,
           -- What the guest agreed to, falling back to the room type's list
           -- price only for bookings taken before the rate was recorded.
           COALESCE(ru.nightly_rate, rt.base_rate) AS agreed_rate,
           rm.code AS assigned_room,
           g.full_name AS guest_name, g.email, g.phone, g.nationality,
           g.address_line, g.city, g.state, g.postal_code, g.country,
           g.id_type, g.id_number, g.id_verified_at,
           (sc.id IS NOT NULL) AS has_checkin
    FROM booking.reservation_units ru
    JOIN booking.reservations r ON r.id = ru.reservation_id
    LEFT JOIN property.room_types rt ON rt.id = ru.room_type_id
    LEFT JOIN property.meal_plans mp ON mp.id = ru.meal_plan_id
    LEFT JOIN property.rate_plans rp ON rp.id = ru.rate_plan_id
    LEFT JOIN property.rooms rm ON rm.id = ru.assigned_room_id
    -- The room's own guest where a rooming list named one, otherwise the
    -- person who booked. Checking in room 214 of a thirty-room group
    -- against the name on the contract, rather than the name of whoever
    -- is standing there, is how a registration card ends up wrong.
    LEFT JOIN engagement.guests g
           ON g.id = COALESCE(ru.guest_id, r.primary_guest_id)
    LEFT JOIN booking.stay_checkins sc ON sc.reservation_unit_id = ru.id
"""


def _unit(db: Session, unit_id: uuid.UUID, property_id: uuid.UUID):
    row = db.execute(
        text(_UNIT_SQL + " WHERE ru.id = :id AND ru.property_id = :prop"),
        {"id": unit_id, "prop": property_id},
    ).mappings().first()
    if row is None:
        raise HTTPException(status_code=404, detail="Reservation unit not found")
    return row


# --------------------------------------------------------------------------
# Arrivals
# --------------------------------------------------------------------------
@checkin_router.get("/arrivals", response_model=list[ArrivalRow])
def arrivals(
    property_id: uuid.UUID,
    on_date: date | None = Query(None),
    include_checked_in: bool = Query(False),
    search: str | None = Query(None),
    caller: Caller = Depends(require_permission("front_desk", "view")),
    db: Session = Depends(get_session),
):
    """Who is due to arrive, so the desk has a list to work down.

    Defaults to today. Arrivals still sitting unchecked from earlier days are
    included, because a guest who did not turn up yesterday is very much the
    front desk's problem today.
    """
    assert_property_in_org(db, caller, property_id)
    target = on_date or local_today(db, property_id)
    rows = db.execute(
        text(
            _UNIT_SQL
            + """
            WHERE ru.property_id = :prop
              AND ru.status NOT IN ('cancelled', 'no_show')
              AND r.status IN ('confirmed', 'held')
              AND ru.arrival_date <= :d
              AND ru.departure_date > :d
              AND (:inc OR ru.status <> 'checked_in')
              AND (CAST(:q AS text) IS NULL
                   OR r.number ILIKE '%' || CAST(:q AS text) || '%'
                   OR g.full_name ILIKE '%' || CAST(:q AS text) || '%')
            ORDER BY ru.arrival_date, r.number
            LIMIT 200
            """
        ),
        {"prop": property_id, "d": target, "inc": include_checked_in,
         "q": search or None},
    ).mappings().all()
    return [
        ArrivalRow(
            reservation_unit_id=r["unit_id"], reservation_id=r["reservation_id"],
            number=r["number"], guest_name=r["guest_name"],
            room_type=r["room_type"] or "—", room_type_id=r["room_type_id"],
            arrival_date=r["arrival_date"], departure_date=r["departure_date"],
            nights=(r["departure_date"] - r["arrival_date"]).days,
            adults=r["adults"], children=r["children"],
            unit_status=r["unit_status"],
            reservation_status=r["reservation_status"],
            assigned_room=r["assigned_room"],
            assigned_room_id=r["assigned_room_id"],
            checked_in=r["unit_status"] == "checked_in",
            plan=r["plan"],
            organization_id=r["organization_id"],
            **folio_money(db, r["reservation_id"]),
        )
        for r in rows
    ]


# --------------------------------------------------------------------------
# One check-in
# --------------------------------------------------------------------------
def _free_rooms(db: Session, row) -> list[RoomOption]:
    """Rooms of the right type with nothing overlapping the stay.

    Asks the occupancy table rather than a cached availability figure, so the
    list offered here is the list the exclusion constraint will accept.
    """
    rooms = db.execute(
        text(
            """
            SELECT r.id, r.code, r.floor, r.bed_setup, r.view_type
            FROM property.rooms r
            WHERE r.property_id = :prop
              AND r.room_type_id = :rt
              AND r.status = 'active'
              AND r.service_status = 'in_service'
              AND (r.retired_on IS NULL OR r.retired_on > :arrival)
              AND NOT EXISTS (
                    SELECT 1 FROM booking.room_calendar_entries e
                     WHERE e.room_id = r.id
                       AND e.status = 'active'
                       AND e.occupied_period && tstzrange(
                             CAST(:arrival AS timestamptz),
                             CAST(:departure AS timestamptz), '[)')
              )
            ORDER BY r.code
            """
        ),
        {"prop": row["property_id"], "rt": row["room_type_id"],
         "arrival": row["arrival_date"], "departure": row["departure_date"]},
    ).mappings().all()
    return [RoomOption(**r) for r in rooms]


def _ensure_folio(db: Session, row) -> uuid.UUID:
    """Return this reservation's folio, opening one if it has none.

    A folio is the guest's account for the stay, and check-in is exactly when
    it should start existing: the deposit lands on it, and every charge for the
    next few nights will too. Refusing a deposit because no folio had been
    created yet was an obstacle from our own data model, not a rule anyone at a
    front desk would recognise — and it blocked almost every booking, since
    only three of fourteen had one.

    Idempotent: an existing folio is returned untouched.
    """
    existing = db.execute(
        text("SELECT id FROM finance.folios WHERE reservation_id = :r "
             "ORDER BY created_at LIMIT 1"),
        {"r": row["reservation_id"]},
    ).scalar_one_or_none()
    if existing is not None:
        return existing
    folio_id = uuid.uuid4()
    db.execute(
        text(
            """
            -- A folio for a company booking is a company folio from the
            -- start. Deciding at check-out would mean charges posted against
            -- a guest folio that were never the guest's to pay.
            INSERT INTO finance.folios
                (id, organization_id, property_id, reservation_id, type,
                 commercial_account_id, currency, status)
            SELECT :id, :org, :prop, :res,
                   CASE WHEN r.bill_to = 'company' THEN 'company' ELSE 'guest' END,
                   r.commercial_account_id, :cur, 'open'
            FROM booking.reservations r WHERE r.id = :res
            """
        ),
        {"id": folio_id, "org": row["organization_id"],
         "prop": row["property_id"], "res": row["reservation_id"],
         "cur": row["currency"]},
    )
    return folio_id


def _folio_totals(db: Session, reservation_id: uuid.UUID):
    """What the folio says is charged and paid, if one has been opened."""
    row = db.execute(
        text(
            """
            SELECT f.id AS folio_id,
                   COALESCE(SUM(e.amount) FILTER (WHERE e.entry_type = 'debit'), 0)
                     AS charged,
                   COALESCE(SUM(e.amount) FILTER (WHERE e.entry_type = 'credit'), 0)
                     AS paid
            FROM finance.folios f
            LEFT JOIN finance.folio_entries e ON e.folio_id = f.id
            WHERE f.reservation_id = :res
            GROUP BY f.id
            LIMIT 1
            """
        ),
        {"res": reservation_id},
    ).mappings().first()
    return row


@checkin_router.get(
    "/reservation-units/{unit_id}/check-in-view", response_model=CheckInView
)
def check_in_view(
    unit_id: uuid.UUID,
    property_id: uuid.UUID,
    caller: Caller = Depends(require_permission("front_desk", "view")),
    db: Session = Depends(get_session),
):
    """Everything the check-in screen needs, in one call."""
    assert_property_in_org(db, caller, property_id)
    row = _unit(db, unit_id, property_id)
    nights = (row["departure_date"] - row["arrival_date"]).days

    totals = _folio_totals(db, row["reservation_id"])
    # Before any charge is posted the folio is empty, so the stay is worth what
    # it was quoted: nights x the rate the booking was actually taken at. Once
    # the folio has debits it is the authority, because that is what the guest
    # will actually be billed.
    #
    # This used to read the room type's list price, which is not what anybody
    # agreed to the moment a desk gives a discount — a stay booked at 4,500
    # was presented at check-in as 6,500.
    quoted = Decimal(row["agreed_rate"] or 0) * nights
    charged = Decimal(totals["charged"]) if totals else Decimal("0")
    paid = Decimal(totals["paid"]) if totals else Decimal("0")
    total = charged if charged > 0 else quoted

    return CheckInView(
        reservation_unit_id=row["unit_id"], reservation_id=row["reservation_id"],
        number=row["number"], reservation_status=row["reservation_status"],
        unit_status=row["unit_status"],
        already_checked_in=row["unit_status"] == "checked_in",
        # The guest this check-in is actually for -- the room's own, when
        # the rooming list named one.
        guest_id=row["guest_id"] or row["primary_guest_id"],
        guest_name=row["guest_name"],
        email=row["email"], phone=row["phone"], nationality=row["nationality"],
        address_line=row["address_line"], city=row["city"], state=row["state"],
        postal_code=row["postal_code"], country=row["country"],
        id_type=row["id_type"],
        id_type_label=ID_TYPE_LABELS.get(row["id_type"] or ""),
        id_number=row["id_number"],
        id_verified=row["id_verified_at"] is not None,
        documents=_documents(db, row["primary_guest_id"]),
        room_type=row["room_type"] or "—", room_type_id=row["room_type_id"],
        room_description=row["room_description"],
        assigned_room=row["assigned_room"],
        assigned_room_id=row["assigned_room_id"],
        available_rooms=_free_rooms(db, row),
        arrival_date=row["arrival_date"], departure_date=row["departure_date"],
        nights=nights, adults=row["adults"], children=row["children"],
        total_stay_amount=total, amount_paid=paid, balance=total - paid,
        folio_id=totals["folio_id"] if totals else None,
        currency=row["currency"],
    )


def _upsert_guest(db: Session, row, body: GuestIn, caller: Caller) -> uuid.UUID:
    """Write identity onto the guest, creating one if the booking had none."""
    if body.id_type and body.id_type not in ID_TYPES:
        raise HTTPException(status_code=422, detail="Unknown ID type")
    fields = {
        "name": body.full_name.strip(), "email": body.email, "phone": body.phone,
        "nat": body.nationality, "addr": body.address_line, "city": body.city,
        "state": body.state, "pin": body.postal_code, "country": body.country,
        "idt": body.id_type, "idn": body.id_number,
        # Recording an ID number is the verification: someone read the document
        # and typed what it said.
        "verified_by": caller.user_id,
    }
    guest_id = row["primary_guest_id"]
    if guest_id is None:
        guest_id = uuid.uuid4()
        db.execute(
            text(
                """
                INSERT INTO engagement.guests
                    (id, organization_id, full_name, email, phone, nationality,
                     address_line, city, state, postal_code, country,
                     id_type, id_number, id_verified_at, id_verified_by)
                VALUES (:id, :org, :name, :email, :phone, :nat, :addr, :city,
                        :state, :pin, :country, :idt, CAST(:idn AS varchar),
                        CASE WHEN CAST(:idn AS varchar) IS NULL
                             THEN NULL ELSE now() END,
                        CASE WHEN CAST(:idn AS varchar) IS NULL
                             THEN NULL ELSE :verified_by END)
                """
            ),
            {"id": guest_id, "org": row["organization_id"], **fields},
        )
        db.execute(
            text("UPDATE booking.reservations SET primary_guest_id = :g, "
                 "version = version + 1 WHERE id = :r"),
            {"g": guest_id, "r": row["reservation_id"]},
        )
    else:
        db.execute(
            text(
                """
                UPDATE engagement.guests
                   SET full_name = :name, email = :email, phone = :phone,
                       nationality = :nat, address_line = :addr, city = :city,
                       state = :state, postal_code = :pin, country = :country,
                       id_type = :idt, id_number = CAST(:idn AS varchar),
                       id_verified_at = CASE WHEN CAST(:idn AS varchar) IS NULL
                                             THEN id_verified_at ELSE now() END,
                       id_verified_by = CASE WHEN CAST(:idn AS varchar) IS NULL
                                             THEN id_verified_by
                                             ELSE :verified_by END,
                       updated_at = now(), version = version + 1
                 WHERE id = :id
                """
            ),
            {"id": guest_id, **fields},
        )
    return guest_id


class GuestSaved(BaseModel):
    guest_id: uuid.UUID
    documents: list[GuestDoc]


@checkin_router.post(
    "/reservation-units/{unit_id}/guest", response_model=GuestSaved
)
def save_guest(
    unit_id: uuid.UUID,
    property_id: uuid.UUID,
    body: GuestIn,
    caller: Caller = Depends(require_permission("front_desk", "edit")),
    db: Session = Depends(get_session),
):
    """Write the guest's details without completing the check-in.

    ID scans hang off a guest record, and a booking may not have one yet — so
    without this the desk was told to attach a document to something that did
    not exist. Saving identity first breaks that circle, and is useful in its
    own right: details are often taken while the room is still being made up.
    """
    assert_property_in_org(db, caller, property_id)
    row = _unit(db, unit_id, property_id)
    _require_identity(body)
    guest_id = _upsert_guest(db, row, body, caller)
    record_audit(
        db, action="guest.identity.saved", entity_type="guest",
        entity_id=str(guest_id), organization_id=row["organization_id"],
        property_id=property_id, actor_subject=caller.subject,
        after={"reservation_unit": str(unit_id)},
    )
    return GuestSaved(guest_id=guest_id, documents=_documents(db, guest_id))


# Identity a property is obliged to hold before letting someone stay. Enforced
# here and not only in the form: a check-in that skipped it would leave the
# register incomplete, and the screen is not the only way in.
_REQUIRED = [
    ("full_name", "the guest's full name"),
    ("phone", "a mobile number"),
    ("address_line", "an address"),
    ("city", "a city"),
    ("country", "a country"),
    ("id_type", "an ID type"),
    ("id_number", "an ID number"),
]


def _require_identity(guest: GuestIn) -> None:
    missing = [what for field, what in _REQUIRED
               if not (getattr(guest, field) or "").strip()]
    if missing:
        raise HTTPException(
            status_code=422,
            detail="Check-in needs " + ", ".join(missing) + ".",
        )
    wrong = postal_problem(guest.postal_code, guest.country)
    if wrong:
        raise HTTPException(status_code=422, detail=wrong)
    guest.state = normalise_state(guest.state)


@checkin_router.post(
    "/reservation-units/{unit_id}/complete-check-in", response_model=CompleteOut
)
def complete_check_in(
    unit_id: uuid.UUID,
    property_id: uuid.UUID,
    body: CompleteIn,
    caller: Caller = Depends(require_permission("front_desk", "edit")),
    db: Session = Depends(get_session),
):
    """Identity, room, deposit and stay — all of it, or none of it.

    One transaction, so a failure anywhere leaves the booking exactly as it was.
    The room is taken through the exclusion constraint, so two desks checking
    the same room in at once cannot both win.
    """
    assert_property_in_org(db, caller, property_id)
    row = _unit(db, unit_id, property_id)
    warnings: list[str] = []

    if row["unit_status"] == "checked_in":
        raise HTTPException(status_code=409,
                            detail="This guest is already checked in.")
    if row["reservation_status"] not in ("confirmed", "held"):
        raise HTTPException(
            status_code=422,
            detail=f"A {row['reservation_status']} reservation cannot be "
                   f"checked in.",
        )

    _require_identity(body.guest)
    if not body.policies_accepted:
        raise HTTPException(
            status_code=422,
            detail="The guest has to accept the resort policies before "
                   "checking in.",
        )

    guest_id = _upsert_guest(db, row, body.guest, caller)

    # A photographed ID is the record that the document was actually seen.
    front = db.execute(
        text("SELECT 1 FROM engagement.guest_documents "
             "WHERE guest_id = :g AND kind = 'id_front'"),
        {"g": guest_id},
    ).first()
    if front is None:
        raise HTTPException(
            status_code=422,
            detail="Attach a photo of the front of the guest's ID before "
                   "completing the check-in.",
        )

    # The stay needs somewhere to bill from, whether or not a deposit is taken
    # right now.
    folio_id = _ensure_folio(db, row)

    if body.adults is not None or body.children is not None:
        db.execute(
            text("UPDATE booking.reservation_units "
                 "SET adults = COALESCE(:a, adults), "
                 "children = COALESCE(:c, children), version = version + 1 "
                 "WHERE id = :id"),
            {"a": body.adults, "c": body.children, "id": unit_id},
        )

    room_id = row["assigned_room_id"]
    if room_id is None:
        if body.room_id is None:
            raise HTTPException(
                status_code=422,
                detail="Pick a room before completing the check-in.",
            )
        try:
            assign_room(db, reservation_unit_id=unit_id, room_id=body.room_id)
        except RoomCollision as exc:
            raise HTTPException(
                status_code=409,
                detail="That room was taken while this screen was open. "
                       "Pick another.",
            ) from exc
        except FlowError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        room_id = body.room_id
    elif body.room_id is not None and body.room_id != room_id:
        # Moving an already-assigned guest is screen 053's job, not this one.
        raise HTTPException(
            status_code=422,
            detail="This booking already has a room. Use Room Move to change it.",
        )

    deposit_payment_id: uuid.UUID | None = None
    if body.deposit_amount > 0:
        if not body.deposit_method:
            raise HTTPException(status_code=422,
                                detail="Choose how the deposit was paid.")
        deposit_payment_id = _post_deposit(
            db, row, folio_id=folio_id, amount=body.deposit_amount,
            method=body.deposit_method,
        )

    try:
        result = check_in(db, reservation_unit_id=unit_id,
                          checked_in_by=caller.user_id)
    except FlowError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    if body.key_issued:
        warnings.append(
            "Key marked as issued. This system has no door-lock integration, "
            "so nothing was encoded."
        )
    if body.welcome_sent:
        warnings.append(
            "Welcome marked as sent. There is no mail or SMS transport yet, "
            "so no message left the system."
        )

    db.execute(
        text(
            """
            INSERT INTO booking.stay_checkins
                (organization_id, property_id, reservation_unit_id, stay_id,
                 room_id, deposit_amount, deposit_method, deposit_payment_id,
                 id_verified, signature_captured, policies_accepted,
                 welcome_sent, key_issued, notes, checked_in_by)
            VALUES (:org, :prop, :unit, :stay, :room, :amt, :method, :pay,
                    :idv, :sig, :pol, :welcome, :key, :notes, :who)
            """
        ),
        {
            "org": row["organization_id"], "prop": property_id, "unit": unit_id,
            "stay": result.stay_id, "room": room_id,
            "amt": body.deposit_amount, "method": body.deposit_method,
            "pay": deposit_payment_id, "idv": body.id_verified,
            "sig": body.signature_captured, "pol": body.policies_accepted,
            "welcome": body.welcome_sent, "key": body.key_issued,
            "notes": body.notes, "who": caller.user_id,
        },
    )

    code = db.execute(
        text("SELECT code FROM property.rooms WHERE id = :id"), {"id": room_id}
    ).scalar_one()

    record_audit(
        db, action="reservation_unit.check_in", entity_type="reservation_unit",
        entity_id=str(unit_id), organization_id=row["organization_id"],
        property_id=property_id, actor_subject=caller.subject,
        after={"room": code, "guest_id": str(guest_id),
               "stay_id": str(result.stay_id),
               "deposit": str(body.deposit_amount),
               "key_issued": body.key_issued},
    )
    return CompleteOut(
        reservation_unit_id=unit_id, stay_id=result.stay_id, room_id=room_id,
        room_code=code, guest_id=guest_id, deposit_amount=body.deposit_amount,
        deposit_payment_id=deposit_payment_id, warnings=warnings,
    )


def _post_deposit(db: Session, row, *, folio_id, amount: Decimal, method: str):
    """Record the deposit as a real payment credit on the folio.

    Written here rather than called across to finance because booking-core has
    no client for it; the shape is exactly what ``post_payment`` writes, so the
    folio, Payments and the deposit agree.

    That includes the spelling of the method. finance's own endpoint validates
    and canonicalises it, and writing raw here is how "UPI" ended up in
    ``finance.payments`` alongside "upi" -- one method stored as two values,
    splitting every report that groups by it.
    """
    method = payment_methods.normalise(method)
    if method not in payment_methods.METHODS:
        raise HTTPException(
            422, f"Unknown payment method {method!r}. Known methods: "
                 + ", ".join(payment_methods.METHODS))
    payment_id = uuid.uuid4()
    db.execute(
        text(
            """
            INSERT INTO finance.payments
                (id, organization_id, property_id, method, amount, currency,
                 status, received_at)
            VALUES (:id, :org, :prop, :method, :amt, :cur, 'succeeded', now())
            """
        ),
        {"id": payment_id, "org": row["organization_id"],
         "prop": row["property_id"], "method": method, "amt": amount,
         "cur": row["currency"]},
    )
    entry_id = uuid.uuid4()
    db.execute(
        text(
            """
            INSERT INTO finance.folio_entries
                (id, organization_id, property_id, folio_id, entry_type,
                 amount, currency, business_date, source_type, source_id,
                 source_line_key)
            VALUES (:id, :org, :prop, :folio, 'credit', :amt, :cur,
                    :bd, 'security_deposit', :src, :slk)
            """
        ),
        # The ledger's open day and the folio's currency; see
        # chirala_common.property_time.trading_day for why not CURRENT_DATE.
        {"id": entry_id, "org": row["organization_id"],
         "prop": row["property_id"], "folio": folio_id, "amt": amount,
         "cur": resolve_currency(db, stated=None, folio_ids=[folio_id]),
         "bd": trading_day(db, row["property_id"]),
         "src": str(payment_id),
         "slk": f"security_deposit:{payment_id}"},
    )
    db.execute(
        text(
            """
            INSERT INTO finance.payment_allocations
                (organization_id, property_id, payment_id, folio_id, amount,
                 folio_entry_id)
            VALUES (:org, :prop, :pid, :folio, :amt, :eid)
            """
        ),
        {"org": row["organization_id"], "prop": row["property_id"],
         "pid": payment_id, "folio": folio_id, "amt": amount, "eid": entry_id},
    )
    return payment_id


# --------------------------------------------------------------------------
# ID documents
# --------------------------------------------------------------------------
async def _read_doc(file: UploadFile) -> tuple[bytes, str]:
    content_type = (file.content_type or "").lower()
    if content_type not in ALLOWED_DOC_TYPES:
        raise HTTPException(
            status_code=415,
            detail="Upload a JPEG, PNG, WebP or PDF.",
        )
    data = await file.read()
    if not data:
        raise HTTPException(status_code=422, detail="That file is empty.")
    if len(data) > MAX_DOC_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"Keep the file under {MAX_DOC_BYTES // (1024 * 1024)}MB.",
        )
    return data, content_type


@checkin_router.post("/guests/{guest_id}/documents", response_model=GuestDoc)
async def upload_guest_document(
    guest_id: uuid.UUID,
    property_id: uuid.UUID,
    kind: str = Form(...),
    file: UploadFile = File(...),
    caller: Caller = Depends(require_permission("front_desk", "edit")),
    db: Session = Depends(get_session),
):
    """Store an ID scan. Only the key is kept; reads are presigned."""
    assert_property_in_org(db, caller, property_id)
    if kind not in DOC_KINDS:
        raise HTTPException(status_code=422, detail="Unknown document kind")
    guest = db.execute(
        text("SELECT organization_id FROM engagement.guests WHERE id = :id"),
        {"id": guest_id},
    ).mappings().first()
    if guest is None:
        raise HTTPException(status_code=404, detail="Guest not found")

    data, content_type = await _read_doc(file)
    key = build_key("guest-docs", str(guest_id), kind, content_type=content_type)
    try:
        put_object(_STORE, key, data, content_type)
    except ObjectStoreError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    # Replacing a scan removes the old object as well as the row: a superseded
    # ID image should not linger in storage.
    if kind in ("id_front", "id_back"):
        old = db.execute(
            text("SELECT id, storage_key FROM engagement.guest_documents "
                 "WHERE guest_id = :g AND kind = :k"),
            {"g": guest_id, "k": kind},
        ).mappings().first()
        if old:
            db.execute(
                text("DELETE FROM engagement.guest_documents WHERE id = :id"),
                {"id": old["id"]},
            )
            try:
                delete_object(_STORE, old["storage_key"])
            except Exception:  # noqa: BLE001 — the row is gone either way
                pass

    doc_id = uuid.uuid4()
    db.execute(
        text(
            """
            INSERT INTO engagement.guest_documents
                (id, organization_id, guest_id, kind, storage_key, content_type,
                 size_bytes, original_name, uploaded_by)
            VALUES (:id, :org, :g, :k, :key, :ct, :size, :name, :who)
            """
        ),
        {"id": doc_id, "org": guest["organization_id"], "g": guest_id,
         "k": kind, "key": key, "ct": content_type, "size": len(data),
         "name": file.filename, "who": caller.user_id},
    )
    record_audit(
        db, action="guest.document.upload", entity_type="guest",
        entity_id=str(guest_id), organization_id=guest["organization_id"],
        property_id=property_id, actor_subject=caller.subject,
        after={"kind": kind, "content_type": content_type, "bytes": len(data)},
    )
    return GuestDoc(id=doc_id, kind=kind, url=_doc_url(key),
                    original_name=file.filename, content_type=content_type)


@checkin_router.delete("/guests/{guest_id}/documents/{doc_id}", status_code=204)
def delete_guest_document(
    guest_id: uuid.UUID,
    doc_id: uuid.UUID,
    property_id: uuid.UUID,
    caller: Caller = Depends(require_permission("front_desk", "edit")),
    db: Session = Depends(get_session),
):
    """Remove an ID scan, from the database and from storage."""
    assert_property_in_org(db, caller, property_id)
    row = db.execute(
        text("SELECT storage_key, organization_id FROM engagement.guest_documents "
             "WHERE id = :id AND guest_id = :g"),
        {"id": doc_id, "g": guest_id},
    ).mappings().first()
    if row is None:
        raise HTTPException(status_code=404, detail="Document not found")
    db.execute(
        text("DELETE FROM engagement.guest_documents WHERE id = :id"),
        {"id": doc_id},
    )
    try:
        delete_object(_STORE, row["storage_key"])
    except Exception:  # noqa: BLE001
        pass
    record_audit(
        db, action="guest.document.delete", entity_type="guest",
        entity_id=str(guest_id), organization_id=row["organization_id"],
        property_id=property_id, actor_subject=caller.subject,
        before={"document_id": str(doc_id)},
    )
