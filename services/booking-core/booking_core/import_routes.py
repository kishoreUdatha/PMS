"""Onboarding step 8 — bringing existing bookings in from a spreadsheet.

A property switching systems arrives with a list of stays already sold, and
typing them back in one form at a time is where onboarding stops. This reads
that list, checks every row against what the property has actually set up, and
says what is wrong with the ones that cannot be taken.

**Validation is separate from import on purpose.** A file of a hundred
bookings will have a handful of rows that name a room that does not exist or
run a departure before an arrival, and finding that out halfway through --
with fifty bookings already written -- is the worst possible moment. So the
file is checked in full first, nothing is written, and the operator decides
what to do with the rows that failed.

**CSV only, for now.** ``.xlsx`` is a zip of XML and needs a library to read;
adding one means rebuilding the service image rather than shipping a file, so
the template is offered as CSV and the uploader says plainly what it accepts
rather than taking an ``.xlsx`` and failing on it.
"""
from __future__ import annotations

import csv
import io
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation

from fastapi import APIRouter, Depends, File, HTTPException, Response, UploadFile
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from chirala_common.audit import record_audit
from chirala_common.authz import Caller, assert_property_in_org, build_authz
from chirala_common.routing import TransactionalRoute

from .database import get_session
from .flow import FlowError, assign_room, confirm_reservation
from .inventory import InventoryShortage, create_hold
from .settings import settings

# The permission checker is built per service, against this service's session
# and environment — the same construction every other router here uses.
_get_caller, require_permission, _require_org_permission = build_authz(
    get_session, lambda: settings.environment,
    lambda: settings.service_token,
)

import_router = APIRouter(tags=["import"], route_class=TransactionalRoute)

#: What the importer reads. Anything else in the file is carried through
#: untouched and ignored, so a property can hand over its own export without
#: first deleting the columns this does not use.
COLUMNS: tuple[tuple[str, str, bool], ...] = (
    ("guest_name", "Guest name", True),
    ("email", "Email", False),
    ("phone", "Phone", False),
    ("room_type", "Room type", True),
    ("room_number", "Room number", False),
    ("arrival", "Arrival (YYYY-MM-DD)", True),
    ("departure", "Departure (YYYY-MM-DD)", True),
    ("adults", "Adults", False),
    ("children", "Children", False),
    ("total_amount", "Total amount", False),
    ("advance_paid", "Advance paid", False),
    ("booking_reference", "Booking reference", False),
    ("notes", "Notes", False),
)

#: One example row, so the shape of a date and an amount is unambiguous.
SAMPLE = {
    "guest_name": "Priya Sharma", "email": "priya@example.com",
    "phone": "+91 98765 43210", "room_type": "Deluxe Sea View",
    "room_number": "101", "arrival": "2026-10-02", "departure": "2026-10-05",
    "adults": "2", "children": "1", "total_amount": "19500",
    "advance_paid": "5000", "booking_reference": "OLD-1042",
    "notes": "Late arrival, keep dinner",
}

MAX_ROWS = 500


class RowIssue(BaseModel):
    """One row that cannot be imported as it stands."""

    row: int
    guest: str
    issue: str
    #: Which field is at fault, so the screen can offer the right fix.
    field: str


class ImportCheck(BaseModel):
    total_rows: int
    valid_rows: int
    issues: list[RowIssue]
    #: Money the file says has already been taken, and what is still owed.
    advance_total: Decimal
    outstanding_total: Decimal
    #: Header names found in the file that the importer does not read.
    ignored_columns: list[str]


@import_router.get("/import/template")
def download_template(
    property_id: uuid.UUID,
    caller: Caller = Depends(require_permission("reservations", "view")),
    db: Session = Depends(get_session),
):
    """The exact columns the importer reads, with one example row.

    Generated rather than kept as a file so it cannot drift from ``COLUMNS``:
    a template that asks for a column the importer ignores is worse than none.
    """
    assert_property_in_org(db, caller, property_id)
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow([label + ("" if required else " (optional)")
                     for _key, label, required in COLUMNS])
    writer.writerow([SAMPLE[key] for key, _label, _req in COLUMNS])
    return Response(
        content=buf.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition":
                 'attachment; filename="booking-import-template.csv"'},
    )


def _norm(header: str) -> str:
    """Match a column heading loosely — case, spaces and notes are ignored."""
    cut = header.split("(")[0]
    return "".join(ch for ch in cut.lower() if ch.isalnum())


#: Headings accepted for each field, beyond the template's own.
ALIASES: dict[str, tuple[str, ...]] = {
    "guest_name": ("guestname", "guest", "name", "customername"),
    "email": ("email", "emailaddress", "guestemail"),
    "phone": ("phone", "mobile", "contact", "phonenumber"),
    "room_type": ("roomtype", "category", "roomcategory"),
    "room_number": ("roomnumber", "room", "roomno"),
    "arrival": ("arrival", "arrivaldate", "checkin", "checkindate", "fromdate"),
    "departure": ("departure", "departuredate", "checkout", "checkoutdate",
                  "todate"),
    "adults": ("adults", "adult", "pax"),
    "children": ("children", "child", "kids"),
    "total_amount": ("totalamount", "total", "amount", "tariff"),
    "advance_paid": ("advancepaid", "advance", "paid", "deposit"),
    "booking_reference": ("bookingreference", "reference", "ref",
                          "bookingid", "confirmationno"),
    "notes": ("notes", "remarks", "comment", "comments"),
}


def _map_columns(header: list[str]) -> tuple[dict[str, int], list[str]]:
    """Which column holds which field, and what was left over."""
    found: dict[str, int] = {}
    used: set[int] = set()
    for key, _label, _req in COLUMNS:
        wanted = {_norm(key), *ALIASES.get(key, ())}
        for i, cell in enumerate(header):
            if i in used:
                continue
            if _norm(cell) in wanted:
                found[key] = i
                used.add(i)
                break
    ignored = [h.strip() for i, h in enumerate(header)
               if i not in used and h.strip()]
    return found, ignored


def _date(value: str) -> date | None:
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%m/%d/%Y", "%d %b %Y"):
        try:
            return datetime.strptime(value.strip(), fmt).date()
        except ValueError:
            continue
    return None


def _money(value: str) -> Decimal | None:
    cleaned = value.replace(",", "").replace("₹", "").strip()
    if cleaned == "":
        return Decimal(0)
    try:
        amount = Decimal(cleaned)
    except InvalidOperation:
        return None
    return amount if amount >= 0 else None


@dataclass
class ParsedRow:
    """A row that passed every check, in the types the writer needs."""

    row: int
    guest_name: str
    email: str | None
    phone: str | None
    room_type_id: uuid.UUID
    room_type_name: str
    room_id: uuid.UUID | None
    room_code: str | None
    arrival: date
    departure: date
    adults: int
    children: int
    total: Decimal
    advance: Decimal
    reference: str | None
    notes: str | None


def _read(
    db: Session, property_id: uuid.UUID, file: UploadFile,
) -> tuple[ImportCheck, list[ParsedRow]]:
    """Parse and check the file once, for both endpoints.

    The commit re-runs this rather than trusting what the check returned:
    between the two calls somebody may have deleted the room type the file
    names, and a browser can send whatever it likes.
    """
    name = (file.filename or "").lower()
    if name.endswith((".xlsx", ".xls")):
        raise HTTPException(
            status_code=415,
            detail="Excel files cannot be read yet — save the sheet as CSV "
                   "(File → Save As → CSV) and upload that.",
        )
    raw = file.file.read()
    if len(raw) > 5 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="Keep the file under 5MB.")
    try:
        body = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        try:
            body = raw.decode("latin-1")
        except UnicodeDecodeError:
            raise HTTPException(
                status_code=422,
                detail="That file is not text the importer can read.",
            ) from None

    rows = list(csv.reader(io.StringIO(body)))
    rows = [r for r in rows if any((c or "").strip() for c in r)]
    if len(rows) < 2:
        raise HTTPException(
            status_code=422,
            detail="The file has a heading row but no bookings under it.",
        )
    if len(rows) - 1 > MAX_ROWS:
        raise HTTPException(
            status_code=422,
            detail=f"That is {len(rows) - 1} bookings; {MAX_ROWS} at a time "
                   f"is the limit. Split the file and import it in parts.",
        )

    mapping, ignored = _map_columns(rows[0])
    missing = [label for key, label, required in COLUMNS
               if required and key not in mapping]
    if missing:
        raise HTTPException(
            status_code=422,
            detail="The file has no column for " + ", ".join(missing)
                   + ". Download the template to see what is needed.",
        )

    # What the property actually has, to check each row against.
    types = {
        r["name"].strip().lower(): r["id"] for r in db.execute(
            text("SELECT id, name FROM property.room_types "
                 "WHERE property_id = :p AND status = 'active'"),
            {"p": property_id},
        ).mappings().all()
    }
    rooms = {
        r["code"].strip().lower(): r["room_type_id"] for r in db.execute(
            text("SELECT code, room_type_id FROM property.rooms "
                 "WHERE property_id = :p AND status = 'active'"),
            {"p": property_id},
        ).mappings().all()
    }

    issues: list[RowIssue] = []
    good: list[ParsedRow] = []
    valid = 0
    advance_total = Decimal(0)
    outstanding_total = Decimal(0)

    # Room code -> id, for assigning the room the file names.
    room_ids = {
        r["code"].strip().lower(): r["id"] for r in db.execute(
            text("SELECT id, code FROM property.rooms "
                 "WHERE property_id = :p AND status = 'active'"),
            {"p": property_id},
        ).mappings().all()
    }

    def whole(value: str, fallback: int) -> int:
        try:
            n = int(value.strip())
        except (TypeError, ValueError):
            return fallback
        return n if n >= 0 else fallback

    def cell(row: list[str], key: str) -> str:
        i = mapping.get(key)
        return (row[i].strip() if i is not None and i < len(row) else "")

    for n, row in enumerate(rows[1:], start=2):
        guest = cell(row, "guest_name")
        problems: list[tuple[str, str]] = []

        if not guest:
            problems.append(("guest_name", "No guest name"))

        type_name = cell(row, "room_type")
        type_id = types.get(type_name.lower())
        if not type_name:
            problems.append(("room_type", "No room type"))
        elif type_id is None:
            problems.append(("room_type", f"Room type '{type_name}' not found"))

        room_no = cell(row, "room_number")
        if room_no:
            room_type_id = rooms.get(room_no.lower())
            if room_type_id is None:
                problems.append(("room_number", f"Room {room_no} not found"))
            elif type_id is not None and room_type_id != type_id:
                problems.append(
                    ("room_number",
                     f"Room {room_no} is not a {type_name}"))

        arrival = _date(cell(row, "arrival"))
        departure = _date(cell(row, "departure"))
        if arrival is None:
            problems.append(("arrival", "Arrival date not understood"))
        if departure is None:
            problems.append(("departure", "Departure date not understood"))
        if arrival and departure and departure <= arrival:
            problems.append(("departure", "Check-out before check-in"))

        total = _money(cell(row, "total_amount"))
        advance = _money(cell(row, "advance_paid"))
        if total is None:
            problems.append(("total_amount", "Total amount is not a number"))
        if advance is None:
            problems.append(("advance_paid", "Advance paid is not a number"))
        if total is not None and advance is not None and advance > total > 0:
            problems.append(
                ("advance_paid", "Advance is more than the total"))

        if problems:
            field, text_ = problems[0]
            extra = f" (+{len(problems) - 1} more)" if len(problems) > 1 else ""
            issues.append(RowIssue(row=n, guest=guest or "—",
                                   issue=text_ + extra, field=field))
            continue

        valid += 1
        advance_total += advance or Decimal(0)
        outstanding_total += (total or Decimal(0)) - (advance or Decimal(0))
        good.append(ParsedRow(
            row=n, guest_name=guest,
            email=cell(row, "email") or None, phone=cell(row, "phone") or None,
            room_type_id=type_id, room_type_name=type_name,
            room_id=room_ids.get(room_no.lower()) if room_no else None,
            room_code=room_no or None,
            arrival=arrival, departure=departure,
            adults=max(1, whole(cell(row, "adults"), 1)),
            children=whole(cell(row, "children"), 0),
            total=total or Decimal(0), advance=advance or Decimal(0),
            reference=cell(row, "booking_reference") or None,
            notes=cell(row, "notes") or None,
        ))

    return ImportCheck(
        total_rows=len(rows) - 1, valid_rows=valid, issues=issues,
        advance_total=advance_total, outstanding_total=outstanding_total,
        ignored_columns=ignored,
    ), good


@import_router.post("/import/check", response_model=ImportCheck)
def check_import(
    property_id: uuid.UUID,
    file: UploadFile = File(...),
    caller: Caller = Depends(require_permission("reservations", "create")),
    db: Session = Depends(get_session),
):
    """Read the file and say what is wrong with it. Nothing is written."""
    assert_property_in_org(db, caller, property_id)
    report, _rows = _read(db, property_id, file)
    return report


# --------------------------------------------------------------------------
# Writing the bookings in
# --------------------------------------------------------------------------
class ImportedRow(BaseModel):
    row: int
    guest: str
    created: bool
    number: str | None = None
    reason: str | None = None


class ImportResult(BaseModel):
    created: int
    skipped: int
    rows: list[ImportedRow]
    charged: Decimal
    paid: Decimal


def _guest_id(
    db: Session, *, organization_id: uuid.UUID, row: ParsedRow
) -> uuid.UUID:
    """The guest this booking belongs to, reusing one who is already known.

    Matched on email or phone, because a property re-importing its history
    should end with one record per person rather than one per booking.
    """
    if row.email or row.phone:
        found = db.execute(
            text(
                """
                SELECT id FROM engagement.guests
                WHERE organization_id = :o
                  AND ((CAST(:em AS text) IS NOT NULL AND email = CAST(:em AS text))
                    OR (CAST(:ph AS text) IS NOT NULL AND phone = CAST(:ph AS text)))
                LIMIT 1
                """
            ),
            {"o": organization_id, "em": row.email, "ph": row.phone},
        ).scalar()
        if found:
            return found

    gid = uuid.uuid4()
    db.execute(
        text(
            """
            INSERT INTO engagement.guests
                (id, organization_id, full_name, email, phone)
            VALUES (:id, :org, :name, :em, :ph)
            """
        ),
        {"id": gid, "org": organization_id, "name": row.guest_name,
         "em": row.email, "ph": row.phone},
    )
    return gid


def _folio_id(
    db: Session, *, organization_id: uuid.UUID, property_id: uuid.UUID,
    reservation_id: uuid.UUID, currency: str,
) -> uuid.UUID:
    """The reservation's folio, made if it has none yet."""
    existing = db.execute(
        text("SELECT id FROM finance.folios WHERE reservation_id = :r "
             "ORDER BY created_at LIMIT 1"),
        {"r": reservation_id},
    ).scalar_one_or_none()
    if existing is not None:
        return existing
    fid = uuid.uuid4()
    db.execute(
        text(
            """
            INSERT INTO finance.folios
                (id, organization_id, property_id, reservation_id, type,
                 commercial_account_id, currency, status)
            SELECT :id, :org, :prop, :res,
                   CASE WHEN r.bill_to = 'company' THEN 'company' ELSE 'guest' END,
                   r.commercial_account_id, :cur, 'open'
            FROM booking.reservations r WHERE r.id = :res
            """
        ),
        {"id": fid, "org": organization_id, "prop": property_id,
         "res": reservation_id, "cur": currency},
    )
    return fid


@import_router.post("/import/commit", response_model=ImportResult)
def commit_import(
    property_id: uuid.UUID,
    valid_only: bool = False,
    file: UploadFile = File(...),
    caller: Caller = Depends(require_permission("reservations", "create")),
    db: Session = Depends(get_session),
):
    """Create the bookings the file describes.

    The file is re-read and re-checked here rather than trusting whatever the
    review screen last reported: between the two calls somebody may have
    deleted the room type a row names, and a browser can send anything.

    **Each booking is written inside its own SAVEPOINT.** One row that fails
    late -- a room already taken by the row before it -- rolls back only
    itself, and the rest of the file still lands. The alternative, one
    transaction for the whole file, means a property of ninety good bookings
    gets none of them because of the ninety-first.

    **``booking_reference`` makes it repeatable.** A row whose reference is
    already on a reservation here is skipped, so uploading the same file twice
    does not double every stay. Rows without a reference cannot be recognised
    and will import again -- which is said plainly in the result rather than
    guessed at by matching names and dates.

    What each booking gets: a guest (an existing one where the email or phone
    matches), a confirmed reservation holding real inventory, the named room
    assigned if the file gave one, the total as a charge on the folio, and the
    advance as a payment against it. The difference is the outstanding balance,
    which is the whole point of bringing the money across rather than only the
    dates.
    """
    assert_property_in_org(db, caller, property_id)
    report, rows = _read(db, property_id, file)

    if report.issues and not valid_only:
        raise HTTPException(
            status_code=422,
            detail=f"{len(report.issues)} row(s) still need attention. Fix "
                   f"them, or tick \u201cImport valid rows only\u201d to take "
                   f"the {report.valid_rows} that are ready.",
        )
    if not rows:
        raise HTTPException(
            status_code=422, detail="No row in that file can be imported.")

    prop = db.execute(
        text("SELECT organization_id, currency FROM iam.properties WHERE id = :p"),
        {"p": property_id},
    ).mappings().first()
    if prop is None:
        raise HTTPException(status_code=404, detail="Property not found")
    org = prop["organization_id"]
    currency = prop["currency"] or "INR"

    # References already here, so a second upload is a no-op rather than a
    # duplicate of everything.
    taken = {
        r[0] for r in db.execute(
            text("SELECT reference FROM booking.reservations "
                 "WHERE property_id = :p AND reference IS NOT NULL"),
            {"p": property_id},
        ).all()
    }

    results: list[ImportedRow] = []
    created = 0
    charged = Decimal(0)
    paid = Decimal(0)

    for row in rows:
        if row.reference and row.reference in taken:
            results.append(ImportedRow(
                row=row.row, guest=row.guest_name, created=False,
                reason=f"Already imported as {row.reference}"))
            continue

        savepoint = db.begin_nested()
        try:
            guest = _guest_id(db, organization_id=org, row=row)
            held = create_hold(
                db,
                organization_id=org,
                property_id=property_id,
                arrival_date=row.arrival,
                departure_date=row.departure,
                idempotency_key=f"import-{property_id}-{row.reference or row.row}"
                                f"-{uuid.uuid4()}",
                room_type_id=row.room_type_id,
                units=1, adults=row.adults, children=row.children,
                hold_ttl_minutes=settings.hold_ttl_minutes,
                overbooking_allowance=settings.overbooking_allowance,
                guest_id=guest,
                # Deliberately no source. `source` records how the guest
                # reached the property -- direct, OTA, phone -- and the file
                # does not say. Stamping "direct" on a year of history would
                # quietly inflate every channel report the property runs in
                # its first month. Unknown is recorded as unknown; the
                # reference and the audit entry are what mark these as
                # imported.
                reference=row.reference,
                special_requests=row.notes,
            )
            confirm_reservation(db, reservation_id=held.reservation_id)
            db.execute(
                text("UPDATE booking.reservations SET primary_guest_id = :g, "
                     "version = version + 1 WHERE id = :r"),
                {"g": guest, "r": held.reservation_id},
            )

            if row.room_id is not None:
                # A room the file named may already be taken by an earlier row
                # for the same night. That is the file's problem to fix, not a
                # reason to lose the booking, so the stay is kept unassigned.
                try:
                    assign_room(db, reservation_unit_id=held.reservation_unit_id,
                                room_id=row.room_id)
                except FlowError:
                    pass

            if row.total > 0 or row.advance > 0:
                folio = _folio_id(
                    db, organization_id=org, property_id=property_id,
                    reservation_id=held.reservation_id, currency=currency)
                if row.total > 0:
                    db.execute(
                        text(
                            """
                            INSERT INTO finance.folio_entries
                                (id, organization_id, property_id, folio_id,
                                 entry_type, amount, currency, business_date,
                                 source_type, source_id, source_line_key)
                            VALUES (gen_random_uuid(), :org, :prop, :folio,
                                    'debit', :amt, :cur, :bd,
                                    'import', :src, :slk)
                            """
                        ),
                        {"org": org, "prop": property_id, "folio": folio,
                         "amt": row.total, "cur": currency, "bd": row.arrival,
                         "src": held.number,
                         "slk": f"import:charge:{held.reservation_id}"},
                    )
                    charged += row.total
                if row.advance > 0:
                    payment_id = uuid.uuid4()
                    db.execute(
                        text(
                            """
                            INSERT INTO finance.payments
                                (id, organization_id, property_id, method,
                                 amount, currency, status, received_at)
                            VALUES (:id, :org, :prop, 'cash', :amt, :cur,
                                    'succeeded', now())
                            """
                        ),
                        {"id": payment_id, "org": org, "prop": property_id,
                         "amt": row.advance, "cur": currency},
                    )
                    db.execute(
                        text(
                            """
                            INSERT INTO finance.folio_entries
                                (id, organization_id, property_id, folio_id,
                                 entry_type, amount, currency, business_date,
                                 source_type, source_id, source_line_key)
                            VALUES (gen_random_uuid(), :org, :prop, :folio,
                                    'credit', :amt, :cur, :bd,
                                    'import', :src, :slk)
                            """
                        ),
                        {"org": org, "prop": property_id, "folio": folio,
                         "amt": row.advance, "cur": currency,
                         "bd": row.arrival, "src": str(payment_id),
                         "slk": f"import:advance:{payment_id}"},
                    )
                    paid += row.advance
        except (InventoryShortage, FlowError, ValueError) as exc:
            savepoint.rollback()
            results.append(ImportedRow(
                row=row.row, guest=row.guest_name, created=False,
                reason=str(exc)))
            continue
        except IntegrityError as exc:
            savepoint.rollback()
            results.append(ImportedRow(
                row=row.row, guest=row.guest_name, created=False,
                reason=f"The database refused it: {exc.orig}"))
            continue

        savepoint.commit()
        created += 1
        if row.reference:
            taken.add(row.reference)
        results.append(ImportedRow(
            row=row.row, guest=row.guest_name, created=True,
            number=held.number))

    record_audit(
        db, action="booking.import", entity_type="property",
        entity_id=str(property_id), organization_id=org,
        property_id=property_id, actor_subject=caller.subject,
        after={"created": created, "skipped": len(results) - created,
               "charged": str(charged), "paid": str(paid),
               "file": file.filename},
    )

    return ImportResult(
        created=created, skipped=len(results) - created, rows=results,
        charged=charged, paid=paid,
    )


# --------------------------------------------------------------------------
# Onboarding step 10 — the test booking
# --------------------------------------------------------------------------
class TestStage(BaseModel):
    name: str
    passed: bool
    detail: str


class TestBookingOut(BaseModel):
    passed: bool
    stages: list[TestStage]
    #: What was booked, for the operator to recognise as plausible.
    room_type: str | None = None
    arrival: date | None = None
    departure: date | None = None
    number: str | None = None


@import_router.post("/onboarding/test-booking", response_model=TestBookingOut)
def run_test_booking(
    property_id: uuid.UUID,
    caller: Caller = Depends(require_permission("reservations", "create")),
    db: Session = Depends(get_session),
):
    """Put a booking through the real flow, then undo it completely.

    **Nothing is written.** The whole sequence runs inside a SAVEPOINT which is
    rolled back before the response is built, so the counters, the reservation
    and its number all disappear. That is what makes "test reservations do not
    affect live availability" true here rather than aspirational: it is not
    that the booking is marked as a test and skipped by inventory — it is that
    no booking survives the request.

    Rolling back rather than creating-and-cancelling matters. A cancelled
    reservation is still a row, still holds a number, and still shows up in
    tomorrow's cancellation report; a property would be told its first booking
    was cancelled before it opened.

    The value is that this exercises the same ``create_hold`` and
    ``confirm_reservation`` the front desk uses, against this property's real
    room types and inventory. If a rate is missing or a type has no rooms, it
    fails here rather than in front of a guest.
    """
    assert_property_in_org(db, caller, property_id)

    stages: list[TestStage] = []

    def fail(name: str, detail: str) -> TestBookingOut:
        stages.append(TestStage(name=name, passed=False, detail=detail))
        return TestBookingOut(passed=False, stages=stages)

    # A room type with a price, and a night the calendar actually covers.
    #
    # The night is chosen from the inventory calendar rather than picked far
    # ahead and hoped for: the calendar only runs a couple of months out, so a
    # date beyond its end has no row at all and fails for want of a row rather
    # than for want of a room. The furthest quiet night is taken, so a busy
    # week does not fail the test for a reason that is not a fault.
    row = db.execute(
        text(
            """
            SELECT rt.id, rt.name, rt.base_rate, i.stay_date,
                   (i.physical_capacity - i.out_of_service
                    - i.held_units - i.reserved_units) AS free,
                   (SELECT count(*) FROM property.rooms rm
                     WHERE rm.room_type_id = rt.id
                       AND rm.status = 'active') AS rooms
            FROM booking.room_type_inventory_days i
            JOIN property.room_types rt
              ON rt.id = i.room_type_id AND rt.property_id = i.property_id
            WHERE i.property_id = :p
              AND rt.status = 'active'
              AND COALESCE(rt.base_rate, 0) > 0
              AND i.stay_date > CURRENT_DATE
              AND (i.physical_capacity - i.out_of_service
                   - i.held_units - i.reserved_units) >= 1
            ORDER BY i.stay_date DESC, free DESC
            LIMIT 1
            """
        ),
        {"p": property_id},
    ).mappings().first()
    if row is None:
        return fail(
            "Find a sellable room",
            "No upcoming night has a free room on a priced room type. Either "
            "no type has both rooms and a rate, or every night in the "
            "calendar is full.")
    stages.append(TestStage(
        name="Find a sellable room", passed=True,
        detail=f"{row['name']} — {row['rooms']} rooms at {row['base_rate']}, "
               f"{row['free']} free on {row['stay_date']:%d %b %Y}"))

    arrival = row["stay_date"]
    departure = arrival + timedelta(days=1)

    org = db.execute(
        text("SELECT organization_id FROM iam.properties WHERE id = :p"),
        {"p": property_id},
    ).scalar_one()

    number: str | None = None
    try:
        # Everything inside here is undone, whatever happens.
        savepoint = db.begin_nested()
        try:
            held = create_hold(
                db,
                organization_id=org,
                property_id=property_id,
                arrival_date=arrival,
                departure_date=departure,
                idempotency_key=f"onboarding-test-{uuid.uuid4()}",
                room_type_id=row["id"],
                units=1, adults=1, children=0,
                hold_ttl_minutes=settings.hold_ttl_minutes,
                overbooking_allowance=settings.overbooking_allowance,
                source="walk_in",
            )
            number = held.number
            stages.append(TestStage(
                name="Hold a room", passed=True,
                detail=f"Inventory taken for {arrival:%d %b %Y} — "
                       f"booking {held.number}"))

            confirm_reservation(db, reservation_id=held.reservation_id)
            stages.append(TestStage(
                name="Confirm the booking", passed=True,
                detail="The hold became a confirmed reservation."))

            unit = db.execute(
                text("SELECT count(*) FROM booking.reservation_units "
                     "WHERE reservation_id = :r AND status = 'reserved'"),
                {"r": held.reservation_id},
            ).scalar_one()
            if unit != 1:
                raise FlowError(
                    f"Expected one reserved room, found {unit}.")
            stages.append(TestStage(
                name="Check the room is reserved", passed=True,
                detail="One room reserved for the night, as expected."))
        finally:
            # Before the response, before anything can commit.
            savepoint.rollback()
    except InventoryShortage as exc:
        return fail("Hold a room", str(exc))
    except (FlowError, ValueError) as exc:
        return fail("Book the room", str(exc))
    except IntegrityError as exc:
        return fail("Book the room", f"The database refused it: {exc.orig}")

    stages.append(TestStage(
        name="Undo it", passed=True,
        detail="Rolled back — no reservation, no number used, counters "
               "untouched."))

    return TestBookingOut(
        passed=True, stages=stages, room_type=row["name"],
        arrival=arrival, departure=departure, number=number,
    )
