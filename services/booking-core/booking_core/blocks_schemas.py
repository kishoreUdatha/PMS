"""Request/response models for Room Block & Out of Order (screen 063)."""

from __future__ import annotations

import uuid
from datetime import date, datetime

from pydantic import BaseModel, Field

BLOCK_TYPES = ("room_block", "out_of_order")
SEVERITIES = ("low", "medium", "high")
STATUSES = ("active", "ended", "cancelled")

REASON_CATEGORIES = {
    "maintenance_scheduled": "Maintenance - Scheduled",
    "maintenance_emergency": "Maintenance - Emergency",
    "deep_cleaning": "Deep Cleaning",
    "renovation": "Renovation",
    "pest_control": "Pest Control",
    "vip_hold": "VIP Hold",
    "group_hold": "Group Hold",
    "damage": "Damage",
    "other": "Other",
}


class BlockCreate(BaseModel):
    """One action can block several rooms; they share a group."""

    room_ids: list[uuid.UUID] = Field(min_length=1)
    block_type: str = Field(default="room_block")
    reason_category: str
    reason: str | None = Field(default=None, max_length=500)
    severity: str = Field(default="medium")
    start_date: date
    end_date: date
    linked_reference: str | None = Field(default=None, max_length=60)


class BlockUpdate(BaseModel):
    reason_category: str | None = None
    reason: str | None = Field(default=None, max_length=500)
    severity: str | None = None
    start_date: date | None = None
    end_date: date | None = None
    linked_reference: str | None = Field(default=None, max_length=60)
    version: int


class BlockEndIn(BaseModel):
    """End a block early. Defaults to today when no date is given."""

    end_date: date | None = None
    reason: str | None = None


class BlockRoomOut(BaseModel):
    id: uuid.UUID
    room_id: uuid.UUID
    room_code: str
    room_type_name: str
    status: str
    version: int


class BlockGroupOut(BaseModel):
    """One line in the 'Active Blocks and Out of Order Rooms' table."""

    group_id: uuid.UUID
    rooms_label: str          # "101 - 104" or "205"
    room_type_name: str
    block_type: str
    block_type_label: str
    reason_category: str
    reason_category_label: str
    reason: str | None = None
    severity: str
    start_date: date
    end_date: date
    status: str
    linked_reference: str | None = None
    created_by_name: str | None = None
    created_at: datetime | None = None
    rooms: list[BlockRoomOut] = Field(default_factory=list)


class BlockStatsOut(BaseModel):
    """The four KPI cards on screen 063."""

    currently_blocked: int
    out_of_order: int
    due_to_end_today: int
    total_unavailable: int


class BlockConflict(BaseModel):
    """A room that could not be blocked, and why."""

    room_id: uuid.UUID
    room_code: str
    detail: str


class BlockCreateOut(BaseModel):
    """Partial success is possible: some rooms block, others clash."""

    group_id: uuid.UUID | None = None
    created: list[BlockRoomOut] = Field(default_factory=list)
    conflicts: list[BlockConflict] = Field(default_factory=list)


class BlockableRoom(BaseModel):
    id: uuid.UUID
    code: str
    room_type_name: str
    floor: str | None = None
