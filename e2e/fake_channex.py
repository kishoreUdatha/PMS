"""A stand-in for the Channex API, for testing the channel integration offline.

Implements the calls the PMS makes -- groups, properties, room types, rate
plans, webhooks, channels, availability/restrictions pushes, booking revisions
and their acknowledgement -- in Channex's JSON:API shape, keeps everything in
memory, and records every request so a test can assert on what was sent.

Control endpoints (not part of Channex) under /_fake:
  POST /_fake/revisions        body = revision attributes -> {"id": ...}
  POST /_fake/fail_fetch       {"times": n}  next n revision fetches return 500
  GET  /_fake/calls            every request received, in order
  GET  /_fake/state            stored objects
  POST /_fake/reset

    uvicorn e2e.fake_channex:app --port 9100
    CHANNEX_API_URL=http://localhost:9100/api/v1 CHANNEX_API_KEY=fake-key
"""
from __future__ import annotations

import uuid
from collections import defaultdict

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

app = FastAPI()
API = "/api/v1"
store: dict[str, dict[str, dict]] = defaultdict(dict)
calls: list[dict] = []
control = {"fail_fetch": 0}
KEY = "fake-key"


def _obj(kind: str, attrs: dict) -> dict:
    return {"id": attrs["id"], "type": kind, "attributes": attrs}


@app.middleware("http")
async def record(request: Request, call_next):
    body = await request.body()
    if request.url.path.startswith(API):
        calls.append({"method": request.method, "path": request.url.path,
                      "query": str(request.url.query),
                      "key": request.headers.get("user-api-key"),
                      "body": body.decode(errors="ignore")[:4000]})
        if request.headers.get("user-api-key") != KEY:
            return JSONResponse({"errors": {"title": "Unauthorized"}}, 401)
    return await call_next(request)


def _filtered(kind: str, request: Request) -> list[dict]:
    items = list(store[kind].values())
    for field in ("property_id", "group_id"):
        v = request.query_params.get(f"filter[{field}]")
        if v:
            items = [i for i in items if i.get(field) == v]
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


@app.post(f"{API}/channels/test_connection")
async def test_connection(request: Request):
    return {"data": {"status": "ok"}}


@app.post(f"{API}/availability")
async def availability(request: Request):
    values = (await request.json()).get("values") or []
    store["pushes"].setdefault("availability", {"values": []})["values"] += values
    return {"data": [{"id": str(uuid.uuid4()), "type": "task"}], "meta": {"message": "Success"}}


@app.post(f"{API}/restrictions")
async def restrictions(request: Request):
    values = (await request.json()).get("values") or []
    store["pushes"].setdefault("restrictions", {"values": []})["values"] += values
    return {"data": [{"id": str(uuid.uuid4()), "type": "task"}], "meta": {"message": "Success"}}


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


@app.get("/_fake/calls")
async def get_calls():
    return calls


@app.get("/_fake/state")
async def state():
    return store


@app.post("/_fake/reset")
async def reset():
    store.clear(); calls.clear(); control["fail_fetch"] = 0
    return {"ok": True}
