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

from chirala_common.db import system_context
from chirala_common.routing import TransactionalRoute
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import text
from sqlalchemy.orm import Session

from .database import get_session

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
def request_demo(body: DemoRequestIn,
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
    return THANKS
