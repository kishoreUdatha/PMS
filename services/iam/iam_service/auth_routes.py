"""Signing in.

Two ways in, and they are not equals.

**Credentials.** Property code, email and password. The password is checked
against a scrypt hash in ``iam.user_credentials``; repeated failures lock the
account for a while. This is the real path, and the one the welcome email sets
somebody up for.

**The subject shortcut.** ``{"subject": "admin-user"}`` with no password, kept
because every seeded fixture and every developer session in this repository
uses it. It is accepted only when the environment is exactly ``local``, and it
is refused for any user who has a password set — otherwise adding credentials
would have achieved nothing, since anyone could go around them.

The session token is a signed, expiring JWT (``chirala_common.session_tokens``)
and ``/auth/logout`` revokes it. It used to be a base64 of the subject, which
anyone could write for themselves; the credential check in front of it bought
nothing while that was so.
"""

from __future__ import annotations

import secrets
import uuid
from datetime import datetime, timedelta, timezone
from smtplib import SMTPException

from fastapi import Request, Response
from chirala_common.db import identity_context, system_context
from chirala_common.db import bind_tenant_context
from chirala_common.audit import record_audit
from chirala_common.delivery_log import record_delivery
from chirala_common.mailer import MailNotConfigured, send as send_mail
from chirala_common.passwords import (
    hash_password, new_token, problem as password_problem, token_hash,
    verify_password,
)
from chirala_common.routing import TransactionalRoute
from chirala_common import session_tokens
from fastapi import APIRouter, Depends, Header, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.orm import Session

from .database import SessionFactory, engine, get_session
from .mail import reset_email, verification_email
from .settings import settings

auth_router = APIRouter(
    prefix="/auth", tags=["auth"], route_class=TransactionalRoute
)


#: How long an account is shut out after repeated wrong passwords, and how
#: many tries it takes. Slow enough to make guessing pointless, short enough
#: that a receptionist who fat-fingered it is not locked out for the shift.
MAX_ATTEMPTS = 5
LOCK_MINUTES = 15


class LoginIn(BaseModel):
    """Either credentials, or the dev subject shortcut."""

    property_code: str | None = None
    email: str | None = None
    password: str | None = None
    #: The legacy path. Ignored when a password is supplied.
    subject: str | None = None


class SetPasswordIn(BaseModel):
    token: str = Field(min_length=10)
    password: str = Field(min_length=1)


class ForgotPasswordIn(BaseModel):
    property_code: str
    email: str


class EmailOtpIn(BaseModel):
    email: str = Field(min_length=3, max_length=254)
    full_name: str | None = Field(default=None, max_length=200)


class EmailOtpVerifyIn(BaseModel):
    email: str = Field(min_length=3, max_length=254)
    code: str = Field(min_length=4, max_length=10)


class SignUpIn(BaseModel):
    """Everything needed to go from nothing to a property being set up."""

    full_name: str = Field(min_length=1, max_length=200)
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=1)
    phone: str | None = Field(default=None, max_length=32)
    #: Optional. Step 2 asks properly; this is only so the wizard does not
    #: open on a property called "New Property" when the name is already known.
    property_name: str | None = Field(default=None, max_length=200)
    agreed: bool = False


class SignUpOut(BaseModel):
    session: SessionOut
    property_id: str
    property_code: str


class MembershipOut(BaseModel):
    organization_id: str
    property_ids: list[str]


class SessionOut(BaseModel):
    token: str
    subject: str
    user_id: str
    display_name: str
    memberships: list[MembershipOut]
    #: True when a temporary credential was used and must be replaced.
    must_change_password: bool = False
    #: True when this account is platform staff rather than tenant staff.
    #:
    #: The two tiers look identical on the wire otherwise -- a platform
    #: session is simply one with no memberships -- and "no memberships" is
    #: also what a broken tenant account looks like. The client has to tell
    #: them apart to know which application to show, and guessing from an
    #: empty list would send a tenant user whose membership lapsed into the
    #: platform console.
    is_platform: bool = False


def _make_token(subject: str) -> str:
    return session_tokens.issue(
        subject, key=settings.session_signing_key,
        ttl_seconds=settings.session_ttl_minutes * 60,
        environment=settings.environment,
    )


def get_auth_session(request: Request, db: Session = Depends(get_session)) -> Session:
    """The session for sign-in routes, in named system context.

    Signing in, verifying an email, setting a password and creating an account
    all read identities before any tenant exists for the request. These routes
    are the identity provider; they run above the tenant boundary, say so, and
    are the only routes that do.
    """
    system_context(db, reason=f"sign-in: {request.url.path}")
    return db

def _load_session(db: Session, subject: str) -> SessionOut | None:
    user = db.execute(
        text(
            """
            SELECT id, display_name FROM iam.users
            WHERE subject_id = :sub AND status = 'active'
            """
        ),
        {"sub": subject},
    ).first()
    if user is None:
        return None

    rows = db.execute(
        text(
            """
            SELECT m.organization_id,
                   -- DISTINCT: three roles on one property is three rows,
                   -- and without it the property came back three times.
                   array_remove(array_agg(DISTINCT ra.property_id), NULL)
                       AS property_ids
            FROM iam.memberships m
            LEFT JOIN iam.role_assignments ra ON ra.membership_id = m.id
            WHERE m.user_id = :uid AND m.status = 'active'
            GROUP BY m.organization_id
            """
        ),
        {"uid": user.id},
    ).all()
    memberships = [
        MembershipOut(
            organization_id=str(r.organization_id),
            property_ids=[str(p) for p in (r.property_ids or [])],
        )
        for r in rows
    ]
    is_platform = db.execute(
        text("SELECT 1 FROM iam.platform_admins "
             "WHERE user_id = :uid AND status = 'active'"),
        {"uid": user.id},
    ).first() is not None

    return SessionOut(
        token=_make_token(subject),
        subject=subject,
        user_id=str(user.id),
        display_name=user.display_name,
        memberships=memberships,
        is_platform=is_platform,
    )


def _now() -> datetime:
    return datetime.now(timezone.utc)


#: How long a code is good for, and how many wrong guesses it survives.
OTP_MINUTES = 10
OTP_MAX_ATTEMPTS = 5
#: A second request inside this window returns the same answer without sending
#: anything, so a stuck "Send code" button cannot be turned into a mail flood
#: aimed at somebody else's inbox.
OTP_RESEND_SECONDS = 60


def _normalise_email(raw: str) -> str:
    email = raw.strip().lower()
    if "@" not in email or "." not in email.split("@")[-1]:
        raise HTTPException(
            status_code=422, detail="That does not look like an email address.")
    return email


@auth_router.post("/email-otp/send", status_code=status.HTTP_202_ACCEPTED)
def send_email_otp(body: EmailOtpIn, db: Session = Depends(get_auth_session)) -> dict:
    """Email a six-digit code to prove the address is reachable.

    Sign-up takes an email and makes it the username, the destination for the
    welcome link and for every password reset afterwards. A typo there is not
    a small mistake: it produces a property nobody can get back into, and a
    stranger who receives its sign-in links.

    An address that already has an account is refused here rather than at the
    end of the form. That does disclose whether an address is registered --
    which is unavoidable on a sign-up screen, since the form has to refuse it
    eventually, and finding out after typing everything else is worse.
    """
    email = _normalise_email(body.email)

    if db.execute(text("SELECT 1 FROM iam.users WHERE lower(email) = :em"),
                  {"em": email}).first():
        raise HTTPException(
            status_code=409,
            detail="An account already exists for that address. Sign in "
                   "instead, or use “Forgot password”.")

    recent = db.execute(
        text("SELECT created_at FROM iam.email_verifications "
             "WHERE lower(email) = :em ORDER BY created_at DESC LIMIT 1"),
        {"em": email},
    ).scalar()
    if recent is not None and (_now() - recent).total_seconds() < OTP_RESEND_SECONDS:
        wait = OTP_RESEND_SECONDS - int((_now() - recent).total_seconds())
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"A code was just sent. Try again in {wait} seconds.")

    code = f"{secrets.randbelow(900_000) + 100_000}"
    # Previous codes for this address stop working the moment a new one is
    # sent, or two inboxes hold two valid codes.
    db.execute(
        text("DELETE FROM iam.email_verifications "
             "WHERE lower(email) = :em AND verified_at IS NULL"),
        {"em": email},
    )
    db.execute(
        text(
            """
            INSERT INTO iam.email_verifications
                (id, email, code_hash, expires_at)
            VALUES (gen_random_uuid(), :em, :h,
                    now() + make_interval(mins => :mins))
            """
        ),
        {"em": email, "h": token_hash(code), "mins": OTP_MINUTES},
    )

    subject, text_body, html_body = verification_email(
        name=body.full_name or "", code=code, minutes=OTP_MINUTES)
    try:
        send_mail(settings.mail_config, to=email, subject=subject,
                  text=text_body, html=html_body)
        record_delivery(SessionFactory, template_code="verification",
                        recipient=email, subject=subject)
    except MailNotConfigured:
        record_delivery(SessionFactory, template_code="verification",
                        recipient=email, status="failed",
                        detail="no mail server configured")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="No mail server is configured, so no code can be sent.",
        ) from None
    except (OSError, SMTPException) as exc:
        record_delivery(SessionFactory, template_code="verification",
                        recipient=email, status="failed",
                        detail=str(exc)[:200])
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="The code could not be sent. Check the address and try "
                   "again.",
        ) from None

    return {"detail": f"A code is on its way to {email}. It expires in "
                      f"{OTP_MINUTES} minutes."}


@auth_router.post("/email-otp/verify")
def verify_email_otp(
    body: EmailOtpVerifyIn, db: Session = Depends(get_auth_session)
) -> dict:
    """Check the code and mark the address verified for the next 30 minutes."""
    email = _normalise_email(body.email)
    row = db.execute(
        text(
            """
            SELECT id, code_hash, attempts, expires_at
            FROM iam.email_verifications
            WHERE lower(email) = :em AND verified_at IS NULL
            ORDER BY created_at DESC LIMIT 1
            """
        ),
        {"em": email},
    ).mappings().first()
    if row is None:
        raise HTTPException(
            status_code=400,
            detail="No code is waiting for that address. Send one first.")
    if row["expires_at"] <= _now():
        raise HTTPException(
            status_code=400, detail="That code has expired. Send a new one.")
    if row["attempts"] >= OTP_MAX_ATTEMPTS:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many wrong codes. Send a new one.")

    if token_hash(body.code.strip()) != row["code_hash"]:
        # On its own connection, for the same reason the sign-in lockout is:
        # the 400 below rolls this transaction back, and a counter that does
        # not survive the rejection is not a counter.
        with engine.connect() as conn:
            conn.execute(text("SELECT set_config('app.system', 'on', true)"))
            conn.execute(
                text("UPDATE iam.email_verifications SET attempts = attempts + 1 "
                     "WHERE id = :i"),
                {"i": row["id"]},
            )
            conn.commit()
        left = OTP_MAX_ATTEMPTS - row["attempts"] - 1
        raise HTTPException(
            status_code=400,
            detail=("That code is not right. "
                    + (f"{left} attempt{'' if left == 1 else 's'} left."
                       if left > 0 else "Send a new one.")))

    db.execute(
        text("UPDATE iam.email_verifications SET verified_at = now() "
             "WHERE id = :i"),
        {"i": row["id"]},
    )
    return {"verified": True}


def _new_property_code(db: Session) -> str:
    """Six digits nobody else on the platform holds."""
    for _ in range(20):
        candidate = str(secrets.randbelow(900_000) + 100_000)
        if db.execute(text("SELECT 1 FROM iam.properties WHERE code = :c"),
                      {"c": candidate}).first() is None:
            return candidate
    raise HTTPException(
        status_code=503, detail="Could not allocate a property code.")


def _seed_roles(db: Session, org_id: uuid.UUID) -> uuid.UUID:
    """Give a brand-new organisation the role catalogue, and return its admin.

    Roles are per-organisation: ``iam.roles.organization_id`` is NOT NULL. A
    new tenant therefore starts with none at all, and without this its owner
    would be refused by every endpoint the wizard calls — and step 7 would
    offer an empty list of roles to invite people into.

    The catalogue is copied from the oldest organisation on the installation,
    which is the one the seed migrations built. That is a pragmatic choice,
    not a good one: a template catalogue should exist in its own right rather
    than being whichever tenant happened to be created first. Worth replacing
    before a second installation exists; recorded here so it is not
    rediscovered by surprise.
    """
    template_org = db.execute(
        text("SELECT organization_id FROM iam.roles "
             "GROUP BY organization_id ORDER BY min(created_at) LIMIT 1")
    ).scalar()
    if template_org is None:
        raise HTTPException(
            status_code=500,
            detail="This installation has no roles to copy. Seed the role "
                   "catalogue before anyone can sign up.")

    db.execute(
        text(
            """
            INSERT INTO iam.roles
                (id, organization_id, code, name, template_code, active,
                 record_scope, property_scope, max_discount, max_refund)
            SELECT gen_random_uuid(), :org, r.code, r.name,
                   COALESCE(r.template_code, r.code), r.active,
                   r.record_scope, r.property_scope, r.max_discount,
                   r.max_refund
            FROM iam.roles r
            WHERE r.organization_id = :tpl
            """
        ),
        {"org": org_id, "tpl": template_org},
    )
    # The permissions behind them, matched by code so the copy does not depend
    # on the order the roles came out in.
    db.execute(
        text(
            """
            INSERT INTO iam.role_permissions
                (id, role_id, permission_id, record_scope)
            SELECT gen_random_uuid(), new_r.id, rp.permission_id,
                   rp.record_scope
            FROM iam.role_permissions rp
            JOIN iam.roles old_r ON old_r.id = rp.role_id
                                AND old_r.organization_id = :tpl
            JOIN iam.roles new_r ON new_r.organization_id = :org
                                AND new_r.code = old_r.code
            """
        ),
        {"org": org_id, "tpl": template_org},
    )

    admin = db.execute(
        text("SELECT id FROM iam.roles "
             "WHERE organization_id = :org AND code = 'PROP_ADMIN'"),
        {"org": org_id},
    ).scalar()
    if admin is None:
        raise HTTPException(
            status_code=500,
            detail="The copied role catalogue has no PROP_ADMIN role.")
    return admin


#: What a brand-new property starts with. Written out here rather than copied
#: from another tenant the way roles are: these are short, universal, and a
#: starter list somebody edits is a much better first experience than an empty
#: screen that gives no clue what belongs in it.
#:
#: Nothing here is mandatory — every one can be renamed, disabled or deleted
#: on the amenities screen. The point is to have something to react to.
STARTER_AMENITIES: tuple[tuple[str, str, str, str, bool], ...] = (
    # code, name, category, icon, chargeable
    ("wifi", "Free Wi-Fi", "technology", "wifi", False),
    ("tv", "Television", "technology", "tv", False),
    ("intercom", "Intercom", "technology", "phone", False),
    ("ac", "AC", "in_room", "wind", False),
    ("desk", "Work Desk", "in_room", "briefcase", False),
    ("safe", "In-room Safe", "in_room", "lock", False),
    ("sofa", "Sofa", "in_room", "armchair", False),
    ("hairdryer", "Hair Dryer", "bathroom", "wind", False),
    ("bathtub", "Bathtub", "bathroom", "bath", False),
    ("kettle", "Electric Kettle", "food_beverage", "coffee", False),
    ("minibar", "Mini Bar", "food_beverage", "wine", True),
    ("balcony", "Balcony", "recreation", "trees", False),
    ("pool", "Private Pool", "recreation", "waves", False),
    ("wheelchair", "Wheelchair Access", "safety_security", "accessibility", False),
)

#: The board bases the industry actually uses. A property that sells none of
#: them still wants Room Only to exist, because every rate plan needs one.
STARTER_MEAL_PLANS: tuple[tuple[str, str, str], ...] = (
    ("RO", "Room Only", "No meals included."),
    ("BB", "Breakfast", "Breakfast included for all occupants."),
    ("HB", "Half Board", "Breakfast and one further meal included."),
    ("FB", "Full Board", "Breakfast, lunch and dinner included."),
    ("AI", "All Inclusive", "All meals, snacks and selected drinks included."),
)


def _seed_property_defaults(
    db: Session, *, org_id: uuid.UUID, property_id: uuid.UUID
) -> None:
    """Give a new property something to work with.

    Amenities and meal plans are per-property rows, and nothing created them
    for a property that did not come from the seed migration. The result was
    an amenities section on step 4 with nothing in it and a meal plan panel on
    step 5 saying "no rate plans yet" — both correct, both useless, and
    neither telling the operator that the answer was to go and create some
    somewhere else first.
    """
    for code, name, category, icon, chargeable in STARTER_AMENITIES:
        db.execute(
            text(
                """
                INSERT INTO property.amenities
                    (id, organization_id, property_id, code, name, category,
                     icon, is_chargeable, status, guest_visible)
                VALUES (gen_random_uuid(), :org, :prop, :code, :name, :cat,
                        :icon, :charge, 'active', true)
                """
            ),
            {"org": org_id, "prop": property_id, "code": code, "name": name,
             "cat": category, "icon": icon, "charge": chargeable},
        )

    for order, (code, name, description) in enumerate(STARTER_MEAL_PLANS, 1):
        db.execute(
            text(
                """
                INSERT INTO property.meal_plans
                    (id, organization_id, property_id, code, name,
                     description, display_order, status)
                VALUES (gen_random_uuid(), :org, :prop, :code, :name, :desc,
                        :ord, 'active')
                """
            ),
            {"org": org_id, "prop": property_id, "code": code, "name": name,
             "desc": description, "ord": order},
        )


@auth_router.post("/sign-up", response_model=SignUpOut,
                  status_code=status.HTTP_201_CREATED)
def sign_up(body: SignUpIn, db: Session = Depends(get_auth_session)) -> SignUpOut:
    """Create an account, an organisation and a property, and sign in.

    This is the only part of onboarding that can be reached without a session,
    for the obvious reason. Every step after it writes to one named property,
    so those must be authenticated — otherwise anyone could edit anyone's
    property by guessing an id. Step one is what issues the session the rest
    of the wizard runs on; until now it was a form with nothing behind it, and
    the only way into the wizard was a developer shortcut.

    Everything is created together or not at all. A half-made signup — a user
    with no organisation, or an organisation with no property — leaves someone
    able to log in to nothing, and no screen from which to repair it.
    """
    email = body.email.strip().lower()
    if "@" not in email or "." not in email.split("@")[-1]:
        raise HTTPException(
            status_code=422, detail="That does not look like an email address.")
    if not body.agreed:
        raise HTTPException(
            status_code=422,
            detail="The terms have to be accepted before an account is made.")

    # Verified within the last half hour. Checked here rather than trusting
    # the screen: the browser decides what it displays, not what is true.
    verified = db.execute(
        text(
            """
            SELECT 1 FROM iam.email_verifications
            WHERE lower(email) = :em AND verified_at IS NOT NULL
              AND verified_at > now() - interval '30 minutes'
            LIMIT 1
            """
        ),
        {"em": email},
    ).first()
    if verified is None:
        raise HTTPException(
            status_code=422,
            detail="Verify the email address first — we sent a six-digit "
                   "code to it.")

    why = password_problem(body.password)
    if why:
        raise HTTPException(status_code=422, detail=why)

    # One owner account per address. Sign-up creates an organisation, so
    # allowing a second would quietly give one person two tenants and no way
    # to tell them apart at the sign-in screen.
    if db.execute(text("SELECT 1 FROM iam.users WHERE lower(email) = :em"),
                  {"em": email}).first():
        raise HTTPException(
            status_code=409,
            detail="An account already exists for that address. Sign in "
                   "instead, or use “Forgot password”.")

    org_id = uuid.uuid4()
    property_id = uuid.uuid4()
    user_id = uuid.uuid4()
    membership_id = uuid.uuid4()
    code = _new_property_code(db)
    name = (body.property_name or "").strip() or "New Property"
    # The subject is what the token carries and what audit rows are stamped
    # with. Derived from the address so it is recognisable in a log, and made
    # unique so two people at different domains cannot collide.
    subject = f"{email.split('@')[0]}-{uuid.uuid4().hex[:6]}"

    db.execute(
        text("INSERT INTO iam.organizations (id, name) VALUES (:id, :name)"),
        {"id": org_id, "name": name},
    )
    db.execute(
        text(
            """
            INSERT INTO iam.properties
                (id, organization_id, code, name, timezone, currency, status)
            VALUES (:id, :org, :code, :name, 'Asia/Kolkata', 'INR', 'active')
            """
        ),
        {"id": property_id, "org": org_id, "code": code, "name": name},
    )
    db.execute(
        text(
            """
            INSERT INTO iam.users
                (id, identity_provider, subject_id, display_name, status,
                 email, phone)
            VALUES (:id, 'local', :sub, :name, 'active', :em, :phone)
            """
        ),
        {"id": user_id, "sub": subject, "name": body.full_name.strip(),
         "em": email, "phone": (body.phone or "").strip() or None},
    )
    db.execute(
        text("INSERT INTO iam.user_credentials (user_id, password_hash) "
             "VALUES (:u, :h)"),
        {"u": user_id, "h": hash_password(body.password)},
    )
    db.execute(
        text(
            """
            INSERT INTO iam.memberships (id, organization_id, user_id, status)
            VALUES (:id, :org, :u, 'active')
            """
        ),
        {"id": membership_id, "org": org_id, "u": user_id},
    )
    role_id = _seed_roles(db, org_id)
    db.execute(
        text(
            """
            INSERT INTO iam.role_assignments
                (id, membership_id, role_id, property_id, scope_type)
            VALUES (gen_random_uuid(), :m, :r, :p, 'property')
            """
        ),
        {"m": membership_id, "r": role_id, "p": property_id},
    )
    db.execute(
        text(
            """
            INSERT INTO iam.property_onboarding
                (property_id, organization_id, current_step)
            VALUES (:p, :org, 'property')
            """
        ),
        {"p": property_id, "org": org_id},
    )
    _seed_property_defaults(db, org_id=org_id, property_id=property_id)

    db.execute(
        text("DELETE FROM iam.email_verifications WHERE lower(email) = :em"),
        {"em": email},
    )

    record_audit(
        db, action="account.signed_up", entity_type="property",
        entity_id=str(property_id), organization_id=org_id,
        property_id=property_id, actor_subject=subject,
        after={"email": email, "property_code": code},
    )

    session = _load_session(db, subject)
    if session is None:  # pragma: no cover - the rows were just written
        raise HTTPException(status_code=500, detail="Account could not be made.")
    return SignUpOut(session=session, property_id=str(property_id),
                     property_code=code)


def _record_failure(user_id, attempts: int) -> None:
    """Count a wrong password, on its own connection.

    This has to outlive the rejection. The route is transactional and rolls
    back on exception, so a counter written on the request's session was
    undone by the very 401 that signalled the failure — five wrong guesses
    left the count at zero and the lockout could never engage. A separate
    connection, committed immediately, is the point: the record of an attack
    must survive the refusal of the attack.
    """
    with engine.connect() as conn:
        conn.execute(text("SELECT set_config('app.system', 'on', true)"))
        conn.execute(
            text(
                """
                UPDATE iam.user_credentials
                   SET failed_attempts = :n,
                       locked_until = CASE WHEN :n >= :max
                            THEN now() + make_interval(mins => :mins) END,
                       updated_at = now(), version = version + 1
                 WHERE user_id = :uid
                """
            ),
            {"n": attempts, "max": MAX_ATTEMPTS, "mins": LOCK_MINUTES,
             "uid": user_id},
        )
        conn.commit()


def _refuse_if_locked(locked_until) -> None:
    """429 while an account is locked out after too many wrong answers."""
    if locked_until is not None and locked_until > _now():
        minutes = max(1, int((locked_until - _now()).total_seconds() // 60) + 1)
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Too many attempts. Try again in {minutes} minute"
                   f"{'' if minutes == 1 else 's'}, or reset your password.",
        )


def _credential_login(db: Session, body: LoginIn) -> SessionOut:
    """Property code, email and password, checked properly."""
    wrong = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        # One message for every kind of failure. Saying "no such user" tells a
        # guesser which addresses are worth attacking.
        detail="Those sign-in details were not recognised.",
    )

    prop = db.execute(
        text("SELECT p.id, p.organization_id, p.name, o.status AS org_status "
             "FROM iam.properties p "
             "JOIN iam.organizations o ON o.id = p.organization_id "
             "WHERE upper(p.code) = upper(:c)"),
        {"c": (body.property_code or "").strip()},
    ).mappings().first()
    if prop is None:
        raise wrong

    # A suspended tenant gets the real reason rather than the deliberately
    # vague credential message. The password was right; telling them it was
    # not sends the whole property hunting for a problem they cannot find.
    # Nothing is leaked by saying so -- the property code is already known to
    # whoever typed it.
    if prop["org_status"] != "active":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This organisation is suspended. Contact support.")

    user = db.execute(
        text(
            """
            SELECT u.id, u.subject_id, u.display_name, u.status,
                   c.password_hash, c.failed_attempts, c.locked_until,
                   c.must_change
            FROM iam.users u
            JOIN iam.memberships m
              ON m.user_id = u.id AND m.organization_id = :org
             AND m.status = 'active'
            LEFT JOIN iam.user_credentials c ON c.user_id = u.id
            WHERE lower(u.email) = lower(:em)
            LIMIT 1
            """
        ),
        {"org": prop["organization_id"], "em": (body.email or "").strip()},
    ).mappings().first()
    if user is None or user["password_hash"] is None:
        raise wrong
    if user["status"] != "active":
        raise wrong

    locked = user["locked_until"]
    if locked is not None and locked > _now():
        minutes = max(1, int((locked - _now()).total_seconds() // 60) + 1)
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Too many attempts. Try again in {minutes} minute"
                   f"{'' if minutes == 1 else 's'}, or reset your password.",
        )

    if not verify_password(body.password or "", user["password_hash"]):
        _record_failure(user["id"], user["failed_attempts"] + 1)
        raise wrong

    db.execute(
        text("UPDATE iam.user_credentials SET failed_attempts = 0, "
             "locked_until = NULL, updated_at = now() WHERE user_id = :uid"),
        {"uid": user["id"]},
    )
    db.execute(
        text("UPDATE iam.users SET last_login_at = now() WHERE id = :uid"),
        {"uid": user["id"]},
    )

    session = _load_session(db, user["subject_id"])
    if session is None:
        raise wrong
    session.must_change_password = bool(user["must_change"])
    return session


@auth_router.post("/login", response_model=SessionOut)
def login(body: LoginIn, db: Session = Depends(get_auth_session)) -> SessionOut:
    if body.password:
        return _credential_login(db, body)

    # --- the subject shortcut -------------------------------------------
    # Allowed in local mode only, compared exactly. It used to be refused
    # only when the environment was exactly "production", so "prod", "staging"
    # or a typo handed out sessions for any password-less account by name.
    if settings.environment != "local":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Sign in with your property code, email and password.",
        )
    if not body.subject:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Give a property code, email and password.",
        )
    subject = body.subject.strip()
    # A user who has a password must use it. Otherwise the credential is
    # decorative: anyone could name the subject and walk straight past it.
    has_password = db.execute(
        text("SELECT 1 FROM iam.user_credentials c JOIN iam.users u "
             "ON u.id = c.user_id WHERE u.subject_id = :s"),
        {"s": subject},
    ).first()
    if has_password:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="That account has a password. Sign in with your property "
                   "code, email and password.",
        )
    session = _load_session(db, subject)
    if session is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Unknown or inactive user",
        )
    return session


class PlatformLoginIn(BaseModel):
    """Credentials for a platform operator. Deliberately no property code."""

    email: str = Field(min_length=5, max_length=200)
    password: str = Field(min_length=1)


@auth_router.post("/platform-login", response_model=SessionOut)
def platform_login(body: PlatformLoginIn,
                   db: Session = Depends(get_auth_session)) -> SessionOut:
    """Sign in as platform staff: email and password, no property code.

    A separate route because the ordinary one cannot serve these accounts at
    all. ``_credential_login`` resolves the user *through* a property code and
    an active membership in that property's organisation -- which is right for
    tenant staff and impossible for a platform operator, who by design belongs
    to no organisation and therefore has no property code to type.

    It is also a separate route because it must not be a fallback. Tenant
    sign-in failing over to this would mean a mistyped property code quietly
    changed which tier the caller was asking for; here, asking for platform
    access is explicit, and an account that is not platform staff is refused
    even when the password is perfectly correct.
    """
    wrong = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        # The same single message for every failure, including "not platform
        # staff". Distinguishing them would turn this route into an oracle for
        # which addresses are worth attacking hardest.
        detail="Those sign-in details were not recognised.",
    )

    email = (body.email or "").strip().lower()
    user = db.execute(
        text(
            """
            SELECT u.id, u.subject_id, u.display_name, u.status,
                   c.password_hash, c.failed_attempts, c.locked_until,
                   c.must_change,
                   (pa.id IS NOT NULL) AS is_platform_admin
            FROM iam.users u
            LEFT JOIN iam.user_credentials c ON c.user_id = u.id
            LEFT JOIN iam.platform_admins pa
              ON pa.user_id = u.id AND pa.status = 'active'
            WHERE lower(u.email) = :em
            LIMIT 1
            """
        ),
        {"em": email},
    ).mappings().first()
    if user is None or user["password_hash"] is None:
        raise wrong
    if user["status"] != "active":
        raise wrong

    locked = user["locked_until"]
    if locked is not None and locked > _now():
        minutes = max(1, int((locked - _now()).total_seconds() // 60) + 1)
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Too many attempts. Try again in {minutes} minute"
                   f"{'' if minutes == 1 else 's'}, or reset your password.",
        )

    # The password is checked before platform staff is, and a wrong one is
    # counted, so that a non-admin guessing passwords here is locked out on
    # exactly the same schedule as anywhere else. Checking membership of the
    # table first would let an attacker probe passwords for free.
    if not verify_password(body.password or "", user["password_hash"]):
        _record_failure(user["id"], user["failed_attempts"] + 1)
        raise wrong
    if not user["is_platform_admin"]:
        raise wrong

    db.execute(
        text("UPDATE iam.user_credentials SET failed_attempts = 0, "
             "locked_until = NULL, updated_at = now() WHERE user_id = :uid"),
        {"uid": user["id"]},
    )

    # --- the second factor, if there is one ---------------------------------
    #
    # The password is now proven and the device is not. What comes back is a
    # challenge, not a session: on its own it can do exactly one thing, which
    # is be exchanged for a session by somebody holding the authenticator. A
    # stolen password therefore buys an attacker a token that opens nothing.
    from .mfa_routes import make_challenge, mfa_state

    system_context(db, reason=f"sign-in: second factor for {user['subject_id']}")
    state = mfa_state(db, user["id"])
    if state and state["status"] == "active":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Enter the code from your authenticator.",
            headers={"X-MFA-Challenge": make_challenge(user["id"])},
        )
    if settings.platform_mfa_required:
        # Policy says every operator must hold one, and this account does not.
        # Refused rather than waved through, which is the whole point of a
        # policy; the message says what to do rather than only that it failed.
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This platform requires a second factor and your account "
                   "has none. Ask another administrator to enrol you.")

    db.execute(
        text("UPDATE iam.users SET last_login_at = now() WHERE id = :uid"),
        {"uid": user["id"]},
    )
    # Signing in at this level is itself worth a row. It is the one login in
    # the system that can reach every tenant.
    record_audit(
        db, action="platform.signed_in", entity_type="user",
        entity_id=str(user["id"]), actor_subject=user["subject_id"],
    )

    session = _load_session(db, user["subject_id"])
    if session is None:
        raise wrong
    session.must_change_password = bool(user["must_change"])
    return session


class MfaVerifyIn(BaseModel):
    challenge: str = Field(min_length=10)
    code: str = Field(min_length=6, max_length=10)


@auth_router.post("/platform-login/verify", response_model=SessionOut)
def platform_login_verify(body: MfaVerifyIn,
                          db: Session = Depends(get_session)) -> SessionOut:
    """Step two: the challenge from the password, plus a code from the device.

    The challenge carries the user id, a one-time nonce and its own expiry,
    sealed with the deployment's key, so this route never has to trust
    anything the client says about who it is.

    **The lockout applies here too.** Wrong codes are counted against the
    same limit as wrong passwords, and that count used to lock only the
    password step: a challenge obtained before the lock kept accepting
    guesses, and a correct one signed in a locked account.

    **A challenge is good for one session.** It used to be reusable until it
    expired, so one captured challenge plus any code -- a recovery code read
    off a printout -- minted as many sessions as its holder liked for five
    minutes.
    """
    from .mfa_routes import check_second_factor, read_challenge

    user_id, nonce, expires = read_challenge(body.challenge)
    system_context(db, reason="sign-in: verify second factor")

    row = db.execute(
        text("SELECT u.subject_id, u.status, c.locked_until "
             "FROM iam.users u "
             "LEFT JOIN iam.user_credentials c ON c.user_id = u.id "
             "WHERE u.id = :u"),
        {"u": user_id},
    ).mappings().first()
    if row is None or row["status"] != "active":
        raise HTTPException(status_code=401,
                            detail="Those sign-in details were not recognised.")
    _refuse_if_locked(row["locked_until"])

    # Spent before the code is checked, so a replay is refused without
    # burning a recovery code or counting as a guess. A wrong code rolls this
    # back with the rest of the transaction, leaving the challenge usable for
    # a retry -- the guess limit, not the challenge, is what bounds guessing.
    # Kept beside revoked sessions: both are "a token we signed that must no
    # longer be honoured", keyed and expiring the same way.
    spent = db.execute(
        text(
            """
            INSERT INTO iam.revoked_sessions (jti, subject, expires_at)
            VALUES (:j, :s, to_timestamp(:e))
            ON CONFLICT (jti) DO NOTHING
            RETURNING jti
            """
        ),
        {"j": f"mfa:{nonce}", "s": row["subject_id"], "e": expires},
    ).first()
    if spent is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="That sign-in attempt has already been used. Start again.")

    if not check_second_factor(db, user_id, body.code):
        # Counted like a wrong password: a second factor with unlimited
        # guesses is a six-digit number, and six digits fall in a day.
        _record_failure(user_id, _attempts(db, user_id) + 1)
        record_audit(
            db, action="platform.mfa.failed", entity_type="user",
            entity_id=str(user_id), actor_subject=row["subject_id"])
        raise HTTPException(status_code=401, detail="That code is not right.")

    db.execute(text("UPDATE iam.users SET last_login_at = now() WHERE id = :u"),
               {"u": user_id})
    # Proven twice over; earlier wrong codes stop counting toward a lockout.
    db.execute(
        text("UPDATE iam.user_credentials SET failed_attempts = 0, "
             "locked_until = NULL, updated_at = now() WHERE user_id = :u"),
        {"u": user_id},
    )
    record_audit(
        db, action="platform.signed_in", entity_type="user",
        entity_id=str(user_id), actor_subject=row["subject_id"],
        after={"second_factor": True})

    session = _load_session(db, row["subject_id"])
    if session is None:
        raise HTTPException(status_code=401, detail="Unknown or inactive user")
    return session


def _attempts(db: Session, user_id) -> int:
    n = db.execute(
        text("SELECT failed_attempts FROM iam.user_credentials "
             "WHERE user_id = :u"),
        {"u": user_id},
    ).scalar()
    return int(n or 0)


def issue_password_token(
    db: Session, *, user_id: uuid.UUID, purpose: str, hours: int,
) -> str:
    """A fresh single-use link token, retiring any earlier one.

    Older unused tokens are burnt so that a second "resend" does not leave two
    working links in two inboxes.
    """
    # Serialise on the user. Two resends arriving together each burnt the
    # tokens that existed when they started and then inserted their own, so
    # both survived -- two live "set your password" links for one person,
    # which is precisely what retiring the old one is meant to prevent.
    db.execute(text("SELECT id FROM iam.users WHERE id = :u FOR UPDATE"),
               {"u": user_id})
    db.execute(
        text("UPDATE iam.password_tokens SET used_at = now() "
             "WHERE user_id = :u AND purpose = :p AND used_at IS NULL"),
        {"u": user_id, "p": purpose},
    )
    token, digest = new_token()
    db.execute(
        text(
            """
            INSERT INTO iam.password_tokens
                (id, user_id, token_hash, purpose, expires_at)
            VALUES (gen_random_uuid(), :u, :h, :p,
                    now() + make_interval(hours => :hrs))
            """
        ),
        {"u": user_id, "h": digest, "p": purpose, "hrs": hours},
    )
    return token


def _accept_invitation(db: Session, *, user_id: uuid.UUID) -> None:
    """Turn an invitation into real access.

    Creating an invitation wrote a user, an ``iam.invitations`` row and its
    grants — but no membership, and the user was left ``invited``. Nothing
    then converted any of that, so an invited person had roles recorded
    against an invitation and no route by which they applied: no membership
    meant no organisation, and no organisation meant no permissions.

    Choosing a password from the welcome link is the moment they accept, so
    that is where it is done. Idempotent throughout, because the alternative
    to ``ON CONFLICT`` here is a second membership for somebody who clicked
    twice.
    """
    invitations = db.execute(
        text(
            """
            SELECT id, organization_id, property_id
            FROM iam.invitations
            WHERE created_user_id = :u
            ORDER BY created_at
            """
        ),
        {"u": user_id},
    ).mappings().all()

    # No early return when there are none. A tenant created from the platform
    # console has no iam.invitations row at all -- create_tenant writes the
    # user, membership, roles and assignment directly -- so returning here
    # left its owner `invited` forever. _load_session then refused them, the
    # route raised, and the transaction rolled back taking the password with
    # it: every owner invited by Super Admin was told their link had expired.
    # The grants below are conditional on an invitation; becoming active is
    # not. Holding the link is the proof, whichever path issued it.
    for inv in invitations:
        membership_id = db.execute(
            text("SELECT id FROM iam.memberships "
                 "WHERE user_id = :u AND organization_id = :o"),
            {"u": user_id, "o": inv["organization_id"]},
        ).scalar()
        if membership_id is None:
            membership_id = uuid.uuid4()
            db.execute(
                text(
                    """
                    INSERT INTO iam.memberships
                        (id, organization_id, user_id, status)
                    VALUES (:id, :org, :u, 'active')
                    """
                ),
                {"id": membership_id, "org": inv["organization_id"],
                 "u": user_id},
            )
        else:
            db.execute(
                text("UPDATE iam.memberships SET status = 'active', "
                     "updated_at = now() WHERE id = :id"),
                {"id": membership_id},
            )

        # The roles the invitation promised, now attached to something that
        # can carry them.
        db.execute(
            text(
                """
                INSERT INTO iam.role_assignments
                    (id, membership_id, role_id, property_id, scope_type)
                SELECT gen_random_uuid(), :mid, g.role_id, :prop, g.scope_type
                FROM iam.invitation_grants g
                WHERE g.invitation_id = :inv
                  AND NOT EXISTS (
                      SELECT 1 FROM iam.role_assignments ra
                      WHERE ra.membership_id = :mid
                        AND ra.role_id = g.role_id
                        AND ra.property_id IS NOT DISTINCT FROM :prop
                  )
                """
            ),
            {"mid": membership_id, "inv": inv["id"], "prop": inv["property_id"]},
        )

    db.execute(
        text("UPDATE iam.users SET status = 'active', updated_at = now(), "
             "version = version + 1 WHERE id = :u AND status = 'invited'"),
        {"u": user_id},
    )
    db.execute(
        text("UPDATE iam.invitations SET status = 'accepted' "
             "WHERE created_user_id = :u AND status <> 'accepted'"),
        {"u": user_id},
    )


@auth_router.post("/set-password", response_model=SessionOut)
def set_password(
    body: SetPasswordIn, db: Session = Depends(get_auth_session)
) -> SessionOut:
    """Use a welcome or reset link to set a password, and sign in."""
    generic = ("That link has expired or has already been used. Ask for a "
               "new one from the sign-in page.")

    def why_not(token_hash_value: str) -> str:
        """The most useful true thing we can say about a link that failed.

        Only ever reached by somebody holding the token in question, so naming
        its state tells them nothing they did not already have. A token that
        matches no row falls through to the generic wording, which is what a
        stranger guessing gets.
        """
        row = db.execute(
            text(
                """
                SELECT t.used_at, t.expires_at <= now() AS expired,
                       EXISTS (
                           -- Any other live link of the same kind, not
                           -- "a later" one: now() is transaction time, so two
                           -- tokens issued in one transaction share a
                           -- created_at and an ordering test never fires. If
                           -- this link is dead and another is alive, the
                           -- newest email is the one to open -- which is the
                           -- thing worth saying.
                           SELECT 1 FROM iam.password_tokens n
                           WHERE n.user_id = t.user_id
                             AND n.purpose = t.purpose
                             AND n.id <> t.id
                             AND n.used_at IS NULL
                             AND n.expires_at > now()
                       ) AS superseded
                FROM iam.password_tokens t
                WHERE t.token_hash = :h
                """
            ),
            {"h": token_hash_value},
        ).mappings().first()
        if row is None:
            return generic
        if row["superseded"]:
            # The one case where the person already has what they need.
            return ("A newer link has been sent since this one. Open the most "
                    "recent email and use the link in that — this link stopped "
                    "working when the new one was issued.")
        if row["expired"]:
            return ("That link has expired. Ask for a new one from the "
                    "sign-in page.")
        if row["used_at"] is not None:
            return ("That link has already been used. If you have set your "
                    "password, sign in; otherwise ask for a new link from the "
                    "sign-in page.")
        return generic

    row = db.execute(
        text(
            """
            SELECT t.id, t.user_id, t.purpose, u.subject_id, u.status
            FROM iam.password_tokens t
            JOIN iam.users u ON u.id = t.user_id
            WHERE t.token_hash = :h AND t.used_at IS NULL
              AND t.expires_at > now()
              -- A welcome link is precisely for somebody who is not active
              -- yet; requiring 'active' here meant no welcome link could ever
              -- work. A reset link is different: you should not be able to
              -- reset your way back into a suspended account.
              AND (t.purpose = 'welcome' OR u.status = 'active')
              AND u.status <> 'disabled'
            """
        ),
        {"h": token_hash(body.token)},
    ).mappings().first()
    if row is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail=why_not(token_hash(body.token)))

    why = password_problem(body.password)
    if why:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=why)

    db.execute(
        text(
            """
            INSERT INTO iam.user_credentials (user_id, password_hash)
            VALUES (:u, :h)
            ON CONFLICT (user_id) DO UPDATE
               SET password_hash = EXCLUDED.password_hash,
                   password_set_at = now(), must_change = false,
                   failed_attempts = 0, locked_until = NULL,
                   updated_at = now(),
                   version = iam.user_credentials.version + 1
            """
        ),
        {"u": row["user_id"], "h": hash_password(body.password)},
    )
    db.execute(
        text("UPDATE iam.password_tokens SET used_at = now() WHERE id = :i"),
        {"i": row["id"]},
    )
    if row["purpose"] == "welcome":
        _accept_invitation(db, user_id=row["user_id"])
    record_audit(
        db, action="user.password.set", entity_type="user",
        entity_id=str(row["user_id"]), organization_id=None,
        property_id=None, actor_subject=row["subject_id"],
    )

    session = _load_session(db, row["subject_id"])
    if session is None:
        # Not a bad link -- the link was fine and the password was written.
        # Raising here rolls that back, so the link stays usable and "try
        # again" is honest. Reporting this as an expired link sent people off
        # to request a new one for a link that was never the problem.
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Your password could not be set just now. Try that link "
                   "again, or ask for a new one from the sign-in page.",
        )
    return session


@auth_router.post("/forgot-password", status_code=status.HTTP_202_ACCEPTED)
def forgot_password(
    body: ForgotPasswordIn, db: Session = Depends(get_auth_session)
) -> dict:
    """Email a reset link, if that address belongs to that property.

    Always answers the same way. Telling an unknown address apart from a known
    one turns this into a way to find out who works somewhere.
    """
    same = {"detail": "If that address belongs to this property, a reset link "
                      "is on its way."}
    row = db.execute(
        text(
            """
            SELECT u.id, u.email, u.display_name, p.name AS property_name,
                   p.code AS property_code, p.organization_id
            FROM iam.properties p
            JOIN iam.memberships m
              ON m.organization_id = p.organization_id AND m.status = 'active'
            JOIN iam.users u ON u.id = m.user_id AND u.status = 'active'
            WHERE upper(p.code) = upper(:c) AND lower(u.email) = lower(:em)
            LIMIT 1
            """
        ),
        {"c": body.property_code.strip(), "em": body.email.strip()},
    ).mappings().first()
    if row is None:
        return same

    token = issue_password_token(
        db, user_id=row["id"], purpose="reset", hours=2)
    subject, text_body, html_body = reset_email(
        name=row["display_name"], property_name=row["property_name"],
        property_code=row["property_code"], email=row["email"], token=token,
    )
    try:
        send_mail(settings.mail_config, to=row["email"], subject=subject,
                  text=text_body, html=html_body)
        record_delivery(SessionFactory, template_code="password_reset",
                        recipient=row["email"], subject=subject,
                        organization_id=row.get("organization_id"))
    except MailNotConfigured:
        record_delivery(SessionFactory, template_code="password_reset",
                        recipient=row["email"], status="failed",
                        organization_id=row.get("organization_id"),
                        detail="no mail server configured")
        # The token is still issued and the answer is still the same one, so
        # this does not tell a stranger anything. It does need to be visible
        # to whoever runs the service.
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="No mail server is configured, so the reset link cannot be "
                   "sent. Set SMTP_HOST and SMTP_SENDER on the iam service.",
        ) from None
    return same


@auth_router.get("/me", response_model=SessionOut)
def me(
    authorization: str | None = Header(default=None),
    db: Session = Depends(get_session),
) -> SessionOut:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="Missing bearer token")
    subject = session_tokens.subject_from_bearer(authorization, db)
    if not subject:
        raise HTTPException(status_code=401, detail="Invalid token")
    # A session reads only its own subject's identity.
    identity_context(db, subject=subject)
    session = _load_session(db, subject)
    if session is None:
        raise HTTPException(status_code=401, detail="Unknown or inactive user")
    # /me answers "who am I", not "give me a new session": the caller keeps
    # the token it already holds, with the expiry it came with. Handing back a
    # fresh one here would let any live token renew itself forever.
    session.token = authorization.split(" ", 1)[1].strip()
    return session


@auth_router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(
    authorization: str | None = Header(default=None),
    db: Session = Depends(get_session),
) -> Response:
    """End this session everywhere, not just in this browser.

    A signed token stays valid until it expires however many times the client
    forgets it, and a copy may be sitting in a proxy log or another tab. So
    its ``jti`` is recorded as revoked and every service refuses it from the
    next request on.

    Always 204, including for a token that is already expired, revoked or
    malformed: the caller wanted to be signed out, and they are. A local dev
    token has no identity of its own to revoke and is simply forgotten by the
    client, as before.
    """
    claims = None
    if authorization and authorization.lower().startswith("bearer "):
        claims = session_tokens.decode(authorization.split(" ", 1)[1])
    if claims is not None and claims.jti is not None:
        system_context(db, reason="sign-out")
        db.execute(
            text(
                """
                INSERT INTO iam.revoked_sessions (jti, subject, expires_at)
                VALUES (:j, :s, to_timestamp(:e))
                ON CONFLICT (jti) DO NOTHING
                """
            ),
            {"j": claims.jti, "s": claims.subject, "e": claims.expires_at},
        )
        # Housekeeping while we are here: a revoked token that has also
        # expired is refused on its expiry alone, so its row is dead weight
        # in a table every request reads.
        db.execute(text("DELETE FROM iam.revoked_sessions "
                        "WHERE expires_at < now()"))
    return Response(status_code=status.HTTP_204_NO_CONTENT)
