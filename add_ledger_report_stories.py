"""Add the Hotel Ledger Report to the tracker as SCR-131 / US-131-01..03.

The screen is built, live at /reports and /reports/ledger, and had no row in
the sheet -- so a substantial piece of finance reporting was signed off
nowhere. The nearest existing row, US-027 Reports Dashboard, is a different
screen: a dashboard OF reports, still Backlog, whose mockup (027) is not this.
What happened is that the ledger report took the /reports path because no
Reports Dashboard exists yet, which is worth recording against US-027 too
rather than leaving the route looking accounted for.

Statuses are written honestly rather than optimistically. -01 and -02 are Done
and were verified against live Lekhana data today. -03 is QA, not Done: the
tenancy and permission work is finished and probed, but the screen has never
been exercised with a second tenant's data present in the same window, and
saying Done would be the same class of claim as a report that ties against
itself.
"""

from __future__ import annotations

import shutil
import sys

import openpyxl

FILE = "Chirala_Bay_Resort_PMS_Development_Tracker (2).xlsx"
SHEET = "User Stories & Acceptance"

COMMON = {
    5: "Finance",                      # Module
    6: "Revenue Reporting",            # Submodule
    7: "SCR-131",                      # Screen ID
    8: "Hotel Ledger Report",          # Screen Name
    4: "EPIC-16",                      # Epic ID
    10: "Finance Manager",             # Persona
    17: "All revenue modules",         # Dependencies
    19: "Must Have",
    20: "MVP",
    22: "S05-S07",
    25: "None",                        # Assignee
}

ROWS = [
    dict(
        sid="US-131-01", seq=391, points=5, type_="View / Search",
        title="View and find information in Hotel Ledger Report",
        story=("As a Finance Manager, I want to open the Hotel Ledger Report and "
               "find the balances behind any window of business dates using "
               "filters, tabs and search"),
        value=("Gives one reconcilable statement of what the property is owed and "
               "what it holds, replacing per-folio arithmetic at month end."),
        pre=("Authenticated user; active property; reports.view permission; folio "
             "entries posted for the window."),
        ac=("Given I am authenticated with reports.view on the selected property, "
            "when I open the Hotel Ledger Report, then the summary shows Opening, "
            "Debit, Credit and Closing per ledger (Guest, Deposit, City/AR, "
            "Package) plus a Hotel Balance total, and the tie check states "
            "whether closing = opening + debits - credits; and the default window "
            "ends on the property's OPEN BUSINESS DATE, not the server's calendar "
            "date, so entries posted into the open day are visible."),
        neg=("A ledger nothing writes to is greyed with the reason rather than "
             "shown as a row of zeroes; an empty account list distinguishes 'all "
             "settled' from 'never used' and names any other ledger still open; a "
             "window with no movement still shows opening balances."),
        api=("GET /api/v1/reports/ledger (property_id, date_from, date_to, "
             "ledger_type, q, confirmation_no, folio_no, room, transaction_code, "
             "payment_method, market, limit); reads finance.folio_entries, "
             "finance.folios, booking.reservations, engagement.guests."),
        qa="TC-131-A",
        status="Done", pct=1,
        notes=(
            "BUILT AND VERIFIED 13-14 Sep 2026, live at /reports/ledger (and at "
            "/reports, which currently has no Reports Dashboard behind it). "
            "Summary grid per ledger with a tinted Hotel Balance row and a tie "
            "panel; five tabs (four ledgers + Transactions) with counts; "
            "ten-field filter panel with Run Report / Reset; paginated account "
            "table; guest detail drawer; CSV export. Ledger availability is "
            "measured from the data over all time, not hard-coded -- the City/AR "
            "and Package tabs were previously greyed permanently and would have "
            "stayed grey after a tenant created a commercial account. "
            "DEFECTS FOUND AND FIXED IN THIS SCREEN: (1) the default window ended "
            "on date.today() in UTC, which after 18:30 is already tomorrow in "
            "Chirala and, once the business date ran ahead of the calendar, hid "
            "every entry posted into the open day -- a hotel balance of -59,224 "
            "was shown for money already refunded; it now ends on the open "
            "business date. (2) Opening balances ignored every filter while the "
            "movements applied them all, so filtering to one reservation showed "
            "its movements on top of the whole property's opening -- "
            "CBRB7192D71 read -50,224 against a folio at zero, and 'Figures tie' "
            "stayed green because the check compares four numbers drawn from the "
            "same mismatched pair. (3) The Transactions balance column was one "
            "accumulator running down the whole list, adding one guest's account "
            "to the next; it is now that folio's balance, computed over the "
            "folio's whole history so filtering or paging changes which rows you "
            "see, never what they say. (4) The tab badge showed movement count "
            "when unselected and outstanding count once clicked -- 18 became 0 "
            "on click; both now show outstanding accounts."),
    ),
    dict(
        sid="US-131-02", seq=392, points=5, type_="Primary Workflow",
        title="Complete the primary Hotel Ledger Report workflow",
        story=("As a Finance Manager, I want to narrow the ledger to a guest, "
               "folio, room, payment method or date range and read the resulting "
               "balances and transactions, then export them"),
        value=("Turns a month-end reconciliation from per-folio arithmetic into a "
               "filtered statement that can be tied to the summary above it."),
        pre="View access confirmed; folio entries exist for the chosen window.",
        ac=("Given a filter is applied, when the report is run, then opening, "
            "movements, closing, the account list and the transaction journal all "
            "describe the SAME population; and the journal footer totals Debit "
            "and Credit so the rows can be tied to the Hotel Balance line above "
            "without adding them by hand."),
        neg=("A truncated journal states that its total is partial rather than "
             "presenting a partial sum as the total; no total is shown under "
             "Folio Balance, because those figures belong to different accounts "
             "and summing them produces a number that is nobody's; a deposit "
             "refund is classified into the ledger it came from, not the guest "
             "ledger."),
        api=("GET /api/v1/reports/ledger with filter parameters; CSV export is "
             "client-side from the same payload, so the file and the screen "
             "cannot disagree."),
        qa="TC-131-B",
        status="Done", pct=1,
        notes=(
            "Verified on live Lekhana data after the 14 Sep fixes: unfiltered, "
            "Guest 105,224/105,224/0, Deposit 15,000/15,000/0, Hotel Balance "
            "120,224/120,224/0, ties true, 54 transactions; filtered to "
            "CBRB7192D71 on 14 Sep, Guest opening -4,000 debit 4,000 closing 0 "
            "and Deposit opening -5,000 debit 5,000 closing 0, hotel closing 0, "
            "matching that reservation's folio exactly. "
            "LEDGER SCOPES NOW PARTITION rather than overlap: package is a "
            "property of the reservation, not the folio, so a guest folio on a "
            "package satisfied both the guest and package scopes and Hotel "
            "Balance -- their plain sum -- counted it twice; a 1,000 package "
            "charge produced a 2,000 hotel total and the tie check could not "
            "detect it. Precedence is now deposit, city, package, guest, each "
            "excluding the ones above; verified that all 65 live entries classify "
            "to exactly one scope, none to two, none to none. Deposit refunds "
            "classify back to the deposit ledger (matched through the refund to "
            "the payment to that payment's own entry, per entry rather than per "
            "folio, because one folio held both a card refund and a deposit "
            "refund on the same day). "
            "NOT BUILT, deliberately: Save Filter from the mockup needs a "
            "saved-views table that does not exist, and a dead control would be "
            "worse than none."),
    ),
    dict(
        sid="US-131-03", seq=393, points=3, type_="Security / Exceptions",
        title="Control permissions, overrides and exceptions in Hotel Ledger Report",
        story=("As a department supervisor, I want the ledger report to refuse "
               "callers without reporting rights and to refuse any request for "
               "another tenant's figures"),
        value=("A ledger is the whole property's financial position, including "
               "what other guests still owe; it is not a screen every role should "
               "open."),
        pre="Roles, permissions and tenant scoping configured.",
        ac=("Given a user lacks reports.view, when they request the ledger, then "
            "it is refused 403 and no figures are returned; and given a caller "
            "names a property outside their organisation, then it is refused 403 "
            "whether the tenant is supplied as a parameter or implied by a "
            "credential."),
        neg=("Cross-tenant reads refused rather than returned empty; a service "
             "credential is held to the organisation it names; malformed dates "
             "are rejected by validation rather than silently defaulted."),
        api=("require_permission('reports','view') + assert_property_in_org on "
             "GET /api/v1/reports/ledger; guard_request_tenancy runs inside the "
             "permission dependency."),
        qa="TC-131-C",
        status="QA", pct=0.9,
        notes=(
            "RBAC: iam 0022_reports_grants had to be written before this screen "
            "worked at all -- all seven 'reports' permission rows had existed "
            "since the catalogue was seeded and were granted to NO role, "
            "including the role literally named Reports, so the report 403'd for "
            "every user in every tenant the first time it was opened. Granted to "
            "Reports and Resort Manager (view/export) and to Administrator and "
            "Property IT Administrator (all). Front Desk and Reservations are "
            "deliberately absent: a ledger is the property's whole position, not "
            "one guest's bill. "
            "TENANCY: verified refused 403 cross-tenant. The guard now runs "
            "inside the permission dependency rather than as a line each route "
            "must remember -- across the three services 52 handlers accepted a "
            "tenant and never checked it, including this one's neighbours. "
            "WHY QA AND NOT DONE: the report has never been exercised with a "
            "second tenant's entries present in the same date window. Isolation "
            "is proved at the endpoint (403) and by construction (property-scoped "
            "predicates), but not by reading a mixed ledger and confirming the "
            "other tenant's figures are absent from the totals. Until that is "
            "done this is verified, not demonstrated."),
    ),
]


def main() -> int:
    wb = openpyxl.load_workbook(FILE)
    ws = wb[SHEET]

    existing = {ws.cell(row=r, column=3).value for r in range(5, ws.max_row + 1)}
    if any(d["sid"] in existing for d in ROWS):
        print("US-131 stories already present; nothing written")
        return 0

    shutil.copyfile(FILE, FILE.replace(".xlsx", ".bak.20260914e.xlsx"))
    src = next(r for r in range(5, ws.max_row + 1)
               if ws.cell(row=r, column=3).value == "US-115-01")
    at = ws.max_row + 1

    for i, d in enumerate(ROWS):
        row = at + i
        # Carry the boilerplate columns from a finance story of the same shape.
        for col in (16, 27, 28):                       # rules, DoR, DoD
            ws.cell(row=row, column=col).value = ws.cell(row=src, column=col).value
        for col, val in COMMON.items():
            ws.cell(row=row, column=col + 1).value = val
        ws.cell(row=row, column=1).value = 1           # Priority Rank
        ws.cell(row=row, column=2).value = d["seq"]
        ws.cell(row=row, column=3).value = d["sid"]
        ws.cell(row=row, column=4).value = d["title"]
        ws.cell(row=row, column=10).value = d["type_"]
        ws.cell(row=row, column=12).value = d["story"]
        ws.cell(row=row, column=13).value = d["value"]
        ws.cell(row=row, column=14).value = d["pre"]
        ws.cell(row=row, column=15).value = d["ac"]
        ws.cell(row=row, column=16).value = d["neg"]
        ws.cell(row=row, column=19).value = d["api"]
        ws.cell(row=row, column=22).value = d["points"]
        ws.cell(row=row, column=24).value = d["status"]
        ws.cell(row=row, column=25).value = d["pct"]
        ws.cell(row=row, column=27).value = d["qa"]
        ws.cell(row=row, column=30).value = d["notes"]
        print(f"  {d['sid']:<12} {d['status']:<5} row {row}  {d['title'][:52]}")

    # And say, on the story that owns /reports, that it is not what is there.
    for r in range(5, ws.max_row + 1):
        if ws.cell(row=r, column=3).value == "US-027-01":
            c = ws.cell(row=r, column=30)
            note = ("[14 Sep 2026] Still Backlog and still unbuilt. NOTE: the "
                    "/reports route currently renders the Hotel Ledger Report "
                    "(SCR-131, US-131-01..03), so the path looks accounted for "
                    "while this screen -- a dashboard OF reports, mockup 027 -- "
                    "does not exist. Do not read a working /reports as this "
                    "story being delivered.")
            if note[:18] not in str(c.value or ""):
                c.value = (f"{c.value}\n\n{note}" if c.value and str(c.value) != "None"
                           else note)
                print("  US-027-01   note added (route is occupied by SCR-131)")
            break

    wb.save(FILE)
    print(f"\n3 stories written at rows {at}-{at + 2}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
