"""Business source and market segment masters.

Two controlled vocabularies a booking is classified by. Business source says
which OTA or agent the booking came through; market segment says what kind of
business it is. They were free text until now, which meant "MakeMyTrip",
"makemytrip" and "MMT" were three sources to every report and one to everybody
else.

**They share a table and differ by ``kind``** because they are the same shape —
a code, a name and whether it is still in use. Splitting them into two tables
would duplicate this file twice over for no difference anybody could name.

**Rows are deactivated, never deleted.** A source with bookings against it is
part of what those bookings say; removing it would leave them classified as
nothing. Deactivating takes it out of the pickers and leaves history intact,
which is why the delete endpoint refuses when a booking still points at the
row and offers deactivation instead.

Reading needs ``reservations.view`` — anybody taking a booking has to see the
list to pick from it. Changing the vocabulary needs ``reservations.configure``,
which is a manager's right, because renaming a segment silently rewrites what
every past booking appears to say.
"""

from __future__ import annotations

import logging
import re
import uuid
from datetime import date, datetime
from decimal import Decimal

from chirala_common.audit import record_audit
from chirala_common.authz import Caller, assert_property_in_org, build_authz, assert_org_matches_caller, caller_org
from chirala_common.routing import TransactionalRoute
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .database import get_session
from .settings import settings

#: A child of uvicorn's logger, so it inherits the handlers the
#: server configured rather than printing into a bare root.
log = logging.getLogger("uvicorn.error").getChild("distribution")

_get_caller, _require_permission, require_org_permission = build_authz(
    get_session, lambda: settings.environment,
    lambda: settings.service_token,
)

attribute_router = APIRouter(tags=["booking-attributes"],
                             route_class=TransactionalRoute)

KINDS = ("business_source", "market_segment")
KIND_LABELS = {
    "business_source": "Business Source",
    "market_segment": "Market Segment",
}
STATUSES = ("active", "inactive")


def _check_kind(kind: str) -> None:
    if kind not in KINDS:
        raise HTTPException(
            status_code=422,
            detail=f"Unknown kind {kind!r}. Use one of: {', '.join(KINDS)}.")


def _auto_code(name: str) -> str:
    """A code from the name, for a desk that should not have to invent one.

    Letters and digits only, upper-cased: "Make My Trip" becomes MAKEMYTRIP.
    Editable afterwards — this is a starting point, not a rule.
    """
    return re.sub(r"[^A-Za-z0-9]+", "", name).upper()[:30] or "CODE"


def assert_attribute(
    db: Session, *, attribute_id, kind: str, organization_id,
    require_active: bool = True,
) -> None:
    """The row exists, belongs to this organisation and is of this kind.

    The foreign key only says the row exists in the table. Without this a
    market segment could be set to a business source, which reads as nonsense
    on every report that groups by it — and a booking could be classified with
    another organisation's vocabulary.

    ``require_active`` is relaxed when re-saving a booking that already
    carries a retired value: taking a source out of use should not make old
    bookings uneditable.
    """
    if attribute_id is None:
        return
    row = db.execute(
        text("SELECT kind, name, status FROM engagement.booking_attributes "
             "WHERE id = :i AND organization_id = :org"),
        {"i": attribute_id, "org": organization_id},
    ).mappings().first()
    label = KIND_LABELS.get(kind, kind).lower()
    if row is None:
        raise HTTPException(
            status_code=422,
            detail=f"That {label} does not exist or belongs to another "
                   f"organisation.")
    if row["kind"] != kind:
        raise HTTPException(
            status_code=422,
            detail=f"{row['name']} is a "
                   f"{KIND_LABELS.get(row['kind'], row['kind']).lower()}, "
                   f"not a {label}.")
    if require_active and row["status"] != "active":
        raise HTTPException(
            status_code=422,
            detail=f"{row['name']} is no longer in use. Pick an active "
                   f"{label}, or reactivate it in the master.")


class AttributeIn(BaseModel):
    kind: str
    name: str = Field(min_length=1, max_length=120)
    # Optional: derived from the name when the desk does not care.
    code: str | None = Field(default=None, max_length=30)
    status: str = "active"
    sort_order: int = 0
    notes: str | None = Field(default=None, max_length=400)


class Attribute(BaseModel):
    id: uuid.UUID
    kind: str
    kind_label: str
    code: str
    name: str
    status: str
    sort_order: int
    notes: str | None
    created_at: datetime
    #: How many bookings are classified by this row. Derived, never stored —
    #: it is what makes deleting refusable and deactivating meaningful.
    bookings: int = 0


class AttributeList(BaseModel):
    rows: list[Attribute]
    total: int
    can_configure: bool


_USED_SQL = """
    SELECT business_source_id AS id, count(*) AS n
    FROM booking.reservations
    WHERE business_source_id IS NOT NULL
    GROUP BY 1
    UNION ALL
    SELECT market_segment_id AS id, count(*) AS n
    FROM booking.reservations
    WHERE market_segment_id IS NOT NULL
    GROUP BY 1
"""


def _usage(db: Session) -> dict[uuid.UUID, int]:
    return {r[0]: int(r[1]) for r in db.execute(text(_USED_SQL))}


def _decorate(db: Session, rows) -> list[Attribute]:
    used = _usage(db)
    return [
        Attribute(
            **{k: r[k] for k in Attribute.model_fields
               if k in r and k not in ("kind_label", "bookings")},
            kind_label=KIND_LABELS.get(r["kind"], r["kind"]),
            bookings=used.get(r["id"], 0),
        )
        for r in rows
    ]


@attribute_router.get("/booking-attributes", response_model=AttributeList)
def list_attributes(
    organization_id: uuid.UUID,
    kind: str | None = Query(None),
    status: str | None = Query(None),
    q: str | None = Query(None),
    caller: Caller = Depends(require_org_permission("reservations", "view")),
    db: Session = Depends(get_session),
):
    """The vocabulary, with how many bookings use each entry."""
    assert_org_matches_caller(caller, organization_id)
    from chirala_common.authz import _GRANT_SQL

    if kind:
        _check_kind(kind)
    rows = db.execute(
        text(
            """
            SELECT * FROM engagement.booking_attributes
            WHERE organization_id = :org
              AND (CAST(:kind AS text) IS NULL OR kind = CAST(:kind AS text))
              AND (CAST(:st AS text) IS NULL OR status = CAST(:st AS text))
              AND (CAST(:q AS text) IS NULL
                   OR name ILIKE '%' || CAST(:q AS text) || '%'
                   OR code ILIKE '%' || CAST(:q AS text) || '%')
            ORDER BY kind, sort_order, name
            """
        ),
        {"org": organization_id, "kind": kind or None,
         "st": status or None, "q": q or None},
    ).mappings().all()

    may = db.execute(
        text(_GRANT_SQL),
        {"uid": caller.user_id, "res": "reservations", "act": "configure",
         "prop": None},
    ).first() is not None

    decorated = _decorate(db, [dict(r) for r in rows])
    return AttributeList(rows=decorated, total=len(decorated),
                         can_configure=may)


@attribute_router.post("/booking-attributes", response_model=Attribute,
                       status_code=201)
def create_attribute(
    body: AttributeIn,
    caller: Caller = Depends(require_org_permission("reservations", "configure")),
    db: Session = Depends(get_session),
):
    """Add an entry to one of the vocabularies."""
    _check_kind(body.kind)
    if body.status not in STATUSES:
        raise HTTPException(
            status_code=422,
            detail=f"Unknown status. Use one of: {', '.join(STATUSES)}.")

    aid = uuid.uuid4()
    code = (body.code or "").strip() or _auto_code(body.name)
    try:
        db.execute(
            text(
                """
                INSERT INTO engagement.booking_attributes
                    (id, organization_id, kind, code, name, status,
                     sort_order, notes, created_by, updated_by)
                VALUES (:id, :org, :kind, :code, :name, :st, :ord, :notes,
                        :who, :who)
                """
            ),
            {"id": aid, "org": caller_org(caller), "kind": body.kind,
             "code": code, "name": body.name.strip(), "st": body.status,
             "ord": body.sort_order, "notes": body.notes,
             "who": caller.user_id},
        )
        db.flush()
    except IntegrityError as exc:
        raise HTTPException(
            status_code=409,
            detail=f"A {KIND_LABELS[body.kind].lower()} with that code or "
                   f"name already exists.",
        ) from exc

    record_audit(
        db, action="booking_attribute.created",
        entity_type="booking_attribute", entity_id=str(aid),
        organization_id=caller_org(caller), property_id=None,
        actor_subject=caller.subject,
        after={"kind": body.kind, "code": code, "name": body.name},
    )
    row = db.execute(
        text("SELECT * FROM engagement.booking_attributes WHERE id = :i"),
        {"i": aid},
    ).mappings().first()
    return _decorate(db, [dict(row)])[0]


@attribute_router.patch("/booking-attributes/{attribute_id}",
                        response_model=Attribute)
def update_attribute(
    attribute_id: uuid.UUID,
    body: AttributeIn,
    caller: Caller = Depends(require_org_permission("reservations", "configure")),
    db: Session = Depends(get_session),
):
    """Correct an entry. The kind cannot change — that would reclassify every
    booking pointing at it into a vocabulary it was never meant for."""
    before = db.execute(
        text("SELECT * FROM engagement.booking_attributes "
             "WHERE id = :i AND organization_id = :org"),
        {"i": attribute_id, "org": caller_org(caller)},
    ).mappings().first()
    if before is None:
        raise HTTPException(status_code=404, detail="Entry not found")
    if body.status not in STATUSES:
        raise HTTPException(
            status_code=422,
            detail=f"Unknown status. Use one of: {', '.join(STATUSES)}.")
    if body.kind != before["kind"]:
        raise HTTPException(
            status_code=422,
            detail="A business source cannot become a market segment. Add a "
                   "new entry and deactivate this one.",
        )

    code = (body.code or "").strip() or _auto_code(body.name)
    try:
        db.execute(
            text(
                """
                UPDATE engagement.booking_attributes
                   SET code = :code, name = :name, status = :st,
                       sort_order = :ord, notes = :notes,
                       updated_by = :who, updated_at = now(),
                       version = version + 1
                 WHERE id = :id
                """
            ),
            {"id": attribute_id, "code": code, "name": body.name.strip(),
             "st": body.status, "ord": body.sort_order, "notes": body.notes,
             "who": caller.user_id},
        )
        db.flush()
    except IntegrityError as exc:
        raise HTTPException(
            status_code=409,
            detail="Another entry already uses that code or name.",
        ) from exc

    fields = {"code": code, "name": body.name.strip(), "status": body.status,
              "sort_order": body.sort_order, "notes": body.notes}
    changed = {k: v for k, v in fields.items()
               if str(v or "") != str(before[k] or "")}
    record_audit(
        db, action="booking_attribute.updated",
        entity_type="booking_attribute", entity_id=str(attribute_id),
        organization_id=caller_org(caller), property_id=None,
        actor_subject=caller.subject,
        before={k: str(before[k]) for k in changed},
        after={k: str(v) for k, v in changed.items()},
    )
    row = db.execute(
        text("SELECT * FROM engagement.booking_attributes WHERE id = :i"),
        {"i": attribute_id},
    ).mappings().first()
    return _decorate(db, [dict(row)])[0]


@attribute_router.delete("/booking-attributes/{attribute_id}",
                         status_code=204)
def delete_attribute(
    attribute_id: uuid.UUID,
    organization_id: uuid.UUID,
    caller: Caller = Depends(require_org_permission("reservations", "configure")),
    db: Session = Depends(get_session),
):
    """Remove an entry nothing uses.

    An entry with bookings against it is part of what those bookings say, so
    it is refused with the count and the alternative — deactivating keeps the
    history readable and takes it out of the pickers, which is what the
    request almost always means.
    """
    assert_org_matches_caller(caller, organization_id)
    row = db.execute(
        text("SELECT * FROM engagement.booking_attributes "
             "WHERE id = :i AND organization_id = :org"),
        {"i": attribute_id, "org": organization_id},
    ).mappings().first()
    if row is None:
        raise HTTPException(status_code=404, detail="Entry not found")

    used = _usage(db).get(attribute_id, 0)
    if used:
        raise HTTPException(
            status_code=409,
            detail=f"{row['name']} classifies {used} booking(s), so deleting "
                   f"it would leave them saying nothing. Set it to inactive "
                   f"instead — it disappears from the pickers and the "
                   f"bookings keep their history.",
        )

    db.execute(
        text("DELETE FROM engagement.booking_attributes WHERE id = :i"),
        {"i": attribute_id},
    )
    record_audit(
        db, action="booking_attribute.deleted",
        entity_type="booking_attribute", entity_id=str(attribute_id),
        organization_id=organization_id, property_id=None,
        actor_subject=caller.subject,
        before={"kind": row["kind"], "code": row["code"], "name": row["name"]},
    )


# --- Channel partners (screen 024) ----------------------------------------
#
# The partners a property sells through, and how much business each has
# actually brought. It reads the same registry the booking screens classify
# against, so the list here and the dropdown there cannot disagree.
#
# **There is no channel-manager integration in this deployment.** No OTA is
# connected, nothing syncs, and no room types are mapped to anybody's
# inventory. So this reports what is true — bookings received and when the
# last one arrived — instead of a connection state it cannot know. A green
# "Connected · 2 min ago" backed by nothing is worse than an empty column: it
# is the one thing that would stop somebody asking why no bookings have come
# in for a fortnight.

PARTNER_TYPES = ("online_channel", "travel_agent", "direct")
PARTNER_TYPE_LABELS = {
    "online_channel": "Online channel",
    "travel_agent": "Travel agent",
    "direct": "Direct",
}


class PartnerRow(BaseModel):
    """One partner, and the business it has actually brought."""

    id: uuid.UUID
    code: str
    name: str
    #: None until somebody classifies it. Rendered as "Unclassified" rather
    #: than guessed at: an OTA and an agent are worked differently, and a
    #: wrong guess sends somebody chasing the wrong thing.
    partner_type: str | None = None
    partner_type_label: str | None = None
    status: str
    #: Bookings attributed to this partner. The real measure of a channel.
    bookings: int = 0
    #: The last one that arrived, which is the honest version of "last sync" —
    #: it is the thing a manager is actually checking when they look at sync
    #: times. None when the partner has never produced a booking.
    last_booking_at: datetime | None = None
    #: Revenue booked through this partner, at the rate the rooms were sold.
    revenue: Decimal = Decimal("0")


class PartnerSummary(BaseModel):
    partners: int
    online_channels: int
    travel_agents: int
    #: Partners that are active but have never produced a booking. This is the
    #: column the mockup gave to "needs attention", and it is the one thing in
    #: that area we can actually answer.
    never_booked: int


class PartnerList(BaseModel):
    summary: PartnerSummary
    rows: list[PartnerRow]
    #: Whether the caller may change the registry, not merely read it.
    can_configure: bool
    #: False everywhere, until somebody integrates a channel manager. The
    #: screen reads this rather than assuming, so the day it becomes true the
    #: page stops claiming otherwise on its own.
    channel_manager_connected: bool = False


@attribute_router.get("/channel-partners", response_model=PartnerList)
def list_channel_partners(
    organization_id: uuid.UUID,
    partner_type: str | None = Query(None),
    status: str | None = Query(None),
    q: str | None = Query(None),
    caller: Caller = Depends(require_org_permission("distribution", "view")),
    db: Session = Depends(get_session),
):
    """Who this organisation sells through, and what each has brought in.

    Gated on ``distribution:view`` -- distribution is the hotel word for the
    channels a property sells through, and it is the permission that already
    guards putting a property on sale.

    Counts are organisation-wide rather than per property, because a business
    source is: the same Booking.com listing feeds every hotel in the group, and
    splitting it per property would report one partner as several.
    """
    assert_org_matches_caller(caller, organization_id)
    if partner_type and partner_type not in PARTNER_TYPES:
        raise HTTPException(
            status_code=422,
            detail=f"Unknown partner type. Use one of: "
                   f"{', '.join(PARTNER_TYPES)}.")

    rows = db.execute(
        text(
            """
            SELECT a.id, a.code, a.name, a.status, a.partner_type,
                   count(r.id) AS bookings,
                   max(r.created_at) AS last_booking_at,
                   COALESCE(sum(v.value), 0) AS revenue
            FROM engagement.booking_attributes a
            LEFT JOIN booking.reservations r
                   ON r.business_source_id = a.id
                  AND r.status <> 'cancelled'
            LEFT JOIN LATERAL (
                SELECT COALESCE(sum(u.nightly_rate
                                    * (u.departure_date - u.arrival_date)), 0)
                           AS value
                FROM booking.reservation_units u
                WHERE u.reservation_id = r.id
                  AND u.status NOT IN ('cancelled', 'no_show')
            ) v ON TRUE
            WHERE a.organization_id = :org
              AND a.kind = 'business_source'
              AND (CAST(:pt AS text) IS NULL
                   OR a.partner_type = CAST(:pt AS text))
              AND (CAST(:st AS text) IS NULL OR a.status = CAST(:st AS text))
              AND (CAST(:q AS text) IS NULL
                   OR a.name ILIKE '%' || CAST(:q AS text) || '%'
                   OR a.code ILIKE '%' || CAST(:q AS text) || '%')
            GROUP BY a.id, a.code, a.name, a.status, a.partner_type,
                     a.sort_order
            ORDER BY a.sort_order, a.name
            """
        ),
        {"org": organization_id, "pt": partner_type or None,
         "st": status or None, "q": q or None},
    ).mappings().all()

    out = [
        PartnerRow(
            id=r["id"], code=r["code"], name=r["name"], status=r["status"],
            partner_type=r["partner_type"],
            partner_type_label=PARTNER_TYPE_LABELS.get(r["partner_type"] or ""),
            bookings=int(r["bookings"]),
            last_booking_at=r["last_booking_at"],
            revenue=Decimal(str(r["revenue"])),
        )
        for r in rows
    ]

    # The summary counts the whole registry, not the filtered view: a strip
    # that changed every time somebody typed in the search box would be
    # reporting the filter rather than the business.
    totals = db.execute(
        text(
            """
            SELECT a.partner_type, a.status, count(*) AS n,
                   count(*) FILTER (
                       WHERE NOT EXISTS (
                           SELECT 1 FROM booking.reservations r
                           WHERE r.business_source_id = a.id
                             AND r.status <> 'cancelled')
                   ) AS never_booked
            FROM engagement.booking_attributes a
            WHERE a.organization_id = :org AND a.kind = 'business_source'
            GROUP BY a.partner_type, a.status
            """
        ),
        {"org": organization_id},
    ).mappings().all()

    summary = PartnerSummary(
        partners=sum(int(t["n"]) for t in totals),
        online_channels=sum(int(t["n"]) for t in totals
                            if t["partner_type"] == "online_channel"),
        travel_agents=sum(int(t["n"]) for t in totals
                          if t["partner_type"] == "travel_agent"),
        never_booked=sum(int(t["never_booked"]) for t in totals
                         if t["status"] == "active"),
    )

    from chirala_common.authz import _GRANT_SQL
    may = db.execute(
        text(_GRANT_SQL),
        {"uid": caller.user_id, "res": "distribution", "act": "configure",
         "prop": None},
    ).first() is not None

    return PartnerList(summary=summary, rows=out, can_configure=may)


class PartnerTypeIn(BaseModel):
    partner_type: str | None = None


@attribute_router.put("/channel-partners/{partner_id}/type",
                      response_model=PartnerRow)
def set_partner_type(
    partner_id: uuid.UUID,
    body: PartnerTypeIn,
    caller: Caller = Depends(
        require_org_permission("distribution", "configure")),
    db: Session = Depends(get_session),
):
    """Classify a partner as an online channel or a travel agent.

    ``null`` puts it back to unclassified, which is a real answer: better an
    empty cell than a wrong label on the list somebody works from.
    """
    if body.partner_type is not None and body.partner_type not in PARTNER_TYPES:
        raise HTTPException(
            status_code=422,
            detail=f"Unknown partner type. Use one of: "
                   f"{', '.join(PARTNER_TYPES)}.")

    row = db.execute(
        text("SELECT id FROM engagement.booking_attributes "
             "WHERE id = :i AND organization_id = :org "
             "AND kind = 'business_source'"),
        {"i": partner_id, "org": caller.organization_id},
    ).first()
    if row is None:
        raise HTTPException(status_code=404, detail="No such partner.")

    db.execute(
        text("UPDATE engagement.booking_attributes "
             "SET partner_type = :pt, updated_at = now(), updated_by = :by "
             "WHERE id = :i"),
        {"pt": body.partner_type, "i": partner_id, "by": caller.user_id},
    )
    record_audit(
        db, action="channel_partner.classified", entity_type="booking_attribute",
        entity_id=str(partner_id), organization_id=caller.organization_id,
        actor_subject=caller.subject,
        after={"partner_type": body.partner_type},
    )
    fresh = db.execute(
        text("SELECT id, code, name, status, partner_type "
             "FROM engagement.booking_attributes WHERE id = :i"),
        {"i": partner_id},
    ).mappings().first()
    return PartnerRow(
        id=fresh["id"], code=fresh["code"], name=fresh["name"],
        status=fresh["status"], partner_type=fresh["partner_type"],
        partner_type_label=PARTNER_TYPE_LABELS.get(fresh["partner_type"] or ""),
    )


# --- Channel connections and mapping (screen 120) --------------------------
#
# What a channel calls our rooms and rates. Collected before anything can read
# it, because it is the half no integration can derive: somebody has to sit
# with the channel's extranet open and write the pairs out, and that work is
# the same whichever channel manager is eventually wired in.
#
# Nothing here syncs. `status` records what a person set, never what a
# connection reported.

CONNECTION_STATUSES = ("not_connected", "configuring", "ready", "paused")
PAYMENT_MODELS = ("hotel_collect", "channel_collect", "virtual_card")


class MappingRow(BaseModel):
    """One of ours, and what the channel calls it."""

    id: uuid.UUID
    #: Our room type or rate plan.
    local_id: uuid.UUID
    local_code: str
    local_name: str
    #: Theirs. Empty string when this one has not been mapped yet.
    external_id: str = ""
    external_name: str | None = None


class ConnectionOut(BaseModel):
    id: uuid.UUID
    partner_id: uuid.UUID
    partner_name: str
    partner_code: str
    property_id: uuid.UUID
    commission_percent: Decimal | None = None
    payment_model: str | None = None
    status: str
    notes: str | None = None
    #: The OTA's own id for this hotel, as the tenant gave it.
    ota_hotel_id: str | None = None
    #: Set once a channel for this OTA exists at the channel manager. Null
    #: means terms recorded and nothing built yet.
    external_channel_id: str | None = None
    #: Rooms and rates, mapped and not. The unmapped ones are included on
    #: purpose: a mapping screen that only lists what is done cannot show what
    #: is left, which is the only question anybody opens it with.
    rooms: list[MappingRow] = []
    rates: list[MappingRow] = []
    rooms_mapped: int = 0
    rooms_total: int = 0
    rates_mapped: int = 0
    rates_total: int = 0


class ConnectionIn(BaseModel):
    partner_id: uuid.UUID
    property_id: uuid.UUID
    commission_percent: Decimal | None = Field(None, ge=0, le=100)
    payment_model: str | None = None
    notes: str | None = Field(None, max_length=400)
    #: This OTA's own id for this hotel -- Agoda's 96019126, and so on. It
    #: comes with the hotel's contract with that OTA and is the one value a
    #: channel cannot be created without.
    #:
    #: Emphatically not the channel manager's property id, which nobody types
    #: and which routes arriving bookings. Storing one where the other belongs
    #: makes every booking for that property unroutable.
    ota_hotel_id: str | None = Field(None, max_length=80)


class MappingIn(BaseModel):
    """One pair. ``external_id`` empty removes the mapping."""

    local_id: uuid.UUID
    external_id: str = Field("", max_length=80)
    external_name: str | None = Field(None, max_length=160)


class MappingsIn(BaseModel):
    rooms: list[MappingIn] | None = None
    rates: list[MappingIn] | None = None


def _require_ota_id(db: Session, partner_id: uuid.UUID,
                    ota_hotel_id: str | None) -> str | None:
    """An online channel needs the OTA's own id for this hotel. Enforced here.

    Without it the connection is a record and nothing else: no channel can be
    built, nothing is sold, and the screens have to keep explaining that what
    looks connected is not. A tenant could add Booking.com, see it listed, and
    wait for bookings that were never going to come -- which is exactly what
    happened to one property in this deployment.

    In the API rather than only in the form, because a rule that lives in a
    form is not a rule. The same connection can be created by the wizard, by
    the partner's own page, or by anything else that speaks to this service.

    Travel agents are exempt and always were. An agent is a record by design
    -- somebody telephones, the booking is attributed to them -- and there is
    no id to give.
    """
    clean = (ota_hotel_id or "").strip()
    if clean:
        return clean

    kind = db.execute(
        text("SELECT partner_type, name FROM engagement.booking_attributes "
             "WHERE id = :i"),
        {"i": partner_id},
    ).mappings().first()
    if kind and kind["partner_type"] == "online_channel":
        raise HTTPException(
            status_code=422,
            detail=f"{kind['name']} needs their own property id for this "
                   f"hotel before it can be connected. It comes with your "
                   f"contract with them, and without it no channel can be "
                   f"built and nothing is sold.",
        )
    return None


def _connection_or_404(db: Session, caller: Caller,
                       connection_id: uuid.UUID):
    row = db.execute(
        text("SELECT * FROM distribution.channel_connections "
             "WHERE id = :i AND organization_id = :o"),
        {"i": connection_id, "o": caller.organization_id},
    ).mappings().first()
    if row is None:
        raise HTTPException(status_code=404, detail="No such connection.")
    return row


def _mappings(db: Session, link_id: uuid.UUID, property_id: uuid.UUID,
              kind: str) -> list[MappingRow]:
    """Every room type (or rate plan) on the property, mapped or not."""
    if kind == "rooms":
        sql = """
            SELECT rt.id AS local_id, rt.code AS local_code,
                   rt.name AS local_name,
                   m.id AS map_id, m.external_id, m.external_name
            FROM property.room_types rt
            LEFT JOIN distribution.channel_room_mappings m
                   ON m.room_type_id = rt.id AND m.link_id = :c
            WHERE rt.property_id = :p AND rt.status = 'active'
            ORDER BY rt.name
        """
    else:
        sql = """
            SELECT rp.id AS local_id, rp.code AS local_code,
                   rp.name AS local_name,
                   m.id AS map_id, m.external_id, m.external_name
            FROM property.rate_plans rp
            LEFT JOIN distribution.channel_rate_mappings m
                   ON m.rate_plan_id = rp.id AND m.link_id = :c
            WHERE rp.property_id = :p
            ORDER BY rp.name
        """
    rows = db.execute(text(sql), {"c": link_id, "p": property_id})
    return [
        MappingRow(
            id=r["map_id"] or r["local_id"], local_id=r["local_id"],
            local_code=r["local_code"], local_name=r["local_name"],
            external_id=r["external_id"] or "",
            external_name=r["external_name"],
        )
        for r in rows.mappings()
    ]


def _present(db: Session, row) -> ConnectionOut:
    partner = db.execute(
        text("SELECT name, code FROM engagement.booking_attributes "
             "WHERE id = :i"),
        {"i": row["partner_id"]},
    ).mappings().first()
    rooms = _mappings(db, row["id"], row["property_id"], "rooms")
    rates = _mappings(db, row["id"], row["property_id"], "rates")
    return ConnectionOut(
        id=row["id"], partner_id=row["partner_id"],
        partner_name=partner["name"] if partner else "(removed)",
        partner_code=partner["code"] if partner else "",
        property_id=row["property_id"],
        commission_percent=row["commission_percent"],
        payment_model=row["payment_model"], status=row["status"],
        notes=row["notes"],
        ota_hotel_id=row["ota_hotel_id"],
        external_channel_id=row["external_channel_id"],
        rooms=rooms, rates=rates,
        rooms_mapped=sum(1 for r in rooms if r.external_id),
        rooms_total=len(rooms),
        rates_mapped=sum(1 for r in rates if r.external_id),
        rates_total=len(rates),
    )


@attribute_router.get("/channel-connections",
                      response_model=list[ConnectionOut])
def list_connections(
    property_id: uuid.UUID,
    caller: Caller = Depends(require_org_permission("distribution", "view")),
    db: Session = Depends(get_session),
):
    """Every channel connection configured for one property."""
    assert_property_in_org(db, caller, property_id)
    rows = db.execute(
        text("SELECT * FROM distribution.channel_connections "
             "WHERE property_id = :p AND organization_id = :o "
             "ORDER BY created_at"),
        {"p": property_id, "o": caller.organization_id},
    ).mappings().all()
    return [_present(db, r) for r in rows]


@attribute_router.get("/channel-connections/{connection_id}",
                      response_model=ConnectionOut)
def get_connection(
    connection_id: uuid.UUID,
    caller: Caller = Depends(require_org_permission("distribution", "view")),
    db: Session = Depends(get_session),
):
    return _present(db, _connection_or_404(db, caller, connection_id))


@attribute_router.post("/channel-connections", response_model=ConnectionOut,
                       status_code=201)
def create_connection(
    body: ConnectionIn,
    caller: Caller = Depends(
        require_org_permission("distribution", "configure")),
    db: Session = Depends(get_session),
):
    """Start a connection for one partner at one property.

    Creating it connects nothing. It opens somewhere to record the channel's
    own identifiers, which is the work that has to happen before any
    integration can be wired in.
    """
    assert_property_in_org(db, caller, body.property_id)
    if body.payment_model and body.payment_model not in PAYMENT_MODELS:
        raise HTTPException(
            status_code=422,
            detail=f"Unknown payment model. Use one of: "
                   f"{', '.join(PAYMENT_MODELS)}.")

    partner = db.execute(
        text("SELECT id FROM engagement.booking_attributes "
             "WHERE id = :i AND organization_id = :o "
             "AND kind = 'business_source'"),
        {"i": body.partner_id, "o": caller.organization_id},
    ).first()
    if partner is None:
        raise HTTPException(status_code=404, detail="No such partner.")

    hotel_id = _require_ota_id(db, body.partner_id, body.ota_hotel_id)

    try:
        cid = db.execute(
            text(
                """
                INSERT INTO distribution.channel_connections
                    (organization_id, property_id, partner_id,
                     commission_percent, payment_model, notes, status,
                     ota_hotel_id, updated_by)
                VALUES (:o, :p, :partner, :comm, :pm, :notes, 'configuring',
                        :hid, :by)
                RETURNING id
                """
            ),
            {"o": caller.organization_id, "p": body.property_id,
             "partner": body.partner_id,
             "comm": body.commission_percent, "pm": body.payment_model,
             "notes": body.notes,
             "hid": hotel_id,
             "by": caller.user_id},
        ).scalar_one()
    except IntegrityError:
        raise HTTPException(
            status_code=409,
            detail="This partner is already connected to this property.",
        ) from None

    record_audit(
        db, action="channel_connection.created",
        entity_type="channel_connection", entity_id=str(cid),
        organization_id=caller.organization_id, property_id=body.property_id,
        actor_subject=caller.subject,
        after={"partner_id": str(body.partner_id),
               "commission_percent": str(body.commission_percent or "")},
    )
    return _present(db, _connection_or_404(db, caller, cid))


@attribute_router.put("/channel-connections/{connection_id}",
                      response_model=ConnectionOut)
def update_connection(
    connection_id: uuid.UUID,
    body: ConnectionIn,
    caller: Caller = Depends(
        require_org_permission("distribution", "configure")),
    db: Session = Depends(get_session),
):
    """Change the channel's identifiers or commercial terms."""
    # The property is named in the body, where guard_request_tenancy
    # cannot see it.
    assert_property_in_org(db, caller, body.property_id)
    _connection_or_404(db, caller, connection_id)
    if body.payment_model and body.payment_model not in PAYMENT_MODELS:
        raise HTTPException(
            status_code=422,
            detail=f"Unknown payment model. Use one of: "
                   f"{', '.join(PAYMENT_MODELS)}.")
    # Also on update: clearing the id would leave the same dead connection
    # this exists to prevent, and an existing one without an id has to be
    # given one rather than saved around.
    hotel_id = _require_ota_id(db, body.partner_id, body.ota_hotel_id)

    try:
        db.execute(
            text(
                """
                UPDATE distribution.channel_connections
                   SET commission_percent = :comm, payment_model = :pm,
                       notes = :notes, ota_hotel_id = :hid,
                       updated_at = now(), updated_by = :by
                 WHERE id = :i
                """
            ),
            {"comm": body.commission_percent, "pm": body.payment_model,
             "notes": body.notes, "hid": hotel_id,
             "by": caller.user_id, "i": connection_id},
        )
    except IntegrityError:
        raise HTTPException(
            status_code=409, detail="Could not save those terms.") from None
    return _present(db, _connection_or_404(db, caller, connection_id))


def _write_mappings(db: Session, link_id: uuid.UUID, body: MappingsIn) -> None:
    """Record what the channel manager calls our rooms and rate plans.

    Sent as whole lists rather than one pair at a time: mapping is done in a
    sitting with the channel's extranet open, and a partial save halfway down
    the list is the state nobody wants to come back to.

    An empty ``external_id`` removes that pair, which is how a mistake is
    undone. Both directions stay unique — the database refuses two of ours
    pointing at one of theirs, because an arriving booking would then be
    ambiguous and the ambiguity would surface at the front desk.
    """
    for kind, entries in (("rooms", body.rooms), ("rates", body.rates)):
        if entries is None:
            continue
        table = ("distribution.channel_room_mappings" if kind == "rooms"
                 else "distribution.channel_rate_mappings")
        col = "room_type_id" if kind == "rooms" else "rate_plan_id"
        for e in entries:
            external = e.external_id.strip()
            if not external:
                db.execute(
                    text(f"DELETE FROM {table} WHERE link_id = :c "
                         f"AND {col} = :l"),
                    {"c": link_id, "l": e.local_id},
                )
                continue
            try:
                db.execute(
                    text(
                        f"""
                        INSERT INTO {table}
                            (link_id, {col}, external_id, external_name)
                        VALUES (:c, :l, :ext, :name)
                        ON CONFLICT (link_id, {col}) DO UPDATE
                           SET external_id = EXCLUDED.external_id,
                               external_name = EXCLUDED.external_name,
                               updated_at = now()
                        """
                    ),
                    {"c": link_id, "l": e.local_id, "ext": external,
                     "name": (e.external_name or "").strip() or None},
                )
            except IntegrityError:
                raise HTTPException(
                    status_code=409,
                    detail=f"'{external}' is already mapped to something else "
                           f"on this property. One of theirs maps to one of "
                           f"ours.",
                ) from None




@attribute_router.delete("/channel-connections/{connection_id}",
                         status_code=204)
def delete_connection(
    connection_id: uuid.UUID,
    caller: Caller = Depends(
        require_org_permission("distribution", "configure")),
    db: Session = Depends(get_session),
):
    """Remove a connection, and the mappings under it.

    Deleting is allowed here in a way it is not for a partner. A partner is
    referenced by every booking that came through it, so removing one would
    orphan their attribution — hence the 409 there. A connection references
    nothing: it is the channel's own identifiers and the pairs we wrote down,
    and no booking points at it. Keeping it as a tombstone would only leave a
    half-configured channel on a screen whose whole job is to show what is
    configured.

    The mappings go with it, by the cascade declared on the foreign key. They
    are meaningless without the connection they belong to — an external room
    code means nothing except in relation to one channel at one property.
    """
    row = _connection_or_404(db, caller, connection_id)
    db.execute(
        text("DELETE FROM distribution.channel_connections WHERE id = :i"),
        {"i": connection_id},
    )
    record_audit(
        db, action="channel_connection.deleted",
        entity_type="channel_connection", entity_id=str(connection_id),
        organization_id=caller.organization_id,
        property_id=row["property_id"], actor_subject=caller.subject,
        before={"partner_id": str(row["partner_id"])},
        reason="Connection removed; its room and rate mappings went with it.",
    )
    return None


class PushResult(BaseModel):
    #: 'ok' | 'partial' | 'failed'. Partial is its own answer: availability
    #: going out while a rate could not be priced is not success.
    status: str
    detail: str




# --- The channel manager link (one per property) ---------------------------
#
# Everything that belongs to the property rather than to any one OTA: the
# aggregator's id for it, what that aggregator calls each room and rate, and
# when it was last pushed to. A hotel selling on Booking.com, Agoda and
# Expedia has one of these and three partner connections.


class LinkOut(BaseModel):
    id: uuid.UUID
    property_id: uuid.UUID
    provider: str
    #: What the channel manager calls this property. Routing an arriving
    #: booking to a tenant is done entirely on this value.
    external_property_id: str | None = None
    currency: str | None = None
    rooms: list[MappingRow] = []
    rates: list[MappingRow] = []
    rooms_mapped: int = 0
    rooms_total: int = 0
    rates_mapped: int = 0
    rates_total: int = 0
    last_pushed_at: datetime | None = None
    last_push_status: str | None = None
    last_push_detail: str | None = None
    #: The structural half. The push columns above say whether the numbers are
    #: current; these say whether the far side knows about this property and
    #: its rooms at all — which the sweep now keeps true without anybody
    #: asking, and which therefore needs somewhere visible to report itself.
    last_provisioned_at: datetime | None = None
    last_provision_status: str | None = None
    last_provision_detail: str | None = None


class LinkIn(BaseModel):
    property_id: uuid.UUID
    external_property_id: str | None = Field(None, max_length=80)
    currency: str | None = Field(None, max_length=3)


def _link_or_404(db: Session, caller: Caller, link_id: uuid.UUID):
    row = db.execute(
        text("SELECT * FROM distribution.channel_manager_links "
             "WHERE id = :i AND organization_id = :o"),
        {"i": link_id, "o": caller.organization_id},
    ).mappings().first()
    if row is None:
        raise HTTPException(status_code=404, detail="No such link.")
    return row


def _present_link(db: Session, row) -> LinkOut:
    rooms = _mappings(db, row["id"], row["property_id"], "rooms")
    rates = _mappings(db, row["id"], row["property_id"], "rates")
    return LinkOut(
        id=row["id"], property_id=row["property_id"],
        provider=row["provider"],
        external_property_id=row["external_property_id"],
        currency=row["currency"],
        rooms=rooms, rates=rates,
        rooms_mapped=sum(1 for r in rooms if r.external_id),
        rooms_total=len(rooms),
        rates_mapped=sum(1 for r in rates if r.external_id),
        rates_total=len(rates),
        last_pushed_at=row["last_pushed_at"],
        last_push_status=row["last_push_status"],
        last_push_detail=row["last_push_detail"],
        last_provisioned_at=row["last_provisioned_at"],
        last_provision_status=row["last_provision_status"],
        last_provision_detail=row["last_provision_detail"],
    )


# --------------------------------------------------------------------------
# Is it actually connected?
# --------------------------------------------------------------------------
#
# The honest answer has four parts, and until now the screens showed one of
# them and let it stand for all four. A row in ``channel_connections`` records
# what an OTA charges; it talks to nobody. Reading it as "connected" told a
# hotel it was selling on Agoda when no Agoda channel existed anywhere.
#
# So this asks the channel manager rather than inferring, and reports each
# link in the chain separately:
#
#   1. the property exists at the channel manager      -- we do this
#   2. its rooms and rate plans are mirrored and paired -- we do this
#   3. a channel for that OTA exists there             -- a person does this
#   4. the OTA has authorised it                       -- the OTA does this
#
# Steps 3 and 4 are not ours to perform and never will be: a channel needs an
# id that comes with a signed contract, and authorisation happens inside the
# OTA's own extranet. Saying so is more useful than a green tick that means
# something narrower than it looks.

def _norm(value: str) -> str:
    """Letters and digits, lower-cased.

    "Booking.com" here and "BookingCom" at the channel manager are the same
    channel. Comparing on this rather than keeping a table of their codes
    means a channel we have never heard of still matches itself.
    """
    return re.sub(r"[^A-Za-z0-9]+", "", value or "").lower()


class OtaRow(BaseModel):
    partner_id: uuid.UUID
    partner_name: str
    commission_percent: Decimal | None = None
    payment_model: str | None = None
    #: Whether a channel for this OTA exists at the channel manager at all.
    channel_exists: bool = False
    #: And whether it is switched on there. Inactive is the normal state for a
    #: channel that has been created but not yet authorised by the OTA.
    channel_active: bool = False
    channel_title: str | None = None
    #: The OTA's own id for this hotel, as configured at the channel manager.
    #: This is the number a hotel gets from the OTA, and the one people expect
    #: to type into this system -- it lives there, not here.
    hotel_id: str | None = None
    #: ``terms_only``, ``configured`` or ``live``.
    state: str
    detail: str


class OtaStatus(BaseModel):
    """The chain, end to end, for one property."""

    #: False when no channel manager is configured in this deployment. Every
    #: other field is then meaningless rather than merely empty, and the
    #: screen says so instead of drawing an empty chain.
    channel_manager_configured: bool
    property_linked: bool
    external_property_id: str | None = None
    rooms_mapped: int = 0
    rooms_total: int = 0
    rates_mapped: int = 0
    rates_total: int = 0
    #: Set when the channel manager could not be reached. The rows are then
    #: what we know locally, and the screen must not read absence as "no".
    unreachable: str | None = None
    rows: list[OtaRow] = []


# --------------------------------------------------------------------------
# Is that actually their hotel id?
# --------------------------------------------------------------------------


class ConnectionTest(BaseModel):
    """What the OTA said when asked about this id."""

    #: ``ok`` -- the OTA recognised it. ``rejected`` -- it did not, and this
    #: id will never work. ``unverified`` -- nobody could say, which is not
    #: the same thing and must not be treated as either.
    verdict: str
    message: str


@attribute_router.post("/channel-connections/test",
                       response_model=ConnectionTest)
def test_ota_hotel_id(
    partner_id: uuid.UUID,
    ota_hotel_id: str,
    caller: Caller = Depends(
        require_org_permission("distribution", "configure")),
    db: Session = Depends(get_session),
):
    """Ask the OTA, through the channel manager, whether it knows this hotel.

    Requiring the field only proves somebody typed something. This asks the
    OTA itself, which is the only party that can actually say -- and it says
    it *before* anything is created, so a mistyped digit is caught while the
    person who typed it is still looking at it, rather than as a silent
    failure to sell three weeks later.

    Three answers, not two. "Rejected" and "could not be checked" are
    genuinely different and collapsing them would be its own bug: Agoda has
    no test implementation at all on this deployment, so treating an
    unanswerable question as a wrong answer would make Agoda impossible to
    connect.
    """
    partner = db.execute(
        text("SELECT name, partner_type FROM engagement.booking_attributes "
             "WHERE id = :i AND organization_id = :o"),
        {"i": partner_id, "o": caller.organization_id},
    ).mappings().first()
    if partner is None:
        raise HTTPException(status_code=404, detail="No such partner.")

    hotel_id = (ota_hotel_id or "").strip()
    if not hotel_id:
        return ConnectionTest(verdict="rejected",
                              message="No property id given.")

    from .channel_provision import CHANNEL_CODES, Channex, _norm_partner

    code = CHANNEL_CODES.get(_norm_partner(partner["name"]))
    if not code:
        return ConnectionTest(
            verdict="unverified",
            message=f"{partner['name']} is not a channel the channel manager "
                    f"supports, so this id cannot be checked here.")
    if not settings.channex_api_key:
        return ConnectionTest(
            verdict="unverified",
            message="No channel manager is configured, so this id cannot be "
                    "checked.")

    try:
        with Channex() as cx:
            status, body = cx.post("/channels/test_connection",
                                   {"channel": code,
                                    "settings": {"hotel_id": hotel_id}})
    except Exception as exc:  # noqa: BLE001 - unreachable is not rejected
        log.warning("test_connection failed for %s: %s", partner["name"], exc)
        return ConnectionTest(
            verdict="unverified",
            message="The channel manager could not be reached, so this id "
                    "could not be checked.")

    if status >= 400:
        return ConnectionTest(
            verdict="unverified",
            message=f"The channel manager could not check this id "
                    f"({status}).")

    data = body.get("data") or {}
    if data.get("success"):
        return ConnectionTest(
            verdict="ok",
            message=f"{partner['name']} recognises this property id.")

    errors = data.get("errors")
    # Their word for "this channel has no test built". Not a rejection: the
    # id may be perfectly good and simply unaskable.
    if errors == "implementation_not_defined":
        # The channel manager's *API* has no check for this channel. Its own
        # admin does offer a Test Connection button for several of them, so
        # this is a gap in what is exposed to us rather than proof that the
        # channel cannot be tested at all -- worth saying accurately, because
        # the two send somebody to different places.
        #
        # Either way the answer depends on the hotel first authorising the
        # channel manager in the OTA's own extranet; before that there is no
        # relationship for anyone to query.
        return ConnectionTest(
            verdict="unverified",
            message=f"This id cannot be checked from here: the channel "
                    f"manager offers no API check for {partner['name']}. "
                    f"Authorise the channel manager in {partner['name']}'s "
                    f"extranet first, then Test Connection in the channel "
                    f"manager's own admin, or simply build the channel -- "
                    f"that proves it either way.")

    return ConnectionTest(
        verdict="rejected",
        message=f"{partner['name']} does not recognise property id "
                f"{hotel_id}. "
                + (str(errors)[:200] if errors
                   else "Check it against your contract with them."))


@attribute_router.get("/channel-links/ota-status", response_model=OtaStatus)
def ota_status(
    property_id: uuid.UUID,
    caller: Caller = Depends(require_org_permission("distribution", "view")),
    db: Session = Depends(get_session),
):
    """What is genuinely live for this property, asked rather than assumed."""
    assert_property_in_org(db, caller, property_id)
    from .channel_provision import Channex

    link = db.execute(
        text("SELECT * FROM distribution.channel_manager_links "
             "WHERE property_id = :p"),
        {"p": property_id},
    ).mappings().first()

    out = OtaStatus(
        channel_manager_configured=bool(settings.channex_api_key),
        property_linked=bool(link and link["external_property_id"]),
        external_property_id=link["external_property_id"] if link else None,
    )
    if link:
        rooms = _mappings(db, link["id"], property_id, "rooms")
        rates = _mappings(db, link["id"], property_id, "rates")
        out.rooms_total, out.rates_total = len(rooms), len(rates)
        out.rooms_mapped = sum(1 for r in rooms if r.external_id)
        out.rates_mapped = sum(1 for r in rates if r.external_id)

    partners = db.execute(
        text(
            """
            SELECT c.partner_id, a.name AS partner_name,
                   c.commission_percent, c.payment_model
            FROM distribution.channel_connections c
            JOIN engagement.booking_attributes a ON a.id = c.partner_id
            WHERE c.property_id = :p
            ORDER BY a.name
            """
        ),
        {"p": property_id},
    ).mappings().all()

    # What the channel manager has for this property. Asked once, not once per
    # partner: a hotel with four OTAs should not cost four round trips.
    channels: dict[str, dict] = {}
    if out.channel_manager_configured and out.external_property_id:
        try:
            with Channex() as cx:
                code, body = cx.get(
                    "/channels?filter%5Bproperty_id%5D="
                    + out.external_property_id)
            if code >= 400:
                out.unreachable = f"Channel manager returned {code}."
            else:
                for row in body.get("data") or []:
                    a = row.get("attributes") or {}
                    key = _norm(a.get("channel") or a.get("title") or "")
                    if key:
                        channels[key] = a
        except Exception as exc:  # noqa: BLE001 - reported, never raised
            # A screen that cannot reach the channel manager must say so. The
            # alternative is a page that reports "not connected" during an
            # outage and sends somebody to reconnect something that is fine.
            log.warning("could not list channels for %s: %s", property_id, exc)
            out.unreachable = "The channel manager could not be reached."

    for pr in partners:
        found = channels.get(_norm(pr["partner_name"]))
        settings_blob = (found or {}).get("settings") or {}
        exists = found is not None
        active = bool((found or {}).get("is_active"))
        if active:
            state, detail = "live", (
                f"Live. {pr['partner_name']} is switched on at the channel "
                f"manager and sending bookings here.")
        elif exists:
            state, detail = "configured", (
                f"A {pr['partner_name']} channel exists at the channel "
                f"manager but is not switched on. It goes live once "
                f"{pr['partner_name']} authorises it in their extranet.")
        elif out.unreachable:
            state, detail = "unknown", (
                "Could not check the channel manager just now.")
        else:
            state, detail = "terms_only", (
                f"Terms recorded only. No {pr['partner_name']} channel exists "
                f"at the channel manager yet, so nothing is being sold "
                f"through {pr['partner_name']}.")
        out.rows.append(OtaRow(
            partner_id=pr["partner_id"], partner_name=pr["partner_name"],
            commission_percent=pr["commission_percent"],
            payment_model=pr["payment_model"],
            channel_exists=exists, channel_active=active,
            channel_title=(found or {}).get("title"),
            hotel_id=settings_blob.get("hotel_id"),
            state=state, detail=detail,
        ))
    return out


@attribute_router.get("/channel-links", response_model=LinkOut | None)
def get_link(
    property_id: uuid.UUID,
    caller: Caller = Depends(require_org_permission("distribution", "view")),
    db: Session = Depends(get_session),
):
    """This property's channel manager link, or null if it has none."""
    assert_property_in_org(db, caller, property_id)
    row = db.execute(
        text("SELECT * FROM distribution.channel_manager_links "
             "WHERE property_id = :p"),
        {"p": property_id},
    ).mappings().first()
    return _present_link(db, row) if row else None


@attribute_router.put("/channel-links", response_model=LinkOut)
def upsert_link(
    body: LinkIn,
    caller: Caller = Depends(
        require_org_permission("distribution", "configure")),
    db: Session = Depends(get_session),
):
    """Create or update this property's link to the channel manager.

    Upsert rather than create-then-update: there is exactly one per property,
    so asking a caller to know whether it exists yet is asking them to track
    something the database already knows.
    """
    assert_property_in_org(db, caller, body.property_id)
    try:
        db.execute(
            text(
                """
                INSERT INTO distribution.channel_manager_links
                    (organization_id, property_id, provider,
                     external_property_id, currency, updated_by)
                VALUES (:o, :p, 'channex', :ext, :cur, :by)
                ON CONFLICT (property_id) DO UPDATE
                   SET external_property_id = EXCLUDED.external_property_id,
                       currency = EXCLUDED.currency,
                       updated_at = now(), updated_by = EXCLUDED.updated_by
                """
            ),
            {"o": caller.organization_id, "p": body.property_id,
             "ext": (body.external_property_id or "").strip() or None,
             "cur": (body.currency or "").strip().upper() or None,
             "by": caller.user_id},
        )
    except IntegrityError:
        # The other uniqueness rule: one channel manager property belongs to
        # one hotel. Two claiming it would route bookings by chance.
        raise HTTPException(
            status_code=409,
            detail="Another property is already linked to that channel "
                   "manager property id.",
        ) from None

    row = db.execute(
        text("SELECT * FROM distribution.channel_manager_links "
             "WHERE property_id = :p"),
        {"p": body.property_id},
    ).mappings().first()
    record_audit(
        db, action="channel_link.set", entity_type="channel_manager_link",
        entity_id=str(row["id"]), organization_id=caller.organization_id,
        property_id=body.property_id, actor_subject=caller.subject,
        after={"external_property_id": body.external_property_id},
    )
    return _present_link(db, row)


# --------------------------------------------------------------------------
# Everything one screen needs to edit a partner's mappings
# --------------------------------------------------------------------------
#
# Assembled here rather than left to the browser to stitch together from four
# calls. The screen has to show our room types, the rate plan under each one,
# what the channel manager calls both, and every counterpart still available
# to choose -- and it must show them consistently. Fetched separately they
# arrive at different moments, and a half-drawn mapping tree is exactly where
# somebody pairs the wrong two.
#
# The rate plans nest under their room type because that is what they are: a
# channel rate plan belongs to exactly one room, which is the rule
# provisioning already enforces when it creates them. A flat list of plans
# beside a flat list of rooms would hide the one relationship that matters.


class Candidate(BaseModel):
    """One thing at the channel manager that could be paired with ours."""

    id: str
    title: str
    #: For a rate plan, the channel manager's room type it belongs to. Lets
    #: the screen offer only the plans that sit under the room already
    #: chosen, instead of every plan in the property.
    room_type_id: str | None = None


class RateRow(BaseModel):
    local_id: uuid.UUID
    local_code: str
    local_name: str
    external_id: str = ""
    external_name: str | None = None


class RoomRow(RateRow):
    rates: list[RateRow] = []


class MappingEditor(BaseModel):
    link_id: uuid.UUID
    property_id: uuid.UUID
    property_name: str
    currency: str | None = None
    timezone: str | None = None
    external_property_id: str | None = None
    rooms: list[RoomRow] = []
    rooms_mapped: int = 0
    rooms_total: int = 0
    rates_mapped: int = 0
    rates_total: int = 0
    last_pushed_at: datetime | None = None
    last_push_status: str | None = None
    last_push_detail: str | None = None
    #: What this property sends, and whether anyone is told when it fails.
    #: On the property rather than on one OTA because that is the grain the
    #: push actually works at -- one feed goes to the channel manager and it
    #: fans out to every channel.
    send_availability: bool = True
    send_rates: bool = True
    send_restrictions: bool = True
    notify_on_failure: bool = True
    #: Whether the channel manager is pointed back at us for this property.
    #: Separate from whether any OTA is live: the delivery path can be
    #: perfectly ready with nothing yet sending down it, and calling that
    #: "not connected" is how a screen contradicts the one beside it.
    webhook_registered: bool = False
    #: What is available to pair with. Empty when the channel manager could
    #: not be reached, which the screen must say rather than presenting an
    #: empty dropdown as "nothing to choose".
    candidate_rooms: list[Candidate] = []
    candidate_rates: list[Candidate] = []
    unreachable: str | None = None


@attribute_router.get("/channel-links/{link_id}/mapping-editor",
                      response_model=MappingEditor)
def mapping_editor(
    link_id: uuid.UUID,
    caller: Caller = Depends(require_org_permission("distribution", "view")),
    db: Session = Depends(get_session),
):
    """Our rooms and rates, theirs, and which are paired."""
    link = db.execute(
        text("SELECT * FROM distribution.channel_manager_links "
             "WHERE id = :i AND organization_id = :o"),
        {"i": link_id, "o": caller.organization_id},
    ).mappings().first()
    if link is None:
        raise HTTPException(status_code=404, detail="No such connection.")
    assert_property_in_org(db, caller, link["property_id"])

    prop = db.execute(
        text("SELECT name, currency, timezone FROM iam.properties "
             "WHERE id = :p"),
        {"p": link["property_id"]},
    ).mappings().first()

    rooms = _mappings(db, link_id, link["property_id"], "rooms")
    rates = _mappings(db, link_id, link["property_id"], "rates")

    # Which room type each of our rate plans covers, so the tree can nest.
    # Only plans covering exactly one room type can be nested, and only those
    # can be sold on a channel anyway -- the rest are deliberately left at the
    # bottom rather than hidden, because "my plan is missing" is worse than
    # seeing it and being told why.
    owner = dict(db.execute(
        text(
            """
            SELECT x.rate_plan_id, min(x.room_type_id::text) AS room_type_id
            FROM property.rate_plan_room_types x
            WHERE x.property_id = :p
            GROUP BY x.rate_plan_id
            HAVING count(*) = 1
            """
        ),
        {"p": link["property_id"]},
    ).all())

    by_room: dict[str, list[RateRow]] = {}
    orphan: list[RateRow] = []
    for r in rates:
        row = RateRow(local_id=r.local_id, local_code=r.local_code,
                      local_name=r.local_name, external_id=r.external_id,
                      external_name=r.external_name)
        rt = owner.get(r.local_id)
        if rt:
            by_room.setdefault(str(rt), []).append(row)
        else:
            orphan.append(row)

    tree = [
        RoomRow(local_id=r.local_id, local_code=r.local_code,
                local_name=r.local_name, external_id=r.external_id,
                external_name=r.external_name,
                rates=by_room.get(str(r.local_id), []))
        for r in rooms
    ]
    if orphan:
        # A visible home for plans that span several room types. They cannot
        # be mapped, and saying so beats them vanishing.
        tree.append(RoomRow(
            local_id=uuid.UUID(int=0), local_code="",
            local_name="Not sellable on a channel", rates=orphan))

    out = MappingEditor(
        link_id=link_id, property_id=link["property_id"],
        property_name=prop["name"] if prop else "",
        currency=prop["currency"] if prop else None,
        timezone=prop["timezone"] if prop else None,
        external_property_id=link["external_property_id"],
        rooms=tree,
        rooms_mapped=sum(1 for r in rooms if r.external_id),
        rooms_total=len(rooms),
        rates_mapped=sum(1 for r in rates if r.external_id),
        rates_total=len(rates),
        last_pushed_at=link["last_pushed_at"],
        last_push_status=link["last_push_status"],
        last_push_detail=link["last_push_detail"],
        send_availability=link["send_availability"],
        send_rates=link["send_rates"],
        send_restrictions=link["send_restrictions"],
        notify_on_failure=link["notify_on_failure"],
    )

    if link["external_property_id"] and settings.channex_api_key:
        from .channel_provision import Channex
        try:
            with Channex() as cx:
                ext = link["external_property_id"]
                code, body = cx.get(
                    f"/room_types?filter%5Bproperty_id%5D={ext}")
                if code < 400:
                    out.candidate_rooms = [
                        Candidate(id=r["attributes"]["id"],
                                  title=r["attributes"].get("title") or "")
                        for r in body.get("data") or []
                    ]
                code, body = cx.get(f"/webhooks?filter%5Bproperty_id%5D={ext}")
                if code < 400:
                    out.webhook_registered = any(
                        (r.get("attributes") or {}).get("is_active")
                        for r in body.get("data") or [])
                code, body = cx.get(
                    f"/rate_plans?filter%5Bproperty_id%5D={ext}")
                if code < 400:
                    out.candidate_rates = [
                        Candidate(
                            id=r["attributes"]["id"],
                            title=r["attributes"].get("title") or "",
                            room_type_id=((r.get("relationships") or {})
                                          .get("room_type") or {})
                            .get("data", {}).get("id"),
                        )
                        for r in body.get("data") or []
                    ]
        except Exception as exc:  # noqa: BLE001 - reported, never raised
            log.warning("could not list candidates for %s: %s", link_id, exc)
            out.unreachable = (
                "The channel manager could not be reached, so the list of "
                "things to pair with could not be loaded.")
    return out


class SyncPrefsIn(BaseModel):
    send_availability: bool
    send_rates: bool
    send_restrictions: bool
    notify_on_failure: bool


@attribute_router.put("/channel-links/{link_id}/sync-settings",
                      response_model=MappingEditor)
def set_sync_settings(
    link_id: uuid.UUID,
    body: SyncPrefsIn,
    caller: Caller = Depends(
        require_org_permission("distribution", "configure")),
    db: Session = Depends(get_session),
):
    """What this property sends to the channel manager.

    Property-wide, and the screen says so. The alternative -- a switch per
    OTA -- would read better and mean nothing: one feed leaves here and the
    channel manager fans it out, so there is no point at which availability
    could be withheld from one channel and not another.

    Switching sending off does **not** close the rooms already at the channel
    manager. Whatever it holds is what the OTAs keep selling, at the price
    they last heard. Stopping the feed freezes it; it does not withdraw it.
    """
    link = db.execute(
        text("SELECT property_id FROM distribution.channel_manager_links "
             "WHERE id = :i AND organization_id = :o"),
        {"i": link_id, "o": caller.organization_id},
    ).mappings().first()
    if link is None:
        raise HTTPException(status_code=404, detail="No such connection.")
    assert_property_in_org(db, caller, link["property_id"])

    before = db.execute(
        text("SELECT send_availability, send_rates, send_restrictions, "
             "notify_on_failure "
             "FROM distribution.channel_manager_links WHERE id = :i"),
        {"i": link_id},
    ).mappings().first()

    db.execute(
        text("UPDATE distribution.channel_manager_links "
             "SET send_availability = :a, send_rates = :r, "
             "    send_restrictions = :x, notify_on_failure = :n, "
             "    updated_at = now(), updated_by = :by "
             "WHERE id = :i"),
        {"a": body.send_availability, "r": body.send_rates,
         "x": body.send_restrictions, "n": body.notify_on_failure,
         "by": caller.user_id, "i": link_id},
    )
    # Audited: switching the feed off is how a property silently stops
    # updating every OTA it sells on, and somebody will eventually need to
    # know who did it and when.
    record_audit(
        db, action="channel_link.sync_settings", entity_type="channel_manager_link",
        entity_id=str(link_id), organization_id=caller.organization_id,
        property_id=link["property_id"], actor_subject=caller.subject,
        before=dict(before) if before else None,
        after={"send_availability": body.send_availability,
               "send_rates": body.send_rates,
               "send_restrictions": body.send_restrictions,
               "notify_on_failure": body.notify_on_failure},
    )
    return mapping_editor(link_id, caller, db)


@attribute_router.put("/channel-links/{link_id}/mappings",
                      response_model=LinkOut)
def set_link_mappings(
    link_id: uuid.UUID,
    body: MappingsIn,
    caller: Caller = Depends(
        require_org_permission("distribution", "configure")),
    db: Session = Depends(get_session),
):
    """What the channel manager calls our rooms and rate plans.

    One set per property, shared by every OTA connected to it. The aggregator
    normalises the ids, so a booking from Agoda and one from Booking.com both
    arrive carrying the same room id — mapping per OTA would mean maintaining
    the same pairs several times and watching them drift.
    """
    row = _link_or_404(db, caller, link_id)
    _write_mappings(db, link_id, body)
    record_audit(
        db, action="channel_link.mapped", entity_type="channel_manager_link",
        entity_id=str(link_id), organization_id=caller.organization_id,
        property_id=row["property_id"], actor_subject=caller.subject,
        after={"rooms": len(body.rooms or []), "rates": len(body.rates or [])},
    )
    return _present_link(db, _link_or_404(db, caller, link_id))


@attribute_router.post("/channel-links/{link_id}/push",
                       response_model=PushResult)
def push_link(
    link_id: uuid.UUID,
    caller: Caller = Depends(
        require_org_permission("distribution", "configure")),
    db: Session = Depends(get_session),
):
    """Full sync: send every rate, availability and stay rule, now.

    500 days for every mapped room and rate plan, whatever was sent before --
    for go-live, and for recovering from anything that may have left the
    channel manager out of step. Day-to-day changes need no button: they go
    out on their own within a sync interval.
    """
    _link_or_404(db, caller, link_id)
    from .channel_sync import sync
    res = sync(db, link_id, full=True)
    record_audit(
        db, action="channel_link.full_sync", entity_type="channel_manager_link",
        entity_id=str(link_id), organization_id=caller.organization_id,
        actor_subject=caller.subject, after={"status": res["status"]},
    )
    return PushResult(status=res["status"], detail=res["detail"])


class SyncLogRow(BaseModel):
    id: uuid.UUID
    created_at: datetime
    endpoint: str
    trigger: str
    outcome: str
    status_code: int | None = None
    value_count: int
    date_from: date | None = None
    date_to: date | None = None
    task_ids: list[str] = []
    summary: str | None = None
    error: str | None = None
    request_excerpt: str | None = None


@attribute_router.get("/channel-links/{link_id}/sync-log",
                      response_model=list[SyncLogRow])
def sync_log(
    link_id: uuid.UUID,
    limit: int = Query(50, ge=1, le=500),
    caller: Caller = Depends(require_org_permission("distribution", "view")),
    db: Session = Depends(get_session),
):
    """Every request sent to the channel manager for this property, newest
    first, with the task ids it returned -- the receipt for each update."""
    _link_or_404(db, caller, link_id)
    rows = db.execute(
        text("SELECT id, created_at, endpoint, trigger, outcome, status_code, "
             "value_count, date_from, date_to, task_ids, summary, error, "
             "request_excerpt FROM distribution.channel_sync_log "
             "WHERE link_id = :l ORDER BY created_at DESC LIMIT :n"),
        {"l": link_id, "n": limit},
    ).mappings().all()
    return [SyncLogRow(**r) for r in rows]


class ProvisionResult(BaseModel):
    """What setting this property up at the channel manager actually did."""

    status: str
    external_property_id: str | None = None
    created_property: bool = False
    rooms_created: int = 0
    rooms_mapped: int = 0
    rates_created: int = 0
    rates_mapped: int = 0
    webhook_registered: bool = False
    #: OTA channels built this run, and how many OTAs are still waiting for
    #: the hotel's own id from that OTA.
    channels_created: int = 0
    channels_pending: int = 0
    #: Named rather than counted. "Two rooms failed" tells somebody there is a
    #: problem; "Sea View Suite: no rate plan covers this room type" tells them
    #: what to do about it.
    problems: list[str] = []


@attribute_router.post("/channel-links/provision",
                       response_model=ProvisionResult)
def provision_channel(
    property_id: uuid.UUID,
    require_intent: bool = False,
    caller: Caller = Depends(
        require_org_permission("distribution", "configure")),
    db: Session = Depends(get_session),
):
    """Set this property up at the channel manager, end to end.

    Creates the property there, mirrors every active room type and gives each
    one a rate plan, registers the booking webhook, and writes the mappings —
    so nobody has to copy identifiers between two browser tabs and nobody can
    pair the wrong two.

    Safe to run again. Every step asks what already exists and creates only
    what is missing, so this is also how a property catches up after a room
    type is added.

    ``require_intent`` makes this decline unless the property has connected an
    OTA or is already linked. Automatic callers pass it — going live fires for
    every property, and most hotels never sell through a channel, so creating
    each of them at the channel manager regardless is a bill for listings
    nobody looks at. A person calling this has already supplied the intent by
    asking, so the default is off.
    """
    assert_property_in_org(db, caller, property_id)
    from .channel_provision import provision

    res = provision(db, property_id, caller.organization_id, caller.user_id,
                    require_intent=require_intent)
    if res.status == "skipped":
        # Nothing happened and nothing is wrong. Auditing it would put a row
        # against every property that goes live without selling on an OTA,
        # which is most of them.
        return ProvisionResult(status=res.status)
    record_audit(
        db, action="channel_link.provisioned",
        entity_type="channel_manager_link",
        entity_id=str(res.external_property_id or ""),
        organization_id=caller.organization_id, property_id=property_id,
        actor_subject=caller.subject,
        after={"status": res.status, "rooms": res.rooms_mapped,
               "rates": res.rates_mapped,
               "webhook": res.webhook_registered},
    )
    return ProvisionResult(
        status=res.status, external_property_id=res.external_property_id,
        created_property=res.created_property,
        rooms_created=res.rooms_created, rooms_mapped=res.rooms_mapped,
        rates_created=res.rates_created, rates_mapped=res.rates_mapped,
        webhook_registered=res.webhook_registered,
        channels_created=res.channels_created,
        channels_pending=res.channels_pending, problems=res.problems,
    )
