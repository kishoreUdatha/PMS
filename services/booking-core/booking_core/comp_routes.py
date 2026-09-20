"""Declaring a room complimentary, or taken for the hotel's own use.

The Complimentary Room Report existed before this did, and inferred its rows
from a zero rate, a fully adjusted-off folio, or a comp upgrade. That found
most comps and could explain none of them: a zero rate is equally a rate
nobody typed, and a room booked free from the start carried no approver even
though giving a room away is exactly the decision worth attributing.

So it is declared here instead, and the report reads the declaration.

Two kinds, because they are different facts that were previously forced into
one shape:

``complimentary``
    A guest stays and is not charged -- a VIP, an agent on a FAM trip, the
    tour leader on a group, or putting right a bad stay. Revenue given away.

``house_use``
    The hotel itself occupies the room: maintenance, an office, staff, a show
    room. Not revenue given away, because there was never a guest to charge --
    it is inventory taken off sale. Previously this had to be faked as a
    zero-rate booking and then appeared in the report as a comp *guest*.

What this does **not** do is zero the folio. Marking a stay complimentary and
removing what has already been posted are separate acts with separate
approvals: room charges already on a folio are reversed through a folio
adjustment, which is approved and leaves a trail. Doing both from one button
would let someone erase posted revenue without the approval that path
requires. The flag governs what is charged from here on and how the stay is
reported; it never rewrites the ledger behind it.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from chirala_common.audit import record_audit
from chirala_common.authz import Caller, assert_property_in_org, build_authz
from chirala_common.routing import TransactionalRoute
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.orm import Session

from .database import get_session
from .settings import settings

_get_caller, require_permission, _require_org = build_authz(
    get_session, lambda: settings.environment,
    lambda: settings.service_token,
)

comp_router = APIRouter(tags=["complimentary"], route_class=TransactionalRoute)

KINDS = ("complimentary", "house_use")

#: Kept in step with REASONS in migration 0053. Reporting categories, not a
#: property's own vocabulary -- a revenue manager compares these across
#: properties and periods, and free text would make that impossible.
COMP_REASONS: dict[str, str] = {
    "vip": "VIP guest",
    "fam_trip": "Agent familiarisation trip",
    "tour_leader": "Tour leader on a group",
    "service_recovery": "Putting right a bad stay",
    "owner": "Owner or management",
    "marketing": "Marketing or influencer",
    "loyalty": "Loyalty redemption",
    "other": "Other",
}
HOUSE_REASONS: dict[str, str] = {
    "maintenance": "Maintenance or repair",
    "office": "Office or back-of-house",
    "staff_accommodation": "Staff accommodation",
    "show_room": "Show room",
    "other": "Other",
}


class CompIn(BaseModel):
    kind: str
    reason: str
    note: str | None = Field(default=None, max_length=300)


class CompOut(BaseModel):
    reservation_unit_id: uuid.UUID
    comp_kind: str | None
    comp_reason: str | None
    comp_reason_label: str | None
    comp_note: str | None
    comp_authorised_by: uuid.UUID | None
    comp_authorised_at: datetime | None


class ReasonOption(BaseModel):
    value: str
    label: str


class CompOptions(BaseModel):
    complimentary: list[ReasonOption]
    house_use: list[ReasonOption]


def _label(kind: str | None, reason: str | None) -> str | None:
    if not reason:
        return None
    table = HOUSE_REASONS if kind == "house_use" else COMP_REASONS
    return table.get(reason, reason)


def _row(db: Session, unit_id: uuid.UUID) -> CompOut:
    r = db.execute(
        text(
            """
            SELECT id, comp_kind, comp_reason, comp_note,
                   comp_authorised_by, comp_authorised_at
              FROM booking.reservation_units WHERE id = :id
            """
        ),
        {"id": unit_id},
    ).mappings().one()
    return CompOut(
        reservation_unit_id=r["id"], comp_kind=r["comp_kind"],
        comp_reason=r["comp_reason"],
        comp_reason_label=_label(r["comp_kind"], r["comp_reason"]),
        comp_note=r["comp_note"],
        comp_authorised_by=r["comp_authorised_by"],
        comp_authorised_at=r["comp_authorised_at"],
    )


@comp_router.get("/complimentary/reasons", response_model=CompOptions)
def reasons(caller: Caller = Depends(require_permission("front_desk", "view"))):
    """The reasons a room may be given away or taken in house.

    Served rather than written into the client, so the list, the constraint in
    the database and the report's grouping cannot drift apart.
    """
    return CompOptions(
        complimentary=[ReasonOption(value=k, label=v)
                       for k, v in COMP_REASONS.items()],
        house_use=[ReasonOption(value=k, label=v)
                   for k, v in HOUSE_REASONS.items()],
    )


@comp_router.get("/reservation-units/{unit_id}/complimentary",
                 response_model=CompOut)
def get_complimentary(
    unit_id: uuid.UUID,
    property_id: uuid.UUID,
    caller: Caller = Depends(require_permission("front_desk", "view")),
    db: Session = Depends(get_session),
):
    """What this room is currently declared as, if anything.

    Without this the dialog opened blank on a room already given away: the
    desk could not see the reason, the note, or whose decision it was -- which
    is the accountability the declaration exists to provide. Worse, it made a
    second mark look like a first one.
    """
    assert_property_in_org(db, caller, property_id)
    exists = db.execute(
        text("SELECT 1 FROM booking.reservation_units"
             " WHERE id = :id AND property_id = :prop"),
        {"id": unit_id, "prop": property_id},
    ).first()
    if not exists:
        raise HTTPException(404, "No such room on this booking.")
    return _row(db, unit_id)


@comp_router.post("/reservation-units/{unit_id}/complimentary",
                  response_model=CompOut, status_code=status.HTTP_200_OK)
def mark_complimentary(
    unit_id: uuid.UUID,
    property_id: uuid.UUID,
    body: CompIn,
    # Giving a room away is a decision, not a correction: it needs the
    # permission that changes a booking, not the one that reads it.
    caller: Caller = Depends(require_permission("front_desk", "edit")),
    db: Session = Depends(get_session),
):
    """Declare this room complimentary, or taken for the hotel's own use."""
    assert_property_in_org(db, caller, property_id)
    if body.kind not in KINDS:
        raise HTTPException(422, f"Unknown kind '{body.kind}'.")
    table = HOUSE_REASONS if body.kind == "house_use" else COMP_REASONS
    if body.reason not in table:
        raise HTTPException(
            422, f"'{body.reason}' is not a reason for {body.kind}. "
                 f"Use one of: {', '.join(table)}.")
    # "Other" with nothing written down is the row a revenue manager cannot
    # act on, which is the whole reason this is being recorded.
    if body.reason == "other" and not (body.note or "").strip():
        raise HTTPException(
            422, "Choose a reason, or say what it was in the note.")

    done = db.execute(
        text(
            """
            UPDATE booking.reservation_units
               SET comp_kind = :kind, comp_reason = :reason,
                   comp_note = :note,
                   comp_authorised_by = :actor,
                   comp_authorised_at = now(),
                   updated_at = now()
             WHERE id = :id AND property_id = :prop
               AND status NOT IN ('cancelled', 'no_show')
            """
        ),
        {"id": unit_id, "prop": property_id, "kind": body.kind,
         "reason": body.reason, "note": (body.note or "").strip() or None,
         "actor": caller.user_id},
    ).rowcount
    if not done:
        raise HTTPException(
            404, "No such room on this booking, or it is cancelled.")

    record_audit(
        db, actor_subject=caller.subject, action="reservation_unit.complimentary",
        entity_type="reservation_unit", entity_id=str(unit_id),
        property_id=property_id,
        after={"kind": body.kind, "reason": body.reason, "note": body.note},
    )
    return _row(db, unit_id)


@comp_router.delete("/reservation-units/{unit_id}/complimentary",
                    response_model=CompOut)
def clear_complimentary(
    unit_id: uuid.UUID,
    property_id: uuid.UUID,
    caller: Caller = Depends(require_permission("front_desk", "edit")),
    db: Session = Depends(get_session),
):
    """Put the room back on normal terms.

    Does not re-post anything. Nights already waived stay waived — what was
    not charged was not charged, and inventing the charge now would bill a
    guest for a night somebody told them was free.
    """
    assert_property_in_org(db, caller, property_id)
    # ``AND comp_kind IS NOT NULL`` matters: without it the UPDATE matched a
    # room that was on normal terms all along, wrote nulls over nulls, and
    # still recorded "complimentary_cleared" -- an audit trail claiming a comp
    # was withdrawn from a room that never had one.
    done = db.execute(
        text(
            """
            UPDATE booking.reservation_units
               SET comp_kind = NULL, comp_reason = NULL, comp_note = NULL,
                   comp_authorised_by = NULL, comp_authorised_at = NULL,
                   updated_at = now()
             WHERE id = :id AND property_id = :prop
               AND comp_kind IS NOT NULL
            """
        ),
        {"id": unit_id, "prop": property_id},
    ).rowcount
    if not done:
        # Already on normal terms is the asked-for state, not an error -- but
        # it is not a change, so nothing is recorded.
        exists = db.execute(
            text("SELECT 1 FROM booking.reservation_units"
                 " WHERE id = :id AND property_id = :prop"),
            {"id": unit_id, "prop": property_id},
        ).first()
        if not exists:
            raise HTTPException(404, "No such room on this booking.")
        return _row(db, unit_id)
    record_audit(
        db, actor_subject=caller.subject,
        action="reservation_unit.complimentary_cleared",
        entity_type="reservation_unit", entity_id=str(unit_id),
        property_id=property_id,
    )
    return _row(db, unit_id)
