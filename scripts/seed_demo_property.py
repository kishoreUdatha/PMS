"""Seed a clearly FICTIONAL demo tenant for marketing screenshots.

Creates "Palm Cove Hospitality (Demo)" with one property, "Palm Cove Demo
Resort", an owner who can sign in, a finished setup, four room types, fourteen
rooms and a fortnight of made-up reservations -- a few of them already checked
in today. Nothing here names a real hotel, guest or company: guest phones are
+91 90000 1xxxx, guest e-mails are @example.com, everything else is .example.

HOW TO RUN (from the repository root, with the compose stack up):

    python scripts/seed_demo_property.py

That is the whole thing. The host part uses only the standard library; it
copies this same file into the iam, finance and booking-core containers and
runs one stage in each, so every write goes through the services' own code
(their settings, row-level-security context and, for rooms, rates and
bookings, their real HTTP routes called in-process with fastapi's TestClient).

    python scripts/seed_demo_property.py --reset-password   # new owner password

Re-running is safe. The organisation is matched by name and reused; room
types, rooms, buildings, booking sources, guests and reservations are each
matched before being created (reservations by a fixed idempotency key), so a
second run adds nothing and a run that failed half-way simply carries on.
Reservation dates are relative to the day of the FIRST run, so re-running on a
later day does not add a second fortnight.

The owner's password is generated on the first run and written to
scripts/demo_property_credentials.txt (git-ignored). It is never printed.

Stages (run inside containers by the host part; listed for debugging only):

    docker compose exec -T iam          python /tmp/seed_demo_property.py --stage iam-create   < json
    docker compose exec -T iam          python /tmp/seed_demo_property.py --stage iam-profile  < json
    docker compose exec -T finance      python /tmp/seed_demo_property.py --stage finance      < json
    docker compose exec -T booking-core python /tmp/seed_demo_property.py --stage booking      < json
    docker compose exec -T finance      python /tmp/seed_demo_property.py --stage finance-pos  < json
    docker compose exec -T iam          python /tmp/seed_demo_property.py --stage iam-finalize < json

Deliberately NOT done, so nothing leaves the machine:
  * no owner invitation / welcome e-mail (the platform create_tenant route and
    the go-live route both send one) -- the rows are written the way sign-up
    writes them and go-live is recorded directly;
  * guest confirmation e-mails/SMS after confirming a booking are suppressed
    in-process for this run only;
  * no OTA channel connections are made (a connection would be provisioned at
    the channel manager); OTA bookings are tagged with Business Source rows
    "Booking.com", "Agoda" and "MakeMyTrip" instead.
"""

from __future__ import annotations

import argparse
import json
import os
import secrets
import string
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

ORG_NAME = "Palm Cove Hospitality (Demo)"
PROPERTY_NAME = "Palm Cove Demo Resort"
OWNER_EMAIL = "demo.owner@palmcove.example"
OWNER_NAME = "Demo Owner"
OWNER_PHONE = "+91 90000 10000"

PROFILE = {
    "name": PROPERTY_NAME,
    "property_type": "resort",
    "contact_email": "demo@palmcove.example",
    "contact_phone": "+91 90000 10099",
    "address_line": "1 Palm Cove Beach Road (fictional)",
    "city": "Palm Cove",
    "state": "Goa",
    "postal_code": "403999",
    "country": "India",
    "timezone": "Asia/Kolkata",
    "currency": "INR",
}

RESULT_MARK = "@@RESULT "

REPO = Path(__file__).resolve().parent.parent
CREDS = Path(__file__).resolve().parent / "demo_property_credentials.txt"
IN_CONTAINER = "/tmp/seed_demo_property.py"


def emit(obj) -> None:
    print(RESULT_MARK + json.dumps(obj, default=str), flush=True)


def read_input() -> dict:
    raw = sys.stdin.read()
    return json.loads(raw) if raw.strip() else {}


# =========================================================== stage: iam =====

def stage_iam_create(inp: dict) -> None:
    """Organisation, property, owner, roles, defaults -- as sign-up does it."""
    import uuid

    from sqlalchemy import text
    from chirala_common.audit import record_audit
    from chirala_common.db import system_context
    from chirala_common.passwords import hash_password, problem as password_problem
    from iam_service.auth_routes import (
        _new_property_code, _seed_property_defaults, _seed_roles,
    )
    from iam_service.database import SessionFactory

    password = inp.get("password")
    with SessionFactory() as db:
        system_context(db, reason="seed demo property (scripts/seed_demo_property.py)")
        org = db.execute(
            text("SELECT id FROM iam.organizations WHERE name = :n"),
            {"n": ORG_NAME},
        ).scalar()
        created = False
        if org is None:
            if not password:
                raise SystemExit("A new demo tenant needs a password.")
            why = password_problem(password)
            if why:
                raise SystemExit(f"Generated password rejected: {why}")
            # The owner address must be free: sign-up allows one account each.
            if db.execute(text("SELECT 1 FROM iam.users WHERE lower(email) = :e"),
                          {"e": OWNER_EMAIL}).first():
                raise SystemExit(
                    f"{OWNER_EMAIL} already exists outside the demo organisation; "
                    "refusing to touch it.")

            org, prop, user, membership = (uuid.uuid4() for _ in range(4))
            code = _new_property_code(db)
            subject = f"{OWNER_EMAIL.split('@')[0]}-{uuid.uuid4().hex[:6]}"
            db.execute(text("INSERT INTO iam.organizations (id, name) VALUES (:id, :n)"),
                       {"id": org, "n": ORG_NAME})
            db.execute(
                text("""
                    INSERT INTO iam.properties
                        (id, organization_id, code, name, timezone, currency, status)
                    VALUES (:id, :org, :code, :name, 'Asia/Kolkata', 'INR', 'active')
                """),
                {"id": prop, "org": org, "code": code, "name": PROPERTY_NAME},
            )
            db.execute(
                text("""
                    INSERT INTO iam.users
                        (id, identity_provider, subject_id, display_name, status, email, phone)
                    VALUES (:id, 'local', :sub, :name, 'active', :em, :ph)
                """),
                {"id": user, "sub": subject, "name": OWNER_NAME,
                 "em": OWNER_EMAIL, "ph": OWNER_PHONE},
            )
            db.execute(
                text("INSERT INTO iam.user_credentials (user_id, password_hash) "
                     "VALUES (:u, :h)"),
                {"u": user, "h": hash_password(password)},
            )
            db.execute(
                text("INSERT INTO iam.memberships (id, organization_id, user_id, status) "
                     "VALUES (:id, :org, :u, 'active')"),
                {"id": membership, "org": org, "u": user},
            )
            role_id = _seed_roles(db, org)
            db.execute(
                text("""
                    INSERT INTO iam.role_assignments
                        (id, membership_id, role_id, property_id, scope_type)
                    VALUES (gen_random_uuid(), :m, :r, :p, 'property')
                """),
                {"m": membership, "r": role_id, "p": prop},
            )
            db.execute(
                text("INSERT INTO iam.property_onboarding "
                     "(property_id, organization_id, current_step) "
                     "VALUES (:p, :org, 'property')"),
                {"p": prop, "org": org},
            )
            _seed_property_defaults(db, org_id=org, property_id=prop)
            record_audit(
                db, action="account.signed_up", entity_type="property",
                entity_id=str(prop), organization_id=org, property_id=prop,
                actor_subject=subject,
                after={"email": OWNER_EMAIL, "property_code": code,
                       "seeded_by": "scripts/seed_demo_property.py"},
            )
            created = True
        else:
            if password and inp.get("reset"):  # --reset-password
                why = password_problem(password)
                if why:
                    raise SystemExit(f"Generated password rejected: {why}")
                db.execute(
                    text("""
                        UPDATE iam.user_credentials c
                           SET password_hash = :h, failed_attempts = 0,
                               locked_until = NULL, updated_at = now()
                          FROM iam.users u
                         WHERE u.id = c.user_id AND lower(u.email) = :e
                    """),
                    {"h": hash_password(password), "e": OWNER_EMAIL},
                )

        row = db.execute(
            text("""
                SELECT p.id AS property_id, p.code, u.id AS user_id, u.subject_id
                FROM iam.properties p
                JOIN iam.memberships m ON m.organization_id = p.organization_id
                JOIN iam.users u ON u.id = m.user_id AND lower(u.email) = :e
                WHERE p.organization_id = :org AND p.name = :pn
            """),
            {"org": org, "e": OWNER_EMAIL, "pn": PROPERTY_NAME},
        ).mappings().first()
        if row is None:
            raise SystemExit("Demo organisation exists but its property/owner is missing.")
        db.commit()

    emit({"organization_id": str(org), "property_id": str(row["property_id"]),
          "property_code": row["code"], "owner_user_id": str(row["user_id"]),
          "owner_subject": row["subject_id"], "created": created})


def _owner_client(subject: str):
    """The iam app, called as the owner with the same dev session token that
    /auth/login issues (base64 'dev:<subject>')."""
    import base64

    from fastapi.testclient import TestClient
    from iam_service.main import app

    token = base64.urlsafe_b64encode(f"dev:{subject}".encode()).decode()
    return TestClient(app), {"Authorization": f"Bearer {token}"}


def _check(resp, what: str):
    if resp.status_code >= 400:
        raise SystemExit(f"{what} failed: HTTP {resp.status_code} {resp.text[:500]}")
    return resp.json() if resp.content else None


def stage_iam_profile(inp: dict) -> None:
    """Step 2 of the wizard, through its own route."""
    client, h = _owner_client(inp["owner_subject"])
    pid = inp["property_id"]
    out = _check(client.put("/onboarding/property", params={"property_id": pid},
                            json=PROFILE, headers=h), "Save property profile")
    emit({"profile": {k: out.get(k) for k in ("name", "city", "contact_email")}})


def stage_iam_finalize(inp: dict) -> None:
    """Rates & policies through step 5's route, then mark the property live.

    Go-live is recorded directly rather than through POST /onboarding/activate,
    because that route e-mails everyone with access and provisions the channel
    manager -- neither is wanted for a demo tenant.
    """
    from sqlalchemy import text
    from chirala_common.db import bind_tenant_context
    from iam_service.database import SessionFactory

    client, h = _owner_client(inp["owner_subject"])
    pid = inp["property_id"]
    rates = _check(client.get("/onboarding/rates", params={"property_id": pid},
                              headers=h), "Read rates")
    extra = {"GARDEN": (1200, 600), "SEAVIEW": (1500, 750),
             "SUITE": (2000, 1000), "VILLA": (2500, 1200)}
    by_name = {v["name"]: k for k, v in ROOM_TYPES.items()}
    for r in rates["rates"]:
        code = by_name.get(r["name"])
        if code:
            r["base_rate"] = ROOM_TYPES[code]["base_rate"]
            r["extra_adult_rate"], r["extra_child_rate"] = extra[code]
    rates["policy"].update({
        "checkin_time": "14:00", "checkout_time": "11:00",
        "advance_kind": "percent", "advance_value": 25,
        "cancellation_name": "Moderate", "free_until_days": 3,
        "policy_text": "Free cancellation up to 3 days before arrival. "
                       "Demo property -- not a real resort.",
    })
    _check(client.put("/onboarding/rates", params={"property_id": pid},
                      json=rates, headers=h), "Save rates & policies")

    with SessionFactory() as db:
        bind_tenant_context(db, organization_id=inp["organization_id"],
                            property_id=pid, user_id=inp["owner_user_id"])
        marks = {k: {"visited": True} for k in (
            "account", "property", "structure", "rooms", "rates", "billing",
            "team", "golive")}
        marks["import"] = {"visited": True, "skipped": True}
        marks["connections"] = {"visited": True, "skipped": True}
        db.execute(
            text("""
                UPDATE iam.property_onboarding
                   SET activated_at = COALESCE(activated_at, now()),
                       activated_by = COALESCE(activated_by, :who),
                       current_step = 'golive',
                       steps = CAST(:marks AS jsonb),
                       updated_at = now(), version = version + 1
                 WHERE property_id = :p
            """),
            {"who": inp["owner_user_id"], "p": pid, "marks": json.dumps(marks)},
        )
        db.commit()

    state = _check(client.get("/onboarding", params={"property_id": pid}, headers=h),
                   "Read onboarding state")
    emit({"activated_at": state.get("activated_at"),
          "ready_to_activate": state.get("ready_to_activate"),
          "incomplete_steps": [s["key"] for s in state["steps"]
                               if not s["complete"] and not s["optional"]]})


# ======================================================= stage: finance =====

def stage_finance(inp: dict) -> None:
    """Billing identity (wizard step 6) and the night-audit hour."""
    from fastapi.testclient import TestClient
    from finance_service.main import app
    from finance_service.settings import settings

    client = TestClient(app)
    h = {"X-Service-Token": settings.service_token,
         "X-Service-Org": inp["organization_id"]}
    pid = inp["property_id"]
    current = _check(client.get("/invoice-settings", params={"property_id": pid},
                                headers=h), "Read invoice settings")
    if not (current.get("legal_name") or "").strip():
        body = {
            "legal_name": ORG_NAME, "tagline": "Demo property for screenshots",
            "tax_inclusive": False,
            "payment_methods": ["cash", "upi", "card", "bank_transfer"],
            "address_line": PROFILE["address_line"], "city": PROFILE["city"],
            "state": "Goa", "state_code": "30", "postal_code": PROFILE["postal_code"],
            "country": "India", "phone": PROFILE["contact_phone"],
            "email": PROFILE["contact_email"], "gst_registered": False,
            "gstin": None, "fiscal_series": "PCD", "next_number": 1,
            "footer_note": "Demo invoice. Palm Cove is a fictional property.",
        }
        _check(client.put("/invoice-settings", params={"property_id": pid},
                          json=body, headers=h), "Save invoice settings")
    audit = _check(client.get("/night-audit/settings", params={"property_id": pid},
                              headers=h), "Read night audit settings")
    if audit.get("audit_hour") is None:
        _check(client.put("/night-audit/settings", params={"property_id": pid},
                          json={"audit_hour": 2}, headers=h), "Save night audit hour")
    emit({"invoice_settings": "ok", "audit_hour": 2})


# ======================================================= stage: booking =====

ROOM_TYPES = {
    "GARDEN": dict(name="Garden Room", base_rate=5500, max_adults=2, max_children=1,
                   max_occupancy=3, bed_setup="Queen", size_sqft=320,
                   room_view="Garden", description="Ground-floor room opening onto the palm garden.",
                   rooms="101-105", floor="F1", amenities=["wifi", "tv", "ac", "kettle"]),
    "SEAVIEW": dict(name="Sea View Room", base_rate=7500, max_adults=2, max_children=1,
                    max_occupancy=3, bed_setup="King", size_sqft=360,
                    room_view="Sea", description="Upper-floor room with a balcony facing the bay.",
                    rooms="201-205", floor="F2",
                    amenities=["wifi", "tv", "ac", "balcony", "minibar"]),
    "SUITE": dict(name="Deluxe Suite", base_rate=11500, max_adults=3, max_children=1,
                  max_occupancy=4, bed_setup="King", size_sqft=620,
                  room_view="Sea", description="Separate living room, bathtub and wrap-around balcony.",
                  rooms="301-302", floor="F3",
                  amenities=["wifi", "tv", "ac", "sofa", "bathtub", "balcony", "minibar"]),
    "VILLA": dict(name="Pool Villa", base_rate=18500, max_adults=4, max_children=2,
                  max_occupancy=6, bed_setup="King", size_sqft=1100,
                  room_view="Pool", description="Private villa with its own plunge pool.",
                  rooms="401-402", floor="VG",
                  amenities=["wifi", "tv", "ac", "pool", "sofa", "safe", "minibar"]),
}

BUILDINGS = [
    ("MAIN", "Main Wing", [("F1", "First Floor", "101", "105"),
                           ("F2", "Second Floor", "201", "205"),
                           ("F3", "Third Floor", "301", "302")]),
    ("VILLA", "Villa Court", [("VG", "Villa Court", "401", "402")]),
]

SOURCES = ["Booking.com", "Agoda", "MakeMyTrip"]

# Fictional guests. Phones +91 90000 1xxxx, e-mails @example.com.
GUESTS = {
    "aarav":   dict(full_name="Aarav Mehta", title="Mr", nationality="Indian",
                    city="Pune", state="Maharashtra", country="India", postal_code="411001",
                    id_type="aadhaar", id_number="9000 0000 0001"),
    "sofia":   dict(full_name="Sofia Rossi", title="Ms", nationality="Italian",
                    city="Milan", country="Italy", postal_code="20121",
                    id_type="passport", id_number="DEMO-IT-0002"),
    "james":   dict(full_name="James Carter", title="Mr", nationality="American",
                    city="Austin", country="United States", postal_code="73301",
                    id_type="passport", id_number="DEMO-US-0003"),
    "mei":     dict(full_name="Mei Tanaka", title="Ms", nationality="Japanese",
                    city="Osaka", country="Japan", postal_code="530-0001",
                    id_type="passport", id_number="DEMO-JP-0004"),
    "priya":   dict(full_name="Priya Nair", title="Ms", nationality="Indian",
                    city="Kochi", state="Kerala", country="India", postal_code="682001",
                    id_type="driving_licence", id_number="DEMO-KL-0005"),
    "liam":    dict(full_name="Liam O'Connor", title="Mr", nationality="Irish",
                    city="Cork", country="Ireland", postal_code="T12 X000"),
    "ananya":  dict(full_name="Ananya Reddy", title="Ms", nationality="Indian",
                    city="Hyderabad", state="Telangana", country="India", postal_code="500001"),
    "lucas":   dict(full_name="Lucas Müller", title="Mr", nationality="German",
                    city="Munich", country="Germany", postal_code="80331"),
    "fatima":  dict(full_name="Fatima Khan", title="Ms", nationality="Indian",
                    city="Lucknow", state="Uttar Pradesh", country="India", postal_code="226001"),
    "vikram":  dict(full_name="Vikram Kapoor", title="Mr", nationality="Indian",
                    city="New Delhi", state="Delhi", country="India", postal_code="110001"),
    "emma":    dict(full_name="Emma Wilson", title="Ms", nationality="British",
                    city="Bristol", country="United Kingdom", postal_code="BS1 0AA"),
    "chen":    dict(full_name="Chen Wei", title="Mr", nationality="Singaporean",
                    city="Singapore", country="Singapore", postal_code="018900"),
    "isabella": dict(full_name="Isabella García", title="Ms", nationality="Spanish",
                     city="Valencia", country="Spain", postal_code="46001"),
    "arjun":   dict(full_name="Arjun Sharma", title="Mr", nationality="Indian",
                    city="Bengaluru", state="Karnataka", country="India", postal_code="560001"),
    "olivia":  dict(full_name="Olivia Brown", title="Ms", nationality="Australian",
                    city="Perth", country="Australia", postal_code="6000"),
    "meera":   dict(full_name="Meera Pillai", title="Ms", nationality="Indian",
                    city="Chennai", state="Tamil Nadu", country="India", postal_code="600001"),
    "kiara":   dict(full_name="Kiara Desai", title="Ms", nationality="Indian",
                    city="Ahmedabad", state="Gujarat", country="India", postal_code="380001"),
}

# (key, guest, arrive offset, depart offset, lines[(type, adults, children, rate)],
#  source, business source, extra hold fields, action, advance)
# action: "checkin" = checked in via the front-desk flow; "assign" = confirmed
# with a room; "confirm" = confirmed, room left for the desk to assign.
BOOKINGS = [
    ("01", "aarav", 0, 3, [("SEAVIEW", 2, 0, 7500)], "direct", None, {}, "checkin", 0),
    ("02", "sofia", 0, 4, [("SUITE", 2, 0, 10900)], "ota", "Booking.com",
     {"reference": "BDC-DEMO-58213"}, "checkin", 0),
    ("03", "james", 0, 2, [("GARDEN", 1, 0, 5500)], "walk_in", None, {}, "checkin", 0),
    ("04", "mei", 0, 5, [("VILLA", 2, 1, 17800)], "ota", "Agoda",
     {"reference": "AGD-DEMO-77140"}, "checkin", 0),
    ("05", "priya", -1, 1, [("GARDEN", 2, 0, 5500)], "phone", None, {}, "checkin", 0),
    ("06", "liam", 0, 2, [("SEAVIEW", 2, 0, 7200)], "ota", "MakeMyTrip",
     {"reference": "MMT-DEMO-30418"}, "assign", 0),
    ("07", "ananya", 0, 1, [("GARDEN", 2, 1, 5500)], "website", None,
     {"expected_arrival_time": "18:30:00"}, "assign", 2750),
    ("08", "lucas", 1, 4, [("SEAVIEW", 2, 0, 7300)], "ota", "Booking.com",
     {"reference": "BDC-DEMO-58377"}, "assign", 0),
    ("09", "fatima", 2, 4, [("SUITE", 2, 1, 11500)], "direct", None,
     {"special_requests": "Anniversary -- flowers in the room, please."}, "assign", 11500),
    ("10", "vikram", 3, 5, [("GARDEN", 2, 1, 5500), ("GARDEN", 2, 0, 5500),
                            ("SEAVIEW", 2, 0, 7500)], "phone", None,
     {"special_requests": "Kapoor family -- three rooms close together."}, "assign", 10000),
    ("11", "emma", 4, 6, [("VILLA", 2, 0, 18500)], "website", None, {}, "assign", 9250),
    ("12", "chen", 2, 3, [("GARDEN", 1, 0, 5200)], "ota", "Agoda",
     {"reference": "AGD-DEMO-77391"}, "assign", 0),
    ("13", "isabella", 6, 9, [("SUITE", 2, 0, 10900)], "ota", "Booking.com",
     {"reference": "BDC-DEMO-58902"}, "confirm", 0),
    ("14", "arjun", 7, 9, [("SEAVIEW", 1, 0, 7500)], "corporate", None,
     {"company_name": "Northwind Demo Pvt Ltd (fictional)"}, "confirm", 0),
    ("15", "olivia", 8, 11, [("VILLA", 2, 2, 18500)], "direct", None, {}, "confirm", 15000),
    ("16", "meera", 9, 11, [("GARDEN", 2, 0, 5500)], "travel_agent", None,
     {"travel_agent": "Coastal Trails Travel (Demo)"}, "confirm", 0),
    ("17", "kiara", 7, 10, [("SEAVIEW", 2, 0, 6900), ("SEAVIEW", 2, 0, 6900),
                           ("SEAVIEW", 2, 0, 6900)], "group", None,
     {"company_name": "Sunrise Yoga Retreat (Demo)",
      "special_requests": "Yoga group -- early breakfast at 6:30."}, "confirm", 20000),
]


#: The demo restaurant's menu: (category, bills_as, [(code, name, price)]).
MENU = [
    ("Breakfast", "restaurant", [
        ("BF01", "Masala Dosa", 180), ("BF02", "Poha", 140),
        ("BF03", "Continental Breakfast", 420)]),
    ("Mains", "restaurant", [
        ("MN01", "Goan Fish Curry & Rice", 540), ("MN02", "Paneer Butter Masala", 420),
        ("MN03", "Prawn Balchão", 620), ("MN04", "Veg Thali", 380),
        ("MN05", "Grilled Chicken", 560)]),
    ("Drinks", "restaurant", [
        ("DR01", "Fresh Lime Soda", 120), ("DR02", "Filter Coffee", 90),
        ("DR03", "Kokum Cooler", 150), ("DR04", "Tender Coconut", 110)]),
    ("Desserts", "restaurant", [
        ("DS01", "Bebinca", 220), ("DS02", "Gulab Jamun", 160)]),
]
OUTLETS = [("Palm Cove Kitchen", "restaurant", 10), ("Sunset Bar", "bar", 6)]


def stage_finance_pos(inp: dict) -> None:
    """A menu, two outlets with tables, and two open checks for the POS screen."""
    from fastapi.testclient import TestClient
    from finance_service.main import app
    from finance_service.settings import settings

    client = TestClient(app)
    h = {"X-Service-Token": settings.service_token,
         "X-Service-Org": inp["organization_id"]}
    pid = inp["property_id"]
    q = {"property_id": pid}

    cats = {c["name"]: c for c in _check(client.get(
        "/service-categories", params={**q, "include_inactive": True}, headers=h),
        "List menu categories")}
    items = {i["code"]: i for i in _check(client.get(
        "/service-items", params={**q, "include_inactive": True}, headers=h),
        "List menu items")}
    for order, (name, bills_as, rows) in enumerate(MENU, start=1):
        if name not in cats:
            cats[name] = _check(client.post("/service-categories", headers=h, json={
                "property_id": pid, "name": name, "bills_as": bills_as,
                "sort_order": order}), f"Create category {name}")
        for code, item, price in rows:
            if code not in items:
                items[code] = _check(client.post("/service-items", headers=h, json={
                    "property_id": pid, "code": code, "name": item,
                    "category_id": cats[name]["id"], "price": price}),
                    f"Create item {item}")

    outlets = {o["name"]: o for o in _check(client.get(
        "/pos/outlets", params=q, headers=h), "List outlets")}
    for name, kind, tables in OUTLETS:
        if name not in outlets:
            _check(client.post("/pos/outlets", params=q, headers=h, json={
                "name": name, "kind": kind, "tables": tables}), f"Create outlet {name}")
    outlets = {o["name"]: o for o in _check(client.get(
        "/pos/outlets", params=q, headers=h), "List outlets")}

    opened = 0
    if not _check(client.get("/pos/checks", params=q, headers=h), "List open checks"):
        kitchen = outlets["Palm Cove Kitchen"]
        plans = [("T2", [("MN01", 2), ("MN04", 1), ("DR03", 3)], True),
                 ("T5", [("BF03", 2), ("DR02", 2)], False)]
        for label, lines, kot in plans:
            table = next(t for t in kitchen["tables"] if t["label"] == label)
            chk = _check(client.post("/pos/checks", params=q, headers=h, json={
                "outlet_id": kitchen["id"], "table_id": table["id"], "covers": 2}),
                f"Open check at {label}")
            _check(client.post(f"/pos/checks/{chk['id']}/lines", params=q, headers=h,
                               json={"lines": [{"item_id": items[c]["id"], "quantity": n}
                                               for c, n in lines]}), "Add lines")
            if kot:
                _check(client.post(f"/pos/checks/{chk['id']}/kot", params=q, headers=h),
                       "Send KOT")
            opened += 1
    emit({"categories": len(cats), "items": len(items),
          "outlets": len(outlets), "checks_opened": opened})


def _tiny_png() -> bytes:
    """A small grey PNG standing in for the photographed ID the desk requires."""
    import struct
    import zlib

    w, h = 64, 40
    raw = b"".join(b"\x00" + b"\xc8\xc8\xc8" * w for _ in range(h))

    def chunk(tag, data):
        return (struct.pack(">I", len(data)) + tag + data
                + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))

    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


def stage_booking(inp: dict) -> None:
    import datetime as dt
    from zoneinfo import ZoneInfo

    import httpx
    from fastapi.testclient import TestClient
    from sqlalchemy import text
    from chirala_common.db import bind_tenant_context

    import booking_core.checkin_routes as checkin_routes
    import booking_core.routes as booking_routes
    from booking_core.database import SessionFactory
    from booking_core.main import app
    from booking_core.settings import settings

    # This run must not message anybody: no guest confirmation, no welcome.
    booking_routes._email_guest_confirmation = lambda *a, **k: None
    checkin_routes._send_welcome = lambda *a, **k: None

    org, pid, code = inp["organization_id"], inp["property_id"], inp["property_code"]
    client = TestClient(app)
    h = {"X-Service-Token": settings.service_token, "X-Service-Org": org}
    P = {"property_id": pid}

    def get(path, **params):
        return _check(client.get(path, params={**P, **params}, headers=h), f"GET {path}")

    def post(path, body=None, params=None, what=None, **kw):
        return _check(client.post(path, json=body, params=params if params is not None else P,
                                  headers=h, **kw), what or f"POST {path}")

    def q(sql, **params):
        with SessionFactory() as s:
            bind_tenant_context(s, organization_id=org, property_id=pid, is_service=True)
            return [dict(r) for r in s.execute(text(sql), params).mappings()]

    # ---- structure: buildings and floors
    existing_b = {b["code"]: b for b in get("/buildings")}
    floor_ids: dict[str, str] = {}
    for order, (bcode, bname, floors) in enumerate(BUILDINGS):
        b = existing_b.get(bcode) or post("/buildings", {"code": bcode, "name": bname,
                                                         "display_order": order})
        have = {f["code"]: f["id"] for f in q(
            "SELECT code, id::text FROM property.floors WHERE building_id = :b", b=b["id"])}
        for forder, (fcode, fname, lo, hi) in enumerate(floors):
            if fcode not in have:
                have[fcode] = post("/floors", {"building_id": b["id"], "code": fcode,
                                               "name": fname, "from_room_no": lo,
                                               "to_room_no": hi,
                                               "display_order": forder})["id"]
            floor_ids[fcode] = have[fcode]

    # ---- room types (screen 061 / wizard step 4 route)
    amen = {a["code"]: a["id"] for a in q(
        "SELECT code, id::text FROM property.amenities WHERE property_id = :p", p=pid)}
    types = {r["code"]: r["id"] for r in q(
        "SELECT code, id::text FROM property.room_types WHERE property_id = :p", p=pid)}
    for tcode, t in ROOM_TYPES.items():
        if tcode in types:
            continue
        body = {k: t[k] for k in ("name", "base_rate", "max_adults", "max_children",
                                  "max_occupancy", "bed_setup", "size_sqft",
                                  "room_view", "description")}
        body.update(code=tcode, status="active", default_rate_plan="Best Available Rate",
                    amenity_ids=[amen[a] for a in t["amenities"] if a in amen])
        types[tcode] = post("/room-types/manage", body)["id"]

    # ---- rooms (bulk-create skips codes that already exist)
    for tcode, t in ROOM_TYPES.items():
        post("/rooms/bulk-create", {"room_type_id": types[tcode], "spec": t["rooms"],
                                    "floor_id": floor_ids[t["floor"]],
                                    "max_adults": t["max_adults"],
                                    "max_children": t["max_children"],
                                    "base_rate": t["base_rate"],
                                    "bed_setup": t["bed_setup"]})

    # ---- dates: fixed at the first run, in the property's own timezone
    first = q("SELECT min(created_at) AS t FROM property.room_types WHERE property_id = :p",
              p=pid)[0]["t"]
    tz = ZoneInfo("Asia/Kolkata")
    base = first.astimezone(tz).date() if first else dt.datetime.now(tz).date()
    today = dt.datetime.now(tz).date()

    # Inventory rows are written from today forward; a stay that began before
    # today needs its earlier nights to exist. Same route setup uses.
    earliest = base + dt.timedelta(days=min(b[2] for b in BOOKINGS))
    if earliest < today:
        counts = {r["code"]: r["n"] for r in q(
            "SELECT rt.code, count(*) AS n FROM property.rooms rm JOIN property.room_types rt "
            "ON rt.id = rm.room_type_id WHERE rm.property_id = :p AND rm.status = 'active' "
            "GROUP BY rt.code", p=pid)}
        for tcode, tid in types.items():
            have = q("SELECT 1 FROM booking.room_type_inventory_days WHERE room_type_id = :t "
                     "AND stay_date = :d", t=tid, d=earliest)
            if not have:
                post("/inventory/seed", {"property_id": pid, "room_type_id": tid,
                                         "start_date": str(earliest),
                                         "end_date": str(today - dt.timedelta(days=1)),
                                         "physical_capacity": counts.get(tcode, 0)},
                     params={})

    # ---- business sources (which OTA)
    attrs = {r["name"]: r["id"] for r in q(
        "SELECT name, id::text FROM engagement.booking_attributes "
        "WHERE organization_id = :o AND kind = 'business_source'", o=org)}
    for name in SOURCES:
        if name not in attrs:
            attrs[name] = post("/booking-attributes",
                               {"kind": "business_source", "name": name},
                               params={"organization_id": org})["id"]

    # ---- guests
    guest_ids: dict[str, str] = {}
    for n, (key, g) in enumerate(GUESTS.items(), start=1):
        email = f"{g['full_name'].split()[0].lower()}.demo{n:02d}@example.com"
        email = email.replace("ü", "u").replace("í", "i")
        found = q("SELECT id::text FROM engagement.guests WHERE organization_id = :o "
                  "AND email = :e", o=org, e=email)
        if found:
            guest_ids[key] = found[0]["id"]
            continue
        body = {k: g[k] for k in ("full_name", "title", "nationality", "city",
                                  "country", "postal_code") if k in g}
        body.update(email=email, phone=f"+91 90000 1{n:04d}",
                    address_line=f"{10 + n} Demo Street (fictional)",
                    state=g.get("state"), organization_id=org)
        guest_ids[key] = post("/guests", body, params={})["id"]
        g["email"], g["phone"] = body["email"], body["phone"]
    for n, (key, g) in enumerate(GUESTS.items(), start=1):
        g.setdefault("phone", f"+91 90000 1{n:04d}")
        g.setdefault("address_line", f"{10 + n} Demo Street (fictional)")

    # ---- reservations
    png = _tiny_png()
    summary = []
    for (key, gkey, a_off, d_off, lines, source, bsrc, extra, action, advance) in BOOKINGS:
        arrive = base + dt.timedelta(days=a_off)
        depart = base + dt.timedelta(days=d_off)
        idem = f"seed-demo-{code}-{key}"
        body = {
            "organization_id": org, "property_id": pid,
            "arrival_date": str(arrive), "departure_date": str(depart),
            "idempotency_key": idem, "guest_id": guest_ids[gkey], "source": source,
            "lines": [{"room_type_id": types[t], "units": 1, "adults": a,
                       "children": c, "nightly_rate": rate} for (t, a, c, rate) in lines],
            **extra,
        }
        if bsrc:
            body["business_source_id"] = attrs[bsrc]
        hold = post("/holds", body, params={}, what=f"Hold {key}")
        rid = hold["reservation_id"]
        units = hold.get("reservation_unit_ids") or [hold["reservation_unit_id"]]

        status = q("SELECT status FROM booking.reservations WHERE id = :r", r=rid)[0]["status"]
        if status == "held":
            post(f"/reservations/{rid}/confirm", {}, params={}, what=f"Confirm {key}")

        if action in ("assign", "checkin"):
            for uid, (t, *_rest) in zip(units, lines):
                u = q("SELECT assigned_room_id FROM booking.reservation_units WHERE id = :u",
                      u=uid)[0]
                if u["assigned_room_id"]:
                    continue
                rooms = get("/available-rooms", room_type_id=types[t],
                            arrival_date=str(arrive), departure_date=str(depart))
                free = [r for r in rooms if r["available"]]
                if not free:
                    raise SystemExit(f"No free {t} room for booking {key}")
                post(f"/reservation-units/{uid}/assign", {"room_id": free[0]["room_id"]},
                     params={}, what=f"Assign room for {key}")

        if action == "checkin":
            uid = units[0]
            st = q("SELECT status FROM booking.reservation_units WHERE id = :u",
                   u=uid)[0]["status"]
            if st != "checked_in":
                g = GUESTS[gkey]
                gid = guest_ids[gkey]
                has_doc = q("SELECT 1 FROM engagement.guest_documents WHERE guest_id = :g "
                            "AND kind = 'id_front'", g=gid)
                if not has_doc:
                    _check(client.post(f"/guests/{gid}/documents", params=P, headers=h,
                                       data={"kind": "id_front"},
                                       files={"file": ("demo-id.png", png, "image/png")}),
                           f"Upload ID for {key}")
                guest = {k: g.get(k) for k in ("full_name", "email", "phone", "nationality",
                                               "address_line", "city", "state",
                                               "postal_code", "country", "id_type",
                                               "id_number")}
                guest["email"] = q("SELECT email FROM engagement.guests WHERE id = :g",
                                   g=gid)[0]["email"]
                rate = lines[0][3]
                post(f"/reservation-units/{uid}/complete-check-in", {
                    "guest": guest, "deposit_amount": rate,
                    "deposit_method": "card" if source == "ota" else "upi",
                    "id_verified": True, "signature_captured": True,
                    "policies_accepted": True, "welcome_sent": False,
                    "key_issued": True, "notes": "Demo check-in",
                }, what=f"Check in {key}")

        # An advance taken at booking, through the gateway's settle flow
        # (the same call the New Reservation screen makes). Only once.
        if advance:
            paid = q("""SELECT 1 FROM finance.payment_allocations pa
                        JOIN finance.folios f ON f.id = pa.folio_id
                        WHERE f.reservation_id = :r LIMIT 1""", r=rid)
            if not paid:
                # The settle flow opens the folio. Its own advance path posts
                # without a reference, which finance rightly refuses for UPI,
                # so the advance itself goes to finance's /payments with one.
                r = httpx.post(
                    f"http://gateway:8000/flows/reservations/{rid}/settle",
                    json={"room_charge": 0, "business_date": str(today)},
                    headers=h, timeout=30)
                if r.status_code < 400:
                    r = httpx.post(
                        f"{settings.finance_url}/payments",
                        json={"property_id": pid, "method": "upi",
                              "reference": f"UPI-DEMO-{code}-{key}",
                              "note": "Advance at booking (demo)",
                              "allocations": [{"folio_id": r.json()["folio_id"],
                                               "amount": advance}]},
                        headers=h, timeout=30)
                if r.status_code >= 400:
                    raise SystemExit(f"Advance for {key} not recorded: "
                                     f"{r.status_code} {r.text[:300]}")
        summary.append({"key": key, "number": hold["number"], "guest": GUESTS[gkey]["full_name"],
                        "arrive": str(arrive), "depart": str(depart), "rooms": len(units),
                        "action": action})

    emit({"room_types": len(types), "bookings": summary, "base_date": str(base)})


# ============================================================ host side =====

def compose(*args, input_text: str | None = None, check=True) -> subprocess.CompletedProcess:
    env = {**os.environ, "MSYS_NO_PATHCONV": "1"}
    return subprocess.run(["docker", "compose", *args], cwd=REPO, env=env,
                          input=input_text, capture_output=True, text=True,
                          encoding="utf-8", check=check)


def run_stage(service: str, stage: str, payload: dict) -> dict:
    compose("cp", str(Path(__file__).resolve()), f"{service}:{IN_CONTAINER}")
    p = compose("exec", "-T", service, "python", IN_CONTAINER, "--stage", stage,
                input_text=json.dumps(payload), check=False)
    result = None
    for line in p.stdout.splitlines():
        if line.startswith(RESULT_MARK):
            result = json.loads(line[len(RESULT_MARK):])
    if p.returncode != 0 or result is None:
        sys.stderr.write(p.stdout[-4000:] + "\n" + p.stderr[-6000:] + "\n")
        raise SystemExit(f"Stage {stage} in {service} failed (exit {p.returncode}).")
    return result


def gen_password() -> str:
    alphabet = string.ascii_letters + string.digits
    core = "".join(secrets.choice(alphabet) for _ in range(18))
    return (core[:6] + "-" + core[6:12] + "-" + core[12:] + secrets.choice("!#%+=")
            + secrets.choice(string.digits))


def read_creds_password() -> str | None:
    if not CREDS.exists():
        return None
    for line in CREDS.read_text(encoding="utf-8").splitlines():
        if line.lower().startswith("password:"):
            return line.split(":", 1)[1].strip()
    return None


def write_creds(code: str, password: str) -> None:
    CREDS.write_text(
        "MyGuest demo tenant -- FICTIONAL, for marketing screenshots only.\n"
        "Created by scripts/seed_demo_property.py. Do not commit (git-ignored).\n\n"
        f"Organisation:  {ORG_NAME}\n"
        f"Property:      {PROPERTY_NAME}\n"
        f"Property code: {code}\n"
        f"Email:         {OWNER_EMAIL}\n"
        f"Password:      {password}\n\n"
        "Sign in at http://localhost:5173 with property code, email and password.\n",
        encoding="utf-8")


def try_login(code: str, password: str) -> int:
    req = urllib.request.Request(
        "http://localhost:8111/api/iam/auth/login",
        data=json.dumps({"property_code": code, "email": OWNER_EMAIL,
                         "password": password}).encode(),
        headers={"content-type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status
    except urllib.error.HTTPError as e:
        return e.code
    except OSError:
        return 0


def host_main(reset_password: bool) -> None:
    stored = read_creds_password()
    fresh = gen_password()
    ident = run_stage("iam", "iam-create", {"password": fresh, "reset": reset_password})
    if ident["created"] or reset_password:
        password = fresh
        write_creds(ident["property_code"], password)
        print(f"Owner credentials written to {CREDS}")
    elif stored is None:
        raise SystemExit("The demo tenant exists but no credentials file was found; "
                         "re-run with --reset-password.")
    else:
        password = stored
    print(f"{'Created' if ident['created'] else 'Reusing'} {ORG_NAME} -- "
          f"property code {ident['property_code']}")

    run_stage("iam", "iam-profile", ident)
    print("Property profile saved.")
    run_stage("finance", "finance", ident)
    print("Billing identity and night-audit hour saved.")
    booked = run_stage("booking-core", "booking", ident)
    b = booked["bookings"]
    print(f"Room types: {booked['room_types']}; reservations: {len(b)} "
          f"({sum(x['rooms'] for x in b)} rooms), checked in: "
          f"{sum(1 for x in b if x['action'] == 'checkin')}; dates from {booked['base_date']}.")
    pos = run_stage("finance", "finance-pos", ident)
    print(f"POS: {pos['categories']} menu categories, {pos['items']} items, "
          f"{pos['outlets']} outlets, {pos['checks_opened']} checks opened.")
    fin = run_stage("iam", "iam-finalize", ident)
    print(f"Go-live recorded at {fin['activated_at']}; incomplete required steps: "
          f"{fin['incomplete_steps'] or 'none'}.")
    status = try_login(ident["property_code"], password)
    print(f"Owner sign-in through the gateway: HTTP {status}.")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--stage", choices=["iam-create", "iam-profile", "iam-finalize",
                                        "finance", "finance-pos", "booking"])
    ap.add_argument("--reset-password", action="store_true")
    args = ap.parse_args()
    if not args.stage:
        host_main(args.reset_password)
        return
    inp = read_input()
    {"iam-create": stage_iam_create, "iam-profile": stage_iam_profile,
     "iam-finalize": stage_iam_finalize, "finance": stage_finance,
     "finance-pos": stage_finance_pos,
     "booking": stage_booking}[args.stage](inp)


if __name__ == "__main__":
    main()
