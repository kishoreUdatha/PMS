"""Pydantic request/response models for booking-core."""

from __future__ import annotations

import uuid
from typing import Literal
from datetime import date, datetime, time
from decimal import Decimal

from pydantic import BaseModel, Field, model_validator


class RoomTypeCreate(BaseModel):
    property_id: uuid.UUID
    code: str = Field(max_length=30)
    name: str = Field(max_length=150)
    max_adults: int = Field(default=2, ge=1)
    max_children: int = Field(default=0, ge=0)
    max_occupancy: int = Field(default=2, ge=1)


class RoomTypeOut(BaseModel):
    id: uuid.UUID
    code: str
    name: str
    max_occupancy: int


class InventorySeed(BaseModel):
    """Set/adjust physical capacity for a room type over a date range."""

    property_id: uuid.UUID
    room_type_id: uuid.UUID
    start_date: date
    end_date: date
    physical_capacity: int = Field(ge=0)
    # When True, also reset held/reserved/allotment counters to 0 (setup/demo).
    # When False (default), counters are preserved and the new capacity must not
    # drop below what is already consumed.
    reset_counters: bool = False


# Kept in step with ck_reservation_source in booking-core 0020.
BookingSource = Literal[
    "direct", "website", "phone", "email", "walk_in", "ota",
    "travel_agent", "corporate", "group", "other",
]


class HoldLineIn(BaseModel):
    """One room on the booking, with its own type, plan and occupancy.

    A family taking a suite and a deluxe is one booking, not two, and the two
    rooms do not share a rate or an occupancy. ``reservation_units`` has always
    been able to hold that; this is the API finally able to say it.
    """

    room_type_id: uuid.UUID
    units: int = Field(default=1, ge=1, le=20)
    adults: int = Field(default=1, ge=1)
    children: int = Field(default=0, ge=0)
    rate_plan_id: uuid.UUID | None = None
    meal_plan_id: uuid.UUID | None = None
    #: Agreed rate per night for this room, before tax. Omitted means the
    #: room type's list price stands.
    nightly_rate: Decimal | None = Field(default=None, ge=0)


class HoldCreate(BaseModel):
    property_id: uuid.UUID
    arrival_date: date
    departure_date: date
    idempotency_key: str = Field(min_length=1, max_length=200)
    # Either ``lines`` — one per distinct room — or the flat fields below,
    # which say the same thing for a booking of one kind of room. Callers that
    # only ever book one type keep working untouched.
    lines: list[HoldLineIn] | None = None
    room_type_id: uuid.UUID | None = None
    units: int = Field(default=1, ge=1)
    adults: int = Field(default=1, ge=1)
    children: int = Field(default=0, ge=0)
    guest_id: uuid.UUID | None = None
    # How the booking arrived. A closed set at the database, so the values
    # cannot drift into "walk in", "Walk-In" and "walkin" — and declared here
    # too, so a bad one is a 422 naming the allowed values rather than a check
    # constraint surfacing as a 500.
    source: BookingSource | None = None
    market_segment_id: uuid.UUID | None = None
    purpose_of_stay: str | None = None
    company_name: str | None = None
    travel_agent: str | None = None
    reference: str | None = None
    special_requests: str | None = None
    rate_plan_id: uuid.UUID | None = None
    meal_plan_id: uuid.UUID | None = None
    # Which OTA, which agent — a different question from `source`, which is
    # only how the booking reached us.
    business_source_id: uuid.UUID | None = None
    # Who the bill goes to. 'company' requires an account — the database
    # refuses the pair without one.
    bill_to: str = "guest"
    commercial_account_id: uuid.UUID | None = None
    #: Draw these rooms from a group block instead of general availability.
    #: The block is already holding them off sale, so without this the booking
    #: is refused by the hotel's own block.
    group_block_id: uuid.UUID | None = None
    # What the guest said, as against the property's published times.
    expected_arrival_time: time | None = None
    expected_departure_time: time | None = None

    @model_validator(mode="after")
    def _needs_a_room(self) -> "HoldCreate":
        if not self.lines and self.room_type_id is None:
            raise ValueError(
                "A booking needs at least one room. Send `lines`, or a "
                "`room_type_id` with `units`."
            )
        return self


class GuestCreate(BaseModel):
    full_name: str = Field(min_length=1, max_length=200)
    email: str | None = None
    phone: str | None = None
    nationality: str | None = None
    title: str | None = None
    # engagement.guests has carried these columns since the first migration;
    # nothing has ever filled them at the point a booking is taken, which is
    # the one moment the guest is on the phone to be asked.
    address_line: str | None = None
    city: str | None = None
    state: str | None = None
    postal_code: str | None = None
    country: str | None = None


class GuestOut(BaseModel):
    id: uuid.UUID
    full_name: str
    email: str | None = None
    phone: str | None = None
    nationality: str | None = None


class GuestUpdate(BaseModel):
    full_name: str = Field(min_length=1, max_length=200)
    email: str | None = None
    phone: str | None = None
    nationality: str | None = None


class HoldOut(BaseModel):
    reservation_id: uuid.UUID
    #: The first unit, kept for callers that only ever book one room.
    reservation_unit_id: uuid.UUID
    #: Every unit created, in line order — one per room. A three-room booking
    #: has three of these, and assigning rooms needs all of them.
    reservation_unit_ids: list[uuid.UUID] = Field(default_factory=list)
    hold_id: uuid.UUID
    number: str
    status: str = "held"


class AvailabilityOut(BaseModel):
    stay_date: date
    sellable: int


class RoomTypeAvailability(BaseModel):
    """One room type's availability across a whole stay.

    ``sellable`` is the *minimum* across the nights, because a stay needs the
    same room on every night of it — three free on Monday and none on Tuesday
    sells nothing. ``nights_loaded`` is reported separately so a gap in the
    inventory calendar reads as "not on sale" rather than silently shrinking
    the window the minimum is taken over.
    """

    room_type_id: uuid.UUID
    code: str | None = None
    name: str
    max_occupancy: int | None = None
    sellable: int
    nights: int
    nights_loaded: int
    #: The nightly rate this type would be sold at, so a booking screen can
    #: offer a real price instead of inventing one.
    rate: Decimal | None = None
    # None when bookable; otherwise 'sold_out' or 'not_loaded'.
    blocked_reason: str | None = None


# ---- Reservation flow ----
class ConfirmOut(BaseModel):
    reservation_id: uuid.UUID
    status: str = "confirmed"


class AssignRoomIn(BaseModel):
    room_id: uuid.UUID
    checkin_time: str | None = None
    checkout_time: str | None = None


class AssignRoomOut(BaseModel):
    reservation_unit_id: uuid.UUID
    room_id: uuid.UUID
    room_calendar_entry_id: uuid.UUID


class CheckInIn(BaseModel):
    checked_in_by: uuid.UUID | None = None


class CheckInOut(BaseModel):
    reservation_unit_id: uuid.UUID
    stay_id: uuid.UUID
    status: str = "checked_in"


class CheckOutIn(BaseModel):
    checked_out_by: uuid.UUID | None = None
    business_date: date | None = None


class CheckOutOut(BaseModel):
    reservation_unit_id: uuid.UUID
    stay_id: uuid.UUID
    nights_released: int
    status: str = "checked_out"


class ReservationUnitDetail(BaseModel):
    id: uuid.UUID
    organization_id: uuid.UUID
    property_id: uuid.UUID
    reservation_id: uuid.UUID
    room_type_id: uuid.UUID
    arrival_date: date
    departure_date: date
    nights: int
    adults: int
    children: int
    status: str
    assigned_room_id: uuid.UUID | None = None


class ArrivalOut(BaseModel):
    guest_name: str
    room_no: str
    arrival_time: str
    reservation_no: str
    status: str


class DashboardOut(BaseModel):
    occupancy_pct: int
    arrivals: int
    departures: int
    available_rooms: int
    total_rooms: int
    room_status: dict[str, int]
    housekeeping: dict[str, int]
    # Yesterday's figures, for the dashboard's KPI trend indicators. Defaulted
    # so an older gateway reading this payload is unaffected.
    prev_occupancy_pct: int = 0
    prev_arrivals: int = 0
    prev_departures: int = 0
    occupancy_series: list[dict]
    todays_arrivals: list[ArrivalOut]


class ReservationListItem(BaseModel):
    id: uuid.UUID
    number: str
    status: str
    # None when no guest is on file yet, so a screen can say so rather than
    # print a placeholder that reads like somebody's name.
    guest_name: str | None = None
    # Short enough for a list column: the one type, or the first and a count.
    room_type: str
    # Every distinct type on the booking, for a tooltip or a detail line. A
    # booking can be a suite and a deluxe, and `room_type` alone would read as
    # if it were only the suite.
    room_types: list[str] = []
    arrival_date: date | None = None
    departure_date: date | None = None
    nights: int
    units: int
    currency: str
    # When the booking was taken, as against when the guest arrives.
    booked_at: datetime | None = None
    # When the guest said they would arrive and leave, where they said.
    # Null is the ordinary case and means nobody asked -- not midnight, and
    # not the property's standard hour. A screen that has none shows none.
    expected_arrival_time: time | None = None
    expected_departure_time: time | None = None
    adults: int = 0
    children: int = 0
    # The rooms actually assigned, and how many units are still waiting for
    # one — the desk's job queue, visible without opening each booking.
    room_codes: list[str] = []
    unassigned_units: int = 0
    # Board or rate basis, whichever the booking carries. Rooms on one
    # booking can sit on different ones, so `plans` holds them all and `plan`
    # is the summary that fits a column.
    plan: str | None = None
    plans: list[str] = []
    # Money. Two different questions, kept apart because conflating them is
    # what made a confirmed, fully paid booking read as 0.00 on this list.
    #
    #: What has actually been posted to the folio. The ledger's answer, and
    #: the one the folio and invoice screens show. Zero until the guest has
    #: stayed a night, because a room is charged the night it is slept in.
    total_charges: Decimal = Decimal("0")
    #: What the stay is expected to come to: the rate it was sold at for the
    #: whole stay, plus extras already on the folio. This is what a person
    #: means by the total of a booking, and what a list column should show —
    #: a booking six months out has a value even though nothing is charged.
    total_amount: Decimal = Decimal("0")
    #: The room half of that on its own, before extras.
    room_value: Decimal = Decimal("0")
    total_paid: Decimal = Decimal("0")
    #: Measured against ``total_amount``. Against charges it would report a
    #: prepaid booking as a negative balance — money owed *to* the guest —
    #: when in fact they are simply paid up in advance.
    balance_due: Decimal = Decimal("0")
    has_folio: bool = False


class ReservationUnitLine(BaseModel):
    id: uuid.UUID
    room_type: str
    arrival_date: date
    departure_date: date
    nights: int
    adults: int
    children: int
    status: str
    assigned_room: str | None = None
    # The room's id as well as its code, so a screen can link to the room
    # rather than only name it.
    assigned_room_id: uuid.UUID | None = None
    # Room revenue for this unit: nights x the rate resolved for each night.
    value: Decimal = Decimal("0")


class ReservationDetail(BaseModel):
    id: uuid.UUID
    number: str
    status: str
    currency: str
    guest_name: str | None = None
    units: list[ReservationUnitLine]
    # Sum of the unit values. This is the room revenue the booking is worth,
    # which is what a deposit is a percentage of (screen 058). It is not the
    # folio total: extras and taxes land on the folio as they are incurred.
    booking_value: Decimal = Decimal("0")
