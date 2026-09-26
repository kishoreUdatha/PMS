"""A stand-in for the Channex API, for testing the channel integration offline.

Implements the calls the PMS makes -- groups, properties, room types, rate
plans, webhooks, channels, availability/restrictions pushes, booking revisions
and their acknowledgement -- in Channex's JSON:API shape, keeps everything in
memory, and records every request so a test can assert on what was sent.

Control endpoints (not part of Channex) under /_fake:
  POST /_fake/revisions        body = revision attributes -> {"id": ...}
  POST /_fake/fail_fetch       {"times": n}  next n revision fetches return 500
  POST /_fake/ota_rooms        {hotel_id: rooms}  the OTA rooms mapping_details returns
  GET  /_fake/calls            every request received, in order
  GET  /_fake/state            stored objects
  POST /_fake/reset

    uvicorn e2e.fake_channex:app --port 9100
    CHANNEX_API_URL=http://localhost:9100/api/v1 CHANNEX_API_KEY=fake-key
"""
from __future__ import annotations

import time
import uuid
from collections import defaultdict

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

app = FastAPI()
API = "/api/v1"
store: dict[str, dict[str, dict]] = defaultdict(dict)
calls: list[dict] = []
control = {"fail_fetch": 0, "limit": 10, "ota_rooms": {}}
hits: dict[tuple, list[float]] = defaultdict(list)
KEY = "fake-key"


def _obj(kind: str, attrs: dict) -> dict:
    # As Channex answers: a rate plan's room type is a relationship, not an
    # attribute.
    if kind == "rate_plan" and "room_type_id" in attrs:
        attrs = dict(attrs)
        rt = attrs.pop("room_type_id")
        return {"id": attrs["id"], "type": kind, "attributes": attrs,
                "relationships": {"room_type": {"data": {"type": "room_type", "id": rt}}}}
    # A channel's group is a relationship too; real Channex has no group_id
    # attribute on a channel, and code reading one would never match.
    if kind == "channel":
        attrs = dict(attrs)
        grp = attrs.pop("group_id", None)
        attrs.setdefault("is_active", False)
        attrs.setdefault("rate_plans", [])
        return {"id": attrs["id"], "type": kind, "attributes": attrs,
                "relationships": {"group": {"data": {"type": "group", "id": grp}}}}
    return {"id": attrs["id"], "type": kind, "attributes": attrs}


@app.middleware("http")
async def record(request: Request, call_next):
    body = await request.body()
    if request.url.path.startswith(API):
        calls.append({"method": request.method, "path": request.url.path,
                      "query": str(request.url.query),
                      "key": request.headers.get("user-api-key"),
                      "body": body.decode(errors="ignore")[:500000]})
        if request.headers.get("user-api-key") != KEY:
            return JSONResponse({"errors": {"title": "Unauthorized"}}, 401)
    return await call_next(request)


def _filtered(kind: str, request: Request) -> list[dict]:
    items = list(store[kind].values())
    for field in ("property_id", "group_id"):
        v = request.query_params.get(f"filter[{field}]")
        if v:
            # A channel names its properties in a list, and Channex filters
            # channels by any of them.
            items = [i for i in items if i.get(field) == v
                     or (field == "property_id" and v in (i.get("properties") or []))]
    return items


def _resource(kind: str, singular: str):
    @app.get(f"{API}/{kind}", name=f"list_{kind}")
    async def list_(request: Request):
        return {"data": [_obj(singular, i) for i in _filtered(kind, request)]}

    @app.post(f"{API}/{kind}", name=f"create_{kind}")
    async def create(request: Request):
        body = (await request.json()).get(singular) or {}
        attrs = {"id": str(uuid.uuid4()), **body}
        store[kind][attrs["id"]] = attrs
        return JSONResponse({"data": _obj(singular, attrs)}, 201)

    @app.get(f"{API}/{kind}/{{oid}}", name=f"get_{kind}")
    async def get(oid: str):
        if oid not in store[kind]:
            return JSONResponse({"errors": {"title": "Not found"}}, 404)
        return {"data": _obj(singular, store[kind][oid])}


for _k, _s in [("groups", "group"), ("properties", "property"),
               ("room_types", "room_type"), ("rate_plans", "rate_plan"),
               ("webhooks", "webhook"), ("channels", "channel")]:
    _resource(_k, _s)


@app.post(f"{API}/channels/mapping_details")
async def mapping_details(request: Request):
    """The OTA's rooms for a hotel, once the test has said it authorised."""
    body = await request.json()
    hotel = str(((body.get("settings") or {}).get("hotel_id")) or "")
    rooms = control["ota_rooms"].get(hotel)
    if rooms is None:
        return {"errors": None}
    return {"data": {"rooms": rooms}}


@app.put(f"{API}/channels/{{oid}}")
async def update_channel(oid: str, request: Request):
    if oid not in store["channels"]:
        return JSONResponse({"errors": {"title": "Not found"}}, 404)
    body = (await request.json()).get("channel") or {}
    ch = store["channels"][oid]
    if "rate_plans" in body:
        # Like the real one: whatever rate plan it is given, whoever's it is.
        ch["rate_plans"] = [{"id": str(uuid.uuid4()), **rp} for rp in body["rate_plans"]]
    return {"data": _obj("channel", ch)}


@app.post(f"{API}/channels/{{oid}}/activate")
async def activate_channel(oid: str):
    if oid not in store["channels"]:
        return JSONResponse({"errors": {"title": "Not found"}}, 404)
    store["channels"][oid]["is_active"] = True
    return {"meta": {"message": "Success"}}


@app.post(f"{API}/channels/{{oid}}/deactivate")
async def deactivate_channel(oid: str):
    if oid not in store["channels"]:
        return JSONResponse({"errors": {"title": "Not found"}}, 404)
    store["channels"][oid]["is_active"] = False
    return {"meta": {"message": "Success"}}


@app.post("/_fake/ota_rooms")
async def set_ota_rooms(request: Request):
    """{"hotel_id": [...rooms...]}: that hotel has authorised; these are its
    rooms and rates as the OTA returns them."""
    control["ota_rooms"].update(await request.json())
    return {"ok": True}


@app.post(f"{API}/channels/test_connection")
async def test_connection(request: Request):
    return {"data": {"status": "ok"}}


def _limited(endpoint: str, values: list) -> bool:
    """Channex: 10 requests a minute per property per endpoint, then 429."""
    prop = (values[0] if values else {}).get("property_id", "")
    now = time.time()
    recent = [t for t in hits[(prop, endpoint)] if now - t < 60]
    hits[(prop, endpoint)] = recent
    if len(recent) >= control["limit"]:
        return True
    recent.append(now)
    return False


@app.post(f"{API}/availability")
async def availability(request: Request):
    values = (await request.json()).get("values") or []
    if _limited("availability", values):
        return JSONResponse({"errors": {"code": "too_many_requests"}}, 429)
    store["pushes"].setdefault("availability", {"values": []})["values"] += values
    return {"data": [{"id": str(uuid.uuid4()), "type": "task"}], "meta": {"message": "Success"}}


@app.post(f"{API}/restrictions")
async def restrictions(request: Request):
    values = (await request.json()).get("values") or []
    if _limited("restrictions", values):
        return JSONResponse({"errors": {"code": "too_many_requests"}}, 429)
    store["pushes"].setdefault("restrictions", {"values": []})["values"] += values
    return {"data": [{"id": str(uuid.uuid4()), "type": "task"}], "meta": {"message": "Success"}}


@app.get(f"{API}/booking_revisions/feed")
async def revision_feed():
    """Revisions not yet acknowledged -- what Channex offers a PMS to poll."""
    return {"data": [_obj("booking_revision", r)
                     for r in store["revisions"].values()
                     if not r.get("acknowledged")]}


@app.get(f"{API}/booking_revisions/{{rid}}")
async def get_revision(rid: str):
    if control["fail_fetch"] > 0:
        control["fail_fetch"] -= 1
        return JSONResponse({"errors": {"title": "Temporary failure"}}, 500)
    rev = store["revisions"].get(rid)
    if rev is None:
        return JSONResponse({"errors": {"title": "Not found"}}, 404)
    return {"data": _obj("booking_revision", rev)}


@app.post(f"{API}/booking_revisions/{{rid}}/ack")
async def ack(rid: str):
    if rid in store["revisions"]:
        store["revisions"][rid]["acknowledged"] = True
    return {"meta": {"message": "Success"}}


# ---------------------------------------------------------------- control --
@app.post("/_fake/revisions")
async def add_revision(request: Request):
    attrs = await request.json()
    attrs.setdefault("id", str(uuid.uuid4()))
    attrs.setdefault("acknowledged", False)
    store["revisions"][attrs["id"]] = attrs
    return {"id": attrs["id"]}


@app.post("/_fake/fail_fetch")
async def fail_fetch(request: Request):
    control["fail_fetch"] = int((await request.json()).get("times", 1))
    return control


@app.post("/_fake/limit")
async def set_limit(request: Request):
    control["limit"] = int((await request.json()).get("limit", 10))
    hits.clear()
    return control


@app.get("/_fake/calls")
async def get_calls():
    return calls


@app.get("/_fake/state")
async def state():
    return store


@app.post("/_fake/reset")
async def reset():
    store.clear(); calls.clear(); hits.clear()
    control.update(fail_fetch=0, limit=10, ota_rooms={})
    return {"ok": True}
