"""Pair an OTA's own rooms and rates with this property's plans, and go live.

Provisioning builds the OTA channel at the channel manager switched off. What
it cannot do is say which of the OTA's rooms and rates each of our rate plans
sells as: those codes live in the OTA's extranet and only exist once the hotel
has authorised the channel manager there. Until now that pairing -- and the
switch that puts the hotel on sale -- was done by hand in the channel
manager's own admin, which no tenant can log in to. So every hotel waited on
somebody at the platform, and that somebody was choosing among every tenant's
rate plans in one account.

These routes let the hotel do it from its own screens:

* ``GET``  reads the OTA's rooms and rates through the channel manager
  (``/channels/mapping_details``), this property's plans, and the pairs the
  channel holds now;
* ``PUT``  writes the pairs;
* ``POST .../go-live`` switches the channel on or off.

**The channel manager does not check whose rate plan it is given.** Tried on
staging: a channel of one tenant's property accepted another tenant's rate
plan without complaint. So the check is here, and it is the only one: a pair
can name only a rate plan this property's own link maps, and the channel must
be this tenant's -- in its group, on its property -- before anything is read
from it or written to it.
"""

from __future__ import annotations

import logging
import uuid

from chirala_common.audit import record_audit
from chirala_common.authz import (
    Caller, assert_property_in_org, build_authz, require_property_permission,
)
from chirala_common.routing import TransactionalRoute
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.orm import Session

from .attribute_routes import _connection_or_404
from .channel_provision import Channex, _error_text
from .database import get_session
from .settings import settings

log = logging.getLogger("uvicorn.error").getChild("ota-mapping")

_get_caller, _require_permission, require_org_permission = build_authz(
    get_session, lambda: settings.environment,
    lambda: settings.service_token,
)

ota_mapping_router = APIRouter(tags=["ota-mapping"],
                               route_class=TransactionalRoute)


class OtaRate(BaseModel):
    code: str
    title: str | None = None
    occupancy: int | None = None


class OtaRoom(BaseModel):
    code: str
    title: str | None = None
    rates: list[OtaRate] = Field(default_factory=list)


class LocalPlan(BaseModel):
    rate_plan_id: uuid.UUID
    code: str
    name: str
    room_type_name: str | None = None
    occupancy: int | None = None


class Pair(BaseModel):
    ota_room_code: str = Field(min_length=1, max_length=80)
    ota_rate_code: str = Field(min_length=1, max_length=80)
    rate_plan_id: uuid.UUID
    occupancy: int | None = Field(default=None, ge=1, le=50)


class MappingIn(BaseModel):
    pairs: list[Pair] = Field(default_factory=list, max_length=400)


class GoLiveIn(BaseModel):
    live: bool


class MappingOut(BaseModel):
    connection_id: uuid.UUID
    channel_id: str
    channel: str
    live: bool
    ota_rooms: list[OtaRoom]
    #: Why the OTA's rooms could not be read, in words. Usually that the hotel
    #: has not authorised the channel manager in its extranet yet.
    ota_rooms_error: str | None = None
    plans: list[LocalPlan]
    pairs: list[Pair]
    #: Pairs on the channel that name a rate plan this property does not own.
    #: Never shown by id and dropped on the next save.
    foreign_pairs: int = 0


# ---- the channel, and proof that it is this tenant's -------------------------

def _context(db: Session, caller: Caller, connection_id: uuid.UUID,
             action: str):
    conn = _connection_or_404(db, caller, connection_id)
    assert_property_in_org(db, caller, conn["property_id"])
    # The route's own permission is checked organisation-wide, because the
    # property is only known once the connection is read. Check it again
    # against that property, or a user of one hotel could map a sister's.
    require_property_permission(db, caller, conn["property_id"],
                                "distribution", action)
    link = db.execute(
        text("SELECT id, external_property_id "
             "FROM distribution.channel_manager_links "
             "WHERE property_id = :p AND organization_id = :o"),
        {"p": conn["property_id"], "o": caller.organization_id},
    ).mappings().first()
    if not link or not link["external_property_id"]:
        raise HTTPException(
            status_code=409,
            detail="This property is not set up at the channel manager yet.")
    if not conn["external_channel_id"]:
        raise HTTPException(
            status_code=409,
            detail="The OTA channel has not been built yet. It is built from "
                   "the OTA's property id when the partner is saved.")
    group = db.execute(
        text("SELECT external_group_id FROM distribution.channel_manager_groups "
             "WHERE organization_id = :o AND provider = 'channex'"),
        {"o": caller.organization_id},
    ).scalar()
    return conn, link, group


def _channel(cx: Channex, conn, link, group) -> dict:
    """The channel as the channel manager has it, once it is shown to be ours.

    The id is ours to begin with -- it was stored by our own provisioning --
    but the account is shared by every tenant, so a stored id that somehow
    names another tenant's channel must be refused rather than trusted.
    """
    code, out = cx.get(f"/channels/{conn['external_channel_id']}")
    if code >= 400:
        raise HTTPException(
            status_code=502,
            detail=f"The channel manager could not return this channel "
                   f"({code}).")
    data = out.get("data") or {}
    attrs = data.get("attributes") or {}
    owner = (((data.get("relationships") or {}).get("group") or {})
             .get("data") or {}).get("id")
    props = attrs.get("properties") or []
    if props != [link["external_property_id"]] or (group and owner != group):
        log.error("channel %s is not on property %s in group %s; refusing",
                  conn["external_channel_id"], link["external_property_id"],
                  group)
        raise HTTPException(
            status_code=409,
            detail="This channel is not set up for this property alone at the "
                   "channel manager, so it cannot be mapped from here.")
    return attrs


# ---- this property's side ----------------------------------------------------

def _plans(db: Session, link_id) -> dict[uuid.UUID, dict]:
    """Rate plans this property's link maps: the only ones a pair may name."""
    rows = db.execute(
        text(
            """
            SELECT rp.id AS rate_plan_id, rp.code, rp.name, m.external_id,
                   (SELECT rt.name FROM property.rate_plan_room_types x
                      JOIN property.room_types rt ON rt.id = x.room_type_id
                     WHERE x.rate_plan_id = rp.id
                     ORDER BY rt.name LIMIT 1) AS room_type_name,
                   (SELECT rt.max_occupancy FROM property.rate_plan_room_types x
                      JOIN property.room_types rt ON rt.id = x.room_type_id
                     WHERE x.rate_plan_id = rp.id
                     ORDER BY rt.name LIMIT 1) AS occupancy
            FROM distribution.channel_rate_mappings m
            JOIN property.rate_plans rp ON rp.id = m.rate_plan_id
            WHERE m.link_id = :l AND m.external_id IS NOT NULL
              AND m.external_id <> ''
            ORDER BY room_type_name, rp.name
            """
        ),
        {"l": link_id},
    ).mappings().all()
    return {r["rate_plan_id"]: dict(r) for r in rows}


# ---- the OTA's side ----------------------------------------------------------

def _first(d: dict, *keys):
    for k in keys:
        v = d.get(k)
        if v not in (None, ""):
            return v
    return None


def parse_ota_rooms(out: dict) -> list[OtaRoom]:
    """The OTA's rooms and rates from ``/channels/mapping_details``.

    Read loosely on purpose. The channel manager passes on what each OTA
    returns, and the field names differ between OTAs (``id`` for one,
    ``room_type_code`` for another); a strict reader would show an empty list
    for a hotel whose rooms are right there.
    """
    data = out.get("data")
    if isinstance(data, dict):
        data = data.get("attributes") or data
        rooms = _first(data, "rooms", "room_types") or []
    elif isinstance(data, list):
        rooms = data
    else:
        rooms = []
    result = []
    for r in rooms:
        if not isinstance(r, dict):
            continue
        r = r.get("attributes") or r
        code = _first(r, "id", "room_type_code", "code")
        if code is None:
            continue
        rates = []
        for p in _first(r, "rates", "rate_plans") or []:
            if not isinstance(p, dict):
                continue
            p = p.get("attributes") or p
            rcode = _first(p, "id", "rate_plan_code", "code")
            if rcode is None:
                continue
            occ = _first(p, "max_persons", "occupancy", "max_occupancy")
            rates.append(OtaRate(code=str(rcode),
                                 title=_first(p, "title", "name"),
                                 occupancy=int(occ) if str(occ or "").isdigit()
                                 else None))
        result.append(OtaRoom(code=str(code), title=_first(r, "title", "name"),
                              rates=rates))
    return result


def _ota_rooms(cx: Channex, attrs: dict) -> tuple[list[OtaRoom], str | None]:
    try:
        code, out = cx.post("/channels/mapping_details", {
            "channel": attrs.get("channel"),
            "settings": {k: v for k, v in (attrs.get("settings") or {}).items()
                         if k != "mappingSettings"}})
    except Exception as exc:  # noqa: BLE001 - said, never raised
        log.warning("mapping_details failed: %s", exc)
        return [], "The channel manager could not be reached just now."
    rooms = parse_ota_rooms(out) if code < 400 else []
    if rooms:
        return rooms, None
    return [], (
        f"{attrs.get('channel')} did not return this hotel's rooms "
        f"({_error_text(out) if code >= 400 or out.get('errors') else 'none listed'}). "
        f"Usually the hotel has not yet authorised the channel manager in the "
        f"OTA's extranet; once it has, the rooms appear here. The codes can "
        f"also be typed in from the extranet.")


def _code(v: str):
    """OTA codes go back the way OTAs issue them: numbers as numbers.

    Only when that loses nothing -- "0123" stays a string, or its leading
    zero would silently name a different room.
    """
    return int(v) if v.isdigit() and str(int(v)) == v else v


def _present(conn, attrs, plans, rooms, err) -> MappingOut:
    by_external = {p["external_id"]: rid for rid, p in plans.items()}
    pairs, foreign = [], 0
    for rp in attrs.get("rate_plans") or []:
        local = by_external.get(rp.get("rate_plan_id"))
        s = rp.get("settings") or {}
        if local is None:
            foreign += 1
            continue
        if s.get("room_type_code") is None or s.get("rate_plan_code") is None:
            continue
        occ = s.get("occupancy")
        pairs.append(Pair(ota_room_code=str(s["room_type_code"]),
                          ota_rate_code=str(s["rate_plan_code"]),
                          rate_plan_id=local,
                          occupancy=occ if isinstance(occ, int) else None))
    return MappingOut(
        connection_id=conn["id"], channel_id=conn["external_channel_id"],
        channel=attrs.get("channel") or "", live=bool(attrs.get("is_active")),
        ota_rooms=rooms, ota_rooms_error=err,
        plans=[LocalPlan(rate_plan_id=rid, code=p["code"], name=p["name"],
                         room_type_name=p["room_type_name"],
                         occupancy=p["occupancy"])
               for rid, p in plans.items()],
        pairs=pairs, foreign_pairs=foreign)


def _require_channex() -> None:
    if not settings.channex_api_key:
        raise HTTPException(status_code=503,
                            detail="No channel manager is configured.")


# ---- routes ------------------------------------------------------------------

@ota_mapping_router.get("/channel-connections/{connection_id}/ota-mapping",
                        response_model=MappingOut)
def get_ota_mapping(
    connection_id: uuid.UUID,
    caller: Caller = Depends(require_org_permission("distribution", "view")),
    db: Session = Depends(get_session),
):
    """The OTA's rooms and rates, this property's plans, and today's pairs."""
    _require_channex()
    conn, link, group = _context(db, caller, connection_id, "view")
    with Channex() as cx:
        attrs = _channel(cx, conn, link, group)
        rooms, err = _ota_rooms(cx, attrs)
    return _present(conn, attrs, _plans(db, link["id"]), rooms, err)


@ota_mapping_router.put("/channel-connections/{connection_id}/ota-mapping",
                        response_model=MappingOut)
def set_ota_mapping(
    connection_id: uuid.UUID,
    body: MappingIn,
    caller: Caller = Depends(
        require_org_permission("distribution", "configure")),
    db: Session = Depends(get_session),
):
    """Replace the channel's pairs with these. An empty list clears them."""
    _require_channex()
    conn, link, group = _context(db, caller, connection_id, "configure")
    plans = _plans(db, link["id"])

    seen: set[tuple[str, str]] = set()
    for p in body.pairs:
        if p.rate_plan_id not in plans:
            raise HTTPException(
                status_code=422,
                detail="Only this property's own rate plans, already set up at "
                       "the channel manager, can be paired.")
        key = (p.ota_room_code.strip(), p.ota_rate_code.strip())
        if key in seen:
            raise HTTPException(
                status_code=422,
                detail=f"OTA room {key[0]} / rate {key[1]} is paired twice. "
                       f"Each OTA rate sells one of your plans.")
        seen.add(key)

    with Channex() as cx:
        attrs = _channel(cx, conn, link, group)
        rooms, err = _ota_rooms(cx, attrs)
        if rooms:
            known = {(r.code, x.code) for r in rooms for x in r.rates}
            unknown = sorted(k for k in seen if k not in known)
            if unknown:
                raise HTTPException(
                    status_code=422,
                    detail=f"{attrs.get('channel')} has no room/rate "
                           f"{unknown[0][0]} / {unknown[0][1]} for this hotel.")
        rate_plans = [{
            "rate_plan_id": plans[p.rate_plan_id]["external_id"],
            "settings": {
                "room_type_code": _code(p.ota_room_code.strip()),
                "rate_plan_code": _code(p.ota_rate_code.strip()),
                "occupancy": p.occupancy or plans[p.rate_plan_id]["occupancy"],
                "readonly": False,
                "primary_occ": True,
            },
        } for p in body.pairs]
        code, out = cx.put(f"/channels/{conn['external_channel_id']}",
                           {"channel": {"rate_plans": rate_plans}})
        if code >= 400:
            raise HTTPException(
                status_code=502,
                detail=f"The channel manager refused this mapping ({code}). "
                       f"{_error_text(out)}")
        attrs = (out.get("data") or {}).get("attributes") or attrs

    record_audit(
        db, action="channel_connection.ota_mapped",
        entity_type="channel_connection", entity_id=str(conn["id"]),
        organization_id=caller.organization_id,
        property_id=conn["property_id"], actor_subject=caller.subject,
        after={"pairs": [p.model_dump(mode="json") for p in body.pairs]},
    )
    return _present(conn, attrs, plans, rooms, err)


@ota_mapping_router.post("/channel-connections/{connection_id}/go-live",
                         response_model=MappingOut)
def go_live(
    connection_id: uuid.UUID,
    body: GoLiveIn,
    caller: Caller = Depends(
        require_org_permission("distribution", "configure")),
    db: Session = Depends(get_session),
):
    """Switch the OTA channel on (the hotel goes on sale there) or off."""
    _require_channex()
    conn, link, group = _context(db, caller, connection_id, "configure")
    plans = _plans(db, link["id"])
    with Channex() as cx:
        attrs = _channel(cx, conn, link, group)
        if body.live:
            ours = {p["external_id"] for p in plans.values()}
            paired = [rp for rp in attrs.get("rate_plans") or []
                      if rp.get("rate_plan_id") in ours]
            if not paired:
                raise HTTPException(
                    status_code=409,
                    detail="Pair at least one OTA rate with one of your rate "
                           "plans first. A live channel with nothing mapped "
                           "sells nothing.")
            if len(paired) != len(attrs.get("rate_plans") or []):
                raise HTTPException(
                    status_code=409,
                    detail="This channel holds pairs that are not this "
                           "property's. Save the mapping again before going "
                           "live.")
        action = "activate" if body.live else "deactivate"
        code, out = cx.post(
            f"/channels/{conn['external_channel_id']}/{action}", {})
        if code >= 400:
            raise HTTPException(
                status_code=502,
                detail=f"The channel manager did not {action} the channel "
                       f"({code}). {_error_text(out)}")
        attrs = _channel(cx, conn, link, group)
        rooms, err = _ota_rooms(cx, attrs)

    record_audit(
        db, action=f"channel_connection.{'live' if body.live else 'paused'}",
        entity_type="channel_connection", entity_id=str(conn["id"]),
        organization_id=caller.organization_id,
        property_id=conn["property_id"], actor_subject=caller.subject,
        after={"live": body.live},
    )
    return _present(conn, attrs, plans, rooms, err)
