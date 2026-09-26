# Channex PMS certification — runbook

How to run Channex's PMS certification tests from this PMS on the Channex
**staging** environment, and where to find each **task id** for the
certification form.

> **Check the exact values against Channex's page.** The scenario list below
> follows the published structure (full sync, rate / restriction /
> availability updates, bookings, rate limits, update logic). The specific
> dates, prices and occupancies each scenario asks for are on
> <https://docs.channex.io/api-v.1-documentation/pms-certification-tests>.
> Use theirs, not the examples here.

---

## 1. Before you start

| What | Where |
|---|---|
| Staging API key | Channex staging → user profile. Set `CHANNEX_API_KEY` on booking-core. |
| API base | `CHANNEX_API_URL=https://staging.channex.io/api/v1` (the default). |
| Webhook secret | Any long random string. Set `CHANNEX_WEBHOOK_SECRET`; provisioning registers it with the webhook. |
| Public HTTPS address | `APP_BASE_URL` must be a stable `https://` host (named Cloudflare tunnel, see `infra/cloudflared/README.md`). A quick tunnel changes hostname on restart and Channex would deliver to a dead address. |
| Sync settings | Defaults are right: `CHANNEL_PUSH_SECONDS=20` (batching window), `CHANNEL_SYNC_DAYS=500`, `CHANNEX_REQUESTS_PER_MINUTE=10`, `CHANNEL_FEED_SECONDS=300`. |

Restart booking-core after changing these.

### Test property

Channex's tests use a property with **two room types** and **two rate plans on
each** (typically a room-only / Best Available rate and a Bed & Breakfast
rate). Set that up in the PMS first:

1. **Property Setup → Room Types**: create the two room types Channex names,
   with the occupancies it specifies, and rooms for each (**Room Inventory**).
2. **Rates → Rate Plans**: create **one rate plan per room type per plan
   kind**, each tied to exactly one room type (a channel rate plan belongs to
   one room). For B&B, set the plan's adjustment (e.g. `+` fixed amount) so
   its price differs from room-only.
3. **Distribution → Channel Partners → Add partner**: add the OTA you will test
   (Booking.com test account or an Airbnb listing — the only live channels on
   staging) with its hotel/listing id. Adding it provisions the property at
   Channex: property, room types, rate plans, mappings and the webhook.
4. **Distribution → Sales Channels**: check *Property registered*, *Room
   mapping* and *Rates and availability* are green.

---

## 2. Where the task ids are

**Distribution → Sales Channels → Sync log.** Every request the PMS sends to
Channex is listed with what it carried (availability, or rates &
restrictions), why (Change / Full sync), its dates, Channex's answer, and the
**task id** with a copy button. Expand a row to see the exact JSON sent.

Changes go out on their own within ~20 seconds of saving; there is nothing to
press. Make the change, wait for the new row, copy its task id into the form.

---

## 3. Scenarios

| # | Channex test | Do this in the PMS | Expect in the Sync log |
|---|---|---|---|
| 1 | **Full sync** (500 days, all rooms and rates) | Sales Channels → **Full sync (500 days)** | 2 rows, *Full sync*: one *Availability*, one *Rates & restrictions*, each covering today → +499 days. Two task ids. |
| 2 | Single date, single rate | Rates → **Rates & Inventory**: change one room type's price on one date | 1 row, *Rates & restrictions*, 1 range, `rate` only |
| 3 | Single dates, multiple rates | Change prices for the dates/plans Channex lists, all within ~20 s | 1 row carrying every changed plan/date |
| 4 | Multiple dates, multiple rates | Rates & Inventory → select the date range → set price (or bulk update) | 1 row; each run of equal prices is one range |
| 5 | Min stay | Rates & Inventory cell → *Min stay* (or **Rates → Rate Rules** for ranges) | 1 row, `min_stay_arrival` + `min_stay_through` only |
| 6 | Stop sell | Rates & Inventory cell → *Stop sell* | 1 row, `stop_sell: true` only |
| 7 | Multiple restrictions (CTA, CTD, max stay, min stay) | **Rates → Rate Rules** → new rule with those settings for the dates, **Publish** | 1 row with those fields for the range |
| 8 | Half-year / long-range update | Rate Rules or bulk update over the months Channex specifies | 1 row (split only if over 400 ranges) |
| 9 | Single date availability | Reduce availability on a date: **Property Setup → Room Blocks** (block N rooms) or take a booking | 1 row, *Availability*, that date |
| 10 | Multiple date availability | Room Blocks across the date range | 1 row, *Availability*, one range per run of equal values |
| 11 | Booking receiving (new, modified, cancelled) | In Channex staging create a test booking on the connected channel, then modify it, then cancel it | Sales Channels → **Booking deliveries**: *Booked* → *Changed* → *Cancelled*, each linked to the reservation; each acknowledged to Channex |
| — | Rate limits | Nothing to do: the PMS keeps to 10 availability and 10 rate/restriction requests per minute per property; a 429 pauses that property for a minute and the change is resent afterwards (row shows *Rate limited*, then *Accepted*). | |
| — | Update logic | Nothing to do: only changed values are sent, collapsed into date ranges; nothing is resent on a timer; full sync only at go-live, on recovery, or on the button. | |

**Availability note.** Availability in this PMS is what is actually sellable
(rooms minus blocks, holds and bookings); it cannot be typed as an arbitrary
number above the physical room count. To reach a scenario's value, block rooms
(Room Blocks) or change the room count.

**Booking note.** A booking whose room is not mapped, or for which no room is
free, shows *Room not mapped* / *No room free* with a **Replay** button. It is
not acknowledged to Channex until it is placed, and the PMS also polls
Channex's revision feed every 5 minutes for anything the webhook missed.

---

## 4. Submitting

1. Fill the certification form (<https://forms.gle/xA8F3eSYBPBd8apYA>) with
   the task id for each scenario from the Sync log.
2. For live verification (screenshare or recording), use the Sales Channels
   page side by side with Rates & Inventory: every change you make appears in
   the Sync log within ~20 seconds with its payload and task id, which is
   exactly what Channex will be watching on their side.

## 5. Checking it without Channex

`e2e/fake_channex.py` is a local stand-in for the Channex API (with task ids,
the 10/minute limit and a booking feed). Against it:

```bash
uvicorn e2e.fake_channex:app --port 9100   # CHANNEX_API_URL=http://localhost:9100/api/v1, CHANNEX_API_KEY=fake-key
python e2e/channel_sync_check.py --creds creds_a.json        # ARI scenarios
python e2e/channel_integration_check.py --a creds_a.json --b creds_b.json   # partners, provisioning, bookings, isolation
```
