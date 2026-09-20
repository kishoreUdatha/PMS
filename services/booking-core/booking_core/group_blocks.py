"""Holding rooms for a group, and giving back what nobody takes.

A block is the thing between "thirty rooms in March, probably" and thirty
named bookings. It takes rooms off sale without selling them, so the rooms are
neither available to a passing guest nor pretending to be occupied by invented
people, and it hands back whatever the group has not claimed by an agreed
date.

The whole design rests on one counter that already existed and had never been
used. ``room_type_inventory_days.allotment_units`` is subtracted in every
sellable calculation in this service; until now it was zero everywhere. Rooms
in a block sit there. When a booking picks one up, the room moves from
``allotment_units`` to ``reserved_units`` and the sellable count does not
change -- correctly, because the room was already off sale. That single
property is what makes a block safe: the hotel cannot oversell by blocking,
and cannot double-count by picking up.

``group_block_nights`` is the part that looks redundant and is not.
``allotment_units`` is one number shared by every block on that night, so
releasing a block means subtracting exactly the rooms *this* block still
holds. Deriving that from the block's original size minus its pick-ups is
wrong the moment a second block exists or a pick-up is cancelled. So what each
block holds, per type, per night, is written down and decremented as it is
spent.

Everything here must run inside the caller's transaction. A block that takes
inventory but whose rows roll back is rooms off sale with nothing to explain
them, and nobody would ever find it.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import text
from sqlalchemy.orm import Session

from .inventory import shift_inventory

#: Holding rooms and accepting pick-ups. Everything else is an ending.
STATUSES = ("open", "released", "cancelled")
#: What a forecast should believe. Both hold inventory.
COMMITMENTS = ("tentative", "definite")


class BlockError(Exception):
    """Something about the block itself is wrong, not the inventory."""


@dataclass(frozen=True)
class BlockLine:
    room_type_id: uuid.UUID
    rooms_blocked: int
    nightly_rate: Decimal | None = None


def _nights(arrival: date, departure: date) -> list[date]:
    return [arrival + timedelta(days=i) for i in range((departure - arrival).days)]


def next_code(db: Session, property_id: uuid.UUID) -> str:
    """The next ``GRP-0001`` for this property.

    Per property, not per organisation: two hotels each having a GRP-0007 is
    how a group is referred to on the phone, and a global sequence would give
    one of them GRP-0312 for no reason a person could explain.
    """
    row = db.execute(
        text(
            """
            SELECT COALESCE(MAX(CAST(NULLIF(regexp_replace(code, '^GRP-', ''),
                                            '') AS integer)), 0) AS n
              FROM booking.group_blocks
             WHERE property_id = :p AND code ~ '^GRP-[0-9]+$'
            """
        ),
        {"p": property_id},
    ).mappings().one()
    return f"GRP-{int(row['n']) + 1:04d}"


def create_block(
    db: Session,
    *,
    organization_id: uuid.UUID,
    property_id: uuid.UUID,
    name: str,
    arrival_date: date,
    departure_date: date,
    lines: list[BlockLine],
    cut_off_date: date | None = None,
    commitment: str = "tentative",
    commercial_account_id: uuid.UUID | None = None,
    notes: str | None = None,
    created_by: uuid.UUID | None = None,
    code: str | None = None,
) -> uuid.UUID:
    """Agree a block and take its rooms off sale.

    The inventory move goes through ``shift_inventory`` like every booking, so a
    block that would oversell a night is refused by the same check and with
    the same message -- a block is not a licence to promise rooms that do not
    exist.
    """
    if departure_date <= arrival_date:
        raise BlockError("A block has to end after it starts.")
    if commitment not in COMMITMENTS:
        raise BlockError(f"Unknown commitment {commitment!r}.")
    if not lines:
        raise BlockError("A block needs at least one room type.")
    if cut_off_date and cut_off_date > arrival_date:
        raise BlockError(
            "A cut-off after arrival would release rooms the group is already "
            "sleeping in.")
    seen: set[uuid.UUID] = set()
    for ln in lines:
        if ln.rooms_blocked < 1:
            raise BlockError("Every line must block at least one room.")
        if ln.room_type_id in seen:
            raise BlockError(
                "One line per room type — two lines of the same type are two "
                "answers to how many rooms were agreed.")
        seen.add(ln.room_type_id)

    block_id = uuid.uuid4()
    db.execute(
        text(
            """
            INSERT INTO booking.group_blocks
                (id, organization_id, property_id, code, name,
                 commercial_account_id, arrival_date, departure_date,
                 cut_off_date, commitment, status, notes, created_by)
            VALUES (:id, :org, :prop, :code, :name, :acct, :arr, :dep,
                    :cut, :commit, 'open', :notes, :by)
            """
        ),
        {"id": block_id, "org": organization_id, "prop": property_id,
         "code": code or next_code(db, property_id), "name": name.strip(),
         "acct": commercial_account_id, "arr": arrival_date,
         "dep": departure_date, "cut": cut_off_date, "commit": commitment,
         "notes": (notes or "").strip() or None, "by": created_by},
    )

    nights = _nights(arrival_date, departure_date)
    delta: dict[tuple[uuid.UUID, date], int] = {}
    for ln in lines:
        db.execute(
            text(
                """
                INSERT INTO booking.group_block_lines
                    (id, organization_id, property_id, block_id, room_type_id,
                     rooms_blocked, nightly_rate)
                VALUES (:id, :org, :prop, :block, :rt, :rooms, :rate)
                """
            ),
            {"id": uuid.uuid4(), "org": organization_id, "prop": property_id,
             "block": block_id, "rt": ln.room_type_id,
             "rooms": ln.rooms_blocked, "rate": ln.nightly_rate},
        )
        for day in nights:
            db.execute(
                text(
                    """
                    INSERT INTO booking.group_block_nights
                        (organization_id, property_id, block_id, room_type_id,
                         stay_date, rooms_held)
                    VALUES (:org, :prop, :block, :rt, :d, :n)
                    """
                ),
                {"org": organization_id, "prop": property_id,
                 "block": block_id, "rt": ln.room_type_id, "d": day,
                 "n": ln.rooms_blocked},
            )
            delta[(ln.room_type_id, day)] = (
                delta.get((ln.room_type_id, day), 0) + ln.rooms_blocked)

    shift_inventory(db, property_id=property_id, organization_id=organization_id,
                counter="allotment_units", delta=delta)
    return block_id


def draw_down(
    db: Session,
    *,
    block_id: uuid.UUID,
    property_id: uuid.UUID,
    room_type_id: uuid.UUID,
    nights: list[date],
    need: int,
) -> dict[date, int]:
    """Spend this block's held rooms, freeing them for the booking being made.

    Called from inside ``create_hold``, after it has locked the inventory rows
    for this room type and before it checks whether they are sellable. That
    order is the whole point. A block takes its rooms *off* sale, so a group
    member booking against the block is refused by ordinary availability --
    the rooms they were promised are the very rooms the block is holding. The
    first version of this ran after the booking was made and could never work:
    the booking had already been rejected.

    So the block's rooms are handed back first, on the nights it holds them,
    and the availability check that follows sees them free.

    Decrementing ``allotment_units`` can only raise the sellable count, so
    there is no invariant to re-check and no need to go through
    ``shift_inventory`` -- the caller already holds these rows' locks, and
    taking them again in a different order is how two bookings deadlock.

    Returns what was drawn per night. Nights the block does not hold come back
    absent, not as an error: a group whose block runs Monday to Thursday and
    whose guest stays to Saturday has bought two ordinary nights, and those
    were never off sale for anyone else to be denied.
    """
    taken: dict[date, int] = {}
    for day in nights:
        row = db.execute(
            text(
                """
                SELECT rooms_held FROM booking.group_block_nights
                 WHERE block_id = :b AND room_type_id = :rt AND stay_date = :d
                 FOR UPDATE
                """
            ),
            {"b": block_id, "rt": room_type_id, "d": day},
        ).mappings().first()
        n = min(int(row["rooms_held"]), need) if row else 0
        if not n:
            continue
        db.execute(
            text(
                """
                UPDATE booking.group_block_nights
                   SET rooms_held = rooms_held - :n
                 WHERE block_id = :b AND room_type_id = :rt AND stay_date = :d
                """
            ),
            {"n": n, "b": block_id, "rt": room_type_id, "d": day},
        )
        db.execute(
            text(
                """
                UPDATE booking.room_type_inventory_days
                   SET allotment_units = GREATEST(allotment_units - :n, 0)
                 WHERE property_id = :p AND room_type_id = :rt
                   AND stay_date = :d
                """
            ),
            {"n": n, "p": property_id, "rt": room_type_id, "d": day},
        )
        taken[day] = n
    return taken


def give_back(
    db: Session,
    *,
    block_id: uuid.UUID,
    property_id: uuid.UUID,
    organization_id: uuid.UUID,
    units: list[dict],
) -> int:
    """Return rooms to the block when a picked-up booking is cancelled.

    Without this a cancelled group booking would leak: the room comes out of
    ``reserved_units`` by the ordinary cancellation path and goes back on
    general sale, so the group silently loses a room it had agreed and the
    block's own numbers stop adding up.

    Capped at what the block originally agreed, so a booking cancelled twice,
    or one that ran beyond the block's dates, cannot inflate it.
    """
    given = 0
    for u in units:
        for day in _nights(u["arrival_date"], u["departure_date"]):
            row = db.execute(
                text(
                    """
                    SELECT n.rooms_held, l.rooms_blocked
                      FROM booking.group_block_nights n
                      JOIN booking.group_block_lines l
                        ON l.block_id = n.block_id
                       AND l.room_type_id = n.room_type_id
                     WHERE n.block_id = :b AND n.room_type_id = :rt
                       AND n.stay_date = :d
                       FOR UPDATE OF n
                    """
                ),
                {"b": block_id, "rt": u["room_type_id"], "d": day},
            ).mappings().first()
            if row is None or row["rooms_held"] >= row["rooms_blocked"]:
                continue
            db.execute(
                text("UPDATE booking.group_block_nights "
                     "SET rooms_held = rooms_held + 1 "
                     "WHERE block_id = :b AND room_type_id = :rt "
                     "AND stay_date = :d"),
                {"b": block_id, "rt": u["room_type_id"], "d": day},
            )
            given += 1
            shift_inventory(
                db, property_id=property_id, organization_id=organization_id,
                counter="allotment_units", delta={(u["room_type_id"], day): 1})
    return given


def release(
    db: Session,
    *,
    block_id: uuid.UUID,
    property_id: uuid.UUID,
    organization_id: uuid.UUID,
    released_by: uuid.UUID | None = None,
    status: str = "released",
) -> int:
    """Give back every room this block still holds.

    Bookings already picked up are untouched, and that is the point of a
    cut-off: the group keeps what it has claimed and the hotel gets back what
    it has not. Releasing is idempotent -- a block already released holds
    nothing, so the second call gives back nothing rather than crediting the
    hotel with rooms twice.
    """
    if status not in ("released", "cancelled"):
        raise BlockError(f"A block cannot end as {status!r}.")

    # Lock inventory before block nights, which is the order ``create_hold``
    # takes them in. Both orders work on their own; mixing them is what lets a
    # release and a booking wait on each other forever. The night *keys* never
    # change after a block is created -- only ``rooms_held`` moves -- so
    # reading them unlocked to decide what to lock is safe.
    keys = db.execute(
        text(
            """
            SELECT DISTINCT room_type_id, stay_date
              FROM booking.group_block_nights
             WHERE block_id = :b
             ORDER BY room_type_id, stay_date
            """
        ),
        {"b": block_id},
    ).mappings().all()
    if keys:
        db.execute(
            text(
                """
                SELECT 1 FROM booking.room_type_inventory_days
                 WHERE property_id = :p
                   AND (room_type_id, stay_date) IN (
                       SELECT * FROM unnest(CAST(:rts AS uuid[]),
                                            CAST(:days AS date[])))
                 ORDER BY room_type_id, stay_date
                 FOR UPDATE
                """
            ),
            {"p": property_id,
             "rts": [k["room_type_id"] for k in keys],
             "days": [k["stay_date"] for k in keys]},
        )

    held = db.execute(
        text(
            """
            SELECT room_type_id, stay_date, rooms_held
              FROM booking.group_block_nights
             WHERE block_id = :b AND rooms_held > 0
             ORDER BY room_type_id, stay_date
             FOR UPDATE
            """
        ),
        {"b": block_id},
    ).mappings().all()

    delta = {(r["room_type_id"], r["stay_date"]): -int(r["rooms_held"])
             for r in held}
    if delta:
        db.execute(
            text("UPDATE booking.group_block_nights SET rooms_held = 0 "
                 "WHERE block_id = :b"),
            {"b": block_id},
        )
        shift_inventory(db, property_id=property_id,
                        organization_id=organization_id,
                        counter="allotment_units", delta=delta)

    db.execute(
        text(
            """
            UPDATE booking.group_blocks
               SET status = :st, released_at = now(), released_by = :by,
                   updated_at = now(), version = version + 1
             WHERE id = :b AND status = 'open'
            """
        ),
        {"b": block_id, "st": status, "by": released_by},
    )
    return sum(-n for n in delta.values())


def sweep_cut_offs(db: Session, *, today: date) -> list[dict]:
    """Release every open block whose cut-off has arrived.

    Runs against every tenant, so it discovers blocks under ``system_context``
    and then binds each property's own tenant before touching it -- the same
    shape the occupancy sweep uses, and for the same reason: a background loop
    has no user to inherit a tenant from, and reading with RLS unbound finds
    nothing at all.

    A block is released *on* its cut-off date rather than after it. "Cut-off
    15 March" means the rooms are back on sale that morning, which is the last
    day they can still be sold for the stay.
    """
    from chirala_common.db import bind_tenant_context, system_context

    system_context(db, reason="group block cut-off: find blocks due")
    due = db.execute(
        text(
            """
            SELECT id, organization_id, property_id, code, name
              FROM booking.group_blocks
             WHERE status = 'open' AND cut_off_date IS NOT NULL
               AND cut_off_date <= :today
             ORDER BY cut_off_date
            """
        ),
        {"today": today},
    ).mappings().all()

    out: list[dict] = []
    for b in due:
        try:
            bind_tenant_context(db, organization_id=b["organization_id"],
                                property_id=b["property_id"])
            given = release(db, block_id=b["id"], property_id=b["property_id"],
                            organization_id=b["organization_id"])
            out.append({"block_id": str(b["id"]), "code": b["code"],
                        "name": b["name"], "released": given, "status": "ok"})
        except Exception as exc:  # one bad block must not stop the rest
            out.append({"block_id": str(b["id"]), "code": b["code"],
                        "status": "failed", "detail": str(exc)})
    return out


def summary(db: Session, block_id: uuid.UUID) -> dict:
    """The block, its lines, and how much of each has actually been taken.

    Picked-up counts are derived from the reservations themselves rather than
    kept as a running total on the line. A total would have to be corrected
    every time a booking is cancelled, moved or shortened, and the first time
    somebody forgot, the block would quietly stop adding up.
    """
    block = db.execute(
        text(
            """
            SELECT b.*, ca.name AS account_name
              FROM booking.group_blocks b
              LEFT JOIN engagement.commercial_accounts ca
                     ON ca.id = b.commercial_account_id
             WHERE b.id = :b
            """
        ),
        {"b": block_id},
    ).mappings().first()
    if block is None:
        raise BlockError("No such block.")

    lines = db.execute(
        text(
            """
            SELECT l.room_type_id, rt.name AS room_type, l.rooms_blocked,
                   l.nightly_rate,
                   COALESCE(pick.rooms, 0) AS rooms_picked_up,
                   COALESCE(held.still_held, 0) AS rooms_still_held
              FROM booking.group_block_lines l
              JOIN property.room_types rt ON rt.id = l.room_type_id
              LEFT JOIN (
                    SELECT ru.room_type_id, count(*) AS rooms
                      FROM booking.reservation_units ru
                      JOIN booking.reservations r ON r.id = ru.reservation_id
                     WHERE r.group_block_id = :b
                       AND ru.status NOT IN ('cancelled', 'no_show')
                     GROUP BY ru.room_type_id
              ) pick ON pick.room_type_id = l.room_type_id
              LEFT JOIN (
                    -- The most any night still holds. A block part-taken on
                    -- one night and untouched on another is not "half
                    -- released": what is still on offer is what the emptiest
                    -- night can supply, and the fullest night is what the
                    -- hotel is still carrying.
                    SELECT room_type_id, max(rooms_held) AS still_held
                      FROM booking.group_block_nights
                     WHERE block_id = :b
                     GROUP BY room_type_id
              ) held ON held.room_type_id = l.room_type_id
             WHERE l.block_id = :b
             ORDER BY rt.name
            """
        ),
        {"b": block_id},
    ).mappings().all()

    return {"block": dict(block), "lines": [dict(r) for r in lines]}
