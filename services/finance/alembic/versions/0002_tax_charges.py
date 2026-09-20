"""Tax and service charge setup (screen 118)

Revision ID: 0002_tax_charges
Revises: 0001_finance_core
Create Date: 2026-09-09

``finance.tax_rules`` was created by 0001 as scaffolding — a code, a validity
window and an opaque ``calculation_rule`` blob — and never filled in or read by
any code. It already has the one thing this screen genuinely needs, which is
*versioning by effective date*: that is exactly what "Schedule Change" means, a
future revision of the same rule sitting alongside the current one.

So this builds on it rather than adding a second table beside it, and gives it
the columns the screen actually shows: a name, what kind of charge it is, its
rate, whether it is added to the bill or already inside the price, what it
applies to, and where it posts in the accounts.

One rename matters. ``tax_rules.version`` counted revisions, but everywhere else
in this codebase ``version`` is the optimistic-locking counter — a trap for
whoever writes the next query. It becomes ``revision``, and a normal ``version``
is added for locking. The table is empty and unread, so the rename costs
nothing now and removes the ambiguity permanently.

``calculation_rule`` is kept for the one genuinely nested thing: how a GST group
splits into components (CGST 2.5 + SGST 2.5). A rate that decomposes is data;
everything else is a column.
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0002_tax_charges"
down_revision: str | None = "0001_finance_core"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

CHARGE_TYPES = ("tax_group", "gst_component", "service_charge", "other_tax")
RATE_TYPES = ("percent", "amount")
AMOUNT_BASES = ("per_night", "per_person", "per_stay", "per_unit")
APPLY_AS = ("exclusive", "inclusive")
STATUSES = ("active", "inactive")
APPLICABILITY = (
    "rooms", "spa", "fnb_outlets", "banquets_events", "in_room_dining",
    "other_services",
)

# The configuration on mockup 118, seeded as demo data alongside the rest.
# rate_value is the headline rate; components carry the CGST/SGST split.
SEED = [
    ("GST_STD", "GST Standard (5%)", "tax_group", "percent", 5, None, "exclusive",
     "{rooms,fnb_outlets,spa}", "2025-09-01", "Room Revenue (4001)",
     '{"components": [{"code": "CGST", "rate": 2.5}, {"code": "SGST", "rate": 2.5}]}'),
    ("GST_RED", "GST Reduced (12%)", "tax_group", "percent", 12, None, "exclusive",
     "{fnb_outlets}", "2025-09-01", "F&B Revenue (4002)",
     '{"components": [{"code": "CGST", "rate": 6}, {"code": "SGST", "rate": 6}]}'),
    ("GST_18", "GST Standard (18%)", "tax_group", "percent", 18, None, "exclusive",
     "{banquets_events,other_services}", "2025-09-01", "Banquet Revenue (4003)",
     '{"components": [{"code": "CGST", "rate": 9}, {"code": "SGST", "rate": 9}]}'),
    ("GST_28", "GST Higher (28%)", "tax_group", "percent", 28, None, "exclusive",
     "{fnb_outlets,other_services}", "2025-09-01", "F&B Revenue (4002)",
     '{"components": [{"code": "CGST", "rate": 14}, {"code": "SGST", "rate": 14}]}'),
    ("GST_EXEMPT", "GST Exempt", "tax_group", "percent", 0, None, "exclusive",
     "{other_services}", "2025-09-01", None, '{"components": []}'),
    ("CGST", "Central GST", "gst_component", "percent", 0, None, "exclusive",
     "{rooms,fnb_outlets,spa,banquets_events,in_room_dining,other_services}",
     "2025-09-01", "CGST Payable (2101)", '{"note": "Rate comes from the tax group"}'),
    ("SGST", "State GST", "gst_component", "percent", 0, None, "exclusive",
     "{rooms,fnb_outlets,spa,banquets_events,in_room_dining,other_services}",
     "2025-09-01", "SGST Payable (2102)", '{"note": "Rate comes from the tax group"}'),
    ("IGST", "Integrated GST", "gst_component", "percent", 0, None, "exclusive",
     "{rooms,fnb_outlets,spa,banquets_events,in_room_dining,other_services}",
     "2025-09-01", "IGST Payable (2103)",
     '{"note": "Inter-state supply, outside Andhra Pradesh"}'),
    ("CESS", "Compensation Cess", "gst_component", "percent", 0, None, "exclusive",
     "{fnb_outlets}", "2025-09-01", "Cess Payable (2104)",
     '{"note": "Applies to selected goods"}'),
    ("SC_10", "Service Charge 10%", "service_charge", "percent", 10, None, "exclusive",
     "{fnb_outlets}", "2026-04-01", "Service Charge Income (4005)",
     '{"remarks": "10% service charge as per resort policy."}'),
    ("SC_07", "Service Charge 7%", "service_charge", "percent", 7, None, "exclusive",
     "{in_room_dining}", "2026-04-01", "Service Charge Income (4005)", "{}"),
    ("SC_05", "Service Charge 5%", "service_charge", "percent", 5, None, "exclusive",
     "{spa}", "2026-04-01", "Service Charge Income (4005)", "{}"),
    ("ENV_FEE", "Environmental Fee", "other_tax", "amount", 50, "per_night",
     "exclusive", "{rooms}", "2026-06-01", "Environmental Levy (2201)", "{}"),
    ("BEACH_CESS", "Beach Conservation Cess", "other_tax", "amount", 20, "per_night",
     "exclusive", "{rooms}", "2026-09-01", "Local Cess Payable (2202)", "{}"),
]


def upgrade() -> None:
    quoted = lambda values: ", ".join(f"'{v}'" for v in values)  # noqa: E731

    # `version` here counted revisions, not concurrent edits. Rename it before
    # anything starts relying on the wrong meaning.
    op.execute("ALTER TABLE finance.tax_rules RENAME COLUMN version TO revision")
    op.execute(
        "ALTER TABLE finance.tax_rules ADD COLUMN version bigint NOT NULL DEFAULT 0"
    )
    op.execute(
        "ALTER TABLE finance.tax_rules ALTER COLUMN calculation_rule "
        "SET DEFAULT '{}'::jsonb"
    )

    for ddl in (
        "ADD COLUMN name varchar(120) NOT NULL DEFAULT ''",
        "ADD COLUMN charge_type varchar(20) NOT NULL DEFAULT 'other_tax'",
        "ADD COLUMN rate_type varchar(10) NOT NULL DEFAULT 'percent'",
        "ADD COLUMN rate_value numeric(12, 4) NOT NULL DEFAULT 0",
        "ADD COLUMN amount_basis varchar(20)",
        "ADD COLUMN apply_as varchar(10) NOT NULL DEFAULT 'exclusive'",
        "ADD COLUMN applicability text[] NOT NULL DEFAULT '{}'",
        "ADD COLUMN income_account varchar(80)",
        "ADD COLUMN parent_group_id uuid",
        "ADD COLUMN remarks varchar(400)",
        "ADD COLUMN status varchar(20) NOT NULL DEFAULT 'active'",
        "ADD COLUMN created_by uuid",
        "ADD COLUMN updated_by uuid",
    ):
        op.execute(f"ALTER TABLE finance.tax_rules {ddl}")

    op.execute(
        f"""
        ALTER TABLE finance.tax_rules
          ADD CONSTRAINT ck_tax_charge_type
              CHECK (charge_type IN ({quoted(CHARGE_TYPES)})),
          ADD CONSTRAINT ck_tax_rate_type CHECK (rate_type IN ({quoted(RATE_TYPES)})),
          ADD CONSTRAINT ck_tax_apply_as CHECK (apply_as IN ({quoted(APPLY_AS)})),
          ADD CONSTRAINT ck_tax_status CHECK (status IN ({quoted(STATUSES)})),
          ADD CONSTRAINT ck_tax_rate_value CHECK (rate_value >= 0),
          -- A percentage over 100 is a data entry slip, not a tax.
          ADD CONSTRAINT ck_tax_percent_cap
              CHECK (rate_type <> 'percent' OR rate_value <= 100),
          -- A flat amount needs to say what it is charged per.
          ADD CONSTRAINT ck_tax_amount_basis CHECK (
              (rate_type = 'amount' AND amount_basis IN ({quoted(AMOUNT_BASES)}))
              OR (rate_type = 'percent' AND amount_basis IS NULL)
          ),
          ADD CONSTRAINT ck_tax_dates
              CHECK (effective_to IS NULL OR effective_to >= effective_from),
          ADD CONSTRAINT ck_tax_applicability
              CHECK (applicability <@ ARRAY[{quoted(APPLICABILITY)}]::text[]),
          ADD CONSTRAINT fk_tax_parent_group
              FOREIGN KEY (parent_group_id) REFERENCES finance.tax_rules (id)
        """
    )
    op.execute(
        "CREATE INDEX ix_tax_rules_property "
        "ON finance.tax_rules (property_id, charge_type, status)"
    )
    op.execute(
        "CREATE INDEX ix_tax_rules_effective "
        "ON finance.tax_rules (property_id, code, effective_from DESC)"
    )

    # ---------------- seed --------------------------------------------------
    for (code, name, ctype, rtype, rate, basis, apply_as, applic, eff_from,
         account, rule) in SEED:
        op.execute(
            f"""
            INSERT INTO finance.tax_rules
                (organization_id, property_id, code, revision, name, charge_type,
                 rate_type, rate_value, amount_basis, apply_as, applicability,
                 income_account, effective_from, status, calculation_rule)
            SELECT p.organization_id, p.id, '{code}', 1, $${name}$$, '{ctype}',
                   '{rtype}', {rate},
                   {f"'{basis}'" if basis else "NULL"},
                   '{apply_as}', '{applic}'::text[],
                   {f"$${account}$$" if account else "NULL"},
                   DATE '{eff_from}', 'active', $${rule}$$::jsonb
            FROM iam.properties p
            ON CONFLICT (property_id, code, revision) DO NOTHING
            """
        )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS finance.ix_tax_rules_effective")
    op.execute("DROP INDEX IF EXISTS finance.ix_tax_rules_property")
    for name in (
        "fk_tax_parent_group", "ck_tax_applicability", "ck_tax_dates",
        "ck_tax_amount_basis", "ck_tax_percent_cap", "ck_tax_rate_value",
        "ck_tax_status", "ck_tax_apply_as", "ck_tax_rate_type", "ck_tax_charge_type",
    ):
        op.execute(f"ALTER TABLE finance.tax_rules DROP CONSTRAINT IF EXISTS {name}")
    for col in (
        "updated_by", "created_by", "status", "remarks", "parent_group_id",
        "income_account", "applicability", "apply_as", "amount_basis",
        "rate_value", "rate_type", "charge_type", "name", "version",
    ):
        op.execute(f"ALTER TABLE finance.tax_rules DROP COLUMN IF EXISTS {col}")
    op.execute("ALTER TABLE finance.tax_rules RENAME COLUMN revision TO version")
