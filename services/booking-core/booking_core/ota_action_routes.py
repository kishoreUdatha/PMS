"""The list of things the hotel owes a channel, and the clock on each.

A no-show on an OTA booking has to be reported to that OTA within 24 hours or
the hotel pays commission on a room nobody slept in. That obligation used to
live nowhere: the clerk marked the no-show, the room went back on sale, and
the commission quietly stayed owed until a monthly statement said so — by
which time the window had closed on every one of them.

This is the screen that makes it visible and the record that makes it
answerable. Reporting is still a person going to an extranet, deliberately:
whether a channel manager can report a no-show depends on the channel and on
the plan, and a queue that silently fails to send is worse than one somebody
works through. If an API is wired up later it closes these same rows.

Closing an obligation asks for the reference the OTA gave back, because a
waiver nobody can evidence is a waiver the hotel argues about later and loses.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from chirala_common import ota_actions
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

ota_router = APIRouter(tags=["ota actions"], route_class=TransactionalRoute)


class OtaActionOut(BaseModel):
    id: uuid.UUID
    reservation_id: uuid.UUID | None
    reservation_unit_id: uuid.UUID
    reservation_number: str | None
    ota_name: str | None
    ota_reservation_code: str | None
    action_type: str
    action_label: str
    status: str
    due_at: datetime
    #: Negative once the window has closed. Served rather than worked out in
    #: the browser so every viewer sees the same number against the same
    #: server clock — a deadline that depends on the reader's laptop is not a
    #: deadline.
    hours_left: float
    reported_at: datetime | None
    reference: str | None
    note: str | None


class OtaSummary(BaseModel):
    rows: list[OtaActionOut]
    open_count: int
    overdue_count: int
    window_hours: int


def _rows(db: Session, property_id: uuid.UUID,
          status: str | None) -> list[OtaActionOut]:
    rs = db.execute(
        text(
            """
            SELECT id, reservation_id, reservation_unit_id,
                   reservation_number, ota_name, ota_reservation_code,
                   action_type, status, due_at, reported_at, reference, note,
                   EXTRACT(EPOCH FROM (due_at - now())) / 3600.0 AS hours_left
              FROM distribution.ota_actions
             WHERE property_id = :p
               AND (CAST(:st AS text) IS NULL OR status = CAST(:st AS text))
             ORDER BY status = 'open' DESC, due_at
             LIMIT 500
            """
        ),
        {"p": property_id, "st": status},
    ).mappings().all()
    return [
        OtaActionOut(
            **{k: v for k, v in r.items() if k != "hours_left"},
            action_label=ota_actions.LABELS.get(r["action_type"],
                                                r["action_type"]),
            hours_left=float(r["hours_left"]),
        )
        for r in rs
    ]


@ota_router.get("/properties/{property_id}/ota-actions",
                response_model=OtaSummary)
def list_actions(
    property_id: uuid.UUID,
    status: str | None = Query(default=None),
    caller: Caller = Depends(require_permission("front_desk", "view")),
    db: Session = Depends(get_session),
):
    """What is owed to channels, soonest deadline first."""
    assert_property_in_org(db, caller, property_id)
    rows = _rows(db, property_id, status)
    counts = db.execute(
        text(
            """
            SELECT count(*) FILTER (WHERE status = 'open') AS open_count,
                   count(*) FILTER (WHERE status = 'open'
                                      AND due_at < now()) AS overdue_count
              FROM distribution.ota_actions WHERE property_id = :p
            """
        ),
        {"p": property_id},
    ).mappings().one()
    return OtaSummary(rows=rows, open_count=counts["open_count"],
                      overdue_count=counts["overdue_count"],
                      window_hours=ota_actions.WINDOW_HOURS)


class CloseIn(BaseModel):
    #: ``reported`` means it was done. ``dismissed`` means it did not need
    #: doing — the channel had already cancelled it, or it was never really
    #: theirs. Kept apart because a dismissed row is not evidence of anything
    #: and must not be counted as if it were.
    status: str = "reported"
    reference: str | None = Field(default=None, max_length=160)
    note: str | None = Field(default=None, max_length=400)


@ota_router.post("/ota-actions/{action_id}", response_model=OtaActionOut)
def close_action(
    action_id: uuid.UUID,
    property_id: uuid.UUID,
    body: CloseIn,
    caller: Caller = Depends(require_permission("front_desk", "edit")),
    db: Session = Depends(get_session),
):
    """Say the channel was told, and what they said back."""
    assert_property_in_org(db, caller, property_id)
    if body.status not in ("reported", "dismissed"):
        raise HTTPException(422, "An obligation is either reported or dismissed.")
    # Saying it was reported without saying what came back leaves the hotel
    # with nothing to show if the commission is billed anyway.
    if body.status == "dismissed" and not (body.note or "").strip():
        raise HTTPException(
            422, "Say why this did not need reporting — a dismissed row with "
                 "no reason cannot be checked later.")

    done = db.execute(
        text(
            """
            UPDATE distribution.ota_actions
               SET status = :st, reported_at = now(), reported_by = :who,
                   reference = :ref, note = :note,
                   updated_at = now(), version = version + 1
             WHERE id = :id AND property_id = :p AND status = 'open'
            """
        ),
        {"id": action_id, "p": property_id, "st": body.status,
         "who": caller.user_id,
         "ref": (body.reference or "").strip() or None,
         "note": (body.note or "").strip() or None},
    ).rowcount
    if not done:
        raise HTTPException(
            404, "No such open obligation — it may already be closed.")

    record_audit(
        db, actor_subject=caller.subject, action=f"ota_action.{body.status}",
        entity_type="ota_action", entity_id=str(action_id),
        property_id=property_id,
        after={"reference": body.reference, "note": body.note},
    )
    row = db.execute(
        text(
            """
            SELECT id, reservation_id, reservation_unit_id,
                   reservation_number, ota_name, ota_reservation_code,
                   action_type, status, due_at, reported_at, reference, note,
                   EXTRACT(EPOCH FROM (due_at - now())) / 3600.0 AS hours_left
              FROM distribution.ota_actions WHERE id = :id
            """
        ),
        {"id": action_id},
    ).mappings().one()
    return OtaActionOut(
        **{k: v for k, v in row.items() if k != "hours_left"},
        action_label=ota_actions.LABELS.get(row["action_type"],
                                            row["action_type"]),
        hours_left=float(row["hours_left"]),
    )
