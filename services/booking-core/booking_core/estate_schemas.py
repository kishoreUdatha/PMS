"""Request/response models for Buildings & Floors (screen 061)."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, Field

STATUSES = ("active", "inactive")


class FloorOut(BaseModel):
    id: uuid.UUID
    building_id: uuid.UUID
    code: str
    name: str
    from_room_no: str | None = None
    to_room_no: str | None = None
    display_order: int
    status: str
    version: int
    # Always counted from the rooms actually linked, never from the range.
    room_count: int = 0
    created_by_name: str | None = None
    created_at: datetime | None = None
    updated_by_name: str | None = None
    updated_at: datetime | None = None


class BuildingOut(BaseModel):
    id: uuid.UUID
    code: str
    name: str
    display_order: int
    status: str
    version: int
    floor_count: int = 0
    room_count: int = 0
    floors: list[FloorOut] = Field(default_factory=list)
    created_by_name: str | None = None
    created_at: datetime | None = None
    updated_by_name: str | None = None
    updated_at: datetime | None = None


class BuildingIn(BaseModel):
    code: str = Field(min_length=1, max_length=30)
    name: str = Field(min_length=1, max_length=120)
    display_order: int = Field(default=0, ge=0)
    status: str = Field(default="active")


class BuildingUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    display_order: int | None = Field(default=None, ge=0)
    status: str | None = None
    version: int
    reason: str | None = None


class FloorIn(BaseModel):
    building_id: uuid.UUID
    code: str = Field(min_length=1, max_length=10)
    name: str = Field(min_length=1, max_length=120)
    from_room_no: str | None = Field(default=None, max_length=30)
    to_room_no: str | None = Field(default=None, max_length=30)
    display_order: int = Field(default=0, ge=0)
    status: str = Field(default="active")


class FloorUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    code: str | None = Field(default=None, min_length=1, max_length=10)
    from_room_no: str | None = Field(default=None, max_length=30)
    to_room_no: str | None = Field(default=None, max_length=30)
    display_order: int | None = Field(default=None, ge=0)
    status: str | None = None
    version: int
    reason: str | None = None


class DependentRoom(BaseModel):
    id: uuid.UUID
    code: str
    room_type_name: str
    status: str
    service_status: str
    active_reservations: int = 0


class FloorDetailOut(FloorOut):
    """Floor plus everything the editor panel needs to reason about it."""

    building_name: str
    active_reservations: int = 0
    # False while rooms on this floor still hold live reservations — the
    # screen explains why rather than letting the user find out on save.
    can_deactivate: bool = True
    blocker_message: str | None = None


class MoveRoomsIn(BaseModel):
    """Reassign every room on a floor (screen 061 'Move Rooms')."""

    target_floor_id: uuid.UUID
    room_ids: list[uuid.UUID] | None = None  # omit to move the whole floor
    reason: str | None = None


class AssignRoomsIn(BaseModel):
    """Put rooms onto a floor, wherever they are now.

    Distinct from MoveRoomsIn, which moves rooms *off* a named floor. A room
    that belongs to no floor cannot be the subject of a move — there is
    nothing to move it from — so without this there is no way to give it one,
    and rooms that arrive without a floor stay unreachable forever.
    """

    room_ids: list[uuid.UUID] = Field(min_length=1)
    reason: str | None = None


class UnassignedRoom(BaseModel):
    """A room no floor claims."""

    id: uuid.UUID
    code: str
    room_type: str | None = None
    #: What the seed wrote in the denormalised columns, which is often the
    #: only clue about where the room was meant to live.
    floor_hint: str | None = None
    building_hint: str | None = None


class ReorderIn(BaseModel):
    """New display order, as an ordered list of ids."""

    ids: list[uuid.UUID] = Field(min_length=1)


class StatusIn(BaseModel):
    status: str
    version: int
    reason: str | None = None
