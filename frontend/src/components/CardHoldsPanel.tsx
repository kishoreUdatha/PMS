import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { CreditCard, Loader2 } from 'lucide-react'
import {
  captureCardHold, confirmCardHold, createCardHold, listCardHolds, releaseCardHold,
  type CardHold, type HoldCheckout,
} from '../api'
import { errorText, inputCls } from '../lib/forms'

/** Card holds: authorise an amount on the guest's card, capture it later.
 *
 *  The card goes into Razorpay's own checkout, loaded from Razorpay, and never
 *  touches this app. What comes back is a payment id and a signature, which
 *  the server checks with Razorpay before calling the hold authorised
 *  (card_hold_routes.py). Capturing posts the amount to the folio. Releasing
 *  takes nothing, and the bank returns the money when the authorisation
 *  lapses.
 */

declare global {
  interface Window {
    Razorpay?: new (opts: Record<string, unknown>) => { open: () => void; on: (e: string, cb: (r: unknown) => void) => void }
  }
}

function loadCheckout(): Promise<void> {
  if (window.Razorpay) return Promise.resolve()
  return new Promise((resolve, reject) => {
    const s = document.createElement('script')
    s.src = 'https://checkout.razorpay.com/v1/checkout.js'
    s.onload = () => resolve()
    s.onerror = () => reject(new Error('Razorpay checkout could not load.'))
    document.body.appendChild(s)
  })
}

const STATUS: Record<CardHold['status'], { label: string; tone: string }> = {
  created: { label: 'Waiting for card', tone: 'bg-slate-100 text-slate-500' },
  authorized: { label: 'Held', tone: 'bg-sky-50 text-sky-700' },
  succeeded: { label: 'Captured', tone: 'bg-emerald-50 text-emerald-700' },
  released: { label: 'Released', tone: 'bg-slate-100 text-slate-500' },
  cancelled: { label: 'Cancelled', tone: 'bg-slate-100 text-slate-500' },
  expired: { label: 'Expired', tone: 'bg-slate-100 text-slate-500' },
  failed: { label: 'Failed', tone: 'bg-red-50 text-red-700' },
}
const money = (n: number | null | undefined) =>
  new Intl.NumberFormat('en-IN', { style: 'currency', currency: 'INR' }).format(Number(n ?? 0))

export default function CardHoldsPanel({ reservationId, propertyId }: {
  reservationId: string; propertyId: string
}) {
  const qc = useQueryClient()
  const key = ['cardHolds', reservationId]
  const { data: holds = [] } = useQuery({
    queryKey: key, queryFn: () => listCardHolds(reservationId, propertyId),
    enabled: !!reservationId && !!propertyId,
  })
  const [amount, setAmount] = useState('5000')
  const [purpose, setPurpose] = useState('Security deposit')
  const [open, setOpen] = useState(false)
  const [note, setNote] = useState<{ ok: boolean; text: string } | null>(null)
  const [captureFor, setCaptureFor] = useState<string | null>(null)
  const [captureAmt, setCaptureAmt] = useState('')

  const refresh = () => qc.invalidateQueries({ queryKey: key })
  const fail = (e: unknown) => setNote({ ok: false, text: errorText(e, 'That did not work.') })

  async function takeCard(co: HoldCheckout) {
    await loadCheckout()
    const rzp = new window.Razorpay!({
      key: co.key_id, order_id: co.order_id, amount: co.amount_paise, currency: co.currency,
      name: 'Card hold', description: co.description, prefill: co.prefill,
      theme: { color: '#007A85' },
      handler: async (r: { razorpay_payment_id: string; razorpay_signature: string }) => {
        try {
          await confirmCardHold(propertyId, co.id, { payment_id: r.razorpay_payment_id, signature: r.razorpay_signature })
          setNote({ ok: true, text: 'Card authorised. Nothing has been taken yet.' })
        } catch (e) { fail(e) }
        refresh()
      },
      modal: { ondismiss: refresh },
    })
    rzp.open()
  }

  const create = useMutation({
    mutationFn: () => createCardHold(propertyId, { reservation_id: reservationId,
      amount: Number(amount), purpose: purpose.trim() || null }),
    onSuccess: (co) => { setOpen(false); setNote(null); refresh(); void takeCard(co).catch(fail) },
    onError: fail,
  })
  const capture = useMutation({
    mutationFn: ({ id, amt }: { id: string; amt: number }) => captureCardHold(propertyId, id, amt),
    onSuccess: (h) => { setCaptureFor(null); setNote({ ok: true, text: `Captured ${money(h.captured_amount)} to the bill.` }); refresh() },
    onError: fail,
  })
  const release = useMutation({
    mutationFn: (id: string) => releaseCardHold(propertyId, id),
    onSuccess: () => { setNote({ ok: true, text: 'Hold released. The bank returns the money when the authorisation lapses.' }); refresh() },
    onError: fail,
  })

  return (
    <section className="rounded-xl border border-slate-200 bg-white p-4">
      <div className="mb-1 flex items-center justify-between">
        <h3 className="flex items-center gap-2 font-semibold text-ink">
          <CreditCard className="h-4 w-4 text-brand" /> Card holds
        </h3>
        {!open && (
          <button onClick={() => { setOpen(true); setNote(null) }}
            className="text-xs font-semibold text-brand hover:underline">Hold a card</button>
        )}
      </div>
      <p className="mb-3 text-xs text-slate-500">
        Authorise a deposit on the guest's card now and take only what is owed at check-out.
      </p>
      {note && (
        <p className={`mb-3 rounded-lg px-3 py-2 text-xs ${note.ok ? 'bg-emerald-50 text-emerald-800' : 'bg-red-50 text-red-700'}`}>{note.text}</p>
      )}
      {open && (
        <div className="mb-3 space-y-2 rounded-lg bg-slate-50 p-3">
          <input type="number" min={1} value={amount} onChange={(e) => setAmount(e.target.value)}
            className={`${inputCls} bg-white`} aria-label="Amount to hold" />
          <input value={purpose} onChange={(e) => setPurpose(e.target.value)} maxLength={200}
            className={`${inputCls} bg-white`} aria-label="What the hold is for" />
          <div className="flex gap-2">
            <button onClick={() => create.mutate()} disabled={Number(amount) <= 0 || create.isPending}
              className="inline-flex items-center gap-1.5 rounded-lg bg-brand px-3 py-2 text-xs font-semibold text-white hover:bg-brand-dark disabled:opacity-50">
              {create.isPending && <Loader2 className="h-3.5 w-3.5 animate-spin" />} Take the card
            </button>
            <button onClick={() => setOpen(false)} className="rounded-lg border border-slate-200 px-3 py-2 text-xs font-semibold text-slate-600">Cancel</button>
          </div>
        </div>
      )}
      <ul className="space-y-2">
        {holds.length === 0 && !open && <li className="text-sm text-slate-400">No card held for this booking.</li>}
        {holds.map((h) => (
          <li key={h.id} className="rounded-lg border border-slate-100 p-2.5 text-sm">
            <div className="flex items-center justify-between">
              <span className="font-semibold tabular-nums text-ink">{money(h.amount)}</span>
              <span className={`rounded-full px-2 py-0.5 text-[11px] font-semibold ${STATUS[h.status].tone}`}>{STATUS[h.status].label}</span>
            </div>
            <p className="text-xs text-slate-500">
              {h.purpose}{h.status === 'succeeded' && h.captured_amount ? ` · captured ${money(h.captured_amount)}` : ''}
            </p>
            {h.status === 'authorized' && (captureFor === h.id ? (
              <div className="mt-2 flex gap-2">
                <input type="number" min={1} max={h.authorized_amount ?? h.amount} value={captureAmt}
                  onChange={(e) => setCaptureAmt(e.target.value)} className={`${inputCls} py-1.5`} aria-label="Amount to capture" />
                <button onClick={() => capture.mutate({ id: h.id, amt: Number(captureAmt) })}
                  disabled={Number(captureAmt) <= 0 || capture.isPending}
                  className="rounded-lg bg-brand px-3 text-xs font-semibold text-white disabled:opacity-50">Capture</button>
              </div>
            ) : (
              <div className="mt-2 flex gap-3 text-xs font-semibold">
                <button onClick={() => { setCaptureFor(h.id); setCaptureAmt(String(h.authorized_amount ?? h.amount)) }}
                  className="text-brand hover:underline">Capture…</button>
                <button onClick={() => release.mutate(h.id)} className="text-slate-500 hover:text-red-600">Release</button>
              </div>
            ))}
            {h.status === 'created' && (
              <button onClick={() => release.mutate(h.id)} className="mt-1 text-xs font-semibold text-slate-500 hover:text-red-600">Discard</button>
            )}
          </li>
        ))}
      </ul>
    </section>
  )
}
