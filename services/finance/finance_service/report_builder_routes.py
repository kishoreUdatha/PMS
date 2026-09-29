"""Custom report builder: pick a dataset, columns, filters, a grouping.

The fixed reports in backoffice_reports.py answer the questions somebody
thought of in advance. This answers the rest. "Payments by method last
month", "reservations from Agoda by nationality", "what each outlet took".

**Nothing from the request reaches the SQL as text.** A dataset is a fixed
FROM clause. A column is a key into a whitelist that maps to a fixed SQL
expression. An operator is one of a handful, each with a fixed template.
Values are always bound parameters. So the query is assembled only from
pieces written here, and a request can choose among them but cannot add
to them. That is what makes a user-driven query builder safe to expose.

**Scoped twice.** Every dataset filters on the property the caller was
checked against, and row security filters on the tenant underneath.

**Bounded.** At most MAX_ROWS rows and a MAX_DAYS window, so a careless
report cannot scan years of folio entries on the request thread.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal

from chirala_common.audit import record_audit
from chirala_common.authz import Caller, assert_property_in_org
from chirala_common.routing import TransactionalRoute
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.orm import Session

from .database import get_session
from .night_audit import local_today
from .report_routes import require_permission

builder_router = APIRouter(prefix="/reports/builder", tags=["reports"],
                           route_class=TransactionalRoute)

MAX_ROWS = 5000
MAX_DAYS = 366
#: Operators allowed for each kind of column, and their SQL template.
OPS = {
    "eq": "{col} = {p}",
    "neq": "{col} <> {p}",
    "contains": "{col} ILIKE {p}",
    "gte": "{col} >= {p}",
    "lte": "{col} <= {p}",
}
OPS_FOR_KIND = {
    "text": ("eq", "neq", "contains"),
    "date": ("gte", "lte", "eq"),
    "number": ("eq", "gte", "lte"),
    "money": ("gte", "lte"),
}


@dataclass(frozen=True)
class C:
    label: str
    sql: str
    kind: str = "text"          # text | date | number | money
    #: Offer a dropdown of the values that occur, for an "is" filter.
    choices: bool = False


@dataclass(frozen=True)
class Dataset:
    label: str
    description: str
    source: str                  # FROM ... WHERE <property filter>
    date_col: str                # the column the date range applies to
    columns: dict[str, C] = field(default_factory=dict)
    default: tuple[str, ...] = ()


DATASETS: dict[str, Dataset] = {
    "reservations": Dataset(
        label="Reservations",
        description="One row per booking, dated by arrival.",
        source="""
            FROM booking.reservations r
            LEFT JOIN engagement.guests g ON g.id = r.primary_guest_id
            LEFT JOIN engagement.booking_attributes bs ON bs.id = r.business_source_id
            LEFT JOIN LATERAL (
                SELECT min(u.arrival_date) AS arrival, max(u.departure_date) AS departure,
                       count(*) AS rooms, sum(u.adults) AS adults,
                       sum(u.children) AS children,
                       max(u.departure_date - u.arrival_date) AS nights,
                       sum(coalesce(u.nightly_rate, 0)
                           * (u.departure_date - u.arrival_date)) AS value
                FROM booking.reservation_units u
                WHERE u.reservation_id = r.id AND u.status <> 'cancelled'
            ) u ON true
            WHERE r.property_id = :prop
        """,
        date_col="arrival",
        columns={
            "number": C("Reservation", "r.number"),
            "status": C("Status", "r.status", choices=True),
            "source": C("Source", "r.source", choices=True),
            "business_source": C("Business source", "bs.name", choices=True),
            "guest": C("Guest", "g.full_name"),
            "nationality": C("Nationality", "g.nationality", choices=True),
            "country": C("Country", "g.country", choices=True),
            "company": C("Company", "r.company_name", choices=True),
            "travel_agent": C("Travel agent", "r.travel_agent", choices=True),
            "arrival": C("Arrival", "u.arrival", "date"),
            "departure": C("Departure", "u.departure", "date"),
            "booked_on": C("Booked on", "r.created_at::date", "date"),
            "nights": C("Nights", "u.nights", "number"),
            "rooms": C("Rooms", "u.rooms", "number"),
            "adults": C("Adults", "u.adults", "number"),
            "value": C("Room value", "u.value", "money"),
        },
        default=("number", "guest", "arrival", "departure", "status", "value"),
    ),
    "payments": Dataset(
        label="Payments",
        description="Money received, dated by the property's local day.",
        source="""
            FROM finance.payments p
            JOIN iam.properties pr ON pr.id = p.property_id
            WHERE p.property_id = :prop
        """,
        date_col="received_on",
        columns={
            "received_on": C("Received on", "(p.received_at AT TIME ZONE pr.timezone)::date", "date"),
            "receipt_no": C("Receipt", "p.receipt_no"),
            "method": C("Method", "p.method", choices=True),
            "source": C("Taken at", "p.source", choices=True),
            "status": C("Status", "p.status", choices=True),
            "reference": C("Reference", "p.reference"),
            "amount": C("Amount", "p.amount", "money"),
        },
        default=("received_on", "receipt_no", "method", "source", "amount"),
    ),
    "charges": Dataset(
        label="Charges",
        description="Everything posted to a bill, dated by business date.",
        source="""
            FROM finance.folio_entries e
            JOIN finance.folios f ON f.id = e.folio_id
            LEFT JOIN booking.reservations r ON r.id = f.reservation_id
            WHERE e.property_id = :prop AND e.entry_type = 'debit'
        """,
        date_col="business_date",
        columns={
            "business_date": C("Business date", "e.business_date", "date"),
            "department": C("Department", "e.source_type", choices=True),
            "bill_type": C("Bill type", "f.type", choices=True),
            "reservation": C("Reservation", "r.number"),
            "note": C("Description", "e.note"),
            "amount": C("Amount", "e.amount", "money"),
        },
        default=("business_date", "department", "reservation", "note", "amount"),
    ),
    "pos": Dataset(
        label="Restaurant checks",
        description="POS checks, dated by business date.",
        source="""
            FROM finance.pos_checks c
            JOIN finance.pos_outlets o ON o.id = c.outlet_id
            WHERE c.property_id = :prop
        """,
        date_col="business_date",
        columns={
            "business_date": C("Business date", "c.business_date", "date"),
            "number": C("Check", "c.number"),
            "outlet": C("Outlet", "o.name", choices=True),
            "status": C("Status", "c.status", choices=True),
            "method": C("Paid by", "c.method", choices=True),
            "covers": C("Covers", "c.covers", "number"),
            "subtotal": C("Subtotal", "c.subtotal", "money"),
            "total": C("Total", "c.total", "money"),
        },
        default=("business_date", "number", "outlet", "status", "total"),
    ),
    "guests": Dataset(
        label="Guests",
        description="Guests who stayed here, dated by their last stay.",
        source="""
            FROM engagement.guests g
            JOIN LATERAL (
                SELECT count(DISTINCT r.id) AS stays,
                       min(u.arrival_date) AS first_stay,
                       max(u.departure_date) AS last_stay
                FROM booking.reservations r
                JOIN booking.reservation_units u ON u.reservation_id = r.id
                WHERE r.primary_guest_id = g.id AND r.property_id = :prop
                  AND u.status <> 'cancelled'
            ) s ON s.stays > 0
            WHERE true
        """,
        date_col="last_stay",
        columns={
            "name": C("Guest", "g.full_name"),
            "email": C("Email", "g.email"),
            "phone": C("Phone", "g.phone"),
            "nationality": C("Nationality", "g.nationality", choices=True),
            "country": C("Country", "g.country", choices=True),
            "city": C("City", "g.city", choices=True),
            "state": C("State", "g.state", choices=True),
            "stays": C("Stays", "s.stays", "number"),
            "first_stay": C("First stay", "s.first_stay", "date"),
            "last_stay": C("Last stay", "s.last_stay", "date"),
        },
        default=("name", "country", "stays", "last_stay"),
    ),
}


# --------------------------------------------------------------- catalog --

@builder_router.get("/datasets")
def datasets(caller: Caller = Depends(require_permission("reports", "view"))):
    return [
        {"key": k, "label": d.label, "description": d.description,
         "date_column": d.date_col, "default_columns": list(d.default),
         "columns": [{"key": ck, "label": c.label, "kind": c.kind,
                      "choices": c.choices, "ops": list(OPS_FOR_KIND[c.kind])}
                     for ck, c in d.columns.items()]}
        for k, d in DATASETS.items()
    ]


def _dataset(key: str) -> Dataset:
    ds = DATASETS.get(key)
    if ds is None:
        raise HTTPException(422, "Unknown dataset")
    return ds


@builder_router.get("/{dataset}/values")
def column_values(dataset: str, column: str, property_id: uuid.UUID,
                  caller: Caller = Depends(require_permission("reports", "view")),
                  db: Session = Depends(get_session)):
    """The values that occur in a column, for an "is" filter's dropdown."""
    assert_property_in_org(db, caller, property_id)
    ds = _dataset(dataset)
    col = ds.columns.get(column)
    if col is None or not col.choices:
        raise HTTPException(422, "That column has no list of values")
    rows = db.execute(text(
        f"SELECT DISTINCT {col.sql} AS v {ds.source} AND {col.sql} IS NOT NULL "
        f"ORDER BY 1 LIMIT 200"), {"prop": property_id}).scalars().all()
    return [str(v) for v in rows]


# ------------------------------------------------------------------- run --

class FilterIn(BaseModel):
    column: str
    op: str
    value: str = Field(max_length=200)


class DefinitionIn(BaseModel):
    dataset: str
    columns: list[str] = Field(min_length=1, max_length=20)
    date_from: date | None = None
    date_to: date | None = None
    filters: list[FilterIn] = Field(default_factory=list, max_length=10)
    group_by: str | None = None
    sort: str | None = None
    descending: bool = False


def _coerce(kind: str, value: str):
    try:
        if kind == "date":
            return date.fromisoformat(value)
        if kind in ("number", "money"):
            return Decimal(value)
    except (ValueError, ArithmeticError):
        raise HTTPException(422, f"'{value}' is not a valid {kind}") from None
    return value


def build(ds: Dataset, d: DefinitionIn, today: date) -> tuple[str, dict, list[dict]]:
    """The SQL, its parameters, and the output columns. Whitelist only."""
    unknown = [c for c in d.columns if c not in ds.columns]
    if unknown:
        raise HTTPException(422, f"Unknown column: {', '.join(unknown)}")
    date_to = d.date_to or today
    date_from = d.date_from or (date_to - timedelta(days=30))
    if date_from > date_to:
        raise HTTPException(422, "The start date is after the end date.")
    if (date_to - date_from).days > MAX_DAYS:
        raise HTTPException(422, f"Choose a window of at most {MAX_DAYS} days.")

    params: dict = {"prop": None, "df": date_from, "dt": date_to}
    date_sql = ds.columns[ds.date_col].sql
    where = [f"{date_sql} BETWEEN :df AND :dt"]
    for n, f in enumerate(d.filters):
        col = ds.columns.get(f.column)
        if col is None:
            raise HTTPException(422, f"Unknown filter column: {f.column}")
        if f.op not in OPS_FOR_KIND[col.kind]:
            raise HTTPException(422, f"'{f.op}' cannot be used on {col.label}")
        p = f"f{n}"
        value = _coerce(col.kind, f.value)
        params[p] = f"%{value}%" if f.op == "contains" else value
        where.append(OPS[f.op].format(col=col.sql, p=f":{p}"))

    if d.group_by:
        g = ds.columns.get(d.group_by)
        if g is None or g.kind in ("money", "number"):
            raise HTTPException(422, "Group by a text or date column.")
        out = [{"key": d.group_by, "label": g.label, "kind": g.kind},
               {"key": "count", "label": "Count", "kind": "number"}]
        select = [f'{g.sql} AS "{d.group_by}"', 'count(*) AS "count"']
        for key in d.columns:
            c = ds.columns[key]
            if c.kind in ("money", "number") and key != d.group_by:
                select.append(f'sum({c.sql}) AS "{key}"')
                out.append({"key": key, "label": f"Total {c.label.lower()}", "kind": c.kind})
        group = f" GROUP BY {g.sql}"
        order_keys = {o["key"] for o in out}
    else:
        out = [{"key": k, "label": ds.columns[k].label, "kind": ds.columns[k].kind}
               for k in d.columns]
        select = [f'{ds.columns[k].sql} AS "{k}"' for k in d.columns]
        group = ""
        order_keys = set(d.columns)

    sort = d.sort if d.sort in order_keys else out[0]["key"]
    direction = "DESC" if d.descending else "ASC"
    sql = (f"SELECT {', '.join(select)} {ds.source} AND {' AND '.join(where)}"
           f'{group} ORDER BY "{sort}" {direction} NULLS LAST LIMIT {MAX_ROWS + 1}')
    return sql, params, out


@builder_router.post("/run")
def run(property_id: uuid.UUID, body: DefinitionIn,
        caller: Caller = Depends(require_permission("reports", "view")),
        db: Session = Depends(get_session)):
    assert_property_in_org(db, caller, property_id)
    ds = _dataset(body.dataset)
    sql, params, columns = build(ds, body, local_today(db, property_id))
    params["prop"] = property_id
    rows = [dict(r) for r in db.execute(text(sql), params).mappings()]
    truncated = len(rows) > MAX_ROWS
    rows = rows[:MAX_ROWS]
    totals = {}
    for c in columns:
        if c["kind"] in ("money", "number") and c["key"] != "count":
            totals[c["key"]] = sum((Decimal(str(r[c["key"]] or 0)) for r in rows), Decimal("0"))
    if any(c["key"] == "count" for c in columns):
        totals["count"] = sum(int(r["count"] or 0) for r in rows)
    return {"columns": columns, "rows": rows, "totals": totals,
            "truncated": truncated, "row_limit": MAX_ROWS,
            "date_from": params["df"], "date_to": params["dt"]}


# ----------------------------------------------------------------- saved --

class SavedIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    definition: DefinitionIn


@builder_router.get("/saved")
def list_saved(property_id: uuid.UUID,
               caller: Caller = Depends(require_permission("reports", "view")),
               db: Session = Depends(get_session)):
    assert_property_in_org(db, caller, property_id)
    return [dict(r) for r in db.execute(text("""
        SELECT id, name, dataset, definition, updated_at FROM finance.saved_reports
        WHERE property_id = :p ORDER BY name
    """), {"p": property_id}).mappings()]


@builder_router.post("/saved", status_code=201)
def save(property_id: uuid.UUID, body: SavedIn,
         caller: Caller = Depends(require_permission("reports", "view")),
         db: Session = Depends(get_session)):
    """Save (or overwrite by name) a report definition. Validated by building it."""
    assert_property_in_org(db, caller, property_id)
    ds = _dataset(body.definition.dataset)
    build(ds, body.definition, date.today())
    org = db.execute(text("SELECT organization_id FROM iam.properties WHERE id = :p"),
                     {"p": property_id}).scalar_one()
    rid = db.execute(text("""
        INSERT INTO finance.saved_reports
            (organization_id, property_id, name, dataset, definition, created_by)
        VALUES (:org, :p, :n, :d, CAST(:def AS jsonb), :by)
        ON CONFLICT (property_id, name) DO UPDATE
           SET dataset = EXCLUDED.dataset, definition = EXCLUDED.definition,
               updated_at = now()
        RETURNING id
    """), {"org": org, "p": property_id, "n": body.name.strip(),
           "d": body.definition.dataset,
           "def": body.definition.model_dump_json(), "by": caller.user_id}).scalar_one()
    record_audit(db, action="report.saved", entity_type="saved_report",
                 entity_id=str(rid), organization_id=org, property_id=property_id,
                 actor_subject=caller.subject, after={"name": body.name.strip()})
    return {"id": rid}


@builder_router.delete("/saved/{report_id}")
def delete_saved(report_id: uuid.UUID, property_id: uuid.UUID,
                 caller: Caller = Depends(require_permission("reports", "view")),
                 db: Session = Depends(get_session)):
    assert_property_in_org(db, caller, property_id)
    row = db.execute(text("DELETE FROM finance.saved_reports WHERE id = :i "
                          "AND property_id = :p RETURNING organization_id, name"),
                     {"i": report_id, "p": property_id}).mappings().first()
    if row is None:
        raise HTTPException(404, "Saved report not found")
    record_audit(db, action="report.deleted", entity_type="saved_report",
                 entity_id=str(report_id), organization_id=row["organization_id"],
                 property_id=property_id, actor_subject=caller.subject,
                 before={"name": row["name"]})
    return {"deleted": True}
