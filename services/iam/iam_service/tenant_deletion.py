"""Removing a tenant and everything scoped to it.

Separated from the routes because the hard part is not the endpoint, it is
knowing when to stop.

Three things this gets right that a hand-written DELETE did not:

**Order is discovered, not declared.** 102 tables carry an organisation or
property scope and the graph between them is not a tree. Each table is
attempted inside a SAVEPOINT and anything that fails is retried on the next
pass, so the order falls out of the constraints themselves rather than being
a list somebody has to maintain as the schema grows.

**Some children carry no scope of their own.** ``finance.folio_entry_taxes``
belongs to a folio, not to an organisation, so a scoped sweep can never see it
-- and its parent cannot be deleted while it exists. Those are named
explicitly below, by their parent.

**The commit condition is "nothing references the tenant", not "the tenant is
gone".** Deleting the organisation row while children survive is worse than
not deleting it at all: it converts a live tenant into orphaned rows that no
screen can show and no policy can scope. Doing this by hand, that is exactly
what happened, and it is why the check runs across every scoped table before
anything is committed.
"""
from __future__ import annotations

import base64
import gzip
import json
import uuid
from datetime import date, datetime, time, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

#: Kept deliberately: the record of what was done outlives its subject.
#:
#: platform.tenant_deletions carries an organization_id, so the scoped sweep
#: below would otherwise delete the very row describing the deletion -- which
#: it did, the first time this ran. Its organization_id is deliberately not a
#: foreign key for the same reason: the row has to refer to a tenant that no
#: longer exists.
NEVER_TOUCH = {("iam", "audit_events"), ("platform", "tenant_deletions")}

#: Children that hang off a parent rather than off a tenant, so a scoped sweep
#: never sees them. Each is (table, the DELETE that clears it for these orgs).
UNSCOPED_CHILDREN = [
    ("finance.folio_entry_taxes", """
        DELETE FROM finance.folio_entry_taxes WHERE folio_entry_id IN (
            SELECT e.id FROM finance.folio_entries e
            WHERE e.organization_id = ANY(:o))"""),
    ("finance.night_audit_steps", """
        DELETE FROM finance.night_audit_steps WHERE run_id IN (
            SELECT r.id FROM finance.night_audit_runs r
            WHERE r.organization_id = ANY(:o))"""),
    ("iam.role_permissions", """
        DELETE FROM iam.role_permissions WHERE role_id IN (
            SELECT id FROM iam.roles WHERE organization_id = ANY(:o))"""),
]

#: iam's own graph, child first. Everything else is discovered.
IAM_ORDER = [
    ("iam.role_assignments",
     "DELETE FROM iam.role_assignments WHERE membership_id IN "
     "(SELECT id FROM iam.memberships WHERE organization_id = ANY(:o))"),
    ("iam.memberships",
     "DELETE FROM iam.memberships WHERE organization_id = ANY(:o)"),
    ("iam.roles", "DELETE FROM iam.roles WHERE organization_id = ANY(:o)"),
    ("iam.properties",
     "DELETE FROM iam.properties WHERE organization_id = ANY(:o)"),
    ("iam.password_tokens",
     "DELETE FROM iam.password_tokens WHERE user_id = ANY(:u)"),
    ("iam.user_credentials",
     "DELETE FROM iam.user_credentials WHERE user_id = ANY(:u)"),
    ("iam.login_sessions",
     "DELETE FROM iam.login_sessions WHERE user_id = ANY(:u)"),
    ("iam.users", "DELETE FROM iam.users WHERE id = ANY(:u)"),
    ("iam.organizations",
     "DELETE FROM iam.organizations WHERE id = ANY(:o)"),
]


def scoped_tables(db: Session) -> list[tuple[str, str, bool, bool]]:
    """Every table carrying an organisation or property column."""
    return [
        (r[0], r[1], r[2], r[3])
        for r in db.execute(text("""
            SELECT table_schema, table_name,
                   bool_or(column_name = 'organization_id') AS has_org,
                   bool_or(column_name = 'property_id')     AS has_prop
            FROM information_schema.columns
            WHERE column_name IN ('organization_id', 'property_id')
              AND table_schema NOT IN ('pg_catalog', 'information_schema')
            GROUP BY 1, 2
        """)).all()
        if (r[0], r[1]) not in NEVER_TOUCH
    ]


def survey(db: Session, org_id: uuid.UUID) -> dict[str, int]:
    """What this tenant holds, table by table. Reads nothing destructive."""
    props = [str(r[0]) for r in db.execute(
        text("SELECT id FROM iam.properties WHERE organization_id = :o"),
        {"o": org_id}).all()]
    held: dict[str, int] = {}
    for sc, tb, has_org, has_prop in scoped_tables(db):
        where, params = [], {}
        if has_org:
            where.append("organization_id = :o")
            params["o"] = str(org_id)
        if has_prop and props:
            where.append("property_id = ANY(:p)")
            params["p"] = props
        if not where:
            continue
        n = db.execute(
            text(f'SELECT count(*) FROM "{sc}"."{tb}" WHERE ' + " OR ".join(where)),
            params).scalar()
        if n:
            held[f"{sc}.{tb}"] = n
    return held


def dangling(db: Session, org_id: uuid.UUID) -> dict[str, int]:
    """Rows still pointing at this organisation or one of its properties.

    The commit condition. Run after the sweep and before committing: anything
    here means the deletion is incomplete, and an incomplete deletion must not
    be kept.
    """
    left: dict[str, int] = {}
    for sc, tb, has_org, has_prop in scoped_tables(db):
        where, params = [], {}
        if has_org:
            where.append("organization_id = :o")
            params["o"] = str(org_id)
        if has_prop:
            where.append(
                "property_id IN (SELECT id FROM iam.properties "
                "WHERE organization_id = :o)")
            params["o"] = str(org_id)
        if not where:
            continue
        n = db.execute(
            text(f'SELECT count(*) FROM "{sc}"."{tb}" WHERE ' + " OR ".join(where)),
            params).scalar()
        if n:
            left[f"{sc}.{tb}"] = n
    return left


def purge(db: Session, org_id: uuid.UUID, *, max_passes: int = 10) -> dict[str, Any]:
    """Remove the tenant. Raises if anything is left pointing at it.

    Does not commit. The caller owns the transaction, so a refusal here undoes
    everything rather than leaving the database half-way through.
    """
    orgs = [str(org_id)]
    props = [str(r[0]) for r in db.execute(
        text("SELECT id FROM iam.properties WHERE organization_id = :o"),
        {"o": org_id}).all()]
    users = [str(r[0]) for r in db.execute(
        text("""SELECT DISTINCT m.user_id FROM iam.memberships m
                WHERE m.organization_id = :o
                  AND NOT EXISTS (SELECT 1 FROM iam.memberships x
                                  WHERE x.user_id = m.user_id
                                    AND x.organization_id <> :o)"""),
        {"o": org_id}).all()]

    removed: dict[str, int] = {}

    def run(label: str, sql: str, params: dict[str, Any]) -> bool:
        sp = db.begin_nested()
        try:
            n = db.execute(text(sql), params).rowcount
            sp.commit()
            if n:
                removed[label] = removed.get(label, 0) + n
            return True
        except Exception:                             # noqa: BLE001
            sp.rollback()
            return False

    # Children with no scope of their own, first: their parents cannot go
    # while they exist, and the scoped sweep below will never reach them.
    for label, sql in UNSCOPED_CHILDREN:
        run(label, sql, {"o": orgs})

    todo = scoped_tables(db)
    for _ in range(max_passes):
        stuck, progress = [], False
        for sc, tb, has_org, has_prop in todo:
            where, params = [], {}
            if has_org:
                where.append("organization_id = ANY(:o)")
                params["o"] = orgs
            if has_prop and props:
                where.append("property_id = ANY(:p)")
                params["p"] = props
            if not where:
                continue
            before = removed.get(f"{sc}.{tb}", 0)
            if run(f"{sc}.{tb}",
                   f'DELETE FROM "{sc}"."{tb}" WHERE ' + " OR ".join(where),
                   params):
                progress = progress or removed.get(f"{sc}.{tb}", 0) != before
            else:
                stuck.append((sc, tb, has_org, has_prop))
        todo = stuck
        if not todo or not progress:
            break

    for label, sql in IAM_ORDER:
        run(label, sql, {"o": orgs, "u": users})

    # The condition that matters. Not "is the organisation gone".
    left = dangling(db, org_id)
    if left:
        raise RuntimeError(
            "deletion incomplete, nothing was removed: "
            + ", ".join(f"{t}={n}" for t, n in sorted(left.items())))

    return {"removed": removed, "users_removed": len(users),
            "properties_removed": len(props)}


def export_tenant(db: Session, org_id: uuid.UUID) -> tuple[bytes, dict[str, int]]:
    """Everything this tenant holds, as one gzipped JSON document.

    Taken immediately before the purge and in the same transaction, so what is
    written is exactly what is about to be destroyed -- not what the tenant
    looked like when somebody filed the request, which may be days earlier.

    Rows are emitted table by table with their columns named, rather than as a
    pg_dump: the point is that a person can read it and answer "what did this
    customer have" long after the schema has moved on.
    """
    props = [str(r[0]) for r in db.execute(
        text("SELECT id FROM iam.properties WHERE organization_id = :o"),
        {"o": org_id}).all()]

    org = db.execute(
        text("SELECT * FROM iam.organizations WHERE id = :o"), {"o": org_id}
    ).mappings().first()

    document: dict[str, Any] = {
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "format": "chirala.tenant-export/1",
        "organization": {k: _plain(v) for k, v in dict(org or {}).items()},
        "properties": [
            {k: _plain(v) for k, v in dict(r).items()}
            for r in db.execute(
                text("SELECT * FROM iam.properties WHERE organization_id = :o"),
                {"o": org_id}).mappings().all()
        ],
        "tables": {},
    }

    counts: dict[str, int] = {}
    for sc, tb, has_org, has_prop in scoped_tables(db):
        where, params = [], {}
        if has_org:
            where.append("organization_id = :o")
            params["o"] = str(org_id)
        if has_prop and props:
            where.append("property_id = ANY(:p)")
            params["p"] = props
        if not where:
            continue
        rows = db.execute(
            text(f'SELECT * FROM "{sc}"."{tb}" WHERE ' + " OR ".join(where)),
            params).mappings().all()
        if rows:
            document["tables"][f"{sc}.{tb}"] = [
                {k: _plain(v) for k, v in dict(r).items()} for r in rows]
            counts[f"{sc}.{tb}"] = len(rows)

    document["row_counts"] = counts
    document["rows_total"] = sum(counts.values())
    blob = gzip.compress(
        json.dumps(document, indent=1, sort_keys=True).encode("utf-8"))
    return blob, counts


def _plain(value: Any) -> Any:
    """JSON-safe, without losing what the value was.

    Dates become ISO strings and Decimals become strings rather than floats --
    an export of somebody's money that silently rounds is worse than no export.
    """
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, (bytes, memoryview)):
        return base64.b64encode(bytes(value)).decode("ascii")
    return value
