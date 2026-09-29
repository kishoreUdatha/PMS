import { useEffect, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Copy, Link2, Loader2, Send, X } from 'lucide-react'
import {
  cancelPaymentLink, createPaymentLink, listPaymentLinks, resendPaymentLink,
  type PaymentLink,
} from '../api'
import { errorText, inputCls } from '../lib/forms'

/** Payment links for one reservation: ask a guest who is not here to pay.
 *
 *  A link is a Razorpay page for one amount, texted to the guest by SMS or
 *  WhatsApp on the channels the property has turned on. Nothing here marks
 *  anything paid: the folio is credited by Razorpay's webhook when the money
 *  moves, and this list shows "Paid" once it has.
 *
 *  One live link per reservation. Creating a new one cancels the old one, so
 *  a guest can never pay the same balance twice through two links.
 */

const STATUS: Record<PaymentLink['status'], { label: string; tone: string }> = {
  created: { label: 'Waiting', tone: 'bg-sky-50 text-sky-700' },
  processing: { label: 'Waiting', tone: 'bg-sky-50 text-sky-700' },
  succeeded: { label: 'Paid', tone: 'bg-emerald-50 text-emerald-700' },
  failed: { label: 'Failed', tone: 'bg-red-50 text-red-700' },
  cancelled: { label: 'Cancelled', tone: 'bg-slate-100 text-slate-500' },
  expired: { label: 'Expired', tone: 'bg-slate-100 text-slate-500' },
}

const money = (n: number) =>
  new Intl.NumberFormat('en-IN', { style: 'currency', currency: 'INR' }).format(Number(n) || 0)

export default function PaymentLinksPanel({ reservationId, propertyId, suggestedAmount }: {
  reservationId: string
  propertyId: string
  /** Pre-filled in the form: usually the balance still due. */
  suggestedAmount?: number
}) {
  const qc = useQueryClient()
  const key = ['paymentLinks', reservationId]
  const { data: links = [], isLoading } = useQuery({
    queryKey: key,
    queryFn: () => listPaymentLinks(reservationId, propertyId),
    enabled: !!reservationId && !!propertyId,
  })

  const [open, setOpen] = useState(false)
  const [amount, setAmount] = useState('')
  const [purpose, setPurpose] = useState('')
  const [hours, setHours] = useState('72')
  const [send, setSend] = useState(true)
  const [note, setNote] = useState<{ tone: 'ok' | 'err'; text: string } | null>(null)
  const [copied, setCopied] = useState('')

  useEffect(() => {
    if (open && !amount && suggestedAmount && suggestedAmount > 0) {
      setAmount(String(Math.round(suggestedAmount * 100) / 100))
    }
  }, [open, amount, suggestedAmount])

  const done = (text: string) => {
    qc.invalidateQueries({ queryKey: key })
    setNote({ tone: 'ok', text })
  }
  const fail = (e: unknown) => setNote({ tone: 'err', text: errorText(e, 'That did not work.') })

  const create = useMutation({
    mutationFn: () => createPaymentLink(propertyId, {
      reservation_id: reservationId, amount: Number(amount),
      purpose: purpose.trim() || null, expires_in_hours: Number(hours) || 72, send,
    }),
    onSuccess: (l) => {
      setOpen(false); setAmount(''); setPurpose('')
      done(l.mock
        ? 'Link created on the mock gateway. It cannot take money until Razorpay is connected.'
        : `Link created.${l.message ? ` ${l.message}.` : ''}`)
    },
    onError: fail,
  })
  const resend = useMutation({
    mutationFn: (id: string) => resendPaymentLink(id, propertyId),
    onSuccess: (l) => done(l.message ? `${l.message}.` : 'Sent.'),
    onError: fail,
  })
  const cancel = useMutation({
    mutationFn: (id: string) => cancelPaymentLink(id, propertyId),
    onSuccess: () => done('Link cancelled. The guest can no longer pay through it.'),
    onError: fail,
  })

  async function copy(l: PaymentLink) {
    if (!l.url) return
    try {
      await navigator.clipboard.writeText(l.url)
      setCopied(l.id)
      setTimeout(() => setCopied(''), 1500)
    } catch { /* clipboard blocked: the link is still visible to select */ }
  }

  const amountOk = Number(amount) > 0

  return (
    <section className="rounded-xl border border-slate-200 bg-white p-4">
      <div className="mb-1 flex items-center justify-between gap-2">
        <h3 className="flex items-center gap-2 font-semibold text-ink">
          <Link2 className="h-4 w-4 text-brand" /> Payment Links
        </h3>
        {!open && (
          <button type="button" onClick={() => { setOpen(true); setNote(null) }}
            className="text-xs font-semibold text-brand hover:underline">
            New link
          </button>
        )}
      </div>
      <p className="mb-3 text-xs text-slate-500">
        Send the guest a secure Razorpay link to pay from their phone. The folio
        is credited automatically when they pay.
      </p>

      {note && (
        <p className={`mb-3 rounded-lg px-3 py-2 text-xs ${
          note.tone === 'ok' ? 'bg-emerald-50 text-emerald-800' : 'bg-red-50 text-red-700'}`}>
          {note.text}
        </p>
      )}

      {open && (
        <div className="mb-4 space-y-2.5 rounded-lg border border-slate-100 bg-slate-50 p-3">
          <label className="block text-xs font-medium text-slate-600">
            Amount (₹)
            <input type="number" min={1} step="0.01" value={amount}
              onChange={(e) => setAmount(e.target.value)} className={`${inputCls} mt-1 bg-white`} />
          </label>
          <label className="block text-xs font-medium text-slate-600">
            What it is for
            <input value={purpose} maxLength={200} onChange={(e) => setPurpose(e.target.value)}
              placeholder="Advance for your stay" className={`${inputCls} mt-1 bg-white`} />
          </label>
          <label className="block text-xs font-medium text-slate-600">
            Guest has
            <select value={hours} onChange={(e) => setHours(e.target.value)}
              className={`${inputCls} mt-1 bg-white`}>
              <option value="24">1 day to pay</option>
              <option value="72">3 days to pay</option>
              <option value="168">7 days to pay</option>
              <option value="336">14 days to pay</option>
            </select>
          </label>
          <label className="flex items-center gap-2 text-xs text-slate-600">
            <input type="checkbox" checked={send} onChange={(e) => setSend(e.target.checked)}
              className="h-4 w-4 accent-brand" />
            Text it to the guest now (SMS / WhatsApp)
          </label>
          {links.some((l) => l.status === 'created' || l.status === 'processing') && (
            <p className="text-xs text-amber-700">
              The link already open for this booking will be cancelled.
            </p>
          )}
          <div className="flex gap-2 pt-1">
            <button type="button" disabled={!amountOk || create.isPending}
              onClick={() => create.mutate()}
              className="inline-flex items-center gap-1.5 rounded-lg bg-brand px-3 py-2 text-xs font-semibold text-white hover:bg-brand-dark disabled:opacity-50">
              {create.isPending && <Loader2 className="h-3.5 w-3.5 animate-spin" />}
              Create link
            </button>
            <button type="button" onClick={() => setOpen(false)}
              className="rounded-lg border border-slate-200 px-3 py-2 text-xs font-semibold text-slate-600 hover:bg-white">
              Cancel
            </button>
          </div>
        </div>
      )}

      {isLoading && <p className="text-sm text-slate-400">Loading…</p>}
      {!isLoading && links.length === 0 && !open && (
        <p className="text-sm text-slate-400">No links sent for this booking.</p>
      )}
      <ul className="space-y-2.5">
        {links.map((l) => {
          const st = STATUS[l.status]
          const live = l.status === 'created' || l.status === 'processing'
          return (
            <li key={l.id} className="rounded-lg border border-slate-100 p-2.5 text-sm">
              <div className="flex items-center justify-between gap-2">
                <span className="font-semibold tabular-nums text-ink">{money(l.amount)}</span>
                <span className={`rounded-full px-2 py-0.5 text-[11px] font-semibold ${st.tone}`}>
                  {st.label}
                </span>
              </div>
              <p className="mt-0.5 truncate text-xs text-slate-500">{l.purpose}</p>
              <p className="text-[11px] text-slate-400">
                {live && l.expires_at
                  ? `Open until ${new Date(l.expires_at).toLocaleString('en-IN', { day: 'numeric', month: 'short', hour: 'numeric', minute: '2-digit' })}`
                  : `Created ${new Date(l.created_at).toLocaleDateString('en-IN', { day: 'numeric', month: 'short' })}`}
                {l.mock && ' · mock gateway'}
              </p>
              {live && (
                <div className="mt-2 flex flex-wrap gap-3 text-xs font-semibold">
                  {l.url && (
                    <button type="button" onClick={() => copy(l)}
                      className="inline-flex items-center gap-1 text-brand hover:underline">
                      <Copy className="h-3.5 w-3.5" /> {copied === l.id ? 'Copied' : 'Copy link'}
                    </button>
                  )}
                  <button type="button" disabled={resend.isPending}
                    onClick={() => resend.mutate(l.id)}
                    className="inline-flex items-center gap-1 text-brand hover:underline disabled:opacity-50">
                    <Send className="h-3.5 w-3.5" /> Resend
                  </button>
                  <button type="button" disabled={cancel.isPending}
                    onClick={() => cancel.mutate(l.id)}
                    className="inline-flex items-center gap-1 text-slate-500 hover:text-red-600 disabled:opacity-50">
                    <X className="h-3.5 w-3.5" /> Cancel
                  </button>
                </div>
              )}
            </li>
          )
        })}
      </ul>
    </section>
  )
}
