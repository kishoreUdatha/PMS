"""Night audit: lock the business day, accrue the night, close it (§6).

A hotel never stops trading, so there is no natural moment when the books are
still. The night audit makes one. It locks the business day, posts the room
charge for every guest who slept in the house tonight, reports what the desk
must deal with in the morning, closes the day against further posting, and
rolls the business date forward.

**Room revenue accrues nightly.** Each night a guest is in-house, that night's
rate posts to their folio -- so revenue lands on the night it was earned, an
early departure simply stops accruing, and a mid-stay rate change affects only
the nights after it. The charges are derived here, from who is actually in the
house, rather than supplied by the caller: working out what to bill is the
audit's whole job, and an audit told to post nothing closes the day with every
guest un-charged and reports success.

Reruns are safe by construction. Each night's charge carries a line key of
``room_night:{business_date}:{reservation_unit_id}``, so a run interrupted
half-way -- the normal failure at 3am -- can simply be run again. A partial
unique index allows only one *completed* run per business date, and the day row
is locked FOR UPDATE so two runs cannot interleave.

What it reports but does not decide: no-shows and overstays. Both carry money
consequences (a no-show penalty is a policy choice, and there is a screen where
a human makes it), so the audit surfaces them and leaves the judgement to the
morning.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from chirala_common import no_show, ota_actions
from sqlalchemy import text
from sqlalchemy.orm import Session

from chirala_common.audit import record_audit

from .ledger import post_charge

#: How far ahead room-type inventory is kept open, in days. Every night the
#: audit pushes this edge one day further out, which is the only thing that
#: keeps a rolling horizon rolling: without it the far edge quietly stops
#: being bookable and looks exactly like a room type that was never loaded.
INVENTORY_HORIZON_DAYS = 400


class NightAuditError(Exception):
    def __init__(self, message: str, *, conflict: bool = False) -> None:
        self.conflict = conflict
        super().__init__(message)


@dataclass
class NightlyCharge:
    """A room charge to post during the audit for one room, for one night."""

    folio_id: uuid.UUID
    amount: Decimal
    charge_code_id: uuid.UUID | None = None
    #: The room this night belongs to. Part of the idempotency key, because a
    #: booking of two rooms puts both on one folio: keyed by folio alone, the
    #: second room's night collided with the first and was silently dropped.
    unit_id: uuid.UUID | None = None
    #: Who and what this night is for. Carried so the charge can be *shown* --
    #: "12 rooms, Rs 48,000" is a number to take on trust, and an auditor
    #: about to seal a day against further posting is entitled to see the
    #: lines it is made of.
    reservation_number: str | None = None
    room: str | None = None
    guest: str | None = None
    room_type: str | None = None


@dataclass
class Pending:
    """Something the audit found that a person has to deal with."""

    unit_id: uuid.UUID
    reservation_number: str | None
    room: str | None
    guest: str | None
    arrival_date: date
    departure_date: date


@dataclass
class AuditResult:
    run_id: uuid.UUID
    business_date: date
    charges_posted: int
    next_business_date: date
    steps: list[str] = field(default_factory=list)
    #: Rooms billed tonight, and what they came to.
    rooms_charged: int = 0
    amount_charged: Decimal = Decimal("0")
    #: Due to arrive, never checked in. Reported, not actioned.
    no_shows: list[Pending] = field(default_factory=list)
    #: Still in-house past their departure date.
    overstays: list[Pending] = field(default_factory=list)
    #: Inventory days created at the far edge of the horizon.
    horizon_days_added: int = 0
    #: Cashier shifts still open when the day closed.
    open_shifts: int = 0
    #: Anything that needs saying but is not an error.
    warnings: list[str] = field(default_factory=list)


def _line_key(business_date: date, ch: NightlyCharge) -> str:
    """One key per room per night, falling back to the folio.

    The fallback keeps callers that pass charges by hand working; anything
    derived here always carries a unit.
    """
    return f"room_night:{business_date}:{ch.unit_id or ch.folio_id}"


# --------------------------------------------------------------- derivation --

#: Everyone who was asleep in the house on ``:bd``.
#:
#: Two ways to have been there, and the audit needs both.
#:
#: **Still checked in.** Status is the authority over the dates: a guest who is
#: checked in occupies the room whatever their booking says, which is exactly
#: what an overstay is -- and an overstay still owes for the bed.
#:
#: **Checked in that night and has since left.** This is the one that was
#: missing, and it only bites when the audit runs late. Status is a statement
#: about *now*, so an audit catching up on a night that has already passed
#: looked for guests who were still in the house at the moment it ran and
#: found none of the ones who had checked out in between -- billing them for
#: nothing. One guest at Lekhana slept the night of 12 September, left at
#: 00:04, and the catch-up run at 09:40 saw ``checked_out`` and moved on;
#: their room night was never charged. Whether the audit is punctual decides
#: how much revenue is recognised, which is not a property a ledger may have.
#:
#: So the stay itself is consulted: checked in on or before that day, and not
#: yet gone by the end of it. A guest who arrives and leaves the same day was
#: asleep in the house on no night and is charged for none, which is what an
#: empty accrual correctly says.
#:
#: A booking that never checked in owes nothing for the room; it is a no-show,
#: handled separately.
_IN_HOUSE_SQL = """
    SELECT ru.id AS unit_id, ru.reservation_id, ru.nightly_rate,
           ru.comp_kind, ru.comp_reason,
           ru.arrival_date, ru.departure_date,
           r.number AS reservation_number, r.currency,
           r.organization_id,
           rm.code AS room, g.full_name AS guest,
           rt.name AS room_type
    FROM booking.reservation_units ru
    JOIN booking.reservations r ON r.id = ru.reservation_id
    LEFT JOIN property.rooms rm ON rm.id = ru.assigned_room_id
    LEFT JOIN property.room_types rt ON rt.id = ru.room_type_id
    LEFT JOIN engagement.guests g ON g.id = r.primary_guest_id
    WHERE ru.property_id = :prop
      AND ru.arrival_date <= :bd
      AND (
            ru.status = 'checked_in'
         OR EXISTS (
                SELECT 1
                  FROM booking.stays st
                  JOIN iam.properties pr ON pr.id = st.property_id
                 WHERE st.reservation_unit_id = ru.id
                   AND (st.actual_checkin_at
                        AT TIME ZONE COALESCE(pr.timezone, 'Asia/Kolkata')
                       )::date <= :bd
                   AND (st.actual_checkout_at IS NULL
                        OR (st.actual_checkout_at
                            AT TIME ZONE COALESCE(pr.timezone, 'Asia/Kolkata')
                           )::date > :bd)
            )
      )
    ORDER BY rm.code NULLS LAST
"""


def _folio_for(
    session: Session,
    *,
    organization_id: uuid.UUID,
    property_id: uuid.UUID,
    reservation_id: uuid.UUID,
    currency: str,
) -> uuid.UUID:
    """The open folio for a booking, opened now if it has none.

    A guest in the house with nowhere to post is not a reason to skip the
    night's revenue.
    """
    row = session.execute(
        text(
            """
            SELECT id FROM finance.folios
            WHERE property_id = :prop AND reservation_id = :res
              AND status = 'open'
            -- A group's master folio first, then the oldest.
            --
            -- A group booking now opens a master alongside a folio per
            -- guest, and the room nights belong on the master: the
            -- organiser agreed to pay for rooms, and a guest settling their
            -- bar tab should never be shown three nights they are not
            -- paying for. Ordering by `created_at` alone would have done
            -- the same thing today, because the master is created first --
            -- but only by accident of insertion order, and an accident is
            -- not a billing rule. Said out loud, it survives somebody
            -- reordering the inserts.
            ORDER BY (type = 'group') DESC, created_at
            LIMIT 1
            """
        ),
        {"prop": property_id, "res": reservation_id},
    ).first()
    if row is not None:
        return row.id

    folio_id = uuid.uuid4()
    session.execute(
        text(
            """
            INSERT INTO finance.folios
                (id, organization_id, property_id, reservation_id, type,
                 currency, status)
            VALUES (:id, :org, :prop, :res, 'guest', :cur, 'open')
            """
        ),
        {
            "id": folio_id,
            "org": organization_id,
            "prop": property_id,
            "res": reservation_id,
            "cur": currency or "INR",
        },
    )
    return folio_id


#: Why a room was given away, for the line the audit prints. Mirrors
#: COMP_REASONS in booking_core/comp_routes.py -- the audit reads the column
#: directly and cannot call that service.
_COMP_REASONS: dict[str, str] = {
    "vip": "VIP guest",
    "fam_trip": "Agent familiarisation trip",
    "tour_leader": "Tour leader on a group",
    "service_recovery": "Putting right a bad stay",
    "owner": "Owner or management",
    "marketing": "Marketing or influencer",
    "loyalty": "Loyalty redemption",
    "maintenance": "Maintenance or repair",
    "office": "Office or back-of-house",
    "staff_accommodation": "Staff accommodation",
    "show_room": "Show room",
    "other": "Other",
}


def derive_nightly_charges(
    session: Session,
    *,
    organization_id: uuid.UUID,
    property_id: uuid.UUID,
    business_date: date,
) -> tuple[list[NightlyCharge], list[str]]:
    """Tonight's room charges, worked out from who is in the house.

    Returns the charges and any warnings. A unit with no nightly rate is
    skipped and named rather than billed at a guessed figure: an audit that
    invents a price is worse than one that tells you a price is missing.

    A room declared complimentary or house use is not charged. Until this
    check existed the declaration was a label the audit ignored: the front
    desk marked a VIP's room free and the audit billed it every night anyway,
    so somebody had to notice and reverse each posting by hand. The skip is
    named in the warnings rather than done silently -- a night auditor should
    see which rooms produced no revenue and why, or a comp marked by mistake
    would never surface.
    """
    rows = session.execute(
        text(_IN_HOUSE_SQL), {"prop": property_id, "bd": business_date}
    ).mappings().all()

    charges: list[NightlyCharge] = []
    warnings: list[str] = []
    for r in rows:
        # Before the missing-rate check, deliberately: a comp room often has
        # no rate either, and reporting it as a missing price would send the
        # auditor looking for a fault where there is a decision.
        if r["comp_kind"]:
            kind = ("house use" if r["comp_kind"] == "house_use"
                    else "complimentary")
            reason = _COMP_REASONS.get(r["comp_reason"] or "", r["comp_reason"])
            warnings.append(
                f"{r['reservation_number']} "
                f"({r['room'] or 'no room'}) is {kind}"
                f"{f' — {reason}' if reason else ''} — not charged."
            )
            continue
        if r["nightly_rate"] is None:
            warnings.append(
                f"{r['reservation_number']} "
                f"({r['room'] or 'no room'}) has no nightly rate — not charged."
            )
            continue
        folio_id = _folio_for(
            session,
            organization_id=r["organization_id"] or organization_id,
            property_id=property_id,
            reservation_id=r["reservation_id"],
            currency=r["currency"],
        )
        charges.append(
            NightlyCharge(
                folio_id=folio_id,
                amount=Decimal(str(r["nightly_rate"])),
                unit_id=r["unit_id"],
                reservation_number=r["reservation_number"],
                room=r["room"],
                guest=r["guest"],
                room_type=r["room_type"],
            )
        )
    return charges, warnings


def _pending(rows) -> list[Pending]:
    return [
        Pending(
            unit_id=r["unit_id"],
            reservation_number=r["reservation_number"],
            room=r["room"],
            guest=r["guest"],
            arrival_date=r["arrival_date"],
            departure_date=r["departure_date"],
        )
        for r in rows
    ]


_PENDING_COLS = """
    ru.id AS unit_id, ru.arrival_date, ru.departure_date,
    r.number AS reservation_number,
    rm.code AS room, g.full_name AS guest
"""


def find_no_shows(
    session: Session, *, property_id: uuid.UUID, business_date: date
) -> list[Pending]:
    """Bookings that were due by tonight and never checked in.

    Reported, not actioned. Turning one into a no-show may charge a penalty,
    which is a policy decision with a screen of its own -- a background job
    should not take a guest's money while nobody is watching.
    """
    rows = session.execute(
        text(
            f"""
            SELECT {_PENDING_COLS}
            FROM booking.reservation_units ru
            JOIN booking.reservations r ON r.id = ru.reservation_id
            LEFT JOIN property.rooms rm ON rm.id = ru.assigned_room_id
            LEFT JOIN engagement.guests g ON g.id = r.primary_guest_id
            WHERE ru.property_id = :prop
              AND ru.status = 'reserved'
              AND ru.arrival_date <= :bd
            ORDER BY ru.arrival_date, r.number
            """
        ),
        {"prop": property_id, "bd": business_date},
    ).mappings().all()
    return _pending(rows)


#: What a no-show costs when nobody is there to decide.
#:
#: ``none`` now, from the shared module. It used to be one night plus tax,
#: which meant every property that had never chosen a policy -- all of them --
#: charged guests automatically, on a path whose own sibling docstring says a
#: background job should not take a guest's money while nobody is watching. A
#: hotel that wants the audit to charge says so.
DEFAULT_NO_SHOW_PENALTY = no_show.DEFAULT_BASIS


def process_no_shows(
    session: Session,
    *,
    organization_id: uuid.UUID,
    property_id: uuid.UUID,
    business_date: date,
    basis: str = DEFAULT_NO_SHOW_PENALTY,
) -> tuple[list[Pending], Decimal, list[str]]:
    """Turn arrivals that never came into no-shows, and charge the policy.

    An unprocessed no-show is not merely untidy: the booking sits on a sealed
    date still holding its room, so the night is neither sold nor available,
    and it stays that way until somebody notices. Resolving them is what the
    audit is for.

    Three things happen per booking, and the order matters less than the fact
    that all three happen: the penalty is charged, the room is handed back to
    inventory, and the unit is marked so tomorrow's audit does not find it
    again.

    **The penalty shares its idempotency key with the manual screen**
    (``no_show_penalty:{unit_id}``). A guest processed by hand this afternoon
    and by the audit tonight is charged once, by whichever got there first --
    which is the only safe way to have two paths to the same money.

    Tax comes from the finance tax engine via ``post_charge`` rather than being
    computed here. booking-core keeps its own mirror of those rules for the
    manual screen; using the engine itself means the penalty cannot be taxed
    differently from the room night it stands in for.
    """
    basis = no_show.normalise(basis)
    found = find_no_shows(
        session, property_id=property_id, business_date=business_date)
    if not found:
        return found, Decimal("0"), []
    # "none" skips the *charge*, not the resolution. Returning here -- which is
    # what this did -- left the booking on a sealed date still holding its
    # room, which is precisely the state this function exists to clear. It
    # mattered little while nobody chose "none"; it matters completely now that
    # an unconfigured property gets it.

    warnings: list[str] = []
    charged = Decimal("0")
    for p in found:
        row = session.execute(
            text(
                """
                SELECT u.id, u.reservation_id, u.room_type_id, u.nightly_rate,
                       u.comp_kind,
                       u.arrival_date, u.departure_date, r.number, r.currency,
                       r.organization_id
                FROM booking.reservation_units u
                JOIN booking.reservations r ON r.id = u.reservation_id
                WHERE u.id = :u
                """
            ),
            {"u": p.unit_id},
        ).mappings().one()

        if basis == "none":
            # Recorded and released below, simply not billed. No warning: a
            # property that chose not to charge does not need telling every
            # night that it did not charge.
            pass
        elif row["comp_kind"]:
            # A room nobody was going to be charged for cannot be charged for
            # failing to turn up. The no-show is still recorded and the room
            # still goes back on sale; only the penalty is dropped.
            warnings.append(
                f"{row['number']} was "
                f"{'house use' if row['comp_kind'] == 'house_use' else 'complimentary'}"
                f", so no penalty was charged — the room was released and it "
                f"is marked no-show."
            )
        elif row["nightly_rate"] is None:
            # No rate means no defensible penalty. The booking is still turned
            # into a no-show -- leaving it open would keep the room off sale --
            # but nobody is billed a number this code invented.
            warnings.append(
                f"{row['number']} had no nightly rate, so no penalty was "
                f"charged — the room was released and it is marked no-show."
            )
        else:
            # What the chosen basis actually says, not one night regardless.
            # Until this used chirala_common.no_show, ``basis`` only ever
            # distinguished "none" from "charge something": a property that
            # chose Full Stay had its guests charged a single night, and
            # nothing anywhere reported the difference.
            amount, _taxed = no_show.penalty(
                basis, nightly_rate=row["nightly_rate"],
                # len(): _nights_between returns the dates, not a count.
                nights=len(_nights_between(row["arrival_date"],
                                           row["departure_date"])) or 1)
            folio_id = _folio_for(
                session,
                organization_id=row["organization_id"] or organization_id,
                property_id=property_id,
                reservation_id=row["reservation_id"],
                currency=row["currency"],
            )
            res = post_charge(
                session,
                organization_id=organization_id,
                property_id=property_id,
                folio_id=folio_id,
                amount=amount,
                business_date=business_date,
                source_type="no_show_penalty",
                tax_category="rooms",
                # Shared with the manual screen, on purpose.
                source_line_key=f"no_show_penalty:{p.unit_id}",
            )
            if res.created:
                charged += amount

        # Hand the room back: the calendar entry, then the nights it held.
        session.execute(
            text("UPDATE booking.room_calendar_entries SET status = 'released',"
                 " version = version + 1 "
                 "WHERE reservation_unit_id = :u AND status = 'active'"),
            {"u": p.unit_id},
        )
        nights = _nights_between(row["arrival_date"], row["departure_date"])
        if nights:
            session.execute(
                text(
                    """
                    UPDATE booking.room_type_inventory_days
                       SET reserved_units = GREATEST(reserved_units - 1, 0)
                     WHERE property_id = :p AND room_type_id = :rt
                       AND stay_date = ANY(:dates)
                    """
                ),
                {"p": property_id, "rt": row["room_type_id"], "dates": nights},
            )

        # An OTA booking that no-showed must be reported to that OTA within
        # 24 hours or the hotel pays commission on a room nobody slept in.
        # Raised whether or not a penalty was charged: the commission waiver
        # and the guest's penalty are different money owed by different people.
        ota_actions.raise_action(
            session,
            organization_id=row["organization_id"] or organization_id,
            property_id=property_id,
            reservation_id=row["reservation_id"],
            reservation_unit_id=p.unit_id,
            reservation_number=row["number"],
            action_type="no_show",
        )

        session.execute(
            text("UPDATE booking.reservation_units "
                 "SET status = 'no_show', assigned_room_id = NULL, "
                 "    version = version + 1 "
                 "WHERE id = :u AND status = 'reserved'"),
            {"u": p.unit_id},
        )
        # A booking with nothing left alive is over.
        session.execute(
            text(
                """
                UPDATE booking.reservations r SET status = 'cancelled',
                       version = version + 1
                 WHERE r.id = :res
                   AND NOT EXISTS (
                       SELECT 1 FROM booking.reservation_units u2
                        WHERE u2.reservation_id = r.id
                          AND u2.status NOT IN ('no_show', 'cancelled'))
                """
            ),
            {"res": row["reservation_id"]},
        )

    return found, charged, warnings


def _nights_between(arrival: date, departure: date) -> list[date]:
    """Half-open, as everywhere else: the departure date is not a night."""
    return [arrival + timedelta(days=i)
            for i in range((departure - arrival).days)]


def find_overstays(
    session: Session, *, property_id: uuid.UUID, business_date: date
) -> list[Pending]:
    """Guests still in-house on or after the day they were due to leave.

    They are charged for tonight like anyone else in a bed; what needs a human
    is the booking, which now says they left.
    """
    rows = session.execute(
        text(
            f"""
            SELECT {_PENDING_COLS}
            FROM booking.reservation_units ru
            JOIN booking.reservations r ON r.id = ru.reservation_id
            LEFT JOIN property.rooms rm ON rm.id = ru.assigned_room_id
            LEFT JOIN engagement.guests g ON g.id = r.primary_guest_id
            WHERE ru.property_id = :prop
              AND ru.status = 'checked_in'
              AND ru.departure_date <= :bd
            ORDER BY ru.departure_date, r.number
            """
        ),
        {"prop": property_id, "bd": business_date},
    ).mappings().all()
    return _pending(rows)


def extend_inventory_horizon(
    session: Session,
    *,
    property_id: uuid.UUID,
    business_date: date,
    days: int = INVENTORY_HORIZON_DAYS,
) -> int:
    """Open inventory for any night inside the horizon that has none.

    A night with no inventory row has nothing to sell, so the horizon *is* the
    booking window. It was written once when a room type was created and never
    moved, so the window silently shrank by a day every day.

    Only missing nights are created -- ON CONFLICT DO NOTHING -- because an
    existing night's capacity is owned by the room-type code, which knows about
    rooms going out of service. This step opens the far edge; it does not
    second-guess the near one.
    """
    result = session.execute(
        text(
            """
            INSERT INTO booking.room_type_inventory_days
                (organization_id, property_id, room_type_id, stay_date,
                 physical_capacity, out_of_service, held_units,
                 reserved_units, allotment_units)
            SELECT rt.organization_id, rt.property_id, rt.id, d::date,
                   (SELECT count(*) FROM property.rooms rm
                     WHERE rm.room_type_id = rt.id AND rm.status = 'active'),
                   0, 0, 0, 0
            FROM property.room_types rt
            CROSS JOIN generate_series(
                CAST(:bd AS date),
                CAST(:bd AS date) + CAST(:days AS int),
                interval '1 day') AS d
            WHERE rt.property_id = :prop
              AND rt.status = 'active'
            ON CONFLICT (property_id, room_type_id, stay_date) DO NOTHING
            """
        ),
        {"prop": property_id, "bd": business_date, "days": days},
    )
    return result.rowcount or 0


def local_today(session: Session, property_id: uuid.UUID) -> date:
    """Today where the property is, not where the server is.

    A resort in Kolkata and one in Lisbon do not change date together, and the
    server may be in neither.
    """
    row = session.execute(
        text("SELECT timezone FROM iam.properties WHERE id = :p"),
        {"p": property_id},
    ).first()
    tz_name = (row.timezone if row else None) or "Asia/Kolkata"
    try:
        tz = ZoneInfo(tz_name)
    except (ZoneInfoNotFoundError, ValueError):
        tz = ZoneInfo("Asia/Kolkata")
    return datetime.now(tz).date()


def local_now(session: Session, property_id: uuid.UUID) -> datetime:
    """The property's own wall clock, for deciding whether its day is over."""
    row = session.execute(
        text("SELECT timezone FROM iam.properties WHERE id = :p"),
        {"p": property_id},
    ).first()
    tz_name = (row.timezone if row else None) or "Asia/Kolkata"
    try:
        tz = ZoneInfo(tz_name)
    except (ZoneInfoNotFoundError, ValueError):
        tz = ZoneInfo("Asia/Kolkata")
    return datetime.now(tz)


def audit_hour_of(session: Session, property_id: uuid.UUID) -> int:
    """The hour this property ends its business day, or the deployment's.

    The same LEFT JOIN the scheduler uses: a tenant that never expressed a
    preference still has an audit hour, it is simply the default one.
    """
    from .settings import settings

    row = session.execute(
        text("SELECT audit_hour FROM finance.night_audit_settings "
             "WHERE property_id = :p"),
        {"p": property_id},
    ).first()
    if row is not None and row.audit_hour is not None:
        return int(row.audit_hour)
    return int(settings.night_audit_hour)


def future_day_reason(
    business_date: date,
    today: date,
    now_local: datetime | None = None,
    audit_hour: int | None = None,
) -> str | None:
    """Why this date cannot be closed yet, or None if it can.

    **A day that has not happened cannot be closed.** Closing seals the date
    against posting, so sealing tomorrow means every guest who sleeps here
    tomorrow is never charged for the night -- the accrual has nowhere to land
    and nothing will ever try again, because the audit only visits days that
    are still open. It is silent, and it is unrecoverable without reopening a
    closed financial period.

    **Nor can today, until today is over.** This is the half that was missing,
    and "over" is the property's own audit hour, not midnight: a hotel's
    business day ends when the auditor closes it, which is what the audit hour
    configures. Lekhana closes at 23:00 and was closed at 00:15, sealing a day
    with 22 3/4 hours still to run. Nothing was posted -- and that is the
    damage: the date advanced, so everything that happened for the rest of the
    14th was booked to the 15th, one day ahead of the calendar, where a report
    ending "today" could not see it.

    Running a little early is still a normal operational choice; the auditor
    may close at 22:40 when the hour is 23:00 and the guard will not argue
    about minutes. What it refuses is closing a day that has barely begun.
    """
    if business_date > today:
        days = (business_date - today).days
        return (
            f"{business_date:%d %b %Y} has not happened yet — it is "
            f"{days} day(s) ahead of the property's date ({today:%d %b %Y}). "
            f"Closing it would seal the night against the charges it has yet "
            f"to earn."
        )
    # Today, before the day has run its course. Yesterday and earlier are
    # always closeable; this only guards the day in progress.
    if business_date == today and now_local is not None and audit_hour is not None:
        # An hour's grace, so an auditor who starts a few minutes early is not
        # told to wait. The point is to refuse 00:15, not to police 22:40.
        earliest = max(audit_hour - 1, 0)
        if now_local.hour < earliest:
            return (
                f"{business_date:%d %b %Y} is still in progress — it is "
                f"{now_local:%H:%M} and this property closes its day at "
                f"{audit_hour:02d}:00. Closing now would seal the day against "
                f"everything it has yet to earn, and move the business date a "
                f"day ahead of the calendar."
            )
    return None


@dataclass
class OpenShift:
    """A till still open as the day closes."""

    shift_id: uuid.UUID
    cashier: str | None
    opened_at: datetime | None
    opening_float: Decimal


def find_open_shifts(
    session: Session, *, property_id: uuid.UUID, business_date: date
) -> list[OpenShift]:
    """Which drawers are still open, not just how many.

    "1 cashier shift open" tells the auditor there is a problem and nothing
    about whose it is. The name and the time it was opened are what turn it
    into something somebody can go and settle.
    """
    rows = session.execute(
        text(
            """
            SELECT cs.id, cs.opened_at, cs.opening_float,
                   u.display_name AS cashier
            FROM finance.cashier_shifts cs
            LEFT JOIN iam.users u ON u.id = cs.cashier_id
            WHERE cs.property_id = :prop AND cs.business_date = :bd
              AND cs.status = 'open'
            ORDER BY cs.opened_at
            """
        ),
        {"prop": property_id, "bd": business_date},
    ).mappings().all()
    return [
        OpenShift(
            shift_id=r["id"], cashier=r["cashier"], opened_at=r["opened_at"],
            opening_float=Decimal(str(r["opening_float"] or 0)),
        )
        for r in rows
    ]


#: The house as it stood when the day closed.
#:
#: A snapshot, so it is recorded rather than recomputed. Sales and receipts can
#: be read back from dated ledger rows months later and still be true; "how
#: many rooms were occupied" cannot -- ask that question next March and you get
#: next March's answer.
_OCCUPANCY_SQL = """
    SELECT
      (SELECT count(*) FROM property.rooms r
        WHERE r.property_id = :prop AND r.status = 'active') AS total_rooms,
      (SELECT count(*) FROM booking.reservation_units u
        WHERE u.property_id = :prop AND u.status = 'checked_in'
          AND u.arrival_date <= :bd) AS occupied,
      (SELECT coalesce(sum(u.adults), 0) FROM booking.reservation_units u
        WHERE u.property_id = :prop AND u.status = 'checked_in'
          AND u.arrival_date <= :bd) AS adults,
      (SELECT coalesce(sum(u.children), 0) FROM booking.reservation_units u
        WHERE u.property_id = :prop AND u.status = 'checked_in'
          AND u.arrival_date <= :bd) AS children,
      (SELECT count(*) FROM booking.reservation_units u
        WHERE u.property_id = :prop AND u.arrival_date = :bd
          AND u.status IN ('checked_in', 'checked_out')) AS arrivals,
      (SELECT count(*) FROM booking.stays st
        WHERE st.property_id = :prop
          AND CAST(st.actual_checkout_at AS date) = :bd) AS departures,
      (SELECT count(*) FROM booking.reservation_units u
        WHERE u.property_id = :prop AND u.status = 'checked_in'
          AND u.departure_date = :bd) AS due_out
"""


def occupancy_snapshot(
    session: Session, *, property_id: uuid.UUID, business_date: date
) -> dict:
    """Rooms and heads as the day closed."""
    r = session.execute(
        text(_OCCUPANCY_SQL), {"prop": property_id, "bd": business_date}
    ).mappings().one()
    return {
        "total_rooms": int(r["total_rooms"]),
        "occupied": int(r["occupied"]),
        "vacant": int(r["total_rooms"]) - int(r["occupied"]),
        "due_out": int(r["due_out"]),
        "arrivals": int(r["arrivals"]),
        "departures": int(r["departures"]),
        "adults": int(r["adults"]),
        "children": int(r["children"]),
    }


def count_open_shifts(
    session: Session, *, property_id: uuid.UUID, business_date: date
) -> int:
    """Cashier shifts still open as the day closes.

    An open shift means cash counted against a day that is now sealed. The
    audit does not force it shut -- only the cashier can declare their drawer
    -- but a day closing over one needs saying out loud.
    """
    return int(
        session.execute(
            text(
                """
                SELECT count(*) FROM finance.cashier_shifts
                WHERE property_id = :prop AND business_date = :bd
                  AND status = 'open'
                """
            ),
            {"prop": property_id, "bd": business_date},
        ).scalar_one()
    )


# ------------------------------------------------------------------- the run --


def open_shift_block(shifts: list[OpenShift]) -> str:
    """The refusal text for closing a day over an uncounted drawer."""
    who = ", ".join(sh.cashier or "an unnamed cashier" for sh in shifts)
    return (
        f"{len(shifts)} cashier shift(s) are still open ({who}). A shift ties "
        f"cash to a business date, so a drawer left open when the date rolls "
        f"puts its cash on the wrong day and the deposit will not reconcile. "
        f"Close the drawer, or close the day over it deliberately."
    )


def run_night_audit(
    session: Session,
    *,
    organization_id: uuid.UUID,
    property_id: uuid.UUID,
    business_date: date,
    nightly_charges: list[NightlyCharge] | None = None,
    allow_open_shifts: bool = False,
    #: Who is closing the day. None means the scheduler ran it unattended,
    #: which is a different answer from "a person did this" -- and the one
    #: question an audit trail has to be able to answer about a sealed day.
    run_by: uuid.UUID | None = None,
    actor_subject: str | None = None,
) -> AuditResult:
    """Run (or safely rerun) the night audit for a business date.

    ``nightly_charges`` is derived from who is in the house unless the caller
    passes its own list, which only tests and manual repairs do.

    Idempotency:
      - Each night posts under ``room_night:{business_date}:{unit_id}``, so a
        rerun posts once.
      - A completed run is blocked by the partial unique index; if the day is
        already closed we short-circuit.
    """
    # Refuse a day that has not happened. Checked here rather than in the
    # route so every caller inherits it -- the scheduler already declined to
    # run ahead of today, and the guard belonged next to the thing it protects
    # rather than in one of the two callers.
    reason = future_day_reason(
        business_date,
        local_today(session, property_id),
        local_now(session, property_id),
        audit_hour_of(session, property_id),
    )
    if reason is not None:
        raise NightAuditError(reason)

    # An open till stops the audit, as it does in the systems hotels actually
    # run on. It is not ceremony: the shift's cash is counted against the day
    # it belongs to, and a drawer spanning the date roll leaves that count
    # straddling two days, so the banking never squares.
    #
    # It stops it -- it does not forbid it. `allow_open_shifts` is a deliberate
    # override, recorded with the names of the drawers it was taken over,
    # because a day that cannot close is worse than one that closes with a
    # noted exception: the revenue stays unposted and tomorrow there are two
    # open days instead of one.
    blocking_shifts = find_open_shifts(
        session, property_id=property_id, business_date=business_date)
    if blocking_shifts and not allow_open_shifts:
        raise NightAuditError(open_shift_block(blocking_shifts))

    # Ensure the business day row exists and lock it.
    session.execute(
        text(
            """
            INSERT INTO finance.business_days
                (organization_id, property_id, business_date, status)
            VALUES (:org, :prop, :bd, 'open')
            ON CONFLICT (property_id, business_date) DO NOTHING
            """
        ),
        {"org": organization_id, "prop": property_id, "bd": business_date},
    )
    day = session.execute(
        text(
            """
            SELECT status FROM finance.business_days
            WHERE property_id = :prop AND business_date = :bd
            FOR UPDATE
            """
        ),
        {"prop": property_id, "bd": business_date},
    ).first()

    next_date = business_date + timedelta(days=1)

    derived_warnings: list[str] = []
    if nightly_charges is None:
        nightly_charges, derived_warnings = derive_nightly_charges(
            session,
            organization_id=organization_id,
            property_id=property_id,
            business_date=business_date,
        )

    if day.status == "closed":
        # Already closed: return the existing completed run idempotently.
        existing = session.execute(
            text(
                """
                SELECT id FROM finance.night_audit_runs
                WHERE property_id = :prop AND business_date = :bd
                  AND status = 'completed'
                ORDER BY started_at DESC LIMIT 1
                """
            ),
            {"prop": property_id, "bd": business_date},
        ).first()
        posted_now = 0
        # Even on rerun, ensure charges exist (idempotent post_charge).
        for ch in nightly_charges:
            res = post_charge(
                session,
                organization_id=organization_id,
                property_id=property_id,
                folio_id=ch.folio_id,
                amount=ch.amount,
                business_date=business_date,
                source_type="room_night",
                # A room night is taxed as accommodation.
                tax_category="rooms",
                source_line_key=_line_key(business_date, ch),
                charge_code_id=ch.charge_code_id,
            )
            if res.created:
                posted_now += 1
        return AuditResult(
            run_id=existing.id if existing else uuid.uuid4(),
            business_date=business_date,
            charges_posted=posted_now,
            next_business_date=next_date,
            steps=["already_closed"],
            rooms_charged=len(nightly_charges),
            amount_charged=sum(
                (c.amount for c in nightly_charges), Decimal("0")),
            warnings=derived_warnings,
        )

    # Move to closing and open a run.
    session.execute(
        text(
            "UPDATE finance.business_days SET status = 'closing' "
            "WHERE property_id = :prop AND business_date = :bd"
        ),
        {"prop": property_id, "bd": business_date},
    )
    run_id = uuid.uuid4()
    run_number = session.execute(
        text(
            "SELECT coalesce(max(run_number),0)+1 FROM finance.night_audit_runs "
            "WHERE property_id = :prop AND business_date = :bd"
        ),
        {"prop": property_id, "bd": business_date},
    ).scalar_one()
    session.execute(
        text(
            """
            INSERT INTO finance.night_audit_runs
                (id, organization_id, property_id, business_date, run_number,
                 status, run_by)
            VALUES (:id, :org, :prop, :bd, :rn, 'running', :by)
            """
        ),
        {
            "id": run_id,
            "org": organization_id,
            "prop": property_id,
            "bd": business_date,
            "rn": run_number,
            "by": run_by,
        },
    )

    steps: list[str] = []

    def _step(
        code: str,
        ok: bool = True,
        error: str | None = None,
        detail: dict | None = None,
    ) -> None:
        session.execute(
            text(
                """
                INSERT INTO finance.night_audit_steps
                    (run_id, step_code, status, error, detail)
                VALUES (:rid, :code, :status, :error, CAST(:detail AS jsonb))
                """
            ),
            {
                "rid": run_id,
                "code": code,
                "status": "completed" if ok else "failed",
                "error": error,
                "detail": json.dumps(detail) if detail else None,
            },
        )
        steps.append(code)

    # Step: post the night's room charges (idempotent per room/night).
    charges_posted = 0
    for ch in nightly_charges:
        res = post_charge(
            session,
            organization_id=organization_id,
            property_id=property_id,
            folio_id=ch.folio_id,
            amount=ch.amount,
            business_date=business_date,
            source_type="room_night",
            tax_category="rooms",
            source_line_key=_line_key(business_date, ch),
            charge_code_id=ch.charge_code_id,
        )
        if res.created:
            charges_posted += 1
    amount_charged = sum((c.amount for c in nightly_charges), Decimal("0"))
    _step(
        "post_room_charges",
        detail={
            "rooms": len(nightly_charges),
            "posted": charges_posted,
            "amount": str(amount_charged),
            # Recorded, not re-derived later: a report opened in March must
            # say what was billed in September, not what today's bookings
            # would imply about that night after months of amendments.
            "lines": [
                {
                    "room": c.room,
                    "guest": c.guest,
                    "room_type": c.room_type,
                    "reservation_number": c.reservation_number,
                    "amount": str(c.amount),
                }
                for c in nightly_charges
            ],
        },
    )

    # Step: resolve the arrivals that never came.
    # normalise, not `or`: it maps the old "first_night" rows onto the basis
    # they were actually charged at, and turns anything unrecognised into the
    # safe default rather than into "charge something".
    basis = no_show.normalise(session.execute(
        text("SELECT no_show_penalty FROM finance.night_audit_settings "
             "WHERE property_id = :p"),
        {"p": property_id},
    ).scalar())
    no_shows, no_show_charged, no_show_warnings = process_no_shows(
        session, organization_id=organization_id, property_id=property_id,
        business_date=business_date, basis=basis)
    derived_warnings.extend(no_show_warnings)
    _step(
        "flag_no_shows",
        detail={
            "count": len(no_shows),
            "basis": basis,
            "charged": str(no_show_charged),
            "bookings": [p.reservation_number for p in no_shows],
        },
    )

    overstays = find_overstays(
        session, property_id=property_id, business_date=business_date)
    _step("flag_overstays", detail={"count": len(overstays)})

    # Step: keep the booking window from closing in on itself.
    horizon_added = extend_inventory_horizon(
        session, property_id=property_id, business_date=business_date)
    _step(
        "extend_inventory_horizon",
        detail={"days_added": horizon_added,
                "horizon_days": INVENTORY_HORIZON_DAYS},
    )

    # Step: a day should not close over an uncounted drawer. If it did, the
    # record says whose, so the report cannot be read as a clean close.
    open_shifts = len(blocking_shifts)
    warnings = list(derived_warnings)
    if open_shifts:
        warnings.append(
            f"{open_shifts} cashier shift(s) were still open when the day "
            f"closed — the drawer was never declared."
        )
    _step(
        "reconcile_cashiering",
        detail={
            "open_shifts": open_shifts,
            "overridden": bool(open_shifts),
            "cashiers": [sh.cashier for sh in blocking_shifts],
        },
    )

    # Step: photograph the house before sealing it.
    occupancy = occupancy_snapshot(
        session, property_id=property_id, business_date=business_date)
    _step("snapshot_occupancy", detail=occupancy)

    # Step: close the day and complete the run.
    session.execute(
        text(
            "UPDATE finance.business_days SET status = 'closed', "
            "closed_at = now() WHERE property_id = :prop AND business_date = :bd"
        ),
        {"prop": property_id, "bd": business_date},
    )
    _step("close_business_day")
    session.execute(
        text(
            "UPDATE finance.night_audit_runs SET status = 'completed', "
            "completed_at = now() WHERE id = :id"
        ),
        {"id": run_id},
    )

    # The trail, separate from the run row: closing a day is a financial act,
    # and an override of an uncounted till is one somebody has to own.
    record_audit(
        session,
        action="night_audit.closed",
        entity_type="business_day",
        entity_id=str(business_date),
        organization_id=organization_id,
        property_id=property_id,
        actor_subject=actor_subject or "system:night-audit",
        after={
            "run_id": str(run_id),
            "rooms_charged": len(nightly_charges),
            "amount_charged": str(amount_charged),
            "no_shows": len(no_shows),
            "overstays": len(overstays),
            "open_shifts_overridden": open_shifts,
        },
        reason=(open_shift_block(blocking_shifts) if open_shifts else None),
    )

    # Advance: ensure the next business day is open.
    session.execute(
        text(
            """
            INSERT INTO finance.business_days
                (organization_id, property_id, business_date, status)
            VALUES (:org, :prop, :nd, 'open')
            ON CONFLICT (property_id, business_date) DO NOTHING
            """
        ),
        {"org": organization_id, "prop": property_id, "nd": next_date},
    )

    return AuditResult(
        run_id=run_id,
        business_date=business_date,
        charges_posted=charges_posted,
        next_business_date=next_date,
        steps=steps,
        rooms_charged=len(nightly_charges),
        amount_charged=amount_charged,
        no_shows=no_shows,
        overstays=overstays,
        horizon_days_added=horizon_added,
        open_shifts=open_shifts,
        warnings=warnings,
    )
