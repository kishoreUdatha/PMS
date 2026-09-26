# Channex PMS certification — runbook

How to pass Channex's PMS certification tests
(<https://docs.channex.io/api-v.1-documentation/pms-certification-tests>) from
this PMS on Channex **staging**, and where to find each **task id** for the
form (<https://forms.gle/xA8F3eSYBPBd8apYA>).

Every test is done **by a person in the PMS screens**. Nothing here calls
Channex directly: the PMS notices each change as it is saved, queues it, and
sends it by itself. Channex explicitly rejects scripts, Postman collections
or special "certification" screens.

---

## 1. How the PMS sends changes (what Channex will check)

| Channex requirement | How the PMS meets it |
|---|---|
| Detect ARI changes as they happen, not by polling the database | Database triggers on inventory, room calendar, rate-plan calendar, rate rules, rate plans and room types write to an outbox (`distribution.ari_outbox`) **in the same transaction** as the change. |
| Queue/batch changes, respect 20 ARI calls/min | A worker picks up a property's queued changes once it has been quiet for 20 s (at most 60 s after the first). It sends **only values that differ** from what Channex last accepted, folded into date ranges, as one `/restrictions` request (rates + restrictions) and/or one `/availability` request. Max 10 per endpoint per minute per property. |
| Retry/backoff for 429 and 5xx | 429 → the property is paused for 60 s, then resent. 5xx/network → retried after 5·2ⁿ s (max 5 min). A queued change is deleted only once Channex accepts it. |
| No full sync on a timer | A full sync (500 days, 2 requests) runs only on first connection, on the **Full sync** button, or when the PMS has no record of what Channex holds. Never scheduled. |
| Task ids | Every request and Channex's task id are stored and shown in **Distribution → Sales Channels → Sync log** (copy button, expandable JSON). |

**Two ways to make "1 call" tests go out as one call:**

1. **Recommended:** use Rates & Inventory → select the rate plan → **Edit a date
   range**. Enter the first line, press **Add another**, and so on for each line.
   **Save all** writes them in one transaction, which becomes one request.
2. Or edit cells one after another, less than 20 s apart. They are sent together
   once you stop.

---

## 2. Set up the test property

Channex asks for a property named **"Test Property - (Provider Name)"**, in
**USD**, with two room types and two rate plans on each.

1. **Property.** Create or rename the property as *Test Property - Chirala PMS*
   with currency **USD**. The Channex property takes the PMS property's
   name, currency and timezone.
2. **Property Setup → Room Types.**
   - **Twin Room**: occupancy 2, base rate **100**, with **10 rooms** (Room Inventory).
   - **Double Room**: occupancy 2, base rate **100**, with **5 rooms**.

   10 and 5 leave room for the availability tests (block rooms to reach each value).
3. **Rates → Rate Plans.** Create four plans, each tied to exactly one room
   type, **named exactly as Channex lists them**. The same name is allowed on
   both rooms; only the code must differ.

   | Name | Code (any) | Room type | Price |
   |---|---|---|---|
   | Best Available Rate | TW-BAR | Twin Room | no adjustment (100) |
   | Bed & Breakfast Rate | TW-BB | Twin Room | +20 fixed amount (120) |
   | Best Available Rate | DB-BAR | Double Room | no adjustment (100) |
   | Bed & Breakfast Rate | DB-BB | Double Room | +20 fixed amount (120) |

4. **Distribution → Channel Partners → Add partner.** Add Booking.com (a test
   hotel id from Channex) or Airbnb; these are the only live channels on staging.
   Adding it creates the property, room types, rate plans and webhook at
   Channex, then runs the first full sync.
5. **Distribution → Sales Channels.** Check that *Property registered*, *Room
   mapping* and *Rates and availability* are green.

### "Use our API to fetch IDs … set up mapping"

The PMS does this itself when it provisions:

- It reads Channex's Properties, Room Types and Rate Plans lists
  (`GET /properties`, `/room_types?filter[property_id]=…`,
  `/rate_plans?filter[property_id]=…`).
- It adopts what already exists and creates only what is missing.
- It stores each Channex id against the PMS entity: one channel room per PMS
  room type, and one channel rate plan per PMS rate plan.
- Rate plans are matched by **room and name**, so the two "Best Available Rate"
  plans map to the Twin's and the Double's plans respectively.

**Where to see the ids:**

- **Property id:** Channel Partners → edit partner, under the channel manager
  property.
- **Room and rate mapping:** the same page's mapping section. Dropdowns list
  Channex's room types and rate plans, fetched from its API. Use it to check
  or change any pair.
- **Every id Channex is sent:** Sales Channels → Sync log. Expand a row to see
  `property_id`, `room_type_id` and `rate_plan_id` on each value.

If the test property was created in Channex by hand first, give it the same
names. Running provisioning (the **Full sync** button, or re-saving the
partner) then adopts it instead of creating a second one.

Environment (booking-core): `CHANNEX_API_URL=https://staging.channex.io/api/v1`,
`CHANNEX_API_KEY`, `CHANNEX_WEBHOOK_SECRET` (a long random string), and
`APP_BASE_URL` on a stable `https://` host (named Cloudflare tunnel,
`infra/cloudflared/README.md`) so webhooks reach the PMS.

> **Price tests: edit the plan, not the room type.** Changing a room type's
> price moves both of its plans (B&B follows BAR +20), which puts extra values
> in the request. Always pick the plan in the **rate plan** dropdown on Rates &
> Inventory first. The grid then edits that plan alone.

---

## 3. The tests

For every test, open **Sales Channels → Sync log** in a second tab. The new row
appears about 20 s after saving; copy its task id into the form.

| # | Channex asks for | Do this in the PMS | Expected in the Sync log |
|---|---|---|---|
| 1 | Full sync, 500 days | Sales Channels → **Full sync (500 days)** | 2 rows *Full sync*: Availability + Rates & restrictions. 2 task ids. |
| 2 | Twin BAR, 22 Nov 2026 → 333 | Rates & Inventory, plan **Twin BAR**, dates around 22 Nov → type 333 in the Price cell | 1 row, 1 range, `rate` only |
| 3 | Twin BAR 21 Nov 333 · Double BAR 25 Nov 444 · Double B&B 29 Nov 456.23 (1 call) | **Edit a date range**: one line per item (same From/To), **Add another** between them, then **Save all** | 1 row, 3 values |
| 4 | Twin BAR 1–10 Nov 241 · Double BAR 10–16 Nov 312.66 · Double B&B 1–20 Nov 111 | Range panel, 3 lines, **Save all** | 1 row, 3 ranges |
| 5 | Min stay: Twin BAR 23 Nov 3 · Double BAR 25 Nov 2 · Double B&B 15 Nov 5 | Range panel, *Min stay* field, 3 lines, **Save all** | 1 row, `min_stay_arrival` + `min_stay_through` |
| 6 | Stop sell: Twin BAR 14 Nov · Double BAR 16 Nov · Double B&B 20 Nov | Range panel, *Stop sell* = Yes, 3 lines, **Save all** | 1 row, `stop_sell: true` ×3 |
| 7 | Twin BAR 1–10 Nov CTA, max 4, min 1 · Twin B&B 12–16 Nov CTD, min 6 · Double BAR 10–16 Nov CTA, min 2 · Double B&B 1–20 Nov min 10 | Range panel, 4 lines (CTA/CTD Yes; stays in the fields), **Save all** | 1 row, 4 ranges |
| 8 | Twin BAR 1 Dec 2026–1 May 2027 rate 432, min 2 · Double BAR same range 342, min 3 | Range panel, 2 lines, **Save all** | 1 row, 2 ranges |
| 9 | Availability Twin 8 / Double 1, then a booking brings them to 7 / 0 | **Block / Out of Order** (`/rooms/blocks`): one block of 2 Twin rooms and one of 4 Double rooms for the night, less than 20 s apart. Then take one booking with a Twin and a Double for that night (Reservations → New, or Booking CRS). | 1 row for the blocks, then 1 row for the booking (7 / 0) |
| 10 | Twin 10–16 Nov = 3 · Double 17–24 Nov = 4 | Block / Out of Order: tick 7 Twin rooms for 10–16 Nov and save, then 1 Double room for 17–24 Nov and save, less than 20 s apart | 1 row, *Availability*, 2 ranges |
| 11 | Booking new / modify / cancel | In Channex staging, use **Booking CRS** (or the Booking.com test account) to create, then modify, then cancel a booking | Sales Channels → **Booking deliveries**: *Booked* → *Changed* → *Cancelled*, each linked to the reservation. Screenshot this and the reservation; give its OTA code / Channex booking id. |
| 12 | Rate limits | Nothing to do; see §1 | |
| 13 | Update logic | Nothing to do; see §1 | |

Notes:
- **Only changes are sent.** In tests 7 and 8, "CTA false" or "min stay 1"
  is already the value on those nights, so it is correctly *not* sent.
  That is the update logic Channex asks for.
- **Values are ranges.** Nights with equal values become one range, so test 4
  is three ranges, not 37 values.
- **Resetting.** To hand nights back to the defaults, tick *Reset* (price and
  stays) or choose *Reset* (CTA / CTD / stop sell) in the range panel. The
  reverted values go out as a change.
- **Availability is real.** It is rooms minus blocks, holds and bookings and
  cannot be typed in, so tests 9 and 10 use blocks. One block can cover many rooms (tick them all) and is one save. The block screen is at `/rooms/blocks`, reached from a room's **More → Block / Out of Order**.
- **Missed webhooks.** A delivery that could not be placed (room not mapped,
  none free) shows **Replay** and is not acknowledged to Channex until placed.
  The PMS also reads Channex's booking feed every 5 minutes.

---

## 4. Test 14 / form answers ("extra notes")

| Question | Answer |
|---|---|
| Min stay: arrival or through? | The PMS has one minimum stay per night. It is sent as both `min_stay_arrival` and `min_stay_through`. |
| Restrictions supported | Rate, min stay, max stay, closed to arrival, closed to departure, stop sell, availability. |
| Multiple room types / rate plans | Yes: any number of room types, and any number of rate plans per room type (each channel rate plan belongs to one room type). |
| Update logic | Changes are detected by database triggers into an outbox and sent within ~20 s, only the changed values, collapsed into ranges. A full sync runs only on connection, on demand, or on recovery, never on a timer. |
| Rate limits | At most 10 availability and 10 restriction requests per minute per property. 429 → 60 s pause then resend; 5xx → exponential backoff. |
| Bookings | Webhook (secret header) → fetch `booking_revisions/{id}` → create, modify or cancel the reservation → acknowledge. The `booking_revisions/feed` is read every 5 min as a safety net. |
| Credit card details | Not required: the PMS does not receive or store card data and is **not PCI DSS certified**. *(Confirm before submitting.)* |

---

## 5. Checking without Channex

`e2e/fake_channex.py` is a local stand-in for the Channex API, with task ids,
the per-minute limit and a booking feed. These checks use the PMS's own
API and **their own random values**, not Channex's test tables:

```bash
uvicorn e2e.fake_channex:app --port 9100   # CHANNEX_API_URL=http://localhost:9100/api/v1, CHANNEX_API_KEY=fake-key
python e2e/channel_sync_check.py --creds creds_a.json        # ARI: 11 scenarios
python e2e/channel_integration_check.py --a creds_a.json --b creds_b.json   # partners, provisioning, bookings, isolation: 31
```
