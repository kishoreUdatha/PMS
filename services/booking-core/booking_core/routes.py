"""Booking-core API routes."""

from __future__ import annotations

import uuid
from datetime import date, timedelta
from decimal import Decimal
from typing import NoReturn

from chirala_common.db import bind_tenant_context
from chirala_common.india import normalise_state
from chirala_common.postal import problem as postal_problem
from chirala_common.audit import record_audit
from chirala_common.authz import (
    caller_org,
    assert_entity_in_org,
    assert_org_matches_caller,
    Caller, assert_property_in_org, build_authz,
)
from chirala_common.routing import TransactionalRoute
from fastapi import (
    APIRouter, BackgroundTasks, Depends, HTTPException, status,
)
from sqlalchemy import text
from sqlalchemy.orm import Session

from . import group_blocks, schemas
from .database import SessionFactory, get_session
from .dashboard import get_dashboard
from .guest_mail import send_confirmation
from .flow import (
    FlowError,
    RoomCollision,
    assign_room,
    check_in,
    check_out,
    confirm_reservation,
)
from .attribute_routes import assert_attribute
from .inventory import HoldLine, InventoryShortage, create_hold
from .settings import settings

_get_caller, require_permission, require_org_permission = build_authz(
    get_session, lambda: settings.environment,
    lambda: settings.service_token,
)

router = APIRouter(route_class=TransactionalRoute)


@router.post(
    "/room-types",
    response_model=schemas.RoomTypeOut,
    status_code=status.HTTP_201_CREATED,
    tags=["room-types"],
)
def create_room_type(body: schemas.RoomTypeCreate, caller: Caller = Depends(require_permission("rooms", "configure")), db: Session = Depends(get_session)):
    # The organisation is the caller's own; the body no longer
    # carries one to disagree with.
    assert_property_in_org(db, caller, body.property_id)
    rt_id = uuid.uuid4()
    db.execute(
        text(
            """
            INSERT INTO property.room_types
                (id, organization_id, property_id, code, name,
                 max_adults, max_children, max_occupancy)
            VALUES (:id, :org, :prop, :code, :name, :ad, :ch, :occ)
            """
        ),
        {
            "id": rt_id,
            "org": caller_org(caller),
            "prop": body.property_id,
            "code": body.code,
            "name": body.name,
            "ad": body.max_adults,
            "ch": body.max_children,
            "occ": body.max_occupancy,
        },
    )
    return schemas.RoomTypeOut(
        id=rt_id, code=body.code, name=body.name, max_occupancy=body.max_occupancy
    )


@router.get(
    "/room-types",
    response_model=list[schemas.RoomTypeOut],
    tags=["room-types"],
)
def list_room_types(property_id: uuid.UUID, caller: Caller = Depends(require_org_permission("rooms", "view")), db: Session = Depends(get_session)):
    assert_property_in_org(db, caller, property_id)
    rows = db.execute(
        text(
            """
            SELECT id, code, name, max_occupancy FROM property.room_types
            WHERE property_id = :prop ORDER BY name
            """
        ),
        {"prop": property_id},
    ).all()
    return [
        schemas.RoomTypeOut(
            id=r.id, code=r.code, name=r.name, max_occupancy=r.max_occupancy
        )
        for r in rows
    ]


@router.post("/inventory/seed", status_code=status.HTTP_200_OK, tags=["inventory"])
def seed_inventory(body: schemas.InventorySeed, caller: Caller = Depends(require_permission("rooms", "configure")), db: Session = Depends(get_session)):
    """Set physical_capacity for each night in [start_date, end_date].

    - reset_counters=True: also zero held/reserved/allotment (setup/demo).
    - reset_counters=False (default): preserve counters; reject if the new
      capacity would drop below units already consumed (would make sellable
      negative), guarding against inconsistent inventory (§4).
    """
    # The organisation is the caller's own; the body no longer
    # carries one to disagree with.
    assert_property_in_org(db, caller, body.property_id)
    if body.end_date < body.start_date:
        raise HTTPException(status_code=422, detail="end_date before start_date")

    day = body.start_date
    count = 0
    while day <= body.end_date:
        if body.reset_counters:
            db.execute(
                text(
                    """
                    INSERT INTO booking.room_type_inventory_days
                        (organization_id, property_id, room_type_id, stay_date,
                         physical_capacity, out_of_service, held_units,
                         reserved_units, allotment_units)
                    VALUES (:org, :prop, :rt, :d, :cap, 0, 0, 0, 0)
                    ON CONFLICT (property_id, room_type_id, stay_date)
                    DO UPDATE SET physical_capacity = EXCLUDED.physical_capacity,
                                  out_of_service = 0, held_units = 0,
                                  reserved_units = 0, allotment_units = 0
                    """
                ),
                {
                    "org": caller_org(caller),
                    "prop": body.property_id,
                    "rt": body.room_type_id,
                    "d": day,
                    "cap": body.physical_capacity,
                },
            )
        else:
            # Lock the row (if any), validate, then set capacity only.
            existing = db.execute(
                text(
                    """
                    SELECT out_of_service, held_units, reserved_units,
                           allotment_units
                    FROM booking.room_type_inventory_days
                    WHERE property_id = :prop AND room_type_id = :rt
                      AND stay_date = :d
                    FOR UPDATE
                    """
                ),
                {"prop": body.property_id, "rt": body.room_type_id, "d": day},
            ).first()
            if existing is not None:
                consumed = (
                    existing.out_of_service
                    + existing.held_units
                    + existing.reserved_units
                    + existing.allotment_units
                )
                if body.physical_capacity < consumed:
                    raise HTTPException(
                        status_code=409,
                        detail=(
                            f"Capacity {body.physical_capacity} on {day} is below "
                            f"already-consumed units ({consumed}). Use "
                            f"reset_counters=true to force, or raise capacity."
                        ),
                    )
                db.execute(
                    text(
                        """
                        UPDATE booking.room_type_inventory_days
                        SET physical_capacity = :cap
                        WHERE property_id = :prop AND room_type_id = :rt
                          AND stay_date = :d
                        """
                    ),
                    {
                        "cap": body.physical_capacity,
                        "prop": body.property_id,
                        "rt": body.room_type_id,
                        "d": day,
                    },
                )
            else:
                db.execute(
                    text(
                        """
                        INSERT INTO booking.room_type_inventory_days
                            (organization_id, property_id, room_type_id, stay_date,
                             physical_capacity, out_of_service, held_units,
                             reserved_units, allotment_units)
                        VALUES (:org, :prop, :rt, :d, :cap, 0, 0, 0, 0)
                        """
                    ),
                    {
                        "org": caller_org(caller),
                        "prop": body.property_id,
                        "rt": body.room_type_id,
                        "d": day,
                        "cap": body.physical_capacity,
                    },
                )
        count += 1
        day += timedelta(days=1)
    return {"nights_seeded": count}


@router.get(
    "/availability",
    response_model=list[schemas.AvailabilityOut],
    tags=["inventory"],
)
def availability(
    property_id: uuid.UUID,
    room_type_id: uuid.UUID,
    arrival_date: str,
    departure_date: str,
    caller: Caller = Depends(require_permission("reservations", "view")),
    db: Session = Depends(get_session),
):
    assert_property_in_org(db, caller, property_id)
    rows = db.execute(
        text(
            """
            SELECT stay_date,
                   physical_capacity - out_of_service - held_units
                   - reserved_units - allotment_units AS sellable
            FROM booking.room_type_inventory_days
            WHERE property_id = :prop AND room_type_id = :rt
              AND stay_date >= :arr AND stay_date < :dep
            ORDER BY stay_date
            """
        ),
        {
            "prop": property_id,
            "rt": room_type_id,
            "arr": arrival_date,
            "dep": departure_date,
        },
    ).all()
    return [
        schemas.AvailabilityOut(stay_date=r.stay_date, sellable=r.sellable)
        for r in rows
    ]


@router.get(
    "/availability/by-room-type",
    response_model=list[schemas.RoomTypeAvailability],
    tags=["inventory"],
)
def availability_by_room_type(
    property_id: uuid.UUID,
    arrival_date: date,
    departure_date: date,
    caller: Caller = Depends(require_permission("reservations", "view")),
    db: Session = Depends(get_session),
):
    """How many rooms of each type are sellable for one stay.

    ``/availability`` answers for a single room type, which meant the booking
    screen could only check a type *after* the user had already picked it —
    so a clerk chose, waited, was told it was sold out, and chose again. One
    call for every type lets the picker say up front what is bookable.

    A night with no row in the inventory calendar is not availability, it is
    an absence of information: the calendar is only seeded so far ahead, and
    treating a missing night as sellable would promise rooms nobody has
    loaded. Such a type comes back ``not_loaded`` with nothing sellable, and
    ``nights_loaded`` says how much of the stay was actually covered.
    """
    assert_property_in_org(db, caller, property_id)
    if departure_date <= arrival_date:
        raise HTTPException(
            status_code=422, detail="Departure must be after arrival"
        )
    nights = (departure_date - arrival_date).days

    rows = db.execute(
        text(
            """
            SELECT rt.id, rt.code, rt.name, rt.max_occupancy,
                   COALESCE(MIN(d.physical_capacity - d.out_of_service
                                - d.held_units - d.reserved_units
                                - d.allotment_units), 0) AS sellable,
                   count(d.stay_date) AS nights_loaded,
                   -- What a night of this type actually costs, resolved the
                   -- same way the stayview resolves it: a date-specific rate
                   -- first, then the cheapest room's own price, then the
                   -- type's list price. Without it the booking screen had no
                   -- rate to offer and fell back to a hard-coded number that
                   -- matched no room type in the property.
                   COALESCE(MIN(rc.rate), MIN(eff.lo), rt.base_rate) AS rate
            FROM property.room_types rt
            LEFT JOIN LATERAL (
                SELECT min(COALESCE(rm.base_rate, rt.base_rate)) AS lo
                FROM property.rooms rm
                WHERE rm.room_type_id = rt.id AND rm.status = 'active'
            ) eff ON true
            LEFT JOIN booking.room_type_inventory_days d
                   ON d.room_type_id = rt.id
                  AND d.property_id = rt.property_id
                  AND d.stay_date >= :arr
                  AND d.stay_date < :dep
            LEFT JOIN property.rate_calendar_days rc
                   ON rc.room_type_id = rt.id
                  AND rc.property_id = rt.property_id
                  AND rc.stay_date >= :arr
                  AND rc.stay_date < :dep
            WHERE rt.property_id = :prop
            GROUP BY rt.id, rt.code, rt.name, rt.max_occupancy, rt.base_rate
            ORDER BY rt.name
            """
        ),
        {"prop": property_id, "arr": arrival_date, "dep": departure_date},
    ).mappings().all()

    out = []
    for r in rows:
        loaded = int(r["nights_loaded"])
        sellable = max(0, int(r["sellable"]))
        if loaded < nights:
            reason, sellable = "not_loaded", 0
        elif sellable <= 0:
            reason = "sold_out"
        else:
            reason = None
        out.append(
            schemas.RoomTypeAvailability(
                room_type_id=r["id"], code=r["code"], name=r["name"],
                max_occupancy=r["max_occupancy"], sellable=sellable,
                nights=nights, nights_loaded=loaded, rate=r["rate"],
                blocked_reason=reason,
            )
        )
    return out


@router.post(
    "/holds",
    response_model=schemas.HoldOut,
    status_code=status.HTTP_201_CREATED,
    tags=["booking"],
)
def create_hold_endpoint(body: schemas.HoldCreate, caller: Caller = Depends(require_permission("reservations", "create")), db: Session = Depends(get_session)):
    """Create a concurrency-safe booking hold (§4).

    The entire operation runs in the single transaction opened by ``get_session``
    so ``SELECT FOR UPDATE`` locks are held until commit.
    """
    # The organisation is the caller's own; the body no longer
    # carries one to disagree with.
    assert_property_in_org(db, caller, body.property_id)
    if body.bill_to == "company" and body.commercial_account_id is None:
        raise HTTPException(
            status_code=422,
            detail="Billing a company needs the company named. Pick a "
                   "commercial account, or bill the guest.",
        )
    if body.commercial_account_id is not None:
        exists = db.execute(
            text("SELECT 1 FROM engagement.commercial_accounts "
                 "WHERE id = :a AND organization_id = :o AND status = 'active'"),
            {"a": body.commercial_account_id, "o": caller_org(caller)},
        ).first()
        if exists is None:
            raise HTTPException(
                status_code=422,
                detail="That commercial account does not exist, belongs to "
                       "another organisation, or is inactive.",
            )

    # A booking of several kinds of room arrives as lines; one of a single
    # kind may still arrive flat. Both end up as lines here, so there is only
    # one path through the locking below.
    lines = [
        HoldLine(room_type_id=ln.room_type_id, units=ln.units,
                 adults=ln.adults, children=ln.children,
                 rate_plan_id=ln.rate_plan_id, meal_plan_id=ln.meal_plan_id,
                 nightly_rate=ln.nightly_rate)
        for ln in (body.lines or [])
    ]
    if lines:
        wanted = {ln.room_type_id for ln in lines}
        known = {
            r[0] for r in db.execute(
                text("SELECT id FROM property.room_types "
                     "WHERE property_id = :p AND id = ANY(:ids)"),
                {"p": body.property_id, "ids": list(wanted)},
            )
        }
        missing = wanted - known
        if missing:
            raise HTTPException(
                status_code=422,
                detail="A room type on this booking does not belong to this "
                       "property.",
            )

    # Classifications are master rows now: check them before anything is
    # locked, so a bad one is a 422 rather than a rolled-back hold.
    assert_attribute(db, attribute_id=body.business_source_id,
                     kind="business_source",
                     organization_id=caller_org(caller))
    assert_attribute(db, attribute_id=body.market_segment_id,
                     kind="market_segment",
                     organization_id=caller_org(caller))

    try:
        result = create_hold(
            db,
            organization_id=caller_org(caller),
            property_id=body.property_id,
            lines=lines or None,
            room_type_id=body.room_type_id,
            arrival_date=body.arrival_date,
            departure_date=body.departure_date,
            units=body.units,
            adults=body.adults,
            children=body.children,
            idempotency_key=body.idempotency_key,
            hold_ttl_minutes=settings.hold_ttl_minutes,
            overbooking_allowance=settings.overbooking_allowance,
            guest_id=body.guest_id,
            source=body.source,
            market_segment_id=body.market_segment_id,
            purpose_of_stay=body.purpose_of_stay,
            company_name=body.company_name,
            travel_agent=body.travel_agent,
            reference=body.reference,
            special_requests=body.special_requests,
            rate_plan_id=body.rate_plan_id,
            meal_plan_id=body.meal_plan_id,
            business_source_id=body.business_source_id,
            bill_to=body.bill_to,
            commercial_account_id=body.commercial_account_id,
            group_block_id=body.group_block_id,
            expected_arrival_time=body.expected_arrival_time,
            expected_departure_time=body.expected_departure_time,
        )
    except InventoryShortage as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except group_blocks.BlockError as exc:
        # A booking drawing on a block that holds nothing -- see `draw_down`.
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    return schemas.HoldOut(
        reservation_id=result.reservation_id,
        reservation_unit_id=result.reservation_unit_id,
        reservation_unit_ids=result.reservation_unit_ids,
        hold_id=result.hold_id,
        number=result.number,
    )


@router.get(
    "/reservations/{reservation_id}/scope",
    tags=["flow"],
)
def reservation_scope(
    reservation_id: uuid.UUID, caller: Caller = Depends(require_org_permission("reservations", "view")), db: Session = Depends(get_session)
):
    """Return organization/property scope for a reservation (for orchestration)."""
    assert_entity_in_org(db, caller, table="booking.reservations", entity_id=reservation_id)
    row = db.execute(
        text(
            """
            SELECT organization_id, property_id, number, status, primary_guest_id
            FROM booking.reservations WHERE id = :id
            """
        ),
        {"id": reservation_id},
    ).first()
    if row is None:
        raise HTTPException(status_code=404, detail="Reservation not found")
    return {
        "reservation_id": str(reservation_id),
        "organization_id": str(row.organization_id),
        "property_id": str(row.property_id),
        "number": row.number,
        "status": row.status,
        "primary_guest_id": str(row.primary_guest_id) if row.primary_guest_id else None,
    }


def _raise_flow(exc: FlowError) -> NoReturn:
    """Translate a FlowError into the right HTTP status."""
    raise HTTPException(
        status_code=409 if exc.conflict else 422, detail=str(exc)
    ) from exc


@router.post(
    "/guests",
    response_model=schemas.GuestOut,
    status_code=status.HTTP_201_CREATED,
    tags=["guests"],
)
def create_guest(body: schemas.GuestCreate, caller: Caller = Depends(require_org_permission("guests", "create")), db: Session = Depends(get_session)):
    wrong = postal_problem(body.postal_code, body.country)
    if wrong:
        raise HTTPException(status_code=422, detail=wrong)
    body.state = normalise_state(body.state)
    # The organisation is the caller's own; the body no longer
    # carries one to disagree with.
    gid = uuid.uuid4()
    db.execute(
        text(
            """
            INSERT INTO engagement.guests
                (id, organization_id, title, full_name, email, phone,
                 nationality, address_line, city, state, postal_code, country)
            VALUES (:id, :org, :title, :name, :email, :phone, :nat,
                    :addr, :city, :state, :zip, :country)
            """
        ),
        {
            "id": gid,
            "org": caller_org(caller),
            "title": body.title,
            "name": body.full_name,
            "email": body.email,
            "phone": body.phone,
            "nat": body.nationality,
            "addr": body.address_line, "city": body.city,
            "state": body.state, "zip": body.postal_code,
            "country": body.country,
        },
    )
    return schemas.GuestOut(
        id=gid,
        full_name=body.full_name,
        email=body.email,
        phone=body.phone,
        nationality=body.nationality,
    )


@router.get(
    "/guests",
    response_model=list[schemas.GuestOut],
    tags=["guests"],
)
def search_guests(
    organization_id: uuid.UUID,
    query: str | None = None,
    caller: Caller = Depends(require_org_permission("guests", "view")),
    db: Session = Depends(get_session),
):
    """Search guests by name/phone/email within an organization."""
    assert_org_matches_caller(caller, organization_id)
    if query:
        rows = db.execute(
            text(
                """
                SELECT id, full_name, email, phone, nationality
                FROM engagement.guests
                WHERE organization_id = :org
                  AND (full_name ILIKE :q OR phone ILIKE :q OR email ILIKE :q)
                ORDER BY full_name LIMIT 200
                """
            ),
            {"org": organization_id, "q": f"%{query}%"},
        ).all()
    else:
        rows = db.execute(
            text(
                """
                SELECT id, full_name, email, phone, nationality
                FROM engagement.guests
                WHERE organization_id = :org
                ORDER BY full_name LIMIT 200
                """
            ),
            {"org": organization_id},
        ).all()
    return [
        schemas.GuestOut(
            id=r.id, full_name=r.full_name, email=r.email, phone=r.phone,
            nationality=r.nationality,
        )
        for r in rows
    ]


@router.get("/guests/{guest_id}", response_model=schemas.GuestOut, tags=["guests"])
def get_guest(
    guest_id: uuid.UUID,
    caller: Caller = Depends(require_org_permission("guests", "view")),
    db: Session = Depends(get_session),
):
    assert_entity_in_org(db, caller, table="engagement.guests", entity_id=guest_id)
    r = db.execute(
        text(
            "SELECT id, full_name, email, phone, nationality "
            "FROM engagement.guests WHERE id = :id"
        ),
        {"id": guest_id},
    ).first()
    if r is None:
        raise HTTPException(status_code=404, detail="Guest not found")
    return schemas.GuestOut(
        id=r.id, full_name=r.full_name, email=r.email, phone=r.phone,
        nationality=r.nationality,
    )


@router.patch("/guests/{guest_id}", response_model=schemas.GuestOut, tags=["guests"])
def update_guest(
    guest_id: uuid.UUID,
    body: schemas.GuestUpdate,
    caller: Caller = Depends(require_org_permission("guests", "edit")),
    db: Session = Depends(get_session),
):
    assert_entity_in_org(db, caller, table="engagement.guests", entity_id=guest_id)
    r = db.execute(
        text(
            """
            UPDATE engagement.guests
            SET full_name = :name, email = :email, phone = :phone,
                nationality = :nat, updated_at = now(), version = version + 1
            WHERE id = :id
            RETURNING id, full_name, email, phone, nationality
            """
        ),
        {
            "id": guest_id, "name": body.full_name, "email": body.email,
            "phone": body.phone, "nat": body.nationality,
        },
    ).first()
    if r is None:
        raise HTTPException(status_code=404, detail="Guest not found")
    return schemas.GuestOut(
        id=r.id, full_name=r.full_name, email=r.email, phone=r.phone,
        nationality=r.nationality,
    )


@router.get(
    "/reservation-units/{unit_id}",
    response_model=schemas.ReservationUnitDetail,
    tags=["flow"],
)
def get_reservation_unit(
    unit_id: uuid.UUID, caller: Caller = Depends(require_org_permission("reservations", "view")), db: Session = Depends(get_session)
):
    """Return reservation-unit detail (nights, dates, status) for orchestration."""
    assert_entity_in_org(db, caller, table="booking.reservation_units", entity_id=unit_id)
    row = db.execute(
        text(
            """
            SELECT id, organization_id, property_id, reservation_id, room_type_id,
                   arrival_date, departure_date, adults, children, status,
                   assigned_room_id,
                   (departure_date - arrival_date) AS nights
            FROM booking.reservation_units
            WHERE id = :id
            """
        ),
        {"id": unit_id},
    ).first()
    if row is None:
        raise HTTPException(status_code=404, detail="Reservation unit not found")
    return schemas.ReservationUnitDetail(
        id=row.id,
        organization_id=row.organization_id,
        property_id=row.property_id,
        reservation_id=row.reservation_id,
        room_type_id=row.room_type_id,
        arrival_date=row.arrival_date,
        departure_date=row.departure_date,
        nights=row.nights,
        adults=row.adults,
        children=row.children,
        status=row.status,
        assigned_room_id=row.assigned_room_id,
    )


@router.post(
    "/reservations/{reservation_id}/confirm",
    response_model=schemas.ConfirmOut,
    tags=["flow"],
)
def confirm_endpoint(
    reservation_id: uuid.UUID,
    background: BackgroundTasks,
    caller: Caller = Depends(require_org_permission("reservations", "create")),
    db: Session = Depends(get_session),
):
    """Confirm a held reservation: held_units -> reserved_units (§4)."""
    # Whose reservation is this? The permission check says the caller may
    # confirm bookings *somewhere*; it says nothing about this booking. Without
    # the tenancy check below, any caller could confirm a reservation belonging
    # to another tenant by naming its id -- and a service caller, which skips
    # the membership check entirely, could confirm anything in the deployment.
    # This is the route the payment webhook calls, so it is the last place to
    # leave unguarded.
    owner = db.execute(
        text("SELECT property_id FROM booking.reservations WHERE id = :r"),
        {"r": reservation_id},
    ).scalar()
    if owner is None:
        raise HTTPException(status_code=404, detail="Reservation not found")
    assert_property_in_org(db, caller, owner)

    try:
        changed = confirm_reservation(db, reservation_id=reservation_id)
    except FlowError as exc:
        _raise_flow(exc)

    # Tell the guest -- but only if this call is what confirmed them, and only
    # after the route commits. Queued rather than sent inline for two reasons:
    # this handler's transaction is still open, so sending here would promise a
    # booking that might yet roll back; and a slow mail server would hold up
    # the payment webhook that is usually the caller.
    if changed:
        background.add_task(_email_guest_confirmation, reservation_id,
                            caller_org(caller))
    return schemas.ConfirmOut(reservation_id=reservation_id)


def _email_guest_confirmation(reservation_id: uuid.UUID, organization_id) -> None:
    """Runs after the response, on its own session, once the booking is real."""
    with SessionFactory() as session:
        # A fresh session has no caller; it acts for the tenant that confirmed.
        bind_tenant_context(session, organization_id=organization_id, is_service=True)
        address = send_confirmation(session, reservation_id)
        if address is None:
            return
        # Recorded so "did the guest ever get a confirmation?" has an answer
        # at the front desk. Only written when something actually went out --
        # an audit row for a mail that failed would be worse than no row.
        row = session.execute(
            text("SELECT organization_id, property_id, number "
                 "FROM booking.reservations WHERE id = :r"),
            {"r": reservation_id},
        ).mappings().first()
        if row is not None:
            record_audit(
                session,
                action="guest_confirmation_sent",
                entity_type="reservation",
                entity_id=str(reservation_id),
                organization_id=row["organization_id"],
                property_id=row["property_id"],
                actor_subject="system",
                after={"to": address, "reservation": row["number"]},
                reason="Booking confirmed.",
            )
        session.commit()


@router.post(
    "/reservation-units/{unit_id}/assign",
    response_model=schemas.AssignRoomOut,
    tags=["flow"],
)
def assign_endpoint(
    unit_id: uuid.UUID,
    body: schemas.AssignRoomIn,
    caller: Caller = Depends(require_org_permission("front_desk", "edit")),
    db: Session = Depends(get_session),
):
    """Assign a physical room; GiST exclusion rejects overlaps with 409 (§4)."""
    assert_entity_in_org(db, caller, table="booking.reservation_units", entity_id=unit_id)
    try:
        result = assign_room(
            db,
            reservation_unit_id=unit_id,
            room_id=body.room_id,
            checkin_time=body.checkin_time,
            checkout_time=body.checkout_time,
        )
    except RoomCollision as exc:
        _raise_flow(exc)
    except FlowError as exc:
        _raise_flow(exc)

    # Who put which guest in which room is an operational decision people ask
    # about later — who got the sea view, why a booking moved. Nothing in
    # flow.py records anything, so it is recorded here.
    ctx = db.execute(
        text(
            """
            SELECT ru.organization_id, ru.property_id, r.number,
                   rm.code AS room_code, g.full_name AS guest_name
            FROM booking.reservation_units ru
            JOIN booking.reservations r ON r.id = ru.reservation_id
            LEFT JOIN property.rooms rm ON rm.id = ru.assigned_room_id
            -- The room's own guest when a rooming list named one: an audit
            -- line saying a room moved should name who was moved.
            LEFT JOIN engagement.guests g
                   ON g.id = COALESCE(ru.guest_id, r.primary_guest_id)
            WHERE ru.id = :u
            """
        ),
        {"u": unit_id},
    ).mappings().first()
    if ctx:
        record_audit(
            db, action="reservation_unit.room_assigned",
            entity_type="reservation_unit", entity_id=str(unit_id),
            organization_id=ctx["organization_id"],
            property_id=ctx["property_id"], actor_subject=caller.subject,
            after={"room": ctx["room_code"], "reservation": ctx["number"],
                   "guest": ctx["guest_name"]},
        )
    return schemas.AssignRoomOut(
        reservation_unit_id=unit_id,
        room_id=result.room_id,
        room_calendar_entry_id=result.room_calendar_entry_id,
    )


@router.post(
    "/reservation-units/{unit_id}/check-in",
    response_model=schemas.CheckInOut,
    tags=["flow"],
)
def check_in_endpoint(
    unit_id: uuid.UUID,
    body: schemas.CheckInIn,
    caller: Caller = Depends(require_org_permission("front_desk", "edit")),
    db: Session = Depends(get_session),
):
    """Check in an assigned, confirmed unit and open a stay (§4)."""
    assert_entity_in_org(db, caller, table="booking.reservation_units", entity_id=unit_id)
    try:
        result = check_in(db, reservation_unit_id=unit_id, checked_in_by=body.checked_in_by)
    except FlowError as exc:
        _raise_flow(exc)
    return schemas.CheckInOut(reservation_unit_id=unit_id, stay_id=result.stay_id)


@router.post(
    "/reservation-units/{unit_id}/check-out",
    response_model=schemas.CheckOutOut,
    tags=["flow"],
)
def check_out_endpoint(
    unit_id: uuid.UUID,
    body: schemas.CheckOutIn,
    caller: Caller = Depends(require_org_permission("front_desk", "edit")),
    db: Session = Depends(get_session),
):
    """Check out: end stay, release future nights, emit room-dirty (§4/§5)."""
    assert_entity_in_org(db, caller, table="booking.reservation_units", entity_id=unit_id)
    try:
        result = check_out(
            db,
            reservation_unit_id=unit_id,
            checked_out_by=body.checked_out_by,
            business_date=body.business_date,
        )
    except FlowError as exc:
        _raise_flow(exc)
    return schemas.CheckOutOut(
        reservation_unit_id=unit_id,
        stay_id=result.stay_id,
        nights_released=result.nights_released,
    )



@router.get(
    "/dashboard",
    response_model=schemas.DashboardOut,
    tags=["dashboard"],
)
def dashboard(
    property_id: uuid.UUID,
    business_date: date | None = None,
    caller: Caller = Depends(require_permission("dashboard", "view")),
    db: Session = Depends(get_session),
):
    """Operational dashboard metrics for a property on a business date."""
    assert_property_in_org(db, caller, property_id)
    bd = business_date or date.today()
    m = get_dashboard(db, property_id=property_id, business_date=bd)
    return schemas.DashboardOut(
        # Whichever day was actually used, echoed back. Everything below is
        # computed against it, and a caller lining the dashboard up with any
        # other screen has no way to do so without being told.
        business_date=m.business_date,
        occupancy_pct=m.occupancy_pct,
        arrivals=m.arrivals,
        departures=m.departures,
        available_rooms=m.available_rooms,
        total_rooms=m.total_rooms,
        room_status=m.room_status,
        housekeeping=m.housekeeping,
        prev_occupancy_pct=m.prev_occupancy_pct,
        prev_arrivals=m.prev_arrivals,
        prev_departures=m.prev_departures,
        occupancy_series=m.occupancy_series,
        todays_arrivals=[
            schemas.ArrivalOut(
                guest_name=a.guest_name,
                room_no=a.room_no,
                arrival_time=a.arrival_time,
                reservation_no=a.reservation_no,
                status=a.status,
            )
            for a in m.todays_arrivals
        ],
    )


# ---- Reservations list + detail (US-028) ----
@router.get(
    "/reservations",
    response_model=list[schemas.ReservationListItem],
    tags=["reservations"],
)
def list_reservations(
    property_id: uuid.UUID,
    status_filter: str | None = None,
    query: str | None = None,
    arrival_from: str | None = None,
    arrival_to: str | None = None,
    caller: Caller = Depends(require_permission("reservations", "view")),
    db: Session = Depends(get_session),
):
    """List reservations for a property with search + filters (US-028).

    Aggregates units into one row per reservation (min arrival, max departure,
    unit count, representative room type) and joins the primary guest name.
    """
    assert_property_in_org(db, caller, property_id)
    status_filter = status_filter or None
    query = query or None
    sql = """
        SELECT r.id, r.number, r.status, r.currency, r.created_at AS booked_at,
               g.full_name AS guest_name,
               -- Every distinct type on the booking, not a representative
               -- one. A booking can now be a suite and a deluxe, and showing
               -- only the alphabetically-first would quietly misdescribe it.
               COALESCE(array_agg(DISTINCT rt.name)
                        FILTER (WHERE rt.name IS NOT NULL), '{}') AS room_types,
               min(ru.arrival_date) AS arrival_date,
               max(ru.departure_date) AS departure_date,
               -- The time from the unit that sets the date, not the earliest
               -- time on the booking. min() over a booking whose rooms arrive
               -- on different days would pair one room's date with another
               -- room's hour, which is worse than saying nothing: the desk
               -- would prepare for an arrival at an hour nobody agreed.
               (array_agg(ru.expected_arrival_time
                          ORDER BY ru.arrival_date))[1]
                   AS expected_arrival_time,
               (array_agg(ru.expected_departure_time
                          ORDER BY ru.departure_date DESC))[1]
                   AS expected_departure_time,
               count(ru.id) AS units,
               COALESCE(sum(ru.adults), 0) AS adults,
               COALESCE(sum(ru.children), 0) AS children,
               -- Rooms actually assigned, and how many units still are not.
               COALESCE(array_agg(DISTINCT rm.code)
                        FILTER (WHERE rm.code IS NOT NULL), '{}') AS room_codes,
               count(ru.id) FILTER (
                   WHERE ru.assigned_room_id IS NULL
                     AND ru.status NOT IN ('cancelled', 'no_show')
               ) AS unassigned_units,
               -- Meal plan names the board; the rate plan names the basis.
               -- Either is more use on a list than neither, and rooms on one
               -- booking can now sit on different ones.
               COALESCE(
                   array_agg(DISTINCT COALESCE(mp.name, rp.name))
                       FILTER (WHERE COALESCE(mp.name, rp.name) IS NOT NULL),
                   '{}') AS plans,
               COALESCE(fin.charges, 0) AS total_charges,
               -- What this booking is worth: the rate it was sold at, for
               -- every room on it, for the whole stay. Cancelled rooms are
               -- excluded because they are not being sold.
               COALESCE(sum(ru.nightly_rate
                            * (ru.departure_date - ru.arrival_date))
                        FILTER (WHERE ru.status NOT IN ('cancelled', 'no_show')),
                        0) AS room_value,
               -- What the stay is expected to come to.
               --
               -- Not the same as what has been charged, and the difference is
               -- the whole point of this column: a room night is posted to the
               -- folio the night it is slept in, so a booking six months out
               -- has no charges at all and read as 0.00 on this list -- which
               -- looks like a booking with no rate rather than one not yet
               -- stayed.
               --
               -- GREATEST, rather than the room value alone, because once the
               -- audit starts posting, the charged figure is the one that has
               -- actually happened: if it exceeds what was booked (an upgrade,
               -- a rate correction) it is the truer number.
               GREATEST(
                   COALESCE(sum(ru.nightly_rate
                                * (ru.departure_date - ru.arrival_date))
                            FILTER (WHERE ru.status
                                    NOT IN ('cancelled', 'no_show')), 0),
                   COALESCE(fin.room_charges, 0)
               ) + COALESCE(fin.other_charges, 0) AS total_amount,
               COALESCE(fin.paid, 0) AS total_paid,
               fin.reservation_id IS NOT NULL AS has_folio
        FROM booking.reservations r
        LEFT JOIN booking.reservation_units ru ON ru.reservation_id = r.id
        LEFT JOIN property.room_types rt ON rt.id = ru.room_type_id
        LEFT JOIN property.rooms rm ON rm.id = ru.assigned_room_id
        LEFT JOIN property.meal_plans mp ON mp.id = ru.meal_plan_id
        LEFT JOIN property.rate_plans rp ON rp.id = ru.rate_plan_id
        LEFT JOIN engagement.guests g ON g.id = r.primary_guest_id
        -- One pass over the ledger for the whole page, joined by reservation.
        -- Charges are what was billed; payments are how it was settled, so
        -- they are summed separately and never netted into one figure.
        LEFT JOIN (
            SELECT f.reservation_id,
                   COALESCE(sum(
                       CASE WHEN e.entry_type = 'debit' THEN e.amount
                            ELSE -e.amount END
                   ) FILTER (
                       WHERE COALESCE(e.source_type, '') NOT IN
                             ('payment', 'refund', 'security_deposit')
                   ), 0) AS charges,
                   -- The room, separately from everything else on the folio.
                   -- The stay's value is already known from the booking, so
                   -- room charges posted by the night audit would be counted
                   -- twice if they were simply added to it.
                   COALESCE(sum(
                       CASE WHEN e.entry_type = 'debit' THEN e.amount
                            ELSE -e.amount END
                   ) FILTER (
                       WHERE COALESCE(e.source_type, '') IN
                             ('room_night', 'room_stay', 'room_upgrade')
                   ), 0) AS room_charges,
                   -- Extras: the bar, a no-show penalty, exclusive tax. These
                   -- are additional to the room and are added as they appear.
                   COALESCE(sum(
                       CASE WHEN e.entry_type = 'debit' THEN e.amount
                            ELSE -e.amount END
                   ) FILTER (
                       WHERE COALESCE(e.source_type, '') NOT IN
                             ('payment', 'refund', 'security_deposit',
                              'room_night', 'room_stay', 'room_upgrade')
                   ), 0) AS other_charges,
                   -- Allocations across every folio on this booking.
                   --
                   -- Correlated on `f.reservation_id`, which is the GROUP BY
                   -- key, so it yields one value per reservation. It cannot
                   -- be `sum()` of a joined column: this query already
                   -- fans out one row per folio ENTRY, so summing anything
                   -- per-folio here would multiply it by that folio's entry
                   -- count. It used to correlate on `f.id`, which was right
                   -- only while the grouping was per folio.
                   COALESCE((
                       SELECT sum(pa.amount)
                         FROM finance.payment_allocations pa
                         JOIN finance.folios pf ON pf.id = pa.folio_id
                        WHERE pf.reservation_id = f.reservation_id
                   ), 0)
                   -- Net of refunds. A payment that was given back is not
                   -- money the property holds, and counting it made the
                   -- balance disagree with the folio ledger.
                   - COALESCE(sum(e.amount) FILTER (
                       WHERE e.entry_type = 'debit' AND e.source_type = 'refund'
                   ), 0) AS paid
            FROM finance.folios f
            LEFT JOIN finance.folio_entries e ON e.folio_id = f.id
            WHERE f.property_id = :prop
            -- By reservation, NOT by folio.
            --
            -- Grouping by `f.id` as well returned one row per folio, and
            -- joining that to the reservation multiplied every other
            -- aggregate on the page by the number of folios. It was
            -- invisible while a booking only ever had one: a group booking
            -- now opens a master plus a folio per guest, and a two-room
            -- booking with three folios reported "Deluxe Room x6" and a
            -- total of three times the money.
            --
            -- A reservation's charges and payments are the sum across all
            -- of its folios, which is what this now says.
            GROUP BY f.reservation_id
        ) fin ON fin.reservation_id = r.id
        WHERE r.property_id = :prop
    """
    params: dict = {"prop": property_id}
    if status_filter:
        sql += " AND r.status = :st"
        params["st"] = status_filter
    if query:
        sql += " AND (r.number ILIKE :q OR g.full_name ILIKE :q)"
        params["q"] = f"%{query}%"
    if arrival_from:
        sql += " AND ru.arrival_date >= :af"
        params["af"] = arrival_from
    if arrival_to:
        sql += " AND ru.arrival_date <= :at"
        params["at"] = arrival_to
    sql += """
        GROUP BY r.id, r.number, r.status, r.currency, r.created_at,
                 g.full_name, fin.charges, fin.room_charges, fin.other_charges,
                 fin.paid, fin.reservation_id
        -- Newest booking first.
        --
        -- This sorted by arrival date, which is the operational order and the
        -- one the Arrivals, In-house and Departures tabs beside it already
        -- use. Those three answer "who is coming today"; this list answers
        -- "what have we got", and the thing somebody opening it wants to see
        -- is what has just come in.
        --
        -- The tiebreak matters as much as the sort. It used to fall back to
        -- r.number, which is effectively random, so a page of bookings that
        -- shared an arrival date came out alphabetically by confirmation
        -- number and the Booked column read 14 Sep, 15 Sep, 14 Sep, 15 Sep --
        -- sorted, and indistinguishable from unsorted. created_at carries the
        -- time, so it is a total order; r.number only breaks the tie if two
        -- bookings were created in the same microsecond.
        ORDER BY r.created_at DESC, r.number
        LIMIT 200
    """
    rows = db.execute(text(sql), params).mappings().all()

    def summarise(names: list[str]) -> str | None:
        """One name, or the first and a count. A list column has no room for
        four room types spelled out, and truncating silently would read as if
        the booking were only the first."""
        names = [n for n in names if n]
        if not names:
            return None
        return names[0] if len(names) == 1 else f"{names[0]} +{len(names) - 1}"

    out = []
    for r in rows:
        nights = 0
        if r["arrival_date"] and r["departure_date"]:
            nights = (r["departure_date"] - r["arrival_date"]).days
        out.append(
            schemas.ReservationListItem(
                id=r["id"], number=r["number"], status=r["status"],
                booked_at=r["booked_at"], adults=int(r["adults"]),
                children=int(r["children"]),
                expected_arrival_time=r["expected_arrival_time"],
                expected_departure_time=r["expected_departure_time"],
                room_codes=list(r["room_codes"] or []),
                unassigned_units=int(r["unassigned_units"]),
                plan=summarise(list(r["plans"] or [])),
                plans=list(r["plans"] or []),
                total_charges=r["total_charges"],
                total_amount=r["total_amount"], room_value=r["room_value"],
                total_paid=r["total_paid"],
                balance_due=r["total_amount"] - r["total_paid"],
                has_folio=bool(r["has_folio"]),
                guest_name=r["guest_name"],
                room_type=summarise(list(r["room_types"] or [])) or '-',
                room_types=list(r["room_types"] or []),
                arrival_date=r["arrival_date"], departure_date=r["departure_date"],
                nights=nights, units=r["units"], currency=r["currency"],
            )
        )
    return out


@router.get(
    "/reservations/{reservation_id}/detail",
    response_model=schemas.ReservationDetail,
    tags=["reservations"],
)
def reservation_detail(
    reservation_id: uuid.UUID, caller: Caller = Depends(require_org_permission("reservations", "view")), db: Session = Depends(get_session)
):
    """Reservation header + its unit lines (US-028)."""
    assert_entity_in_org(db, caller, table="booking.reservations", entity_id=reservation_id)
    head = db.execute(
        text(
            """
            SELECT r.id, r.number, r.status, r.currency,
                   g.full_name AS guest_name
            FROM booking.reservations r
            LEFT JOIN engagement.guests g ON g.id = r.primary_guest_id
            WHERE r.id = :id
            """
        ),
        {"id": reservation_id},
    ).mappings().first()
    if head is None:
        raise HTTPException(status_code=404, detail="Reservation not found")

    # ``value`` prices each night the unit actually stays: the calendar's rate
    # for that date where one is set, the room type's base rate otherwise. That
    # is as far as resolution can honestly go here, because a reservation does
    # not record which rate plan booked it — plans and rules need a plan to
    # apply to. Cancelled units are priced at zero: they are still listed, but
    # they are not revenue, and a deposit percentage of them would be wrong.
    unit_rows = db.execute(
        text(
            """
            SELECT ru.id, COALESCE(rt.name, '-') AS room_type,
                   ru.arrival_date, ru.departure_date, ru.adults, ru.children,
                   ru.status, rm.code AS assigned_room, rm.id AS assigned_room_id,
                   COALESCE((
                       SELECT SUM(COALESCE(cal.rate, rt.base_rate, 0))
                       FROM generate_series(ru.arrival_date,
                                            ru.departure_date - 1,
                                            interval '1 day') AS night
                       LEFT JOIN property.rate_calendar_days cal
                              ON cal.property_id = ru.property_id
                             AND cal.room_type_id = ru.room_type_id
                             AND cal.stay_date = CAST(night AS date)
                   ), 0) AS value
            FROM booking.reservation_units ru
            LEFT JOIN property.room_types rt ON rt.id = ru.room_type_id
            LEFT JOIN property.rooms rm ON rm.id = ru.assigned_room_id
            WHERE ru.reservation_id = :id
            ORDER BY ru.arrival_date, ru.line_index
            """
        ),
        {"id": reservation_id},
    ).mappings().all()

    units = [
        schemas.ReservationUnitLine(
            id=u["id"], room_type=u["room_type"],
            arrival_date=u["arrival_date"], departure_date=u["departure_date"],
            nights=(u["departure_date"] - u["arrival_date"]).days,
            adults=u["adults"], children=u["children"], status=u["status"],
            assigned_room=u["assigned_room"],
            assigned_room_id=u["assigned_room_id"],
            value=Decimal("0") if u["status"] == "cancelled" else u["value"],
        )
        for u in unit_rows
    ]
    return schemas.ReservationDetail(
        id=head["id"], number=head["number"], status=head["status"],
        currency=head["currency"], guest_name=head["guest_name"], units=units,
        booking_value=sum((u.value for u in units), Decimal("0")),
    )
