"""Request/response models for the Rooms, Room Types and Amenities screens.

Covers screens 008 (Rooms & Villas Inventory), 059 (Add or Edit Room),
060 (Room Type Management) and 062 (Amenities Management).
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, Field

ROOM_STATUSES = ("active", "inactive", "draft")
SERVICE_STATUSES = ("in_service", "out_of_service", "maintenance")
ACCESSIBILITY = ("none", "wheelchair", "hearing", "visual")
AMENITY_CATEGORIES = (
    "in_room",
    "bathroom",
    "technology",
    "food_beverage",
    "recreation",
    "safety_security",
)

# Labels as the mockups write them, for the category tabs and dropdowns.
AMENITY_CATEGORY_LABELS = {
    "in_room": "In-Room",
    "bathroom": "Bathroom",
    "technology": "Technology",
    "food_beverage": "Food & Beverage",
    "recreation": "Recreation",
    "safety_security": "Safety & Security",
}


# --------------------------------------------------------------------------
# Amenities (screen 062)
# --------------------------------------------------------------------------
class AmenityIn(BaseModel):
    code: str = Field(min_length=1, max_length=40)
    name: str = Field(min_length=1, max_length=120)
    category: str = Field(default="in_room")
    icon: str | None = Field(default=None, max_length=40)
    is_chargeable: bool = False
    guest_visible: bool = True
    description: str | None = Field(default=None, max_length=300)
    status: str = Field(default="active")
    # Room types this amenity applies to (screen 062 "Assign to Room Types").
    room_type_ids: list[uuid.UUID] = Field(default_factory=list)


class AmenityUpdate(BaseModel):
    """All fields optional: a partial update. ``version`` guards the write."""

    name: str | None = Field(default=None, min_length=1, max_length=120)
    category: str | None = None
    icon: str | None = Field(default=None, max_length=40)
    is_chargeable: bool | None = None
    guest_visible: bool | None = None
    description: str | None = Field(default=None, max_length=300)
    status: str | None = None
    # Omit to leave assignments untouched; pass a list to replace the set.
    room_type_ids: list[uuid.UUID] | None = None
    version: int


class NamedRef(BaseModel):
    id: uuid.UUID
    name: str


class AmenityOut(BaseModel):
    id: uuid.UUID
    code: str
    name: str
    category: str
    category_label: str = ""
    icon: str | None = None
    is_chargeable: bool
    guest_visible: bool = True
    description: str | None = None
    status: str
    version: int
    room_count: int = 0
    room_types: list[NamedRef] = Field(default_factory=list)
    created_by_name: str | None = None
    created_at: datetime | None = None
    updated_by_name: str | None = None
    updated_at: datetime | None = None


class AmenityListOut(BaseModel):
    """Paginated list for screen 062."""

    items: list[AmenityOut]
    total: int


class AmenityStatsOut(BaseModel):
    total: int
    guest_visible: int
    active: int
    inactive: int
    # category code -> count, for the tab labels
    by_category: dict[str, int] = Field(default_factory=dict)


class AmenityMergeIn(BaseModel):
    """Fold a duplicate amenity into a survivor (screen 062 'Merge Duplicate')."""

    target_amenity_id: uuid.UUID
    reason: str | None = None


# --------------------------------------------------------------------------
# Room types (screen 060)
# --------------------------------------------------------------------------
class RoomTypeIn(BaseModel):
    code: str = Field(min_length=1, max_length=30)
    name: str = Field(min_length=1, max_length=150)
    description: str | None = Field(default=None, max_length=500)
    max_adults: int = Field(default=2, ge=1)
    max_children: int = Field(default=0, ge=0)
    max_occupancy: int = Field(default=2, ge=1)
    base_rate: Decimal | None = Field(default=None, ge=0)
    bed_setup: str | None = Field(default=None, max_length=60)
    size_sqft: int | None = Field(default=None, gt=0)
    child_policy: str | None = Field(default=None, max_length=60)
    extra_bed_available: bool = False
    extra_bed_charge: Decimal | None = Field(default=None, ge=0)
    room_view: str | None = Field(default=None, max_length=60)
    default_rate_plan: str | None = Field(default=None, max_length=80)
    status: str = Field(default="active")
    amenity_ids: list[uuid.UUID] = Field(default_factory=list)


class RoomTypeUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=150)
    description: str | None = Field(default=None, max_length=500)
    max_adults: int | None = Field(default=None, ge=1)
    max_children: int | None = Field(default=None, ge=0)
    max_occupancy: int | None = Field(default=None, ge=1)
    base_rate: Decimal | None = Field(default=None, ge=0)
    bed_setup: str | None = Field(default=None, max_length=60)
    size_sqft: int | None = Field(default=None, gt=0)
    child_policy: str | None = Field(default=None, max_length=60)
    extra_bed_available: bool | None = None
    extra_bed_charge: Decimal | None = Field(default=None, ge=0)
    room_view: str | None = Field(default=None, max_length=60)
    default_rate_plan: str | None = Field(default=None, max_length=80)
    status: str | None = None
    # Omit to leave the amenity set untouched; pass a list to replace it.
    amenity_ids: list[uuid.UUID] | None = None
    version: int
    reason: str | None = None


class RoomTypeDetailOut(BaseModel):
    id: uuid.UUID
    code: str
    name: str
    description: str | None = None
    max_adults: int
    max_children: int
    max_occupancy: int
    base_rate: Decimal | None = None
    bed_setup: str | None = None
    size_sqft: int | None = None
    child_policy: str | None = None
    extra_bed_available: bool = False
    extra_bed_charge: Decimal | None = None
    room_view: str | None = None
    default_rate_plan: str | None = None
    status: str
    version: int
    room_count: int = 0
    active_room_count: int = 0
    primary_photo_url: str | None = None
    amenity_ids: list[uuid.UUID] = Field(default_factory=list)
    photos: list["RoomPhotoOut"] = Field(default_factory=list)
    created_by_name: str | None = None
    created_at: datetime | None = None
    updated_by_name: str | None = None
    updated_at: datetime | None = None


class RoomTypeStatsOut(BaseModel):
    """The four KPI cards on screen 060."""

    room_types: int
    active_types: int
    inactive_types: int
    total_rooms: int
    active_rooms: int
    inactive_rooms: int
    max_guest_capacity: int
    average_base_rate: Decimal | None = None


class RoomTypeDuplicateIn(BaseModel):
    """Screen 060 'Duplicate' — clone a type, its policy fields and amenities."""

    name: str | None = Field(default=None, max_length=150)
    code: str | None = Field(default=None, max_length=30)


class StatusChangeIn(BaseModel):
    status: str
    version: int
    reason: str | None = None


# --------------------------------------------------------------------------
# Rooms (screens 008 / 059)
# --------------------------------------------------------------------------
class RoomIn(BaseModel):
    room_type_id: uuid.UUID
    code: str = Field(min_length=1, max_length=30)
    building: str | None = Field(default=None, max_length=100)
    floor: str | None = Field(default=None, max_length=20)
    bed_setup: str | None = Field(default=None, max_length=60)
    view_type: str | None = Field(default=None, max_length=60)
    max_adults: int = Field(default=2, ge=1)
    max_children: int = Field(default=0, ge=0)
    base_rate: Decimal | None = Field(default=None, ge=0)
    housekeeping_zone: str | None = Field(default=None, max_length=100)
    accessibility: str = Field(default="none")
    near_elevator: bool = False
    status: str = Field(default="active")
    service_status: str = Field(default="in_service")
    notes: str | None = None
    active_from: date | None = None
    amenity_ids: list[uuid.UUID] = Field(default_factory=list)


class BulkDeleteIn(BaseModel):
    """Delete several rooms at once (screen 058, Rooms & Villas)."""

    room_ids: list[uuid.UUID] = Field(min_length=1, max_length=500)
    reason: str | None = None


class BulkDeleteRoomResult(BaseModel):
    """What happened to one room, so a partial result can be explained."""

    id: uuid.UUID
    code: str
    deleted: bool
    #: Why not, in words, when ``deleted`` is False.
    reason: str | None = None


class BulkDeleteOut(BaseModel):
    deleted: int
    refused: int
    results: list[BulkDeleteRoomResult]


class RoomUpdate(BaseModel):
    room_type_id: uuid.UUID | None = None
    code: str | None = Field(default=None, min_length=1, max_length=30)
    #: Moves the room to this floor, setting the building, the ids and the
    #: display names together. Takes precedence over ``building``/``floor``.
    floor_id: uuid.UUID | None = None
    building: str | None = Field(default=None, max_length=100)
    floor: str | None = Field(default=None, max_length=20)
    bed_setup: str | None = Field(default=None, max_length=60)
    view_type: str | None = Field(default=None, max_length=60)
    max_adults: int | None = Field(default=None, ge=1)
    max_children: int | None = Field(default=None, ge=0)
    base_rate: Decimal | None = Field(default=None, ge=0)
    housekeeping_zone: str | None = Field(default=None, max_length=100)
    accessibility: str | None = None
    near_elevator: bool | None = None
    status: str | None = None
    service_status: str | None = None
    notes: str | None = None
    # Omit to leave the amenity set untouched; pass a list to replace it.
    amenity_ids: list[uuid.UUID] | None = None
    version: int
    reason: str | None = None


class RoomAmenityOut(BaseModel):
    id: uuid.UUID
    code: str
    name: str
    icon: str | None = None
    category: str


class RoomPhotoOut(BaseModel):
    id: uuid.UUID
    url: str
    caption: str | None = None
    is_primary: bool
    sort_order: int


class RoomListItem(BaseModel):
    """One card/row on the Rooms & Villas inventory grid."""

    id: uuid.UUID
    code: str
    room_type_id: uuid.UUID
    room_type_name: str
    #: The structure link. ``building``/``floor`` below are the display names
    #: that go with them; screens that let you move a room need the ids.
    building_id: uuid.UUID | None = None
    floor_id: uuid.UUID | None = None
    building: str | None = None
    floor: str | None = None
    bed_setup: str | None = None
    view_type: str | None = None
    max_adults: int
    max_children: int
    base_rate: Decimal | None = None
    status: str
    service_status: str
    housekeeping_zone: str | None = None
    accessibility: str
    near_elevator: bool
    version: int
    # Derived operational state, computed at query time (never stored).
    occupancy_state: str
    housekeeping_state: str
    guest_name: str | None = None
    arrival_date: date | None = None
    departure_date: date | None = None
    primary_photo_url: str | None = None
    amenities: list[RoomAmenityOut] = Field(default_factory=list)
    # Today's active block, when there is one (screen 063 owns the record).
    block_group_id: uuid.UUID | None = None
    block_reason: str | None = None
    block_start: date | None = None
    block_end: date | None = None


class RoomDetailOut(RoomListItem):
    notes: str | None = None
    active_from: date | None = None
    retired_on: date | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    photos: list[RoomPhotoOut] = Field(default_factory=list)


class RoomStatsOut(BaseModel):
    """KPI cards across the top of screen 008."""

    total_units: int
    occupied: int
    available: int
    cleaning: int
    maintenance: int
    occupancy_pct: float
    available_pct: float


class RoomFacetsOut(BaseModel):
    """Filter dropdown options, derived from live data."""

    floors: list[str]
    buildings: list[str]
    housekeeping_zones: list[str]
    room_types: list[RoomTypeDetailOut]


class RoomListOut(BaseModel):
    items: list[RoomListItem]
    total: int
    stats: RoomStatsOut


class RoomServiceStatusIn(BaseModel):
    """Block / unblock a room (screen 063's core action, used by the Block button)."""

    service_status: str
    reason: str | None = None
    version: int


class AssignableUnitOut(BaseModel):
    """An unassigned reservation unit that could take this room (screen 008 Assign)."""

    reservation_unit_id: uuid.UUID
    reservation_id: uuid.UUID
    reservation_number: str | None = None
    guest_name: str | None = None
    arrival_date: date
    departure_date: date
    adults: int
    children: int
    status: str


class BulkCreateIn(BaseModel):
    """Create a floor's worth of rooms from a numbering spec.

    Onboarding is where a property types in fifty rooms, and doing that one
    form at a time is why people give up. The spec is what an operator would
    write on paper: ``101-110``, or ``101, 103, 105``, or both together.
    """

    room_type_id: uuid.UUID
    #: "101-110", "101,103,105", or a mixture of ranges and singles.
    spec: str = Field(min_length=1, max_length=500)
    #: The floor these rooms sit on. A floor knows its building, so this one
    #: id fixes both, and the display names are taken from the records rather
    #: than typed in again -- see ``_resolve_floor``.
    floor_id: uuid.UUID | None = None
    #: Display names, for callers that have no structure set up. Ignored when
    #: ``floor_id`` is given.
    building: str | None = Field(default=None, max_length=100)
    floor: str | None = Field(default=None, max_length=20)
    max_adults: int = Field(default=2, ge=1)
    max_children: int = Field(default=0, ge=0)
    base_rate: Decimal | None = Field(default=None, ge=0)
    bed_setup: str | None = Field(default=None, max_length=60)


class BulkCreateRoomResult(BaseModel):
    code: str
    created: bool
    reason: str | None = None


class BulkCreateOut(BaseModel):
    created: int
    skipped: int
    results: list[BulkCreateRoomResult]


# ==================================== booking page branding ===========
class BrandingIn(BaseModel):
    """What a hotel may say about how its own booking page looks.

    Deliberately small. A colour, a line of text and two images is enough for
    a page to belong to the hotel rather than to us; a full theme editor is a
    way to make a booking page worse, and every field here is one more thing
    that can be left half-set.

    No field takes a URL. Images are object-store keys uploaded through the
    same path the room photos use -- a tenant-supplied URL would let a hotel
    point every guest's browser at a host of their choosing.
    """

    brand_color: str | None = Field(default=None, max_length=7)
    tagline: str | None = Field(default=None, max_length=160)
    logo_key: str | None = Field(default=None, max_length=400)
    banner_key: str | None = Field(default=None, max_length=400)


class BrandingOut(BaseModel):
    brand_color: str | None = None
    ink_on_brand: str | None = None
    contrast: float | None = None
    tagline: str | None = None
    logo_url: str | None = None
    banner_url: str | None = None
    updated_at: datetime | None = None
