"""Record the 14 September defect sweep against the stories it touched.

Every story below was already Done. This does not change that -- each one was
built and verified as its note says -- but each also carried a defect found
afterwards, by reading the live screens against known figures rather than by
exercising the endpoints. The notes are appended, never replaced: what was
believed at sign-off is part of the record, and a note that quietly rewrites
itself is worth less than one that shows what was missed.

Status and Progress are left alone, except where a defect is still open. A
story with a known wrong number on screen is not Done, and saying otherwise in
a tracker is the same class of mistake as a report that ties against itself.

Run once. Appends are guarded by a marker so a second run is a no-op.
"""

from __future__ import annotations

from pathlib import Path
import shutil
import sys

import openpyxl

# Beside this script, not in the working directory: the tracker and the
# scripts that edit it live together in tools/tracker, and a relative
# name would quietly create or miss a copy wherever the script is run from.
FILE = str(Path(__file__).resolve().with_name("Chirala_Bay_Resort_PMS_Development_Tracker (2).xlsx"))
SHEET = "User Stories & Acceptance"
COL_ID, COL_STATUS, COL_PCT, COL_NOTES = 3, 24, 25, 30
MARKER = "[14 Sep 2026 defect sweep]"

#: Story ID -> (new status or None to leave, new progress or None, note to append)
UPDATES: dict[str, tuple[str | None, float | None, str]] = {
    "US-001-01": (
        "QA", 0.9,
        "DEFECT FOUND AND PARTLY FIXED. Arrivals and Departures were counting the "
        "booking, not the event. Departures matched departure_date = business date, "
        "so six guests who checked out of Lekhana on 13 Sep against booked "
        "departures of 15 Sep, 16 Sep and 14 Oct -- all early departures -- showed "
        "as ZERO departures on a day seven rooms emptied. Arrivals excluded "
        "checked_out, while the yesterday figure it was compared against included "
        "it, so a same-day guest vanished from today and still counted in the "
        "delta: 0 arrivals shown against a -2 trend computed on a different "
        "definition. Both now count the stay (booking.stays), and the local "
        "calendar day is resolved in the property timezone rather than UTC -- one "
        "checkout at 2026-09-12 18:34Z is 00:04 IST on the 13th and belonged to "
        "the 13th. Verified 11-14 Sep: 1/1, 2/1, 6/7, 0/0. "
        "STILL OPEN: Occupancy has the same asymmetry and is NOT fixed. Today's "
        "query is status='checked_in'; the previous-day query is "
        "status IN ('checked_in','checked_out'). The same date reads 0% as today "
        "and 75% as yesterday, and every historical date reads 0% because every "
        "unit is now checked out. Status moved to QA for this reason."),
    "US-001-02": (
        None, None,
        "Room Status vs Stayview disagree on the same house at the same moment: "
        "the dashboard reports 4 Available and 8 Cleaning, the rack reports 12 "
        "Vacant and 0 Dirty. Both are internally defensible -- the dashboard "
        "excludes rooms being cleaned from availability, the rack counts only "
        "hk_status='dirty' and Lekhana's eight rooms are 'cleaning' -- and "
        "together they are contradictory. NOT FIXED: which one is right is a "
        "product decision about whether a room mid-clean is sellable."),
    "US-007-01": (
        None, None,
        "DEFECT FOUND AND FIXED. The folio summary split entries by entry_type "
        "alone: every debit a charge, every credit a payment. A refund is a "
        "debit, so handing money back RAISED the bill. FOL-1008 had 9,000 "
        "collected and all 9,000 returned and read Subtotal 9,000, Grand Total "
        "9,000, Advance Paid 9,000 -- a fully refunded account presented as a "
        "fully paid one. The balance was right throughout, which is why it "
        "survived review: 9,000 - 9,000 is zero whether or not either number "
        "means anything. Charges now exclude refund and deposit_refund; advance "
        "is credits less those refunds. Verified: FOL-1008 and FOL-1024 to "
        "0/0/0, FOL-1009 to 4,000/4,000/0, FOL-1001 unchanged at "
        "16,000/16,000/0."),
    "US-036-02": (
        None, None,
        "DEFECT FOUND AND FIXED. The close-shift dialog computed expected cash as "
        "float + taken, while the server files float + taken - refunded. A drawer "
        "that had handed cash back was told it balanced and then filed a surplus "
        "of exactly the refunds: Lekhana counted 0 against a previewed "
        "expectation of 0 and recorded a 15,000 overage. The refunds line is now "
        "shown (it moves the total, so it belongs in the list that adds up to "
        "it), and a negative expectation is called out before the count is filed "
        "-- a till cannot hold less than nothing, so it always means the opening "
        "float was understated. Warned, not blocked: a cashier facing a genuinely "
        "odd drawer still has to record what they found. NOT CORRECTED IN DATA: "
        "two shifts closed with phantom variances (+15,000 on 14 Sep, +29,000 on "
        "13 Sep) and there is no endpoint to annotate a closed count, by design."),
    "US-036-03": (
        None, None,
        "Cross-tenant probe: 12 finance endpoints called with one tenant's "
        "credentials asking for another's property -- all refused 403. Cash "
        "refund against a closed or absent drawer refused 409. Two knock-ons "
        "found and fixed elsewhere: _open_shift_of resolves the drawer from the "
        "CALLER's user, so a service credential (user_id NULL) posts a cash "
        "refund into no drawer at all; and finance.refunds was not written by the "
        "deposit refund route, so the same payment stayed refundable twice."),
    "US-114-02": (
        None, None,
        "DEFECT FOUND AND FIXED. An adjustment credited amount + tax_amount, "
        "which is right for exclusive tax -- charge and its separate tax debit "
        "both reverse -- and wrong for inclusive, where the tax is already inside "
        "the amount. A 1,050 charge containing 50 of tax produced a 1,100 credit: "
        "the guest 50 up and the folio reconciling to nothing. Root cause was "
        "deeper than the formula: folio_entry_taxes recorded the amount but not "
        "the treatment, so nothing downstream could tell the two apart. Migration "
        "0026_tax_apply_as adds apply_as with a CHECK matching tax_rules; TaxLine "
        "carries it; both adjustment lookups now sum only the exclusive share. NO "
        "BACKFILL NEEDED HERE -- this deployment has zero tax rules and zero tax "
        "lines, so nothing historical is affected; a deployment with history "
        "should decide deliberately rather than inherit the 'exclusive' default."),
    "US-115-02": (
        None, None,
        "DEFECT FOUND AND FIXED (high). Repeated refunds debited the wrong folio. "
        "The allocation loop read each allocation at its ORIGINAL size with no "
        "memory of earlier refunds, so a second refund restarted at the first "
        "folio: a 1,000 payment split 600/400 and refunded in two halves of 500 "
        "debited the first folio 1,000 -- from a folio that only ever received "
        "600 -- while the second kept 400 it should have given up. The payment's "
        "own arithmetic still balanced, which is why it could happen quietly. "
        "Each allocation is now offset by the refund debits already posted "
        "against that folio for that payment, and an allocation already fully "
        "refunded is skipped rather than producing a zero entry. Also on this "
        "screen: 'Outstanding Balance' described the FOLIO while sitting under "
        "the payment's own figures, sending a reader to the wrong payment; it now "
        "names the folio. 'Total Paid' showed the refundable remainder, reading "
        "0.00 on a payment the guest did make; relabelled 'Still held'."),
    "US-118-02": (
        None, None,
        "DEFECT FOUND AND FIXED (high). tax_rules.amount_basis exists, is "
        "CHECK-constrained to per_night/per_person/per_stay/per_unit, and is "
        "SELECTed by resolve_rules -- and the engine then multiplied every flat "
        "levy by whatever single 'units' the caller passed. A 100 per-STAY levy "
        "on three nights came out at 300 and a per-PERSON levy was charged per "
        "night. compute_tax now asks each rule what it is charged per and takes "
        "the matching quantity; callers pass the quantities they know rather "
        "than pre-deciding which one matters. Verified at 3 nights / 2 guests: "
        "per_stay 100, per_night 300, per_person 200, per_unit 300, percentage "
        "unaffected. Unknown occupancy charges for one -- under-charging is "
        "visible on the bill, over-charging becomes a refund. UNEXERCISED END TO "
        "END: no tax rules are configured on this deployment, so this is verified "
        "in the engine and not against a posted bill."),
    "US-058-02": (
        None, None,
        "DEFECT FOUND AND FIXED (high). The deposit refund route posted a folio "
        "debit and a negative deposit_allocation and never wrote finance.refunds "
        "-- the table that decides how much of a payment is still refundable. The "
        "same money could therefore be given back a second time through /refunds, "
        "which would pass its own check happily. Now recorded, with the caller's "
        "open drawer attached when they have one. RESIDUAL, STATED DELIBERATELY: "
        "this route does not REQUIRE an open drawer the way /refunds does, "
        "because it never has and refusing now would stop a working flow -- so a "
        "cash deposit refund can still leave a till with no shift recording it. "
        "Two knock-ons fixed: 'deposit_refund' is a different source_type from "
        "'refund', so it was escaping both the ledger-scope classification and "
        "folio_money's net-of-refunds calculation."),
}


def main() -> int:
    shutil.copyfile(FILE, FILE.replace(".xlsx", ".bak.20260914.xlsx"))
    wb = openpyxl.load_workbook(FILE)
    ws = wb[SHEET]

    seen, skipped = 0, 0
    for row in range(5, ws.max_row + 1):
        sid = ws.cell(row=row, column=COL_ID).value
        if sid not in UPDATES:
            continue
        status, pct, note = UPDATES[sid]
        cell = ws.cell(row=row, column=COL_NOTES)
        existing = str(cell.value or "")
        if MARKER in existing:
            skipped += 1
            continue
        cell.value = f"{existing}\n\n{MARKER} {note}".strip()
        if status is not None:
            ws.cell(row=row, column=COL_STATUS).value = status
        if pct is not None:
            ws.cell(row=row, column=COL_PCT).value = pct
        seen += 1
        print(f"  {sid}: note appended"
              + (f", status -> {status} ({pct})" if status else ""))

    if seen:
        wb.save(FILE)
    print(f"\n{seen} stories updated, {skipped} already carried the marker")
    missing = set(UPDATES) - {
        ws.cell(row=r, column=COL_ID).value for r in range(5, ws.max_row + 1)
    }
    if missing:
        print("NOT FOUND in the sheet:", ", ".join(sorted(missing)))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
