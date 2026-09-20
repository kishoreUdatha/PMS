"""Getting a new property from nothing to open.

Ten steps, and the only two facts worth storing are where the operator got to
and whether the property has gone live. Everything else -- whether a step is
*done* -- is counted from the property's own data each time it is asked.

That is the whole design decision here. A stored "rooms: complete" flag and an
empty room list disagree the moment somebody deletes a room, and it is always
the flag that lies. Counting is a few cheap queries and cannot drift.

It also means the checklist is honest in both directions: finish the rooms step
by hand outside the wizard and it ticks itself; delete every room afterwards
and it un-ticks.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

import json

import httpx

from chirala_common.objectstore import (
    ObjectStoreConfig,
    ObjectStoreError,
    build_key,
    delete_object,
    presigned_url,
    put_object,
)
from chirala_common.india import normalise_state
from chirala_common.postal import problem as postal_problem
from chirala_common.routing import TransactionalRoute
from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel
from sqlalchemy import text
from smtplib import SMTPException

from chirala_common.delivery_log import record_delivery
from chirala_common.mailer import MailNotConfigured, send as send_mail
from sqlalchemy.orm import Session

from .audit import record_audit
from .auth_routes import issue_password_token
from .authz import Caller, require_permission, assert_property_in_org
from .database import SessionFactory, get_session
from .mail import property_live_email, welcome_email
from .settings import settings

onboarding_router = APIRouter(tags=["onboarding"], route_class=TransactionalRoute)

# The order is the product, so it lives in one list rather than being implied
# by whatever order the queries happen to run in.
STEPS: tuple[tuple[str, str], ...] = (
    ("account", "Account"),
    ("property", "Property"),
    ("structure", "Structure"),
    ("rooms", "Rooms"),
    ("rates", "Rates & Policies"),
    ("billing", "Billing"),
    ("team", "Team"),
    ("import", "Import Bookings"),
    ("connections", "Connections"),
    ("golive", "Go Live"),
)
STEP_KEYS = {k for k, _ in STEPS}

# Steps a property can open its doors without. Connections is genuinely
# optional; import is optional because a brand-new property has nothing to
# import.
OPTIONAL = {"import", "connections"}


class StepState(BaseModel):
    key: str
    label: str
    #: Counted from the property's data, never stored.
    complete: bool
    optional: bool
    visited: bool
    skipped: bool
    #: What is missing, in words, when it is not complete.
    blocker: str | None = None


class Counts(BaseModel):
    rooms: int
    room_types: int
    staff_invited: int
    bookings: int


class OnboardingState(BaseModel):
    property_id: uuid.UUID
    property_name: str
    property_city: str | None = None
    property_state: str | None = None
    currency: str
    timezone: str
    current_step: str
    steps: list[StepState]
    counts: Counts
    #: True once every non-optional step is complete.
    ready_to_activate: bool
    activated_at: datetime | None = None


class StepPatch(BaseModel):
    current_step: str | None = None
    visited: str | None = None
    skipped: str | None = None


def _row(db: Session, property_id: uuid.UUID):
    row = db.execute(
        text("SELECT * FROM iam.property_onboarding WHERE property_id = :p"),
        {"p": property_id},
    ).mappings().first()
    if row is None:
        # A property created before onboarding existed, or by a path that does
        # not open one. Start it rather than 404 -- the wizard is a view over
        # the property, not a thing the property belongs to.
        db.execute(
            text(
                """
                INSERT INTO iam.property_onboarding
                    (property_id, organization_id, current_step)
                SELECT id, organization_id, 'account' FROM iam.properties
                WHERE id = :p
                ON CONFLICT (property_id) DO NOTHING
                """
            ),
            {"p": property_id},
        )
        row = db.execute(
            text("SELECT * FROM iam.property_onboarding WHERE property_id = :p"),
            {"p": property_id},
        ).mappings().first()
        if row is None:
            raise HTTPException(status_code=404, detail="Property not found")
    return row


def _facts(db: Session, property_id: uuid.UUID) -> dict:
    """Everything the checklist is derived from, in one pass."""
    return dict(
        db.execute(
            text(
                """
                SELECT
                  (SELECT count(*) FROM property.buildings b
                    WHERE b.property_id = :p)                       AS buildings,
                  (SELECT count(*) FROM property.floors f
                    WHERE f.property_id = :p)                       AS floors,
                  -- Deactivated types are excluded throughout. A type that
                  -- cannot be sold does not need a price, and since there is
                  -- no hard delete for a room type, counting them meant one
                  -- created by mistake blocked the wizard for good.
                  (SELECT count(*) FROM property.room_types rt
                    WHERE rt.property_id = :p
                      AND rt.status = 'active')                     AS room_types,
                  (SELECT count(*) FROM property.rooms rm
                    WHERE rm.property_id = :p AND rm.status = 'active') AS rooms,
                  (SELECT count(*) FROM property.room_types rt
                    WHERE rt.property_id = :p AND rt.status = 'active'
                      AND COALESCE(rt.base_rate, 0) > 0)            AS priced_types,
                  (SELECT count(*) FROM iam.invitations i
                    WHERE i.property_id = :p)                       AS invites,
                  (SELECT count(*) FROM iam.memberships m
                    JOIN iam.properties pr ON pr.organization_id = m.organization_id
                    WHERE pr.id = :p)                               AS members,
                  (SELECT count(*) FROM booking.reservations r
                    WHERE r.property_id = :p)                       AS bookings,
                  -- Named, not counted. "1 room type(s) have no rate" makes
                  -- the operator go and find which one; the name sends them
                  -- straight to it.
                  (SELECT array_agg(rt.name ORDER BY rt.name)
                     FROM property.room_types rt
                    WHERE rt.property_id = :p AND rt.status = 'active'
                      AND COALESCE(rt.base_rate, 0) = 0)            AS unpriced
                """
            ),
            {"p": property_id},
        ).mappings().first()
    )


def _no_rate_reason(names: list[str]) -> str:
    """Say which room types still need a price.

    Listed by name up to three, then counted, because a property with twelve
    unpriced types does not want twelve names in a sentence -- but one with a
    single "Suite" wants to be told it is Suite.
    """
    if not names:
        return "No room types yet"
    if len(names) == 1:
        return f"{names[0]} has no rate"
    if len(names) <= 3:
        return f"{', '.join(names[:-1])} and {names[-1]} have no rate"
    return f"{names[0]}, {names[1]} and {len(names) - 2} more have no rate"


def _reachable(db: Session, property_id: uuid.UUID) -> tuple[bool, str | None]:
    """Can anything this property sends actually arrive?

    Every unattended message -- the night audit report, the welcome mail, a
    guest confirmation -- is addressed about this property. Without an address
    they are composed, handed to the mailer and dropped, and the only symptom
    is that nobody mentions receiving them. Kalpataru Residency has run that
    way since it went live.
    """
    row = db.execute(
        text("SELECT contact_email FROM iam.properties WHERE id = :p"),
        {"p": property_id},
    ).mappings().first()
    email = (row or {}).get("contact_email")
    if not email or "@" not in str(email):
        return False, "No contact email — audit reports and guest mail have nowhere to go"
    return True, None


def _audit_hour_set(db: Session, property_id: uuid.UUID) -> tuple[bool, str | None]:
    """Has the property said when its day ends?

    The audit closes the business day, and which hour it closes at decides
    which date every transaction after it lands on. Left unset it takes the
    deployment default, which is a guess about someone else's hotel.
    """
    row = db.execute(
        text("SELECT audit_hour FROM finance.night_audit_settings "
             "WHERE property_id = :p"),
        {"p": property_id},
    ).mappings().first()
    if row is None or row["audit_hour"] is None:
        return False, "Night audit hour not set — the day would close at the deployment default"
    return True, None


def _refunds_approvable(db: Session, organization_id) -> tuple[bool, str | None]:
    """Is there anybody who can approve a refund?

    With no policy for the category every refund is routed for a manual
    decision and no approver role is named, so requests queue and nothing
    clears them. Advisory: requiring a manager for every refund is a defensible
    policy, and an organisation that has decided that should not be stopped.
    """
    n = db.execute(
        text("SELECT count(*) FROM iam.approval_policies "
             "WHERE organization_id = :o AND category = 'refund' AND active"),
        {"o": organization_id},
    ).scalar_one()
    if not n:
        return False, ("No refund approval policy — every refund will wait for a "
                       "manual decision with no approver named")
    return True, None


def _billing_ready(db: Session, property_id: uuid.UUID) -> tuple[bool, str | None]:
    """A property cannot bill without a legal identity to bill under.

    The GSTIN is required only of a property that says it is registered. It
    used to be required of everyone, which left a property genuinely outside
    GST unable to finish the step at all -- there was no number it could give.

    "Not answered yet" is kept distinct from "no". An unanswered registration
    leaves the step open and asks the question, rather than assuming the more
    convenient answer and letting an unregistered-looking property through.

    The place of supply stays required either way: it decides CGST+SGST
    against IGST, and an invoice cannot be worked out without it.
    """
    row = db.execute(
        text("SELECT legal_name, address_line, state, state_code, "
             "       gst_registered, gstin "
             "FROM finance.invoice_settings WHERE property_id = :p"),
        {"p": property_id},
    ).mappings().first()
    if row is None:
        return False, "No invoice settings yet"

    missing = [label for field, label in (
        ("legal_name", "legal name"), ("address_line", "address"),
        ("state", "state"), ("state_code", "state code"),
    ) if not (row[field] or "").strip()]

    if row["gst_registered"] is None:
        missing.append("whether you are GST registered")
    elif row["gst_registered"] and not (row["gstin"] or "").strip():
        missing.append("GSTIN")

    if missing:
        return False, "Missing " + ", ".join(missing)
    return True, None


def _build(db: Session, row, caller: Caller) -> OnboardingState:
    prop = db.execute(
        text("SELECT id, name, address, currency, timezone "
             "FROM iam.properties WHERE id = :p"),
        {"p": row["property_id"]},
    ).mappings().first()
    f = _facts(db, row["property_id"])
    billing_ok, billing_why = _billing_ready(db, row["property_id"])
    # Operational facts nothing used to ask for. A property can have rooms,
    # rates and a GSTIN and still be unable to tell anyone anything.
    reachable_ok, reachable_why = _reachable(db, row["property_id"])
    audit_ok, audit_why = _audit_hour_set(db, row["property_id"])
    refunds_ok, refunds_why = _refunds_approvable(db, caller.organization_id)
    marks = row["steps"] or {}

    # Each step says what would make it complete, in the property's own terms.
    checks: dict[str, tuple[bool, str | None]] = {
        "account": (True, None),
        "property": (
            bool(prop and prop["name"] and prop["currency"] and prop["timezone"])
            and reachable_ok and audit_ok,
            ("Property name, currency and timezone are needed"
             if not (prop and prop["name"] and prop["currency"] and prop["timezone"])
             else reachable_why or audit_why),
        ),
        "structure": (
            f["buildings"] > 0 or f["floors"] > 0,
            "No buildings or floors set up",
        ),
        "rooms": (
            f["rooms"] > 0 and f["room_types"] > 0,
            ("No room types yet" if f["room_types"] == 0
             else "No rooms yet" if f["rooms"] == 0 else None),
        ),
        "rates": (
            f["priced_types"] > 0 and f["priced_types"] == f["room_types"],
            _no_rate_reason(f["unpriced"] or []),
        ),
        "billing": (billing_ok, billing_why),
        "team": (f["members"] > 0 or f["invites"] > 0, "Nobody invited yet"),
        # Counting reservations was wrong: a booking taken at the front desk
        # is not an imported one, so a property that had never opened a
        # spreadsheet was told its import was complete. There is no import
        # facility yet either, so nothing here can be true. Optional, so it
        # holds nothing up — which is exactly why a false tick bought nothing
        # and only misled.
        "import": (False, "No bookings have been imported"),
        # Nothing in the system connects to a channel manager or serves a
        # booking engine yet, so this cannot report itself complete. Said
        # plainly rather than ticked because the step was clicked through.
        "connections": (False, "No booking connections are available yet"),
        "golive": (row["activated_at"] is not None, "Not activated"),
    }

    steps: list[StepState] = []
    for key, label in STEPS:
        done, why = checks[key]
        mark = marks.get(key) or {}
        steps.append(StepState(
            key=key, label=label, complete=done, optional=key in OPTIONAL,
            visited=bool(mark.get("visited")), skipped=bool(mark.get("skipped")),
            blocker=None if done else why,
        ))

    ready = all(s.complete for s in steps if not s.optional and s.key != "golive")

    # Advisory: said out loud on the Team step rather than blocking go-live,
    # because "every refund needs a manager" is a policy a property may have
    # chosen. Stated so it is a choice rather than an oversight discovered
    # the first time somebody tries to give money back.
    if not refunds_ok:
        for st in steps:
            if st.key == "team" and st.complete:
                st.blocker = refunds_why
                break

    return OnboardingState(
        property_id=row["property_id"],
        property_name=prop["name"] if prop else "",
        property_city=(prop["address"] if prop else None),
        currency=prop["currency"] if prop else "INR",
        timezone=prop["timezone"] if prop else "Asia/Kolkata",
        current_step=row["current_step"],
        steps=steps,
        counts=Counts(
            rooms=f["rooms"], room_types=f["room_types"],
            staff_invited=f["invites"] + f["members"], bookings=f["bookings"],
        ),
        ready_to_activate=ready,
        activated_at=row["activated_at"],
    )


@onboarding_router.get("/onboarding", response_model=OnboardingState)
def get_onboarding(
    property_id: uuid.UUID,
    caller: Caller = Depends(require_permission("property", "view")),
    db: Session = Depends(get_session),
):
    """Where this property is, and what is still missing before it can open."""
    assert_property_in_org(db, caller, property_id)
    return _build(db, _row(db, property_id), caller)


@onboarding_router.patch("/onboarding", response_model=OnboardingState)
def patch_onboarding(
    property_id: uuid.UUID,
    body: StepPatch,
    caller: Caller = Depends(require_permission("property", "update")),
    db: Session = Depends(get_session),
):
    """Record where the operator is, and which steps they passed over.

    Only navigation is written. Nothing here can mark a step complete -- that
    is counted from the data, so a skipped step stays visibly incomplete
    instead of being ticked by having been clicked through.
    """
    assert_property_in_org(db, caller, property_id)
    row = _row(db, property_id)
    marks = dict(row["steps"] or {})

    for value, flag in ((body.visited, "visited"), (body.skipped, "skipped")):
        if value is None:
            continue
        if value not in STEP_KEYS:
            raise HTTPException(status_code=422, detail=f"Unknown step '{value}'")
        marks[value] = {**(marks.get(value) or {}), flag: True}

    if body.current_step is not None and body.current_step not in STEP_KEYS:
        raise HTTPException(
            status_code=422, detail=f"Unknown step '{body.current_step}'")

    db.execute(
        text(
            """
            UPDATE iam.property_onboarding
            SET current_step = COALESCE(:step, current_step),
                steps = CAST(:marks AS jsonb),
                updated_at = now(), version = version + 1
            WHERE property_id = :p
            """
        ),
        {"step": body.current_step, "marks": json.dumps(marks),
         "p": property_id},
    )
    return _build(db, _row(db, property_id), caller)




#: How long a welcome link lasts. Long enough for somebody activating on a
#: Friday to still get in on Monday.
WELCOME_LINK_HOURS = 72


def _send_welcome_emails(
    db: Session, property_id: uuid.UUID
) -> tuple[int, list[str]]:
    """Tell everyone with access that the property is open, and how to get in.

    One link each, and never a password — see ``mail.py`` for why.

    A send that fails does not fail the activation. The property is live
    either way, and refusing to open the front desk because a mail server was
    unreachable would be the wrong trade; the addresses that did not get one
    are returned so the screen can name them and offer to try again.

    **Everybody with an address hears something.** People who already have a
    password get the details without a link -- they need no link, and one
    arriving unprompted is indistinguishable from a phishing attempt, but they
    do need the property code. It is generated by the server and shown nowhere
    they would think to look, and it is required at sign-in; skipping them
    meant the one person who completed onboarding was the one person who could
    not get back in.
    """
    prop = db.execute(
        text("SELECT name, code, organization_id FROM iam.properties "
             "WHERE id = :p"),
        {"p": property_id},
    ).mappings().first()
    if prop is None or not prop["code"]:
        return 0, []

    # Anyone the property has given access to, whether or not they have taken
    # it up yet. Requiring an *active* membership here was circular: a person
    # becomes active by accepting, and accepting is what this email is for —
    # so the only people who qualified were those who no longer needed it.
    people = db.execute(
        text(
            """
            SELECT DISTINCT u.id, u.email, u.display_name,
                   (c.user_id IS NOT NULL) AS has_password
            FROM iam.users u
            LEFT JOIN iam.memberships m
              ON m.user_id = u.id AND m.organization_id = :org
            LEFT JOIN iam.invitations i
              ON i.created_user_id = u.id AND i.property_id = :prop
            LEFT JOIN iam.user_credentials c ON c.user_id = u.id
            WHERE u.email IS NOT NULL AND btrim(u.email) <> ''
              AND u.status IN ('active', 'invited')
              AND (
                    (m.id IS NOT NULL AND m.status IN ('active', 'invited'))
                 OR (i.id IS NOT NULL AND i.status IN ('invited', 'accepted'))
              )
            """
        ),
        {"org": prop["organization_id"], "prop": property_id},
    ).mappings().all()

    sent = 0
    failed: list[str] = []
    for person in people:
        if person["has_password"]:
            subject, text_body, html_body = property_live_email(
                name=person["display_name"], property_name=prop["name"],
                property_code=prop["code"], email=person["email"],
            )
        else:
            token = issue_password_token(
                db, user_id=person["id"], purpose="welcome",
                hours=WELCOME_LINK_HOURS)
            subject, text_body, html_body = welcome_email(
                name=person["display_name"], property_name=prop["name"],
                property_code=prop["code"], email=person["email"],
                token=token, hours=WELCOME_LINK_HOURS,
            )
        try:
            send_mail(settings.mail_config, to=person["email"],
                      subject=subject, text=text_body, html=html_body)
            sent += 1
            record_delivery(SessionFactory, template_code="welcome",
                            recipient=person["email"], subject=subject,
                            organization_id=prop.get("organization_id"))
        except (MailNotConfigured, OSError, SMTPException) as exc:
            failed.append(person["email"])
            record_delivery(SessionFactory, template_code="welcome",
                            recipient=person["email"], status="failed",
                            organization_id=prop.get("organization_id"),
                            detail=str(exc)[:200] or "no mail server configured")
    return sent, failed


class WelcomeSendOut(BaseModel):
    sent: int
    #: Addresses the mail server would not take, so the screen can name them.
    failed: list[str]


@onboarding_router.post("/onboarding/resend-welcome",
                        response_model=WelcomeSendOut)
def resend_welcome(
    property_id: uuid.UUID,
    caller: Caller = Depends(require_permission("property", "update")),
    db: Session = Depends(get_session),
):
    """Send the welcome email again to everyone still waiting on one.

    Activation sends these once, which is not enough in practice: mail is
    filtered, addresses are mistyped, and the link expires after three days
    over a long weekend. Without this the only way to send another was to
    de-activate the property and activate it again, which rewrites the going
    live date to cover for a mail server.

    Issuing a new token retires the previous unused one, so the old link stops
    working. That is deliberate — two live "set your password" links in two
    copies of the same email is one more than anybody needs.

    Anyone who already has a password is skipped, so this cannot be used to
    push an unprompted reset at a colleague.
    """
    assert_property_in_org(db, caller, property_id)
    row = _row(db, property_id)
    if row["activated_at"] is None:
        raise HTTPException(
            status_code=409,
            detail="This property is not live yet. Welcome emails go out when "
                   "it is activated.",
        )

    sent, failed = _send_welcome_emails(db, property_id)
    record_audit(
        db, action="property.welcome.resent", entity_type="property",
        entity_id=str(property_id), organization_id=caller.organization_id,
        property_id=property_id, actor_subject=caller.subject,
        after={"sent": sent, "failed": failed},
    )
    return WelcomeSendOut(sent=sent, failed=failed)


def _provision_channel_manager(
    property_id: uuid.UUID, organization_id: uuid.UUID | None,
) -> tuple[bool, str]:
    """Set this property up at the channel manager, as it goes live.

    Over HTTP with the platform's own credential, because the channel manager
    belongs to booking-core -- the same way finance asks booking-core to
    confirm a reservation once money arrives. Reimplementing it here would put
    a second copy of the mapping rules in a service that does not own them.

    **Best effort, and deliberately so.** Going live is the moment a hotel
    starts taking money at its own front desk, and that must not be blocked
    because a third-party API is slow this afternoon. A failure here leaves a
    property that is live and not yet selling online, which the Sales Channels
    screen shows and its button re-runs; a failure that refused activation
    would leave a hotel that cannot check anybody in.
    """
    if not settings.service_token:
        return False, "no service credential configured"
    if organization_id is None:
        return False, "caller has no organization"
    try:
        resp = httpx.post(
            f"{settings.booking_url}/channel-links/provision",
            # Only if this hotel has actually connected an OTA. Going live
            # fires for every property, and most never sell through a
            # channel; creating each of them at the channel manager anyway is
            # a bill for listings nobody looks at. Booking-core decides --
            # it owns the tables that know -- and the moment a tenant does
            # connect one, its own sweep picks the property up.
            params={"property_id": str(property_id), "require_intent": "true"},
            headers={"X-Service-Token": settings.service_token,
                     "X-Service-Org": str(organization_id)},
            # Generous: provisioning is several round trips to the channel
            # manager -- a property, every room type, a rate plan each, a
            # webhook -- and nobody is waiting on this to finish going live.
            timeout=90.0,
        )
    except httpx.HTTPError as exc:
        return False, str(exc)
    if resp.status_code >= 400:
        return False, f"{resp.status_code} {resp.text[:200]}"
    try:
        body = resp.json()
    except ValueError:
        return False, "unreadable response"
    status_ = str(body.get("status"))
    # "skipped" is a success: the property does not sell through a channel, so
    # there was correctly nothing to do.
    return status_ in ("ok", "partial", "skipped"), status_


@onboarding_router.post("/onboarding/activate", response_model=OnboardingState)
def activate_property(
    property_id: uuid.UUID,
    caller: Caller = Depends(require_permission("property", "update")),
    db: Session = Depends(get_session),
):
    """Open the front desk.

    Refuses while a required step is incomplete, and says which. Going live is
    the moment the property stops being a draft and starts taking real money,
    so it is worth being the one place that checks rather than trusting that
    the wizard was followed in order.
    """
    assert_property_in_org(db, caller, property_id)
    row = _row(db, property_id)
    if row["activated_at"] is not None:
        raise HTTPException(status_code=409, detail="This property is already live.")

    state = _build(db, row, caller)
    blocking = [s for s in state.steps
                if not s.optional and s.key != "golive" and not s.complete]
    if blocking:
        detail = "; ".join(f"{s.label}: {s.blocker}" for s in blocking)
        raise HTTPException(
            status_code=422,
            detail=f"Not ready to go live — {detail}.",
        )

    db.execute(
        text(
            "UPDATE iam.property_onboarding "
            "SET activated_at = now(), activated_by = :who, current_step = 'golive', "
            "    updated_at = now(), version = version + 1 "
            "WHERE property_id = :p"
        ),
        {"who": caller.user_id, "p": property_id},
    )
    sent, failed = _send_welcome_emails(db, property_id)
    # A property that just went live should already be sellable on the OTAs
    # its owner has connected, without anybody remembering to press anything.
    channels_ok, channels_detail = _provision_channel_manager(
        property_id, caller.organization_id)

    record_audit(
        db, action="property.activated", entity_type="property",
        entity_id=str(property_id), organization_id=caller.organization_id,
        property_id=property_id, actor_subject=caller.subject,
        after={"rooms": state.counts.rooms, "room_types": state.counts.room_types,
               "welcome_emails_sent": sent,
               "welcome_emails_failed": len(failed),
               "channel_manager": channels_detail if channels_ok
                                  else f"failed: {channels_detail}"},
    )
    return _build(db, _row(db, property_id), caller)


# --------------------------------------------------------------------------
# Step 2 — the property's own details
# --------------------------------------------------------------------------
PROPERTY_TYPES = ("hotel", "resort", "serviced_apartment", "guest_house", "villa")


class PropertyProfile(BaseModel):
    """What step 2 collects. Everything optional except the name.

    A property is created before this is filled in -- it has to exist for the
    wizard to have something to attach to -- so this is an update, and half a
    form is a legitimate state to save. Only the name is required, because a
    property without one cannot be told apart in a list.
    """

    name: str
    property_type: str | None = None
    contact_email: str | None = None
    contact_phone: str | None = None
    address_line: str | None = None
    city: str | None = None
    state: str | None = None
    postal_code: str | None = None
    country: str | None = None
    timezone: str | None = None
    currency: str | None = None


class PropertyProfileOut(PropertyProfile):
    id: uuid.UUID
    code: str
    logo_key: str | None = None


@onboarding_router.get("/onboarding/property", response_model=PropertyProfileOut)
def get_property_profile(
    property_id: uuid.UUID,
    caller: Caller = Depends(require_permission("property", "view")),
    db: Session = Depends(get_session),
):
    """The property as step 2 knows it."""
    assert_property_in_org(db, caller, property_id)
    row = db.execute(
        text(
            """
            SELECT id, code, name, property_type, contact_email, contact_phone,
                   address_line, city, state, postal_code, country,
                   timezone, currency, logo_key
            FROM iam.properties WHERE id = :p
            """
        ),
        {"p": property_id},
    ).mappings().first()
    if row is None:
        raise HTTPException(status_code=404, detail="Property not found")
    return PropertyProfileOut(**dict(row))


@onboarding_router.put("/onboarding/property", response_model=PropertyProfileOut)
def put_property_profile(
    property_id: uuid.UUID,
    body: PropertyProfile,
    caller: Caller = Depends(require_permission("property", "update")),
    db: Session = Depends(get_session),
):
    """Save step 2.

    ``address`` is kept in step with the parts, so the screens that still read
    the single line do not quietly go stale as soon as somebody edits the
    address here.
    """
    assert_property_in_org(db, caller, property_id)
    if not body.name.strip():
        raise HTTPException(status_code=422, detail="A property name is required.")
    wrong = postal_problem(body.postal_code, body.country)
    if wrong:
        raise HTTPException(status_code=422, detail=wrong)
    body.state = normalise_state(body.state)
    if body.property_type and body.property_type not in PROPERTY_TYPES:
        raise HTTPException(
            status_code=422,
            detail=f"Unknown property type '{body.property_type}'.",
        )

    before = db.execute(
        text("SELECT name, property_type, city FROM iam.properties WHERE id = :p"),
        {"p": property_id},
    ).mappings().first()
    if before is None:
        raise HTTPException(status_code=404, detail="Property not found")

    one_line = ", ".join(
        x.strip() for x in (body.address_line, body.city, body.state)
        if x and x.strip()
    ) or None

    db.execute(
        text(
            """
            UPDATE iam.properties SET
                name = :name,
                property_type = :ptype,
                contact_email = :email,
                contact_phone = :phone,
                address_line = :addr,
                city = :city,
                state = :state,
                postal_code = :postal,
                country = :country,
                timezone = COALESCE(:tz, timezone),
                currency = COALESCE(:ccy, currency),
                address = COALESCE(:one_line, address),
                updated_at = now(), version = version + 1
            WHERE id = :p
            """
        ),
        {
            "name": body.name.strip(), "ptype": body.property_type,
            "email": body.contact_email, "phone": body.contact_phone,
            "addr": body.address_line, "city": body.city, "state": body.state,
            "postal": body.postal_code, "country": body.country,
            "tz": body.timezone, "ccy": body.currency,
            "one_line": one_line, "p": property_id,
        },
    )
    renamed_org = _rename_placeholder_org(
        db, caller.organization_id, before["name"], body.name.strip())

    record_audit(
        db, action="property.profile.updated", entity_type="property",
        entity_id=str(property_id), organization_id=caller.organization_id,
        property_id=property_id, actor_subject=caller.subject,
        before={k: str(v) for k, v in dict(before).items() if v is not None},
        after={"name": body.name, "property_type": body.property_type or "",
               "city": body.city or "",
               **({"organisation_renamed": renamed_org} if renamed_org else {})},
    )
    return get_property_profile(property_id, caller, db)


def _rename_placeholder_org(db: Session, organization_id: uuid.UUID | None,
                            old_name: str, new_name: str) -> str | None:
    """Carry a rename up to the organisation while it is still a placeholder.

    Signing up creates the organisation and its first property with the *same*
    name -- whatever was typed, or "New Property" when nothing was. Renaming
    the property afterwards changed only the property, so the organisation
    kept the placeholder for ever. Nothing displayed an organisation name, so
    nobody saw it, and two unrelated businesses both sat there called "New
    Property".

    That stopped being invisible when tenants started appearing by name at the
    channel manager. It is worth repairing at the source rather than papering
    over: an organisation called "New Property" is wrong wherever it shows up.

    Narrow on purpose. The organisation follows only while it still carries
    exactly the property's old name *and* owns only this one property -- which
    together mean nobody has ever set it deliberately. A group that has named
    itself, or that has a second hotel, is left alone: its name is its own.
    """
    if organization_id is None or old_name == new_name:
        return None
    row = db.execute(
        text(
            """
            SELECT o.name,
                   (SELECT count(*) FROM iam.properties p
                     WHERE p.organization_id = o.id) AS properties
            FROM iam.organizations o WHERE o.id = :o
            """
        ),
        {"o": organization_id},
    ).mappings().first()
    if row is None or row["properties"] != 1 or row["name"] != old_name:
        return None

    db.execute(
        text("UPDATE iam.organizations SET name = :n, updated_at = now(), "
             "version = version + 1 WHERE id = :o"),
        {"n": new_name, "o": organization_id},
    )
    return new_name


# --------------------------------------------------------------------------
# Step 5 — rates and stay policies
# --------------------------------------------------------------------------
class TypeRate(BaseModel):
    room_type_id: uuid.UUID
    name: str
    base_rate: Decimal | None = None
    extra_adult_rate: Decimal | None = None
    extra_child_rate: Decimal | None = None
    #: Rooms on sale under this type. A rate is what one of these costs, so
    #: knowing there are none is the difference between a price that matters
    #: and one nobody can ever be charged.
    rooms: int = 0


CANCELLATION_NAMES = ("Flexible", "Moderate", "Strict", "Non-refundable")


class StayPolicy(BaseModel):
    checkin_time: str | None = None
    checkout_time: str | None = None
    #: 'percent' or 'fixed', with the matching value.
    advance_kind: str | None = None
    advance_value: Decimal | None = None
    #: What the default policy is called -- "Flexible", "Strict" and so on.
    #: The name is what a guest is quoted; ``free_until_days`` is the rule it
    #: is made of, and the two are stored on the same row so they cannot
    #: contradict each other.
    cancellation_name: str | None = None
    free_until_days: int | None = None
    policy_text: str | None = None


class RatesAndPolicies(BaseModel):
    rates: list[TypeRate]
    policy: StayPolicy


@onboarding_router.get("/onboarding/rates", response_model=RatesAndPolicies)
def get_rates(
    property_id: uuid.UUID,
    caller: Caller = Depends(require_permission("property", "view")),
    db: Session = Depends(get_session),
):
    """Every room type's tariff, and the rules a stay is sold under."""
    assert_property_in_org(db, caller, property_id)
    rates = db.execute(
        text(
            """
            SELECT rt.id AS room_type_id, rt.name, rt.base_rate,
                   rt.extra_adult_rate, rt.extra_child_rate,
                   (SELECT count(*) FROM property.rooms rm
                     WHERE rm.room_type_id = rt.id
                       AND rm.status = 'active')            AS rooms
            FROM property.room_types rt
            WHERE rt.property_id = :p AND rt.status = 'active'
            ORDER BY rt.name
            """
        ),
        {"p": property_id},
    ).mappings().all()
    prop = db.execute(
        text("SELECT checkin_time, checkout_time, advance_kind, advance_value "
             "FROM iam.properties WHERE id = :p"),
        {"p": property_id},
    ).mappings().first()
    if prop is None:
        raise HTTPException(status_code=404, detail="Property not found")
    cancel = db.execute(
        text("SELECT name, free_until_days, policy_text "
             "FROM property.cancellation_policies "
             "WHERE property_id = :p AND is_default ORDER BY updated_at DESC LIMIT 1"),
        {"p": property_id},
    ).mappings().first()

    return RatesAndPolicies(
        rates=[TypeRate(**dict(r)) for r in rates],
        policy=StayPolicy(
            checkin_time=prop["checkin_time"], checkout_time=prop["checkout_time"],
            advance_kind=prop["advance_kind"], advance_value=prop["advance_value"],
            cancellation_name=cancel["name"] if cancel else None,
            free_until_days=cancel["free_until_days"] if cancel else None,
            policy_text=cancel["policy_text"] if cancel else None,
        ),
    )


@onboarding_router.put("/onboarding/rates", response_model=RatesAndPolicies)
def put_rates(
    property_id: uuid.UUID,
    body: RatesAndPolicies,
    caller: Caller = Depends(require_permission("property", "update")),
    db: Session = Depends(get_session),
):
    """Save the tariff and the stay rules together.

    They are one step to the operator and one transaction here: a property
    left with rates but no cancellation rule, because the second call failed,
    is a property that cannot quote a booking.
    """
    assert_property_in_org(db, caller, property_id)
    p = body.policy
    if p.advance_kind and p.advance_kind not in ("percent", "fixed"):
        raise HTTPException(
            status_code=422,
            detail="Advance payment must be a percentage or a fixed amount.")
    if p.advance_kind == "percent" and (p.advance_value or 0) > 100:
        raise HTTPException(
            status_code=422, detail="A percentage cannot be above 100.")
    if p.advance_kind and p.advance_value is None:
        raise HTTPException(
            status_code=422, detail="Set how much advance is required.")

    org = db.execute(
        text("SELECT organization_id FROM iam.properties WHERE id = :p"),
        {"p": property_id},
    ).scalar()
    if org is None:
        raise HTTPException(status_code=404, detail="Property not found")

    for r in body.rates:
        for value, label in ((r.base_rate, "room rate"),
                             (r.extra_adult_rate, "extra adult rate"),
                             (r.extra_child_rate, "extra child rate")):
            if value is not None and value < 0:
                raise HTTPException(
                    status_code=422,
                    detail=f"{r.name}: the {label} cannot be negative.")
        db.execute(
            text(
                """
                UPDATE property.room_types
                SET base_rate = :base, extra_adult_rate = :adult,
                    extra_child_rate = :child,
                    updated_at = now(), version = version + 1
                WHERE id = :rt AND property_id = :p
                """
            ),
            {"base": r.base_rate, "adult": r.extra_adult_rate,
             "child": r.extra_child_rate, "rt": r.room_type_id, "p": property_id},
        )

    db.execute(
        text(
            """
            UPDATE iam.properties
            SET checkin_time = COALESCE(:cin, checkin_time),
                checkout_time = COALESCE(:cout, checkout_time),
                advance_kind = :akind, advance_value = :avalue,
                updated_at = now(), version = version + 1
            WHERE id = :p
            """
        ),
        {"cin": p.checkin_time, "cout": p.checkout_time,
         "akind": p.advance_kind, "avalue": p.advance_value, "p": property_id},
    )

    # One default cancellation policy per property, rewritten in place.
    if (p.cancellation_name
            and p.cancellation_name not in CANCELLATION_NAMES):
        raise HTTPException(
            status_code=422,
            detail=f"Cancellation policy must be one of "
                   f"{', '.join(CANCELLATION_NAMES)}.")

    if p.free_until_days is not None or p.policy_text or p.cancellation_name:
        existing = db.execute(
            text("SELECT id FROM property.cancellation_policies "
                 "WHERE property_id = :p AND is_default LIMIT 1"),
            {"p": property_id},
        ).scalar()
        if existing:
            db.execute(
                text("UPDATE property.cancellation_policies "
                     "SET name = COALESCE(:name, name), "
                     "    free_until_days = COALESCE(:days, free_until_days), "
                     "    policy_text = COALESCE(:txt, policy_text), "
                     "    updated_at = now(), version = version + 1 "
                     "WHERE id = :id"),
                {"name": p.cancellation_name, "days": p.free_until_days,
                 "txt": p.policy_text, "id": existing},
            )
        else:
            db.execute(
                text(
                    """
                    INSERT INTO property.cancellation_policies
                        (id, organization_id, property_id, name,
                         free_until_days, policy_text, is_default)
                    VALUES (gen_random_uuid(), :org, :p, :name,
                            COALESCE(:days, 7), COALESCE(:txt, ''), true)
                    """
                ),
                {"org": org, "p": property_id,
                 "name": p.cancellation_name or 'Flexible',
                 "days": p.free_until_days, "txt": p.policy_text},
            )

    record_audit(
        db, action="property.rates.updated", entity_type="property",
        entity_id=str(property_id), organization_id=caller.organization_id,
        property_id=property_id, actor_subject=caller.subject,
        after={"room_types": len(body.rates),
               "advance": f"{p.advance_kind or 'none'} {p.advance_value or ''}".strip()},
    )
    return get_rates(property_id, caller, db)


# --------------------------------------------------------------------------
# Property logo
# --------------------------------------------------------------------------
LOGO_TYPES = {"image/png", "image/jpeg", "image/webp", "image/svg+xml"}
MAX_LOGO_BYTES = 2 * 1024 * 1024

_STORE = ObjectStoreConfig(
    endpoint=settings.minio_endpoint,
    public_endpoint=settings.minio_public_endpoint,
    access_key=settings.minio_access_key,
    secret_key=settings.minio_secret_key,
    bucket=settings.minio_bucket,
    secure=settings.minio_secure,
    url_ttl_seconds=settings.minio_url_ttl_seconds,
)


class LogoOut(BaseModel):
    logo_key: str | None
    #: Expiring link; the object is never public.
    url: str | None = None


def _logo_url(key: str | None) -> str | None:
    if not key:
        return None
    try:
        return presigned_url(_STORE, key)
    except Exception:  # noqa: BLE001 — a missing link must not break the page
        return None


@onboarding_router.get("/onboarding/property/logo", response_model=LogoOut)
def get_property_logo(
    property_id: uuid.UUID,
    caller: Caller = Depends(require_permission("property", "view")),
    db: Session = Depends(get_session),
):
    """The property's logo, as a link that expires."""
    assert_property_in_org(db, caller, property_id)
    key = db.execute(
        text("SELECT logo_key FROM iam.properties WHERE id = :p"),
        {"p": property_id},
    ).scalar()
    return LogoOut(logo_key=key, url=_logo_url(key))


@onboarding_router.post("/onboarding/property/logo", response_model=LogoOut)
async def upload_property_logo(
    property_id: uuid.UUID,
    file: UploadFile = File(...),
    caller: Caller = Depends(require_permission("property", "update")),
    db: Session = Depends(get_session),
):
    """Store a property logo.

    Only the object key is kept in the database and reads go out as expiring
    presigned URLs -- the same shape guest ID scans use, so there is one way
    images are handled rather than two.

    Replacing a logo deletes the old object as well as the row's pointer: a
    superseded image should not linger in storage where nothing references it.
    """
    assert_property_in_org(db, caller, property_id)
    content_type = (file.content_type or "").lower()
    if content_type not in LOGO_TYPES:
        raise HTTPException(
            status_code=415, detail="Upload a PNG, JPG, WebP or SVG.")
    data = await file.read()
    if not data:
        raise HTTPException(status_code=422, detail="That file is empty.")
    if len(data) > MAX_LOGO_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"Keep the logo under {MAX_LOGO_BYTES // (1024 * 1024)}MB.",
        )

    old = db.execute(
        text("SELECT logo_key FROM iam.properties WHERE id = :p"),
        {"p": property_id},
    ).scalar()

    key = build_key("property-logos", str(property_id), "logo",
                    content_type=content_type)
    try:
        put_object(_STORE, key, data, content_type)
    except ObjectStoreError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    db.execute(
        text("UPDATE iam.properties SET logo_key = :k, updated_at = now(), "
             "version = version + 1 WHERE id = :p"),
        {"k": key, "p": property_id},
    )
    if old and old != key:
        try:
            delete_object(_STORE, old)
        except Exception:  # noqa: BLE001 — the pointer has moved either way
            pass

    record_audit(
        db, action="property.logo.uploaded", entity_type="property",
        entity_id=str(property_id), organization_id=caller.organization_id,
        property_id=property_id, actor_subject=caller.subject,
        after={"content_type": content_type, "bytes": len(data)},
    )
    return LogoOut(logo_key=key, url=_logo_url(key))


@onboarding_router.delete("/onboarding/property/logo", response_model=LogoOut)
def delete_property_logo(
    property_id: uuid.UUID,
    caller: Caller = Depends(require_permission("property", "update")),
    db: Session = Depends(get_session),
):
    """Remove the logo, from the database and from storage."""
    assert_property_in_org(db, caller, property_id)
    key = db.execute(
        text("SELECT logo_key FROM iam.properties WHERE id = :p"),
        {"p": property_id},
    ).scalar()
    if key:
        db.execute(
            text("UPDATE iam.properties SET logo_key = NULL, updated_at = now(), "
                 "version = version + 1 WHERE id = :p"),
            {"p": property_id},
        )
        try:
            delete_object(_STORE, key)
        except Exception:  # noqa: BLE001
            pass
        record_audit(
            db, action="property.logo.removed", entity_type="property",
            entity_id=str(property_id), organization_id=caller.organization_id,
            property_id=property_id, actor_subject=caller.subject,
            before={"logo_key": key},
        )
    return LogoOut(logo_key=None, url=None)
