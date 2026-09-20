/**
 * Take a payment against a folio, without leaving the folio.
 *
 * The method list is served, never written down here — the same list every
 * other collection screen reads, so `finance.payments` cannot end up holding
 * "UPI" and "upi" as two methods again.
 *
 * A reference is required for every method that has one to give. Cash has
 * none, so it is not asked for; a card, UPI, bank transfer, cheque or wallet
 * payment all produce something the guest can quote back, and a payment
 * nobody can trace is the one that gets disputed. The rule is the server's —
 * this only asks for what the server will insist on, so the desk finds out
 * before it presses the button rather than after.
 *
 * The amount defaults to the balance due, because that is what is being
 * settled nine times out of ten, and is freely overridden for a part payment.
 */
import { useEffect, useState } from 'react'
import { AlertTriangle, CreditCard, Loader2, ShieldCheck, X } from 'lucide-react'
import Select from './Select'
import { usePaymentMethods } from '../lib/paymentMethods'
import { collectFolioPayment, ensureFolio } from '../api'

const field = 'w-full rounded-lg border border-slate-200 px-3 py-2.5 text-sm outline-none focus:border-brand'
const lbl = 'mb-1 block text-xs font-medium text-slate-600'

const money = (v: number) => new Intl.NumberFormat('en-IN', {
  style: 'currency', currency: 'INR', minimumFractionDigits: 2,
}).format(v)

export default function AddPaymentDialog({
  reservationId, organizationId, propertyId, folioId, folioLabel,
  balanceDue, businessDate, onClose, onSaved,
}: {
  reservationId: string
  organizationId: string
  propertyId: string
  folioId: string | null
  folioLabel: string
  /** Positive when the guest owes; negative when the folio is in credit. */
  balanceDue: number
  businessDate: string
  onClose: () => void
  onSaved: () => void
}) {
  const { methods } = usePaymentMethods(propertyId)

  const [date, setDate] = useState(businessDate)
  const [method, setMethod] = useState('')
  const [amount, setAmount] = useState(
    balanceDue > 0 ? String(balanceDue) : '')
  const [reference, setReference] = useState('')
  const [note, setNote] = useState('')
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')

  // Defaulted from the served list, never from a spelling named in this file.
  useEffect(() => {
    if (methods.length > 0) setMethod((m) => m || methods[0].value)
  }, [methods])

  const chosen = methods.find((m) => m.value === method)
  const needsReference = chosen?.needs_reference ?? false
  const value = Number(amount) || 0
  const overPaying = balanceDue > 0 && value > balanceDue
  const valid = value > 0 && method !== ''
    && (!needsReference || reference.trim() !== '')

  async function save() {
    setErr(''); setBusy(true)
    try {
      const fid = folioId
        ?? (await ensureFolio(reservationId, organizationId, propertyId)).id
      await collectFolioPayment({
        organization_id: organizationId,
        property_id: propertyId,
        method,
        business_date: date,
        reference: reference.trim() || null,
        note: note.trim() || null,
        allocations: [{ folio_id: fid, amount: value }],
      })
      onSaved()
    } catch (e) {
      const ax = e as { response?: { data?: { detail?: string } } }
      setErr(ax?.response?.data?.detail ?? 'The payment could not be taken.')
    } finally { setBusy(false) }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-start justify-center overflow-y-auto bg-slate-900/30 p-4"
      onClick={onClose}>
      <div onClick={(e) => e.stopPropagation()}
        className="mt-16 w-full max-w-lg rounded-2xl bg-white p-6 shadow-xl">
        <div className="mb-5 flex items-start justify-between gap-3">
          <h2 className="flex items-center gap-2 text-lg font-semibold text-ink">
            <CreditCard size={18} className="text-brand" /> Add payment
          </h2>
          <button onClick={onClose}
            className="rounded-lg p-1 text-slate-400 hover:bg-slate-100">
            <X size={18} />
          </button>
        </div>

        <div className="grid grid-cols-2 gap-4">
          <label className="block">
            <span className={lbl}>Date</span>
            <input type="date" className={field} value={date}
              onChange={(e) => setDate(e.target.value)} />
          </label>
          <label className="block">
            <span className={lbl}>Folio</span>
            <p className="w-full rounded-lg border border-slate-200 bg-slate-50 px-3 py-2.5 text-sm text-slate-600">
              {folioLabel}
            </p>
          </label>

          <label className="col-span-2 block">
            <span className={lbl}>Method</span>
            <Select className={field} value={method}
              onChange={(e) => { setMethod(e.target.value); setErr('') }}>
              {methods.map((m) => (
                <option key={m.value} value={m.value}>{m.label}</option>
              ))}
            </Select>
          </label>

          <label className="block">
            <span className={lbl}>Amount (₹)</span>
            <input type="number" min="0" step="0.01" className={field}
              value={amount} placeholder="0.00"
              onChange={(e) => setAmount(e.target.value)} />
            {/* Warned, not blocked: a guest handing over a round figure and
                taking change is ordinary, and refusing it would send the desk
                looking for a way round the screen. */}
            {overPaying && (
              <span className="mt-1 block text-xs text-caution">
                More than the {money(balanceDue)} owing — the folio will be in
                credit.
              </span>
            )}
          </label>
          <div>
            <span className={lbl}>Balance due</span>
            <p className={`rounded-lg px-3 py-2.5 text-sm font-bold ${
              balanceDue > 0
                ? 'bg-amber-50 text-caution' : 'bg-emerald-50 text-emerald-700'}`}>
              {money(balanceDue)}
            </p>
            {value > 0 && (
              <span className="mt-1 block text-xs text-slate-500">
                {money(balanceDue - value)} after this
              </span>
            )}
          </div>

          {/* Add charge has carried a Comments field since it was built, and
              a payment had nothing equivalent — so a folio with three payments
              on one day showed "Payment" three times and no way to tell which
              was which. This is the same field by the name the folio uses for
              it: it becomes the line's description. */}
          <label className="col-span-2 block">
            <span className={lbl}>Description</span>
            <input className={field} value={note}
              placeholder="What this payment is for — shown on the folio"
              onChange={(e) => setNote(e.target.value)} />
          </label>

          <label className="col-span-2 block">
            <span className={lbl}>
              Reference
              {needsReference
                ? <span className="text-red-500"> — required</span>
                : <span className="text-slate-400"> — optional</span>}
            </span>
            <input className={field} value={reference}
              placeholder={needsReference
                ? 'Transaction or approval code'
                : 'Receipt number, if there is one'}
              onChange={(e) => setReference(e.target.value)} />
            {/* No helper line under this field. Whether a reference is needed
                was already said twice above it — once by the label's own
                "required"/"optional", once by the placeholder naming what to
                type — and a third telling is not emphasis, it is clutter in a
                dialog that should be four fields and a button. */}
          </label>
        </div>

        {/* This used to be a permanent notice on a Credit Card tab. It says
            something true and worth saying — that no card number is held
            anywhere in this system — but a tab nobody opened was the wrong
            place to say it. It belongs beside the button that takes a card
            payment, on the one occasion somebody might be about to look for a
            field to type a card number into. */}
        {method === 'card' && (
          <p className="mt-4 flex items-start gap-2 rounded-lg bg-brand-light px-3 py-2.5 text-xs text-brand">
            <ShieldCheck size={14} className="mt-0.5 shrink-0" />
            <span>
              <strong>No card details are held here.</strong> Card numbers,
              expiry dates and holder names are never stored by this system —
              the payment runs through the provider’s hosted checkout, and the
              reference below is all that comes back.
            </span>
          </p>
        )}

        {err && (
          <p className="mt-4 flex items-start gap-2 rounded-lg bg-red-50 px-3 py-2 text-xs text-red-700">
            <AlertTriangle size={14} className="mt-0.5 shrink-0" />{err}
          </p>
        )}

        <div className="mt-5 flex justify-end gap-2">
          <button onClick={onClose}
            className="rounded-lg border border-slate-200 px-4 py-2 text-sm font-semibold text-slate-600 hover:bg-slate-50">
            Cancel
          </button>
          <button onClick={() => void save()} disabled={!valid || busy}
            className="flex items-center gap-2 rounded-lg bg-brand px-4 py-2 text-sm font-semibold text-white enabled:hover:bg-brand-dark disabled:opacity-50">
            {busy && <Loader2 size={15} className="animate-spin" />}
            Take {value > 0 ? money(value) : 'payment'}
          </button>
        </div>
      </div>
    </div>
  )
}
