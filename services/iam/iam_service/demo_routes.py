"""Book a demo: the one thing a stranger can write.

The landing page offers two actions, sign in or book a demo. Sign-in is the
identity provider's business. This module is the other one. It takes a
hotel's contact details and stores them as a lead in ``platform.demo_requests``,
where the platform console follows them up (see platform_ops_routes.py).

It is public, so it is written for abuse first:

* **A honeypot, not a CAPTCHA.** ``website`` is a field no person sees. A
  bot that fills every input fills it, and is told "thank you" and stored
  nowhere. Telling it that it failed would only teach it which field to skip.
* **One open request per address per day.** A second submission from the same
  email inside 24 hours is accepted and not stored again. The person who
  pressed the button twice sees the same thanks, and the console sees one lead.
* **A ceiling on the whole table.** Past ``HOURLY_CAP`` new requests an hour
  the endpoint answers 429. Real demand never gets near it, and a flood fills
  the sales inbox for an hour at most, not for good.

No tenant parameter, no caller, and no tenant data read or written. It runs
in a named system context only to insert one row in a table that nothing on
the tenant side can see.
"""
from __future__ import annotations

import logging

from chirala_common.db import system_context
from chirala_common.delivery_log import record_delivery
from chirala_common.mailer import MailNotConfigured, send as send_mail
from chirala_common.routing import TransactionalRoute
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import text
from sqlalchemy.orm import Session

from .database import SessionFactory, get_session
from .mail import demo_alert_email, demo_confirmation_email
from .settings import settings

log = logging.getLogger("uvicorn.error").getChild("demo")

public_router = APIRouter(
    prefix="/public", tags=["public"], route_class=TransactionalRoute
)

#: New demo requests accepted per hour across everyone, before 429.
HOURLY_CAP = 60

THANKS = {
    "detail": "Thank you. Our team will be in touch shortly "
              "to set up your demo.",
}


class DemoRequestIn(BaseModel):
    full_name: str = Field(min_length=2, max_length=120)
    email: str = Field(min_length=5, max_length=254)
    phone: str = Field(min_length=7, max_length=20)
    property_name: str = Field(min_length=2, max_length=160)
    city: str | None = Field(default=None, max_length=80)
    state: str | None = Field(default=None, max_length=80)
    country: str | None = Field(default=None, max_length=80)
    rooms: int | None = Field(default=None, ge=1, le=5000)
    message: str | None = Field(default=None, max_length=1000)
    #: The honeypot. Hidden from people. See the module docstring.
    website: str | None = None

    @field_validator("full_name", "property_name", "city", "state", "country",
                     "message")
    @classmethod
    def _strip(cls, v: str | None) -> str | None:
        v = v.strip() if v else v
        return v or None

    @field_validator("email")
    @classmethod
    def _email(cls, v: str) -> str:
        v = v.strip().lower()
        local, _, domain = v.partition("@")
        if not local or "." not in domain or " " in v:
            raise ValueError("Enter a valid email address")
        return v

    @field_validator("phone")
    @classmethod
    def _phone(cls, v: str) -> str:
        digits = "".join(ch for ch in v if ch.isdigit())
        if not 7 <= len(digits) <= 15 or any(
                ch not in "0123456789 +-()" for ch in v):
            raise ValueError("Enter a valid phone number")
        return v.strip()


def get_public_session(db: Session = Depends(get_session)) -> Session:
    """System context, named, for the one insert this module makes."""
    system_context(db, reason="public: demo request from the landing page")
    return db


@public_router.post("/demo-requests", status_code=status.HTTP_202_ACCEPTED)
def request_demo(body: DemoRequestIn, background: BackgroundTasks,
                 db: Session = Depends(get_public_session)) -> dict:
    if body.website:
        return THANKS

    email = body.email
    duplicate = db.execute(
        text("SELECT 1 FROM platform.demo_requests "
             "WHERE lower(email) = :em "
             "AND created_at > now() - interval '24 hours' LIMIT 1"),
        {"em": email},
    ).first()
    if duplicate:
        return THANKS

    recent = db.execute(
        text("SELECT count(*) FROM platform.demo_requests "
             "WHERE created_at > now() - interval '1 hour'"),
    ).scalar_one()
    if recent >= HOURLY_CAP:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="We are receiving a lot of requests right now. "
                   "Please try again in a little while.")

    db.execute(
        text("""
            INSERT INTO platform.demo_requests
                (full_name, email, phone, property_name, city, state,
                 country, rooms, message)
            VALUES (:name, :em, :phone, :prop, :city, :state, :country,
                    :rooms, :msg)
        """),
        {"name": body.full_name, "em": email, "phone": body.phone,
         "prop": body.property_name, "city": body.city, "state": body.state,
         "country": body.country,
         "rooms": body.rooms, "msg": body.message},
    )
    # After the commit, like every other mail here: a lead announced to sales
    # and then rolled back would be a phone call about nothing.
    background.add_task(_mail_lead, {**body.model_dump(), "email": email})
    return THANKS


def _send(code: str, to: str, composed: tuple[str, str, str]) -> None:
    subject, text_body, html_body = composed
    try:
        send_mail(settings.mail_config, to=to, subject=subject,
                  text=text_body, html=html_body)
        record_delivery(SessionFactory, template_code=code, recipient=to,
                        subject=subject)
    except MailNotConfigured:
        record_delivery(SessionFactory, template_code=code, recipient=to,
                        subject=subject, status="failed",
                        detail="no mail server configured")
    except Exception as exc:  # noqa: BLE001 - the lead is saved either way
        log.warning("demo mail %s to %s failed: %s", code, to, exc)
        record_delivery(SessionFactory, template_code=code, recipient=to,
                        subject=subject, status="failed", detail=str(exc)[:300])


def _mail_lead(lead: dict) -> None:
    """Tell sales, and tell the prospect. Never raises: the lead is saved."""
    if settings.sales_alert_email:
        _send("demo_alert", settings.sales_alert_email, demo_alert_email(lead))
    _send("demo_confirmation", lead["email"], demo_confirmation_email(lead))


# ------------------------------------------------------------- plans ------
#
# The pricing section of the landing page, without the prices. The latest
# published version of each active plan: its name, summary, limits and the
# modules it includes. Amounts are deliberately left out. The decision is
# "plans, price on request", and a figure published here would be a quote
# nobody meant to make.

#: Modules shown to prospects, and what a hotel calls them. A module missing
#: here (ai_center, or pos until the POS ships) is left off the page, because
#: listing something a demo cannot show is a promise the product cannot keep.
PUBLIC_MODULES = {
    "front_desk": "Front desk & check-in",
    "reservations": "Reservations & calendar",
    "housekeeping": "Housekeeping",
    "guests": "Guest profiles",
    "reports": "Reports",
    "rates": "Rate plans & yield rules",
    "distribution": "Channel manager (OTAs)",
    "booking_engine": "Direct booking engine",
    "administration": "Roles, approvals & audit log",
}
LIMITS = ("properties", "rooms", "active_users")


class PublicPlan(BaseModel):
    code: str
    name: str
    summary: str
    limits: dict[str, int | None]
    modules: list[str]


@public_router.get("/plans", response_model=list[PublicPlan])
def public_plans(db: Session = Depends(get_public_session)) -> list[PublicPlan]:
    rows = db.execute(
        text("""
            SELECT DISTINCT ON (p.id) p.code, p.name, p.summary, p.sort_order,
                   v.id AS version_id
            FROM billing.plans p
            JOIN billing.plan_versions v ON v.plan_id = p.id
            WHERE p.status = 'active' AND v.status = 'published'
            ORDER BY p.id, v.version_no DESC
        """),
    ).mappings().all()
    out = []
    for r in sorted(rows, key=lambda x: x["sort_order"]):
        mods = set(db.execute(
            text("SELECT module_code FROM billing.plan_version_modules "
                 "WHERE plan_version_id = :v"), {"v": r["version_id"]},
        ).scalars())
        limits = {m: v for m, v in db.execute(
            text("SELECT metric_code, limit_value FROM billing.plan_version_limits "
                 "WHERE plan_version_id = :v"), {"v": r["version_id"]},
        ).all() if m in LIMITS}
        out.append(PublicPlan(
            code=r["code"], name=r["name"], summary=r["summary"],
            limits=limits,
            modules=[label for code, label in PUBLIC_MODULES.items() if code in mods],
        ))
    return out
