"""Guest Profile (screen 009).

One guest, everything known about them, and a clear line between the two
kinds of "known".

**Derived, and therefore always right.** Total stays, lifetime value, average
stay length, the current reservation, the next booking, the activity timeline.
None of it is stored on the guest; all of it is computed from stays and
folios, the same way the directory (screen 010) computes its columns. That is
deliberate: a "total stays" counter maintained by hand is a number that goes
wrong the first time somebody cancels a reservation, and then stays wrong.

**Recorded, because nobody can work it out.** Preferences, tags and internal
notes. These arrived with migration 0044 -- until then the directory returned
an empty preferences column and said in its own docstring that the table did
not exist.

Two panels of the mockup are absent rather than empty, and this is the place
to say why:

* **Average rating.** Nothing in this system collects guest feedback. A 4.8
  beside a guest's face is a number somebody would eventually quote to that
  guest.
* **Communications.** The mockup's timeline shows WhatsApp messages and emails
  sent. Confirmation mail is sent (``guest_mail``) but nothing records that it
  was, and no channel sends WhatsApp at all. The timeline here carries only
  events this system can prove happened.

The activity timeline is assembled from six real sources -- reservations
created, check-ins, check-outs, room moves, documents uploaded and notes
added -- merged and sorted in SQL rather than in Python, so limiting it does
not mean reading everything first.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal

from chirala_common.authz import Caller, build_authz, assert_org_matches_caller
from chirala_common.routing import TransactionalRoute
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.orm import Session

from .database import get_session
from .settings import settings

_get_caller, _require_permission, require_org_permission = build_authz(
    get_session, lambda: settings.environment,
    lambda: settings.service_token,
)

guestprofile_router = APIRouter(tags=["guests"], route_class=TransactionalRoute)

#: How many timeline entries the Overview tab shows.
ACTIVITY_LIMIT = 8


# --------------------------------------------------------------- schemas ---

class Stat(BaseModel):
    total_stays: int
    lifetime_value: Decimal
    nights_total: int
    average_nights: float
    cancelled: int


class StayRow(BaseModel):
    reservation_id: uuid.UUID
    unit_id: uuid.UUID
    number: str
    property_name: str | None
    room_type: str | None
    room: str | None
    arrival_date: date
    departure_date: date
    nights: int
    adults: int
    children: int
    status: str
    source: str | None
    charges: Decimal
    paid: Decimal


class ActivityRow(BaseModel):
    at: datetime
    kind: str
    title: str
    detail: str | None
    reservation_number: str | None


class PreferenceRow(BaseModel):
    id: uuid.UUID
    kind: str
    label: str


class NoteRow(BaseModel):
    id: uuid.UUID
    body: str
    created_at: datetime
    author: str | None


class ProfileOut(BaseModel):
    id: uuid.UUID
    #: G-1001. Allocated by a trigger, so every path that creates a guest has
    #: one; nullable only for the moment between migration and backfill.
    reference: str | None
    full_name: str
    title: str | None
    email: str | None
    phone: str | None
    city: str | None
    state: str | None
    country: str | None
    nationality: str | None
    address_line: str | None
    occupation: str | None
    date_of_birth: date | None
    id_type: str | None
    id_verified: bool
    member_since: date
    initials: str
    in_house: bool
    stats: Stat
    last_stay: date | None
    next_stay: date | None
    current: StayRow | None
    upcoming: StayRow | None
    stays: list[StayRow]
    activity: list[ActivityRow]
    preferences: list[PreferenceRow]
    tags: list[str]
    notes: list[NoteRow]
    documents: int
    can_edit: bool
    can_book: bool
    #: Panels of the mockup with no data source. Sent so the UI can say what is
    #: missing and why, rather than drawing an empty card that looks broken.
    unavailable: dict[str, str]


# ------------------------------------------------------------------- SQL ---

# Charges and payments per reservation, on the same definition the folio and
# the directory use: charges are what was billed net of adjustment, payments
# are how it was settled.
_MONEY_SQL = """
    SELECT f.reservation_id,
           COALESCE(sum(CASE WHEN e.entry_type = 'debit' THEN e.amount
                             ELSE -e.amount END)
                    FILTER (WHERE COALESCE(e.source_type, '')
                            NOT IN ('payment', 'refund', 'security_deposit')),
                    0) AS charges,
           COALESCE(sum(CASE WHEN e.entry_type = 'credit' THEN e.amount
                             ELSE -e.amount END)
                    FILTER (WHERE COALESCE(e.source_type, '') = 'payment'),
                    0) AS paid
    FROM finance.folios f
    JOIN finance.folio_entries e ON e.folio_id = f.id
    GROUP BY f.reservation_id
"""

_STAYS_SQL = f"""
    SELECT r.id AS reservation_id, ru.id AS unit_id, r.number,
           p.name AS property_name, rt.name AS room_type, rm.code AS room,
           ru.arrival_date, ru.departure_date,
           (ru.departure_date - ru.arrival_date) AS nights,
           ru.adults, ru.children, ru.status, r.source,
           COALESCE(m.charges, 0) AS charges,
           COALESCE(m.paid, 0) AS paid
    FROM booking.reservations r
    JOIN booking.reservation_units ru ON ru.reservation_id = r.id
    LEFT JOIN iam.properties p ON p.id = ru.property_id
    LEFT JOIN property.room_types rt ON rt.id = ru.room_type_id
    LEFT JOIN property.rooms rm ON rm.id = ru.assigned_room_id
    LEFT JOIN ({_MONEY_SQL}) m ON m.reservation_id = r.id
    WHERE r.organization_id = :org AND r.primary_guest_id = :guest
    ORDER BY ru.arrival_date DESC, ru.line_index
"""

# Six sources, merged in SQL. Every row is something that demonstrably
# happened: the row exists because the event occurred, not because somebody
# remembered to write a log line beside it.
_ACTIVITY_SQL = """
    WITH mine AS (
        SELECT r.id, r.number
        FROM booking.reservations r
        WHERE r.organization_id = :org AND r.primary_guest_id = :guest
    ),
    ev AS (
        SELECT r.created_at AS at, 'booking' AS kind,
               'New booking' AS title,
               'Reservation ' || m.number || ' created' AS detail,
               m.number AS reservation_number
        FROM booking.reservations r JOIN mine m ON m.id = r.id

        UNION ALL
        SELECT ci.checked_in_at, 'checkin', 'Checked in',
               COALESCE(rm.code, 'Room not assigned')
                 || COALESCE(' / ' || rt.name, ''),
               m.number
        FROM booking.stay_checkins ci
        JOIN booking.reservation_units ru ON ru.id = ci.reservation_unit_id
        JOIN mine m ON m.id = ru.reservation_id
        LEFT JOIN property.rooms rm ON rm.id = ci.room_id
        LEFT JOIN property.room_types rt ON rt.id = ru.room_type_id

        UNION ALL
        SELECT co.checked_out_at, 'checkout', 'Checked out',
               'Balance at checkout '
                 || to_char(co.balance_at_checkout, 'FM999G999G990D00'),
               m.number
        FROM booking.stay_checkouts co
        JOIN booking.reservation_units ru ON ru.id = co.reservation_unit_id
        JOIN mine m ON m.id = ru.reservation_id

        UNION ALL
        SELECT mv.created_at, 'move', 'Room moved',
               COALESCE(fr.code, '?') || ' to ' || COALESCE(tr.code, '?')
                 || COALESCE(' / ' || mv.reason, ''),
               m.number
        FROM booking.room_moves mv
        JOIN booking.reservation_units ru ON ru.id = mv.reservation_unit_id
        JOIN mine m ON m.id = ru.reservation_id
        LEFT JOIN property.rooms fr ON fr.id = mv.from_room_id
        LEFT JOIN property.rooms tr ON tr.id = mv.to_room_id

        UNION ALL
        SELECT d.created_at, 'document', 'Document uploaded',
               COALESCE(d.original_name, d.kind), NULL
        FROM engagement.guest_documents d
        WHERE d.organization_id = :org AND d.guest_id = :guest

        UNION ALL
        SELECT n.created_at, 'note', 'Note added', n.body, NULL
        FROM engagement.guest_notes n
        WHERE n.organization_id = :org AND n.guest_id = :guest
    )
    SELECT * FROM ev WHERE at IS NOT NULL ORDER BY at DESC LIMIT :lim
"""

_PREFS_SQL = """
    SELECT id, kind, label FROM engagement.guest_preferences
    WHERE organization_id = :org AND guest_id = :guest
    ORDER BY position, created_at
"""

_TAGS_SQL = """
    SELECT tag FROM engagement.guest_tags
    WHERE organization_id = :org AND guest_id = :guest
    ORDER BY created_at
"""


def _initials(name: str) -> str:
    """Two letters for the avatar.

    There is no guest photograph anywhere in this system and no screen that
    captures one, so the mockup's portrait cannot be honoured. A monogram says
    more than an empty circle and does not pretend to be a photo.
    """
    parts = [p for p in name.replace(",", " ").split() if p]
    if not parts:
        return "?"
    if len(parts) == 1:
        return parts[0][:2].upper()
    return (parts[0][0] + parts[-1][0]).upper()


def _stay_row(r) -> StayRow:
    return StayRow(**{k: r[k] for k in StayRow.model_fields})


def _assert_guest(db: Session, guest_id: uuid.UUID, org: uuid.UUID) -> None:
    """Exists, and belongs to this organisation.

    Scoped in the same query as the lookup rather than checked afterwards: a
    guest of another tenant has to be indistinguishable from one that does not
    exist, or the 404 becomes a way to enumerate them.
    """
    ok = db.execute(
        text("SELECT 1 FROM engagement.guests WHERE id = :id "
             "AND organization_id = :org"),
        {"id": guest_id, "org": org},
    ).first()
    if ok is None:
        raise HTTPException(404, "Guest not found")


def _tags(db: Session, guest_id: uuid.UUID, org: uuid.UUID) -> list[str]:
    return [r[0] for r in db.execute(
        text(_TAGS_SQL), {"org": org, "guest": guest_id})]


def _prefs(db: Session, guest_id: uuid.UUID, org: uuid.UUID) -> list[PreferenceRow]:
    return [PreferenceRow(**r) for r in db.execute(
        text(_PREFS_SQL), {"org": org, "guest": guest_id}).mappings()]


# ---------------------------------------------------------------- routes ---

@guestprofile_router.get("/guests/{guest_id}/profile", response_model=ProfileOut)
def profile(
    guest_id: uuid.UUID,
    organization_id: uuid.UUID,
    caller: Caller = Depends(require_org_permission("guests", "view")),
    db: Session = Depends(get_session),
):
    """Everything screen 009 shows, in one round trip.

    One request rather than eight, because every panel is about the same guest
    and a screen that paints itself in eight stages is one the desk watches
    assemble.
    """
    assert_org_matches_caller(caller, organization_id)
    from chirala_common.authz import _GRANT_SQL

    g = db.execute(
        text("""
            SELECT id, reference, full_name, title, email, phone, city,
                   state, country, nationality, address_line, occupation,
                   date_of_birth, id_type, id_verified_at, created_at
            FROM engagement.guests
            WHERE id = :id AND organization_id = :org
        """),
        {"id": guest_id, "org": organization_id},
    ).mappings().first()
    if g is None:
        raise HTTPException(404, "Guest not found")

    params = {"org": organization_id, "guest": guest_id}
    stays = [_stay_row(r) for r in db.execute(text(_STAYS_SQL), params).mappings()]

    # A stay counts once it has begun. Confirmed-but-not-arrived is a plan, and
    # counting it would make "8 stays" a number that can go down.
    stayed = [s for s in stays if s.status in ("checked_in", "checked_out")]
    nights = sum(s.nights for s in stayed)
    stats = Stat(
        total_stays=len(stayed),
        lifetime_value=sum((s.charges for s in stayed), Decimal(0)),
        nights_total=nights,
        average_nights=round(nights / len(stayed), 1) if stayed else 0.0,
        cancelled=sum(1 for s in stays if s.status == "cancelled"),
    )

    current = next((s for s in stays if s.status == "checked_in"), None)
    today = date.today()
    # The soonest arrival still ahead, not merely the newest booking: a guest
    # who books July and then June should be shown June.
    upcoming = min(
        (s for s in stays
         if s.status in ("confirmed", "tentative") and s.arrival_date >= today),
        key=lambda s: s.arrival_date, default=None)

    activity = [ActivityRow(**r) for r in db.execute(
        text(_ACTIVITY_SQL), {**params, "lim": ACTIVITY_LIMIT}).mappings()]

    notes = [NoteRow(**r) for r in db.execute(
        text("""
            SELECT n.id, n.body, n.created_at, u.display_name AS author
            FROM engagement.guest_notes n
            LEFT JOIN iam.users u ON u.id = n.created_by
            WHERE n.organization_id = :org AND n.guest_id = :guest
            ORDER BY n.created_at DESC
        """), params).mappings()]

    documents = db.execute(
        text("SELECT count(*) FROM engagement.guest_documents "
             "WHERE organization_id = :org AND guest_id = :guest"), params,
    ).scalar_one()

    def may(action: str) -> bool:
        if caller.is_service:
            return True
        return db.execute(
            text(_GRANT_SQL),
            {"uid": caller.user_id, "res": "guests", "act": action, "prop": None},
        ).first() is not None

    return ProfileOut(
        id=g["id"], reference=g["reference"],
        full_name=g["full_name"], title=g["title"],
        email=g["email"], phone=g["phone"], city=g["city"], state=g["state"],
        country=g["country"], nationality=g["nationality"],
        address_line=g["address_line"], occupation=g["occupation"],
        date_of_birth=g["date_of_birth"], id_type=g["id_type"],
        id_verified=g["id_verified_at"] is not None,
        member_since=g["created_at"].date(),
        initials=_initials(g["full_name"]),
        in_house=current is not None,
        stats=stats,
        last_stay=max((s.arrival_date for s in stayed), default=None),
        next_stay=upcoming.arrival_date if upcoming else None,
        current=current, upcoming=upcoming, stays=stays,
        activity=activity,
        preferences=_prefs(db, guest_id, organization_id),
        tags=_tags(db, guest_id, organization_id),
        notes=notes, documents=documents,
        can_edit=may("edit"), can_book=may("create"),
        unavailable={
            "rating": "No guest feedback is collected anywhere in this system, "
                      "so there is no rating to average.",
            "communications": "Confirmation mail is sent, but nothing records "
                              "that it was, and no channel sends WhatsApp. A "
                              "log would have to be written before this tab "
                              "could show anything.",
        },
    )


class NoteIn(BaseModel):
    body: str = Field(min_length=1, max_length=4000)


@guestprofile_router.post("/guests/{guest_id}/notes", response_model=NoteRow,
                          status_code=201)
def add_note(
    guest_id: uuid.UUID,
    organization_id: uuid.UUID,
    body: NoteIn,
    caller: Caller = Depends(require_org_permission("guests", "edit")),
    db: Session = Depends(get_session),
):
    """Append a note.

    There is no edit and no delete, on purpose. "Prefers quiet rooms,
    celebrating a birthday" is worth having only if you can see who said so
    and when, and a note somebody can quietly rewrite afterwards is evidence
    of nothing.
    """
    assert_org_matches_caller(caller, organization_id)
    _assert_guest(db, guest_id, organization_id)
    row = db.execute(
        text("""
            INSERT INTO engagement.guest_notes
                (organization_id, guest_id, body, created_by)
            VALUES (:org, :guest, :body, :by)
            RETURNING id, body, created_at
        """),
        {"org": organization_id, "guest": guest_id,
         "body": body.body.strip(), "by": caller.user_id},
    ).mappings().one()
    author = db.execute(
        text("SELECT display_name FROM iam.users WHERE id = :id"),
        {"id": caller.user_id},
    ).scalar()
    return NoteRow(**row, author=author)


class TagIn(BaseModel):
    tag: str = Field(min_length=1, max_length=40)


@guestprofile_router.post("/guests/{guest_id}/tags", response_model=list[str])
def add_tag(
    guest_id: uuid.UUID,
    organization_id: uuid.UUID,
    body: TagIn,
    caller: Caller = Depends(require_org_permission("guests", "edit")),
    db: Session = Depends(get_session),
):
    assert_org_matches_caller(caller, organization_id)
    _assert_guest(db, guest_id, organization_id)
    # ON CONFLICT rather than check-then-insert: two people tagging the same
    # guest at the same moment should end with one tag, not an error on one of
    # their screens.
    db.execute(
        text("""
            INSERT INTO engagement.guest_tags
                (organization_id, guest_id, tag, created_by)
            VALUES (:org, :guest, :tag, :by)
            ON CONFLICT (guest_id, tag) DO NOTHING
        """),
        {"org": organization_id, "guest": guest_id,
         "tag": body.tag.strip(), "by": caller.user_id},
    )
    return _tags(db, guest_id, organization_id)


@guestprofile_router.delete("/guests/{guest_id}/tags", response_model=list[str])
def remove_tag(
    guest_id: uuid.UUID,
    organization_id: uuid.UUID,
    tag: str = Query(...),
    caller: Caller = Depends(require_org_permission("guests", "edit")),
    db: Session = Depends(get_session),
):
    assert_org_matches_caller(caller, organization_id)
    _assert_guest(db, guest_id, organization_id)
    db.execute(
        text("DELETE FROM engagement.guest_tags WHERE organization_id = :org "
             "AND guest_id = :guest AND tag = :tag"),
        {"org": organization_id, "guest": guest_id, "tag": tag},
    )
    return _tags(db, guest_id, organization_id)


class PreferenceIn(BaseModel):
    kind: str = Field(default="other", max_length=30)
    label: str = Field(min_length=1, max_length=120)


class PreferencesIn(BaseModel):
    items: list[PreferenceIn]


@guestprofile_router.put("/guests/{guest_id}/preferences",
                         response_model=list[PreferenceRow])
def set_preferences(
    guest_id: uuid.UUID,
    organization_id: uuid.UUID,
    body: PreferencesIn,
    caller: Caller = Depends(require_org_permission("guests", "edit")),
    db: Session = Depends(get_session),
):
    """Replace the whole list.

    The editor is a list somebody adds to, retypes and removes from, so a PUT
    of the finished list is what it actually produces. Patching row by row
    would mean the UI inventing ids for lines the user has only just typed.
    """
    assert_org_matches_caller(caller, organization_id)
    _assert_guest(db, guest_id, organization_id)
    db.execute(
        text("DELETE FROM engagement.guest_preferences "
             "WHERE organization_id = :org AND guest_id = :guest"),
        {"org": organization_id, "guest": guest_id},
    )
    for i, item in enumerate(body.items):
        label = item.label.strip()
        if not label:
            continue
        db.execute(
            text("""
                INSERT INTO engagement.guest_preferences
                    (organization_id, guest_id, kind, label, position,
                     created_by)
                VALUES (:org, :guest, :kind, :label, :pos, :by)
            """),
            {"org": organization_id, "guest": guest_id,
             "kind": item.kind or "other", "label": label, "pos": i,
             "by": caller.user_id},
        )
    return _prefs(db, guest_id, organization_id)
