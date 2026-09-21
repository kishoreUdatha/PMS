"""Form C — reporting foreign guests to the Bureau of Immigration.

Rule 14 of the Registration of Foreigners Rules 1992, under the Foreigners
Act 1946: every hotel, guest house, lodge and homestay in India reports the
arrival of a foreign national within 24 hours of check-in, on the FRRO
portal. Not filing is an offence, and this is the record an immigration or
police inspection asks for.

It is a central obligation, not a state one, so one implementation serves
every property on this platform -- unlike tax, which has to be told which
state it is in.

Three things this module is careful about.

**A guest's nationality decides whether a form exists at all.** An Indian
passport holder needs none, and creating an empty row for one would put them
on a compliance register they do not belong on. ``due`` therefore lists
foreign nationals only, and the row is created when somebody opens the form,
not when the guest checks in.

**The form is seeded from what is known and then stops guessing.** Name,
nationality, date of birth, address and purpose of visit are copied from the
guest and the stay. Visa details are left blank, because nothing in this
system has ever collected them and inventing a plausible visa number would
be worse than an empty field on a legal document.

**Arrival in India is not arrival at the hotel.** They differ by days for
most guests -- a traveller lands in Delhi on the 3rd and reaches a Chirala
resort on the 6th -- and the form asks for the border, not the front door.
So it is its own field, seeded blank rather than defaulted to check-in,
which is the commonest way a Form C is filed wrong.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime

from chirala_common.audit import record_audit
from chirala_common.authz import Caller, assert_property_in_org, build_authz
from chirala_common.routing import TransactionalRoute
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.orm import Session

from .database import get_session
from .settings import settings

_get_caller, require_permission, _require_org = build_authz(
    get_session, lambda: settings.environment,
    lambda: settings.service_token,
)

formc_router = APIRouter(tags=["form-c"], route_class=TransactionalRoute)

STATUSES = ("pending", "filed", "exempt")

#: Spellings of "this guest is Indian and needs no Form C".
#:
#: Matched case-insensitively against a free-text column that has been filled
#: in by hand, by an importer and by a channel, so it has to tolerate all
#: three. Anything not on this list is treated as foreign -- the safe
#: direction to be wrong in, because the cost is a form somebody marks
#: exempt, against a fine and an offence under the Act.
INDIAN = {"india", "indian", "in", "ind", "bharat"}

#: Documents only an Indian citizen or resident holds. Not proof -- a
#: long-resident foreign national can hold an Aadhaar -- but at a hotel desk
#: it is a strong enough signal to keep somebody off a compliance register,
#: and it is the difference between a register that is read and one that is
#: dismissed.
INDIAN_IDS = {"aadhaar", "voter_id"}


def nationality_state(nationality: str | None, id_type: str | None = None) -> str:
    """``foreign``, ``indian``, or ``unknown`` -- and the third one matters.

    This began as a boolean where unknown counted as foreign, on the
    reasoning that a blank nationality is a question rather than an
    exemption. That reasoning is sound and the boolean was still wrong: this
    database has nationality recorded for NONE of its guests, so the first
    register drew 48 rows headed "overdue", every one of them a domestic
    guest, every one of them a legal breach the property had not committed.

    A compliance tool that raises forty-eight false alarms on the day it
    ships is not cautious, it is ignored -- and then the one real foreign
    arrival is buried in the same list nobody reads any more.

    So unknown is its own state. It is still surfaced, and still needs
    answering, but it is counted as missing DATA rather than a missed
    FILING, because that is what it is.
    """
    if nationality and nationality.strip():
        return "indian" if nationality.strip().lower() in INDIAN else "foreign"
    if (id_type or "").lower() in INDIAN_IDS:
        return "indian"
    return "unknown"


def is_foreign(nationality: str | None, id_type: str | None = None) -> bool:
    """Whether a Form C is definitely required. Unknown is not "yes"."""
    return nationality_state(nationality, id_type) == "foreign"


# --------------------------------------------------------------------------
# Schemas
# --------------------------------------------------------------------------
class FormCIn(BaseModel):
    full_name: str | None = Field(default=None, max_length=200)
    sex: str | None = Field(default=None, max_length=10)
    date_of_birth: date | None = None
    nationality: str | None = Field(default=None, max_length=80)

    passport_number: str | None = Field(default=None, max_length=60)
    passport_issue_place: str | None = Field(default=None, max_length=120)
    passport_issue_date: date | None = None
    passport_expiry_date: date | None = None

    visa_number: str | None = Field(default=None, max_length=60)
    visa_type: str | None = Field(default=None, max_length=60)
    visa_issue_place: str | None = Field(default=None, max_length=120)
    visa_issue_date: date | None = None
    visa_expiry_date: date | None = None

    arrived_in_india_on: date | None = None
    arrived_in_india_at: str | None = Field(default=None, max_length=120)
    address_in_india: str | None = Field(default=None, max_length=300)
    permanent_address: str | None = Field(default=None, max_length=300)
    purpose_of_visit: str | None = Field(default=None, max_length=120)
    next_destination: str | None = Field(default=None, max_length=160)
    notes: str | None = Field(default=None, max_length=500)


class FileIn(BaseModel):
    #: What the FRRO portal gave back. Required: a filing nobody can evidence
    #: is not a filing, and the whole point of recording it here is to have
    #: the number to hand when an inspection asks.
    acknowledgement_no: str = Field(min_length=1, max_length=80)
    notes: str | None = Field(default=None, max_length=500)


class FormCOut(FormCIn):
    id: uuid.UUID | None = None
    reservation_unit_id: uuid.UUID
    reservation_number: str | None = None
    room_code: str | None = None
    arrival_date: date | None = None
    departure_date: date | None = None
    status: str = "pending"
    filed_at: datetime | None = None
    acknowledgement_no: str | None = None
    #: Whether this guest needs a form at all, and what is still missing if
    #: they do. The desk should not have to know the FRRO's field list by
    #: heart to find out why a form cannot be filed yet.
    required: bool = True
    missing: list[str] = []
    exists: bool = False


class RegisterRow(BaseModel):
    reservation_unit_id: uuid.UUID
    reservation_number: str | None
    guest_name: str | None
    nationality: str | None
    #: foreign | indian | unknown. `unknown` is a data gap, not a breach.
    nationality_state: str
    room_code: str | None
    arrival_date: date | None
    departure_date: date | None
    checked_in_at: datetime | None
    status: str
    filed_at: datetime | None
    acknowledgement_no: str | None
    missing_count: int
    #: Hours since check-in, against the 24 the law allows. Negative means
    #: the deadline has passed, which is the number the register sorts on.
    hours_left: float | None


class Register(BaseModel):
    rows: list[RegisterRow]
    pending: int
    overdue: int
    filed: int
    #: Arrivals whose nationality nobody recorded. Reported separately and
    #: never counted as overdue: they are a question for the desk, not a
    #: filing the property has missed.
    unknown: int


#: What the FRRO will not accept a form without. Checked so the register can
#: say "3 fields missing" instead of letting a clerk discover it on the
#: portal with the guest gone.
REQUIRED_FIELDS = (
    ("full_name", "Name"),
    ("nationality", "Nationality"),
    ("passport_number", "Passport number"),
    ("visa_number", "Visa number"),
    ("visa_type", "Visa type"),
    ("arrived_in_india_on", "Date of arrival in India"),
    ("arrived_in_india_at", "Place of arrival in India"),
)


def _missing(row: dict) -> list[str]:
    return [label for key, label in REQUIRED_FIELDS if not row.get(key)]


_UNIT_SQL = """
    SELECT ru.id AS unit_id, ru.property_id, ru.arrival_date,
           ru.departure_date, ru.purpose_of_visit, ru.status AS unit_status,
           r.id AS reservation_id, r.number AS reservation_number,
           r.organization_id,
           g.id AS guest_id, g.full_name, g.nationality, g.date_of_birth,
           g.id_type, g.id_number,
           g.address_line, g.city, g.state, g.postal_code, g.country,
           rm.code AS room_code,
           st.actual_checkin_at
    FROM booking.reservation_units ru
    JOIN booking.reservations r ON r.id = ru.reservation_id
    LEFT JOIN engagement.guests g ON g.id = r.primary_guest_id
    LEFT JOIN property.rooms rm ON rm.id = ru.assigned_room_id
    LEFT JOIN booking.stays st ON st.reservation_unit_id = ru.id
"""


def _unit(db: Session, unit_id: uuid.UUID, property_id: uuid.UUID):
    row = db.execute(
        text(_UNIT_SQL + " WHERE ru.id = :u AND ru.property_id = :p"),
        {"u": unit_id, "p": property_id},
    ).mappings().first()
    if row is None:
        raise HTTPException(404, "No such stay at this property.")
    return row


def _existing(db: Session, unit_id: uuid.UUID):
    return db.execute(
        text("SELECT * FROM booking.form_c WHERE reservation_unit_id = :u"),
        {"u": unit_id},
    ).mappings().first()


def _seed(unit) -> dict:
    """The form as far as this system can honestly fill it in.

    Passport number comes across only when the ID on file IS a passport --
    an Aadhaar number in the passport box would be a false statement on a
    document filed with the Bureau of Immigration.

    Visa fields and arrival-in-India are left blank on purpose. Nothing has
    ever collected them, and a seeded guess on a legal form is worse than an
    empty box: the empty box gets asked about at the desk.
    """
    addr = ", ".join(p for p in (
        unit["address_line"], unit["city"], unit["state"],
        unit["postal_code"], unit["country"]) if p)
    is_passport = (unit["id_type"] or "").lower() == "passport"
    return {
        "full_name": unit["full_name"],
        "nationality": unit["nationality"],
        "date_of_birth": unit["date_of_birth"],
        "passport_number": unit["id_number"] if is_passport else None,
        "permanent_address": addr or None,
        "purpose_of_visit": unit["purpose_of_visit"],
    }


@formc_router.get("/reservation-units/{unit_id}/form-c",
                  response_model=FormCOut)
def get_form_c(
    unit_id: uuid.UUID,
    property_id: uuid.UUID,
    caller: Caller = Depends(require_permission("reservations", "view")),
    db: Session = Depends(get_session),
):
    """This stay's Form C, or the blank one it would start from."""
    assert_property_in_org(db, caller, property_id)
    unit = _unit(db, unit_id, property_id)
    row = _existing(db, unit_id)
    data = dict(row) if row else _seed(unit)

    return FormCOut(
        **{k: data.get(k) for k in FormCIn.model_fields},
        id=row["id"] if row else None,
        reservation_unit_id=unit_id,
        reservation_number=unit["reservation_number"],
        room_code=unit["room_code"],
        arrival_date=unit["arrival_date"],
        departure_date=unit["departure_date"],
        status=row["status"] if row else "pending",
        filed_at=row["filed_at"] if row else None,
        acknowledgement_no=row["acknowledgement_no"] if row else None,
        required=is_foreign(unit["nationality"], unit["id_type"]),
        missing=_missing(data),
        exists=row is not None,
    )


@formc_router.put("/reservation-units/{unit_id}/form-c",
                  response_model=FormCOut)
def save_form_c(
    unit_id: uuid.UUID,
    property_id: uuid.UUID,
    body: FormCIn,
    caller: Caller = Depends(require_permission("reservations", "edit")),
    db: Session = Depends(get_session),
):
    """Save what has been collected. Partial is fine and expected.

    A desk types a passport number while the guest is at the counter and the
    visa details after they have gone up to the room. Refusing an incomplete
    form would mean the first half is lost.
    """
    assert_property_in_org(db, caller, property_id)
    unit = _unit(db, unit_id, property_id)
    row = _existing(db, unit_id)
    values = body.model_dump()

    if row is None:
        cols = ", ".join(values)
        binds = ", ".join(f":{k}" for k in values)
        db.execute(
            text(
                f"""
                INSERT INTO booking.form_c
                    (organization_id, property_id, reservation_unit_id,
                     guest_id, {cols})
                VALUES (:org, :prop, :unit, :guest, {binds})
                """
            ),
            {"org": unit["organization_id"], "prop": property_id,
             "unit": unit_id, "guest": unit["guest_id"], **values},
        )
    else:
        if row["status"] == "filed":
            raise HTTPException(
                409,
                "This Form C has already been filed. Editing it here would "
                "make the record disagree with what the Bureau of "
                "Immigration holds — file a correction on the portal, then "
                "note it here.",
            )
        sets = ", ".join(f"{k} = :{k}" for k in values)
        db.execute(
            text(f"UPDATE booking.form_c SET {sets}, "
                 f"updated_at = now(), version = version + 1 "
                 f"WHERE reservation_unit_id = :unit"),
            {"unit": unit_id, **values},
        )

    record_audit(
        db, action="form_c.saved", entity_type="reservation_unit",
        entity_id=str(unit_id), organization_id=unit["organization_id"],
        property_id=property_id, actor_subject=caller.subject,
    )
    return get_form_c(unit_id, property_id, caller, db)


@formc_router.post("/reservation-units/{unit_id}/form-c/file",
                   response_model=FormCOut)
def mark_filed(
    unit_id: uuid.UUID,
    property_id: uuid.UUID,
    body: FileIn,
    caller: Caller = Depends(require_permission("reservations", "edit")),
    db: Session = Depends(get_session),
):
    """Record that the form went to the FRRO, with its acknowledgement.

    This does not submit anything. Submission is a portal login the property
    holds, and a button here that claimed to file while doing nothing would
    be the most dangerous thing in this module -- a register that says
    "filed" is the evidence somebody relies on in an inspection.
    """
    assert_property_in_org(db, caller, property_id)
    unit = _unit(db, unit_id, property_id)
    row = _existing(db, unit_id)
    if row is None:
        raise HTTPException(
            422, "There is no Form C for this stay yet. Fill it in first.")
    if row["status"] == "filed":
        raise HTTPException(
            409, f"Already recorded as filed on "
                 f"{row['filed_at']:%d %b %Y} "
                 f"({row['acknowledgement_no']}).")

    gaps = _missing(dict(row))
    if gaps:
        raise HTTPException(
            422,
            "The FRRO will not accept this form yet — still missing: "
            + ", ".join(gaps) + ".",
        )

    db.execute(
        text(
            """
            UPDATE booking.form_c
               SET status = 'filed', filed_at = now(), filed_by = :who,
                   acknowledgement_no = :ack,
                   notes = COALESCE(:notes, notes),
                   updated_at = now(), version = version + 1
             WHERE reservation_unit_id = :unit
            """
        ),
        {"who": caller.user_id, "ack": body.acknowledgement_no,
         "notes": body.notes, "unit": unit_id},
    )
    record_audit(
        db, action="form_c.filed", entity_type="reservation_unit",
        entity_id=str(unit_id), organization_id=unit["organization_id"],
        property_id=property_id, actor_subject=caller.subject,
        after={"acknowledgement_no": body.acknowledgement_no},
    )
    return get_form_c(unit_id, property_id, caller, db)


@formc_router.get("/form-c/register", response_model=Register)
def register(
    property_id: uuid.UUID,
    status: str | None = Query(None),
    days: int = Query(30, ge=1, le=365),
    #: Off by default. The compliance list is guests known to be foreign;
    #: the unrecorded ones are a separate job and a separate screen-full.
    include_unknown: bool = Query(False),
    caller: Caller = Depends(require_permission("reservations", "view")),
    db: Session = Depends(get_session),
):
    """Who owes a Form C, who has filed one, and who is running out of time.

    Scoped to guests who have actually ARRIVED: the 24 hours runs from
    check-in, so a foreign booking for next month is not yet anybody's
    problem and putting it on this list would bury the one that is.
    """
    assert_property_in_org(db, caller, property_id)
    # The form joined in rather than fetched per row: a register that ran a
    # query per guest would do fifty of them to draw one page.
    rows = db.execute(
        text(
            _UNIT_SQL.replace(
                "    FROM booking.reservation_units ru",
                """,
           fc.id AS fc_id, fc.status AS fc_status, fc.filed_at,
           fc.acknowledgement_no,
           fc.full_name AS fc_full_name, fc.nationality AS fc_nationality,
           fc.passport_number, fc.visa_number, fc.visa_type,
           fc.arrived_in_india_on, fc.arrived_in_india_at
    FROM booking.reservation_units ru""")
            + """
             LEFT JOIN booking.form_c fc ON fc.reservation_unit_id = ru.id
             WHERE ru.property_id = :p
               AND st.actual_checkin_at IS NOT NULL
               AND st.actual_checkin_at > now() - make_interval(days => :d)
             ORDER BY st.actual_checkin_at DESC
            """
        ),
        {"p": property_id, "d": days},
    ).mappings().all()

    out: list[RegisterRow] = []
    unknown = 0
    for u in rows:
        state = nationality_state(u["nationality"], u["id_type"])
        if state == "indian":
            continue
        if state == "unknown":
            unknown += 1
            if not include_unknown:
                continue
        saved = u["fc_id"] is not None
        # The saved form when there is one, otherwise what the seed would
        # fill in -- so "missing 3 fields" counts the same way before and
        # after somebody first opens the form.
        data = ({"full_name": u["fc_full_name"],
                 "nationality": u["fc_nationality"],
                 "passport_number": u["passport_number"],
                 "visa_number": u["visa_number"], "visa_type": u["visa_type"],
                 "arrived_in_india_on": u["arrived_in_india_on"],
                 "arrived_in_india_at": u["arrived_in_india_at"]}
                if saved else _seed(u))
        st = u["fc_status"] if saved else "pending"
        if status and status != "all" and st != status:
            continue
        checked_in = u["actual_checkin_at"]
        left = None
        # The 24-hour clock runs only for a guest we KNOW is foreign. An
        # unrecorded nationality has no deadline attached to it, because
        # there may be no obligation at all.
        if checked_in is not None and st == "pending" and state == "foreign":
            elapsed = (datetime.now(checked_in.tzinfo) - checked_in)
            left = round(24 - elapsed.total_seconds() / 3600, 1)
        out.append(RegisterRow(
            reservation_unit_id=u["unit_id"],
            reservation_number=u["reservation_number"],
            guest_name=u["full_name"], nationality=u["nationality"],
            nationality_state=state,
            room_code=u["room_code"], arrival_date=u["arrival_date"],
            departure_date=u["departure_date"], checked_in_at=checked_in,
            status=st, filed_at=u["filed_at"] if saved else None,
            acknowledgement_no=u["acknowledgement_no"] if saved else None,
            missing_count=len(_missing(data)), hours_left=left,
        ))

    # The overdue first, then whatever time is left, then the done ones. A
    # register sorted by check-in buries the breach under the routine.
    out.sort(key=lambda r: (r.status != "pending",
                            r.hours_left if r.hours_left is not None else 1e9))
    return Register(
        rows=out,
        pending=sum(1 for r in out if r.status == "pending"
                    and r.nationality_state == "foreign"),
        overdue=sum(1 for r in out
                    if r.status == "pending"
                    and r.nationality_state == "foreign"
                    and (r.hours_left or 0) < 0),
        filed=sum(1 for r in out if r.status == "filed"),
        unknown=unknown,
    )
