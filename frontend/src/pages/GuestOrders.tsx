import { useMemo, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  BedDouble, Loader2, Minus, Plus, Search, ShoppingBag, Trash2, UtensilsCrossed,
} from 'lucide-react'
import { Link } from 'react-router-dom'
import { Crumbs } from '../components/Crumbs'
import { notify } from '../components/AskDialog'
import { CONTROL, FILTER_BOX_ICON } from '../lib/controls'
import { fmtDate } from '../lib/dates'
import { useActivePropertyId } from '../hooks/useProperty'
import {
  listInHouseGuests, listServiceItems, listServiceOrders, postServiceOrder,
  type InHouseGuest, type ServiceItem,
} from '../api'

/**
 * Guest Services — what a guest ordered during the stay.
 *
 * A guest in room 203 asks for two dosas and a coffee. Somebody puts it on
 * their bill, and at check-out they pay for the room and the dosas together.
 * That is the whole screen.
 *
 * It bills from the catalogue, never from this page: the request carries item
 * ids and quantities, and the server prices them. A screen that sends its own
 * prices is a screen that can be made to sell a thali for one rupee.
 *
 * Two things it deliberately does not do:
 *
 * * **No order status.** There is no open/preparing/served here. The order is
 *   taken and billed; a mistake is reversed with a folio adjustment, which
 *   already exists and already leaves a trail. A second way to un-bill
 *   something would mean two records disagreeing about what the guest owes.
 * * **No payment.** Nothing is collected here. These charges join the rest of
 *   the folio and are settled at check-out, which is what the guest expects
 *   and what the ledger is already built for.
 */

const money = new Intl.NumberFormat('en-IN', { maximumFractionDigits: 2 })
const rupees = (n: number) => `₹${money.format(n)}`

export default function GuestOrders() {
  const propertyId = useActivePropertyId()
  const qc = useQueryClient()
  const [search, setSearch] = useState('')
  const [pickedId, setPickedId] = useState<string | null>(null)
  const [cart, setCart] = useState<Record<string, number>>({})
  const [note, setNote] = useState('')

  const guestsQ = useQuery({
    queryKey: ['in-house', propertyId, search],
    queryFn: () => listInHouseGuests(propertyId, search),
    enabled: propertyId !== '',
  })
  const itemsQ = useQuery({
    queryKey: ['service-items', propertyId],
    queryFn: () => listServiceItems(propertyId),
    enabled: propertyId !== '',
  })

  const guests = guestsQ.data ?? []
  const picked = guests.find((g) => g.reservation_id === pickedId) ?? null

  const ordersQ = useQuery({
    queryKey: ['service-orders', propertyId, picked?.folio_id],
    queryFn: () => listServiceOrders(propertyId, picked!.folio_id!),
    enabled: propertyId !== '' && !!picked?.folio_id,
  })

  const items = itemsQ.data ?? []
  const grouped = useMemo(() => {
    const out = new Map<string, ServiceItem[]>()
    for (const it of items) {
      const bucket = out.get(it.category_label) ?? []
      bucket.push(it)
      out.set(it.category_label, bucket)
    }
    return [...out.entries()]
  }, [items])

  const lines = Object.entries(cart)
    .filter(([, qty]) => qty > 0)
    .map(([id, qty]) => ({ item: items.find((i) => i.id === id)!, qty }))
    .filter((l) => l.item)
  const total = lines.reduce((sum, l) => sum + Number(l.item.price) * l.qty, 0)

  const post = useMutation({
    mutationFn: async () => {
      if (!picked?.folio_id) throw new Error('no folio')
      return postServiceOrder({
        property_id: propertyId,
        folio_id: picked.folio_id,
        reservation_id: picked.reservation_id,
        // No business_date: the server stamps the day the property is
        // trading, rather than the browser's date in UTC.
        note: note.trim() || null,
        lines: lines.map((l) => ({ item_id: l.item.id, quantity: l.qty })),
        // One key per Post, so a retry of *this* Post is recognised as the
        // same submission rather than billed again.
        client_key: crypto.randomUUID(),
      })
    },
    onSuccess: (order) => {
      setCart({})
      setNote('')
      void qc.invalidateQueries({ queryKey: ['in-house'] })
      void qc.invalidateQueries({ queryKey: ['service-orders'] })
      notify(`${rupees(Number(order.total))} added to ${picked?.guest_name ?? 'the folio'}.`,
        'Posted to the bill')
    },
    onError: (e) => {
      const er = e as { response?: { data?: { detail?: string } } }
      notify(er.response?.data?.detail ?? 'That did not go through.',
        'Nothing was posted')
    },
  })

  const bump = (id: string, by: number) => setCart((c) => {
    const next = Math.max(0, (c[id] ?? 0) + by)
    const out = { ...c }
    if (next === 0) delete out[id]
    else out[id] = next
    return out
  })

  return (
    <div className="space-y-4">
      <Crumbs title="Guest Services" trail={[{ label: 'Guest Services' }]} />
      <div className="flex flex-wrap items-center justify-between gap-x-4 gap-y-2">
        <h1 className="flex items-center gap-2 text-display text-ink">
          <UtensilsCrossed size={26} className="text-brand" /> Guest Services
        </h1>
      </div>

      {itemsQ.isSuccess && items.length === 0 && (
        <p className="rounded-xl bg-amber-50 px-4 py-3 text-sm text-caution">
          Nothing is on the menu yet.{' '}
          <Link to="/services/menu" className="font-semibold underline">
            Set up what this property sells
          </Link>{' '}
          before taking an order.
        </p>
      )}

      <div className="grid gap-4 xl:grid-cols-[20rem,1fr,22rem]">
        {/* ------------------------------------------------------ who ---- */}
        <section className="rounded-2xl border border-slate-100 bg-white p-4 shadow-sm">
          <h2 className="text-base font-semibold text-slate-800">In-house</h2>
          <p className="mb-3 text-xs text-slate-500">
            Guests checked in right now.
          </p>
          <div className="relative mb-3">
            <Search size={15}
              className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" />
            <input value={search} onChange={(e) => setSearch(e.target.value)}
              placeholder="Name, room or booking…"
              className={`w-full ${FILTER_BOX_ICON} ${CONTROL} outline-none focus:border-brand`} />
          </div>

          {guestsQ.isLoading && (
            <Loader2 className="mx-auto my-6 animate-spin text-slate-400" />
          )}
          {guestsQ.isSuccess && guests.length === 0 && (
            <p className="py-6 text-center text-sm text-slate-400">
              Nobody is checked in.
            </p>
          )}
          <ul className="space-y-1.5">
            {guests.map((g) => (
              <GuestRow key={g.reservation_id} guest={g}
                active={g.reservation_id === pickedId}
                onPick={() => { setPickedId(g.reservation_id); setCart({}) }} />
            ))}
          </ul>
        </section>

        {/* ----------------------------------------------------- menu ---- */}
        <section className="rounded-2xl border border-slate-100 bg-white p-4 shadow-sm">
          <h2 className="text-base font-semibold text-slate-800">Menu</h2>
          <p className="mb-3 text-xs text-slate-500">
            {picked
              ? `Adding to ${picked.guest_name ?? 'this guest'}${picked.room_code ? ` · room ${picked.room_code}` : ''}.`
              : 'Pick a guest on the left first.'}
          </p>

          {itemsQ.isLoading && (
            <Loader2 className="mx-auto my-8 animate-spin text-slate-400" />
          )}
          <div className="space-y-4">
            {grouped.map(([label, group]) => (
              <div key={label}>
                <h3 className="mb-2 text-xs font-semibold uppercase tracking-wide text-slate-400">
                  {label}
                </h3>
                <div className="grid gap-2 sm:grid-cols-2">
                  {group.map((it) => (
                    <MenuButton key={it.id} item={it} qty={cart[it.id] ?? 0}
                      disabled={!picked?.folio_id}
                      onAdd={() => bump(it.id, 1)}
                      onRemove={() => bump(it.id, -1)} />
                  ))}
                </div>
              </div>
            ))}
          </div>
        </section>

        {/* ----------------------------------------------------- cart ---- */}
        <section className="h-fit rounded-2xl border border-slate-100 bg-white p-4 shadow-sm xl:sticky xl:top-4">
          <h2 className="flex items-center gap-2 text-base font-semibold text-slate-800">
            <ShoppingBag size={16} className="text-brand" /> This order
          </h2>

          {!picked && (
            <p className="py-8 text-center text-sm text-slate-400">
              No guest selected.
            </p>
          )}

          {picked && !picked.folio_id && (
            <p className="mt-3 rounded-lg bg-amber-50 px-3 py-2 text-xs text-caution">
              This booking has no open folio, so there is nothing to bill to.
              Check the guest in properly, or open a folio from Payments.
            </p>
          )}

          {picked && picked.folio_id && (
            <>
              <p className="mt-1 text-xs text-slate-500">
                {picked.guest_name}
                {picked.room_code && ` · room ${picked.room_code}`}
              </p>

              {lines.length === 0 && (
                <p className="py-8 text-center text-sm text-slate-400">
                  Nothing added yet.
                </p>
              )}

              {lines.length > 0 && (
                <ul className="mt-3 space-y-2">
                  {lines.map((l) => (
                    <li key={l.item.id} className="flex items-center gap-2 text-sm">
                      <span className="min-w-0 flex-1 truncate text-slate-700"
                        title={l.item.name}>{l.item.name}</span>
                      <span className="tabular-nums text-xs text-slate-400">
                        ×{l.qty}
                      </span>
                      <span className="w-20 text-right tabular-nums font-semibold text-slate-800">
                        {rupees(Number(l.item.price) * l.qty)}
                      </span>
                      <button onClick={() => bump(l.item.id, -l.qty)}
                        aria-label={`Remove ${l.item.name}`}
                        className="rounded p-1 text-slate-300 hover:bg-red-50 hover:text-red-500">
                        <Trash2 size={14} />
                      </button>
                    </li>
                  ))}
                </ul>
              )}

              {lines.length > 0 && (
                <>
                  <div className="mt-3 flex items-center justify-between border-t border-slate-100 pt-3">
                    <span className="text-sm font-semibold text-slate-600">Total</span>
                    <span className="text-xl font-semibold tabular-nums text-ink">
                      {rupees(total)}
                    </span>
                  </div>
                  {/* Said plainly, because the number above is not the whole
                      story: tax is worked out by the ledger from the
                      property's own rules, which this screen does not know. */}
                  <p className="mt-1 text-xs text-slate-400">
                    Tax, where the property has rules for it, is added by the
                    folio.
                  </p>

                  <input value={note} onChange={(e) => setNote(e.target.value)}
                    placeholder="Note (optional) — e.g. room service, 8pm"
                    className={`mt-3 w-full rounded-lg border border-slate-200 px-3 py-2 text-sm outline-none focus:border-brand`} />

                  <button onClick={() => post.mutate()} disabled={post.isPending}
                    className="mt-3 flex w-full items-center justify-center gap-2 rounded-lg bg-brand px-3 py-2.5 text-sm font-semibold text-white hover:bg-brand-dark disabled:opacity-50">
                    {post.isPending && <Loader2 size={14} className="animate-spin" />}
                    Add {rupees(total)} to the bill
                  </button>
                </>
              )}

              <PreviousOrders orders={ordersQ.data ?? []} />
            </>
          )}
        </section>
      </div>
    </div>
  )
}

/* ---------------------------------------------------------------- rows --- */
function GuestRow({ guest, active, onPick }: {
  guest: InHouseGuest; active: boolean; onPick: () => void
}) {
  return (
    <li>
      <button onClick={onPick}
        className={`w-full rounded-lg border px-3 py-2 text-left transition-colors ${
          active
            ? 'border-brand bg-brand-light'
            : 'border-slate-200 hover:bg-slate-50'}`}>
        <div className="flex items-center gap-2">
          <span className="grid h-7 w-7 shrink-0 place-items-center rounded-md bg-slate-75 text-xs font-bold text-slate-600">
            {guest.room_code ?? '—'}
          </span>
          <span className="min-w-0 flex-1 truncate text-sm font-semibold text-slate-800"
            title={guest.guest_name ?? ''}>
            {guest.guest_name ?? 'Guest'}
          </span>
        </div>
        <div className="mt-1 flex items-center justify-between text-xs text-slate-500">
          <span className="flex items-center gap-1">
            <BedDouble size={12} /> out {fmtDate(guest.departure_date)}
          </span>
          <span className="tabular-nums">
            {rupees(Number(guest.balance))} due
          </span>
        </div>
      </button>
    </li>
  )
}

function MenuButton({ item, qty, disabled, onAdd, onRemove }: {
  item: ServiceItem; qty: number; disabled: boolean
  onAdd: () => void; onRemove: () => void
}) {
  return (
    <div className={`flex items-center gap-2 rounded-lg border px-3 py-2 ${
      qty > 0 ? 'border-brand bg-brand-light' : 'border-slate-200'}`}>
      <div className="min-w-0 flex-1">
        <div className="truncate text-sm font-medium text-slate-800" title={item.name}>
          {item.name}
        </div>
        <div className="text-xs tabular-nums text-slate-500">
          {rupees(Number(item.price))}
        </div>
      </div>
      {qty > 0 && (
        <button onClick={onRemove} aria-label={`One less ${item.name}`}
          className="grid h-7 w-7 shrink-0 place-items-center rounded-md border border-slate-200 bg-white text-slate-500 hover:bg-slate-50">
          <Minus size={13} />
        </button>
      )}
      {qty > 0 && (
        <span className="w-4 shrink-0 text-center text-sm font-semibold tabular-nums text-brand">
          {qty}
        </span>
      )}
      <button onClick={onAdd} disabled={disabled}
        aria-label={`Add ${item.name}`}
        className="grid h-7 w-7 shrink-0 place-items-center rounded-md bg-brand text-white hover:bg-brand-dark disabled:bg-slate-200 disabled:text-slate-400">
        <Plus size={13} />
      </button>
    </div>
  )
}

function PreviousOrders({ orders }: {
  orders: { id: string; posted_at: string; total: string; note: string | null
    lines: { item_name: string; quantity: string; amount: string }[] }[]
}) {
  if (orders.length === 0) return null
  return (
    <div className="mt-5 border-t border-slate-100 pt-4">
      <h3 className="text-xs font-semibold uppercase tracking-wide text-slate-400">
        Already on this bill
      </h3>
      <ul className="mt-2 space-y-3">
        {orders.map((o) => (
          <li key={o.id} className="text-xs">
            <div className="flex items-center justify-between text-slate-500">
              <span>{new Date(o.posted_at).toLocaleString('en-IN', {
                day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit',
              })}</span>
              <span className="font-semibold tabular-nums text-slate-700">
                {rupees(Number(o.total))}
              </span>
            </div>
            <ul className="mt-0.5 text-slate-400">
              {o.lines.map((l, i) => (
                <li key={i} className="truncate">
                  {l.item_name} ×{Number(l.quantity)}
                </li>
              ))}
            </ul>
          </li>
        ))}
      </ul>
    </div>
  )
}
