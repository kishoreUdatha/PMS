"""Booking-core domain models (schema §2 property subset, §4 booking).

Includes room types, rooms, the inventory-day authority, reservations,
reservation units, holds, and room calendar entries. The inventory day counters
and the room calendar GiST exclusion constraint are the concurrency backbone
(§4). Money and locking paths use explicit SQL (see ``inventory.py``); these
models exist for ORM reads and non-critical writes.
"""

from __future__ import annotations

import uuid
from datetime import date

from chirala_common.models import (
    Base,
    TimestampMixin,
    UUIDPrimaryKeyMixin,
    VersionMixin,
)
from sqlalchemy import (
    Date,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

PROPERTY_SCHEMA = "property"
BOOKING_SCHEMA = "booking"


class RoomType(Base, UUIDPrimaryKeyMixin, TimestampMixin, VersionMixin):
    """Sellable room category (§2). Property scoped."""

    __tablename__ = "room_types"
    __table_args__ = (
        UniqueConstraint(
            "property_id", "code", name="uq_room_type_property_code"
        ),
        UniqueConstraint("property_id", "id", name="uq_room_type_property_id"),
        {"schema": PROPERTY_SCHEMA},
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False
    )
    property_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False
    )
    code: Mapped[str] = mapped_column(String(30), nullable=False)
    name: Mapped[str] = mapped_column(String(150), nullable=False)
    max_adults: Mapped[int] = mapped_column(Integer, nullable=False, default=2)
    max_children: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_occupancy: Mapped[int] = mapped_column(Integer, nullable=False, default=2)


class Room(Base, UUIDPrimaryKeyMixin, TimestampMixin, VersionMixin):
    """A physical room/villa (§2). Unique property + code."""

    __tablename__ = "rooms"
    __table_args__ = (
        UniqueConstraint("property_id", "code", name="uq_room_property_code"),
        {"schema": PROPERTY_SCHEMA},
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False
    )
    property_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False
    )
    room_type_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False
    )
    code: Mapped[str] = mapped_column(String(30), nullable=False)
    active_from: Mapped[date | None] = mapped_column(Date, nullable=True)
    retired_on: Mapped[date | None] = mapped_column(Date, nullable=True)


class Reservation(Base, UUIDPrimaryKeyMixin, TimestampMixin, VersionMixin):
    """Reservation header (§4). Unique property + number.

    Statuses: draft, held, confirmed, cancelled, completed.
    """

    __tablename__ = "reservations"
    __table_args__ = (
        UniqueConstraint(
            "property_id", "number", name="uq_reservation_property_number"
        ),
        UniqueConstraint(
            "property_id", "id", name="uq_reservation_property_id"
        ),
        {"schema": BOOKING_SCHEMA},
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False
    )
    property_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False
    )
    number: Mapped[str] = mapped_column(String(30), nullable=False)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="draft"
    )
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="INR")


class ReservationUnit(Base, UUIDPrimaryKeyMixin, TimestampMixin, VersionMixin):
    """One room booked (§4). departure > arrival enforced in DB.

    Per-unit status: reserved, checked_in, checked_out, cancelled, no_show.
    """

    __tablename__ = "reservation_units"
    __table_args__ = {"schema": BOOKING_SCHEMA}

    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False
    )
    property_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False
    )
    reservation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False
    )
    room_type_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False
    )
    arrival_date: Mapped[date] = mapped_column(Date, nullable=False)
    departure_date: Mapped[date] = mapped_column(Date, nullable=False)
    adults: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    children: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="reserved"
    )
