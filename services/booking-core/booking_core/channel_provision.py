"""Setting a property up at the channel manager, without anybody typing ids.

Onboarding a hotel onto a channel manager by hand is a morning's work and a
list of opaque identifiers: create the property, recreate every room type,
recreate every rate plan, then pair each one with its counterpart by copying
UUIDs between two browser tabs. Every pair is a chance to put the wrong two
together, and a wrong pair is not visible until a guest arrives expecting a
suite and finds a standard double.

None of that judgement is actually required. The PMS knows its own rooms, and
if it creates the far side too then it knows both — so the mapping is written
rather than guessed, and the tenant never sees an id at all.

What this does, in order:

1. Create the property at the channel manager, from the property record here.
2. Create a room type for each active room type here, and record the pair.
3. Create one rate plan per room type, and record the pair.
4. Register the booking webhook, so arrivals come back.
5. Save the link, which is what routes an arriving booking to this tenant.

**Idempotent throughout.** Provisioning is re-run after a failure, after a new
room type is added, and by anybody who clicks the button twice. So every step
asks what already exists and creates only what is missing — matched on title,
which is what a person would match on and is stable in a way a UUID is not
once things have been recreated.

It reports rather than raises. A room type that could not be created should
not abandon the ones that could: the failure is named, the rest is done, and
running it again picks up where it stopped.
"""

from __future__ import annotations

import logging
import re
import uuid
from dataclasses import dataclass, field

import httpx
from chirala_common.db import bind_tenant_context, system_context
from sqlalchemy import text
from sqlalchemy.orm import Session

from .settings import settings

log = logging.getLogger("uvicorn.error").getChild("channel-provision")


@dataclass
class Result:
    property_id: uuid.UUID
    external_property_id: str | None = None
    created_property: bool = False
    rooms_created: int = 0
    rooms_mapped: int = 0
    rates_created: int = 0
    rates_mapped: int = 0
    webhook_registered: bool = False
    #: OTA channels built at the channel manager this run, and how many of the
    #: tenant's OTAs are still waiting for the hotel's own id.
    channels_created: int = 0
    channels_pending: int = 0
    problems: list[str] = field(default_factory=list)
    #: Nothing was attempted, because this property has not asked to sell
    #: through a channel. Deliberately not folded into "failed": an automatic
    #: trigger declining to act is the system working, and filing it as a
    #: failure would bury the real ones among a hundred harmless entries.
    skipped: bool = False

    @property
    def status(self) -> str:
        if self.skipped:
            return "skipped"
        if not self.external_property_id:
            return "failed"
        return "partial" if self.problems else "ok"


class Channex:
    """The calls provisioning needs, and nothing else."""

    def __init__(self) -> None:
        self.c = httpx.Client(
            base_url=settings.channex_api_url.rstrip("/"),
            headers={"user-api-key": settings.channex_api_key,
                     "Content-Type": "application/json"},
            timeout=60.0,
        )

    def __enter__(self) -> "Channex":
        return self

    def __exit__(self, *_) -> None:
        self.c.close()

    def get(self, path: str) -> tuple[int, dict]:
        r = self.c.get(path)
        return r.status_code, (r.json() if r.content else {})

    def post(self, path: str, body: dict) -> tuple[int, dict]:
        r = self.c.post(path, json=body)
        return r.status_code, (r.json() if r.content else {})

    def groups(self) -> dict[str, str]:
        """Every group in the account, by title.

        Keyed on title because that is what we control and what survives:
        the id is only known to whoever stored it, and a tenant whose stored
        id was lost must find its own group again rather than make a second.
        """
        code, out = self.get("/groups")
        if code >= 400:
            return {}
        return {(r["attributes"].get("title") or ""): r["attributes"]["id"]
                for r in out.get("data") or []}

    def create_group(self, title: str) -> str | None:
        code, out = self.post("/groups", {"group": {"title": title[:255]}})
        if code >= 400:
            log.warning("could not create group %r: %s %s", title, code,
                        str(out)[:200])
            return None
        return out["data"]["attributes"]["id"]

    def create_channel(self, body: dict) -> tuple[int, dict]:
        """Build one OTA channel. Created switched *off*, always.

        The channel manager will not accept ``is_active`` on create, and that
        is the right shape: activating puts a hotel's rooms on sale to the
        public, which is not a side effect of pressing Add in a PMS. It stays
        a deliberate act, after the mapping has been checked.
        """
        return self.post("/channels", {"channel": body})

    def properties_in(self, group: str) -> list[dict]:
        """Only this tenant's properties.

        The whole point of the group. Asking for every property in the account
        and matching on name is how one tenant's run could adopt another
        tenant's hotel.
        """
        code, out = self.get(f"/properties?filter%5Bgroup_id%5D={group}")
        if code >= 400:
            return []
        return [r["attributes"] for r in out.get("data") or []]


#: Has this property asked to sell through a channel at all?
#:
#: Not every hotel does, and creating a property at the channel manager for
#: one that never will is somebody's bill and somebody's clutter. A connected
#: partner, or a link that already exists, is the tenant saying they want it.
#:
#: One definition, two callers — the sweep inlines it to choose a set, the
#: single-property path calls it through :func:`wants_channel_distribution`.
#: Two copies of this predicate would drift, and the symptom would be a
#: property the sweep keeps visiting that go-live refuses to touch.
_INTENT_SQL = """
    (EXISTS (SELECT 1 FROM distribution.channel_manager_links li
             WHERE li.property_id = {prop})
     OR EXISTS (SELECT 1 FROM distribution.channel_connections c
                WHERE c.property_id = {prop}))
"""


def wants_channel_distribution(db: Session, property_id: uuid.UUID) -> bool:
    """Whether an automatic trigger should set this property up."""
    return bool(db.execute(
        text("SELECT " + _INTENT_SQL.format(prop=":p")), {"p": property_id},
    ).scalar())


def _group_for_org(db: Session, cx: Channex, organization_id: uuid.UUID,
                   res: Result) -> str | None:
    """This tenant's own group at the channel manager, created once.

    Replaces "whatever group came back first", which put every tenant in one
    bucket and made adoption-by-name reach across all of them.

    Resolution is deliberately belt and braces -- stored id, then a group of
    the right title, then create -- because the failure it avoids is a second
    group for a tenant that already has one, and properties split across the
    two with no way to tell which is current.
    """
    row = db.execute(
        text("SELECT external_group_id FROM distribution.channel_manager_groups "
             "WHERE organization_id = :o AND provider = 'channex'"),
        {"o": organization_id},
    ).mappings().first()
    if row and row["external_group_id"]:
        if not _is_gone(cx, f"/groups/{row['external_group_id']}"):
            return row["external_group_id"]
        # Gone for the same reasons a property can be. Fall through and find
        # or create it again rather than creating properties into a group the
        # far side does not have.
        log.warning("channel manager group %s is gone; resolving again",
                    row["external_group_id"])

    org = db.execute(
        text("SELECT name FROM iam.organizations WHERE id = :o"),
        {"o": organization_id},
    ).scalar()
    # A discriminator in the title on purpose: two of our customers may
    # genuinely trade under the same name, and a group each is the point --
    # a title that could match both would rebuild the bucket this replaces.
    #
    # Eight characters of the id, not all thirty-six. The full uuid made the
    # channel manager's group menu unreadable, and the discriminator only has
    # to separate this account's own customers, not be globally unique.
    title = f"{org or 'Organisation'} [{str(organization_id)[:8]}]"

    external = cx.groups().get(title) or cx.create_group(title)
    if not external:
        res.problems.append(
            "Could not create this organisation's group at the channel "
            "manager, so the property was not created either. Retried on the "
            "next sweep.")
        return None

    db.execute(
        text(
            """
            INSERT INTO distribution.channel_manager_groups
                (organization_id, provider, external_group_id)
            VALUES (:o, 'channex', :g)
            ON CONFLICT (organization_id, provider) DO UPDATE
               SET external_group_id = EXCLUDED.external_group_id,
                   updated_at = now()
            """
        ),
        {"o": organization_id, "g": external},
    )
    log.info("channel manager group for org %s: %s", organization_id, external)
    return external


def provision(db: Session, property_id: uuid.UUID,
              organization_id: uuid.UUID, actor: uuid.UUID | None = None,
              *, require_intent: bool = False) -> Result:
    """Set this property up at the channel manager. Safe to re-run.

    ``require_intent`` is for callers that nobody asked. Going live fires this
    for every property whether or not that hotel will ever sell on an OTA, and
    creating each of them at the channel manager regardless is a bill for
    listings nobody looks at. A person pressing the button, by contrast, *is*
    the intent — which is why this is off by default and asked for explicitly
    rather than enforced here for everyone.
    """
    res = Result(property_id=property_id)
    if require_intent and not wants_channel_distribution(db, property_id):
        res.skipped = True
        # No link row written. A property that was never attempted should not
        # acquire a record saying it was, and the moment the tenant connects
        # an OTA the sweep picks it up on its own.
        return res
    if not settings.channex_api_key:
        res.problems.append("No CHANNEX_API_KEY configured.")
        return res  # Nothing to remember: this is a deployment setting, not
        # a property's problem, and it is the same answer for all of them.

    prop = db.execute(
        text("SELECT id, code, name, currency, timezone, address, city, "
             "country FROM iam.properties WHERE id = :p"),
        {"p": property_id},
    ).mappings().first()
    if prop is None:
        res.problems.append("No such property.")
        return res

    link = db.execute(
        text("SELECT id, external_property_id FROM "
             "distribution.channel_manager_links WHERE property_id = :p"),
        {"p": property_id},
    ).mappings().first()

    with Channex() as cx:
        external = link["external_property_id"] if link else None
        if external and _is_gone(cx, f"/properties/{external}"):
            # The property this link names is not there any more -- deleted at
            # the far side, or the deployment was pointed at a different
            # account. Either way every id we hold for it is now a dangling
            # reference.
            #
            # Found twice the hard way. Without this the run skips creating
            # (an id is set), then fails mirroring rooms into a property that
            # does not exist, and reports the same failure on every sweep for
            # ever. Nothing self-heals, because the thing that is wrong is the
            # state it trusts.
            log.warning("channel manager property %s is gone; rebuilding %s",
                        external, property_id)
            _forget_external(db, link["id"])
            res.problems.append(
                "The channel manager no longer had this property, so it was "
                "created again and the room and rate mappings were rebuilt.")
            external = None

        if not external:
            group = _group_for_org(db, cx, organization_id, res)
            if not group:
                _record_outcome(db, organization_id, property_id, res)
                return res
            before = len(cx.properties_in(group))
            external = _create_property(cx, prop, group, res)
            if not external:
                # Remembered even though there is no property at the far side
                # yet, so the sweep backs off instead of retrying a failing
                # create on every pass.
                _record_outcome(db, organization_id, property_id, res)
                return res
            # Only when one genuinely appeared. Adoption reuses a property
            # that was already there, and reporting that as "created" makes a
            # recovery look like a new listing.
            res.created_property = len(cx.properties_in(group)) > before

        link_id = _save_link(db, organization_id, property_id, external,
                             prop["currency"], actor)
        res.external_property_id = external

        _sync_rooms(db, cx, link_id, property_id, external, res)
        _sync_rates(db, cx, link_id, property_id, external, res)
        _register_webhook(cx, external, res)
        _sync_channels(db, cx, property_id, organization_id, external, res)
        _first_full_sync(db, link_id)

    _record_outcome(db, organization_id, property_id, res)
    return res


def _first_full_sync(db: Session, link_id) -> None:
    """Publish everything once, when the channel manager has nothing yet.

    Going live -- or coming back after the channel manager lost the property
    -- is the one moment a full 500-day sync is right. After it, only changes
    are sent, as the outbox records them.
    """
    if db.execute(text("SELECT 1 FROM distribution.channel_ari_state "
                       "WHERE link_id = :l LIMIT 1"), {"l": link_id}).first():
        return
    if not db.execute(text("SELECT 1 FROM distribution.channel_room_mappings "
                           "WHERE link_id = :l LIMIT 1"), {"l": link_id}).first():
        return
    from .channel_sync import sync
    sync(db, link_id, full=True)



def _is_gone(cx: Channex, path: str) -> bool:
    """Whether the far side definitely no longer has this.

    Only a 404 counts. That distinction is the whole point: a timeout, a 500
    or an expired key also fail to return the record, and treating those as
    "gone" would throw away a working property's mappings because the channel
    manager had a bad minute. Absence of proof is not proof of absence, and
    here the difference is destructive.
    """
    try:
        code, _ = cx.get(path)
    except Exception:  # noqa: BLE001 - a transport failure is not a deletion
        return False
    return code == 404


def _forget_external(db: Session, link_id) -> None:
    """Drop the ids that pointed at something no longer there.

    The mappings go too, and must: each one pairs one of our room types with a
    room id belonging to the property that has vanished. Left behind, the next
    run would see room types already mapped and skip recreating them, leaving
    a property at the far side with no rooms and a link that looks complete.
    """
    db.execute(text("DELETE FROM distribution.channel_room_mappings "
                    "WHERE link_id = :l"), {"l": link_id})
    db.execute(text("DELETE FROM distribution.channel_rate_mappings "
                    "WHERE link_id = :l"), {"l": link_id})
    # What was accepted belonged to the property that has gone; the rebuilt
    # one starts empty and gets a full sync.
    db.execute(text("DELETE FROM distribution.channel_ari_state "
                    "WHERE link_id = :l"), {"l": link_id})
    db.execute(
        text("UPDATE distribution.channel_manager_links "
             "SET external_property_id = NULL, updated_at = now() "
             "WHERE id = :l"),
        {"l": link_id},
    )
    # A channel belongs to the property that has gone, so its id is dangling
    # too. Cleared rather than deleted: the OTA hotel id on the same row is
    # the tenant's own and must survive, so the channel is simply rebuilt.
    db.execute(
        text("UPDATE distribution.channel_connections c "
             "SET external_channel_id = NULL, updated_at = now() "
             "FROM distribution.channel_manager_links l "
             "WHERE l.id = :l AND c.property_id = l.property_id"),
        {"l": link_id},
    )


def _create_property(cx: Channex, prop, group: str, res: Result) -> str | None:
    """Create the property, or adopt one already there under the same name.

    Adoption matters: a half-finished run leaves a property behind, and a
    second attempt that created another would leave the tenant with two
    listings and bookings arriving at whichever one a channel happened to be
    attached to.

    Searched **inside this tenant's group only**. Across the whole account,
    matching on name meant two customers with a hotel of the same name could
    adopt each other's -- caught by the unique index on the link, but caught
    is not the same as correct, and the error it produced said nothing about
    what had happened.
    """
    for a in cx.properties_in(group):
        if (a.get("title") or "").strip().lower() == prop["name"].strip().lower():
            log.info("adopting existing channex property for %s in group %s",
                     prop["name"], group)
            return a["id"]

    body = {"property": {
        "title": prop["name"],
        "currency": prop["currency"] or "INR",
        "country": (prop["country"] or "IN")[:2].upper(),
        "city": prop["city"] or "-",
        "address": prop["address"] or "-",
        "timezone": prop["timezone"] or "Asia/Kolkata",
        "property_type": "hotel",
        "is_active": True,
    }}
    body["property"]["group_id"] = group

    code, out = cx.post("/properties", body)
    if code >= 400:
        res.problems.append(
            f"Could not create the property at the channel manager: "
            f"{code} {str(out)[:200]}")
        return None
    return out["data"]["attributes"]["id"]


def _save_link(db: Session, organization_id, property_id, external,
               currency, actor) -> uuid.UUID:
    db.execute(
        text(
            """
            INSERT INTO distribution.channel_manager_links
                (organization_id, property_id, provider, external_property_id,
                 currency, updated_by)
            VALUES (:o, :p, 'channex', :ext, :cur, :by)
            ON CONFLICT (property_id) DO UPDATE
               SET external_property_id = EXCLUDED.external_property_id,
                   currency = EXCLUDED.currency, updated_at = now(),
                   updated_by = EXCLUDED.updated_by
            """
        ),
        {"o": organization_id, "p": property_id, "ext": external,
         "cur": currency, "by": actor},
    )
    return db.execute(
        text("SELECT id FROM distribution.channel_manager_links "
             "WHERE property_id = :p"),
        {"p": property_id},
    ).scalar_one()


def _sync_rooms(db: Session, cx: Channex, link_id, property_id, external,
                res: Result) -> None:
    """One channel room type per active room type here, and the pair recorded."""
    ours = db.execute(
        text("SELECT id, code, name, max_occupancy FROM property.room_types "
             "WHERE property_id = :p AND status = 'active' ORDER BY name"),
        {"p": property_id},
    ).mappings().all()

    code, out = cx.get(f"/room_types?filter%5Bproperty_id%5D={external}")
    theirs = {} if code >= 400 else {
        (r["attributes"].get("title") or "").strip().lower():
            r["attributes"]["id"]
        for r in out.get("data") or []
    }

    for rt in ours:
        existing = db.execute(
            text("SELECT external_id FROM distribution.channel_room_mappings "
                 "WHERE link_id = :l AND room_type_id = :r"),
            {"l": link_id, "r": rt["id"]},
        ).scalar()
        if existing:
            res.rooms_mapped += 1
            continue

        theirs_id = theirs.get(rt["name"].strip().lower())
        if not theirs_id:
            occ = int(rt["max_occupancy"] or 2)
            code, out = cx.post("/room_types", {"room_type": {
                "property_id": external,
                "title": rt["name"],
                "occ_adults": occ,
                "occ_children": 0,
                "occ_infants": 0,
                "default_occupancy": occ,
                "count_of_rooms": _room_count(db, rt["id"]),
                "room_kind": "room",
            }})
            if code >= 400:
                res.problems.append(
                    f"{rt['name']}: could not create at the channel manager "
                    f"({code}).")
                continue
            theirs_id = out["data"]["attributes"]["id"]
            res.rooms_created += 1

        db.execute(
            text("INSERT INTO distribution.channel_room_mappings "
                 "(link_id, room_type_id, external_id, external_name) "
                 "VALUES (:l, :r, :e, :n) "
                 "ON CONFLICT (link_id, room_type_id) DO UPDATE "
                 "SET external_id = EXCLUDED.external_id, updated_at = now()"),
            {"l": link_id, "r": rt["id"], "e": theirs_id, "n": rt["name"]},
        )
        res.rooms_mapped += 1


def _room_count(db: Session, room_type_id) -> int:
    """How many rooms of this type exist. What the channel sells against."""
    n = db.execute(
        text("SELECT count(*) FROM property.rooms WHERE room_type_id = :r "
             "AND status <> 'inactive'"),
        {"r": room_type_id},
    ).scalar_one()
    return max(1, int(n))


def _sync_rates(db: Session, cx: Channex, link_id, property_id, external,
                res: Result) -> None:
    """One channel rate plan for each of ours that a channel can sell.

    Per *rate plan*, not per room. This used to take one plan per room --
    ``ORDER BY name LIMIT 1`` -- which was right while a room had only one,
    and wrong the moment a hotel wanted to sell Advance Purchase and Bed &
    Breakfast alongside its standard rate. Two of the three simply never
    reached the channel, and the run then tried to give the third's channel
    plan to a second one of ours and hit the uniqueness rule that stops one
    of theirs meaning two of ours.

    Only plans covering exactly one room type are sent. A plan spanning three
    rooms has three nightly prices and no single number to put in a message,
    so it cannot be sold on a channel at all; it is reported per room rather
    than silently dropped.

    Matched on title so a re-run adopts what is already there. Titles are
    stable in a way ids are not once something has been recreated, and this
    runs again every time a room type or a rate plan is added.
    """
    rooms = {
        r["room_type_id"]: r
        for r in db.execute(
            text("SELECT m.room_type_id, m.external_id, rt.name, "
                 "       rt.max_occupancy "
                 "FROM distribution.channel_room_mappings m "
                 "JOIN property.room_types rt ON rt.id = m.room_type_id "
                 "WHERE m.link_id = :l"),
            {"l": link_id},
        ).mappings().all()
    }
    if not rooms:
        return

    # Every plan of ours that prices exactly one room, and which room.
    ours = db.execute(
        text(
            """
            SELECT rp.id, rp.name,
                   min(x.room_type_id::text) AS room_type_id
            FROM property.rate_plans rp
            JOIN property.rate_plan_room_types x ON x.rate_plan_id = rp.id
            WHERE rp.property_id = :p AND rp.status = 'active'
            GROUP BY rp.id, rp.name
            HAVING count(x.room_type_id) = 1
            ORDER BY rp.name
            """
        ),
        {"p": property_id},
    ).mappings().all()

    # What the channel manager already has, by room and title. Normalised,
    # because a title that came back with different spacing is still the
    # same plan. By room too, because titles repeat across rooms: Channex's
    # own certification property has a "Best Available Rate" on the Twin
    # *and* on the Double, and keyed on title alone the Double's plan was
    # paired with the Twin's -- then refused as a clash, never mapped.
    code, out = cx.get(f"/rate_plans?filter%5Bproperty_id%5D={external}")
    theirs: dict[tuple[str, str], str] = {}
    if code < 400:
        for r in out.get("data") or []:
            a = r["attributes"]
            key = re.sub(r"[^a-z0-9]+", "", (a.get("title") or "").lower())
            room_of = a.get("room_type_id") or (
                ((r.get("relationships") or {}).get("room_type") or {})
                .get("data") or {}).get("id")
            if key:
                theirs.setdefault((str(room_of), key), a["id"])

    priced: set[str] = set()

    for plan in ours:
        room = rooms.get(uuid.UUID(plan["room_type_id"]))
        if room is None:
            # The room this plan prices is not mapped, so there is nothing at
            # the far side to hang it on. The room's own problem is reported
            # by _sync_rooms; this is not a second fault.
            continue
        priced.add(plan["room_type_id"])

        already = db.execute(
            text("SELECT external_id FROM distribution.channel_rate_mappings "
                 "WHERE link_id = :l AND rate_plan_id = :r"),
            {"l": link_id, "r": plan["id"]},
        ).scalar()
        if already:
            res.rates_mapped += 1
            continue

        title = plan["name"]
        key = (str(room["external_id"]),
               re.sub(r"[^a-z0-9]+", "", title.lower()))
        theirs_id = theirs.get(key)

        if not theirs_id:
            occ = int(room["max_occupancy"] or 2)
            code, out = cx.post("/rate_plans", {"rate_plan": {
                "title": title,
                "property_id": external,
                "room_type_id": room["external_id"],
                "currency": _currency(db, property_id),
                "sell_mode": "per_room", "rate_mode": "manual",
                "children_fee": "0.00", "infant_fee": "0.00",
                "max_stay": [0] * 7,
                "min_stay_arrival": [1] * 7, "min_stay_through": [1] * 7,
                "closed_to_arrival": [False] * 7,
                "closed_to_departure": [False] * 7,
                "stop_sell": [False] * 7,
                # Occupancy must fit inside the room's own, or the channel
                # manager refuses it -- which is why it is read from the room
                # rather than assumed.
                "options": [{"occupancy": occ, "is_primary": True, "rate": 0}],
                "inherit_rate": False,
            }})
            if code >= 400:
                res.problems.append(
                    f"{title}: could not be created at the channel manager "
                    f"({code}). {_error_text(out)}")
                continue
            theirs_id = out["data"]["attributes"]["id"]
            theirs[key] = theirs_id
            res.rates_created += 1

        # Both directions are unique: one of ours to one of theirs. A clash
        # means this channel plan is already spoken for, which is a mapping
        # somebody has to resolve rather than something to overwrite.
        clash = db.execute(
            text("SELECT rp.name FROM distribution.channel_rate_mappings m "
                 "JOIN property.rate_plans rp ON rp.id = m.rate_plan_id "
                 "WHERE m.link_id = :l AND m.external_id = :e"),
            {"l": link_id, "e": theirs_id},
        ).scalar()
        if clash:
            res.problems.append(
                f"{title}: the channel manager's \"{title}\" is already "
                f"paired with \"{clash}\". Unpair one of them on the "
                f"partner's page.")
            continue

        db.execute(
            text("INSERT INTO distribution.channel_rate_mappings "
                 "(link_id, rate_plan_id, external_id, external_name) "
                 "VALUES (:l, :r, :e, :n) "
                 "ON CONFLICT (link_id, rate_plan_id) DO UPDATE "
                 "SET external_id = EXCLUDED.external_id, updated_at = now()"),
            {"l": link_id, "r": plan["id"], "e": theirs_id, "n": title},
        )
        res.rates_mapped += 1

    # A room with nothing sellable cannot be sold, whatever else worked.
    for rt_id, room in rooms.items():
        if str(rt_id) not in priced:
            res.problems.append(
                f"{room['name']}: no rate plan here covers this room type and "
                f"nothing else. A channel rate plan belongs to one room, so a "
                f"plan spanning several has no single price to send. Add a "
                f"rate plan for this room alone.")


def _currency(db: Session, property_id) -> str:
    return db.execute(
        text("SELECT currency FROM iam.properties WHERE id = :p"),
        {"p": property_id},
    ).scalar() or "INR"


def _register_webhook(cx: Channex, external: str, res: Result) -> None:
    """Point the channel manager back at us, once per property.

    Without this a booking is taken and never arrives, which is the failure
    that costs a hotel a room rather than a page load. Skipped silently when
    the deployment has no public address configured — there is no useful
    webhook to register against localhost.
    """
    base = (settings.app_base_url or "").rstrip("/")
    if not base or not settings.channex_webhook_secret:
        res.problems.append(
            "Webhook not registered: this deployment has no public address or "
            "no webhook secret, so bookings cannot be delivered back.")
        return

    url = f"{base}/api/booking/channels/channex/webhook"
    code, out = cx.get(f"/webhooks?filter%5Bproperty_id%5D={external}")
    if code < 400:
        for r in out.get("data") or []:
            if (r["attributes"].get("callback_url") or "") == url:
                res.webhook_registered = True
                return

    code, out = cx.post("/webhooks", {"webhook": {
        "property_id": external,
        "callback_url": url,
        "event_mask": "booking",
        # Without this the payload is metadata only and carries no revision to
        # fetch — the webhook fires and no booking is ever created.
        "send_data": True,
        "is_active": True,
        "headers": {
            "X-Channex-Webhook-Secret": settings.channex_webhook_secret},
    }})
    if code >= 400:
        # Their reason, verbatim. A bare "(422)" cost a debugging session:
        # the answer was "invalid host" -- the public address is a dead
        # tunnel -- which points at the deployment, not at this code, and
        # nothing in the message said so.
        res.problems.append(
            f"Could not register the booking webhook ({code}). "
            f"{_error_text(out)} Bookings will not arrive until this "
            f"succeeds. The address offered was {url}")
    else:
        res.webhook_registered = True


def _record_outcome(db: Session, organization_id, property_id,
                    res: Result) -> None:
    """Leave the result of this run on the link.

    Nobody watches an automatic job. Without this the only record of a
    property that half-provisioned three weeks ago is a container log that has
    long since rotated, and the symptom — a room type nobody can book — looks
    like anything but a provisioning failure.

    The row is written even when nothing could be created at the far side.
    That is what gives the sweep a memory: a property whose create fails has a
    row saying so, is left alone for the retry interval, and is not attempted
    again on every pass for the rest of the day.
    """
    detail = "; ".join(res.problems)[:2000] or None
    db.execute(
        text(
            """
            INSERT INTO distribution.channel_manager_links
                (organization_id, property_id, provider, last_provisioned_at,
                 last_provision_status, last_provision_detail)
            VALUES (:o, :p, 'channex', now(), :st, :dt)
            ON CONFLICT (property_id) DO UPDATE
               SET last_provisioned_at = now(),
                   last_provision_status = EXCLUDED.last_provision_status,
                   last_provision_detail = EXCLUDED.last_provision_detail,
                   updated_at = now()
            """
        ),
        {"o": organization_id, "p": property_id, "st": res.status,
         "dt": detail},
    )


# --------------------------------------------------------------------------
# The sweep
# --------------------------------------------------------------------------
#
# Provisioning on go-live and a button for the rest is fine for three
# properties and does not survive a hundred. Three things go stale on their
# own, none of which anybody will notice on somebody else's hotel:
#
# * A tenant connects Agoda on Tuesday. Nothing provisions them until a human
#   remembers, and until then the OTA has nothing to sell.
# * A hotel adds a room type. It exists here, does not exist at the channel
#   manager, and is quietly unsellable online.
# * A run half-failed. It stays half-failed.
#
# So the set is computed rather than remembered, exactly like the onboarding
# checklist elsewhere in this system: a stored "provisioned" flag and a room
# type added afterwards disagree, and it is always the flag that lies.

#: Properties that want channel distribution and are not currently in step
#: with it. Ordered oldest-attempt-first so a sweep that cannot finish the
#: whole estate in one pass still gets round everybody.
_NEEDS_WORK_SQL = """
    SELECT p.id AS property_id, p.organization_id, p.name
    FROM iam.properties p
    LEFT JOIN distribution.channel_manager_links l ON l.property_id = p.id
    WHERE p.status = 'active'
      -- Readiness, counted rather than remembered. The obvious gate here was
      -- the onboarding wizard's ``activated_at``, and it is the wrong one: it
      -- records that somebody walked a wizard, not that the property has
      -- anything to sell. A property seeded, imported, or opened before the
      -- wizard existed has a null there forever and would never be swept --
      -- which is exactly the state the first property tested here was in.
      --
      -- What provisioning actually needs is a room type to mirror. Ask that.
      AND EXISTS (
          SELECT 1 FROM property.room_types rt0
          WHERE rt0.property_id = p.id AND rt0.status = 'active')
      AND
      -- Intent, as defined once in _INTENT_SQL above.
      __INTENT__
      -- Cooldown. A failure is retried, but not on every pass: the far side
      -- is rate limited and the cause is usually a person's to fix.
      AND (l.last_provisioned_at IS NULL
           OR l.last_provisioned_at < now() - make_interval(mins => :retry))
      AND (
          -- Never set up, or set up and then lost.
          l.external_property_id IS NULL
          -- Did not finish last time.
          OR l.last_provision_status IS DISTINCT FROM 'ok'
          -- A room type exists here with no counterpart there. This is the
          -- case that matters most: it is silent, and it is the common one.
          OR EXISTS (
              SELECT 1 FROM property.room_types rt
              WHERE rt.property_id = p.id AND rt.status = 'active'
                AND NOT EXISTS (
                    SELECT 1 FROM distribution.channel_room_mappings m
                    WHERE m.link_id = l.id AND m.room_type_id = rt.id))
      )
    ORDER BY l.last_provisioned_at NULLS FIRST
    LIMIT :lim
"""


_NEEDS_WORK_SQL = _NEEDS_WORK_SQL.replace(
    "__INTENT__", _INTENT_SQL.format(prop="p.id"))


def properties_needing_provision(db: Session, *, retry_minutes: int,
                                 limit: int) -> list[dict]:
    """Who the next sweep should visit. Read-only, cheap enough to run often."""
    return [dict(r) for r in db.execute(
        text(_NEEDS_WORK_SQL), {"retry": retry_minutes, "lim": limit},
    ).mappings().all()]


def provision_all(db_factory, *, retry_minutes: int = 60,
                  limit: int = 25) -> list[Result]:
    """Reconcile every property that needs it, one transaction each.

    A session per property, deliberately. The alternative — one transaction
    for the batch — means a failure on the ninetieth property rolls back the
    mappings written for the first eighty-nine, and the next sweep starts from
    nothing. Provisioning is a conversation with a third party that cannot be
    rolled back anyway: the rooms exist at the far side whether or not this
    transaction commits, so the record of them must not be thrown away.
    """
    with db_factory() as session:
        system_context(session, reason="channel provisioning: list properties needing work")
        due = properties_needing_provision(
            session, retry_minutes=retry_minutes, limit=limit)

    results: list[Result] = []
    for row in due:
        with db_factory() as session:
            bind_tenant_context(session, organization_id=row["organization_id"],
                                property_id=row["property_id"], is_service=True)
            try:
                res = provision(session, row["property_id"],
                                row["organization_id"])
                session.commit()
            except Exception:
                session.rollback()
                # Named, because "provisioning failed" across a hundred
                # properties is not something anybody can act on.
                log.exception("provisioning %s (%s) failed",
                              row["name"], row["property_id"])
                continue
        results.append(res)
    return results


# --------------------------------------------------------------------------
# The OTA channels themselves
# --------------------------------------------------------------------------
#
# Everything above connects this system to the channel manager. None of it
# puts a room in front of a guest: that needs a channel for each OTA the hotel
# sells through, and a channel needs the OTA's own id for that hotel.
#
# That id is the one thing here a tenant genuinely has to supply, because it
# comes with their contract with the OTA and nothing can derive it. Once they
# have, the platform builds the channel for them rather than sending them into
# the channel manager's own admin to do it by hand -- which is the difference
# between being the bridge and being a form that points at somebody else.

#: What the channel manager calls each OTA.
#:
#: Established by asking it rather than by reading the documentation, which
#: gives three different spellings: the published channel-codes page says
#: ``AGO`` and ``BDC``, the API guide says ``booking_com``, and every one of
#: those is rejected. The form the API accepts is the display name with the
#: punctuation removed, which is also what it returns on a channel it already
#: has.
#:
#: Keyed on the normalised partner name so a partner named "Booking.com" here
#: finds "BookingCom" there without a second column to keep in step.
CHANNEL_CODES = {
    "agoda": "Agoda",
    "bookingcom": "BookingCom",
    "expedia": "Expedia",
    "airbnb": "Airbnb",
    "makemytrip": "MakeMyTrip",
    "tripcom": "TripCom",
    "goibibo": "Goibibo",
    "cleartrip": "Cleartrip",
    "yatra": "Yatra",
    "googlehotels": "GoogleHotelAds",
}


def _norm_partner(name: str) -> str:
    import re as _re
    return _re.sub(r"[^A-Za-z0-9]+", "", name or "").lower()


def _sync_channels(db: Session, cx: Channex, property_id: uuid.UUID,
                   organization_id: uuid.UUID, external: str,
                   res: Result) -> None:
    """One channel per OTA this property has an id for. Safe to re-run."""
    conns = db.execute(
        text(
            """
            SELECT c.id, c.ota_hotel_id, c.external_channel_id,
                   a.name AS partner_name
            FROM distribution.channel_connections c
            JOIN engagement.booking_attributes a ON a.id = c.partner_id
            WHERE c.property_id = :p
            ORDER BY a.name
            """
        ),
        {"p": property_id},
    ).mappings().all()
    if not conns:
        return

    group = _group_for_org(db, cx, organization_id, res)
    if not group:
        return

    # What already exists there, so a re-run adopts rather than duplicates.
    # Two channels for one OTA on one hotel is two systems pushing different
    # prices at the same listing.
    #
    # Only this tenant's group. The account is shared by every tenant, and a
    # channel found by hotel id anywhere in it could be another customer's
    # listing -- adopting it would attach this hotel to their Booking.com page.
    existing: dict[str, str] = {}
    code, out = cx.get("/channels")
    if code < 400:
        for row in out.get("data") or []:
            a = row.get("attributes") or {}
            if a.get("group_id") != group:
                continue
            settings_blob = a.get("settings") or {}
            key = f"{a.get('channel')}|{settings_blob.get('hotel_id')}"
            existing[key] = a.get("id")

    for c in conns:
        hotel_id = (c["ota_hotel_id"] or "").strip()
        if not hotel_id:
            # Not a failure. The hotel has not given us their id for this OTA
            # yet, and saying so is more useful than a problem that reads like
            # something broke.
            res.channels_pending += 1
            continue

        channel_code = CHANNEL_CODES.get(_norm_partner(c["partner_name"]))
        if not channel_code:
            res.problems.append(
                f"{c['partner_name']}: not a channel the channel manager "
                f"supports, so no channel was created. Its terms are still "
                f"recorded.")
            continue

        found = c["external_channel_id"] or existing.get(
            f"{channel_code}|{hotel_id}")
        if found:
            _save_channel_id(db, c["id"], found)
            continue

        code, out = cx.create_channel({
            "channel": channel_code,
            "group_id": group,
            "title": f"{c['partner_name']} - {hotel_id}",
            "properties": [external],
            "settings": {"hotel_id": hotel_id},
        })
        if code >= 400:
            # Their message verbatim. "Could not create the channel" sends
            # somebody to us; "hotel_id is invalid" sends them to the OTA,
            # which is where the answer is.
            res.problems.append(
                f"{c['partner_name']}: the channel manager refused this "
                f"channel ({code}). {_error_text(out)}")
            continue

        new_id = ((out.get("data") or {}).get("attributes") or {}).get("id")
        if new_id:
            _save_channel_id(db, c["id"], new_id)
            res.channels_created += 1
            log.info("created %s channel %s for property %s", channel_code,
                     new_id, property_id)


def _error_text(out: dict) -> str:
    errors = out.get("errors") or {}
    details = errors.get("details")
    if isinstance(details, dict):
        return "; ".join(f"{k}: {', '.join(v)}" if isinstance(v, list)
                         else f"{k}: {v}" for k, v in details.items())[:300]
    return str(errors.get("title") or out)[:300]


def _save_channel_id(db: Session, connection_id, external_channel_id) -> None:
    db.execute(
        text("UPDATE distribution.channel_connections "
             "SET external_channel_id = :e, updated_at = now() "
             "WHERE id = :i"),
        {"e": external_channel_id, "i": connection_id},
    )
