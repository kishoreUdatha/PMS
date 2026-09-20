# Moving to a production Channex account

Everything in this system is environment-agnostic. Swapping accounts is one
environment variable and a restart; provisioning notices that every stored
identifier has vanished and rebuilds from scratch on its own.

    python scripts/swap_channex_account.py --key <NEW_KEY> --url <API_URL>

Run it with `--check-only` first. It refuses to proceed on the one failure
that costs money rather than time — see **The webhook address** below.

---

## Before you start

### 1. The webhook address — the real blocker

`APP_BASE_URL` is currently a Cloudflare **quick tunnel**:

    https://supplier-cos-completed-pole.trycloudflare.com

A quick tunnel gets a **new hostname every time it restarts**. That is fine
for a rehearsal and unacceptable for an account taking real bookings, because
the webhook registered at Channex would point at a host that no longer
exists. Nothing would error. Channex retries a delivery eleven times over
about twenty-four hours and then stops, and the first symptom is a guest
arriving for a booking nobody in the hotel has heard of.

Before production you need a stable `https://` hostname:

* a domain pointed at this deployment, with a **named** Cloudflare tunnel
  (`infra/cloudflared/README.md`), or
* whatever the real deployment's public address ends up being.

The swap script fails on an ephemeral hostname unless you pass
`--allow-ephemeral-webhook`, which is there for rehearsals only.

Whenever that address changes, provisioning re-registers the webhook, so
running the sync afterwards is enough — nothing has to be done at Channex by
hand.

### 2. The production API base

Staging is `https://staging.channex.io/api/v1`. Production is a different
host; both `https://app.channex.io/api/v1` and
`https://secure.channex.io/api/v1` answer, so use whichever your production
key is issued against — it will come with the account. Pass it as `--url`.

### 3. Things that do *not* change

| Setting | Why |
| --- | --- |
| `CHANNEX_WEBHOOK_SECRET` | Ours, not theirs. We choose it and ask Channex to send it back; it is the whole of the check on an inbound webhook, because Channex does not sign payloads. |
| Every OTA property id | `channel_connections.ota_hotel_id` is the hotel's id with the OTA. It has nothing to do with which Channex account we use. Agoda's `96019126` survives the swap and the channel is rebuilt from it. |
| Rooms, rate plans, prices | PMS data. Untouched. |

---

## What happens during the swap

1. **The key is validated** against the new account before anything changes.
2. `.env` is updated and `booking-core` restarts.
3. Every property with an OTA connection is provisioned.

Step 3 is where the interesting part happens, and it needs no manual cleanup:

* The stored **group** id belongs to the old account. `_group_for_org` asks
  for it, gets a 404, and creates the tenant's group again in the new one.
* The stored **property** id is likewise gone. `_is_gone` sees the 404 and
  `_forget_external` drops it *along with the room and rate mappings* — they
  pair our rooms with ids at an account we no longer use, and leaving them
  would make the next run believe the work was already done.
* The property, its room types and its rate plans are recreated, mapped, and
  the webhook re-registered.
* Any connection carrying an `ota_hotel_id` gets its **OTA channel built**.

Only a definite 404 counts as "gone". A timeout, a 500 or an expired key are
treated as "could not tell" and change nothing — otherwise a bad minute at
Channex would throw away a working property's mappings.

---

## After the swap

Check, in this order:

1. **The sync ran.** Sales Channels → *What is actually live*. Property
   created, rooms mapped, rates mapped.
2. **Prices are right.** The rate that reaches an OTA is the room's rate with
   the plan's adjustment applied — Advance Purchase at −10%, Bed & Breakfast
   at +₹500. Read them back rather than trusting the push's own "ok".
3. **The OTA channel exists but is switched off.** Channels are created
   inactive on purpose: activating puts rooms on sale to the public, and that
   should be a deliberate act after the mapping has been checked.
4. **Agoda's own side.** The hotel must have selected Channex under
   *YCS → Settings → Property Settings → Optional Settings → enable channel
   manager mode*, and **saved** it. Lekhana Resort (Agoda ID 96019126) has
   this set.
5. **Map Agoda's occupancies.** Agoda mapping is always multi-occupancy. This
   PMS prices per room, so the same rate plan is mapped to every occupancy —
   which Agoda's own guide allows. Be aware that means one price regardless
   of party size.

---

## If a booking does not arrive

The four links, in the order they break:

1. Is the property at Channex? — Sales Channels shows the id.
2. Are the rooms mapped? — an unmapped room makes its bookings `unmapped`
   rather than lost; they sit in `distribution.channel_booking_events`.
3. Is the OTA channel **switched on** at Channex? Created ≠ active.
4. Is the webhook pointing at a host that still exists? This is the one that
   fails silently, and the one a quick tunnel guarantees will fail.
