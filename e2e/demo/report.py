"""Turn a demo run (out/results.json + out/isolation.json) into a Markdown report.

    python e2e/demo/report.py [out_dir]  ->  out/TEST_REPORT.md
"""
from __future__ import annotations

import json
import os
import sys

OUT = sys.argv[1] if len(sys.argv) > 1 else os.path.join(os.path.dirname(__file__), "..", "out")

DEFECTS = [
    ("High", "Onboarding blocks at step 3 on a field it never asks for",
     "Step 2 (Property) is only complete once a night-audit hour is set "
     "(`onboarding_routes._audit_hour_set`), but the Property step has no such "
     "field. A new tenant is stuck unless they know to open Night Audit.",
     "Set the hour from Night Audit, then return to the wizard."),
    ("High", "Inviting a team member fails with HTTP 500",
     "`POST /iam/invitations` inserts into `iam.users` with RETURNING; the new "
     "user has no membership yet, so the SELECT policy on `iam.users` rejects "
     "the returned row (`new row violates row-level security policy`).",
     "Skip the Team step; no in-app workaround."),
    ("Medium", "Business date lags the property's local date after midnight IST",
     "Between 00:00 and 05:30 IST the night-audit business date still shows "
     "the previous (UTC) day while check-in and service orders use the local "
     "date, so the first close posts no room charge for today's arrivals.",
     "Close the lagging day, then the current one."),
    ("Medium", "Floor names over ~10 characters are rejected",
     "The wizard derives the floor code from the name (\"Ground Floor\" -> "
     "`GROUND-FLOOR`, 12 chars) but `FloorIn.code` allows 10 -> HTTP 422.",
     "Use short names (\"Ground\", \"First\")."),
    ("Medium", "Advance by UPI/Card/Bank transfer cannot be taken at booking",
     "New Reservation's payment step has no reference field, but the server "
     "requires a reference for these methods.",
     "Take the advance in cash, or later from the folio."),
    ("Low", "First invoice is not a GST tax invoice",
     "The billing entity and GSTIN captured in onboarding print on the "
     "invoice, yet it is flagged 'Missing: Registered legal name' because "
     "onboarding does not fill Invoice Settings.",
     "Fill the legal name in Invoice Settings."),
    ("Fixed", "Room-service orders always failed with HTTP 500",
     "Order lines were posted with an empty business date. Fixed in "
     "`services/finance/finance_service/service_routes.py`.",
     "-"),
    ("Critical (security)", "Login tokens are unsigned",
     "A token is `base64('dev:<subject>')` with no signature or expiry, "
     "accepted in every environment. Anyone who knows a user's subject can act "
     "as them. The Keycloak/JWT validator exists but is not wired in.",
     "Wire `chirala_common.auth` (signed JWT) before production."),
]


def main() -> None:
    steps = json.load(open(os.path.join(OUT, "results.json")))
    iso_path = os.path.join(OUT, "isolation.json")
    iso = json.load(open(iso_path))["results"] if os.path.exists(iso_path) else []

    lines = ["# PMS end-to-end functional test report", ""]
    passed = sum(1 for s in steps if s["ok"])
    menus = [s for s in steps if s["step"].startswith("Menu ")]
    lines += [
        f"- **UI steps:** {passed}/{len(steps)} passed",
        f"- **Menu screens visited:** {sum(1 for m in menus if m['ok'])}/{len(menus)} "
        "(38 per tenant, both tenants)",
        f"- **Isolation checks:** {sum(1 for r in iso if r['ok'] is True)}/{len(iso)} passed",
        "",
        "## Steps", "", "| Tenant | Step | Result | Detail |", "|---|---|---|---|",
    ]
    for s in steps:
        detail = s["detail"].replace("|", "\\|") if s["detail"] else ""
        lines.append(f"| {s['tenant']} | {s['step']} | {'PASS' if s['ok'] else 'FAIL'} | {detail} |")
    if iso:
        lines += ["", "## Tenant isolation (API and database)", "",
                  "| Group | Check | Result | Detail |", "|---|---|---|---|"]
        for r in iso:
            res = "SKIP" if r["ok"] is None else ("PASS" if r["ok"] else "FAIL")
            lines.append(f"| {r['group']} | {r['name']} | {res} | {r['detail']} |")
    lines += ["", "## Defects found", "", "| Severity | Defect | Cause | Workaround |",
              "|---|---|---|---|"]
    for sev, title, cause, work in DEFECTS:
        lines.append(f"| {sev} | {title} | {cause} | {work} |")
    dest = os.path.join(OUT, "TEST_REPORT.md")
    open(dest, "w").write("\n".join(lines) + "\n")
    print(dest)


if __name__ == "__main__":
    main()
